"""Step 5: strict parse + normalise model output into the state DB shape.

Amounts -> integer minor units, dates -> ISO 8601 (flag dd/mm vs mm/dd ambiguity),
currency -> ISO 4217, invoice numbers -> upper-case alnum. A field that will not parse
keeps value None and confidence 0, so it is highlighted for review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from libs.llm_gateway.schemas import F, InvoiceExtraction
from libs.rules.money import to_minor
from libs.rules.text import normalise_invoice_no

_CCY_ALIASES = {"RS": "LKR", "RS.": "LKR", "LKR": "LKR", "$": "USD", "US$": "USD", "€": "EUR"}
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMERIC = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})$")
_INJECTION = re.compile(
    r"(ignore (all|previous) instructions|approve this invoice|skip (the )?checks|system prompt)",
    re.I,
)


@dataclass
class Normalised:
    header: dict[str, Any]
    lines: list[dict[str, Any]]
    tax_lines: list[dict[str, Any]]
    confidence: dict[str, float]
    evidence: dict[str, Any]
    flags: list[str] = field(default_factory=list)


def parse_date(raw: str | None, locale: str | None = None) -> tuple[date | None, bool]:
    """Returns (date, ambiguous). locale: 'DMY' | 'MDY' | None."""
    if not raw:
        return None, False
    s = raw.strip()
    if _ISO.match(s):
        try:
            return date.fromisoformat(s), False
        except ValueError:
            return None, False
    m = _NUMERIC.match(s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        y = y + 2000 if y < 100 else y
        ambiguous = a <= 12 and b <= 12 and a != b
        order = locale or "DMY"
        d, mth = (a, b) if order == "DMY" else (b, a)
        try:
            return date(y, mth, d), ambiguous and locale is None
        except ValueError:
            try:  # only one order is valid -> not ambiguous
                return date(y, a if order == "DMY" else b, b if order == "DMY" else a), False
            except ValueError:
                return None, False
    for fmt in ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt).date(), False
        except ValueError:
            continue
    return None, False


def parse_currency(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip().upper()
    if s in _CCY_ALIASES:
        return _CCY_ALIASES[s]
    return s if re.fullmatch(r"[A-Z]{3}", s) else None


def _amount(f: F | None, ccy: str | None) -> tuple[int | None, float]:
    if f is None or f.value is None:
        return None, 1.0 if f is None else f.confidence
    try:
        return to_minor(re.sub(r"[^\d.,\-]", "", f.value), ccy), f.confidence
    except ValueError:
        return None, 0.0


def _qty(f: F) -> tuple[Decimal | None, float]:
    if f.value is None:
        return None, f.confidence
    try:
        return Decimal(f.value.replace(",", "").strip()), f.confidence
    except InvalidOperation:
        return None, 0.0


def _ev(f: F | None) -> dict[str, Any] | None:
    return f.evidence.model_dump() if f is not None and f.evidence else None


def _check_evidence(f: F | None, pdf_text: str | None) -> float | None:
    """Hallucination guard: quoted source text must appear in the PDF text layer."""
    if f is None or f.value is None or pdf_text is None or f.evidence is None:
        return None
    needle = re.sub(r"\s+", "", f.evidence.text).lower()
    hay = re.sub(r"\s+", "", pdf_text).lower()
    return None if needle and needle in hay else 0.0


def normalise(
    x: InvoiceExtraction, *, pdf_text: str | None = None, date_locale: str | None = None
) -> Normalised:
    conf: dict[str, float] = {}
    ev: dict[str, Any] = {}
    flags: list[str] = []

    def track(key: str, f: F | None, c: float | None = None) -> None:
        if f is None:
            return
        forced = _check_evidence(f, pdf_text)
        conf[key] = forced if forced is not None else (c if c is not None else f.confidence)
        if forced == 0.0:
            flags.append(f"EVIDENCE_MISMATCH:{key}")
        if e := _ev(f):
            ev[key] = e

    ccy = parse_currency(x.currency.value)
    track("currency", x.currency, x.currency.confidence if ccy else 0.0)
    d, ambiguous = parse_date(x.invoice_date.value, date_locale)
    track("invoice_date", x.invoice_date, x.invoice_date.confidence if d else 0.0)
    inv_no = normalise_invoice_no(x.invoice_no.value)
    track("invoice_no", x.invoice_no)
    track("supplier", x.supplier_name)
    track("supplier_tax_id", x.supplier_tax_id)
    track("po_number", x.po_number)

    total, c_total = _amount(x.total, ccy)
    track("total", x.total, c_total)
    subtotal, c_sub = _amount(x.subtotal, ccy)
    track("subtotal", x.subtotal, c_sub)
    charges, c_ch = _amount(x.charges, ccy)
    track("charges", x.charges, c_ch)
    discount, c_di = _amount(x.discount, ccy)
    track("discount", x.discount, c_di)

    tax_lines: list[dict[str, Any]] = []
    tax_sum = 0
    for t in x.tax_lines:
        rate_raw = (t.rate_percent.value or "").replace("%", "").strip()
        try:
            rate = Decimal(rate_raw) / 100
        except InvalidOperation:
            continue
        base, _ = _amount(t.base, ccy)
        amt, _ = _amount(t.amount, ccy)
        if base is not None and amt is not None:
            tax_lines.append({"rate": str(rate), "base_minor": base, "amount_minor": amt})
            tax_sum += amt
    tax, c_tax = _amount(x.tax_total, ccy)
    if tax is None and tax_lines:
        tax = tax_sum
    track("tax", x.tax_total, c_tax)

    lines = []
    for i, ln in enumerate(x.lines, 1):
        q, cq = _qty(ln.qty)
        p, cp = _amount(ln.unit_price, ccy)
        a, ca = _amount(ln.amount, ccy)
        lc = {"qty": cq, "unit_price": cp, "amount": ca, "description": ln.description.confidence}
        lev = {
            k: _ev(f)
            for k, f in (
                ("qty", ln.qty),
                ("unit_price", ln.unit_price),
                ("amount", ln.amount),
                ("description", ln.description),
            )
            if _ev(f)
        }
        for k, f in (("qty", ln.qty), ("unit_price", ln.unit_price), ("amount", ln.amount)):
            forced = _check_evidence(f, pdf_text)
            if forced is not None:
                lc[k] = forced
                flags.append(f"EVIDENCE_MISMATCH:line:{i}:{k}")
            conf[f"line:{i}:{k}"] = lc[k]
        lines.append(
            {
                "line_no": i,
                "description": ln.description.value or "",
                "item_code": (ln.item_code.value or None) if ln.item_code else None,
                "uom": ((ln.uom.value or "").upper() or None) if ln.uom else None,
                "qty": str(q) if q is not None else None,
                "unit_price_minor": p,
                "amount_minor": a,
                "confidence_json": lc,
                "evidence_json": lev,
            }
        )

    parts = [x.supplier_name.value, x.invoice_no.value, *(ln.description.value for ln in x.lines)]
    all_text = " ".join(p or "" for p in parts)
    if _INJECTION.search(all_text):
        flags.append("PROMPT_INJECTION_PATTERN")

    header = {
        "supplier_name": x.supplier_name.value,
        "supplier_tax_id": x.supplier_tax_id.value if x.supplier_tax_id else None,
        "invoice_no_raw": x.invoice_no.value,
        "invoice_no_norm": inv_no,
        "invoice_date": d,
        "date_ambiguous": ambiguous,
        "currency": ccy,
        "po_number": ((x.po_number.value or "").strip().upper() or None) if x.po_number else None,
        "subtotal_minor": subtotal,
        "tax_minor": tax or 0,
        "charges_minor": charges or 0,
        "discount_minor": discount or 0,
        "total_minor": total,
    }
    return Normalised(header, lines, tax_lines, conf, ev, flags)
