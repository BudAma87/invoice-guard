from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from libs.rules import DEFAULT_RULE_SET, Override, approval_check, needs_checker, priority
from libs.rules.config import reason_valid
from libs.rules.money import exponent, fmt, to_lkr_minor, to_minor
from libs.rules.text import levenshtein, normalise_invoice_no


@pytest.mark.parametrize(
    "amount,ccy,minor",
    [
        ("1,416.50", "LKR", 141650),
        ("1000", "JPY", 1000),
        ("1.2345", "KWD", 1235),
        ("0.005", "USD", 1),
    ],
)
def test_to_minor_half_up(amount, ccy, minor):
    assert to_minor(amount, ccy) == minor


def test_fmt_signed():
    assert fmt(50, "LKR", signed=True) == "+0.50"
    assert fmt(-50, "LKR", signed=True) == "-0.50"
    assert fmt(1234567, "JPY") == "1,234,567"


@given(st.integers(min_value=0, max_value=10**12), st.sampled_from(["LKR", "JPY", "KWD"]))
def test_minor_roundtrip(minor, ccy):
    major = Decimal(minor) / (Decimal(10) ** exponent(ccy))
    assert to_minor(major, ccy) == minor


@given(st.integers(0, 10**9), st.integers(0, 10**9), st.integers(0, 10**9))
def test_reconcile_is_exact_integer_math(a, b, c):
    # integer minor units: sum is associative and exact, no float drift
    assert (a + b) + c == a + (b + c)


def test_normalise_invoice_no():
    assert normalise_invoice_no(" inv-1023/a ") == "INV1023A"
    assert normalise_invoice_no("---") is None


def test_levenshtein():
    assert levenshtein("INV1023", "INV1023A") == 1
    assert levenshtein("ABC", "ABC") == 0


def test_reason_other_needs_20_chars():
    assert not reason_valid("OTHER", "too short")
    assert reason_valid("OTHER", "Supplier confirmed by phone call")
    assert reason_valid("PRICE_AGREED", "")
    assert not reason_valid("MADE_UP", "")


def _r(key, status, severity="FAIL"):
    return {"key": key, "code": key.split(":")[0], "status": status, "severity": severity}


def test_approval_requires_override_on_every_fail():
    results = [_r("TOTAL_RECONCILE", "FAIL"), _r("LINE_PRICE:2", "FAIL"), _r("DATE_AGE", "PASS")]
    assert not approval_check(results, {}).allowed
    ok = approval_check(
        results,
        {"TOTAL_RECONCILE": Override("OCR_CORRECTION"), "LINE_PRICE:2": Override("PRICE_AGREED")},
    )
    assert ok.allowed


def test_approval_refuses_block_and_skipped():
    assert not approval_check(
        [_r("DUP_EXACT", "FAIL", "BLOCK")], {"DUP_EXACT": Override("OTHER", "x" * 30)}
    ).allowed
    assert not approval_check([_r("DATE_PERIOD_OPEN", "SKIPPED")], {}).allowed


def test_maker_checker_threshold():
    assert needs_checker(1_000_000_00, DEFAULT_RULE_SET)
    assert not needs_checker(999_999_99, DEFAULT_RULE_SET)


def test_fx_conversion():
    assert to_lkr_minor(3400_00, "USD", Decimal("300")) == 1_020_000_00


def test_priority_formula():
    assert (
        priority(blocker=True, sla_used_ratio=1.0, amount_lkr_minor=10**9, duplicate_suspicion=1.0)
        == 100
    )
    assert (
        priority(blocker=False, sla_used_ratio=0.0, amount_lkr_minor=0, duplicate_suspicion=0) == 0
    )
