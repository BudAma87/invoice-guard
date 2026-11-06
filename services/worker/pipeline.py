"""Agent worker: steps 3-9 per invoice. Stateless; pulls work from the state DB, not Kafka,
so a slow model call never blocks a partition. Each step commits before the next starts.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from libs.config import settings
from libs.core.normalise import normalise
from libs.core.service import create_task, evaluate_invoice, route_validated
from libs.core.store import ObjectStore
from libs.db import models as m
from libs.db.session import is_postgres
from libs.db.state import record_event, transition
from libs.erp_adapter.protocol import ERPAdapter
from libs.llm_gateway.client import ExtractionFailed, Extractor
from libs.llm_gateway.resilience import CircuitOpen, backoff_s

log = logging.getLogger("worker")
MAX_ATTEMPTS = 6
STUCK_AFTER = timedelta(hours=4)


def claim_received(session: Session, now: datetime) -> m.Invoice | None:
    q = (
        select(m.Invoice)
        .where(
            m.Invoice.state == m.State.RECEIVED,
            or_(m.Invoice.next_attempt_at.is_(None), m.Invoice.next_attempt_at <= now),
        )
        .order_by(m.Invoice.created_at)
        .limit(1)
    )
    if is_postgres(session):
        q = q.with_for_update(skip_locked=True)
    inv = session.scalar(q)
    if inv is None:
        return None
    inv.attempts += 1
    inv.lease_until = now + timedelta(seconds=settings.extract_lease_s)
    transition(session, inv, m.State.EXTRACTING, payload={"attempt": inv.attempts})
    return inv


async def process_invoice(
    factory: sessionmaker[Session],
    erp: ERPAdapter,
    extractor: Extractor,
    store: ObjectStore,
    now: datetime,
) -> str | None:
    # Step 3-4 lease + extract
    with factory() as s:
        inv = claim_received(s, now)
        if inv is None:
            return None
        s.commit()
        invoice_id, sha, attempt = inv.invoice_id, inv.pdf_sha256, inv.attempts
    assert sha
    try:
        res = await extractor.extract(store.get(sha))
    except (ExtractionFailed, CircuitOpen, Exception) as e:  # noqa: BLE001 - record every failure
        with factory() as s:
            inv = s.get(m.Invoice, invoice_id)
            assert inv
            s.add(
                m.Extraction(
                    invoice_id=invoice_id,
                    attempt=attempt,
                    model="-",
                    prompt_version="-",
                    error=str(e)[:2000],
                )
            )
            permanent = isinstance(e, ExtractionFailed) or attempt >= MAX_ATTEMPTS
            if permanent:
                transition(s, inv, m.State.EXTRACTION_FAILED, payload={"error": str(e)[:500]})
                create_task(s, inv, m.TaskKind.MANUAL_ENTRY, ["EXTRACTION_FAILED"], now)
            else:
                inv.next_attempt_at = now + timedelta(seconds=backoff_s(attempt))
                transition(
                    s,
                    inv,
                    m.State.RECEIVED,
                    action="EXTRACT_RETRY",
                    payload={
                        "error": str(e)[:500],
                        "next_attempt_at": inv.next_attempt_at.isoformat(),
                    },
                )
            inv.lease_until = None
            s.commit()
        return "failed"

    # Step 5 normalise
    with factory() as s:
        inv = s.get(m.Invoice, invoice_id)
        assert inv
        n = normalise(res.parsed)
        s.add(
            m.Extraction(
                invoice_id=invoice_id,
                attempt=attempt,
                model=res.model,
                prompt_version=res.prompt_version,
                parsed_json=res.parsed.model_dump(),
                field_confidence_json=n.confidence,
                latency_ms=res.latency_ms,
                tokens=res.tokens,
            )
        )
        for k, v in n.header.items():
            setattr(inv, k, v)
        inv.tax_lines = n.tax_lines
        inv.confidence = n.confidence
        inv.evidence = n.evidence
        inv.lines.clear()
        s.flush()
        for ln in n.lines:
            inv.lines.append(m.InvoiceLine(**ln))
        inv.lease_until = None
        transition(
            s,
            inv,
            m.State.EXTRACTED,
            payload={"model": res.model, "tokens": res.tokens, "flags": n.flags},
        )
        s.commit()

    # Steps 6-9 dedupe, enrich, validate, route
    with factory() as s:
        inv = s.get(m.Invoice, invoice_id)
        assert inv
        ev = await evaluate_invoice(s, erp, inv, now)
        route_validated(s, inv, ev, now)
        s.commit()
        return inv.state


def sweep(session: Session, now: datetime) -> dict[str, int]:
    """Every minute: expired EXTRACTING leases go back to RECEIVED; stuck invoices alert;
    expired review-task leases are released."""
    requeued = 0
    for inv in session.scalars(
        select(m.Invoice).where(m.Invoice.state == m.State.EXTRACTING, m.Invoice.lease_until < now)
    ):
        transition(session, inv, m.State.RECEIVED, action="LEASE_EXPIRED")
        inv.lease_until = None
        requeued += 1
    stuck = session.scalars(
        select(m.Invoice.invoice_id).where(
            m.Invoice.state.not_in(list(m.TERMINAL) + list(m.IN_REVIEW)),
            m.Invoice.state_since < now - STUCK_AFTER,
        )
    ).all()
    for iid in stuck:
        log.error("ALERT invoice %s non-terminal > 4h", iid)
    released = 0
    for t in session.scalars(
        select(m.ReviewTask).where(m.ReviewTask.status == "OPEN", m.ReviewTask.lease_until < now)
    ):
        record_event(session, t.invoice_id, "TASK_LEASE_EXPIRED", payload={"was": t.assignee})
        t.assignee, t.lease_until = None, None
        released += 1
    return {"requeued": requeued, "stuck": len(stuck), "released": released}


async def main() -> None:  # pragma: no cover
    from libs.core.runtime import erp_client, extractor_client
    from libs.db.models import utcnow
    from libs.db.session import SessionLocal

    logging.basicConfig(level=logging.INFO)
    erp, ex, store = erp_client(), extractor_client(), ObjectStore()
    sem = asyncio.Semaphore(16)  # concurrent model calls per worker
    last_sweep = utcnow()

    async def one():
        async with sem:
            return await process_invoice(SessionLocal, erp, ex, store, utcnow())

    while True:
        results = await asyncio.gather(*[one() for _ in range(4)])
        if (utcnow() - last_sweep).total_seconds() > 60:
            with SessionLocal() as s:
                log.info("sweep %s", sweep(s, utcnow()))
                s.commit()
            last_sweep = utcnow()
        if not any(results):
            await asyncio.sleep(1)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
