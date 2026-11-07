"""Routing, approval gate, maker-checker and queue priority (TSD §5–8)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from libs.rules.config import (
    REQUIRED_HEADER_FIELDS,
    REQUIRED_LINE_FIELDS,
    RuleSetConfig,
    reason_valid,
)
from libs.rules.types import Invoice, RuleResult, Status


class RouteKind(StrEnum):
    AUTO_APPROVE = "AUTO_APPROVE"
    REVIEW = "REVIEW"
    BLOCKED = "BLOCKED"  # in review, but cannot be approved


@dataclass(frozen=True)
class Route:
    kind: RouteKind
    reasons: tuple[str, ...] = ()


def low_confidence_fields(inv: Invoice, cfg: RuleSetConfig) -> list[str]:
    """Required fields below the threshold. A missing score counts as 0."""
    keys = list(REQUIRED_HEADER_FIELDS)
    for ln in inv.lines:
        keys += [f"line:{ln.line_no}:{f}" for f in REQUIRED_LINE_FIELDS]
    return [k for k in keys if inv.confidence.get(k, 0.0) < cfg.min_confidence]


def decide(
    inv: Invoice,
    results: Iterable[RuleResult],
    cfg: RuleSetConfig,
    *,
    amount_lkr_minor: int | None = None,
    duplicate_suspected: bool = False,
) -> Route:
    results = list(results)
    reasons = [r.code for r in results if r.status in (Status.FAIL, Status.SKIPPED)]
    reasons += [f"LOW_CONF:{f}" for f in low_confidence_fields(inv, cfg)]
    if duplicate_suspected and "DUP_EXACT" not in reasons:
        reasons.append("DUP_EXACT")
    if (
        cfg.auto_approve_ceiling_lkr_minor is not None
        and amount_lkr_minor is not None
        and amount_lkr_minor >= cfg.auto_approve_ceiling_lkr_minor
    ):
        reasons.append("AMOUNT_CEILING")
    reasons = list(dict.fromkeys(reasons))  # dedupe, keep order
    if any(r.is_blocker for r in results):
        return Route(RouteKind.BLOCKED, tuple(reasons))
    return Route(RouteKind.REVIEW, tuple(reasons)) if reasons else Route(RouteKind.AUTO_APPROVE)


@dataclass(frozen=True)
class Override:
    reason_code: str
    comment: str = ""


@dataclass(frozen=True)
class ApprovalCheck:
    allowed: bool
    problems: tuple[str, ...] = field(default_factory=tuple)


def approval_check(
    results: Iterable[dict] | Iterable[RuleResult], overrides: Mapping[str, Override]
) -> ApprovalCheck:
    """Approve is allowed only with no BLOCK, no SKIPPED, and a valid override on every FAIL.

    `overrides` is keyed by result key (e.g. "LINE_PRICE:2" or "TOTAL_RECONCILE").
    """
    problems: list[str] = []
    for r in results:
        d = r.to_dict() if isinstance(r, RuleResult) else r
        status, sev, key = d["status"], d["severity"], d["key"]
        if status == "FAIL" and sev == "BLOCK":
            problems.append(f"{key}: blocking rule — cannot be approved")
        elif status == "SKIPPED":
            problems.append(f"{key}: not checked ({d.get('needs') or 'missing input'})")
        elif status == "FAIL":
            o = overrides.get(key)
            if o is None:
                problems.append(f"{key}: fix or override with a reason code")
            elif not reason_valid(o.reason_code, o.comment):
                problems.append(f"{key}: invalid reason code or comment too short")
    return ApprovalCheck(not problems, tuple(problems))


def needs_checker(amount_lkr_minor: int, cfg: RuleSetConfig) -> bool:
    """Manual approval at or above LKR 1M equivalent needs a second approver."""
    return amount_lkr_minor >= cfg.maker_checker_lkr_minor


def priority(
    *, blocker: bool, sla_used_ratio: float, amount_lkr_minor: int, duplicate_suspicion: float
) -> float:
    """40·blocker + 25·SLA used/SLA + 20·min(1, amount_LKR/1e7) + 15·duplicate suspicion."""
    amount_lkr = amount_lkr_minor / 100
    return round(
        40 * (1 if blocker else 0)
        + 25 * max(0.0, sla_used_ratio)
        + 20 * min(1.0, amount_lkr / 1e7)
        + 15 * max(0.0, min(1.0, duplicate_suspicion)),
        2,
    )
