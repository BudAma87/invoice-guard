"""State DB — the source of truth for every invoice (TSD §4).

Same models on SQLite (dev/pilot, single writer) and Postgres 16 (production).
Uniqueness constraints are the duplicate guard: message, file, business document,
one open task per invoice, one outbox row per idempotency key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class UTCDateTime(TypeDecorator[datetime]):
    """Always aware UTC in Python; SQLite stores naive UTC, Postgres timestamptz."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class State(StrEnum):
    RECEIVED = "RECEIVED"
    EXTRACTING = "EXTRACTING"
    EXTRACTED = "EXTRACTED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    VALIDATED = "VALIDATED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    DUPLICATE_SUSPECTED = "DUPLICATE_SUSPECTED"
    PENDING_CHECKER = "PENDING_CHECKER"
    APPROVED_AUTO = "APPROVED_AUTO"
    APPROVED_MANUAL = "APPROVED_MANUAL"
    POSTING = "POSTING"
    POST_FAILED = "POST_FAILED"
    POSTED = "POSTED"
    REJECTED = "REJECTED"
    DUPLICATE = "DUPLICATE"


TERMINAL = frozenset({State.POSTED, State.REJECTED, State.DUPLICATE})
IN_REVIEW = frozenset(
    {State.NEEDS_REVIEW, State.DUPLICATE_SUSPECTED, State.PENDING_CHECKER, State.EXTRACTION_FAILED}
)


class Inbox(Base):
    __tablename__ = "inbox"

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    pdf_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(String(32), default="kafka")
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoice.invoice_id"))


class Invoice(Base):
    __tablename__ = "invoice"

    invoice_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_id: Mapped[str] = mapped_column(String(16), index=True)
    state: Mapped[str] = mapped_column(String(24), default=State.RECEIVED, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # extracted + normalised header
    supplier_id: Mapped[str | None] = mapped_column(String(32))
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    supplier_tax_id: Mapped[str | None] = mapped_column(String(64))
    invoice_no_raw: Mapped[str | None] = mapped_column(String(64))
    invoice_no_norm: Mapped[str | None] = mapped_column(String(64))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    date_ambiguous: Mapped[bool] = mapped_column(default=False)
    currency: Mapped[str | None] = mapped_column(String(3))
    po_number: Mapped[str | None] = mapped_column(String(32))
    subtotal_minor: Mapped[int | None] = mapped_column(Integer)
    tax_minor: Mapped[int] = mapped_column(Integer, default=0)
    charges_minor: Mapped[int] = mapped_column(Integer, default=0)
    discount_minor: Mapped[int] = mapped_column(Integer, default=0)
    total_minor: Mapped[int | None] = mapped_column(Integer)
    tax_lines: Mapped[list[Any]] = mapped_column(default=list)
    confidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)  # field -> {page,bbox,text}

    # Unique (partial) — see __table_args__. NULL when it would collide.
    business_key: Mapped[str | None] = mapped_column(String(200))
    duplicate_of: Mapped[str | None] = mapped_column(String(36))

    amount_lkr_minor: Mapped[int | None] = mapped_column(Integer)
    rule_set_version: Mapped[str | None] = mapped_column(String(32))
    current_evaluation_id: Mapped[str | None] = mapped_column(String(36))
    posting_period: Mapped[str | None] = mapped_column(String(7))  # PERIOD_CLOSED override

    pdf_sha256: Mapped[str | None] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    erp_doc_no: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    state_since: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice", order_by="InvoiceLine.line_no", cascade="all, delete-orphan"
    )

    __mapper_args__ = {"version_id_col": version}
    __table_args__ = (
        Index(
            "uq_invoice_business_key",
            "business_key",
            unique=True,
            sqlite_where=text("state != 'REJECTED' AND business_key IS NOT NULL"),
            postgresql_where=text("state != 'REJECTED' AND business_key IS NOT NULL"),
        ),
        Index("ix_invoice_supplier_total", "supplier_id", "total_minor"),
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_line"

    line_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoice.invoice_id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(500), default="")
    item_code: Mapped[str | None] = mapped_column(String(64))
    uom: Mapped[str | None] = mapped_column(String(16))
    po_line_id: Mapped[str | None] = mapped_column(String(64))
    qty: Mapped[str | None] = mapped_column(String(32))  # Decimal as text: exact on both DBs
    unit_price_minor: Mapped[int | None] = mapped_column(Integer)
    amount_minor: Mapped[int | None] = mapped_column(Integer)
    confidence_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(default=dict)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")
    __table_args__ = (UniqueConstraint("invoice_id", "line_no"),)


class Extraction(Base):
    __tablename__ = "extraction"

    extraction_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoice.invoice_id"))
    attempt: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    raw_uri: Mapped[str | None] = mapped_column(String(300))
    parsed_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    field_confidence_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    __table_args__ = (UniqueConstraint("invoice_id", "attempt"),)


class RuleResultRow(Base):
    __tablename__ = "rule_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoice.invoice_id"), index=True)
    evaluation_id: Mapped[str] = mapped_column(String(36))
    rule_set_version: Mapped[str] = mapped_column(String(32))
    rule_key: Mapped[str] = mapped_column(String(64))
    rule_code: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(8))
    stage: Mapped[str] = mapped_column(String(16))
    group: Mapped[str] = mapped_column(String(16))
    line: Mapped[int | None] = mapped_column(Integer)
    expected: Mapped[Any] = mapped_column(JSON, nullable=True)
    actual: Mapped[Any] = mapped_column(JSON, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    fields: Mapped[list[Any]] = mapped_column(default=list)
    needs: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    __table_args__ = (UniqueConstraint("invoice_id", "rule_key", "evaluation_id"),)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.rule_key,
            "code": self.rule_code,
            "status": self.status,
            "severity": self.severity,
            "stage": self.stage,
            "group": self.group,
            "line": self.line,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
            "fields": self.fields,
            "needs": self.needs,
        }


class TaskKind(StrEnum):
    REVIEW = "REVIEW"
    CHECK = "CHECK"
    MANUAL_ENTRY = "MANUAL_ENTRY"


class ReviewTask(Base):
    __tablename__ = "review_task"

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoice.invoice_id"), index=True)
    entity_id: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(16), default=TaskKind.REVIEW)
    status: Mapped[str] = mapped_column(String(8), default="OPEN")  # OPEN | DONE
    reasons: Mapped[list[Any]] = mapped_column(default=list)
    priority: Mapped[float] = mapped_column(default=0.0)
    assignee: Mapped[str | None] = mapped_column(String(64))
    claimed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    sla_due_at: Mapped[datetime] = mapped_column(UTCDateTime())
    sla_seconds: Mapped[int] = mapped_column(Integer)
    hold_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    hold_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    hold_note: Mapped[str | None] = mapped_column(Text)
    # Business seconds spent on hold (SLA clock paused); credited when a hold ends.
    sla_paused_seconds: Mapped[int] = mapped_column(Integer, default=0)
    # 0 none, 1 warned, 2 breached (moved to supervisor pool), 3 finance manager
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    pool: Mapped[str | None] = mapped_column(String(16))  # "supervisor" after a breach

    maker_id: Mapped[str | None] = mapped_column(String(64))
    decision: Mapped[str | None] = mapped_column(String(32))
    reason_code: Mapped[str | None] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)
    corrections: Mapped[dict[str, Any]] = mapped_column(default=dict)
    overrides: Mapped[dict[str, Any]] = mapped_column(default=dict)
    results_before: Mapped[list[Any]] = mapped_column(default=list)
    results_after: Mapped[list[Any]] = mapped_column(default=list)
    checker_id: Mapped[str | None] = mapped_column(String(64))
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        Index(
            "uq_review_task_one_open",
            "invoice_id",
            unique=True,
            sqlite_where=text("status = 'OPEN'"),
            postgresql_where=text("status = 'OPEN'"),
        ),
    )


class InvoiceEvent(Base):
    """Append-only audit log. Never updated or deleted; hash-chained per invoice."""

    __tablename__ = "invoice_event"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(40))
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str | None] = mapped_column(String(24))
    actor: Mapped[str] = mapped_column(String(64), default="system")
    actor_type: Mapped[str] = mapped_column(String(8), default="system")  # system | user
    role: Mapped[str | None] = mapped_column(String(16))
    ip: Mapped[str | None] = mapped_column(String(45))
    payload_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class Notification(Base):
    """Durable, idempotent notification log. One row per (task, kind): an escalation is
    raised once even if the scheduler runs twice. A sender delivers PENDING rows."""

    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("review_task.task_id"), index=True)
    invoice_id: Mapped[str] = mapped_column(String(36))
    entity_id: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(24))  # SLA_WARNING | SLA_BREACH | SLA_ESCALATION
    channel: Mapped[str] = mapped_column(String(16), default="email")
    recipients: Mapped[list[Any]] = mapped_column(
        default=list
    )  # "user:<id>" | "role:<role>@<entity>"
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(8), default="PENDING")  # PENDING | SENT | FAILED
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (UniqueConstraint("task_id", "kind"),)


class ReconStatus(StrEnum):
    RUNNING = "RUNNING"
    OK = "OK"  # every check ran, no differences
    DIFFERENCES = "DIFFERENCES"  # every check ran, at least one difference
    PARTIAL = "PARTIAL"  # a source was unavailable; the checks that ran are recorded
    ERROR = "ERROR"  # could not complete (e.g. ERP down); retried on the next tick


class ReconciliationRun(Base):
    """Daily lost-invoice net (TSD §9), one row per entity per business day per attempt."""

    __tablename__ = "reconciliation_run"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entity_id: Mapped[str] = mapped_column(String(16), index=True)
    business_date: Mapped[date] = mapped_column(Date, index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="schedule")  # schedule | manual
    status: Mapped[str] = mapped_column(String(12), default=ReconStatus.RUNNING)
    counts: Mapped[dict[str, Any]] = mapped_column(default=dict)
    skipped: Mapped[list[Any]] = mapped_column(default=list)  # checks not run, and why
    differences: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    items: Mapped[list[ReconciliationItem]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="ReconciliationItem.id"
    )


class ReconciliationItem(Base):
    __tablename__ = "reconciliation_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_run.run_id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))  # critical | high
    invoice_id: Mapped[str | None] = mapped_column(String(36))
    message_id: Mapped[str | None] = mapped_column(String(128))
    erp_doc_no: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str] = mapped_column(Text)

    run: Mapped[ReconciliationRun] = relationship(back_populates="items")


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"  # ambiguous ERP timeout: look up before any retry
    DONE = "DONE"
    FAILED = "FAILED"  # ERP business error


class Outbox(Base):
    __tablename__ = "outbox"

    outbox_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoice.invoice_id"))
    entity_id: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(16), default="POST")
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(8), default=OutboxStatus.PENDING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    erp_doc_no: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
