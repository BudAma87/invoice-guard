"""ORM <-> rule-engine shapes, and reviewer draft corrections.

Corrections shape (from the review UI; never saved by /validate):
  {"header": {"invoice_date": "2026-10-03", "total_minor": 141600, ...},
   "lines": [{"line_no": 1, "description": ..., "qty": "10", "unit_price_minor": ...,
              "amount_minor": ..., "item_code": ..., "uom": ..., "po_line_id": ...}, ...]}
`lines`, when present, replaces the whole line list (so rows can be added/split/deleted).
A corrected field's confidence becomes 1.0: a person typed it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import object_session

from libs.db import models as m
from libs.rules import types as rt
from libs.rules.text import normalise_invoice_no

HEADER_FIELDS = {
    "supplier_name": "supplier",
    "supplier_tax_id": "supplier_tax_id",
    "invoice_no": "invoice_no",
    "invoice_date": "invoice_date",
    "currency": "currency",
    "po_number": "po_number",
    "subtotal_minor": "subtotal",
    "tax_minor": "tax",
    "charges_minor": "charges",
    "discount_minor": "discount",
    "total_minor": "total",
}
LINE_FIELDS = (
    "description",
    "item_code",
    "uom",
    "qty",
    "unit_price_minor",
    "amount_minor",
    "po_line_id",
)


class BadCorrection(ValueError):
    pass


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation as e:
        raise BadCorrection(f"not a number: {v!r}") from e


def _int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError) as e:
        raise BadCorrection(f"not an integer amount: {v!r}") from e


def header_dict(inv: m.Invoice) -> dict[str, Any]:
    return {
        "supplier_name": inv.supplier_name,
        "supplier_tax_id": inv.supplier_tax_id,
        "invoice_no": inv.invoice_no_raw,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "currency": inv.currency,
        "po_number": inv.po_number,
        "subtotal_minor": inv.subtotal_minor,
        "tax_minor": inv.tax_minor,
        "charges_minor": inv.charges_minor,
        "discount_minor": inv.discount_minor,
        "total_minor": inv.total_minor,
    }


def line_dict(ln: m.InvoiceLine) -> dict[str, Any]:
    return {
        "line_no": ln.line_no,
        "description": ln.description,
        "item_code": ln.item_code,
        "uom": ln.uom,
        "qty": ln.qty,
        "unit_price_minor": ln.unit_price_minor,
        "amount_minor": ln.amount_minor,
        "po_line_id": ln.po_line_id,
        "confidence": ln.confidence_json,
        "evidence": ln.evidence_json,
    }


def to_rules(inv: m.Invoice, corrections: dict[str, Any] | None = None) -> rt.Invoice:
    corrections = corrections or {}
    hdr = header_dict(inv)
    conf = dict(inv.confidence or {})
    hc = corrections.get("header") or {}
    unknown = set(hc) - set(HEADER_FIELDS)
    if unknown:
        raise BadCorrection(f"unknown header fields: {sorted(unknown)}")
    for k, v in hc.items():
        hdr[k] = v
        conf[HEADER_FIELDS[k]] = 1.0

    date_ambiguous = inv.date_ambiguous and "invoice_date" not in hc
    try:
        d = date.fromisoformat(hdr["invoice_date"]) if hdr["invoice_date"] else None
    except ValueError as e:
        raise BadCorrection("invoice_date must be YYYY-MM-DD") from e

    if "lines" in corrections:
        raw_lines = corrections["lines"] or []
        lines = []
        for i, ln in enumerate(raw_lines, 1):
            line_no = int(ln.get("line_no") or i)
            orig = next((x for x in inv.lines if x.line_no == line_no), None)
            changed = {f for f in LINE_FIELDS if orig is None or ln.get(f) != getattr(orig, f)}
            lc = dict(orig.confidence_json) if orig else {}
            for f, key in (
                ("qty", "qty"),
                ("unit_price_minor", "unit_price"),
                ("amount_minor", "amount"),
            ):
                if f in changed:
                    lc[key] = 1.0
                conf[f"line:{line_no}:{key}"] = lc.get(key, 1.0)
            lines.append(
                rt.Line(
                    line_no=line_no,
                    description=ln.get("description") or "",
                    qty=_dec(ln.get("qty")),
                    unit_price_minor=_int(ln.get("unit_price_minor")),
                    amount_minor=_int(ln.get("amount_minor")),
                    item_code=ln.get("item_code") or None,
                    uom=(ln.get("uom") or None),
                    po_line_id=ln.get("po_line_id") or None,
                )
            )
        # drop stale per-line confidence for removed lines
        keep = {f"line:{ln.line_no}:" for ln in lines}
        conf = {
            k: v
            for k, v in conf.items()
            if not k.startswith("line:") or any(k.startswith(p) for p in keep)
        }
    else:
        lines = [
            rt.Line(
                line_no=ln.line_no,
                description=ln.description,
                qty=_dec(ln.qty),
                unit_price_minor=ln.unit_price_minor,
                amount_minor=ln.amount_minor,
                item_code=ln.item_code,
                uom=ln.uom,
                po_line_id=ln.po_line_id,
            )
            for ln in inv.lines
        ]

    tax_lines = (
        tuple(
            rt.TaxLine(Decimal(t["rate"]), int(t["base_minor"]), int(t["amount_minor"]))
            for t in (inv.tax_lines or [])
        )
        if "tax_minor" not in hc
        else ()
    )

    return rt.Invoice(
        invoice_id=inv.invoice_id,
        entity_id=inv.entity_id,
        supplier_name=hdr["supplier_name"],
        supplier_tax_id=hdr["supplier_tax_id"],
        invoice_no=normalise_invoice_no(hdr["invoice_no"]),
        invoice_date=d,
        currency=(hdr["currency"] or None) and str(hdr["currency"]).upper(),
        po_number=(hdr["po_number"] or None) and str(hdr["po_number"]).upper(),
        total_minor=_int(hdr["total_minor"]),
        subtotal_minor=_int(hdr["subtotal_minor"]),
        tax_minor=_int(hdr["tax_minor"]) or 0,
        charges_minor=_int(hdr["charges_minor"]) or 0,
        discount_minor=_int(hdr["discount_minor"]) or 0,
        lines=tuple(lines),
        tax_lines=tax_lines,
        date_ambiguous=date_ambiguous,
        confidence=conf,
    )


def apply_corrections(inv: m.Invoice, corrections: dict[str, Any]) -> dict[str, Any]:
    """Persist reviewer corrections onto the invoice. Returns {field: [old, new]} for audit."""
    r = to_rules(inv, corrections)  # validates
    changed: dict[str, Any] = {}
    before = header_dict(inv)
    for k, v in (corrections.get("header") or {}).items():
        if before.get(k) != v:
            changed[k] = [before.get(k), v]
    hc = corrections.get("header") or {}
    if "invoice_no" in hc:
        inv.invoice_no_raw = hc["invoice_no"]
        inv.invoice_no_norm = r.invoice_no
    if "invoice_date" in hc:
        inv.invoice_date = r.invoice_date
        inv.date_ambiguous = False
    for k in ("supplier_name", "supplier_tax_id"):
        if k in hc:
            setattr(inv, k, hc[k])
    if "currency" in hc:
        inv.currency = r.currency
    if "po_number" in hc:
        inv.po_number = r.po_number
    for k in ("subtotal_minor", "total_minor"):
        if k in hc:
            setattr(inv, k, getattr(r, k))
    for k in ("tax_minor", "charges_minor", "discount_minor"):
        if k in hc:
            setattr(inv, k, getattr(r, k))
    if "tax_minor" in hc:
        inv.tax_lines = []
    if "lines" in corrections:
        old = [line_dict(x) for x in inv.lines]
        new_conf = r.confidence
        inv.lines.clear()
        sess = object_session(inv)
        if sess is not None:  # deletes must hit the DB before re-inserting same line_no
            sess.flush()
        for ln in r.lines:
            inv.lines.append(
                m.InvoiceLine(
                    line_no=ln.line_no,
                    description=ln.description,
                    item_code=ln.item_code,
                    uom=ln.uom,
                    po_line_id=ln.po_line_id,
                    qty=str(ln.qty) if ln.qty is not None else None,
                    unit_price_minor=ln.unit_price_minor,
                    amount_minor=ln.amount_minor,
                    confidence_json={
                        k: new_conf.get(f"line:{ln.line_no}:{k}", 1.0)
                        for k in ("qty", "unit_price", "amount")
                    },
                    evidence_json=next(
                        (o["evidence"] for o in old if o["line_no"] == ln.line_no), {}
                    ),
                )
            )
        changed["lines"] = [
            [{k: o[k] for k in ("line_no", *LINE_FIELDS)} for o in old],
            corrections["lines"],
        ]
    inv.confidence = dict(r.confidence)
    return changed
