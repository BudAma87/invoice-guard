"""Stage 2 — identity: who sent it, is it ours, have we seen it."""

from __future__ import annotations

from libs.rules._base import result, skipped
from libs.rules.config import RuleSetConfig
from libs.rules.text import levenshtein
from libs.rules.types import Context, Group, Invoice, RuleResult, Severity, Stage

S = Stage.IDENTITY
G = Group.IDENTITY
ERP_DOWN = "ERP unavailable"


def supplier_resolve(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    if not ctx.erp_available:
        return skipped("SUPPLIER_RESOLVE", "ERP", S, G, ERP_DOWN)
    n = len(ctx.vendor_matches)
    if n == 1:
        v = ctx.vendor_matches[0]
        return result(
            "SUPPLIER_RESOLVE",
            True,
            S,
            G,
            f"Resolved to {v.name} ({v.supplier_id})",
            actual=v.supplier_id,
        )
    msg = "No vendor matches the supplier name or tax ID" if n == 0 else f"{n} vendors match"
    return result("SUPPLIER_RESOLVE", False, S, G, msg, actual=n, fields=("supplier",))


def supplier_inactive(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    v = ctx.vendor
    if v is None:
        return skipped("SUPPLIER_INACTIVE", "SUPPLIER_RESOLVE", S, G, severity=Severity.BLOCK)
    bad = v.blocked or not v.active
    return result(
        "SUPPLIER_INACTIVE",
        not bad,
        S,
        G,
        f"Vendor {v.supplier_id} is {'blocked' if v.blocked else 'inactive'} in the ERP"
        if bad
        else "Vendor is active",
        severity=Severity.BLOCK,
        fields=("supplier",),
    )


def dup_exact(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    if not (inv.invoice_no and inv.invoice_date and inv.total_minor is not None and inv.currency):
        return skipped(
            "DUP_EXACT", "DATE_PARSE", S, G, "Business key incomplete", severity=Severity.BLOCK
        )
    hits = [d for d in ctx.exact_duplicates if d.invoice_id != inv.invoice_id]
    if hits:
        h = hits[0]
        return result(
            "DUP_EXACT",
            False,
            S,
            G,
            f"Same supplier, invoice no., date and total already {h.state.lower()} "
            f"({h.invoice_id[:8]})",
            severity=Severity.BLOCK,
            actual=[d.invoice_id for d in hits],
            fields=("invoice_no", "invoice_date", "total"),
        )
    return result("DUP_EXACT", True, S, G, "No exact duplicate", severity=Severity.BLOCK)


def dup_fuzzy(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    v = ctx.vendor
    if v is None:
        return skipped("DUP_FUZZY", "SUPPLIER_RESOLVE", S, G)
    if not (inv.invoice_no and inv.invoice_date and inv.total_minor is not None):
        return skipped("DUP_FUZZY", "DATE_PARSE", S, G, "Business key incomplete")
    exact_ids = {d.invoice_id for d in ctx.exact_duplicates}
    hits = [
        d
        for d in ctx.fuzzy_candidates
        if d.invoice_id != inv.invoice_id
        and d.invoice_id not in exact_ids
        and d.state != "REJECTED"
        and d.supplier_id == v.supplier_id
        and d.total_minor == inv.total_minor
        and d.currency == inv.currency
        and abs((d.invoice_date - inv.invoice_date).days) <= cfg.dup_fuzzy_days
        and levenshtein(d.invoice_no, inv.invoice_no) <= cfg.dup_fuzzy_max_edit
    ]
    if hits:
        h = hits[0]
        return result(
            "DUP_FUZZY",
            False,
            S,
            G,
            f"Looks like {h.invoice_no} of {h.invoice_date.isoformat()} (same supplier and total)",
            actual=[d.invoice_id for d in hits],
            fields=("invoice_no",),
        )
    return result(
        "DUP_FUZZY", True, S, G, f"No near-duplicate in the last {cfg.dup_fuzzy_days} days"
    )


def po_ownership(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    if not inv.po_number:
        return result(
            "PO_OWNERSHIP", False, S, G, "No PO number on the invoice", fields=("po_number",)
        )
    if not ctx.erp_available:
        return skipped("PO_OWNERSHIP", "ERP", S, G, ERP_DOWN)
    po = ctx.po
    if po is None:
        return result(
            "PO_OWNERSHIP", False, S, G, f"PO {inv.po_number} not found", fields=("po_number",)
        )
    problems = []
    if po.status != "OPEN":
        problems.append(f"PO is {po.status.lower()}")
    if ctx.vendor and po.supplier_id != ctx.vendor.supplier_id:
        problems.append("PO belongs to another supplier")
    if po.entity_id != inv.entity_id:
        problems.append("PO belongs to another legal entity")
    return result(
        "PO_OWNERSHIP",
        not problems,
        S,
        G,
        "; ".join(problems) if problems else f"PO {po.po_number} is open and matches",
        fields=("po_number",),
    )


RULES = (supplier_resolve, supplier_inactive, dup_exact, dup_fuzzy, po_ownership)
