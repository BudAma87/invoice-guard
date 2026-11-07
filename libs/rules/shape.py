"""Stage 1 — shape: required fields, parse and normalisation."""

from __future__ import annotations

from libs.rules._base import result
from libs.rules.config import RuleSetConfig
from libs.rules.types import Context, Group, Invoice, RuleResult, Stage

S = Stage.SHAPE


def date_parse(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    if inv.invoice_date is None:
        return result(
            "DATE_PARSE",
            False,
            S,
            Group.DATE,
            "Invoice date missing or unreadable",
            fields=("invoice_date",),
        )
    if inv.date_ambiguous:
        return result(
            "DATE_PARSE",
            False,
            S,
            Group.DATE,
            "Day/month order is ambiguous and the supplier's locale is unknown",
            actual=inv.invoice_date,
            fields=("invoice_date",),
        )
    return result("DATE_PARSE", True, S, Group.DATE, "Date parsed", actual=inv.invoice_date)


def lines_present(inv: Invoice, ctx: Context, cfg: RuleSetConfig) -> RuleResult:
    n = len(inv.lines)
    return result(
        "LINES_PRESENT",
        n > 0,
        S,
        Group.LINES,
        f"{n} line item{'s' if n != 1 else ''} extracted" if n else "No line items extracted",
        actual=n,
    )


RULES = (date_parse, lines_present)
