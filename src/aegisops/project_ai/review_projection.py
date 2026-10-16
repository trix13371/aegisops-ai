"""Build an explicit source-free payload. Never export raw result or review dictionaries."""

from collections import Counter
from typing import Literal

from aegisops.project_ai.review_models import (
    CheckSummary,
    ReviewCategory,
    ReviewCode,
    ReviewSummary,
    RunnerSummary,
    StaticSummary,
)
from aegisops.project_reviews.models import Review


def review_summary(review: Review) -> ReviewSummary:
    review = Review.model_validate(review)
    categories: Counter[tuple[ReviewCode, Literal["error", "warning"]]] = Counter()
    static = None
    if review.static is not None:
        source = review.static
        static = StaticSummary(
            status=source.status,
            rules_version=source.rules_version,
            files_checked=source.files_checked,
            skipped=source.skipped,
            partial=source.partial,
        )
        for item in source.categories:
            categories[(item.code, item.severity)] += item.count
    runner = None
    if review.runner is not None:
        run = review.runner
        runner = RunnerSummary(
            profile=run.profile,
            environment=run.environment,
            state=run.state,
            reason=run.reason,
            execution_scope=run.execution_scope,
            container_removed=run.container_removed,
            compatibility_issues=run.compatibility_issues,
            checks=tuple(
                CheckSummary(
                    tool=check.tool, status=check.status, output_truncated=check.output_truncated
                )
                for check in run.checks
            ),
        )
        code_for: dict[str, ReviewCode] = {
            "failed": "runner_failed",
            "error": "runner_error",
            "timeout": "runner_timeout",
            "no_tests": "runner_no_tests",
        }
        for check in run.checks:
            if check.status in code_for:
                code = code_for[check.status]
                categories[(code, "warning" if code == "runner_no_tests" else "error")] += 1
        if run.state == "pending":
            categories[("runner_pending", "warning")] += 1
        elif run.state == "not_supported":
            categories[("runner_not_supported", "warning")] += 1
        elif "unconfirmed_runner" in review.limitations:
            categories[("runner_unconfirmed", "warning")] += 1
        if not run.checks and run.state in {"error", "timeout"}:
            categories[("runner_error" if run.state == "error" else "runner_timeout", "error")] += 1
    return ReviewSummary(
        status=review.status,
        static=static,
        runner=runner,
        limitations=review.limitations,
        categories=tuple(
            ReviewCategory(code=code, severity=severity, count=count)
            for (code, severity), count in sorted(categories.items())
        ),
    )
