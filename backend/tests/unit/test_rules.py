from invoice_guard.graph import routing
from invoice_guard.rules.engine import total_reconcile


def test_total_reconcile_within_tolerance():
    assert total_reconcile(120000, 21600, 0, 141601)["status"] == "PASS"


def test_total_reconcile_fails_outside_tolerance():
    assert total_reconcile(120000, 21600, 0, 141650)["status"] == "FAIL"


def test_review_above_threshold_goes_to_checker():
    state = {"review_decision": {"action": "approve", "amount_lkr": 1_500_000}}
    assert routing.after_review(state) == "checker"
