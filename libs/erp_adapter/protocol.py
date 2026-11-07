"""ERP adapter interface. Every vendor implementation must support lookup by external
reference — that lookup is what makes posting exactly-once (TSD §9)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from libs.rules.types import Item, PeriodStatus, PurchaseOrder, Vendor


@dataclass(frozen=True)
class ERPDoc:
    doc_no: str
    ext_ref: str
    entity_id: str
    total_minor: int
    currency: str
    posted_at: str | None = None  # ISO-8601 UTC, when the ERP accepted it


@dataclass(frozen=True)
class PostableLine:
    po_line_id: str | None
    item_code: str | None
    qty: Decimal
    unit_price_minor: int
    amount_minor: int


@dataclass(frozen=True)
class PostableInvoice:
    entity_id: str
    supplier_id: str
    invoice_no: str
    invoice_date: date
    posting_period: str
    currency: str
    po_number: str | None
    total_minor: int
    tax_minor: int
    lines: tuple[PostableLine, ...]


class ERPBusinessError(Exception):
    """ERP refused the document (vendor blocked, period closed...). Goes to review."""


class ERPUnavailable(Exception):
    """Transport failure. If raised after the request was sent, the outcome is unknown."""

    def __init__(self, msg: str, *, ambiguous: bool = False):
        super().__init__(msg)
        self.ambiguous = ambiguous


class ERPAdapter(Protocol):
    async def find_by_external_ref(self, entity_id: str, ext_ref: UUID) -> ERPDoc | None: ...
    async def post_invoice(self, inv: PostableInvoice, ext_ref: UUID) -> ERPDoc: ...
    async def list_documents(
        self, entity_id: str, since: datetime, until: datetime
    ) -> list[ERPDoc]:
        """AP documents the pipeline created (external ref set), posted in [since, until)."""
        ...

    async def find_vendors(
        self, entity_id: str, name: str | None, tax_id: str | None
    ) -> list[Vendor]: ...
    async def get_vendor(self, entity_id: str, supplier_key: str) -> Vendor | None: ...
    async def get_po(self, entity_id: str, po_number: str) -> PurchaseOrder | None: ...
    async def get_items(self, entity_id: str, codes: list[str]) -> dict[str, Item]: ...
    async def is_period_open(self, entity_id: str, on: date) -> PeriodStatus: ...
    async def fx_to_lkr(self, currency: str, on: date) -> Decimal: ...
