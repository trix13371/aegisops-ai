"""Immutable bounded combined evidence, with no inference of matching source versions."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aegisops.project_ai.projection import report_digest
from aegisops.project_reviews import Review, build_review, source_digest
from aegisops.project_reviews.models import CategoryReview, CheckReview, RunnerReview, StaticReview
from aegisops.project_runner.compatibility import inspect_package
from aegisops.project_runner.models import (
    Environment,
    RunClaim,
    RunResult,
    SnapshotInfo,
    ToolCheck,
    profile_for,
    tools_for,
)
from aegisops.projects.models import Finding, ProjectError, ProjectReport

NOW = datetime(2026, 10, 16, tzinfo=UTC)
PROJECT = uuid4()


def static_report(*, findings: bool = False, partial: bool = False) -> ProjectReport:
    return ProjectReport(
        id=uuid4(),
        project_id=PROJECT,
        started_at=NOW,
        completed_at=NOW,
        status="partial" if partial else "issues" if findings else "passed",
        files_seen=3,
        files_checked=2,
        skipped=1,
        stacks=("DO_NOT_EXPOSE_STACK",),
        findings=(
            Finding(
                code="syntax",
                severity="error",
                file="DO_NOT_EXPOSE_PATH.py",
                message="DO_NOT_EXPOSE_MESSAGE",
                recommendation="DO_NOT_EXPOSE_RECOMMENDATION",
                location="DO_NOT_EXPOSE_LOCATION",
                line=10,
            ),
        )
        * (2 if findings else 0),
        partial=partial,
    )


def claim(environment: Environment = "minimal") -> RunClaim:
    return RunClaim(
        id=uuid4(),
        project_id=PROJECT,
        created_at=NOW,
        profile=profile_for(environment),
        environment=environment,
    )


def result(
    run: RunClaim, state: str = "passed", *, removed: bool = True, truncated: bool = False
) -> RunResult:
    tool_state = {
        "issues": "failed",
        "timeout": "timeout",
        "error": "error",
        "no_tests": "no_tests",
    }.get(state, "passed")
    status = "issues" if state == "no_tests" else state
    checks = tuple(
        ToolCheck.model_validate(
            {
                "tool": tool,
                "status": tool_state if index == 1 else "passed",
                "exit_code": (
                    0 if index == 0 or tool_state == "passed" else 5 if state == "no_tests" else 1
                ),
                "output": "DO_NOT_EXPOSE_OUTPUT",
                "output_truncated": truncated if index == 1 else False,
            }
        )
        for index, tool in enumerate(tools_for(run.profile))
    )
    return RunResult.model_validate(
        {
            "id": run.id,
            "project_id": run.project_id,
            "completed_at": NOW,
            "environment": run.environment,
            "profile": run.profile,
            "status": status if removed else "error",
            "reason": "checks_completed",
            "snapshot": SnapshotInfo(
                sha256="a" * 64,
                files=2,
                python_files=2 if run.environment in {"minimal", "python-data-v1"} else 0,
                javascript_files=0 if run.environment in {"minimal", "python-data-v1"} else 2,
                bytes=10,
                skipped=0,
            ),
            "image_id": "sha256:" + "b" * 64,
            "checks": checks,
            "container_removed": removed,
            "execution_scope": "container",
        }
    )


@pytest.mark.parametrize(
    "environment", ["minimal", "python-data-v1", "node-js-v1", "node-vitest-v1"]
)
def test_all_prepared_profiles_with_both_passed_sources(environment: Environment) -> None:
    report, run = static_report(), claim(environment)
    completed = result(run)
    review = build_review(PROJECT, report, run, completed)
    assert review.status == "no_issues_reported"
    assert review.limitations == ("unverified_source_match", "selected_profile_only")
    assert review.static_report_id == report.id
    assert review.run_id == run.id
    assert review.static_report_sha256 == source_digest(report)
    assert review.run_claim_sha256 == source_digest(run)
    assert review.run_result_sha256 == source_digest(completed)
    assert review.static is not None and review.static.rules_version == "local-static-v1"
    assert review.runner is not None and review.runner.profile == run.profile
    assert Review.model_validate_json(review.model_dump_json()) == review


def test_private_strings_are_absent_even_from_local_summary() -> None:
    run = claim()
    report = static_report(findings=True)
    review = build_review(PROJECT, report, run, result(run, "issues"))
    text = review.model_dump_json()
    assert "DO_NOT_EXPOSE" not in text
    assert review.static is not None
    assert review.static.categories == (CategoryReview(code="syntax", severity="error", count=2),)
    assert review.status == "attention"


@pytest.mark.parametrize("state", ["issues", "no_tests"])
def test_confirmed_tool_issues_need_attention(state: str) -> None:
    run = claim()
    review = build_review(PROJECT, static_report(), run, result(run, state))
    assert review.status == "attention"
    assert "unconfirmed_runner" not in review.limitations


@pytest.mark.parametrize("state", ["error", "timeout"])
def test_tool_errors_are_incomplete(state: str) -> None:
    run = claim()
    review = build_review(PROJECT, static_report(), run, result(run, state))
    assert review.status == "incomplete"
    assert "unconfirmed_runner" in review.limitations


def test_static_only_cannot_claim_complete() -> None:
    review = build_review(PROJECT, static_report())
    assert review.status == "incomplete"
    assert review.runner is None
    assert review.run_id is review.run_claim_sha256 is review.run_result_sha256 is None
    assert review.limitations == ("unverified_source_match", "missing_runner")


def test_runner_only_cannot_claim_complete() -> None:
    run = claim()
    review = build_review(PROJECT, run_claim=run, run_result=result(run))
    assert review.status == "incomplete"
    assert review.static is review.static_report_id is review.static_report_sha256 is None
    assert review.limitations == (
        "unverified_source_match",
        "missing_static",
        "selected_profile_only",
    )


def test_pending_claim_is_explicit_without_invented_execution() -> None:
    run = claim()
    review = build_review(PROJECT, static_report(), run)
    assert review.status == "incomplete"
    assert review.runner is not None
    assert review.runner.state == "pending"
    assert review.runner.reason is None
    assert review.runner.execution_scope == "not_started"
    assert review.runner.snapshot_sha256 is None
    assert review.runner.checks == ()
    assert review.runner.container_removed is False
    assert review.run_result_sha256 is None
    assert "pending_runner" in review.limitations


def test_partial_static_is_incomplete_even_when_runner_passed() -> None:
    run = claim()
    review = build_review(PROJECT, static_report(partial=True), run, result(run))
    assert review.status == "incomplete"
    assert "partial_static" in review.limitations


def test_attention_preserves_missing_partial_and_pending_limitations() -> None:
    review = build_review(PROJECT, static_report(findings=True, partial=True), claim())
    assert review.status == "attention"
    assert review.limitations == (
        "unverified_source_match",
        "partial_static",
        "pending_runner",
        "selected_profile_only",
    )


def test_unconfirmed_cleanup_is_incomplete_not_passed() -> None:
    run = claim()
    review = build_review(PROJECT, static_report(), run, result(run, removed=False))
    assert review.status == "incomplete"
    assert review.runner is not None and review.runner.container_removed is False
    assert "unconfirmed_runner" in review.limitations


def test_truncated_failed_output_preserves_issue_and_limitation() -> None:
    run = claim()
    review = build_review(PROJECT, static_report(), run, result(run, "issues", truncated=True))
    assert review.status == "attention"
    assert "truncated_output" in review.limitations
    assert review.runner is not None and review.runner.checks[1].output_truncated


def test_truncated_error_output_is_incomplete() -> None:
    run = claim()
    review = build_review(PROJECT, static_report(), run, result(run, "error", truncated=True))
    assert review.status == "incomplete"
    assert "truncated_output" in review.limitations


def test_incompatible_project_closed_reasons_only() -> None:
    run = claim("node-js-v1")
    completed = RunResult(
        id=run.id,
        project_id=PROJECT,
        profile=run.profile,
        environment=run.environment,
        completed_at=NOW,
        status="not_supported",
        reason="project_incompatible",
        compatibility=inspect_package('{"dependencies":{"jest":"1.0.0"}}'),
        compatibility_issues=("framework_mismatch",),
    )
    review = build_review(PROJECT, static_report(), run, completed)
    assert review.status == "incomplete"
    assert review.runner is not None
    assert review.runner.compatibility_issues == ("framework_mismatch",)
    assert "dependencies" not in review.model_dump_json()


@pytest.mark.parametrize("part", ["static", "claim", "result"])
def test_project_binding(part: str) -> None:
    report, run = static_report(), claim()
    completed = result(run)
    if part == "static":
        report = report.model_copy(update={"project_id": uuid4()})
    elif part == "claim":
        run = run.model_copy(update={"project_id": uuid4()})
    else:
        completed = completed.model_copy(update={"project_id": uuid4()})
    with pytest.raises(ProjectError):
        build_review(PROJECT, report, run, completed)


@pytest.mark.parametrize("part", ["id", "environment", "profile", "time"])
def test_result_claim_binding(part: str) -> None:
    run = claim()
    completed = result(run)
    if part == "id":
        completed = completed.model_copy(update={"id": uuid4()})
    elif part == "environment":
        completed = completed.model_copy(update={"environment": "python-data-v1"})
    elif part == "profile":
        completed = result(claim("node-js-v1")).model_copy(update={"id": run.id})
    else:
        completed = completed.model_copy(update={"completed_at": NOW - timedelta(seconds=1)})
    with pytest.raises(ProjectError):
        build_review(PROJECT, static_report(), run, completed)


def test_requires_evidence_and_claim_for_result() -> None:
    with pytest.raises(ProjectError):
        build_review(PROJECT)
    with pytest.raises(ProjectError):
        build_review(PROJECT, static_report(), run_result=result(claim()))
    with pytest.raises(ProjectError):
        build_review(PROJECT, run_result=result(claim()))


def test_sources_revalidated_before_hashing_or_projection() -> None:
    invalid = static_report().model_copy(update={"status": "issues"})
    with pytest.raises(ValidationError):
        source_digest(invalid)
    with pytest.raises(ValidationError):
        build_review(PROJECT, invalid)
    run = claim()
    invalid_result = result(run).model_copy(update={"container_removed": False})
    with pytest.raises(ValidationError):
        build_review(PROJECT, static_report(), run, invalid_result)


def test_digest_is_canonical_matches_legacy_and_includes_private_evidence() -> None:
    report = static_report(findings=True)
    assert source_digest(report) == report_digest(report)
    canonical = json.dumps(report.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    assert source_digest(report) == hashlib.sha256(canonical.encode()).hexdigest()
    assert source_digest(
        ProjectReport.model_validate_json(report.model_dump_json())
    ) == source_digest(report)
    changed = report.model_copy(
        update={"findings": (report.findings[0].model_copy(update={"message": "changed"}),)}
    )
    assert source_digest(report) != source_digest(changed)
    run = claim()
    completed = result(run)
    changed_result = completed.model_copy(
        update={
            "checks": (completed.checks[0].model_copy(update={"output": "changed"}),)
            + completed.checks[1:]
        }
    )
    assert source_digest(completed) != source_digest(changed_result)


def test_review_and_nested_summaries_are_frozen() -> None:
    review = build_review(PROJECT, static_report(findings=True), claim())
    with pytest.raises(ValidationError):
        review.status = "no_issues_reported"  # type: ignore[misc]
    assert review.static is not None
    with pytest.raises(ValidationError):
        review.static.categories[0].count = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "mutation",
    [
        {"status": "no_issues_reported"},
        {"limitations": ()},
        {"limitations": ("unverified_source_match", "selected_profile_only")},
        {"static_report_sha256": None},
        {"static_report_id": None},
        {"run_id": uuid4()},
        {"run_claim_sha256": "a" * 64},
        {"run_result_sha256": "a" * 64},
    ],
)
def test_review_rejects_conclusion_identity_and_limitations_tampering(mutation: dict) -> None:
    review = build_review(PROJECT, static_report())
    data = review.model_dump()
    data.update(mutation)
    with pytest.raises(ValidationError):
        Review.model_validate(data)


def test_summary_category_counts_are_bounded_unique_and_sorted() -> None:
    data = {
        "status": "issues",
        "rules_version": "local-static-v2",
        "files_checked": 2,
        "skipped": 0,
        "partial": False,
        "categories": [{"code": "syntax", "severity": "error", "count": 200}],
    }
    assert StaticReview.model_validate(data).categories[0].count == 200
    for categories in (
        [{"code": "syntax", "severity": "error", "count": 201}],
        [{"code": "syntax", "severity": "error", "count": 0}],
        data["categories"] * 2,
        [
            {"code": "syntax", "severity": "error", "count": 1},
            {"code": "capabilities", "severity": "warning", "count": 1},
        ],
    ):
        with pytest.raises(ValidationError):
            StaticReview.model_validate({**data, "categories": categories})


@pytest.mark.parametrize(
    "mutation",
    [
        {"environment": "node-js-v1"},
        {"reason": "checks_completed"},
        {"container_removed": True},
        {"execution_scope": "container"},
        {"snapshot_sha256": "a" * 64},
        {"compatibility_issues": ("framework_mismatch",)},
    ],
)
def test_pending_summary_rejects_invented_details(mutation: dict) -> None:
    review = build_review(PROJECT, run_claim=claim())
    assert review.runner is not None
    data = review.runner.model_dump()
    data.update(mutation)
    with pytest.raises(ValidationError):
        RunnerReview.model_validate(data)


def test_creation_identity_new_each_time_evidence_hashes_stay_same() -> None:
    report, run = static_report(), claim()
    first = build_review(PROJECT, report, run)
    second = build_review(PROJECT, report, run)
    assert first.id != second.id
    assert first.static_report_sha256 == second.static_report_sha256
    assert first.run_claim_sha256 == second.run_claim_sha256
    assert first.created_at.tzinfo == UTC


@pytest.mark.parametrize(
    "mutation",
    [
        {"state": "issues"},
        {"state": "timeout"},
        {"reason": None},
        {"snapshot_sha256": None},
        {"checks": ()},
        {"container_removed": False},
        {"execution_scope": "not_started"},
        {"compatibility_issues": ("framework_mismatch",)},
        {"output": "private source or log"},
    ],
)
def test_completed_summary_rejects_misleading_or_private_fields(mutation: dict) -> None:
    run = claim()
    review = build_review(PROJECT, static_report(), run, result(run))
    assert review.runner is not None
    data = review.runner.model_dump()
    data.update(mutation)
    with pytest.raises(ValidationError):
        RunnerReview.model_validate(data)


@pytest.mark.parametrize(
    "data",
    [
        {"tool": "ruff", "status": "no_tests", "output_truncated": False},
        {"tool": "pytest", "status": "passed", "output_truncated": True},
        {"tool": "custom-command", "status": "passed", "output_truncated": False},
        {"tool": "pytest", "status": "passed", "output_truncated": False, "output": "private"},
        {"tool": "pytest", "status": "passed", "output_truncated": "false"},
    ],
)
def test_check_summaries_have_closed_typed_status_only_schema(data: dict) -> None:
    with pytest.raises(ValidationError):
        CheckReview.model_validate(data)


def test_empty_review_and_unknown_limitation_cannot_be_injected() -> None:
    review = build_review(PROJECT, static_report())
    with pytest.raises(ValidationError):
        Review.model_validate(
            {
                **review.model_dump(),
                "static": None,
                "static_report_id": None,
                "static_report_sha256": None,
            }
        )
    with pytest.raises(ValidationError):
        Review.model_validate(
            {**review.model_dump(), "limitations": ("unverified_source_match", "user text")}
        )


def test_digest_rejects_non_dto_and_changed_environment_invalidates_claim() -> None:
    with pytest.raises(ValueError):
        source_digest({"source": "private"})  # type: ignore[arg-type]
    invalid_claim = claim().model_copy(update={"environment": "node-js-v1"})
    with pytest.raises(ValidationError):
        build_review(PROJECT, run_claim=invalid_claim)
