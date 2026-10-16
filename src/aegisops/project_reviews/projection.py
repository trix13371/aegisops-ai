"""Pure construction from validated saved evidence, without reading project files."""

import hashlib
import json
from collections import Counter
from uuid import UUID, uuid4

from aegisops.domain.common import DTO, utc_now
from aegisops.project_reviews.models import (
    CategoryReview,
    CheckReview,
    Review,
    RunnerReview,
    StaticReview,
    review_limitations,
    review_status,
)
from aegisops.project_runner.models import RunClaim, RunResult
from aegisops.projects.models import ProjectError, ProjectReport


def source_digest(obj: DTO) -> str:
    """Canonical complete DTO digest, compatible with historical static report digests."""
    if not isinstance(obj, DTO):
        raise ValueError("validated DTO required")
    validated = type(obj).model_validate(obj)
    data = json.dumps(validated.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def build_review(
    project_id: UUID,
    static_report: ProjectReport | None = None,
    run_claim: RunClaim | None = None,
    run_result: RunResult | None = None,
) -> Review:
    """Summarize selected evidence; a shared project ID does not prove a shared snapshot."""
    if not isinstance(project_id, UUID):
        raise ProjectError("Некорректный идентификатор проекта")
    if static_report is None and run_claim is None:
        raise ProjectError("Выберите сохранённый отчёт или запуск")
    if run_result is not None and run_claim is None:
        raise ProjectError("Результат требует соответствующего запуска")
    if static_report is not None:
        static_report = ProjectReport.model_validate(static_report)
        if static_report.project_id != project_id:
            raise ProjectError("Отчёт относится к другому проекту")
    if run_claim is not None:
        run_claim = RunClaim.model_validate(run_claim)
        if run_claim.project_id != project_id:
            raise ProjectError("Запуск относится к другому проекту")
    if run_result is not None and run_claim is not None:
        run_result = RunResult.model_validate(run_result)
        if (
            run_result.id != run_claim.id
            or run_result.project_id != project_id
            or run_result.profile != run_claim.profile
            or run_result.environment != run_claim.environment
            or run_result.completed_at < run_claim.created_at
        ):
            raise ProjectError("Результат не соответствует запуску")
    static = None
    if static_report is not None:
        counts = Counter((item.code, item.severity) for item in static_report.findings)
        static = StaticReview(
            status=static_report.status,
            rules_version=static_report.rules_version,
            files_checked=static_report.files_checked,
            skipped=static_report.skipped,
            partial=static_report.partial,
            categories=tuple(
                CategoryReview(code=code, severity=severity, count=count)
                for (code, severity), count in sorted(counts.items())
            ),
        )
    runner = None
    if run_claim is not None:
        runner = RunnerReview(
            state=run_result.status if run_result is not None else "pending",
            profile=run_claim.profile,
            environment=run_claim.environment,
            reason=run_result.reason if run_result is not None else None,
            execution_scope=run_result.execution_scope if run_result is not None else "not_started",
            container_removed=run_result.container_removed if run_result is not None else False,
            snapshot_sha256=(
                run_result.snapshot.sha256
                if run_result is not None and run_result.snapshot is not None
                else None
            ),
            checks=tuple(
                CheckReview(
                    tool=item.tool, status=item.status, output_truncated=item.output_truncated
                )
                for item in (run_result.checks if run_result is not None else ())
            ),
            compatibility_issues=(
                run_result.compatibility_issues if run_result is not None else ()
            ),
        )
    limitations = review_limitations(static, runner)
    return Review(
        id=uuid4(),
        project_id=project_id,
        created_at=utc_now(),
        static_report_id=static_report.id if static_report is not None else None,
        run_id=run_claim.id if run_claim is not None else None,
        static_report_sha256=source_digest(static_report) if static_report is not None else None,
        run_claim_sha256=source_digest(run_claim) if run_claim is not None else None,
        run_result_sha256=source_digest(run_result) if run_result is not None else None,
        static=static,
        runner=runner,
        status=review_status(static, runner, limitations),
        limitations=limitations,
    )
