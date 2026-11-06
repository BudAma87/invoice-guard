"""Conditional edges. Pure functions of state: easy to unit-test."""

from invoice_guard.config import settings
from invoice_guard.graph.state import InvoiceState


def after_template(state: InvoiceState) -> str:
    return "normalise" if state.get("template_hit") else "extract"


def after_validate(state: InvoiceState) -> str:
    fails = [r for r in state.get("rule_results", []) if r["status"] == "FAIL"]
    if fails and state.get("repair_attempts", 0) < 1:
        return "repair"
    return "dedupe"


def after_decide(state: InvoiceState) -> str:
    return {"auto_approve": "post_erp", "human_review": "human_review", "blocked": "human_review"}[
        state.get("route", "human_review")
    ]


def after_review(state: InvoiceState) -> str:
    decision = state.get("review_decision") or {}
    if decision.get("action") != "approve":
        return "close_rejected"
    amount = decision.get("amount_lkr", 0)
    return "checker" if amount >= settings.maker_checker_threshold_lkr else "post_erp"


def after_checker(state: InvoiceState) -> str:
    decision = state.get("checker_decision") or {}
    return "post_erp" if decision.get("action") == "approve" else "human_review"
