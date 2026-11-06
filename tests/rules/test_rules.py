from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from libs.rules import (
    DEFAULT_RULE_SET,
    DupCandidate,
    Line,
    PeriodStatus,
    RouteKind,
    Status,
    TaxLine,
    Vendor,
    decide,
    evaluate,
)
from tests.rules.factory import TODAY, context, invoice


def by_key(results):
    return {r.key: r for r in results}


def status(inv, ctx, key, cfg=DEFAULT_RULE_SET):
    return by_key(evaluate(inv, ctx, cfg))[key].status


def test_clean_invoice_auto_approves():
    inv, ctx = invoice(), context()
    results = evaluate(inv, ctx)
    bad = [r for r in results if r.status != Status.PASS]
    assert bad == []
    assert decide(inv, results, DEFAULT_RULE_SET).kind == RouteKind.AUTO_APPROVE


def test_every_stage_runs_no_short_circuit():
    inv = invoice(invoice_date=None, lines=())
    results = evaluate(inv, context())
    stages = {r.stage.value for r in results}
    assert stages == {"shape", "identity", "arithmetic", "reference"}
    assert by_key(results)["DATE_AGE"].status == Status.SKIPPED
    assert by_key(results)["DATE_AGE"].needs == "DATE_PARSE"


# ---------------------------------------------------------------- dates


@pytest.mark.parametrize("age,expected", [(90, Status.PASS), (91, Status.FAIL)])
def test_date_age_boundary(age, expected):
    inv = invoice(invoice_date=TODAY - timedelta(days=age))
    ctx = context(po=replace(context().po, approved_on=date(2026, 1, 1)))
    assert status(inv, ctx, "DATE_AGE") == expected


@pytest.mark.parametrize("ahead,expected", [(1, Status.PASS), (2, Status.FAIL)])
def test_date_not_future_with_one_day_grace(ahead, expected):
    assert (
        status(invoice(invoice_date=TODAY + timedelta(days=ahead)), context(), "DATE_NOT_FUTURE")
        == expected
    )


def test_date_before_po_fails():
    assert status(invoice(invoice_date=date(2026, 9, 1)), context(), "DATE_AFTER_PO") == "FAIL"


def test_date_ambiguous_fails_parse():
    assert status(invoice(date_ambiguous=True), context(), "DATE_PARSE") == Status.FAIL


def test_closed_period_names_first_open():
    ctx = context(period=PeriodStatus("2026-10", False, "2026-11"))
    r = by_key(evaluate(invoice(), ctx))["DATE_PERIOD_OPEN"]
    assert r.status == Status.FAIL and "2026-11" in r.message


def test_erp_down_skips_reference_rules():
    results = by_key(evaluate(invoice(), context(erp_available=False)))
    assert results["DATE_PERIOD_OPEN"].status == Status.SKIPPED
    assert results["DATE_PERIOD_OPEN"].message == "ERP unavailable"
    assert results["SUPPLIER_RESOLVE"].status == Status.SKIPPED


# ---------------------------------------------------------------- totals


@pytest.mark.parametrize("delta,expected", [(1, Status.PASS), (-1, Status.PASS), (2, Status.FAIL)])
def test_total_reconcile_one_minor_unit(delta, expected):
    assert status(invoice(total_minor=1416_00 + delta), context(), "TOTAL_RECONCILE") == expected


def test_total_reconcile_message_signed_diff():
    r = by_key(evaluate(invoice(total_minor=1416_50), context()))["TOTAL_RECONCILE"]
    assert r.status == Status.FAIL
    assert "diff +0.50" in r.message
    assert r.expected == 1416_00 and r.actual == 1416_50


def test_total_reconcile_per_currency_tolerance():
    cfg = replace(DEFAULT_RULE_SET, total_tolerance_minor_by_ccy={"LKR": 100})
    assert status(invoice(total_minor=1416_50), context(), "TOTAL_RECONCILE", cfg) == "PASS"


def test_subtotal_mismatch():
    assert status(invoice(subtotal_minor=1199_00), context(), "SUBTOTAL_SUM") == Status.FAIL


def test_line_arith_tolerates_rounding():
    ln = Line(1, "Steel bolt M8 x 40", Decimal("3"), 33_33, 100_00, "A-100")
    inv = invoice(lines=(ln,), subtotal_minor=None, tax_minor=18_00, total_minor=118_00)
    assert status(inv, context(), "LINE_ARITH:1") == Status.PASS  # 99.99 vs 100.00


def test_tax_rate_not_allowed():
    inv = invoice(
        tax_lines=(TaxLine(Decimal("0.15"), 1200_00, 180_00),),
        tax_minor=180_00,
        total_minor=1380_00,
    )
    assert status(inv, context(), "TAX_CHECK") == Status.FAIL


def test_tax_inferred_from_allowed_rate():
    assert status(invoice(), context(), "TAX_CHECK") == Status.PASS


def test_total_not_positive():
    assert status(invoice(total_minor=0), context(), "TOTAL_POSITIVE") == Status.FAIL


def test_total_vs_po_balance():
    ctx = context(po=replace(context().po, remaining_minor=1000_00))
    assert status(invoice(), ctx, "TOTAL_VS_PO") == Status.FAIL


def test_currency_mismatch():
    assert status(invoice(currency="USD"), context(), "CURRENCY_MATCH") == Status.FAIL


# ---------------------------------------------------------------- lines


@pytest.mark.parametrize("price,expected", [(51_00, Status.PASS), (51_01, Status.FAIL)])
def test_line_price_two_percent_boundary(price, expected):
    # PO price 50.00: 51.00 is exactly 2.0 %, 51.01 is 2.02 %
    ln = Line(1, "Steel bolt M8 x 40", Decimal(10), price, price * 10, "A-100")
    assert status(invoice(lines=(ln,)), context(), "LINE_PRICE:1") == expected


def test_line_qty_exceeds_open():
    ln = Line(1, "Steel bolt", Decimal(11), 50_00, 550_00, "A-100")
    assert status(invoice(lines=(ln,)), context(), "LINE_QTY:1") == Status.FAIL


def test_line_match_by_description_when_no_code():
    ln = Line(1, "Flat washer M8", Decimal(10), 10_00, 100_00, None)
    r = by_key(evaluate(invoice(lines=(ln,)), context()))
    assert r["LINE_PO_MATCH:1"].status == Status.PASS
    assert r["LINE_ITEM_MASTER:1"].status == Status.PASS  # code taken from the PO line


def test_unmatched_line_skips_dependents():
    ln = Line(1, "Consulting services", Decimal(1), 100_00, 100_00, None)
    r = by_key(evaluate(invoice(lines=(ln,)), context()))
    assert r["LINE_PO_MATCH:1"].status == Status.FAIL
    assert r["LINE_PRICE:1"].status == Status.SKIPPED
    assert r["LINE_PRICE:1"].needs == "LINE_PO_MATCH"


def test_reviewer_forced_po_line():
    ln = Line(1, "Consulting services", Decimal(1), 10_00, 10_00, None, po_line_id="pl-3")
    assert status(invoice(lines=(ln,)), context(), "LINE_PO_MATCH:1") == Status.PASS


def test_uom_conversion_box_to_each():
    ln = Line(1, "Steel bolt", Decimal(1), 500_00, 500_00, "A-100", "BOX")
    r = by_key(evaluate(invoice(lines=(ln,)), context()))
    assert r["LINE_UOM:1"].status == Status.PASS
    assert r["LINE_QTY:1"].status == Status.PASS  # 1 BOX = 10 EA = open qty


def test_line_repeat_is_warn():
    a = Line(1, "Steel bolt", Decimal(5), 50_00, 250_00, "A-100")
    b = Line(2, "Steel bolt", Decimal(5), 50_00, 250_00, "A-100")
    rs = [r for r in evaluate(invoice(lines=(a, b)), context()) if r.code == "LINE_REPEAT"]
    assert len(rs) == 1 and rs[0].status == Status.WARN


# ---------------------------------------------------------------- identity


def test_inactive_supplier_blocks():
    ctx = context(vendor_matches=(Vendor("S-A", "Supplier A", "TX-1", blocked=True),))
    results = evaluate(invoice(), ctx)
    assert by_key(results)["SUPPLIER_INACTIVE"].is_blocker
    assert decide(invoice(), results, DEFAULT_RULE_SET).kind == RouteKind.BLOCKED


def test_supplier_ambiguous():
    v = context().vendor_matches[0]
    assert status(invoice(), context(vendor_matches=(v, v)), "SUPPLIER_RESOLVE") == "FAIL"


def test_dup_exact_blocks():
    d = DupCandidate("inv-0", "S-A", "INV10234", date(2026, 10, 3), 1416_00, "LKR", "POSTED")
    r = by_key(evaluate(invoice(), context(exact_duplicates=(d,))))["DUP_EXACT"]
    assert r.is_blocker


def test_dup_fuzzy_edit_distance():
    d = DupCandidate("inv-0", "S-A", "INV1023A", date(2026, 10, 1), 1416_00, "LKR", "POSTED")
    inv = invoice(invoice_no="INV1023")
    assert status(inv, context(fuzzy_candidates=(d,)), "DUP_FUZZY") == Status.FAIL
    far = replace(d, invoice_date=date(2026, 8, 1))
    assert status(inv, context(fuzzy_candidates=(far,)), "DUP_FUZZY") == Status.PASS


def test_po_other_entity():
    ctx = context(po=replace(context().po, entity_id="E02"))
    assert status(invoice(), ctx, "PO_OWNERSHIP") == Status.FAIL


# ---------------------------------------------------------------- routing


def test_low_confidence_routes_to_review():
    inv = invoice()
    inv = replace(inv, confidence={**inv.confidence, "invoice_date": 0.71})
    route = decide(inv, evaluate(inv, context()), DEFAULT_RULE_SET)
    assert route.kind == RouteKind.REVIEW
    assert "LOW_CONF:invoice_date" in route.reasons


def test_auto_approve_ceiling():
    cfg = replace(DEFAULT_RULE_SET, auto_approve_ceiling_lkr_minor=250_000_00)
    inv = invoice()
    route = decide(inv, evaluate(inv, context(), cfg), cfg, amount_lkr_minor=300_000_00)
    assert route.kind == RouteKind.REVIEW and "AMOUNT_CEILING" in route.reasons
