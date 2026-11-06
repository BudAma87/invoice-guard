"""Deterministic rule engine: evaluate(invoice, context) -> list[RuleResult]. No I/O, no LLM.

Stages: shape -> identity -> arithmetic -> reference. See TSD section 5-6.
"""

from invoice_guard.graph.state import RuleResult


def evaluate(invoice: dict, context: dict) -> list[RuleResult]:
    # TODO: DATE_*, TOTAL_*, LINE_*, SUPPLIER_*, DUP_* rules
    return []


def total_reconcile(subtotal: int, tax: int, charges: int, total: int, tol: int = 1) -> RuleResult:
    """All amounts in minor units (integers)."""
    computed = subtotal + tax + charges
    diff = abs(computed - total)
    return {
        "code": "TOTAL_RECONCILE",
        "status": "PASS" if diff <= tol else "FAIL",
        "severity": "FAIL",
        "message": f"computed={computed} stated={total} diff={diff}",
    }
