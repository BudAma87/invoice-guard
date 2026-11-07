"""Demo invoice scenarios -> (PDF bytes, extraction sidecar). Exercises every review path."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scripts.demo.pdfgen import Page, build


@dataclass
class Spec:
    key: str
    entity: str
    supplier: str
    tax_id: str
    invoice_no: str
    date: str
    currency: str
    po: str | None
    lines: list[
        tuple[str, str | None, str, str, str, str | None]
    ]  # desc, code, qty, price, amt, uom
    subtotal: str | None
    tax_rate: str | None
    tax: str | None
    total: str
    charges: str | None = None
    conf: dict[str, float] = field(default_factory=dict)
    salt: str = ""
    no_sidecar: bool = False
    note: str = ""


def _f(value: str | None, bbox: tuple | None, c: float, page: int = 1) -> dict[str, Any]:
    out: dict[str, Any] = {"value": value, "confidence": c}
    if value is not None and bbox is not None:
        out["evidence"] = {"page": page, "bbox": list(bbox), "text": value}
    return out


def render(s: Spec) -> tuple[bytes, dict[str, Any] | None]:
    p = Page()
    c = lambda k, d=0.97: s.conf.get(k, d)  # noqa: E731
    p.rect(0, 0, 595, 92, 0.96)
    b_sup = p.text(48, 52, s.supplier, 18, bold=True)
    p.text(48, 72, f"Tax ID {s.tax_id}  |  Colombo, Sri Lanka", 9, gray=0.35)
    p.text(547, 52, "TAX INVOICE", 14, bold=True, right=True, gray=0.25)
    p.text(48, 128, "Invoice no.", 8, gray=0.45)
    b_no = p.text(48, 142, s.invoice_no, 11, bold=True)
    p.text(200, 128, "Invoice date", 8, gray=0.45)
    b_date = p.text(200, 142, s.date, 11, bold=True)
    p.text(330, 128, "PO number", 8, gray=0.45)
    b_po = p.text(330, 142, s.po, 11, bold=True) if s.po else None
    p.text(460, 128, "Currency", 8, gray=0.45)
    b_ccy = p.text(460, 142, s.currency, 11, bold=True)
    b_tax_id = (0.0, 0.0, 0.0, 0.0)
    p.text(48, 178, "Bill to: Lanka Holdings PLC, 25 Galle Road, Colombo 03", 9, gray=0.35)

    y = 214
    p.rect(40, y - 14, 515, 20, 0.93)
    for x, t, r in (
        (48, "Description", False),
        (250, "Item", False),
        (360, "Qty", True),
        (450, "Unit price", True),
        (547, "Amount", True),
    ):
        p.text(x, y, t, 8, bold=True, right=r, gray=0.3)
    lines_out = []
    for desc, code, qty, price, amt, uom in s.lines:
        y += 24
        bd = p.text(48, y, desc, 9.5)
        bc = p.text(250, y, code, 9.5) if code else None
        bq = p.text(360, y, qty + (f" {uom}" if uom else ""), 9.5, right=True)
        bp = p.text(450, y, price, 9.5, right=True)
        ba = p.text(547, y, amt, 9.5, right=True)
        p.line(40, y + 8, 555)
        n = len(lines_out) + 1
        lines_out.append(
            {
                "description": _f(desc, bd, c(f"line:{n}:description")),
                "item_code": _f(code, bc, c(f"line:{n}:item_code")) if code else None,
                "uom": _f(uom, bq, 0.95) if uom else None,
                "qty": _f(qty, bq, c(f"line:{n}:qty")),
                "unit_price": _f(price, bp, c(f"line:{n}:unit_price")),
                "amount": _f(amt, ba, c(f"line:{n}:amount")),
            }
        )
    y += 36
    tot: dict[str, Any] = {}
    for label, val, key in (
        ("Subtotal", s.subtotal, "subtotal"),
        (f"VAT {s.tax_rate}%" if s.tax_rate else "Tax", s.tax, "tax"),
        ("Charges", s.charges, "charges"),
    ):
        if val is None:
            continue
        p.text(450, y, label, 9.5, right=True, gray=0.35)
        tot[key] = p.text(547, y, val, 9.5, right=True)
        y += 18
    p.line(330, y - 6, 555, 0.3, 1)
    y += 10
    p.text(450, y, f"TOTAL {s.currency}", 11, bold=True, right=True)
    b_total = p.text(547, y, s.total, 12, bold=True, right=True)
    p.text(48, 800, "Payment terms: 30 days. Bank: Commercial Bank, A/C ****4417.", 8, gray=0.5)
    pdf = build([p], salt=s.salt or s.key)

    if s.no_sidecar:
        return pdf, None
    ext: dict[str, Any] = {
        "supplier_name": _f(s.supplier, b_sup, c("supplier")),
        "supplier_tax_id": _f(s.tax_id, b_tax_id, c("supplier_tax_id")),
        "invoice_no": _f(s.invoice_no, b_no, c("invoice_no")),
        "invoice_date": _f(s.date, b_date, c("invoice_date")),
        "currency": _f(s.currency, b_ccy, c("currency")),
        "po_number": _f(s.po, b_po, c("po_number")) if s.po else None,
        "lines": lines_out,
        "subtotal": _f(s.subtotal, tot.get("subtotal"), c("subtotal")) if s.subtotal else None,
        "tax_lines": [],
        "tax_total": _f(s.tax, tot.get("tax"), c("tax")) if s.tax else None,
        "charges": _f(s.charges, tot.get("charges"), c("charges")) if s.charges else None,
        "discount": None,
        "total": _f(s.total, b_total, c("total")),
    }
    return pdf, ext


SCENARIOS: list[Spec] = [
    # --- auto-pass, posts straight through
    Spec(
        "clean-steel",
        "E01",
        "Ceylon Steel Works",
        "TIN-114455",
        "CSW-24017",
        "2026-11-02",
        "LKR",
        "PO-55821",
        [
            ("Steel bolt M8 x 40 zinc", "A-100", "100", "120.00", "12,000.00", None),
            ("Hex nut M8 grade 8", "B-220", "200", "45.00", "9,000.00", None),
        ],
        "21,000.00",
        "18",
        "3,780.00",
        "24,780.00",
        note="Auto-approved and posted",
    ),
    Spec(
        "clean-pack",
        "E01",
        "Kandy Packaging",
        "TIN-441287",
        "KP-1023",
        "2026-11-03",
        "LKR",
        "PO-56044",
        [
            ("Corrugated carton 12x10x8", "PK-100", "40", "2,375.00", "95,000.00", "BDL"),
            ("Stretch wrap film 500mm", "PK-210", "10", "2,400.00", "24,000.00", None),
        ],
        "119,000.00",
        "18",
        "21,420.00",
        "140,420.00",
        note="UOM bundle->each via item master; auto-posted",
    ),
    Spec(
        "clean-e02",
        "E02",
        "Ceylon Steel Works",
        "TIN-114455",
        "CSW-24021",
        "2026-11-04",
        "LKR",
        "PO-61001",
        [
            ("Steel bolt M8 x 40 zinc", "A-100", "500", "118.00", "59,000.00", None),
            ("Anchor bolt M12", "D-440", "100", "310.00", "31,000.00", None),
        ],
        "90,000.00",
        "18",
        "16,200.00",
        "106,200.00",
        note="E02 auto-posted",
    ),
    # --- TSD wireframe: ambiguous date, closed period, +0.50 diff, unmatched line
    Spec(
        "wireframe",
        "E01",
        "Lanka Office Supplies",
        "TIN-220981",
        "INV-10234",
        "03/10/2026",
        "LKR",
        "PO-55902",
        [
            ("A4 copy paper 80gsm ream", "OF-010", "10", "1,450.00", "14,500.00", None),
            ("Toner cartridge HP 26A", "OF-022", "2", "19,126.80", "38,253.60", None),
            ("Desk organiser set", None, "3", "500.00", "1,500.00", None),
        ],
        "54,253.60",
        "18",
        "9,765.65",
        "64,019.75",
        conf={"invoice_date": 0.71},
        note="Date ambiguous + period closed + total +0.50 + L3",
    ),
    # --- maker-checker (USD, ~LKR 3.09M) with a low-confidence total
    Spec(
        "checker-usd",
        "E01",
        "Asia Pacific Logistics",
        "TIN-330112",
        "APL/2026/0881",
        "2026-10-28",
        "USD",
        "PO-56010",
        [
            ("Sea freight CMB-SIN 40ft", "FR-001", "3", "3,200.00", "9,600.00", None),
            ("Customs clearance fee", "FR-009", "3", "250.00", "750.00", None),
        ],
        "10,350.00",
        None,
        None,
        "10,350.00",
        conf={"total": 0.79},
        note="Above LKR 1M: needs a checker",
    ),
    # --- exact duplicate: same invoice, re-scanned (different bytes)
    Spec(
        "dup-exact",
        "E01",
        "Ceylon Steel Works",
        "TIN-114455",
        "CSW-24017",
        "2026-11-02",
        "LKR",
        "PO-55821",
        [
            ("Steel bolt M8 x 40 zinc", "A-100", "100", "120.00", "12,000.00", None),
            ("Hex nut M8 grade 8", "B-220", "200", "45.00", "9,000.00", None),
        ],
        "21,000.00",
        "18",
        "3,780.00",
        "24,780.00",
        salt="rescan-1",
        note="Re-scan of CSW-24017: DUP_EXACT blocks",
    ),
    # --- near duplicate of KP-1023
    Spec(
        "dup-fuzzy",
        "E01",
        "Kandy Packaging",
        "TIN-441287",
        "KP-1023A",
        "2026-11-04",
        "LKR",
        "PO-56044",
        [
            ("Corrugated carton 12x10x8", "PK-100", "40", "2,375.00", "95,000.00", "BDL"),
            ("Stretch wrap film 500mm", "PK-210", "10", "2,400.00", "24,000.00", None),
        ],
        "119,000.00",
        "18",
        "21,420.00",
        "140,420.00",
        note="KP-1023 vs KP-1023A",
    ),
    # --- blocked vendor
    Spec(
        "blocked",
        "E01",
        "Colombo Chemicals",
        "TIN-559034",
        "CC-7781",
        "2026-11-01",
        "LKR",
        None,
        [("Sodium hypochlorite 25L", "CH-201", "12", "4,800.00", "57,600.00", None)],
        "57,600.00",
        "18",
        "10,368.00",
        "67,968.00",
        note="Vendor blocked: BLOCK",
    ),
    # --- price variance over tolerance + qty over open
    Spec(
        "price-var",
        "E01",
        "Ceylon Steel Works",
        "TIN-114455",
        "CSW-24033",
        "2026-11-05",
        "LKR",
        "PO-55821",
        [
            ("Steel bolt M8 x 40 zinc", "A-100", "300", "124.20", "37,260.00", None),
            ("Flat washer M8", "C-330", "12000", "12.00", "144,000.00", None),
        ],
        "181,260.00",
        "18",
        "32,626.80",
        "213,886.80",
        note="L1 price +3.5%, L2 qty over open PO qty",
    ),
    # --- low confidence only (clean otherwise)
    Spec(
        "low-conf",
        "E01",
        "Lanka Office Supplies",
        "TIN-220981",
        "INV-10251",
        "2026-11-04",
        "LKR",
        "PO-55902",
        [("A4 copy paper 80gsm ream", "OF-010", "20", "1,450.00", "29,000.00", None)],
        "29,000.00",
        "18",
        "5,220.00",
        "34,220.00",
        conf={"line:1:qty": 0.62, "invoice_no": 0.81},
        note="Only low confidence",
    ),
    # --- extraction failed -> manual entry
    Spec(
        "unreadable",
        "E01",
        "Unknown Supplier",
        "-",
        "?",
        "?",
        "LKR",
        None,
        [("(smudged)", None, "?", "?", "?", None)],
        None,
        None,
        None,
        "?",
        no_sidecar=True,
        note="No extraction: manual-entry task",
    ),
    # --- more auto-pass volume for the dashboard
    *[
        Spec(
            f"vol-{i}",
            "E01" if i % 3 else "E02",
            "Ceylon Steel Works",
            "TIN-114455",
            f"CSW-2405{i}",
            "2026-11-05",
            "LKR",
            "PO-55821" if i % 3 else "PO-61001",
            [
                (
                    "Steel bolt M8 x 40 zinc",
                    "A-100",
                    str(10 * i),
                    "120.00" if i % 3 else "118.00",
                    f"{10 * i * (120 if i % 3 else 118):,}.00",
                    None,
                )
            ],
            f"{10 * i * (120 if i % 3 else 118):,}.00",
            "18",
            f"{10 * i * (120 if i % 3 else 118) * 0.18:,.2f}",
            f"{10 * i * (120 if i % 3 else 118) * 1.18:,.2f}",
            note="volume",
        )
        for i in range(1, 9)
    ],
]
