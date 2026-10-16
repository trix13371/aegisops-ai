"""Strict DTOs and pure derivation for the shared-snapshot full check.

No IO happens here. ``build_result`` is a pure function of a claim, an
optional static report and a container run result. The repository layer
re-reads the referenced sources and compares the entire rebuilt DTO so a
tampered saved source is rejected.
"""

import hashlib
import json
from typing import Final, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from aegisops.domain.common import DTO, Sha256, UTCDateTime
from aegisops.project_runner.models import (
    Environment,
    Profile,
    RunClaim,
    RunResult,
    SnapshotInfo,
    profile_for,
)
from aegisops.projects.models import ProjectError, ProjectReport

FULL_CHECK_FORMAT: Final = "aegisops-full-check-v1"

FullCheckStatus = Literal["passed", "issues", "incomplete"]


class FullCheckResult(DTO):
    """Immutable, serializable outcome of one shared-snapshot full check.

    ``static_report_id`` and ``static_report_sha256`` are paired: either
    both are present or both are absent. ``snapshot`` mirrors the container
    run snapshot when one exists.
    """

    format: Literal["aegisops-full-check-v1"] = FULL_CHECK_FORMAT
    id: UUID
    project_id: UUID
    environment: Environment = "minimal"
    profile: Profile
    completed_at: UTCDateTime
    static_report_id: UUID | None = None
    static_report_sha256: Sha256 | None = None
    run_result_sha256: Sha256
    snapshot: SnapshotInfo | None = None
    status: FullCheckStatus

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.static_report_id is None) != (self.static_report_sha256 is None):
            raise ValueError("static report reference must be paired")
        if self.profile != profile_for(self.environment):
            raise ValueError("profile does not match environment")
        return self


def canonical_digest(value: DTO) -> str:
    """Deterministic SHA-256 over the JSON projection of a DTO."""
    payload = json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _static_digest(report: ProjectReport) -> str:
    return canonical_digest(report)


def _run_digest(run: RunResult) -> str:
    return canonical_digest(run)


def _incomplete(
    report: ProjectReport | None,
    run: RunResult,
) -> bool:
    if report is None:
        return True
    if report.partial:
        return True
    if run.status not in {"passed", "issues"}:
        return True
    if not run.container_removed:
        return True
    if run.image_id is None:
        return True
    if not run.checks:
        return True
    if run.snapshot is None:
        return True
    if any(check.output_truncated for check in run.checks):
        return True
    return False


def build_result(
    claim: RunClaim,
    report: ProjectReport | None,
    run: RunResult,
) -> FullCheckResult:
    """Derive a full-check result deterministically; performs no IO.

    Raises ``ProjectError`` when the supplied sources are inconsistent with
    the claim or with each other. The repository re-runs this function on
    freshly read sources before trusting a saved result.
    """
    if run.id != claim.id or run.project_id != claim.project_id:
        raise ProjectError("Результат проверки не соответствует запросу")
    if run.environment != claim.environment or run.profile != claim.profile:
        raise ProjectError("Результат проверки не соответствует запросу")
    if run.completed_at < claim.created_at:
        raise ProjectError("Результат проверки не соответствует запросу")

    static_report_id: UUID | None = None
    static_report_sha256: str | None = None
    if report is not None:
        if report.project_id != claim.project_id:
            raise ProjectError("Статический отчёт не соответствует проекту")
        if report.started_at < claim.created_at:
            raise ProjectError("Статический отчёт не соответствует запросу")
        if report.completed_at > run.completed_at:
            raise ProjectError("Статический отчёт завершён позже запуска")
        if run.snapshot is None:
            raise ProjectError("Статический отчёт без снимка не подтверждён")
        static_report_id = report.id
        static_report_sha256 = _static_digest(report)

    if _incomplete(report, run):
        status: FullCheckStatus = "incomplete"
    elif report is not None and report.findings:
        status = "issues"
    elif run.status == "issues":
        status = "issues"
    else:
        status = "passed"

    return FullCheckResult(
        id=claim.id,
        project_id=claim.project_id,
        environment=claim.environment,
        profile=claim.profile,
        completed_at=run.completed_at,
        static_report_id=static_report_id,
        static_report_sha256=static_report_sha256,
        run_result_sha256=_run_digest(run),
        snapshot=run.snapshot,
        status=status,
    )


class FullCheckIndex(DTO):
    checks: tuple[UUID, ...] = Field(default=(), max_length=20)


__all__ = (
    "FULL_CHECK_FORMAT",
    "FullCheckIndex",
    "FullCheckResult",
    "FullCheckStatus",
    "build_result",
    "canonical_digest",
)
