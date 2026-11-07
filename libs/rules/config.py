"""Versioned rule-set thresholds. Each invoice records the version it was checked against.

Changing a value means publishing a new version (config-admin + second approver, TSD §8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class ReasonCode(StrEnum):
    PERIOD_CLOSED = "PERIOD_CLOSED"
    PRICE_AGREED = "PRICE_AGREED"
    QTY_PARTIAL = "QTY_PARTIAL"
    SUPPLIER_CONFIRMED = "SUPPLIER_CONFIRMED"
    OCR_CORRECTION = "OCR_CORRECTION"
    DUPLICATE_CONFIRMED = "DUPLICATE_CONFIRMED"
    NOT_OUR_INVOICE = "NOT_OUR_INVOICE"
    OTHER = "OTHER"


OTHER_MIN_COMMENT = 20


def reason_valid(code: str | None, comment: str | None) -> bool:
    if code is None or code not in ReasonCode.__members__:
        return False
    return code != ReasonCode.OTHER or len((comment or "").strip()) >= OTHER_MIN_COMMENT


@dataclass(frozen=True)
class RuleSetConfig:
    version: str = "2026.11.1"
    min_confidence: float = 0.85
    date_max_age_days: int = 90
    date_future_grace_days: int = 1
    price_tolerance: Decimal = Decimal("0.02")
    total_vs_po_tolerance: Decimal = Decimal("0.02")
    line_similarity: float = 0.90
    default_total_tolerance_minor: int = 1
    total_tolerance_minor_by_ccy: dict[str, int] = field(default_factory=dict)
    dup_fuzzy_days: int = 30
    dup_fuzzy_max_edit: int = 2
    maker_checker_lkr_minor: int = 1_000_000_00
    # Pilot guard (TSD §10 phase 3): auto-post only below this LKR amount. None = no ceiling.
    auto_approve_ceiling_lkr_minor: int | None = None

    def total_tolerance_minor(self, currency: str | None) -> int:
        return self.total_tolerance_minor_by_ccy.get(
            (currency or "").upper(), self.default_total_tolerance_minor
        )


DEFAULT_RULE_SET = RuleSetConfig()

REQUIRED_HEADER_FIELDS = ("supplier", "invoice_no", "invoice_date", "currency", "total")
REQUIRED_LINE_FIELDS = ("qty", "unit_price", "amount")
