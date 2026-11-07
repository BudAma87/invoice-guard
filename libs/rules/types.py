"""Inputs and outputs of the rule engine. Plain frozen dataclasses: no ORM, no I/O.

All money is integer minor units (ISO 4217 exponent). Quantities are Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class Severity(StrEnum):
    BLOCK = "BLOCK"  # cannot be posted, even by a reviewer
    FAIL = "FAIL"  # a person decides; may override with a reason code
    WARN = "WARN"  # shown, does not route on its own


class Stage(StrEnum):
    SHAPE = "shape"
    IDENTITY = "identity"
    ARITHMETIC = "arithmetic"
    REFERENCE = "reference"


class Group(StrEnum):
    """UI card the rule belongs to; also the reviewer's fix order."""

    IDENTITY = "identity"
    DATE = "date"
    TOTAL = "total"
    LINES = "lines"


@dataclass(frozen=True)
class RuleResult:
    code: str
    status: Status
    severity: Severity
    stage: Stage
    group: Group
    message: str
    expected: Any = None
    actual: Any = None
    fields: tuple[str, ...] = ()
    line: int | None = None
    needs: str | None = None  # blocking dependency when SKIPPED

    @property
    def key(self) -> str:
        """Unique within one evaluation: line rules repeat per line."""
        return f"{self.code}:{self.line}" if self.line is not None else self.code

    @property
    def is_blocker(self) -> bool:
        return self.status == Status.FAIL and self.severity == Severity.BLOCK

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "code": self.code,
            "status": self.status.value,
            "severity": self.severity.value,
            "stage": self.stage.value,
            "group": self.group.value,
            "message": self.message,
            "expected": _jsonable(self.expected),
            "actual": _jsonable(self.actual),
            "fields": list(self.fields),
            "line": self.line,
            "needs": self.needs,
        }


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


# --------------------------------------------------------------------------- invoice


@dataclass(frozen=True)
class TaxLine:
    rate: Decimal  # 0.18 for 18 %
    base_minor: int
    amount_minor: int


@dataclass(frozen=True)
class Line:
    line_no: int
    description: str
    qty: Decimal | None
    unit_price_minor: int | None
    amount_minor: int | None
    item_code: str | None = None
    uom: str | None = None
    po_line_id: str | None = None  # set when a reviewer picks the PO line


@dataclass(frozen=True)
class Invoice:
    invoice_id: str
    entity_id: str
    supplier_name: str | None
    supplier_tax_id: str | None
    invoice_no: str | None  # normalised (see text.normalise_invoice_no)
    invoice_date: date | None
    currency: str | None
    po_number: str | None
    total_minor: int | None
    subtotal_minor: int | None = None  # only when printed
    tax_minor: int = 0
    charges_minor: int = 0
    discount_minor: int = 0
    lines: tuple[Line, ...] = ()
    tax_lines: tuple[TaxLine, ...] = ()
    date_ambiguous: bool = False  # dd/mm vs mm/dd unresolved by supplier locale
    confidence: dict[str, float] = field(default_factory=dict)


# --------------------------------------------------------------------------- context


@dataclass(frozen=True)
class Vendor:
    supplier_id: str
    name: str
    tax_id: str | None
    active: bool = True
    blocked: bool = False
    allowed_currencies: tuple[str, ...] = ()
    allowed_tax_rates: tuple[Decimal, ...] = ()


@dataclass(frozen=True)
class POLine:
    po_line_id: str
    line_no: int
    item_code: str
    description: str
    price_minor: int
    open_qty: Decimal
    uom: str = "EA"


@dataclass(frozen=True)
class PurchaseOrder:
    po_number: str
    supplier_id: str
    entity_id: str
    currency: str
    approved_on: date
    remaining_minor: int
    status: str = "OPEN"
    lines: tuple[POLine, ...] = ()


@dataclass(frozen=True)
class Item:
    code: str
    active: bool = True
    uom: str = "EA"
    conversions: dict[str, Decimal] = field(default_factory=dict)  # uom -> factor to base uom


@dataclass(frozen=True)
class PeriodStatus:
    period: str  # "2026-10"
    open: bool
    first_open: str | None = None


@dataclass(frozen=True)
class DupCandidate:
    invoice_id: str
    supplier_id: str
    invoice_no: str
    invoice_date: date
    total_minor: int
    currency: str
    state: str


@dataclass(frozen=True)
class Context:
    """Reference data gathered by the enrich step (or by the API for /validate)."""

    today: date  # in the entity's time zone
    vendor_matches: tuple[Vendor, ...] = ()
    po: PurchaseOrder | None = None
    items: dict[str, Item] = field(default_factory=dict)
    period: PeriodStatus | None = None
    exact_duplicates: tuple[DupCandidate, ...] = ()  # same business key, posted or in flight
    fuzzy_candidates: tuple[DupCandidate, ...] = ()  # same supplier, last N days
    erp_available: bool = True

    @property
    def vendor(self) -> Vendor | None:
        return self.vendor_matches[0] if len(self.vendor_matches) == 1 else None
