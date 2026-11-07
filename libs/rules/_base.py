from __future__ import annotations

from typing import Any

from libs.rules.types import Group, RuleResult, Severity, Stage, Status


def result(
    code: str,
    ok: bool,
    stage: Stage,
    group: Group,
    message: str,
    *,
    severity: Severity = Severity.FAIL,
    fail_status: Status = Status.FAIL,
    **kw: Any,
) -> RuleResult:
    return RuleResult(
        code=code,
        status=Status.PASS if ok else fail_status,
        severity=severity,
        stage=stage,
        group=group,
        message=message,
        **kw,
    )


def skipped(
    code: str,
    needs: str,
    stage: Stage,
    group: Group,
    message: str | None = None,
    *,
    severity: Severity = Severity.FAIL,
    **kw: Any,
) -> RuleResult:
    return RuleResult(
        code=code,
        status=Status.SKIPPED,
        severity=severity,
        stage=stage,
        group=group,
        message=message or f"Not checked: needs {needs}",
        needs=needs,
        **kw,
    )
