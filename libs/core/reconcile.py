"""Daily reconciliation — the lost-invoice net (TSD §9).

At 23:30 in each entity's time zone, for that business day:

1. every message the channel received has an inbox row       (else: a lost invoice)
2. every invoice POSTED that day has its ERP document          (looked up by external ref)
3. every pipeline ERP document posted that day has a POSTED invoice

plus two checks the same data gives for free: more than one ERP document for one invoice
(a double payment) and an ERP document whose number, total or currency differs from ours.

Any difference is stored, written to the invoice's audit trail, paged, and shown on the ops
dashboard. A source that can't be read never passes silently: the run is PARTIAL (ledger)
or ERROR (ERP; retried on the next scheduler tick).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.core.entities import ENTITIES, entity
from libs.core.ledger import MessageLedger
from libs.db import models as m
from libs.db.state import record_event
from libs.erp_adapter.protocol import ERPAdapter, ERPDoc, ERPUnavailable

log = logging.getLogger("reconcile")

RUN_AT = time(23, 30)  # entity local time
LOOKBACK_DAYS = 7  # catch up days the scheduler missed (Kafka keeps 14)
DONE = {m.ReconStatus.OK, m.ReconStatus.DIFFERENCES, m.ReconStatus.PARTIAL}


@dataclass(frozen=True)
class Difference:
    kind: str
    severity: str  # critical | high
    detail: str
    invoice_id: str | None = None
    message_id: str | None = None
    erp_doc_no: str | None = None


@dataclass(frozen=True)
class InvoiceFacts:
    invoice_id: str
    state: str
    total_minor: int | None
    currency: str | None
    erp_doc_no: str | None


# ------------------------------------------------------------------ pure comparison


def compare(
    *,
    messages: set[str] | None,
    inbox_message_ids: set[str],
    posted: Iterable[InvoiceFacts],
    erp_doc_for_posted: dict[str, ERPDoc | None],
    day_docs: Iterable[ERPDoc],
    invoices: dict[str, InvoiceFacts],
) -> list[Difference]:
    """No I/O. `invoices` = every invoice an ERP document of the day refers to."""
    out: list[Difference] = []
    seen: set[tuple[str, str]] = set()

    def add(d: Difference) -> None:
        key = (d.kind, d.invoice_id or d.message_id or d.erp_doc_no or "")
        if key not in seen:
            seen.add(key)
            out.append(d)

    # 1. channel -> inbox
    for mid in sorted((messages or set()) - inbox_message_ids):
        add(
            Difference(
                "MESSAGE_WITHOUT_INBOX",
                "critical",
                "Message arrived but no inbox row exists: the invoice was never taken in",
                message_id=mid,
            )
        )

    # 2. POSTED -> ERP document
    for inv in posted:
        doc = erp_doc_for_posted.get(inv.invoice_id)
        if doc is None:
            add(
                Difference(
                    "POSTED_WITHOUT_ERP_DOC",
                    "critical",
                    "Marked POSTED but the ERP has no document with this external ref",
                    invoice_id=inv.invoice_id,
                    erp_doc_no=inv.erp_doc_no,
                )
            )
            continue
        _check_match(inv, doc, add)

    # 3. ERP document -> POSTED (+ duplicates)
    by_ref: dict[str, list[ERPDoc]] = defaultdict(list)
    for d in day_docs:
        by_ref[d.ext_ref].append(d)
    for ref, docs in sorted(by_ref.items()):
        if len(docs) > 1:
            add(
                Difference(
                    "DUPLICATE_ERP_DOC",
                    "critical",
                    f"{len(docs)} ERP documents for one invoice: "
                    f"{', '.join(d.doc_no for d in docs)} (double payment risk)",
                    invoice_id=ref,
                    erp_doc_no=docs[0].doc_no,
                )
            )
        owner = invoices.get(ref)
        if owner is None or owner.state != m.State.POSTED:
            add(
                Difference(
                    "ERP_DOC_WITHOUT_POSTED",
                    "critical",
                    "ERP document carries our external ref but the invoice is "
                    + (f"{owner.state}, not POSTED" if owner else "unknown to the pipeline"),
                    invoice_id=ref if owner else None,
                    erp_doc_no=docs[0].doc_no,
                )
            )
            continue
        _check_match(owner, docs[0], add)
    return out


def _check_match(inv: InvoiceFacts, doc: ERPDoc, add) -> None:
    if inv.erp_doc_no and doc.doc_no != inv.erp_doc_no:
        add(
            Difference(
                "DOC_NO_MISMATCH",
                "high",
                f"We recorded {inv.erp_doc_no}; the ERP has {doc.doc_no}",
                invoice_id=inv.invoice_id,
                erp_doc_no=doc.doc_no,
            )
        )
    if doc.total_minor != inv.total_minor or doc.currency != inv.currency:
        add(
            Difference(
                "AMOUNT_MISMATCH",
                "high",
                f"ERP {doc.total_minor} {doc.currency} vs ours "
                f"{inv.total_minor} {inv.currency} (minor units)",
                invoice_id=inv.invoice_id,
                erp_doc_no=doc.doc_no,
            )
        )


# ------------------------------------------------------------------ the run


class Pager(Protocol):
    def page(self, run: m.ReconciliationRun, items: list[m.ReconciliationItem]) -> None: ...


class LogPager:
    """Dev pager: an ERROR log line. Production: PagerDuty / Opsgenie."""

    def page(self, run: m.ReconciliationRun, items: list[m.ReconciliationItem]) -> None:
        log.error(
            "ALERT reconciliation %s %s: %s, %d difference(s)%s",
            run.entity_id,
            run.business_date,
            run.status,
            run.differences,
            f" — {run.error}" if run.error else "",
        )


def window(entity_id: str, day: date) -> tuple[datetime, datetime]:
    tz = entity(entity_id).tz
    start = datetime.combine(day, time(0), tz)
    return start, start + timedelta(days=1)


def _facts(inv: m.Invoice) -> InvoiceFacts:
    return InvoiceFacts(inv.invoice_id, inv.state, inv.total_minor, inv.currency, inv.erp_doc_no)


def _uuid(s: str) -> UUID | None:
    try:
        return UUID(s)
    except ValueError:
        return None


async def run_reconciliation(
    session: Session,
    erp: ERPAdapter,
    ledger: MessageLedger,
    entity_id: str,
    day: date,
    now: datetime,
    *,
    trigger: str = "schedule",
    pager: Pager | None = None,
) -> m.ReconciliationRun:
    """Run, store and page one entity-day. Caller commits."""
    pager = pager or LogPager()
    start, end = window(entity_id, day)
    run = m.ReconciliationRun(
        run_id=m.new_id(),
        entity_id=entity_id,
        business_date=day,
        trigger=trigger,
        started_at=now,
        status=m.ReconStatus.RUNNING,
    )
    session.add(run)
    skipped: list[str] = []
    try:
        messages = await ledger.message_ids(entity_id, start, end)
        if messages is None:
            skipped.append("messages→inbox: message ledger unavailable")
        inbox_ids: set[str] = set()
        if messages:
            inbox_ids = set(
                session.scalars(select(m.Inbox.message_id).where(m.Inbox.message_id.in_(messages)))
            )

        posted_ids = set(
            session.scalars(
                select(m.InvoiceEvent.invoice_id)
                .join(m.Invoice, m.Invoice.invoice_id == m.InvoiceEvent.invoice_id)
                .where(
                    m.InvoiceEvent.action == "POSTED",
                    m.InvoiceEvent.at >= start,
                    m.InvoiceEvent.at < end,
                    m.Invoice.entity_id == entity_id,
                )
            )
        )
        posted = (
            [
                _facts(i)
                for i in session.scalars(
                    select(m.Invoice).where(m.Invoice.invoice_id.in_(posted_ids))
                )
            ]
            if posted_ids
            else []
        )
        doc_for: dict[str, ERPDoc | None] = {}
        for inv in posted:
            ref = _uuid(inv.invoice_id)
            doc_for[inv.invoice_id] = (
                await erp.find_by_external_ref(entity_id, ref) if ref else None
            )

        day_docs = await erp.list_documents(entity_id, start, end)
        refs = {d.ext_ref for d in day_docs}
        invoices = (
            {
                i.invoice_id: _facts(i)
                for i in session.scalars(
                    select(m.Invoice).where(
                        m.Invoice.invoice_id.in_(refs), m.Invoice.entity_id == entity_id
                    )
                )
            }
            if refs
            else {}
        )

        diffs = compare(
            messages=messages,
            inbox_message_ids=inbox_ids,
            posted=posted,
            erp_doc_for_posted=doc_for,
            day_docs=day_docs,
            invoices=invoices,
        )
    except ERPUnavailable as e:
        run.status = m.ReconStatus.ERROR
        run.error = f"ERP unavailable: {e}"
        run.finished_at = now
        run.skipped = skipped
        pager.page(run, [])
        return run

    for d in diffs:
        item = m.ReconciliationItem(
            kind=d.kind,
            severity=d.severity,
            invoice_id=d.invoice_id,
            message_id=d.message_id,
            erp_doc_no=d.erp_doc_no,
            detail=d.detail,
        )
        run.items.append(item)
        if d.invoice_id and session.get(m.Invoice, d.invoice_id):
            record_event(
                session,
                d.invoice_id,
                "RECONCILIATION_DIFFERENCE",
                payload={
                    "kind": d.kind,
                    "detail": d.detail,
                    "business_date": day.isoformat(),
                    "run_id": run.run_id,
                },
            )
    run.counts = {
        "messages": len(messages) if messages is not None else None,
        "inbox_matched": len(inbox_ids),
        "posted": len(posted),
        "erp_documents": len(day_docs),
    }
    run.skipped = skipped
    run.differences = len(diffs)
    run.status = (
        m.ReconStatus.DIFFERENCES
        if diffs
        else m.ReconStatus.PARTIAL
        if skipped
        else m.ReconStatus.OK
    )
    run.finished_at = now
    if diffs or skipped:
        pager.page(run, list(run.items))
    return run


# ------------------------------------------------------------------ scheduling


def due(
    session: Session, now: datetime, lookback_days: int = LOOKBACK_DAYS
) -> list[tuple[str, date]]:
    """(entity, day) pairs to run now: today once local time passes 23:30, plus any of the
    last `lookback_days` days without a finished run (the scheduler was down)."""
    out: list[tuple[str, date]] = []
    for eid, ent in ENTITIES.items():
        local = now.astimezone(ent.tz)
        last = local.date() if local.time() >= RUN_AT else local.date() - timedelta(days=1)
        days = [last - timedelta(days=i) for i in range(lookback_days)]
        done = set(
            session.scalars(
                select(m.ReconciliationRun.business_date).where(
                    m.ReconciliationRun.entity_id == eid,
                    m.ReconciliationRun.business_date.in_(days),
                    m.ReconciliationRun.status.in_([s.value for s in DONE]),
                )
            )
        )
        out += [(eid, d) for d in sorted(days) if d not in done]
    return out


def latest_runs(session: Session, entity_ids: Iterable[str]) -> list[m.ReconciliationRun]:
    out = []
    for eid in entity_ids:
        run = session.scalar(
            select(m.ReconciliationRun)
            .where(m.ReconciliationRun.entity_id == eid)
            .order_by(
                m.ReconciliationRun.business_date.desc(), m.ReconciliationRun.started_at.desc()
            )
            .limit(1)
        )
        if run:
            out.append(run)
    return out
