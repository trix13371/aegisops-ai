"""Status-only comparison: never infer individual test fixes by parsing raw output."""

from typing import Literal
from uuid import UUID

from aegisops.domain.common import DTO
from aegisops.project_runner.models import RunClaim, RunResult, ToolCheck, ToolName
from aegisops.projects.models import ProjectError


class ToolTransition(DTO):
    tool: ToolName
    before: Literal["passed", "failed", "error", "timeout", "no_tests"]
    after: Literal["passed", "failed", "error", "timeout", "no_tests"]
    change: Literal["unchanged", "now_passed", "no_longer_passed", "changed"]


class RunComparison(DTO):
    project_id: UUID
    base_id: UUID
    target_id: UUID
    comparable: bool
    reason: Literal[
        "same_environment", "unfinished", "unconfirmed", "image_changed", "environment_changed"
    ]
    same_snapshot: bool | None = None
    transitions: tuple[ToolTransition, ...] = ()


def transition(before: ToolCheck, after: ToolCheck) -> ToolTransition:
    change: Literal["unchanged", "now_passed", "no_longer_passed", "changed"] = (
        "unchanged"
        if before.status == after.status
        else "now_passed"
        if after.status == "passed"
        else "no_longer_passed"
        if before.status == "passed"
        else "changed"
    )
    return ToolTransition(tool=before.tool, before=before.status, after=after.status, change=change)


def compare_runs(
    base: RunClaim, target: RunClaim, before: RunResult | None, after: RunResult | None
) -> RunComparison:
    base, target = RunClaim.model_validate(base), RunClaim.model_validate(target)
    if base.project_id != target.project_id or base.id == target.id:
        raise ProjectError("Выберите два разных запуска одного проекта")
    for claim, result in ((base, before), (target, after)):
        if result is not None:
            result = RunResult.model_validate(result)
            if (
                result.id != claim.id
                or result.project_id != claim.project_id
                or result.environment != claim.environment
                or result.profile != claim.profile
            ):
                raise ProjectError("Результат не соответствует запуску")
    reason: Literal[
        "same_environment", "unfinished", "unconfirmed", "image_changed", "environment_changed"
    ]
    if before is None or after is None:
        reason = "unfinished"
    elif any(
        not item.container_removed
        or item.reason != "checks_completed"
        or item.snapshot is None
        or item.image_id is None
        or len(item.checks) != 2
        or any(check.output_truncated for check in item.checks)
        for item in (before, after)
    ):
        reason = "unconfirmed"
    elif base.environment != target.environment:
        reason = "environment_changed"
    elif before.image_id != after.image_id:
        reason = "image_changed"
    else:
        reason = "same_environment"
    transitions: tuple[ToolTransition, ...] = ()
    same_snapshot = None
    if reason == "same_environment" and before is not None and after is not None:
        transitions = tuple(
            transition(left, right) for left, right in zip(before.checks, after.checks, strict=True)
        )
        if before.snapshot is not None and after.snapshot is not None:
            same_snapshot = before.snapshot.sha256 == after.snapshot.sha256
    return RunComparison(
        project_id=base.project_id,
        base_id=base.id,
        target_id=target.id,
        comparable=reason == "same_environment",
        reason=reason,
        same_snapshot=same_snapshot,
        transitions=transitions,
    )
