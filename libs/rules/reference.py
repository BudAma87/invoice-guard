"""Stage 4 — reference: date rules, PO currency/balance and line rules against PO + item master."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from libs.rules._base import result, skipped
from libs.rules.config import RuleSetConfig
from libs.rules.money import fmt, round_minor
from libs.rules.text import similarity
from libs.rules.types import (
    Context,
    Group,
    Invoice,
    Line,
    POLine,
    RuleResult,
    Severity,
    Stage,
    Status,
)

S = Stage.REFERENCE
ERP_DOWN = "ERP unavailable"


# ----------------------------------------------------------------------------- dates


def date_not_future(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    if inv.invoice_date is None:
        return skipped("DATE_NOT_FUTURE", "DATE_PARSE", S, Group.DATE)
    ahead = (inv.invoice_date - ctx.today).days
    return result(
        "DATE_NOT_FUTURE",
        ahead <= cfg.date_future_grace_days,
        S,
        Group.DATE,
        "Not in the future"
        if ahead <= cfg.date_future_grace_days
        else f"{ahead} days in the future",
        expected=ctx.today,
        actual=inv.invoice_date,
        fields=("invoice_date",),
    )


def date_age(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    if inv.invoice_date is None:
        return skipped("DATE_AGE", "DATE_PARSE", S, Group.DATE)
    age = (ctx.today - inv.invoice_date).days
    ok = age <= cfg.date_max_age_days
    return result(
        "DATE_AGE",
        ok,
        S,
        Group.DATE,
        f"{max(age, 0)} days old (limit {cfg.date_max_age_days})",
        expected=cfg.date_max_age_days,
        actual=age,
        fields=("invoice_date",),
    )


def date_after_po(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    if inv.invoice_date is None:
        return skipped("DATE_AFTER_PO", "DATE_PARSE", S, Group.DATE)
    if ctx.po is None:
        return skipped("DATE_AFTER_PO", "PO_OWNERSHIP", S, Group.DATE)
    ok = inv.invoice_date >= ctx.po.approved_on
    return result(
        "DATE_AFTER_PO",
        ok,
        S,
        Group.DATE,
        f"On or after PO date {ctx.po.approved_on.isoformat()}"
        if ok
        else f"Before PO date {ctx.po.approved_on.isoformat()}",
        expected=ctx.po.approved_on,
        actual=inv.invoice_date,
        fields=("invoice_date",),
    )


def date_period_open(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    if inv.invoice_date is None:
        return skipped("DATE_PERIOD_OPEN", "DATE_PARSE", S, Group.DATE)
    if not ctx.erp_available or ctx.period is None:
        return skipped("DATE_PERIOD_OPEN", "ERP", S, Group.DATE, ERP_DOWN)
    p = ctx.period
    return result(
        "DATE_PERIOD_OPEN",
        p.open,
        S,
        Group.DATE,
        f"Period {p.period} open"
        if p.open
        else f"Period {p.period} closed"
        + (f" — post in {p.first_open} with PERIOD_CLOSED" if p.first_open else ""),
        expected=p.first_open,
        actual=p.period,
        fields=("invoice_date",),
    )


# ----------------------------------------------------------------------------- PO header


def currency_match(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    if not inv.currency:
        return skipped("CURRENCY_MATCH", "currency", S, Group.TOTAL)
    if ctx.po is None:
        return skipped("CURRENCY_MATCH", "PO_OWNERSHIP", S, Group.TOTAL)
    problems = []
    if inv.currency != ctx.po.currency:
        problems.append(f"PO is in {ctx.po.currency}")
    v = ctx.vendor
    if v and v.allowed_currencies and inv.currency not in v.allowed_currencies:
        problems.append(f"{inv.currency} not allowed for this supplier")
    return result(
        "CURRENCY_MATCH",
        not problems,
        S,
        Group.TOTAL,
        "; ".join(problems) or f"{inv.currency} matches PO",
        expected=ctx.po.currency,
        actual=inv.currency,
        fields=("currency",),
    )


def total_vs_po(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    if inv.total_minor is None:
        return skipped("TOTAL_VS_PO", "total", S, Group.TOTAL)
    if ctx.po is None:
        return skipped("TOTAL_VS_PO", "PO_OWNERSHIP", S, Group.TOTAL)
    limit = Decimal(ctx.po.remaining_minor) * (1 + cfg.total_vs_po_tolerance)
    ok = Decimal(inv.total_minor) <= limit
    c = inv.currency
    return result(
        "TOTAL_VS_PO",
        ok,
        S,
        Group.TOTAL,
        f"Remaining PO balance {fmt(ctx.po.remaining_minor, c)} "
        f"(+{cfg.total_vs_po_tolerance:.0%} tolerance)",
        expected=ctx.po.remaining_minor,
        actual=inv.total_minor,
        fields=("total",),
    )


# ----------------------------------------------------------------------------- lines


@dataclass(frozen=True)
class LineMatch:
    po_line: POLine | None
    method: str  # "reviewer" | "item_code" | "description" | "none"
    score: float
    note: str = ""


def rank_po_lines(line: Line, po_lines: tuple[POLine, ...]) -> list[tuple[POLine, float]]:
    """For the UI's PO line picker: open PO lines ranked by match quality."""

    def score(pl: POLine) -> float:
        if line.item_code and line.item_code.upper() == pl.item_code.upper():
            return 1.0
        return similarity(line.description, pl.description)

    return sorted(((pl, score(pl)) for pl in po_lines), key=lambda t: -t[1])


def match_line(line: Line, po_lines: tuple[POLine, ...], cfg: RuleSetConfig) -> LineMatch:
    if line.po_line_id:
        pl = next((p for p in po_lines if p.po_line_id == line.po_line_id), None)
        if pl:
            return LineMatch(pl, "reviewer", 1.0)
        return LineMatch(None, "none", 0.0, "Chosen PO line is not open on this PO")
    if line.item_code:
        by_code = [p for p in po_lines if p.item_code.upper() == line.item_code.upper()]
        if len(by_code) == 1:
            return LineMatch(by_code[0], "item_code", 1.0)
        if len(by_code) > 1:
            return LineMatch(None, "none", 0.0, f"{len(by_code)} PO lines share item code")
    ranked = [(p, similarity(line.description, p.description)) for p in po_lines]
    ranked.sort(key=lambda t: -t[1])
    if ranked and ranked[0][1] >= cfg.line_similarity:
        if len(ranked) > 1 and ranked[1][1] >= cfg.line_similarity:
            return LineMatch(None, "none", ranked[0][1], "Several PO lines match the description")
        return LineMatch(ranked[0][0], "description", ranked[0][1])
    best = ranked[0][1] if ranked else 0.0
    return LineMatch(None, "none", best, f"Best description match {best:.0%}")


def line_rules(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> list[RuleResult]:
    out: list[RuleResult] = []
    G = Group.LINES
    po = ctx.po
    c = inv.currency
    matched_po_lines: dict[str, list[int]] = {}

    for ln in inv.lines:
        n = ln.line_no
        f = (f"line:{n}:item_code",)

        # LINE_PO_MATCH
        if po is None:
            out.append(skipped("LINE_PO_MATCH", "PO_OWNERSHIP", S, G, line=n))
            m = LineMatch(None, "none", 0.0)
        else:
            open_lines = tuple(p for p in po.lines if p.open_qty > 0)
            m = match_line(ln, open_lines, cfg)
            if m.po_line:
                matched_po_lines.setdefault(m.po_line.po_line_id, []).append(n)
            out.append(
                result(
                    "LINE_PO_MATCH",
                    m.po_line is not None,
                    S,
                    G,
                    f"Matched PO line {m.po_line.line_no} ({m.po_line.item_code}) by "
                    f"{m.method.replace('_', ' ')}"
                    if m.po_line
                    else f"Unmatched — {m.note or 'pick a PO line'}",
                    expected=m.po_line.po_line_id if m.po_line else None,
                    actual=round(m.score, 3),
                    line=n,
                    fields=f,
                )
            )

        # LINE_ITEM_MASTER
        code = ln.item_code or (m.po_line.item_code if m.po_line else None)
        if not code:
            out.append(result("LINE_ITEM_MASTER", False, S, G, "No item code", line=n, fields=f))
        elif not ctx.erp_available:
            out.append(skipped("LINE_ITEM_MASTER", "ERP", S, G, ERP_DOWN, line=n))
        else:
            item = ctx.items.get(code.upper())
            ok = item is not None and item.active
            out.append(
                result(
                    "LINE_ITEM_MASTER",
                    ok,
                    S,
                    G,
                    f"{code} active in item master"
                    if ok
                    else f"{code} {'inactive' if item else 'not found'} in item master",
                    actual=code,
                    line=n,
                    fields=f,
                )
            )

        pl = m.po_line
        factor: Decimal | None = _uom_factor(ln, pl, ctx) if pl else Decimal(1)

        # LINE_PRICE — compared per PO unit (a BOX of 10 at 500 is 50 per EA)
        if pl is None:
            out.append(skipped("LINE_PRICE", "LINE_PO_MATCH", S, G, line=n))
        elif ln.unit_price_minor is None:
            out.append(skipped("LINE_PRICE", f"line {n} unit_price", S, G, line=n))
        elif factor is None:
            out.append(skipped("LINE_PRICE", "LINE_UOM", S, G, line=n))
        else:
            per_po_unit = Decimal(ln.unit_price_minor) / factor
            signed = (per_po_unit - pl.price_minor) / Decimal(pl.price_minor)
            conv = f" ({fmt(round_minor(per_po_unit), c)}/{pl.uom})" if factor != 1 else ""
            out.append(
                result(
                    "LINE_PRICE",
                    abs(signed) <= cfg.price_tolerance,
                    S,
                    G,
                    f"Unit price {fmt(ln.unit_price_minor, c)}{conv} vs PO "
                    f"{fmt(pl.price_minor, c)} ({'+' if signed > 0 else ''}{signed:.1%})",
                    expected=pl.price_minor,
                    actual=round_minor(per_po_unit),
                    line=n,
                    fields=(f"line:{n}:unit_price",),
                )
            )

        # LINE_UOM + LINE_QTY
        if pl is None:
            out.append(skipped("LINE_UOM", "LINE_PO_MATCH", S, G, line=n))
        else:
            out.append(
                result(
                    "LINE_UOM",
                    factor is not None,
                    S,
                    G,
                    f"Unit {ln.uom or pl.uom} matches PO"
                    if (ln.uom or pl.uom) == pl.uom
                    else (
                        f"{ln.uom} → {pl.uom} via item master"
                        if factor is not None
                        else f"Unit {ln.uom} vs PO {pl.uom}: no conversion"
                    ),
                    expected=pl.uom,
                    actual=ln.uom or pl.uom,
                    line=n,
                    fields=(f"line:{n}:uom",),
                )
            )
        if pl is None:
            out.append(skipped("LINE_QTY", "LINE_PO_MATCH", S, G, line=n))
        elif ln.qty is None:
            out.append(skipped("LINE_QTY", f"line {n} qty", S, G, line=n))
        elif factor is None:
            out.append(skipped("LINE_QTY", "LINE_UOM", S, G, line=n))
        else:
            qty = ln.qty * factor
            out.append(
                result(
                    "LINE_QTY",
                    qty <= pl.open_qty,
                    S,
                    G,
                    f"Qty {_q(qty)} / open {_q(pl.open_qty)}",
                    expected=pl.open_qty,
                    actual=qty,
                    line=n,
                    fields=(f"line:{n}:qty",),
                )
            )

    for po_line_id, nos in matched_po_lines.items():
        if len(nos) > 1:
            out.append(
                result(
                    "LINE_REPEAT",
                    False,
                    S,
                    G,
                    f"Lines {', '.join(map(str, nos))} bill the same PO line",
                    severity=Severity.WARN,
                    fail_status=Status.WARN,
                    actual=po_line_id,
                    line=nos[-1],
                )
            )
    return out


def _q(d: Decimal) -> str:
    return f"{d.normalize():f}"


def _uom_factor(ln: Line, pl: POLine, ctx: Context) -> Decimal | None:
    """Factor converting invoice qty into PO uom; None when no conversion exists."""
    if not ln.uom or ln.uom == pl.uom:
        return Decimal(1)
    item = ctx.items.get(pl.item_code.upper())
    if item is None:
        return None

    def to_base(u: str) -> Decimal | None:
        return Decimal(1) if u == item.uom else item.conversions.get(u)

    a, b = to_base(ln.uom), to_base(pl.uom)
    return a / b if a is not None and b is not None else None


DATE_RULES = (date_not_future, date_age, date_after_po, date_period_open)
PO_RULES = (currency_match, total_vs_po)
