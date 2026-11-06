"""Evaluate, persist, route. Shared by the worker (steps 6-10) and the review API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from libs.core import sla
from libs.core.convert import to_rules
from libs.core.enrich import Enriched, build_context
from libs.core.entities import entity
from libs.db import models as m
from libs.db.state import SYSTEM, Actor, record_event, transition
from libs.erp_adapter.protocol import ERPAdapter
from libs.rules import RouteKind, decide, evaluate, priority
from libs.rules import types as rt
from libs.rules.decide import Route


@dataclass
class Evaluation:
    invoice: rt.Invoice
    results: list[rt.RuleResult]
    enriched: Enriched
    route: Route
    rule_set_version: str

    @property
    def result_dicts(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.results]

    @property
    def has_blocker(self) -> bool:
        return any(r.is_blocker for r in self.results)

    @property
    def duplicate_suspicion(self) -> float:
        codes = {r.code for r in self.results if r.status == rt.Status.FAIL}
        return 1.0 if "DUP_EXACT" in codes else 0.6 if "DUP_FUZZY" in codes else 0.0


async def evaluate_invoice(
    session: Session,
    erp: ERPAdapter,
    inv: m.Invoice,
    now: datetime,
    corrections: dict[str, Any] | None = None,
) -> Evaluation:
    ent = entity(inv.entity_id)
    cfg = ent.rule_set
    rinv = to_rules(inv, corrections)
    enriched = await build_context(session, erp, rinv, ent.today(now), cfg.dup_fuzzy_days)
    results = evaluate(rinv, enriched.context, cfg)
    route = decide(
        rinv,
        results,
        cfg,
        amount_lkr_minor=enriched.amount_lkr_minor,
        duplicate_suspected=inv.state == m.State.DUPLICATE_SUSPECTED,
    )
    return Evaluation(rinv, results, enriched, route, cfg.version)


def persist_results(session: Session, inv: m.Invoice, ev: Evaluation) -> str:
    eval_id = m.new_id()
    for r in ev.results:
        d = r.to_dict()
        session.add(
            m.RuleResultRow(
                invoice_id=inv.invoice_id,
                evaluation_id=eval_id,
                rule_set_version=ev.rule_set_version,
                rule_key=d["key"],
                rule_code=d["code"],
                severity=d["severity"],
                status=d["status"],
                stage=d["stage"],
                group=d["group"],
                line=d["line"],
                expected=d["expected"],
                actual=d["actual"],
                message=d["message"],
                fields=d["fields"],
                needs=d["needs"],
            )
        )
    inv.current_evaluation_id = eval_id
    inv.rule_set_version = ev.rule_set_version
    if ev.enriched.supplier_id:
        inv.supplier_id = ev.enriched.supplier_id
    inv.amount_lkr_minor = ev.enriched.amount_lkr_minor
    return eval_id


def current_results(session: Session, inv: m.Invoice) -> list[dict[str, Any]]:
    if not inv.current_evaluation_id:
        return []
    rows = session.scalars(
        select(m.RuleResultRow)
        .where(
            m.RuleResultRow.invoice_id == inv.invoice_id,
            m.RuleResultRow.evaluation_id == inv.current_evaluation_id,
        )
        .order_by(m.RuleResultRow.id)
    )
    return [r.to_dict() for r in rows]


def claim_business_key(session: Session, inv: m.Invoice, key: str | None) -> bool:
    """Set the unique business key. False when another live invoice holds it."""
    if key is None or inv.business_key == key:
        return True
    try:
        with session.begin_nested():
            inv.business_key = key
            session.flush()
        return True
    except IntegrityError:
        inv.business_key = None
        holder = session.scalar(
            select(m.Invoice.invoice_id).where(
                m.Invoice.business_key == key, m.Invoice.state != m.State.REJECTED
            )
        )
        inv.duplicate_of = holder
        return False


# ------------------------------------------------------------------ tasks


def task_priority(
    inv: m.Invoice, task: m.ReviewTask, now: datetime, blocker: bool, dup: float
) -> float:
    ent = entity(inv.entity_id)
    ratio = sla.used_ratio(ent, task.created_at, task.sla_due_at, now)
    return priority(
        blocker=blocker,
        sla_used_ratio=ratio,
        amount_lkr_minor=inv.amount_lkr_minor or 0,
        duplicate_suspicion=dup,
    )


def open_task(session: Session, invoice_id: str) -> m.ReviewTask | None:
    return session.scalar(
        select(m.ReviewTask).where(
            m.ReviewTask.invoice_id == invoice_id, m.ReviewTask.status == "OPEN"
        )
    )


def create_task(
    session: Session,
    inv: m.Invoice,
    kind: m.TaskKind,
    reasons: list[str],
    now: datetime,
    *,
    blocker: bool = False,
    dup: float = 0.0,
    assignee: str | None = None,
    maker_id: str | None = None,
    carry: m.ReviewTask | None = None,
) -> m.ReviewTask:
    prev = open_task(session, inv.invoice_id)
    if prev:
        prev.status = "DONE"
        session.flush()
    ent = entity(inv.entity_id)
    secs = sla.sla_seconds(ent, now)
    t = m.ReviewTask(
        invoice_id=inv.invoice_id,
        entity_id=inv.entity_id,
        kind=kind,
        reasons=reasons,
        created_at=now,
        sla_seconds=secs,
        sla_due_at=sla.add_business_seconds(ent, now, secs),
        assignee=assignee,
        maker_id=maker_id,
    )
    if carry:  # checker task carries the maker's decision for the diff view
        for f in (
            "decision",
            "reason_code",
            "comment",
            "corrections",
            "overrides",
            "results_before",
            "results_after",
        ):
            setattr(t, f, getattr(carry, f))
    t.priority = task_priority(inv, t, now, blocker, dup)
    session.add(t)
    record_event(
        session,
        inv.invoice_id,
        f"TASK_{kind}_CREATED",
        payload={"reasons": reasons, "sla_due_at": t.sla_due_at.isoformat()},
    )
    return t


def enqueue_post(session: Session, inv: m.Invoice) -> m.Outbox:
    """Write the outbox row in the same transaction as the approval state change."""
    ob = m.Outbox(
        invoice_id=inv.invoice_id, entity_id=inv.entity_id, idempotency_key=inv.invoice_id
    )
    session.add(ob)
    return ob


# ------------------------------------------------------------------ pipeline routing


def route_validated(
    session: Session, inv: m.Invoice, ev: Evaluation, now: datetime, actor: Actor = SYSTEM
) -> None:
    """EXTRACTED -> VALIDATED -> APPROVED_AUTO | NEEDS_REVIEW | DUPLICATE_SUSPECTED."""
    persist_results(session, inv, ev)
    transition(
        session,
        inv,
        m.State.VALIDATED,
        actor=actor,
        payload={
            "rule_set_version": ev.rule_set_version,
            "failed": [r.key for r in ev.results if r.status != "PASS"],
        },
    )
    key_ok = claim_business_key(session, inv, ev.enriched.business_key)
    reasons = list(ev.route.reasons)
    if not key_ok or any(r.code == "DUP_EXACT" and r.status == "FAIL" for r in ev.results):
        if "DUP_EXACT" not in reasons:
            reasons.insert(0, "DUP_EXACT")
        transition(
            session,
            inv,
            m.State.DUPLICATE_SUSPECTED,
            actor=actor,
            payload={"duplicate_of": inv.duplicate_of},
        )
        create_task(session, inv, m.TaskKind.REVIEW, reasons, now, blocker=True, dup=1.0)
    elif ev.route.kind == RouteKind.AUTO_APPROVE:
        transition(session, inv, m.State.APPROVED_AUTO, actor=actor)
        enqueue_post(session, inv)
    else:
        transition(session, inv, m.State.NEEDS_REVIEW, actor=actor, payload={"reasons": reasons})
        create_task(
            session,
            inv,
            m.TaskKind.REVIEW,
            reasons,
            now,
            blocker=ev.route.kind == RouteKind.BLOCKED,
            dup=ev.duplicate_suspicion,
        )
