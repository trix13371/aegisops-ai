"""Closed local summaries; no paths, source text, findings or command output."""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from aegisops.domain.common import DTO, Sha256, UTCDateTime
from aegisops.project_runner.models import (
    CompatibilityIssue,
    Environment,
    Profile,
    ToolName,
    profile_for,
    tools_for,
)
from aegisops.projects.models import FindingCode

ReviewStatus = Literal["attention", "incomplete", "no_issues_reported"]
Limitation = Literal[
    "unverified_source_match",
    "missing_static",
    "missing_runner",
    "pending_runner",
    "partial_static",
    "unconfirmed_runner",
    "truncated_output",
    "selected_profile_only",
]
RunState = Literal["pending", "passed", "issues", "error", "timeout", "not_supported"]
RunReason = Literal[
    "checks_completed",
    "runner_unavailable",
    "snapshot_rejected",
    "project_incompatible",
    "sandbox_failed",
    "deadline",
    "uncertain",
]


class CategoryReview(DTO):
    code: FindingCode
    severity: Literal["error", "warning"]
    count: int = Field(strict=True, ge=1, le=200)


class StaticReview(DTO):
    status: Literal["passed", "issues", "partial"]
    rules_version: Literal["local-static-v1", "local-static-v2"]
    files_checked: int = Field(strict=True, ge=0, le=1000)
    skipped: int = Field(strict=True, ge=0, le=4000)
    partial: StrictBool
    categories: tuple[CategoryReview, ...] = Field(max_length=26)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        keys = tuple((item.code, item.severity) for item in self.categories)
        expected = "partial" if self.partial else "issues" if keys else "passed"
        if (
            keys != tuple(sorted(set(keys)))
            or sum(item.count for item in self.categories) > 200
            or self.status != expected
            or (not self.partial and self.files_checked == 0)
        ):
            raise ValueError("inconsistent static summary")
        return self


class CheckReview(DTO):
    tool: ToolName
    status: Literal["passed", "failed", "error", "timeout", "no_tests"]
    output_truncated: StrictBool

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.status == "passed" and self.output_truncated:
            raise ValueError("inconsistent check summary")
        if self.status == "no_tests" and self.tool not in {"pytest", "node-test", "vitest"}:
            raise ValueError("invalid no-tests tool")
        return self


class RunnerReview(DTO):
    state: RunState
    profile: Profile
    environment: Environment
    reason: RunReason | None
    execution_scope: Literal["not_started", "container"]
    container_removed: StrictBool
    snapshot_sha256: Sha256 | None
    checks: tuple[CheckReview, ...] = Field(max_length=2)
    compatibility_issues: tuple[CompatibilityIssue, ...] = Field(max_length=4)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.profile != profile_for(self.environment):
            raise ValueError("profile does not match environment")
        if self.state == "pending":
            if (
                self.reason is not None
                or self.execution_scope != "not_started"
                or self.container_removed
                or self.snapshot_sha256 is not None
                or self.checks
                or self.compatibility_issues
            ):
                raise ValueError("inconsistent pending summary")
            return self
        if self.reason is None:
            raise ValueError("completed summary needs a reason")
        if self.compatibility_issues and (
            self.state != "not_supported"
            or self.reason != "project_incompatible"
            or self.execution_scope != "not_started"
            or self.checks
        ):
            raise ValueError("inconsistent compatibility summary")
        if len(set(self.compatibility_issues)) != len(self.compatibility_issues):
            raise ValueError("duplicate compatibility issue")
        if self.checks:
            expected = (
                "timeout"
                if any(item.status == "timeout" for item in self.checks)
                else "error"
                if any(item.status == "error" for item in self.checks)
                else "issues"
                if any(item.status in {"failed", "no_tests"} for item in self.checks)
                else "passed"
            )
            if (
                tuple(item.tool for item in self.checks) != tools_for(self.profile)
                or self.execution_scope != "container"
                or (
                    self.state != expected
                    and not (self.state == "error" and not self.container_removed)
                )
            ):
                raise ValueError("inconsistent checks summary")
        if self.state in {"passed", "issues"} and not runner_confirmed(self):
            raise ValueError("unconfirmed completed summary")
        return self


def runner_confirmed(runner: RunnerReview) -> bool:
    return (
        runner.state in {"passed", "issues"}
        and runner.reason == "checks_completed"
        and runner.execution_scope == "container"
        and runner.container_removed
        and runner.snapshot_sha256 is not None
        and len(runner.checks) == 2
    )


def review_limitations(
    static: StaticReview | None, runner: RunnerReview | None
) -> tuple[Limitation, ...]:
    # Static reports do not record a source snapshot hash, even when both sources exist.
    limitations: list[Limitation] = ["unverified_source_match"]
    if static is None:
        limitations.append("missing_static")
    elif static.partial:
        limitations.append("partial_static")
    if runner is None:
        limitations.append("missing_runner")
    else:
        if runner.state == "pending":
            limitations.append("pending_runner")
        elif not runner_confirmed(runner):
            limitations.append("unconfirmed_runner")
        if any(item.output_truncated for item in runner.checks):
            limitations.append("truncated_output")
        limitations.append("selected_profile_only")
    return tuple(limitations)


def review_status(
    static: StaticReview | None, runner: RunnerReview | None, limitations: tuple[Limitation, ...]
) -> ReviewStatus:
    if (static is not None and static.categories) or (
        runner is not None and runner.state == "issues" and runner_confirmed(runner)
    ):
        return "attention"
    if any(
        item not in {"unverified_source_match", "selected_profile_only"} for item in limitations
    ):
        return "incomplete"
    return "no_issues_reported"


class Review(DTO):
    id: UUID
    project_id: UUID
    created_at: UTCDateTime
    static_report_id: UUID | None
    run_id: UUID | None
    static_report_sha256: Sha256 | None
    run_claim_sha256: Sha256 | None
    run_result_sha256: Sha256 | None
    static: StaticReview | None
    runner: RunnerReview | None
    status: ReviewStatus
    limitations: tuple[Limitation, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.static is None and self.runner is None:
            raise ValueError("at least one evidence source required")
        if (self.static is not None) != (self.static_report_id is not None) or (
            (self.static is not None) != (self.static_report_sha256 is not None)
        ):
            raise ValueError("inconsistent static identity")
        if (self.runner is not None) != (self.run_id is not None) or (
            (self.runner is not None) != (self.run_claim_sha256 is not None)
        ):
            raise ValueError("inconsistent run identity")
        completed = self.runner is not None and self.runner.state != "pending"
        if completed != (self.run_result_sha256 is not None):
            raise ValueError("inconsistent result identity")
        expected = review_limitations(self.static, self.runner)
        if self.limitations != expected or self.status != review_status(
            self.static, self.runner, expected
        ):
            raise ValueError("inconsistent review conclusion")
        return self
