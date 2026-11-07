"""Stage 3 — arithmetic. No reference data except the supplier's allowed tax rates."""

from __future__ import annotations

from decimal import Decimal

from libs.rules._base import result, skipped
from libs.rules.config import RuleSetConfig
from libs.rules.money import fmt, round_minor
from libs.rules.types import Context, Group, Invoice, RuleResult, Stage

S = Stage.ARITHMETIC
G = Group.TOTAL


def line_arith(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> list[RuleResult]:
    out = []
    for ln in inv.lines:
        if ln.qty is None or ln.unit_price_minor is None or ln.amount_minor is None:
            missing = next(
                f
                for f, v in (
                    ("qty", ln.qty),
                    ("unit_price", ln.unit_price_minor),
                    ("amount", ln.amount_minor),
                )
                if v is None
            )
            out.append(
                skipped(
                    "LINE_ARITH", f"line {ln.line_no} {missing}", S, Group.LINES, line=ln.line_no
                )
            )
            continue
        computed = round_minor(ln.qty * Decimal(ln.unit_price_minor))
        diff = ln.amount_minor - computed
        out.append(
            result(
                "LINE_ARITH",
                abs(diff) <= 1,
                S,
                Group.LINES,
                f"{ln.qty} × {fmt(ln.unit_price_minor, inv.currency)} = "
                f"{fmt(computed, inv.currency)}; line says {fmt(ln.amount_minor, inv.currency)}"
                + (f" (diff {fmt(diff, inv.currency, signed=True)})" if diff else ""),
                expected=computed,
                actual=ln.amount_minor,
                line=ln.line_no,
                fields=(f"line:{ln.line_no}:amount",),
            )
        )
    return out


def _line_sum(inv: Invoice) -> int | None:
    if not inv.lines or any(ln.amount_minor is None for ln in inv.lines):
        return None
    return sum(ln.amount_minor or 0 for ln in inv.lines)


def subtotal_sum(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    lines = _line_sum(inv)
    if inv.subtotal_minor is None:
        return result(
            "SUBTOTAL_SUM", True, S, G, "No subtotal printed; line sum used", expected=lines
        )
    if lines is None:
        return skipped("SUBTOTAL_SUM", "LINE_ARITH", S, G)
    diff = inv.subtotal_minor - lines
    return result(
        "SUBTOTAL_SUM",
        abs(diff) <= 1,
        S,
        G,
        f"Lines sum to {fmt(lines, inv.currency)}; subtotal says "
        f"{fmt(inv.subtotal_minor, inv.currency)}",
        expected=lines,
        actual=inv.subtotal_minor,
        fields=("subtotal",),
    )


def tax_check(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    allowed = ctx.vendor.allowed_tax_rates if ctx.vendor else None
    if inv.tax_lines:
        for t in inv.tax_lines:
            if allowed is not None and allowed and t.rate not in allowed:
                return result(
                    "TAX_CHECK",
                    False,
                    S,
                    G,
                    f"Tax rate {t.rate:.2%} not allowed for this supplier",
                    expected=[str(r) for r in allowed],
                    actual=str(t.rate),
                    fields=("tax",),
                )
            computed = round_minor(Decimal(t.base_minor) * t.rate)
            if abs(computed - t.amount_minor) > 1:
                return result(
                    "TAX_CHECK",
                    False,
                    S,
                    G,
                    f"{t.rate:.2%} of {fmt(t.base_minor, inv.currency)} = "
                    f"{fmt(computed, inv.currency)}; invoice says "
                    f"{fmt(t.amount_minor, inv.currency)}",
                    expected=computed,
                    actual=t.amount_minor,
                    fields=("tax",),
                )
        if sum(t.amount_minor for t in inv.tax_lines) != inv.tax_minor:
            return result(
                "TAX_CHECK",
                False,
                S,
                G,
                "Tax lines do not add up to tax total",
                expected=sum(t.amount_minor for t in inv.tax_lines),
                actual=inv.tax_minor,
                fields=("tax",),
            )
        return result("TAX_CHECK", True, S, G, "Tax lines check out")
    if inv.tax_minor == 0:
        return result("TAX_CHECK", True, S, G, "No tax charged")
    if allowed is None:
        return skipped("TAX_CHECK", "SUPPLIER_RESOLVE", S, G)
    base = inv.subtotal_minor if inv.subtotal_minor is not None else _line_sum(inv)
    if base is None:
        return skipped("TAX_CHECK", "LINE_ARITH", S, G)
    for rate in allowed:
        if abs(round_minor(Decimal(base) * rate) - inv.tax_minor) <= 1:
            return result(
                "TAX_CHECK",
                True,
                S,
                G,
                f"Tax = {rate:.2%} of {fmt(base, inv.currency)}",
                expected=inv.tax_minor,
            )
    return result(
        "TAX_CHECK",
        False,
        S,
        G,
        f"Tax {fmt(inv.tax_minor, inv.currency)} matches no allowed rate "
        f"({', '.join(f'{r:.0%}' for r in allowed) or 'none'})",
        actual=inv.tax_minor,
        fields=("tax",),
    )


def total_reconcile(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    if inv.total_minor is None:
        return skipped("TOTAL_RECONCILE", "total", S, G, "Invoice total missing", fields=("total",))
    base = inv.subtotal_minor if inv.subtotal_minor is not None else _line_sum(inv)
    if base is None:
        return skipped("TOTAL_RECONCILE", "LINE_ARITH", S, G)
    tol = cfg.total_tolerance_minor(inv.currency)
    computed = base + inv.tax_minor + inv.charges_minor - inv.discount_minor
    diff = inv.total_minor - computed
    c = inv.currency
    msg = (
        f"Lines {fmt(base, c)} + Tax {fmt(inv.tax_minor, c)} + Charges "
        f"{fmt(inv.charges_minor, c)}"
        + (f" − Discount {fmt(inv.discount_minor, c)}" if inv.discount_minor else "")
        + f" = {fmt(computed, c)}; invoice says {fmt(inv.total_minor, c)}"
        + (f" (diff {fmt(diff, c, signed=True)})" if diff else "")
    )
    return result(
        "TOTAL_RECONCILE",
        abs(diff) <= tol,
        S,
        G,
        msg,
        expected=computed,
        actual=inv.total_minor,
        fields=("total", "tax", "charges"),
    )


def total_positive(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    if inv.total_minor is None:
        return skipped("TOTAL_POSITIVE", "total", S, G)
    return result(
        "TOTAL_POSITIVE",
        inv.total_minor > 0,
        S,
        G,
        "Total is positive"
        if inv.total_minor > 0
        else "Total ≤ 0 — credit notes use a separate flow",
        actual=inv.total_minor,
        fields=("total",),
    )


SINGLE = (subtotal_sum, tax_check, total_reconcile, total_positive)
PER_LINE = (line_arith,)
