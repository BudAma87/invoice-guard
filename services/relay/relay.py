"""Transactional outbox relay — the only component allowed to talk to the ERP (TSD §3, §9).

For every PENDING/UNKNOWN outbox row: look up the ERP by external ref = invoice_id first;
post only if nothing is there. A crash after the ERP accepted but before our DB update is
healed on the next pass by that lookup, so there is at most one ERP document per invoice.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from libs.core.service import create_task
from libs.db import models as m
from libs.db.state import record_event, transition
from libs.erp_adapter.protocol import (
    ERPAdapter,
    ERPBusinessError,
    ERPDoc,
    ERPUnavailable,
    PostableInvoice,
    PostableLine,
)

log = logging.getLogger("relay")
ALERT_AFTER = timedelta(minutes=15)
posted_events: list[dict] = []  # stand-in for the invoices.posted topic in dev


def backoff(attempts: int) -> timedelta:
    """1 min doubling, capped at 30 min."""
    return timedelta(minutes=min(30, 2 ** max(0, attempts - 1)))


def _postable(inv: m.Invoice) -> PostableInvoice:
    assert inv.supplier_id and inv.invoice_no_norm and inv.invoice_date and inv.currency
    return PostableInvoice(
        entity_id=inv.entity_id,
        supplier_id=inv.supplier_id,
        invoice_no=inv.invoice_no_norm,
        invoice_date=inv.invoice_date,
        posting_period=inv.posting_period or inv.invoice_date.strftime("%Y-%m"),
        currency=inv.currency,
        po_number=inv.po_number,
        total_minor=inv.total_minor or 0,
        tax_minor=inv.tax_minor,
        lines=tuple(
            PostableLine(
                ln.po_line_id,
                ln.item_code,
                Decimal(ln.qty or "0"),
                ln.unit_price_minor or 0,
                ln.amount_minor or 0,
            )
            for ln in inv.lines
        ),
    )


def _confirm(session: Session, inv: m.Invoice, ob: m.Outbox, doc: ERPDoc, how: str) -> None:
    ob.status = m.OutboxStatus.DONE
    ob.erp_doc_no = doc.doc_no
    inv.erp_doc_no = doc.doc_no
    transition(
        session,
        inv,
        m.State.POSTED,
        action="POSTED",
        payload={"erp_doc_no": doc.doc_no, "via": how},
    )
    posted_events.append({"invoice_id": inv.invoice_id, "erp_doc_no": doc.doc_no})


async def process_one(session: Session, erp: ERPAdapter, ob: m.Outbox, now: datetime) -> str:
    inv = session.get(m.Invoice, ob.invoice_id)
    assert inv is not None
    if inv.state in (m.State.APPROVED_AUTO, m.State.APPROVED_MANUAL):
        transition(session, inv, m.State.POSTING, action="POSTING")
        session.flush()
    ref = UUID(ob.idempotency_key)
    ob.attempts += 1
    try:
        existing = await erp.find_by_external_ref(inv.entity_id, ref)
        if existing:
            _confirm(session, inv, ob, existing, "lookup")
            return "found"
        doc = await erp.post_invoice(_postable(inv), ref)
        _confirm(session, inv, ob, doc, "post")
        return "posted"
    except ERPBusinessError as e:
        ob.status = m.OutboxStatus.FAILED
        ob.last_error = str(e)
        inv.last_error = str(e)
        transition(
            session, inv, m.State.POST_FAILED, action="POST_FAILED", payload={"erp_message": str(e)}
        )
        transition(session, inv, m.State.NEEDS_REVIEW, action="POST_FAILED_TO_REVIEW")
        create_task(session, inv, m.TaskKind.REVIEW, ["ERP_REFUSED"], now, blocker=False)
        return "business_error"
    except ERPUnavailable as e:
        ob.status = m.OutboxStatus.UNKNOWN if e.ambiguous else m.OutboxStatus.PENDING
        ob.last_error = str(e)
        ob.next_attempt_at = now + backoff(ob.attempts)
        if now - ob.created_at > ALERT_AFTER:
            log.error("ALERT outbox older than 15 min: %s", ob.outbox_id)
        # back to APPROVED_* to wait (TSD §9: "Invoices wait in APPROVED")
        manual = session.scalar(
            select(m.ReviewTask.task_id)
            .where(m.ReviewTask.invoice_id == inv.invoice_id, m.ReviewTask.status == "DONE")
            .limit(1)
        )
        transition(
            session,
            inv,
            m.State.APPROVED_MANUAL if manual else m.State.APPROVED_AUTO,
            action="POST_DEFERRED",
        )
        record_event(
            session,
            inv.invoice_id,
            "POST_RETRY",
            payload={
                "error": str(e),
                "ambiguous": e.ambiguous,
                "next_attempt_at": ob.next_attempt_at.isoformat(),
            },
        )
        return "retry"


async def run_once(factory: sessionmaker[Session], erp: ERPAdapter, now: datetime) -> int:
    with factory() as s:
        ids = s.scalars(
            select(m.Outbox.outbox_id)
            .where(
                m.Outbox.status.in_([m.OutboxStatus.PENDING, m.OutboxStatus.UNKNOWN]),
                m.Outbox.next_attempt_at <= now,
            )
            .order_by(m.Outbox.created_at)
            .limit(50)
        ).all()
    done = 0
    for oid in ids:
        with factory() as s:  # one transaction per invoice
            ob = s.get(m.Outbox, oid)
            if ob is None or ob.status not in (m.OutboxStatus.PENDING, m.OutboxStatus.UNKNOWN):
                continue
            outcome = await process_one(s, erp, ob, now)
            s.commit()
            done += outcome in ("posted", "found")
    return done


async def main() -> None:  # pragma: no cover
    from libs.core.runtime import erp_client
    from libs.db.models import utcnow
    from libs.db.session import SessionLocal

    logging.basicConfig(level=logging.INFO)
    erp = erp_client()
    while True:
        n = await run_once(SessionLocal, erp, utcnow())
        if n:
            log.info("posted %d", n)
        await asyncio.sleep(2)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
