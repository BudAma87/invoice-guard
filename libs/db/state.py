"""Explicit invoice state machine. Every transition is one DB transaction guarded by the
`version` column (optimistic locking) and writes one append-only, hash-chained event."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.db.models import Invoice, InvoiceEvent, State, utcnow

S = State

ALLOWED: dict[State, frozenset[State]] = {
    S.RECEIVED: frozenset({S.EXTRACTING}),
    S.EXTRACTING: frozenset({S.EXTRACTED, S.EXTRACTION_FAILED, S.RECEIVED}),
    S.EXTRACTED: frozenset({S.VALIDATED}),
    S.EXTRACTION_FAILED: frozenset({S.NEEDS_REVIEW, S.REJECTED}),
    S.VALIDATED: frozenset({S.APPROVED_AUTO, S.NEEDS_REVIEW, S.DUPLICATE_SUSPECTED}),
    S.NEEDS_REVIEW: frozenset({S.APPROVED_MANUAL, S.PENDING_CHECKER, S.REJECTED, S.DUPLICATE}),
    S.DUPLICATE_SUSPECTED: frozenset({S.DUPLICATE, S.NEEDS_REVIEW, S.REJECTED}),
    S.PENDING_CHECKER: frozenset({S.APPROVED_MANUAL, S.NEEDS_REVIEW}),
    S.APPROVED_AUTO: frozenset({S.POSTING}),
    S.APPROVED_MANUAL: frozenset({S.POSTING}),
    S.POSTING: frozenset({S.POSTED, S.POST_FAILED, S.APPROVED_AUTO, S.APPROVED_MANUAL}),
    S.POST_FAILED: frozenset({S.NEEDS_REVIEW}),
    S.POSTED: frozenset(),
    S.REJECTED: frozenset(),
    S.DUPLICATE: frozenset(),
}


class IllegalTransition(Exception):
    pass


@dataclass(frozen=True)
class Actor:
    id: str = "system"
    type: str = "system"  # system | user
    role: str | None = None
    ip: str | None = None


SYSTEM = Actor()


def _hash(prev: str | None, body: dict[str, Any]) -> str:
    blob = (prev or "") + json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _body(
    invoice_id: str,
    action: str,
    frm: str | None,
    to: str | None,
    actor: str,
    role: str | None,
    payload: dict[str, Any],
    at: datetime,
) -> dict[str, Any]:
    # Naive-UTC timestamp: SQLite drops tzinfo on read, Postgres keeps it.
    at_utc = at.astimezone(UTC).replace(tzinfo=None) if at.tzinfo else at
    return {
        "invoice_id": invoice_id,
        "action": action,
        "from": frm,
        "to": to,
        "actor": actor,
        "role": role,
        "payload": payload,
        "at": at_utc.isoformat(timespec="microseconds"),
    }


def record_event(
    session: Session,
    invoice_id: str,
    action: str,
    actor: Actor = SYSTEM,
    *,
    from_state: str | None = None,
    to_state: str | None = None,
    payload: dict[str, Any] | None = None,
) -> InvoiceEvent:
    prev = session.scalar(
        select(InvoiceEvent.hash)
        .where(InvoiceEvent.invoice_id == invoice_id)
        .order_by(InvoiceEvent.event_id.desc())
        .limit(1)
    )
    at = utcnow()
    body = _body(invoice_id, action, from_state, to_state, actor.id, actor.role, payload or {}, at)
    ev = InvoiceEvent(
        invoice_id=invoice_id,
        action=action,
        from_state=from_state,
        to_state=to_state,
        actor=actor.id,
        actor_type=actor.type,
        role=actor.role,
        ip=actor.ip,
        payload_json=payload or {},
        at=at,
        prev_hash=prev,
        hash=_hash(prev, body),
    )
    session.add(ev)
    return ev


def transition(
    session: Session,
    inv: Invoice,
    to: State,
    *,
    actor: Actor = SYSTEM,
    action: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Move `inv` to `to`. Caller commits; a concurrent writer raises StaleDataError."""
    frm = State(inv.state)
    if to not in ALLOWED[frm]:
        raise IllegalTransition(f"{frm} -> {to}")
    inv.state = to
    inv.state_since = utcnow()
    record_event(
        session,
        inv.invoice_id,
        action or f"STATE_{to}",
        actor,
        from_state=frm,
        to_state=to,
        payload=payload,
    )


def verify_chain(session: Session, invoice_id: str) -> bool:
    """Recompute the hash chain for one invoice (nightly job; tamper detection)."""
    prev = None
    for ev in session.scalars(
        select(InvoiceEvent)
        .where(InvoiceEvent.invoice_id == invoice_id)
        .order_by(InvoiceEvent.event_id)
    ):
        body = _body(
            ev.invoice_id,
            ev.action,
            ev.from_state,
            ev.to_state,
            ev.actor,
            ev.role,
            ev.payload_json,
            ev.at,
        )
        if ev.prev_hash != prev or _hash(prev, body) != ev.hash:
            return False
        prev = ev.hash
    return True
