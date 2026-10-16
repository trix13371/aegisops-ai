"""Pure projection from saved full-check evidence into a closed full summary."""

from typing import cast

from aegisops.project_ai.full_models import FullLimitation, FullSourceMatch, FullSummary
from aegisops.project_ai.review_models import ReviewLimitation
from aegisops.project_ai.review_projection import review_summary
from aegisops.project_checks.models import FullCheckResult, build_result
from aegisops.project_reviews.projection import build_review
from aegisops.project_runner.models import RunClaim, RunResult
from aegisops.projects.models import ProjectError, ProjectReport

_ALLOWED: frozenset[ReviewLimitation] = frozenset(
    {
        "missing_static",
        "partial_static",
        "unconfirmed_runner",
        "truncated_output",
        "selected_profile_only",
    }
)


def full_summary(
    check: FullCheckResult,
    report: ProjectReport | None,
    claim: RunClaim,
    run: RunResult,
) -> FullSummary:
    """Derive a full summary from saved evidence; performs no IO.

    Rebuilds the full-check result from the supplied sources and requires it
    to equal the saved ``check`` so a tampered or mismatched source is
    rejected. Reuses ``review_summary`` for categories and nested summaries,
    then discards the old unverified source-match limitation and derives the
    full wrapper status from the check itself.
    """
    check = FullCheckResult.model_validate(check)
    claim = RunClaim.model_validate(claim)
    run = RunResult.model_validate(run)
    if report is not None:
        report = ProjectReport.model_validate(report)
    if build_result(claim, report, run) != check:
        raise ProjectError("Сводка полной проверки не подтверждена")
    if check.project_id != claim.project_id:
        raise ProjectError("Сводка полной проверки не подтверждена")

    base = review_summary(build_review(check.project_id, report, claim, run))
    if base.runner is None:
        raise ProjectError("Сводка полной проверки не подтверждена")

    limitations: list[FullLimitation] = []
    for item in base.limitations:
        if item in _ALLOWED:
            limitations.append(cast(FullLimitation, item))
    if "sequential_capture" not in limitations:
        limitations.append("sequential_capture")
    if check.snapshot is None and "missing_snapshot" not in limitations:
        limitations.append("missing_snapshot")

    source_match: FullSourceMatch = (
        "shared_snapshot" if report is not None and check.snapshot is not None else "not_confirmed"
    )

    return FullSummary(
        status=check.status,
        source_match=source_match,
        categories=base.categories,
        static=base.static,
        runner=base.runner,
        limitations=tuple(limitations),
    )


__all__ = ("full_summary",)
