"""Review API: queue, workspace, validate, decision, checker, ops (TSD §10 "Key API endpoints")."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from libs.config import settings
from libs.core import review as rv
from libs.core.convert import BadCorrection
from libs.core.entities import entity as entity_of
from libs.core.reconcile import run_reconciliation
from libs.core.runtime import erp_client, message_ledger
from libs.core.service import open_task
from libs.core.store import ObjectStore, verify
from libs.db import models as m
from libs.db.models import Base, utcnow
from libs.db.session import engine
from libs.rules.config import ReasonCode
from services.api import ops
from services.api.auth import DEV_USERS, client_ip, current_user
from services.api.deps import db, scoped_invoice
from services.api.views import invoice_detail, queue_row, task_view


def _create_schema() -> None:  # dev convenience; prod runs Alembic
    Base.metadata.create_all(engine)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    globals()["_create_schema"]()
    yield


app = FastAPI(title="invoice-guard review API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag"],
)


@app.exception_handler(rv.ReviewError)
async def _review_error(_: Request, e: rv.ReviewError) -> JSONResponse:
    return JSONResponse({"detail": str(e), **e.extra}, status_code=e.status)


@app.exception_handler(StaleDataError)
async def _stale(_: Request, e: StaleDataError) -> JSONResponse:
    return JSONResponse({"detail": "Changed by someone else — reload"}, status_code=409)


@app.exception_handler(BadCorrection)
async def _bad(_: Request, e: BadCorrection) -> JSONResponse:
    return JSONResponse({"detail": str(e)}, status_code=422)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ------------------------------------------------------------------ identity


@app.get("/me")
def me(user: rv.User = Depends(current_user)) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "roles": sorted(user.roles),
        "entities": sorted(user.entities),
    }


@app.get("/users")
def users() -> list[dict]:
    """Dev only: lets the UI switch identity to demo maker-checker."""
    if not settings.dev_auth:
        raise HTTPException(404)
    return [
        {"id": u.id, "name": u.name, "roles": sorted(u.roles), "entities": sorted(u.entities)}
        for u in DEV_USERS.values()
    ]


@app.get("/reason-codes")
def reason_codes() -> list[str]:
    return [c.value for c in ReasonCode]


# ------------------------------------------------------------------ queue

AMOUNT_BANDS = {
    "lt_250k": (0, 250_000_00),
    "250k_1m": (250_000_00, 1_000_000_00),
    "gte_1m": (1_000_000_00, 10**18),
}


@app.get("/queue")
def queue(
    kind: Literal["review", "check"] = "review",
    entity: str | None = None,
    reason: str | None = None,
    sla: str | None = None,
    amount: str | None = None,
    supplier: str | None = None,
    session: Session = Depends(db),
    user: rv.User = Depends(current_user),
) -> dict:
    now = utcnow()
    kinds = [m.TaskKind.CHECK] if kind == "check" else [m.TaskKind.REVIEW, m.TaskKind.MANUAL_ENTRY]
    ents = [entity] if entity and entity in user.entities else sorted(user.entities)
    rows = session.execute(
        select(m.ReviewTask, m.Invoice)
        .join(m.Invoice, m.Invoice.invoice_id == m.ReviewTask.invoice_id)
        .where(
            m.ReviewTask.status == "OPEN",
            m.ReviewTask.kind.in_(kinds),
            m.ReviewTask.entity_id.in_(ents),
        )
    ).all()
    out = [queue_row(t, inv, now, user) for t, inv in rows]
    facets = {
        "reasons": sorted(
            {r.split(":")[0] if r.startswith("LOW_CONF") else r for x in out for r in x["reasons"]}
        ),
        "suppliers": sorted({x["supplier_name"] for x in out if x["supplier_name"]}),
    }
    if reason:
        out = [
            x for x in out if any(r == reason or r.startswith(reason + ":") for r in x["reasons"])
        ]
    if sla:
        out = [x for x in out if x["sla_state"] == sla]
    if amount in AMOUNT_BANDS:
        lo, hi = AMOUNT_BANDS[amount]
        out = [x for x in out if lo <= (x["amount_lkr_minor"] or 0) < hi]
    if supplier:
        out = [x for x in out if supplier.lower() in (x["supplier_name"] or "").lower()]
    out.sort(key=lambda x: (x["sla_state"] != "breached", -x["priority"]))
    next_risk = min((x["sla_due_at"] for x in out if x["sla_state"] == "on_track"), default=None)
    return {"items": out, "total": len(out), "facets": facets, "next_sla_risk_at": next_risk}


class NextIn(BaseModel):
    kind: Literal["review", "check"] = "review"


@app.post("/queue/next")
def queue_next(
    body: NextIn, session: Session = Depends(db), user: rv.User = Depends(current_user)
) -> dict:
    t = rv.next_task(session, user, body.kind, utcnow())
    session.commit()
    return {"invoice_id": t.invoice_id if t else None}


# ------------------------------------------------------------------ invoice


@app.get("/invoices/{invoice_id}")
async def get_invoice(
    invoice_id: str,
    response: Response,
    session: Session = Depends(db),
    user: rv.User = Depends(current_user),
) -> dict:
    inv = scoped_invoice(session, invoice_id, user)
    response.headers["ETag"] = str(inv.version)
    return await invoice_detail(session, erp_client(), inv, user, utcnow())


@app.post("/invoices/{invoice_id}/claim")
def claim(
    invoice_id: str, session: Session = Depends(db), user: rv.User = Depends(current_user)
) -> dict:
    inv = scoped_invoice(session, invoice_id, user)
    t = open_task(session, inv.invoice_id)
    if t is None:
        raise rv.Conflict("No open task")
    rv.claim(session, t, user, utcnow())
    session.commit()
    return task_view(t, utcnow(), user) or {}


@app.post("/invoices/{invoice_id}/release")
def release(
    invoice_id: str, session: Session = Depends(db), user: rv.User = Depends(current_user)
) -> dict:
    inv = scoped_invoice(session, invoice_id, user)
    t = open_task(session, inv.invoice_id)
    if t:
        rv.release(session, t, user, utcnow())
        session.commit()
    return {"ok": True}


@app.get("/invoices/{invoice_id}/pdf")
def pdf(
    invoice_id: str, session: Session = Depends(db), user: rv.User = Depends(current_user)
) -> dict:
    from services.api.views import pdf_link

    inv = scoped_invoice(session, invoice_id, user)
    link = pdf_link(inv.pdf_sha256)
    if link is None:
        raise rv.NotFound("No PDF")
    return link


@app.get("/files/{sha}.pdf")
def file(sha: str, exp: int = Query(...), sig: str = Query(...)) -> Response:
    if not verify(sha, exp, sig):
        raise HTTPException(403, "Link expired")
    store = ObjectStore()
    if not store.exists(sha):
        raise HTTPException(404)
    return Response(
        store.get(sha),
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Security-Policy": "default-src 'none'; script-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


class ValidateIn(BaseModel):
    corrections: dict[str, Any] | None = None
    overrides: dict[str, dict[str, str]] | None = None


@app.post("/invoices/{invoice_id}/validate")
async def validate(
    invoice_id: str,
    body: ValidateIn,
    session: Session = Depends(db),
    user: rv.User = Depends(current_user),
) -> dict:
    inv = scoped_invoice(session, invoice_id, user)
    out = await rv.validate_draft(
        session, erp_client(), inv, body.corrections, body.overrides, utcnow()
    )
    session.rollback()  # never saved
    return {
        "results": out.results,
        "diff": out.diff,
        "approve_allowed": out.allowed,
        "problems": out.problems,
        "needs_checker": out.needs_checker,
        "amount_lkr_minor": out.amount_lkr_minor,
        "erp_available": out.erp_available,
        "results_digest": rv.results_digest(out.results),
    }


class DecisionIn(BaseModel):
    action: Literal[
        "approve", "approve_with_override", "reject", "duplicate", "not_duplicate", "hold"
    ]
    corrections: dict[str, Any] | None = None
    overrides: dict[str, dict[str, str]] | None = None
    reason_code: str | None = None
    comment: str | None = Field(default=None, max_length=2000)
    results_digest: str | None = None


def _version(if_match: str | None) -> int:
    if not if_match:
        raise HTTPException(428, "If-Match: <version> header required")
    try:
        return int(if_match.strip('"W/'))
    except ValueError as e:
        raise HTTPException(400, "Bad If-Match") from e


@app.post("/invoices/{invoice_id}/decision")
async def decision(
    invoice_id: str,
    body: DecisionIn,
    request: Request,
    if_match: str | None = Header(default=None),
    session: Session = Depends(db),
    user: rv.User = Depends(current_user),
) -> dict:
    inv = scoped_invoice(session, invoice_id, user)
    out = await rv.decide(
        session,
        erp_client(),
        inv,
        user,
        action=body.action,
        expected_version=_version(if_match),
        corrections=body.corrections,
        overrides=body.overrides,
        reason_code=body.reason_code,
        comment=body.comment,
        results_digest_seen=body.results_digest,
        now=utcnow(),
        ip=client_ip(request),
    )
    session.commit()
    return out


class CheckIn(BaseModel):
    action: Literal["approve", "send_back"]
    comment: str | None = None


@app.post("/invoices/{invoice_id}/check")
async def check(
    invoice_id: str,
    body: CheckIn,
    request: Request,
    if_match: str | None = Header(default=None),
    session: Session = Depends(db),
    user: rv.User = Depends(current_user),
) -> dict:
    inv = scoped_invoice(session, invoice_id, user)
    out = await rv.check(
        session,
        erp_client(),
        inv,
        user,
        action=body.action,
        expected_version=_version(if_match),
        comment=body.comment,
        now=utcnow(),
        ip=client_ip(request),
    )
    session.commit()
    return out


# ------------------------------------------------------------------ ops


@app.get("/ops/metrics")
def ops_metrics(
    entity: str | None = None, session: Session = Depends(db), user: rv.User = Depends(current_user)
) -> dict:
    if not (user.can("supervisor") or user.can("auditor")):
        raise HTTPException(403, "Supervisor role required")
    ents = [entity] if entity and entity in user.entities else sorted(user.entities)
    return ops.metrics(session, ents, utcnow())


def _ops_user(user: rv.User) -> None:
    if not (user.can("supervisor") or user.can("auditor")):
        raise HTTPException(403, "Supervisor role required")


@app.get("/ops/reconciliation")
def reconciliation_runs(
    entity: str | None = None,
    limit: int = Query(14, ge=1, le=100),
    session: Session = Depends(db),
    user: rv.User = Depends(current_user),
) -> dict:
    """Latest runs, newest first, with their differences."""
    _ops_user(user)
    ents = [entity] if entity and entity in user.entities else sorted(user.entities)
    runs = session.scalars(
        select(m.ReconciliationRun)
        .where(m.ReconciliationRun.entity_id.in_(ents))
        .order_by(m.ReconciliationRun.business_date.desc(), m.ReconciliationRun.started_at.desc())
        .limit(limit)
    ).all()
    return {"runs": [ops.run_view(r) for r in runs]}


class ReconcileIn(BaseModel):
    entity: str
    date: str | None = None  # YYYY-MM-DD, default today in the entity's time zone


@app.post("/ops/reconciliation/run")
async def reconciliation_run_now(
    body: ReconcileIn, session: Session = Depends(db), user: rv.User = Depends(current_user)
) -> dict:
    """Supervisor-triggered run (e.g. after fixing a difference). Auditors can only read."""
    if not user.can("supervisor"):
        raise HTTPException(403, "Supervisor role required")
    if body.entity not in user.entities:
        raise rv.NotFound("Entity not found")
    now = utcnow()
    try:
        day = date.fromisoformat(body.date) if body.date else entity_of(body.entity).today(now)
    except ValueError as e:
        raise HTTPException(422, "date must be YYYY-MM-DD") from e
    run = await run_reconciliation(
        session, erp_client(), message_ledger(), body.entity, day, now, trigger="manual"
    )
    session.commit()
    record = ops.run_view(run)
    return record
