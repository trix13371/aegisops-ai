"""Compare two saved full checks without rescanning, source reads or model calls.

``compare_checks`` is a pure function of two validated ``CheckEvidence``
values. ``read_evidence`` re-reads a saved claim and, when a verified full
result exists, the linked run and static report through the existing
repositories. ``compare_saved_checks`` composes the two. No writes, no
network, no AI, no source-folder reads. Direction is explicit base->target;
reverse chronology is allowed and timestamps are display-only.
"""

from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictStr, model_validator

from aegisops.domain.common import DTO, UTCDateTime
from aegisops.project_checks.models import FullCheckResult, build_result
from aegisops.project_checks.repository import FullCheckRepository
from aegisops.project_runner.comparison import RunComparison, compare_runs
from aegisops.project_runner.models import Environment, Profile, RunClaim, RunResult
from aegisops.projects.comparison import ReportComparison, compare_reports
from aegisops.projects.models import ProjectError, ProjectReport

FULL_COMPARISON_FORMAT: Final = "aegisops-full-comparison-v1"

ImageId = Annotated[StrictStr, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

ComparisonReason = Literal[
    "unfinished",
    "missing_static",
    "incomplete",
    "profile_changed",
    "environment_changed",
    "image_changed",
    "rules_changed",
]

CheckStatus = Literal["pending", "passed", "issues", "incomplete"]
RulesVersion = Literal["local-static-v1", "local-static-v2"]


class CheckEvidence(DTO):
    """A saved claim plus, when verified, its linked result/report/run.

    A pending claim has no verified linked components: ``result`` absent
    requires ``report`` and ``run`` absent. When ``result`` is present the
    run is required and ``build_result`` must reproduce it exactly, which
    rejects wrong ids, project, profile, environment, times and digests.
    """

    claim: RunClaim
    result: FullCheckResult | None = None
    report: ProjectReport | None = None
    run: RunResult | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.result is None:
            if self.report is not None or self.run is not None:
                raise ValueError("pending check has no verified linked components")
            return self
        if self.run is None:
            raise ValueError("verified check requires a run result")
        if build_result(self.claim, self.report, self.run) != self.result:
            raise ValueError("saved check does not match its sources")
        return self


class CheckContext(DTO):
    """Display context derived from actual evidence; no invented fields."""

    id: UUID
    created_at: UTCDateTime
    status: CheckStatus
    profile: Profile
    environment: Environment
    rules_version: RulesVersion | None = None
    image_id: ImageId | None = None


def _context(evidence: CheckEvidence) -> CheckContext:
    claim = evidence.claim
    result = evidence.result
    report = evidence.report
    run = evidence.run
    if result is None:
        status: CheckStatus = "pending"
    else:
        status = result.status
    return CheckContext(
        id=claim.id,
        created_at=claim.created_at,
        status=status,
        profile=claim.profile,
        environment=claim.environment,
        rules_version=report.rules_version if report is not None else None,
        image_id=run.image_id if run is not None else None,
    )


class FullComparison(DTO):
    """Deterministic comparison of two saved full checks.

    ``comparable`` is true exactly when ``reasons`` is empty. A comparable
    comparison requires both nested comparisons to be comparable and a
    non-None ``same_snapshot``. A non-comparable comparison carries no
    static/runner payloads, so no false added/removed entries are produced.
    """

    format: Literal["aegisops-full-comparison-v1"] = FULL_COMPARISON_FORMAT
    project_id: UUID
    base: CheckContext
    target: CheckContext
    comparable: StrictBool
    reasons: tuple[ComparisonReason, ...] = Field(default=(), max_length=7)
    same_snapshot: StrictBool | None = None
    static: ReportComparison | None = None
    runner: RunComparison | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("comparison reasons must be unique")
        if self.comparable != (not self.reasons):
            raise ValueError("comparable must match empty reasons")
        if self.comparable:
            if self.static is None or self.runner is None:
                raise ValueError("comparable comparison requires nested comparisons")
            if not self.static.comparable or not self.runner.comparable:
                raise ValueError("nested comparisons must be comparable")
            if self.same_snapshot is None:
                raise ValueError("comparable comparison requires snapshot equality")
        else:
            if self.static is not None or self.runner is not None:
                raise ValueError("non-comparable comparison carries no payloads")
        return self


def _incomplete(evidence: CheckEvidence) -> bool:
    result = evidence.result
    if result is None:
        return False
    report = evidence.report
    run = evidence.run
    if result.status == "incomplete":
        return True
    if report is None or report.partial:
        return True
    if run is None:
        return True
    if run.status not in {"passed", "issues"}:
        return True
    if run.reason != "checks_completed":
        return True
    if not run.container_removed:
        return True
    if run.image_id is None:
        return True
    if run.snapshot is None:
        return True
    if len(run.checks) != 2:
        return True
    if any(check.output_truncated for check in run.checks):
        return True
    return False


def _same_snapshot(base: CheckEvidence, target: CheckEvidence) -> bool | None:
    before = base.run.snapshot if base.run is not None else None
    after = target.run.snapshot if target.run is not None else None
    if before is None or after is None:
        return None
    return before.sha256 == after.sha256


def compare_checks(base: CheckEvidence, target: CheckEvidence) -> FullComparison:
    """Pure, deterministic comparison of two saved full checks.

    Direction is explicit base->target; reverse chronology is allowed and
    timestamps are display-only. Reasons are derived in a fixed order and
    deduplicated. A pending check contributes only ``unfinished``; it never
    adds ``incomplete`` merely because its run is absent.
    """
    base = CheckEvidence.model_validate(base)
    target = CheckEvidence.model_validate(target)
    if base.claim.id == target.claim.id:
        raise ProjectError("Выберите две разные проверки")
    if base.claim.project_id != target.claim.project_id:
        raise ProjectError("Выберите две проверки одного проекта")

    reasons: list[ComparisonReason] = []
    if base.result is None or target.result is None:
        reasons.append("unfinished")
    if (base.result is not None and base.report is None) or (
        target.result is not None and target.report is None
    ):
        reasons.append("missing_static")
    if _incomplete(base) or _incomplete(target):
        reasons.append("incomplete")
    if base.claim.profile != target.claim.profile:
        reasons.append("profile_changed")
    if base.claim.environment != target.claim.environment:
        reasons.append("environment_changed")
    if (
        base.run is not None
        and target.run is not None
        and base.run.image_id is not None
        and target.run.image_id is not None
        and base.run.image_id != target.run.image_id
    ):
        reasons.append("image_changed")
    if (
        base.report is not None
        and target.report is not None
        and base.report.rules_version != target.report.rules_version
    ):
        reasons.append("rules_changed")

    ordered = tuple(dict.fromkeys(reasons))
    base_context = _context(base)
    target_context = _context(target)
    if ordered:
        return FullComparison(
            project_id=base.claim.project_id,
            base=base_context,
            target=target_context,
            comparable=False,
            reasons=ordered,
            same_snapshot=_same_snapshot(base, target),
            static=None,
            runner=None,
        )

    if base.report is None or target.report is None or base.run is None or target.run is None:
        raise ProjectError("Сравнение проверок не подтверждено")
    static = compare_reports(base.report, target.report)
    runner = compare_runs(base.claim, target.claim, base.run, target.run)
    if not static.comparable or not runner.comparable:
        raise ProjectError("Сравнение проверок не подтверждено")
    same_snapshot = _same_snapshot(base, target)
    if same_snapshot is None:
        raise ProjectError("Сравнение проверок не подтверждено")
    return FullComparison(
        project_id=base.claim.project_id,
        base=base_context,
        target=target_context,
        comparable=True,
        reasons=(),
        same_snapshot=same_snapshot,
        static=static,
        runner=runner,
    )


def read_evidence(repository: FullCheckRepository, identifier: UUID) -> CheckEvidence:
    """Read one saved check; a pending claim yields no linked components.

    The claim is read through the repository (registered project). The
    result, when present, is revalidated against freshly read sources. A
    partial run file without a verified full result is ignored.
    """
    claim = repository.claim(identifier)
    result = repository.result(identifier)
    if result is None:
        return CheckEvidence(claim=claim, result=None, report=None, run=None)
    run = repository.runs.result(identifier)
    if run is None:
        raise ProjectError("Результат проверки не подтверждён")
    report = (
        repository.projects.read_report(result.static_report_id)
        if result.static_report_id is not None
        else None
    )
    return CheckEvidence(claim=claim, result=result, report=report, run=run)


def compare_saved_checks(
    repository: FullCheckRepository, base_id: UUID, target_id: UUID
) -> FullComparison:
    """Read two saved checks and compare them; performs no writes."""
    base = read_evidence(repository, base_id)
    target = read_evidence(repository, target_id)
    return compare_checks(base, target)


__all__ = (
    "FULL_COMPARISON_FORMAT",
    "CheckContext",
    "CheckEvidence",
    "ComparisonReason",
    "FullComparison",
    "compare_checks",
    "compare_saved_checks",
    "read_evidence",
)
