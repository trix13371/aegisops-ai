import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from aegisops.domain.common import utc_now
from aegisops.project_ai import transport
from aegisops.project_ai.full_models import FULL_PROFILE, FullSummary
from aegisops.project_ai.full_projection import full_summary
from aegisops.project_ai.full_repository import FullAnalysisRepository
from aegisops.project_ai.full_transport import explain_full
from aegisops.project_ai.review_models import REVIEW_PROFILE, ReviewAdvice, ReviewExplanation
from aegisops.project_checks.models import build_result
from aegisops.project_checks.repository import FullCheckRepository
from aegisops.project_runner.models import RunClaim, RunResult, SnapshotInfo, ToolCheck
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.models import Finding, ProjectError, ProjectReport
from aegisops.projects.repository import ProjectRepository


def _make_run(claim: RunClaim, t_run, status="passed", truncated=False, no_tests=False):
    st = "no_tests" if no_tests else ("failed" if status == "issues" else "passed")
    ec = 5 if no_tests else (1 if status == "issues" else 0)
    c1 = ToolCheck(tool="ruff", status="passed", exit_code=0, output="ok", output_truncated=False)
    c2 = ToolCheck(tool="pytest", status=st, exit_code=ec, output="out", output_truncated=truncated)
    return RunResult(
        id=claim.id,
        project_id=claim.project_id,
        environment=claim.environment,
        profile=claim.profile,
        completed_at=t_run,
        status=status,
        reason="checks_completed",
        image_id="sha256:" + "a" * 64,
        checks=(c1, c2),
        container_removed=True,
        execution_scope="container",
        snapshot=SnapshotInfo(sha256="b" * 64, files=2, python_files=2, bytes=50, skipped=0),
    )


def _setup_fixtures(tmp_path: Path, canary: str = "CANARY_SECRET_DATA"):
    p_dir, r_dir, c_dir, a_dir = (
        tmp_path / "proj",
        tmp_path / "run",
        tmp_path / "chk",
        tmp_path / "ai",
    )
    proj_repo = ProjectRepository(p_dir)
    run_repo = RunRepository(r_dir)
    chk_repo = FullCheckRepository(c_dir, proj_repo, run_repo)
    ai_repo = FullAnalysisRepository(a_dir, chk_repo)

    code_dir = tmp_path / f"code_{canary}"
    code_dir.mkdir()
    (code_dir / f"main_{canary}.py").write_text(f"# {canary}\nx = 1\n")
    (code_dir / "docker-compose.yaml").write_text(f"services:\n  app:\n    image: {canary}\n")

    project = proj_repo.add(f"Sample_{canary}", str(code_dir))
    claim = chk_repo.create(project.id, "minimal")
    t0 = claim.created_at
    report = ProjectReport(
        id=uuid4(),
        project_id=project.id,
        started_at=t0 + timedelta(seconds=1),
        completed_at=t0 + timedelta(seconds=2),
        status="issues",
        files_seen=2,
        files_checked=2,
        skipped=0,
        stacks=("py",),
        findings=(
            Finding(
                severity="error",
                code="syntax",
                file=f"main_{canary}.py",
                message=f"syntax error {canary}",
            ),
        ),
        partial=False,
    )
    proj_repo.save_report(report)
    run = _make_run(claim, t0 + timedelta(seconds=3), status="issues", no_tests=True)
    run_repo.save(run)
    check = build_result(claim, report, run)
    chk_repo.save(check)
    return ai_repo, chk_repo, proj_repo, run_repo, project, claim, report, run, check, canary


@pytest.mark.unit
@pytest.mark.safety
def test_full_summary_projection_source_free_canary_absence(tmp_path: Path):
    ai_repo, chk_repo, _, _, project, claim, report, run, check, canary = _setup_fixtures(tmp_path)
    _, summary = ai_repo.evidence(check.id)

    assert summary.profile == FULL_PROFILE
    assert summary.profile != REVIEW_PROFILE
    assert summary.status == "issues"
    assert summary.source_match == "shared_snapshot"

    # Closed categories contain static finding and runner category
    cat_codes = {c.code for c in summary.categories}
    assert "syntax" in cat_codes
    assert "runner_no_tests" in cat_codes

    summary_json = summary.model_dump_json()
    # Strict verification: no raw names, paths, canary or foreign IDs leaked
    assert canary not in summary_json
    assert str(project.id) not in summary_json
    assert str(check.id) not in summary_json
    assert "docker-compose" not in summary_json

    with pytest.raises(ValidationError):
        FullSummary.model_validate_json(
            summary_json.replace('"status":"issues"', '"status":"issues","unknown_field":1')
        )


@pytest.mark.unit
@pytest.mark.safety
def test_projection_matrix_matches_and_precedence(tmp_path: Path):
    ai_repo, chk_repo, _, _, project, claim, report, run, check, _ = _setup_fixtures(tmp_path)
    # 1. Missing static report -> not_confirmed, incomplete
    chk_no_static = build_result(claim, None, run)
    sum_no_static = full_summary(chk_no_static, None, claim, run)
    assert sum_no_static.source_match == "not_confirmed"
    assert sum_no_static.status == "incomplete"
    assert "missing_static" in sum_no_static.limitations

    # 2. Incomplete precedence: static has issues, but runner had error
    run_err = RunResult(
        id=claim.id,
        project_id=claim.project_id,
        environment=claim.environment,
        profile=claim.profile,
        completed_at=claim.created_at + timedelta(seconds=4),
        status="error",
        reason="sandbox_failed",
        checks=(),
        container_removed=True,
        execution_scope="not_started",
        snapshot=run.snapshot,
    )
    chk_err = build_result(claim, report, run_err)
    assert chk_err.status == "incomplete"
    sum_err = full_summary(chk_err, report, claim, run_err)
    assert sum_err.status == "incomplete"

    # Missing snapshot on runner error
    run_err_no_snap = run_err.model_copy(update={"snapshot": None})
    chk_err_no_snap = build_result(claim, None, run_err_no_snap)
    sum_err_no_snap = full_summary(chk_err_no_snap, None, claim, run_err_no_snap)
    assert sum_err_no_snap.source_match == "not_confirmed"
    assert sum_err_no_snap.status == "incomplete"

    # 3. Pure projection rejects tampered sources or foreign project
    foreign_claim = RunClaim(id=claim.id, project_id=uuid4(), created_at=claim.created_at)
    with pytest.raises(ProjectError):
        full_summary(check, report, foreign_claim, run)


@pytest.mark.unit
@pytest.mark.safety
def test_full_analysis_repository_lifecycle_monotonic(tmp_path: Path):
    ai_repo, chk_repo, proj_repo, _, _, _, _, _, check, _ = _setup_fixtures(tmp_path)
    c_id = check.id

    # State initially not requested
    assert ai_repo.state(c_id) == {"status": "not_requested"}

    # Orphan started or result without claim fails closed
    ai_repo.write(c_id, "started", utc_now().isoformat())
    with pytest.raises(ProjectError):
        ai_repo.claim(c_id)
    with pytest.raises(ProjectError):
        ai_repo.state(c_id)
    (ai_repo.files.path(c_id, "started")).unlink()

    # Claim once ever
    full_claim = ai_repo.claim(c_id)
    assert full_claim.check_id == c_id
    with pytest.raises(ProjectError):
        ai_repo.claim(c_id)

    assert ai_repo.state(c_id)["status"] == "pending"

    # Completed state with valid response
    good_expl = ReviewExplanation(
        summary="Обнаружены ошибки синтаксиса и отсутствие тестов",
        actions=(
            ReviewAdvice(
                code="syntax", priority="high", explanation="Синтаксис", next_step="Исправить"
            ),
        ),
        limitations="Проверка ограничена выбранным профилем",
    )
    res_data = {
        "check_id": str(c_id),
        "check_sha256": full_claim.check_sha256,
        "completed_at": utc_now().isoformat(),
        "status": "completed",
        "model": "deepseek/deepseek-v4.1-flash",
        "response": good_expl.model_dump(mode="json"),
        "input_tokens": 100,
        "output_tokens": 50,
        "execution_performed": False,
    }
    ai_repo.write(c_id, "result", json.dumps(res_data))
    assert ai_repo.state(c_id)["status"] == "completed"

    # Fail is monotonic: cannot overwrite completed
    ai_repo.fail(c_id)
    assert ai_repo.state(c_id)["status"] == "completed"

    # Attempting to overwrite existing result with ai_repo.write raises FileExistsError
    with pytest.raises(FileExistsError):
        ai_repo.write(c_id, "result", json.dumps(res_data))

    # Result with unknown action code rejected (bypass O_EXCL to test corruption/tamper rejection)
    bad_expl = good_expl.model_copy(
        update={
            "actions": (
                ReviewAdvice(
                    code="runner_timeout", priority="high", explanation="Не было", next_step="Нет"
                ),
            )
        }
    )
    res_data["response"] = bad_expl.model_dump(mode="json")
    ai_repo.files.path(c_id, "result").write_text(json.dumps(res_data), encoding="utf-8")
    with pytest.raises(ProjectError):
        ai_repo.read_result(c_id)

    # Result with completed_at prior to claim rejected
    res_data["response"] = good_expl.model_dump(mode="json")
    res_data["completed_at"] = (full_claim.created_at - timedelta(seconds=10)).isoformat()
    ai_repo.files.path(c_id, "result").write_text(json.dumps(res_data), encoding="utf-8")
    with pytest.raises(ProjectError):
        ai_repo.read_result(c_id)


@pytest.mark.unit
@pytest.mark.safety
def test_explain_full_transport_guards_and_prompts(tmp_path: Path):
    ai_repo, _, _, _, _, _, _, _, check, canary = _setup_fixtures(tmp_path)
    _, summary = ai_repo.evidence(check.id)

    good_resp = {
        "summary": "Сводка на русском",
        "actions": [
            {
                "code": "syntax",
                "priority": "high",
                "explanation": "Ошибка синтаксиса",
                "next_step": "Проверить файл",
            }
        ],
        "limitations": "Ограничения снимка",
    }

    captured_prompts = []

    def mock_exchange(payload_json: str, instructions: str, key: SecretStr):
        captured_prompts.append((payload_json, instructions))
        return good_resp, 10, 20

    key = SecretStr(f"secret-key-{canary}")
    with patch.object(transport, "_exchange", side_effect=mock_exchange):
        expl, inp, out = explain_full(summary, key)
        assert expl.actions[0].code == "syntax"
        assert inp == 10 and out == 20

    sent_payload, sent_inst = captured_prompts[0]
    assert "русском языке" in sent_inst
    assert "Не предлагай исполнение команд" in sent_inst
    assert canary not in sent_payload
    assert canary not in sent_inst

    # Unknown action code rejected by transport
    bad_resp = dict(good_resp)
    bad_resp["actions"] = [
        {"code": "unknown_code", "priority": "high", "explanation": "err", "next_step": "step"}
    ]
    with patch.object(transport, "_exchange", return_value=(bad_resp, 1, 1)):
        with pytest.raises(transport.AnalysisError):
            explain_full(summary, key)

    # Empty categories gating: actions must be empty
    empty_sum = summary.model_copy(update={"categories": ()})
    with patch.object(transport, "_exchange", return_value=(good_resp, 1, 1)):
        with pytest.raises(transport.AnalysisError):
            explain_full(empty_sum, key)

    # Provider exception safety (no key leak)
    def failing_exchange(*args, **kwargs):
        raise RuntimeError(f"Sensitive error with key {canary}")

    with patch.object(transport, "_exchange", side_effect=failing_exchange):
        with pytest.raises(transport.AnalysisError) as exc_info:
            explain_full(summary, key)
        assert canary not in str(exc_info.value)


@pytest.mark.unit
@pytest.mark.safety
def test_pending_state_for_fresh_check(tmp_path: Path):
    ai_repo, chk_repo, _, _, project, _, _, _, _, _ = _setup_fixtures(tmp_path)
    fresh_claim = chk_repo.create(project.id, "minimal")
    with pytest.raises(ProjectError):
        ai_repo.state(fresh_claim.id)
    with pytest.raises(ProjectError):
        ai_repo.claim(fresh_claim.id)
    assert ai_repo.read_claim(fresh_claim.id) is None


@pytest.mark.unit
@pytest.mark.safety
def test_static_report_tampering_raises_project_error(tmp_path: Path):
    ai_repo, _, proj_repo, _, _, _, report, _, check, _ = _setup_fixtures(tmp_path)
    tampered_findings = (
        report.findings[0].model_copy(update={"message": "tampered finding message"}),
    )
    tampered_report = report.model_copy(update={"findings": tampered_findings})
    ProjectReport.model_validate(tampered_report.model_dump())
    rep_path = proj_repo.directory / "reports" / f"{report.id}.json"
    rep_path.write_text(tampered_report.model_dump_json(), encoding="utf-8")
    with pytest.raises(ProjectError):
        ai_repo.state(check.id)


@pytest.mark.unit
@pytest.mark.safety
def test_explain_full_absent_valid_enum_action_raises_analysis_error(tmp_path: Path):
    ai_repo, _, _, _, _, _, _, _, check, canary = _setup_fixtures(tmp_path)
    _, summary = ai_repo.evidence(check.id)
    bad_resp = {
        "summary": "Сводка",
        "actions": [
            {
                "code": "runner_timeout",
                "priority": "high",
                "explanation": "Таймаут",
                "next_step": "Повторить",
            }
        ],
        "limitations": "Ограничения",
    }
    key = SecretStr(f"secret-key-{canary}")
    with patch.object(transport, "_exchange", return_value=(bad_resp, 1, 1)):
        with pytest.raises(transport.AnalysisError):
            explain_full(summary, key)


@pytest.mark.unit
@pytest.mark.safety
def test_standalone_restart_valid_result_persisted(tmp_path: Path):
    ai_repo, _, _, _, _, _, _, _, check, _ = _setup_fixtures(tmp_path)
    c_id = check.id
    full_claim = ai_repo.claim(c_id)
    good_expl = ReviewExplanation(
        summary="Обнаружены ошибки синтаксиса и отсутствие тестов",
        actions=(
            ReviewAdvice(
                code="syntax", priority="high", explanation="Синтаксис", next_step="Исправить"
            ),
        ),
        limitations="Проверка ограничена выбранным профилем",
    )
    res_data = {
        "check_id": str(c_id),
        "check_sha256": full_claim.check_sha256,
        "completed_at": utc_now().isoformat(),
        "status": "completed",
        "model": "deepseek/deepseek-v4.1-flash",
        "response": good_expl.model_dump(mode="json"),
        "input_tokens": 10,
        "output_tokens": 20,
        "execution_performed": False,
    }
    ai_repo.write(c_id, "result", json.dumps(res_data))
    restarted_repo = FullAnalysisRepository(ai_repo.files.directory, ai_repo.checks)
    state = restarted_repo.state(c_id)
    assert state["status"] == "completed"
    res = restarted_repo.read_result(c_id)
    assert res.check_id == c_id
    assert res.status == "completed"
    assert res.response.actions[0].code == "syntax"
