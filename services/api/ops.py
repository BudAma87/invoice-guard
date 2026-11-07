"""Ops dashboard series (TSD §7 screen 4). Computed from the event log and live tables."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from libs.core import sla
from libs.core.entities import SLT, entity
from libs.db import models as m

TERMINAL_ACTIONS = {"POSTED", "REJECT", "CONFIRM_DUPLICATE"}
REVIEWER_ACTIONS = {
    "APPROVE",
    "MAKER_APPROVE",
    "REJECT",
    "CONFIRM_DUPLICATE",
    "CHECKER_APPROVE",
    "CHECKER_SEND_BACK",
    "NOT_DUPLICATE",
}


def metrics(session: Session, entities: list[str], now: datetime) -> dict[str, Any]:
    local_now = now.astimezone(SLT)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    inv_ids = select(m.Invoice.invoice_id).where(m.Invoice.entity_id.in_(entities))
    events = session.scalars(
        select(m.InvoiceEvent).where(
            m.InvoiceEvent.at >= day_start, m.InvoiceEvent.invoice_id.in_(inv_ids)
        )
    ).all()

    received = [e for e in events if e.action == "RECEIVED"]
    validated = [e for e in events if e.to_state == m.State.VALIDATED]
    auto = [
        e
        for e in events
        if e.to_state == m.State.APPROVED_AUTO and e.from_state == m.State.VALIDATED
    ]
    posted = [e for e in events if e.action == "POSTED"]
    dup_blocked = [
        e
        for e in events
        if e.action in ("DUPLICATE_MESSAGE", "CONFIRM_DUPLICATE")
        or e.to_state == m.State.DUPLICATE_SUSPECTED
    ]

    tasks = (
        session.execute(
            select(m.ReviewTask).where(
                m.ReviewTask.status == "OPEN", m.ReviewTask.entity_id.in_(entities)
            )
        )
        .scalars()
        .all()
    )
    states: Counter[str] = Counter()
    by_reason: Counter[str] = Counter()
    for t in tasks:
        ent = entity(t.entity_id)
        states[sla.state_of(sla.task_ratio(ent, t, now), sla.on_hold(t, now))] += 1
        for r in t.reasons:
            by_reason[r.split(":")[0]] += 1

    ex_total = (
        session.scalar(
            select(func.count())
            .select_from(m.Extraction)
            .where(m.Extraction.at >= day_start, m.Extraction.invoice_id.in_(inv_ids))
        )
        or 0
    )
    ex_err = (
        session.scalar(
            select(func.count())
            .select_from(m.Extraction)
            .where(
                m.Extraction.at >= day_start,
                m.Extraction.error.is_not(None),
                m.Extraction.invoice_id.in_(inv_ids),
            )
        )
        or 0
    )

    oldest = session.execute(
        select(m.Invoice.invoice_id, m.Invoice.created_at, m.Invoice.state)
        .where(m.Invoice.entity_id.in_(entities), m.Invoice.state.not_in(list(m.TERMINAL)))
        .order_by(m.Invoice.created_at)
        .limit(1)
    ).first()

    hours = list(range(24))

    def per_hour(evts: list[m.InvoiceEvent]) -> list[int]:
        c = Counter(e.at.astimezone(SLT).hour for e in evts)
        return [c.get(h, 0) for h in hours]

    arrivals = per_hour(received)
    processed = per_hour([e for e in events if e.action in TERMINAL_ACTIONS])
    reviewer = per_hour([e for e in events if e.action in REVIEWER_ACTIONS])
    backlog, run = [], 0
    for a, p in zip(arrivals, processed, strict=True):
        run = max(0, run + a - p)
        backlog.append(run)
    upto = local_now.hour + 1

    reviewers_on_shift = 4
    capacity_per_h = reviewers_on_shift * 24  # 2.5 min per exception
    return {
        "as_of": now.isoformat(),
        "entities": entities,
        "month_end_window": sla.in_month_end_window(entity(entities[0]), local_now.date())
        if entities
        else False,
        "tiles": {
            "received_today": len(received),
            "auto_pass_rate": round(len(auto) / len(validated), 3) if validated else None,
            "queue_depth": len(tasks),
            "sla_at_risk": states[sla.SlaState.AT_RISK],
            "sla_breached": states[sla.SlaState.BREACHED],
            "on_hold": states[sla.SlaState.ON_HOLD],
            "posted_today": len(posted),
            "duplicates_blocked": len(dup_blocked),
            "provider_error_rate": round(ex_err / ex_total, 3) if ex_total else 0.0,
            "oldest_open": {
                "invoice_id": oldest[0],
                "age_minutes": int((now - oldest[1]).total_seconds() // 60),
                "state": oldest[2],
            }
            if oldest
            else None,
        },
        "series": {
            "hours": hours[:upto],
            "arrivals": arrivals[:upto],
            "processed": processed[:upto],
            "reviewer_throughput": reviewer[:upto],
            "backlog": backlog[:upto],
            "capacity_per_hour": capacity_per_h,
        },
        "queue_by_reason": [{"reason": r, "count": c} for r, c in by_reason.most_common()],
    }
