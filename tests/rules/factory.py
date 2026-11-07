"""Builders for a clean, auto-passing invoice + context. Tests mutate with dataclasses.replace."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from libs.rules import (
    Context,
    Invoice,
    Item,
    Line,
    PeriodStatus,
    POLine,
    PurchaseOrder,
    Vendor,
)

TODAY = date(2026, 11, 5)


def conf(n_lines: int, value: float = 0.97) -> dict[str, float]:
    c = {k: value for k in ("supplier", "invoice_no", "invoice_date", "currency", "total")}
    for i in range(1, n_lines + 1):
        for f in ("qty", "unit_price", "amount"):
            c[f"line:{i}:{f}"] = value
    return c


def invoice(**kw) -> Invoice:
    lines = (
        Line(1, "Steel bolt M8 x 40", Decimal(10), 50_00, 500_00, "A-100", "EA"),
        Line(2, "Hex nut M8", Decimal(5), 140_00, 700_00, "B-220", "EA"),
    )
    base = dict(
        invoice_id="inv-1",
        entity_id="E01",
        supplier_name="Supplier A",
        supplier_tax_id="TX-1",
        invoice_no="INV10234",
        invoice_date=date(2026, 10, 3),
        currency="LKR",
        po_number="PO-55821",
        total_minor=1416_00,
        subtotal_minor=1200_00,
        tax_minor=216_00,
        lines=lines,
        confidence=conf(len(lines)),
    )
    base.update(kw)
    return Invoice(**base)


def context(**kw) -> Context:
    po = PurchaseOrder(
        po_number="PO-55821",
        supplier_id="S-A",
        entity_id="E01",
        currency="LKR",
        approved_on=date(2026, 9, 2),
        remaining_minor=5000_00,
        lines=(
            POLine("pl-1", 1, "A-100", "Steel bolt M8x40", 50_00, Decimal(10)),
            POLine("pl-2", 2, "B-220", "Hex nut M8", 140_00, Decimal(5)),
            POLine("pl-3", 3, "C-330", "Flat washer M8", 10_00, Decimal(100)),
        ),
    )
    base = dict(
        today=TODAY,
        vendor_matches=(
            Vendor(
                "S-A",
                "Supplier A",
                "TX-1",
                allowed_currencies=("LKR",),
                allowed_tax_rates=(Decimal("0.18"),),
            ),
        ),
        po=po,
        items={
            "A-100": Item("A-100", uom="EA", conversions={"BOX": Decimal(10)}),
            "B-220": Item("B-220"),
            "C-330": Item("C-330"),
        },
        period=PeriodStatus("2026-10", True),
    )
    base.update(kw)
    return Context(**base)
