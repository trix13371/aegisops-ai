"""Full check worker runs single-attempt AI inference for claimed full checks."""

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import SecretStr

from aegisops.domain.common import utc_now
from aegisops.project_ai.full_repository import FullAnalysisRepository
from aegisops.project_ai.review_models import ReviewExplanation
from aegisops.project_checks.models import build_result
from aegisops.project_checks.repository import FullCheckRepository
from aegisops.project_checks.snapshot import scan_snapshot
from aegisops.project_runner.models import RunResult, ToolCheck
from aegisops.project_runner.repository import RunRepository
from aegisops.project_runner.snapshot import snapshot_project
from aegisops.projects.models import ProjectReport
from aegisops.projects.repository import ProjectRepository

ROOT = Path(__file__).resolve().parents[2]
CANARY = "PRIVATE_SOURCE_CANARY"
pytestmark = [pytest.mark.unit, pytest.mark.safety]

ADVICE = {
    "summary": "Обнаружены привилегированные параметры контейнера.",
    "actions": [
        {
            "code": "privileged",
            "priority": "high",
            "explanation": "Контейнер запускается в привилегированном режиме.",
            "next_step": "Отключите privileged: true в compose-файле.",
        }
    ],
    "limitations": "Проверена только предоставленная сводка результатов.",
}


@pytest.fixture
def case(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "compose.yaml").write_text("services:\n  app:\n    privileged: true\n")
    (source / "test_sample.py").write_text("def test_ok(): pass\n")
    projects = ProjectRepository(tmp_path / ".local/projects")
    project = projects.add(CANARY, str(source))
    runs = RunRepository(tmp_path / ".local/project-runs")
    checks = FullCheckRepository(tmp_path / ".local/project-checks", projects, runs)
    claim = checks.create(project.id, "minimal")

    snapshot = snapshot_project(project, claim.profile)
    report = scan_snapshot(project.id, snapshot)
    projects.save_report(report)

    run = RunResult(
        id=claim.id,
        project_id=project.id,
        completed_at=utc_now(),
        status="passed",
        reason="checks_completed",
        snapshot=snapshot.info,
        image_id="sha256:" + "a" * 64,
        checks=(
            ToolCheck(
                tool="ruff", status="passed", exit_code=0, output="ok", output_truncated=False
            ),
            ToolCheck(
                tool="pytest",
                status="passed",
                exit_code=0,
                output="1 passed",
                output_truncated=False,
            ),
        ),
        container_removed=True,
        execution_scope="container",
    )
    runs.save(run)

    check = build_result(claim, report, run)
    checks.save(check)

    ai_repo = FullAnalysisRepository(tmp_path / ".local/project-check-ai", checks)
    spec = importlib.util.spec_from_file_location(
        "project_check_ai_worker", ROOT / "scripts/project-check-ai-worker.py"
    )
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(worker.sys, "argv", ["worker", str(check.id)])
    monkeypatch.setenv("AEGISOPS_LLM_API_KEY", "test-key-string")
    return projects, runs, report, run, checks, check, ai_repo, worker


def test_worker_success_once_and_persisted(case, monkeypatch, capsys):
    projects, _, report, _, _, check, ai_repo, worker = case
    source_file = Path(projects.get(report.project_id).path) / "compose.yaml"
    before = source_file.read_bytes()
    calls = []

    def provider(summary, key):
        assert isinstance(key, SecretStr)
        assert key.get_secret_value() == "test-key-string"
        assert CANARY not in summary.model_dump_json()
        calls.append(summary)
        return ReviewExplanation.model_validate(ADVICE), 10, 20

    monkeypatch.setattr(worker, "explain_full", provider)
    ai_repo.claim(check.id)
    assert worker.main() == 0
    assert capsys.readouterr().out.strip() == '{"ok": true}'
    assert len(calls) == 1
    result = ai_repo.read_result(check.id)
    assert result is not None
    assert result.status == "completed"
    assert result.input_tokens == 10 and result.output_tokens == 20
    assert result.execution_performed is False
    assert {a.code for a in result.response.actions} <= {
        c.code for c in ai_repo.read_claim(check.id).payload.categories
    }
    assert worker.main() == 1
    assert capsys.readouterr().out.strip() == '{"ok": false}'
    assert len(calls) == 1
    assert source_file.read_bytes() == before


def test_started_marker_prevents_replay(case, monkeypatch, capsys):
    *_, check, ai_repo, worker = case
    ai_repo.claim(check.id)
    ai_repo.write(check.id, "started", "{}")

    def forbidden(*args):
        pytest.fail("forbidden replay")

    monkeypatch.setattr(worker, "explain_full", forbidden)
    assert worker.main() == 1
    assert capsys.readouterr().out.strip() == '{"ok": false}'
    assert ai_repo.state(check.id)["status"] == "pending"
    assert ai_repo.read_result(check.id) is None


def test_provider_failure_persists_fail_record(case, monkeypatch, capsys):
    *_, check, ai_repo, worker = case
    ai_repo.claim(check.id)
    calls = []

    def failure(*args):
        calls.append(True)
        raise RuntimeError(CANARY)

    monkeypatch.setattr(worker, "explain_full", failure)
    assert worker.main() == 1
    assert ai_repo.read_result(check.id).status == "failed"
    assert worker.main() == 1
    assert len(calls) == 1
    out = capsys.readouterr()
    assert out.out == '{"ok": false}\n{"ok": false}\n'
    assert CANARY not in out.out and not out.err


@pytest.mark.parametrize(
    "tamper", ["report_message", "run_output", "claim_payload", "check_digest"]
)
def test_tampered_sources_rejected_before_start(case, monkeypatch, capsys, tamper):
    projects, runs, report, run, _, check, ai_repo, worker = case
    claim = ai_repo.claim(check.id)
    if tamper == "report_message":
        data = report.model_dump()
        data["findings"] = (report.findings[0].model_copy(update={"message": "modified"}),)
        (projects.directory / "reports" / f"{report.id}.json").write_text(
            ProjectReport.model_validate(data).model_dump_json()
        )
    elif tamper == "run_output":
        data = run.model_dump()
        data["checks"] = (run.checks[0].model_copy(update={"output": "changed"}), run.checks[1])
        runs.path(run.id, "result").write_text(RunResult.model_validate(data).model_dump_json())
    elif tamper == "claim_payload":
        data = claim.model_dump(mode="json")
        data["payload"]["categories"][0]["count"] += 1
        ai_repo.files.path(check.id, "claim").write_text(json.dumps(data))
    elif tamper == "check_digest":
        data = claim.model_dump(mode="json")
        data["check_sha256"] = "0" * 64
        ai_repo.files.path(check.id, "claim").write_text(json.dumps(data))

    monkeypatch.setattr(
        worker, "explain_full", lambda *a: pytest.fail("provider invoked on tampered input")
    )
    assert worker.main() == 1
    assert capsys.readouterr().out.strip() == '{"ok": false}'
    assert not ai_repo.files.path(check.id, "started").exists()
    assert ai_repo.read_result(check.id) is None


def test_evidence_changed_during_provider_records_failure(case, monkeypatch, capsys):
    projects, _, report, _, _, check, ai_repo, worker = case
    ai_repo.claim(check.id)

    def provider(*args):
        data = report.model_dump()
        data["findings"] = (report.findings[0].model_copy(update={"message": "modified-during"}),)
        (projects.directory / "reports" / f"{report.id}.json").write_text(
            ProjectReport.model_validate(data).model_dump_json()
        )
        return ReviewExplanation.model_validate(ADVICE), 10, 20

    monkeypatch.setattr(worker, "explain_full", provider)
    assert worker.main() == 1
    assert capsys.readouterr().out.strip() == '{"ok": false}'
    result = ai_repo.read_result(check.id)
    assert result is not None and result.status == "failed"


def test_unclaimed_or_invalid_args_prevent_inference(case, monkeypatch, capsys):
    *_, check, ai_repo, worker = case
    monkeypatch.setattr(worker, "explain_full", lambda *a: pytest.fail("provider invoked"))
    assert worker.main() == 1
    for args in (["worker"], ["worker", CANARY], ["worker", str(check.id), "extra"]):
        monkeypatch.setattr(worker.sys, "argv", args)
        assert worker.main() == 1
    assert not ai_repo.files.path(check.id, "started").exists()
    assert ai_repo.read_result(check.id) is None
    assert CANARY not in capsys.readouterr().out
