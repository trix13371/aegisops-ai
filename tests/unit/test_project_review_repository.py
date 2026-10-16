"""Durable review selection and source-free, single-attempt AI state boundaries."""

import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aegisops.domain.common import utc_now
from aegisops.project_ai.review_models import ReviewAdvice, ReviewExplanation
from aegisops.project_ai.review_projection import review_summary
from aegisops.project_ai.review_repository import ReviewAnalysisRepository, ReviewResult
from aegisops.project_reviews import source_digest
from aegisops.project_reviews.repository import ReviewRepository
from aegisops.project_runner.compatibility import inspect_package
from aegisops.project_runner.models import RunResult, SnapshotInfo, ToolCheck, tools_for
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.models import Finding, ProjectError, ProjectReport
from aegisops.projects.repository import ProjectRepository


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "CANARY_PRIVATE_ROOT"
    root.mkdir()
    (root / "CANARY_PRIVATE_FILENAME.py").write_text("raise RuntimeError('not executed')")
    projects = ProjectRepository(tmp_path / "state" / "projects")
    project = projects.add("CANARY_PRIVATE_PROJECT_NAME", str(root))
    report = ProjectReport(
        id=uuid4(),
        project_id=project.id,
        started_at=utc_now(),
        completed_at=utc_now(),
        status="issues",
        files_seen=2,
        files_checked=1,
        skipped=1,
        stacks=("CANARY_PRIVATE_STACK",),
        findings=(
            Finding(
                code="syntax",
                severity="error",
                file="CANARY_PRIVATE_FILENAME.py",
                message="CANARY_PRIVATE_MESSAGE",
                location="CANARY_PRIVATE_LOCATION",
                recommendation="CANARY_PRIVATE_RECOMMENDATION",
            ),
        ),
        partial=False,
    )
    projects.save_report(report)
    runs = RunRepository(tmp_path / "state" / "runs")
    run = runs.create(project.id, "node-vitest-v1")
    completed = RunResult(
        id=run.id,
        project_id=project.id,
        environment=run.environment,
        profile=run.profile,
        completed_at=utc_now(),
        status="issues",
        reason="checks_completed",
        snapshot=SnapshotInfo(
            sha256="a" * 64,
            files=1,
            python_files=0,
            javascript_files=1,
            bytes=20,
            skipped=0,
        ),
        compatibility=inspect_package(
            '{"dependencies":{"vitest":"1.0.0","canary-private-package":"1.0.0"}}'
        ),
        image_id="sha256:" + "b" * 64,
        checks=(
            ToolCheck(
                tool="typescript",
                status="passed",
                exit_code=0,
                output="CANARY_PRIVATE_TYPESCRIPT_OUTPUT",
                output_truncated=False,
            ),
            ToolCheck(
                tool="vitest",
                status="failed",
                exit_code=1,
                output="CANARY_PRIVATE_LOG_OUTPUT",
                output_truncated=False,
            ),
        ),
        container_removed=True,
        execution_scope="container",
    )
    runs.save(completed)
    reviews = ReviewRepository(tmp_path / "state" / "reviews", projects, runs)
    analyses = ReviewAnalysisRepository(tmp_path / "state" / "review-ai")
    return SimpleNamespace(
        root=root,
        project=project,
        projects=projects,
        report=report,
        runs=runs,
        run=run,
        result=completed,
        reviews=reviews,
        analyses=analyses,
    )


def selected(case):
    return case.reviews.create(case.project.id, case.report.id, case.run.id)


def ai_result(review, claim, *, status="completed", code="syntax"):
    return ReviewResult(
        review_id=review.id,
        review_sha256=claim.review_sha256,
        completed_at=utc_now(),
        status=status,
        response=(
            ReviewExplanation(
                summary="Сохранённые проверки сообщили о проблемах.",
                actions=(
                    ReviewAdvice(
                        code=code,
                        priority="medium",
                        explanation="Проверка сообщила об ошибке.",
                        next_step="Изучите локальный отчёт.",
                    ),
                ),
                limitations="Совпадение исходников не подтверждено.",
            )
            if status == "completed"
            else None
        ),
    )


def overwrite(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_selected_evidence_deduplicates_and_survives_restart(case):
    first = selected(case)
    reopened = ReviewRepository(case.reviews.directory, case.projects, case.runs)
    assert reopened.get(first.id) == first
    assert reopened.create(case.project.id, case.report.id, case.run.id) == first
    assert reopened.history(case.project.id) == (first,)
    assert len(list((case.reviews.directory / "reviews").glob("*.json"))) == 1


@pytest.mark.parametrize("source", ["static", "runner"])
def test_one_saved_source_is_allowed_with_explicit_missing_limit(case, source):
    review = case.reviews.create(
        case.project.id,
        case.report.id if source == "static" else None,
        case.run.id if source == "runner" else None,
    )
    assert ("missing_runner" if source == "static" else "missing_static") in review.limitations
    assert case.reviews.get(review.id) == review


@pytest.mark.parametrize("source", ["static", "runner", "both"])
def test_cross_project_selection_is_rejected_before_persistence(case, tmp_path, source):
    other_root = tmp_path / "other-project"
    other_root.mkdir()
    other = case.projects.add("Other project", str(other_root))
    foreign_report = case.report.model_copy(update={"id": uuid4(), "project_id": other.id})
    case.projects.save_report(foreign_report)
    foreign_run = case.runs.create(other.id, "node-vitest-v1")
    case.runs.save(
        case.result.model_copy(
            update={"id": foreign_run.id, "project_id": other.id, "completed_at": utc_now()}
        )
    )
    with pytest.raises(ProjectError):
        case.reviews.create(
            case.project.id,
            foreign_report.id if source in {"static", "both"} else case.report.id,
            foreign_run.id if source in {"runner", "both"} else case.run.id,
        )
    assert not (case.reviews.directory / "reviews").exists()


def test_pending_run_cannot_be_saved_as_stable_review(case):
    pending = case.runs.create(case.project.id)
    with pytest.raises(ProjectError):
        case.reviews.create(case.project.id, case.report.id, pending.id)
    assert not (case.reviews.directory / "reviews").exists()


def test_empty_selection_is_rejected(case):
    with pytest.raises(ProjectError):
        case.reviews.create(case.project.id, None, None)


@pytest.mark.parametrize("source", ["static", "result", "claim"])
def test_changed_full_source_invalidates_saved_review_even_if_summary_same(case, source):
    review = selected(case)
    if source == "static":
        changed = case.report.model_copy(
            update={
                "findings": (
                    case.report.findings[0].model_copy(update={"message": "changed message"}),
                )
            }
        )
        path = case.projects.directory / "reports" / f"{case.report.id}.json"
    elif source == "result":
        changed = case.result.model_copy(
            update={
                "checks": (
                    case.result.checks[0].model_copy(update={"output": "changed raw log"}),
                    case.result.checks[1],
                )
            }
        )
        path = case.runs.path(case.run.id, "result")
    else:
        changed = case.run.model_copy(
            update={"created_at": case.run.created_at - timedelta(seconds=1)}
        )
        path = case.runs.path(case.run.id, "claim")
    path.write_text(changed.model_dump_json(), encoding="utf-8")
    with pytest.raises(ProjectError):
        case.reviews.get(review.id)
    with pytest.raises(ProjectError):
        case.reviews.history(case.project.id)


@pytest.mark.parametrize("source", ["review", "static", "result", "claim", "index"])
def test_invalid_json_is_never_presented_as_success(case, source):
    review = selected(case)
    paths = {
        "review": case.reviews.path("reviews", review.id),
        "static": case.projects.directory / "reports" / f"{case.report.id}.json",
        "result": case.runs.path(case.run.id, "result"),
        "claim": case.runs.path(case.run.id, "claim"),
        "index": case.reviews.path("projects", case.project.id),
    }
    paths[source].write_text("{broken", encoding="utf-8")
    with pytest.raises((ProjectError, ValueError)):
        if source == "index":
            case.reviews.history(case.project.id)
        else:
            case.reviews.get(review.id)


def test_history_cannot_reference_another_projects_review(case, tmp_path):
    first = selected(case)
    other_root = tmp_path / "other-project"
    other_root.mkdir()
    other = case.projects.add("Other project", str(other_root))
    path = case.reviews.path("projects", other.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    overwrite(path, {"reviews": [str(first.id)]})
    with pytest.raises(ProjectError):
        case.reviews.history(other.id)


def test_history_is_bounded_but_old_review_references_still_work(case):
    reviews = []
    for _ in range(22):
        report = case.report.model_copy(update={"id": uuid4()})
        case.projects.save_report(report)
        reviews.append(case.reviews.create(case.project.id, report.id, case.run.id))
    assert case.reviews.history(case.project.id) == tuple(reversed(reviews[-20:]))
    assert case.reviews.get(reviews[0].id) == reviews[0]
    latest = reviews[-1]
    assert case.reviews.create(case.project.id, latest.static_report_id, latest.run_id) == latest
    history = case.reviews.history(case.project.id)
    assert len(history) == len({item.id for item in history}) == 20
    oldest = reviews[0]
    assert case.reviews.create(case.project.id, oldest.static_report_id, oldest.run_id) == oldest
    restored = case.reviews.history(case.project.id)
    assert restored[0] == oldest
    assert len(restored) == len({item.id for item in restored}) == 20


def test_history_rejects_repeated_references_as_corruption(case):
    review = selected(case)
    overwrite(
        case.reviews.path("projects", case.project.id),
        {"reviews": [str(review.id), str(review.id)]},
    )
    with pytest.raises((ProjectError, ValidationError)):
        case.reviews.history(case.project.id)


def test_deduplicated_create_repairs_index_after_interrupted_index_write(case, monkeypatch):
    original = case.reviews.write

    def interrupted(path, data):
        if path.parent.name == "temporary":
            raise OSError("simulated interrupted index write")
        original(path, data)

    monkeypatch.setattr(case.reviews, "write", interrupted)
    with pytest.raises(OSError):
        selected(case)
    assert len(list((case.reviews.directory / "reviews").glob("*.json"))) == 1
    reopened = ReviewRepository(case.reviews.directory, case.projects, case.runs)
    review = reopened.create(case.project.id, case.report.id, case.run.id)
    assert reopened.history(case.project.id) == (review,)


def test_unregistered_project_cannot_be_read_through_review(case):
    review = selected(case)
    case.projects.remove(case.project.id)
    with pytest.raises(ProjectError):
        case.reviews.get(review.id)
    with pytest.raises(ProjectError):
        case.reviews.history(case.project.id)


def test_saved_review_identity_hash_detects_closed_summary_tampering(case):
    review = selected(case)
    data = review.model_dump(mode="json")
    data["static"]["files_checked"] += 1
    overwrite(case.reviews.path("reviews", review.id), data)
    with pytest.raises(ProjectError):
        case.reviews.get(review.id)


@pytest.mark.parametrize(
    "environment", ["minimal", "python-data-v1", "node-js-v1", "node-vitest-v1"]
)
def test_ai_payload_never_contains_source_strings_ids_hashes_or_package_names(case, environment):
    run = case.runs.create(case.project.id, environment)
    checks = tuple(
        ToolCheck(
            tool=tool,
            status="failed" if index == 1 else "passed",
            exit_code=1 if index == 1 else 0,
            output="CANARY_PRIVATE_LOG_OUTPUT",
            output_truncated=False,
        )
        for index, tool in enumerate(tools_for(run.profile))
    )
    case.runs.save(
        case.result.model_copy(
            update={
                "id": run.id,
                "profile": run.profile,
                "environment": run.environment,
                "completed_at": utc_now(),
                "checks": checks,
                "snapshot": case.result.snapshot.model_copy(
                    update={
                        "python_files": 1 if environment in {"minimal", "python-data-v1"} else 0,
                        "javascript_files": 0
                        if environment in {"minimal", "python-data-v1"}
                        else 1,
                    }
                ),
            }
        )
    )
    review = case.reviews.create(case.project.id, case.report.id, run.id)
    payload = review_summary(review)
    text = payload.model_dump_json()
    for secret in (
        "CANARY_",
        "canary-private-package",
        str(case.root),
        str(case.project.id),
        str(case.report.id),
        str(run.id),
        str(review.id),
        review.static_report_sha256,
        review.run_claim_sha256,
        review.run_result_sha256,
        "a" * 64,
        "b" * 64,
    ):
        assert secret not in text
    assert payload.source_match == "unverified"
    assert "unverified_source_match" in payload.limitations
    assert {item.code for item in payload.categories} == {"syntax", "runner_failed"}
    assert set(payload.model_dump()) == {
        "profile",
        "status",
        "source_match",
        "categories",
        "static",
        "runner",
        "limitations",
    }


def test_ai_claim_is_single_attempt_survives_restart_and_binds_payload(case):
    review = selected(case)
    assert case.analyses.state(review) == {"status": "not_requested"}
    claim = case.analyses.claim(review)
    assert claim.review_sha256 == source_digest(review)
    assert claim.payload == review_summary(review)
    reopened = ReviewAnalysisRepository(case.analyses.files.directory)
    assert reopened.state(review)["status"] == "pending"
    assert reopened.read_claim(review.id) == claim
    with pytest.raises(ProjectError):
        reopened.claim(review)
    result = ai_result(review, claim)
    reopened.write(review.id, "result", result.model_dump_json())
    assert reopened.read_result(review.id) == result
    assert reopened.state(review)["status"] == "completed"
    with pytest.raises(ProjectError):
        reopened.claim(review)
    before = reopened.files.path(review.id, "result").read_bytes()
    reopened.fail(review.id)
    assert reopened.files.path(review.id, "result").read_bytes() == before


def test_failed_attempt_does_not_retry_or_overwrite_result(case):
    review = selected(case)
    case.analyses.claim(review)
    case.analyses.fail(review.id)
    assert case.analyses.state(review)["status"] == "failed"
    with pytest.raises((ProjectError, ValueError)):
        case.analyses.claim(review)
    saved = case.analyses.files.path(review.id, "result").read_bytes()
    case.analyses.fail(review.id)
    assert case.analyses.files.path(review.id, "result").read_bytes() == saved


@pytest.mark.parametrize("field", ["id", "digest", "payload"])
def test_ai_claim_changed_binding_is_rejected_on_state_read(case, field):
    review = selected(case)
    claim = case.analyses.claim(review)
    data = claim.model_dump(mode="json")
    if field == "id":
        data["review_id"] = str(uuid4())
    elif field == "digest":
        data["review_sha256"] = "c" * 64
    else:
        data["payload"]["static"]["files_checked"] += 1
    overwrite(case.analyses.files.path(review.id, "claim"), data)
    with pytest.raises(ProjectError):
        case.analyses.state(review)
    with pytest.raises(ProjectError):
        case.analyses.claim(review)


@pytest.mark.parametrize("field", ["id", "digest", "time", "action"])
def test_ai_response_binding_and_allowed_issue_codes(case, field):
    review = selected(case)
    claim = case.analyses.claim(review)
    result = ai_result(review, claim)
    data = result.model_dump(mode="json")
    if field == "id":
        data["review_id"] = str(uuid4())
    elif field == "digest":
        data["review_sha256"] = "c" * 64
    elif field == "time":
        data["completed_at"] = (claim.created_at - timedelta(seconds=1)).isoformat()
    else:
        data["response"]["actions"][0]["code"] = "docker_socket"
    case.analyses.write(review.id, "result", json.dumps(data))
    with pytest.raises(ProjectError):
        case.analyses.state(review)
    saved = case.analyses.files.path(review.id, "result").read_bytes()
    with pytest.raises(ProjectError):
        case.analyses.fail(review.id)
    assert case.analyses.files.path(review.id, "result").read_bytes() == saved


@pytest.mark.parametrize("name", ["claim", "result"])
@pytest.mark.parametrize("corruption", ["invalid_json", "duplicate_key", "unknown_field"])
def test_ai_corruption_never_becomes_fresh_or_successful_attempt(case, name, corruption):
    review = selected(case)
    claim = case.analyses.claim(review)
    if name == "result":
        case.analyses.write(review.id, "result", ai_result(review, claim).model_dump_json())
    path = case.analyses.files.path(review.id, name)
    if corruption == "invalid_json":
        path.write_text("{broken", encoding="utf-8")
    elif corruption == "duplicate_key":
        data = path.read_text(encoding="utf-8")
        path.write_text('{"review_id":"' + str(review.id) + '",' + data[1:], encoding="utf-8")
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["unexpected"] = "private"
        overwrite(path, data)
    before = path.read_bytes()
    with pytest.raises((ProjectError, ValueError)):
        case.analyses.state(review)
    with pytest.raises((ProjectError, ValueError)):
        case.analyses.claim(review)
    with pytest.raises((ProjectError, ValueError)):
        case.analyses.fail(review.id)
    assert path.read_bytes() == before


def test_ai_orphan_result_is_corruption_not_not_requested(case):
    review = selected(case)
    claim = case.analyses.claim(review)
    case.analyses.write(review.id, "result", ai_result(review, claim).model_dump_json())
    case.analyses.files.path(review.id, "claim").unlink()
    with pytest.raises(ProjectError):
        case.analyses.state(review)
    with pytest.raises(ProjectError):
        case.analyses.claim(review)


def test_ai_orphan_start_marker_blocks_fresh_claim(case):
    review = selected(case)
    case.analyses.write(review.id, "started", '{"status":"started"}')
    with pytest.raises(ProjectError):
        case.analyses.state(review)
    with pytest.raises(ProjectError):
        case.analyses.claim(review)


def test_ai_start_marker_is_one_attempt_and_does_not_count_as_completed(case):
    review = selected(case)
    case.analyses.claim(review)
    case.analyses.write(review.id, "started", '{"status":"started"}')
    with pytest.raises(FileExistsError):
        case.analyses.write(review.id, "started", '{"status":"started"}')
    assert case.analyses.state(review)["status"] == "pending"
    with pytest.raises(ProjectError):
        case.analyses.claim(review)


def test_ai_state_checks_full_review_hash_not_only_safe_projection(case):
    review = selected(case)
    case.analyses.claim(review)
    changed = review.model_copy(update={"created_at": review.created_at - timedelta(seconds=1)})
    assert review_summary(changed) == review_summary(review)
    with pytest.raises(ProjectError):
        case.analyses.state(changed)
