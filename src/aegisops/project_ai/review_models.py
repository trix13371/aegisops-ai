"""Closed source-free combined review profile, separate from static AI v1."""

from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from aegisops.domain.common import DTO
from aegisops.project_ai.models import Count, Note
from aegisops.project_runner.models import (
    CompatibilityIssue,
    Environment,
    Profile,
    ToolName,
    profile_for,
    tools_for,
)
from aegisops.projects.models import FindingCode

REVIEW_PROFILE = "project-review-summary-v1"
RunnerCode = Literal[
    "runner_failed",
    "runner_error",
    "runner_timeout",
    "runner_no_tests",
    "runner_pending",
    "runner_not_supported",
    "runner_unconfirmed",
]
ReviewCode = FindingCode | RunnerCode
ReviewLimitation = Literal[
    "unverified_source_match",
    "missing_static",
    "missing_runner",
    "pending_runner",
    "partial_static",
    "unconfirmed_runner",
    "truncated_output",
    "selected_profile_only",
]
RunReason = Literal[
    "checks_completed",
    "runner_unavailable",
    "snapshot_rejected",
    "project_incompatible",
    "sandbox_failed",
    "deadline",
    "uncertain",
]


class ReviewCategory(DTO):
    code: ReviewCode
    severity: Literal["error", "warning"]
    count: Annotated[int, Field(strict=True, ge=1, le=4000)]


class StaticSummary(DTO):
    status: Literal["passed", "issues", "partial"]
    rules_version: Literal["local-static-v1", "local-static-v2"]
    files_checked: Count
    skipped: Count
    partial: StrictBool

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.status == "partial") != self.partial:
            raise ValueError("inconsistent static summary")
        return self


class CheckSummary(DTO):
    tool: ToolName
    status: Literal["passed", "failed", "error", "timeout", "no_tests"]
    output_truncated: StrictBool


class RunnerSummary(DTO):
    profile: Profile
    environment: Environment
    state: Literal["pending", "passed", "issues", "error", "timeout", "not_supported"]
    reason: RunReason | None
    checks: tuple[CheckSummary, ...] = Field(max_length=2)
    compatibility_issues: tuple[CompatibilityIssue, ...] = Field(max_length=4)
    execution_scope: Literal["not_started", "container"]
    container_removed: StrictBool

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.profile != profile_for(self.environment):
            raise ValueError("inconsistent runner profile")
        if self.checks and tuple(item.tool for item in self.checks) != tools_for(self.profile):
            raise ValueError("inconsistent tool profile")
        if self.state == "pending" and (
            self.reason is not None
            or self.checks
            or self.execution_scope != "not_started"
            or self.container_removed
        ):
            raise ValueError("inconsistent pending runner")
        if self.state != "pending" and self.reason is None:
            raise ValueError("missing runner reason")
        if len(set(self.compatibility_issues)) != len(self.compatibility_issues):
            raise ValueError("duplicate compatibility issue")
        return self


class ReviewSummary(DTO):
    profile: Literal["project-review-summary-v1"] = "project-review-summary-v1"
    status: Literal["attention", "incomplete", "no_issues_reported"]
    source_match: Literal["unverified"] = "unverified"
    categories: tuple[ReviewCategory, ...] = Field(max_length=35)
    static: StaticSummary | None
    runner: RunnerSummary | None
    limitations: tuple[ReviewLimitation, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.static is None and self.runner is None:
            raise ValueError("empty review")
        if len({(item.code, item.severity) for item in self.categories}) != len(self.categories):
            raise ValueError("duplicate review category")
        if len(set(self.limitations)) != len(self.limitations):
            raise ValueError("duplicate review limitation")
        if "unverified_source_match" not in self.limitations:
            raise ValueError("missing source relationship limitation")
        return self


class ReviewAdvice(DTO):
    code: ReviewCode
    priority: Literal["high", "medium", "low"]
    explanation: Note
    next_step: Note


class ReviewExplanation(DTO):
    summary: Note
    actions: tuple[ReviewAdvice, ...] = Field(max_length=5)
    limitations: Note

    @model_validator(mode="after")
    def unique_codes(self) -> Self:
        if len({item.code for item in self.actions}) != len(self.actions):
            raise ValueError("duplicate advice")
        return self
