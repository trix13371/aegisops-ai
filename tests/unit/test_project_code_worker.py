import importlib.util
import json
from pathlib import Path

import pytest

from aegisops.project_code.models import CodeFinding, CodeReview
from aegisops.project_code.repository import CodeRepository
from aegisops.projects.repository import ProjectRepository

pytestmark = [pytest.mark.unit, pytest.mark.safety]


def _load_worker_module():
    worker_path = Path(__file__).resolve().parents[2] / "scripts" / "project-code-ai-worker.py"
    spec = importlib.util.spec_from_file_location("project_code_ai_worker", worker_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def setup_env(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "proj_src"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "demo.py").write_text("msg = 'hello'\n", encoding="utf-8")

    proj_storage = tmp_path / ".local" / "projects"
    proj_repo = ProjectRepository(proj_storage)
    proj = proj_repo.add("demo", str(workspace))

    code_repo = CodeRepository(tmp_path / ".local" / "project-code-ai", proj_repo)
    preview = code_repo.create(proj.id, ["demo.py"])

    worker = _load_worker_module()
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setenv("AEGISOPS_LLM_API_KEY", "fake-test-key-1234567890")
    return worker, code_repo, preview


def test_worker_success_writes_once_and_restart_blocks(setup_env, monkeypatch, capsys):
    worker, code_repo, preview = setup_env
    code_repo.claim(preview.id)

    call_count = 0

    def mock_explain(p, key):
        nonlocal call_count
        call_count += 1
        return (
            CodeReview(
                summary="Good code",
                findings=(
                    CodeFinding(
                        file_id="f1",
                        start_line=1,
                        end_line=1,
                        severity="info",
                        category="maintainability",
                        title="Message",
                        evidence="msg = 'hello'",
                        explanation="Greeting variable",
                        recommendation="Keep as is",
                    ),
                ),
                limitations="None",
            ),
            50,
            20,
        )

    monkeypatch.setattr(worker, "explain_code", mock_explain)
    monkeypatch.setattr("sys.argv", ["worker", str(preview.id)])

    # First run succeeds
    rc1 = worker.main()
    captured1 = capsys.readouterr()
    assert rc1 == 0
    assert json.loads(captured1.out.strip()) == {"ok": True}
    assert call_count == 1

    res1 = code_repo.read_result(preview.id)
    assert res1 is not None
    assert res1.status == "completed"

    # Restarting worker does not call API again
    rc2 = worker.main()
    captured2 = capsys.readouterr()
    assert rc2 == 1
    assert json.loads(captured2.out.strip()) == {"ok": False}
    assert call_count == 1


def test_worker_before_unclaimed_rejects_without_api_call(setup_env, monkeypatch, capsys):
    worker, code_repo, preview = setup_env
    # Preview exists but NOT claimed
    api_called = False

    def mock_explain(p, key):
        nonlocal api_called
        api_called = True
        raise RuntimeError("Should not be called")

    monkeypatch.setattr(worker, "explain_code", mock_explain)
    monkeypatch.setattr("sys.argv", ["worker", str(preview.id)])

    rc = worker.main()
    captured = capsys.readouterr()
    assert rc == 1
    assert json.loads(captured.out.strip()) == {"ok": False}
    assert not api_called
    assert code_repo.read_result(preview.id) is None


def test_worker_provider_failure_persists_failed_no_retry(setup_env, monkeypatch, capsys):
    worker, code_repo, preview = setup_env
    code_repo.claim(preview.id)

    call_count = 0

    def mock_explain_fail(p, key):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("Provider 500 error")

    monkeypatch.setattr(worker, "explain_code", mock_explain_fail)
    monkeypatch.setattr("sys.argv", ["worker", str(preview.id)])

    rc = worker.main()
    captured = capsys.readouterr()
    assert rc == 1
    assert json.loads(captured.out.strip()) == {"ok": False}
    assert call_count == 1

    # Result persisted as failed
    res = code_repo.read_result(preview.id)
    assert res is not None
    assert res.status == "failed"


def test_worker_preexisting_started_blocks_and_does_not_overwrite(setup_env, monkeypatch, capsys):
    worker, code_repo, preview = setup_env
    code_repo.claim(preview.id)
    # Pre-existing started marker (another worker in progress)
    code_repo.write(preview.id, "started", "{}")

    api_called = False

    def mock_explain(p, key):
        nonlocal api_called
        api_called = True
        return CodeReview(summary="X", limitations="Y"), 1, 1

    monkeypatch.setattr(worker, "explain_code", mock_explain)
    monkeypatch.setattr("sys.argv", ["worker", str(preview.id)])

    rc = worker.main()
    captured = capsys.readouterr()
    assert rc == 1
    assert json.loads(captured.out.strip()) == {"ok": False}
    assert not api_called
    # Worker must not overwrite status to failed because it did not own the start
    assert code_repo.read_result(preview.id) is None


def test_worker_tamper_during_response(monkeypatch, setup_env):
    import json
    import sys

    from aegisops.project_code.models import CodeReview

    worker, code_repo, preview = setup_env
    code_repo.claim(preview.id)

    call_count = 0

    def fake_explain(prev, key):
        nonlocal call_count
        call_count += 1
        p_path = code_repo.path(preview.id, "preview")
        data = json.loads(p_path.read_text())
        data["files"][0]["path"] = "mutated.py"
        p_path.write_text(json.dumps(data))
        return CodeReview(summary="Review", findings=(), limitations="Selected only"), 1, 1

    monkeypatch.setattr(worker, "explain_code", fake_explain)
    monkeypatch.setattr(sys, "argv", ["worker", str(preview.id)])

    assert worker.main() == 1
    assert call_count == 1
    assert not code_repo.path(preview.id, "result").exists()

    assert worker.main() == 1
    assert call_count == 1
