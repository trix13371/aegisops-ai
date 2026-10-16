from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictStr, model_validator

from aegisops.domain.common import DTO, Sha256, UTCDateTime
from aegisops.project_runner.compatibility import PackageCompatibility

PROFILE: Final = "python-checks-v1"
JS_PROFILE: Final = "javascript-checks-v1"
VITEST_PROFILE: Final = "javascript-vitest-v1"
Profile = Literal["python-checks-v1", "javascript-checks-v1", "javascript-vitest-v1"]
Environment = Literal["minimal", "python-data-v1", "node-js-v1", "node-vitest-v1"]
ENVIRONMENTS = ("minimal", "python-data-v1", "node-js-v1", "node-vitest-v1")
ToolName = Literal["ruff", "pytest", "typescript", "node-test", "vitest"]
CompatibilityIssue = Literal[
    "package_unsupported", "dependencies_unavailable", "framework_mismatch", "environment_directive"
]


def profile_for(environment: Environment) -> Profile:
    if environment == "node-vitest-v1":
        return VITEST_PROFILE
    return JS_PROFILE if environment == "node-js-v1" else PROFILE


def tools_for(profile: Profile) -> tuple[ToolName, ToolName]:
    if profile == VITEST_PROFILE:
        return ("typescript", "vitest")
    return ("typescript", "node-test") if profile == JS_PROFILE else ("ruff", "pytest")


class RunClaim(DTO):
    id: UUID
    project_id: UUID
    created_at: UTCDateTime
    profile: Profile = PROFILE
    environment: Environment = "minimal"

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.profile != profile_for(self.environment):
            raise ValueError("profile does not match environment")
        return self


class SnapshotInfo(DTO):
    sha256: Sha256
    files: int = Field(strict=True, ge=1, le=1000)
    python_files: int = Field(strict=True, ge=0, le=1000)
    javascript_files: int = Field(default=0, strict=True, ge=0, le=1000)
    bytes: int = Field(strict=True, ge=0, le=8 * 1024 * 1024)
    skipped: int = Field(strict=True, ge=0, le=4000)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if not 1 <= self.python_files + self.javascript_files <= self.files:
            raise ValueError("invalid snapshot counts")
        return self


class ToolCheck(DTO):
    tool: ToolName
    status: Literal["passed", "failed", "error", "timeout", "no_tests"]
    exit_code: int | None = Field(default=None, strict=True, ge=0, le=255)
    output: Annotated[StrictStr, Field(max_length=24000)]
    output_truncated: StrictBool

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.status == "passed" and (self.exit_code != 0 or self.output_truncated):
            raise ValueError("invalid tool result")
        if self.status == "failed" and self.exit_code != 1:
            raise ValueError("invalid tool result")
        if self.status == "no_tests" and (
            self.tool not in {"pytest", "node-test", "vitest"} or self.exit_code != 5
        ):
            raise ValueError("invalid tool result")
        return self


class RunResult(DTO):
    id: UUID
    project_id: UUID
    environment: Environment = "minimal"
    profile: Profile = PROFILE
    completed_at: UTCDateTime
    status: Literal["passed", "issues", "error", "timeout", "not_supported"]
    reason: Literal[
        "checks_completed",
        "runner_unavailable",
        "snapshot_rejected",
        "project_incompatible",
        "sandbox_failed",
        "deadline",
        "uncertain",
    ]
    snapshot: SnapshotInfo | None = None
    compatibility: PackageCompatibility | None = None
    compatibility_issues: tuple[CompatibilityIssue, ...] = Field(default=(), max_length=4)
    image_id: Annotated[StrictStr, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None = None
    checks: tuple[ToolCheck, ...] = Field(default=(), max_length=2)
    container_removed: StrictBool = False
    # Project code is executed, but exclusively in the disposable container.
    execution_scope: Literal["not_started", "container"] = "not_started"

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.profile != profile_for(self.environment):
            raise ValueError("profile does not match environment")
        if self.compatibility_issues and (
            self.compatibility is None
            or self.reason != "project_incompatible"
            or self.status != "not_supported"
            or self.checks
            or self.execution_scope != "not_started"
        ):
            raise ValueError("invalid compatibility rejection")
        if self.snapshot is not None and (
            self.profile == PROFILE
            and self.snapshot.python_files == 0
            or self.profile in {JS_PROFILE, VITEST_PROFILE}
            and self.snapshot.javascript_files == 0
        ):
            raise ValueError("snapshot does not match profile")
        if self.checks:
            if tuple(item.tool for item in self.checks) != tools_for(self.profile):
                raise ValueError("invalid checks")
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
                self.status != expected
                and not (self.status == "error" and not self.container_removed)
                or self.execution_scope != "container"
            ):
                raise ValueError("invalid run result")
        if self.status in {"passed", "issues"} and (
            not self.checks
            or not self.container_removed
            or self.snapshot is None
            or self.image_id is None
            or self.reason != "checks_completed"
        ):
            raise ValueError("unconfirmed run result")
        return self


class RunIndex(DTO):
    runs: tuple[UUID, ...] = Field(default=(), max_length=20)
