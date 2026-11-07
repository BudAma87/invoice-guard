"""SLA scheduler (TSD §8 "SLA and escalation"). Runs every 5 minutes; safe to run twice.

For each open review task:
1. Bank an expired hold: the hold's business time is added to `sla_paused_seconds` and the
   hold is cleared, so the task returns to the queue with its clock resumed.
2. Recompute `sla_due_at` (pushed out by time on hold) and the queue priority.
3. Escalate, once per level, never downwards:
   75 % → warn the assignee and team lead · 100 % → breach: move to the supervisor pool and
   notify · 150 % (125 % in the month-end window) → notify the finance manager.
   Tasks on hold are not escalated: their clock is paused.

Notifications are rows in `notification`, unique per (task, kind), so a re-run never sends
twice. A `Notifier` delivers PENDING rows; email is one implementation, a log another.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from libs.core import sla
from libs.core.entities import entity
from libs.db import models as m
from libs.db.state import record_event
from libs.rules import priority

log = logging.getLogger("sla")

BLOCKING_CODES = frozenset({"SUPPLIER_INACTIVE", "DUP_EXACT"})
SUPERVISOR_POOL = "supervisor"

LEVEL_KIND = {1: "SLA_WARNING", 2: "SLA_BREACH", 3: "SLA_ESCALATION"}


def priority_inputs(task: m.ReviewTask, inv: m.Invoice) -> tuple[bool, float]:
    """(blocker, duplicate suspicion) from the task's reasons, as at creation."""
    reasons = {r.split(":")[0] for r in task.reasons or []}
    blocker = bool(reasons & BLOCKING_CODES) or inv.state == m.State.DUPLICATE_SUSPECTED
    dup = 1.0 if "DUP_EXACT" in reasons else 0.6 if "DUP_FUZZY" in reasons else 0.0
    return blocker, dup


def compute_priority(task: m.ReviewTask, inv: m.Invoice, now: datetime) -> float:
    blocker, dup = priority_inputs(task, inv)
    ratio = sla.task_ratio(entity(task.entity_id), task, now)
    return priority(
        blocker=blocker,
        sla_used_ratio=ratio,
        amount_lkr_minor=inv.amount_lkr_minor or 0,
        duplicate_suspicion=dup,
    )


def recipients(level: int, task: m.ReviewTask) -> list[str]:
    e = task.entity_id
    if level == 1:
        return ([f"user:{task.assignee}"] if task.assignee else []) + [f"role:supervisor@{e}"]
    if level == 2:
        return [f"role:supervisor@{e}"] + ([f"user:{task.assignee}"] if task.assignee else [])
    return [f"role:finance_manager@{e}", f"role:supervisor@{e}"]


def _message(
    level: int, task: m.ReviewTask, inv: m.Invoice, pct: int, due: datetime
) -> tuple[str, str]:
    ref = f"{inv.supplier_name or 'Unknown supplier'} {inv.invoice_no_raw or inv.invoice_id[:8]}"
    head = {1: "SLA at risk", 2: "SLA breached", 3: "SLA escalation"}[level]
    subject = f"[{head}] {ref} ({task.entity_id}) — {pct}% of SLA used"
    reasons = ", ".join(task.reasons or []) or "—"
    action = {
        1: "Please finish or hand it over before the deadline.",
        2: "Moved to the supervisor pool for reassignment.",
        3: "Second escalation: still open well past its SLA.",
    }[level]
    body = (
        f"{ref}\nState: {inv.state}\nReasons: {reasons}\nDue: {due.isoformat()}\n"
        f"Assignee: {task.assignee or 'unassigned'}\n\n{action}"
    )
    return subject, body


@dataclass
class TickReport:
    tasks: int = 0
    holds_resumed: int = 0
    escalated: dict[int, int] = field(default_factory=lambda: {1: 0, 2: 0, 3: 0})
    notifications: int = 0
    reprioritised: int = 0


def sla_tick(session: Session, now: datetime) -> TickReport:
    """One scheduler pass. Caller commits."""
    rep = TickReport()
    rows = session.execute(
        select(m.ReviewTask, m.Invoice)
        .join(m.Invoice, m.Invoice.invoice_id == m.ReviewTask.invoice_id)
        .where(m.ReviewTask.status == "OPEN")
    ).all()
    for task, inv in rows:
        rep.tasks += 1
        ent = entity(task.entity_id)

        # 1. bank an expired hold
        if task.hold_started_at and task.hold_until and task.hold_until <= now:
            held = sla.business_seconds_between(ent, task.hold_started_at, task.hold_until)
            task.sla_paused_seconds = int((task.sla_paused_seconds or 0) + held)
            record_event(
                session,
                task.invoice_id,
                "HOLD_ENDED",
                payload={"paused_business_seconds": int(held), "note": task.hold_note},
            )
            task.hold_started_at = None
            task.hold_until = None
            rep.holds_resumed += 1

        # 2. due date and priority
        due = sla.task_due(ent, task, now)
        if task.sla_due_at != due:
            task.sla_due_at = due
        new_p = compute_priority(task, inv, now)
        if abs((task.priority or 0) - new_p) >= 0.01:
            task.priority = new_p
            rep.reprioritised += 1

        # 3. escalate, once per level
        if sla.on_hold(task, now):
            continue
        ratio = sla.task_ratio(ent, task, now)
        target = sla.escalation_level(ent, ratio, now)
        while (task.escalation_level or 0) < target:
            level = (task.escalation_level or 0) + 1
            task.escalation_level = level
            rep.escalated[level] += 1
            if level >= 2:
                task.pool = SUPERVISOR_POOL
            subject, body = _message(level, task, inv, int(ratio * 100), due)
            if _notify(session, task, level, subject, body, now):
                rep.notifications += 1
            record_event(
                session,
                task.invoice_id,
                LEVEL_KIND[level],
                payload={
                    "level": level,
                    "sla_used_pct": int(ratio * 100),
                    "recipients": recipients(level, task),
                    "pool": task.pool,
                },
            )
    return rep


def _notify(
    session: Session, task: m.ReviewTask, level: int, subject: str, body: str, now: datetime
) -> bool:
    """Insert one notification row; False when this (task, kind) was already raised."""
    try:
        with session.begin_nested():
            session.add(
                m.Notification(
                    task_id=task.task_id,
                    invoice_id=task.invoice_id,
                    entity_id=task.entity_id,
                    kind=LEVEL_KIND[level],
                    recipients=recipients(level, task),
                    subject=subject,
                    body=body,
                    created_at=now,
                )
            )
            session.flush()
        return True
    except IntegrityError:
        return False


# --------------------------------------------------------------------------- delivery


class Notifier(Protocol):
    def send(self, note: m.Notification) -> None: ...


class LogNotifier:
    """Dev sender: logs instead of emailing. Swap for SMTP/Graph/Teams in production."""

    def send(self, note: m.Notification) -> None:
        log.warning("NOTIFY %s → %s: %s", note.kind, ", ".join(note.recipients), note.subject)


def deliver_pending(session: Session, notifier: Notifier, now: datetime, limit: int = 100) -> int:
    sent = 0
    for note in session.scalars(
        select(m.Notification)
        .where(m.Notification.status == "PENDING")
        .order_by(m.Notification.id)
        .limit(limit)
    ):
        note.attempts += 1
        try:
            notifier.send(note)
        except Exception as e:  # noqa: BLE001 - keep the row; retry next tick
            note.last_error = str(e)[:500]
            if note.attempts >= 5:
                note.status = "FAILED"
            continue
        note.status = "SENT"
        note.sent_at = now
        sent += 1
    return sent
