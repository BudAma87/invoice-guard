"""evaluate(invoice, context, rule_set) -> [RuleResult]. Pure: no I/O, deterministic.

Called by the pipeline worker and by the review API's /validate, so a reviewer sees
exactly the rules the agent ran. Every rule in every stage runs; nothing short-circuits.
"""

from __future__ import annotations

from libs.rules import arithmetic, identity, reference, shape
from libs.rules.config import DEFAULT_RULE_SET, RuleSetConfig
from libs.rules.types import Context, Invoice, RuleResult


def evaluate(
    invoice: Invoice, context: Context, rule_set: RuleSetConfig = DEFAULT_RULE_SET
) -> list[RuleResult]:
    out: list[RuleResult] = []
    # 1. shape
    out += [r(invoice, context, rule_set) for r in shape.RULES]
    # 2. identity
    out += [r(invoice, context, rule_set) for r in identity.RULES]
    # 3. arithmetic
    for per_line in arithmetic.PER_LINE:
        out += per_line(invoice, context, rule_set)
    out += [r(invoice, context, rule_set) for r in arithmetic.SINGLE]
    # 4. reference
    out += [r(invoice, context, rule_set) for r in reference.DATE_RULES]
    out += [r(invoice, context, rule_set) for r in reference.PO_RULES]
    out += reference.line_rules(invoice, context, rule_set)
    return out


def diff_results(before: list[dict], after: list[dict]) -> list[dict]:
    """Per-rule status change between two evaluations (dict form), for the UI."""
    prev = {r["key"]: r["status"] for r in before}
    changes = []
    for r in after:
        old = prev.get(r["key"])
        if old != r["status"]:
            changes.append({"key": r["key"], "code": r["code"], "from": old, "to": r["status"]})
    for key in prev.keys() - {r["key"] for r in after}:
        changes.append({"key": key, "code": key.split(":")[0], "from": prev[key], "to": None})
    return changes
