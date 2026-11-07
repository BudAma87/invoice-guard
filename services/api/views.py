"""Read models for the UI. Kept apart from routes so the shapes are easy to see."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.core import sla
from libs.core.convert import header_dict, line_dict
from libs.core.entities import entity
from libs.core.escalation import compute_priority
from libs.core.review import User, results_digest
from libs.core.service import current_results, open_task
from libs.core.store import sign
from libs.db import models as m
from libs.erp_adapter.protocol import ERPUnavailable
from libs.rules.decide import needs_checker
from libs.rules.reference import rank_po_lines
from libs.rules.types import Line
from services.api.auth import DEV_USERS


def user_name(uid: str | None) -> str | None:
    if uid is None:
        return None
    u = DEV_USERS.get(uid)
    return u.name if u else uid


def task_view(t: m.ReviewTask | None, now: datetime, user: User | None = None) -> dict | None:
    if t is None:
        return None
    ent = entity(t.entity_id)
    on_hold = sla.on_hold(t, now)
    ratio = sla.task_ratio(ent, t, now)
    due = sla.task_due(ent, t, now)
    lease_ok = bool(t.assignee and t.lease_until and t.lease_until > now)
    return {
        "task_id": t.task_id,
        "kind": t.kind,
        "status": t.status,
        "reasons": t.reasons,
        "priority": t.priority,
        "sla_due_at": due.isoformat(),
        "sla_ratio": round(ratio, 3),
        "sla_state": sla.state_of(ratio, on_hold),
        "assignee": t.assignee if lease_ok else None,
        "assignee_name": user_name(t.assignee) if lease_ok else None,
        "lease_until": t.lease_until.isoformat() if lease_ok and t.lease_until else None,
        "claimed_by_me": bool(user and lease_ok and t.assignee == user.id),
        "hold_until": t.hold_until.isoformat() if on_hold and t.hold_until else None,
        "hold_note": t.hold_note,
        "escalation_level": t.escalation_level or 0,
        "pool": t.pool,
        "maker_id": t.maker_id,
        "maker_name": user_name(t.maker_id),
        "decision": t.decision,
        "reason_code": t.reason_code,
        "comment": t.comment,
        "corrections": t.corrections,
        "overrides": t.overrides,
        "results_before": t.results_before,
        "results_after": t.results_after,
    }


def queue_row(t: m.ReviewTask, inv: m.Invoice, now: datetime, user: User) -> dict:
    return {
        **(task_view(t, now, user) or {}),
        "priority": compute_priority(t, inv, now),  # live: rises as the SLA burns
        "invoice_id": inv.invoice_id,
        "state": inv.state,
        "entity_id": inv.entity_id,
        "supplier_name": inv.supplier_name,
        "invoice_no": inv.invoice_no_raw,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "total_minor": inv.total_minor,
        "currency": inv.currency,
        "amount_lkr_minor": inv.amount_lkr_minor,
    }


def pdf_link(sha: str | None) -> dict | None:
    if not sha:
        return None
    exp, sig = sign(sha)
    return {
        "url": f"/files/{sha}.pdf?exp={exp}&sig={sig}",
        "expires_at": datetime.fromtimestamp(exp, UTC).isoformat(),
    }


async def invoice_detail(
    session: Session, erp: Any, inv: m.Invoice, user: User, now: datetime
) -> dict:
    ent = entity(inv.entity_id)
    task = open_task(session, inv.invoice_id)
    results = current_results(session, inv)
    po_lines: list[dict] = []
    period = None
    po_meta = None
    erp_ok = True
    try:
        if inv.po_number:
            po = await erp.get_po(inv.entity_id, inv.po_number)
            if po:
                po_meta = {
                    "po_number": po.po_number,
                    "approved_on": po.approved_on.isoformat(),
                    "remaining_minor": po.remaining_minor,
                    "currency": po.currency,
                    "supplier_id": po.supplier_id,
                }
                for ln in inv.lines:
                    rl = Line(ln.line_no, ln.description, None, None, None, ln.item_code)
                    ranked = rank_po_lines(rl, tuple(p for p in po.lines if p.open_qty > 0))
                    po_lines.append(
                        {
                            "line_no": ln.line_no,
                            "candidates": [
                                {
                                    "po_line_id": p.po_line_id,
                                    "line_no": p.line_no,
                                    "item_code": p.item_code,
                                    "description": p.description,
                                    "price_minor": p.price_minor,
                                    "open_qty": str(p.open_qty),
                                    "uom": p.uom,
                                    "score": round(sc, 3),
                                }
                                for p, sc in ranked
                            ],
                        }
                    )
        if inv.invoice_date:
            p = await erp.is_period_open(inv.entity_id, inv.invoice_date)
            period = {"period": p.period, "open": p.open, "first_open": p.first_open}
    except ERPUnavailable:
        erp_ok = False

    dup = None
    dup_id = inv.duplicate_of or next(
        (
            r["actual"][0]
            for r in results
            if r["code"] in ("DUP_EXACT", "DUP_FUZZY") and r["status"] == "FAIL" and r["actual"]
        ),
        None,
    )
    if dup_id:
        other = session.get(m.Invoice, dup_id)
        if other and other.entity_id in user.entities:
            dup = {
                "invoice_id": other.invoice_id,
                "state": other.state,
                "header": header_dict(other),
                "erp_doc_no": other.erp_doc_no,
                "pdf": pdf_link(other.pdf_sha256),
                "lines": [line_dict(x) for x in other.lines],
            }

    events = session.scalars(
        select(m.InvoiceEvent)
        .where(m.InvoiceEvent.invoice_id == inv.invoice_id)
        .order_by(m.InvoiceEvent.event_id)
    ).all()
    cfg = ent.rule_set
    return {
        "invoice_id": inv.invoice_id,
        "entity": {"id": ent.entity_id, "name": ent.name},
        "state": inv.state,
        "version": inv.version,
        "supplier_id": inv.supplier_id,
        "header": header_dict(inv),
        "date_ambiguous": inv.date_ambiguous,
        "confidence": inv.confidence,
        "evidence": inv.evidence,
        "lines": [line_dict(x) for x in inv.lines],
        "rule_set_version": inv.rule_set_version,
        "results": results,
        "results_digest": results_digest(results),
        "amount_lkr_minor": inv.amount_lkr_minor,
        "needs_checker": bool(
            inv.amount_lkr_minor is not None and needs_checker(inv.amount_lkr_minor, cfg)
        ),
        "maker_checker_lkr_minor": cfg.maker_checker_lkr_minor,
        "min_confidence": cfg.min_confidence,
        "posting_period": inv.posting_period,
        "period": period,
        "po": po_meta,
        "po_line_candidates": po_lines,
        "erp_available": erp_ok,
        "erp_doc_no": inv.erp_doc_no,
        "last_error": inv.last_error,
        "duplicate": dup,
        "task": task_view(task, now, user),
        "pdf": pdf_link(inv.pdf_sha256),
        "page_count": inv.page_count,
        "events": [
            {
                "id": e.event_id,
                "action": e.action,
                "from": e.from_state,
                "to": e.to_state,
                "actor": e.actor,
                "actor_name": user_name(e.actor) if e.actor_type == "user" else "System",
                "role": e.role,
                "at": e.at.isoformat(),
                "payload": e.payload_json,
            }
            for e in events
        ],
    }
