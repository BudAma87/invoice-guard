"""Human review workflow and controls (TSD §8).

A person can change what posts, but never alone at or above LKR 1M, never without a
reason code, and never outside their entities. Every action is an append-only event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from libs.config import settings
from libs.core import sla
from libs.core.convert import apply_corrections
from libs.core.entities import entity
from libs.core.service import (
    Evaluation,
    create_task,
    current_results,
    enqueue_post,
    evaluate_invoice,
    open_task,
    persist_results,
)
from libs.db import models as m
from libs.db.session import is_postgres
from libs.db.state import Actor, record_event, transition
from libs.erp_adapter.protocol import ERPAdapter
from libs.rules import Override, approval_check, diff_results, needs_checker
from libs.rules.config import ReasonCode, reason_valid


@dataclass(frozen=True)
class User:
    id: str
    name: str
    roles: frozenset[str]
    entities: frozenset[str]

    def can(self, role: str) -> bool:
        return role in self.roles

    def actor(self, role: str, ip: str | None = None) -> Actor:
        return Actor(self.id, "user", role, ip)


class ReviewError(Exception):
    status = 400

    def __init__(self, msg: str, **extra: Any):
        super().__init__(msg)
        self.extra = extra


class NotFound(ReviewError):
    status = 404


class Forbidden(ReviewError):
    status = 403


class Conflict(ReviewError):
    status = 409


class Unprocessable(ReviewError):
    status = 422


def lease_seconds() -> int:
    return settings.review_lease_s


# ------------------------------------------------------------------ claiming


def _lease_valid(task: m.ReviewTask, now: datetime) -> bool:
    return bool(task.assignee and task.lease_until and task.lease_until > now)


def claim(session: Session, task: m.ReviewTask, user: User, now: datetime) -> m.ReviewTask:
    if _lease_valid(task, now) and task.assignee != user.id:
        raise Conflict(f"Claimed by {task.assignee}", assignee=task.assignee)
    _check_task_role(task, user)
    task.assignee = user.id
    task.claimed_at = task.claimed_at if task.assignee == user.id and task.claimed_at else now
    task.lease_until = now + timedelta(seconds=lease_seconds())
    record_event(
        session,
        task.invoice_id,
        "TASK_CLAIMED",
        user.actor(_role_for(task)),
        payload={"task_id": task.task_id, "lease_until": task.lease_until.isoformat()},
    )
    return task


def release(session: Session, task: m.ReviewTask, user: User, now: datetime) -> None:
    if task.assignee != user.id and not user.can("supervisor"):
        raise Forbidden("Only the assignee or a supervisor can release")
    task.assignee = None
    task.lease_until = None
    record_event(session, task.invoice_id, "TASK_RELEASED", user.actor(_role_for(task)))


def _role_for(task: m.ReviewTask) -> str:
    return "checker" if task.kind == m.TaskKind.CHECK else "reviewer"


def _check_task_role(task: m.ReviewTask, user: User) -> None:
    if task.entity_id not in user.entities:
        raise NotFound("Not found")
    if task.kind == m.TaskKind.CHECK:
        if not user.can("checker"):
            raise Forbidden("Checker role required")
        if task.maker_id == user.id:
            raise Forbidden("You made this decision; a different checker must approve it")
    elif not (user.can("reviewer") or user.can("supervisor")):
        raise Forbidden("Reviewer role required")


def next_task(session: Session, user: User, kind: str, now: datetime) -> m.ReviewTask | None:
    kinds = [m.TaskKind.CHECK] if kind == "check" else [m.TaskKind.REVIEW, m.TaskKind.MANUAL_ENTRY]
    q = (
        select(m.ReviewTask)
        .where(
            m.ReviewTask.status == "OPEN",
            m.ReviewTask.kind.in_(kinds),
            m.ReviewTask.entity_id.in_(user.entities),
            or_(
                m.ReviewTask.lease_until.is_(None),
                m.ReviewTask.lease_until < now,
                m.ReviewTask.assignee == user.id,
            ),
            or_(m.ReviewTask.hold_until.is_(None), m.ReviewTask.hold_until < now),
        )
        # breached first, then highest priority
        .order_by((m.ReviewTask.sla_due_at < now).desc(), m.ReviewTask.priority.desc())
        .limit(1)
    )
    if kind == "check":
        q = q.where(or_(m.ReviewTask.maker_id.is_(None), m.ReviewTask.maker_id != user.id))
    if is_postgres(session):
        q = q.with_for_update(skip_locked=True)
    task = session.scalar(q)
    if task:
        claim(session, task, user, now)
    return task


# ------------------------------------------------------------------ validate (draft)


@dataclass
class ValidateOut:
    results: list[dict[str, Any]]
    diff: list[dict[str, Any]]
    allowed: bool
    problems: list[str]
    needs_checker: bool
    amount_lkr_minor: int | None
    erp_available: bool
    ev: Evaluation = field(repr=False)


def _overrides(raw: dict[str, Any] | None) -> dict[str, Override]:
    return {
        k: Override(v.get("reason_code", ""), v.get("comment", "") or "")
        for k, v in (raw or {}).items()
    }


async def validate_draft(
    session: Session,
    erp: ERPAdapter,
    inv: m.Invoice,
    corrections: dict[str, Any] | None,
    overrides: dict[str, Any] | None,
    now: datetime,
) -> ValidateOut:
    """Re-run the exact rules on draft corrections. Nothing is saved."""
    ev = await evaluate_invoice(session, erp, inv, now, corrections)
    out = ev.result_dicts
    chk = approval_check(out, _overrides(overrides))
    cfg = entity(inv.entity_id).rule_set
    amount = ev.enriched.amount_lkr_minor
    return ValidateOut(
        results=out,
        diff=diff_results(current_results(session, inv), out),
        allowed=chk.allowed and ev.enriched.context.erp_available,
        problems=list(chk.problems)
        + (
            []
            if ev.enriched.context.erp_available
            else ["ERP unavailable: reference rules not checked"]
        ),
        needs_checker=amount is not None and needs_checker(amount, cfg),
        amount_lkr_minor=amount,
        erp_available=ev.enriched.context.erp_available,
        ev=ev,
    )


def results_digest(results: list[dict[str, Any]]) -> str:
    import hashlib
    import json

    sig = sorted((r["key"], r["status"]) for r in results)
    return hashlib.sha256(json.dumps(sig).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ decisions


REVIEWABLE = {m.State.NEEDS_REVIEW, m.State.DUPLICATE_SUSPECTED, m.State.EXTRACTION_FAILED}


def _require_lease(task: m.ReviewTask | None, user: User, now: datetime) -> m.ReviewTask:
    if task is None:
        raise Conflict("No open task for this invoice")
    _check_task_role(task, user)
    if not (_lease_valid(task, now) and task.assignee == user.id):
        raise Conflict(
            "Your claim on this invoice expired or was taken — reload", assignee=task.assignee
        )
    return task


def _require_reason(code: str | None, comment: str | None) -> None:
    if not reason_valid(code, comment):
        raise Unprocessable("A valid reason code is required (OTHER needs a 20+ character comment)")


async def decide(
    session: Session,
    erp: ERPAdapter,
    inv: m.Invoice,
    user: User,
    *,
    action: str,
    expected_version: int,
    corrections: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
    reason_code: str | None = None,
    comment: str | None = None,
    results_digest_seen: str | None = None,
    now: datetime,
    ip: str | None = None,
) -> dict[str, Any]:
    if inv.version != expected_version:
        raise Conflict("Invoice changed since you loaded it — reload", version=inv.version)
    if inv.state not in REVIEWABLE:
        raise Conflict(f"Invoice is {inv.state}; no review decision possible")
    task = _require_lease(open_task(session, inv.invoice_id), user, now)
    actor = user.actor("reviewer", ip)
    before = current_results(session, inv)

    if action == "hold":
        if not comment or len(comment.strip()) < 5:
            raise Unprocessable("A note for the supplier query is required")
        task.hold_until = sla.hold_until(entity(inv.entity_id), now)
        task.hold_note = comment
        record_event(
            session,
            inv.invoice_id,
            "HOLD",
            actor,
            payload={"until": task.hold_until.isoformat(), "note": comment},
        )
        return {"state": inv.state, "hold_until": task.hold_until.isoformat()}

    if action == "reject":
        _require_reason(reason_code, comment)
        _close(task, user, action, reason_code, comment, now)
        transition(
            session,
            inv,
            m.State.REJECTED,
            actor=actor,
            action="REJECT",
            payload={"reason_code": reason_code, "comment": comment},
        )
        return {"state": inv.state}

    if action == "duplicate":
        reason_code = reason_code or ReasonCode.DUPLICATE_CONFIRMED
        _require_reason(reason_code, comment)
        _close(task, user, action, reason_code, comment, now)
        transition(
            session,
            inv,
            m.State.DUPLICATE,
            actor=actor,
            action="CONFIRM_DUPLICATE",
            payload={"reason_code": reason_code, "duplicate_of": inv.duplicate_of},
        )
        return {"state": inv.state}

    if action == "not_duplicate":
        if inv.state != m.State.DUPLICATE_SUSPECTED:
            raise Conflict("Invoice is not a suspected duplicate")
        _require_reason(reason_code, comment)
        transition(
            session,
            inv,
            m.State.NEEDS_REVIEW,
            actor=actor,
            action="NOT_DUPLICATE",
            payload={"reason_code": reason_code, "comment": comment},
        )
        inv.duplicate_of = None
        return {"state": inv.state}

    if action not in ("approve", "approve_with_override"):
        raise Unprocessable(f"Unknown action {action}")

    # Approve: the server re-validates; refuse if the result changed under the reviewer.
    out = await validate_draft(session, erp, inv, corrections, overrides, now)
    if results_digest_seen and results_digest(out.results) != results_digest_seen:
        raise Conflict(
            "Rule results changed since your last check (e.g. PO consumed) — review "
            "the new results",
            results=out.results,
        )
    if not out.allowed:
        raise Unprocessable("Approval not allowed", problems=out.problems, results=out.results)
    if action == "approve" and overrides:
        action = "approve_with_override"

    changed = apply_corrections(inv, corrections) if corrections else {}
    period_override = next(
        (r for r in out.results if r["code"] == "DATE_PERIOD_OPEN" and r["status"] == "FAIL"), None
    )
    if (
        period_override
        and (overrides or {}).get("DATE_PERIOD_OPEN", {}).get("reason_code")
        == ReasonCode.PERIOD_CLOSED
    ):
        inv.posting_period = period_override["expected"]

    from libs.core.service import claim_business_key

    if not claim_business_key(session, inv, out.ev.enriched.business_key):
        raise Conflict("Corrected values match an invoice already in flight or posted")
    persist_results(session, inv, out.ev)

    task.corrections = corrections or {}
    task.overrides = overrides or {}
    task.results_before = before
    task.results_after = out.results
    _close(task, user, action, reason_code, comment, now)
    payload = {
        "changed": changed,
        "overrides": overrides or {},
        "reason_code": reason_code,
        "comment": comment,
        "results_before": _brief(before),
        "results_after": _brief(out.results),
    }

    if inv.state in (m.State.DUPLICATE_SUSPECTED, m.State.EXTRACTION_FAILED):
        transition(session, inv, m.State.NEEDS_REVIEW, actor=actor, action="REVIEWED")
    if out.needs_checker:
        transition(
            session,
            inv,
            m.State.PENDING_CHECKER,
            actor=actor,
            action="MAKER_APPROVE",
            payload=payload,
        )
        create_task(
            session, inv, m.TaskKind.CHECK, ["MAKER_CHECKER"], now, maker_id=user.id, carry=task
        )
    else:
        transition(
            session, inv, m.State.APPROVED_MANUAL, actor=actor, action="APPROVE", payload=payload
        )
        enqueue_post(session, inv)
    return {"state": inv.state, "needs_checker": out.needs_checker}


async def check(
    session: Session,
    erp: ERPAdapter,
    inv: m.Invoice,
    user: User,
    *,
    action: str,
    expected_version: int,
    comment: str | None,
    now: datetime,
    ip: str | None = None,
) -> dict[str, Any]:
    if inv.version != expected_version:
        raise Conflict("Invoice changed since you loaded it — reload")
    if inv.state != m.State.PENDING_CHECKER:
        raise Conflict(f"Invoice is {inv.state}, not pending checker")
    task = _require_lease(open_task(session, inv.invoice_id), user, now)
    if task.maker_id == user.id:
        raise Forbidden("checker_id must differ from maker_id")
    actor = user.actor("checker", ip)
    if action == "send_back":
        if not comment or not comment.strip():
            raise Unprocessable("A comment is required to send back")
        _close(task, user, "send_back", None, comment, now, checker=True)
        transition(
            session,
            inv,
            m.State.NEEDS_REVIEW,
            actor=actor,
            action="CHECKER_SEND_BACK",
            payload={"comment": comment, "maker_id": task.maker_id},
        )
        create_task(session, inv, m.TaskKind.REVIEW, ["SENT_BACK"], now, assignee=task.maker_id)
        return {"state": inv.state}
    if action != "approve":
        raise Unprocessable(f"Unknown action {action}")
    out = await validate_draft(session, erp, inv, None, task.overrides, now)
    if not out.allowed:
        raise Conflict(
            "Rules no longer pass with the maker's overrides — send back", problems=out.problems
        )
    persist_results(session, inv, out.ev)
    _close(task, user, "approve", None, comment, now, checker=True)
    transition(
        session,
        inv,
        m.State.APPROVED_MANUAL,
        actor=actor,
        action="CHECKER_APPROVE",
        payload={"maker_id": task.maker_id, "checker_id": user.id, "comment": comment},
    )
    enqueue_post(session, inv)
    return {"state": inv.state}


def _close(
    task: m.ReviewTask,
    user: User,
    decision: str,
    reason: str | None,
    comment: str | None,
    now: datetime,
    checker: bool = False,
) -> None:
    task.status = "DONE"
    task.decision = decision if not checker else task.decision
    if checker:
        task.checker_id = user.id
        task.checked_at = now
        if comment:
            task.comment = (task.comment or "") + f"\n[checker] {comment}"
    else:
        task.maker_id = user.id
        task.reason_code = reason
        task.comment = comment
        task.decided_at = now
    task.lease_until = None


def _brief(results: list[dict[str, Any]]) -> dict[str, str]:
    return {r["key"]: r["status"] for r in results}
