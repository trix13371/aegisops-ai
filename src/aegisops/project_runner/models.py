from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictStr, model_validator

from aegisops.domain.common import DTO, Sha256, UTCDateTime

PROFILE = "python-checks-v1"


class RunClaim(DTO):
    id: UUID
    project_id: UUID
    created_at: UTCDateTime
    profile: Literal["python-checks-v1"] = "python-checks-v1"


class SnapshotInfo(DTO):
    sha256: Sha256
    files: int = Field(strict=True, ge=1, le=1000)
    python_files: int = Field(strict=True, ge=1, le=1000)
    bytes: int = Field(strict=True, ge=0, le=8 * 1024 * 1024)
    skipped: int = Field(strict=True, ge=0, le=4000)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.python_files > self.files:
            raise ValueError("invalid snapshot counts")
        return self


class ToolCheck(DTO):
    tool: Literal["ruff", "pytest"]
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
        if self.status == "no_tests" and (self.tool != "pytest" or self.exit_code != 5):
            raise ValueError("invalid tool result")
        return self


class RunResult(DTO):
    id: UUID
    project_id: UUID
    completed_at: UTCDateTime
    status: Literal["passed", "issues", "error", "timeout", "not_supported"]
    reason: Literal[
        "checks_completed",
        "runner_unavailable",
        "snapshot_rejected",
        "sandbox_failed",
        "deadline",
        "uncertain",
    ]
    snapshot: SnapshotInfo | None = None
    image_id: Annotated[StrictStr, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None = None
    checks: tuple[ToolCheck, ...] = Field(default=(), max_length=2)
    container_removed: StrictBool = False
    # Project code is executed, but exclusively in the disposable container.
    execution_scope: Literal["not_started", "container"] = "not_started"

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.checks:
            if tuple(item.tool for item in self.checks) != ("ruff", "pytest"):
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
