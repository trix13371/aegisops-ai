import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aegisops.project_code.evaluation_repository import EvalRepository
from aegisops.project_code.models import CodeReview
from aegisops.projects.repository import ProjectRepository

ROOT = Path(__file__).resolve().parents[2]
CANARY = "PRIVACY_CANARY_DO_NOT_LEAK"


def _load_worker():
    script_path = ROOT / "scripts" / "project-code-evaluation-worker.py"
    spec = importlib.util.spec_from_file_location("code_eval_worker", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def worker_env(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.argv", ["project-code-evaluation-worker.py"])
    projects = ProjectRepository(tmp_path / "projects")
    repository = EvalRepository(tmp_path / "eval", projects)

    worker = _load_worker()
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(
        worker,
        "ProjectRepository",
        lambda path: projects,
    )
    monkeypatch.setattr(
        worker,
        "EvalRepository",
        lambda path, prj: repository,
    )
    monkeypatch.setenv("AEGISOPS_LLM_API_KEY", "offline")
    return worker, repository


def test_worker_v2_completes_nine_calls_and_saves_exact_usage(worker_env, monkeypatch, capsys):
    worker, repo = worker_env
    claim = repo.create("synthetic-file-overlap-v2")
    monkeypatch.setattr(sys, "argv", ["worker.py", str(claim.id)])

    call_count = 0

    def fake_explain(preview, key):
        nonlocal call_count
        call_count += 1
        return (
            CodeReview(summary="Synthetic response", findings=(), limitations="Fixture only"),
            10,
            5,
        )

    monkeypatch.setattr(worker, "explain_code", fake_explain)

    exit_code = worker.main()
    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out.strip()) == {"ok": True}
    assert call_count == 9

    saved = repo.result(claim.id)
    assert saved is not None
    assert saved.status == "completed"
    assert len(saved.answers) == 9
    assert len(saved.usage) == 9
    for case_id, usage in saved.usage.items():
        assert usage.input_tokens == 10
        assert usage.output_tokens == 5

    # Replay: already completed suite must make 0 calls and fail closed
    replay_calls = 0

    def counting_replay_explain(preview, key):
        nonlocal replay_calls
        replay_calls += 1
        return (
            CodeReview(summary="Synthetic response", findings=(), limitations="Fixture only"),
            10,
            5,
        )

    monkeypatch.setattr(worker, "explain_code", counting_replay_explain)
    replay_exit = worker.main()
    captured_replay = capsys.readouterr()
    assert replay_exit == 1
    assert json.loads(captured_replay.out.strip()) == {"ok": False}
    assert replay_calls == 0


def test_worker_v2_fails_third_call_without_retry(worker_env, monkeypatch, capsys):
    worker, repo = worker_env
    claim = repo.create("synthetic-file-overlap-v2")
    monkeypatch.setattr(sys, "argv", ["worker.py", str(claim.id)])

    calls = 0

    def failing_explain(preview, key):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError(CANARY)
        return (
            CodeReview(summary="Synthetic response", findings=(), limitations="Fixture only"),
            10,
            5,
        )

    monkeypatch.setattr(worker, "explain_code", failing_explain)

    exit_code = worker.main()
    captured = capsys.readouterr()
    assert exit_code == 1
    assert json.loads(captured.out.strip()) == {"ok": False}
    assert calls == 3
    assert CANARY not in captured.out
    assert CANARY not in captured.err

    saved = repo.result(claim.id)
    assert saved is not None
    assert saved.status == "failed"
    assert len(saved.answers) == 2
    assert len(saved.usage) == 2
    report = saved.report
    assert report["complete"] is False
    assert report["overall_precision"] is None
    assert report["overall_recall"] is None

    # Assert canary wasn't persisted in storage
    raw_record = repo._read(claim.id, "result", 524288)
    assert CANARY not in str(raw_record)


def test_worker_v2_invalid_usage_stops_without_adding_answer(worker_env, monkeypatch, capsys):
    worker, repo = worker_env
    claim = repo.create("synthetic-file-overlap-v2")
    monkeypatch.setattr(sys, "argv", ["worker.py", str(claim.id)])

    calls = 0

    def bad_usage_explain(preview, key):
        nonlocal calls
        calls += 1
        if calls == 2:
            # bool is invalid for StrictInt
            return (
                CodeReview(summary="Synthetic response", findings=(), limitations="Fixture only"),
                True,
                5,
            )
        return (
            CodeReview(summary="Synthetic response", findings=(), limitations="Fixture only"),
            10,
            5,
        )

    monkeypatch.setattr(worker, "explain_code", bad_usage_explain)

    exit_code = worker.main()
    captured = capsys.readouterr()
    assert exit_code == 1
    assert json.loads(captured.out.strip()) == {"ok": False}
    assert calls == 2

    saved = repo.result(claim.id)
    assert saved is not None
    assert saved.status == "failed"
    assert len(saved.answers) == 1
    assert len(saved.usage) == 1


def test_worker_v1_preserved_three_calls(worker_env, monkeypatch, capsys):
    worker, repo = worker_env
    claim = repo.create("synthetic-line-overlap-v1")
    monkeypatch.setattr(sys, "argv", ["worker.py", str(claim.id)])

    calls = 0

    def explain(preview, key):
        nonlocal calls
        calls += 1
        return (
            CodeReview(summary="Synthetic response", findings=(), limitations="Fixture only"),
            10,
            5,
        )

    monkeypatch.setattr(worker, "explain_code", explain)

    exit_code = worker.main()
    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out.strip()) == {"ok": True}
    assert calls == 3

    saved = repo.result(claim.id)
    assert saved is not None
    assert saved.status == "completed"
    assert len(saved.answers) == 3
    assert not hasattr(saved, "usage")


def test_worker_fixture_mismatch_or_too_many_cases_zero_calls(worker_env, monkeypatch, capsys):
    worker, repo = worker_env
    claim = repo.create("synthetic-file-overlap-v2")
    monkeypatch.setattr(sys, "argv", ["worker.py", str(claim.id)])

    calls = 0

    def explain(preview, key):
        nonlocal calls
        calls += 1
        return (
            CodeReview(summary="Synthetic response", findings=(), limitations="Fixture only"),
            10,
            5,
        )

    monkeypatch.setattr(worker, "explain_code", explain)

    # 1. Changed fixture
    monkeypatch.setattr(
        worker,
        "_fixture_digest",
        lambda profile: (
            "mutated_hash_0000000000000000000000000000000000000000000000000000000000000000"
        ),
    )
    exit_code = worker.main()
    captured = capsys.readouterr()
    assert exit_code == 1
    assert json.loads(captured.out.strip()) == {"ok": False}
    assert calls == 0

    # 2. Too many cases
    claim2 = repo.create("synthetic-file-overlap-v2")
    monkeypatch.setattr(sys, "argv", ["worker.py", str(claim2.id)])
    monkeypatch.setattr(worker, "_fixture_digest", lambda profile: claim2.fixture_sha256)
    fake_cases = tuple(SimpleNamespace(id=f"extra-{i}") for i in range(15))
    monkeypatch.setattr(worker, "_cases", lambda profile: fake_cases)

    exit_code2 = worker.main()
    captured2 = capsys.readouterr()
    assert exit_code2 == 1
    assert json.loads(captured2.out.strip()) == {"ok": False}
    assert calls == 0


def test_worker_v2_analysis_error_timeout_preserves_two_answers_and_failure(
    worker_env, monkeypatch, capsys
):
    worker, repo = worker_env
    claim = repo.create("synthetic-file-overlap-v2")
    monkeypatch.setattr(sys, "argv", ["worker.py", str(claim.id)])

    cases = worker._cases("synthetic-file-overlap-v2")
    third_case_id = cases[2].id
    calls = 0

    def failing_explain(preview, key):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise worker.AnalysisError("timeout")
        return (
            CodeReview(summary="Synthetic response", findings=(), limitations="Fixture only"),
            10,
            5,
        )

    monkeypatch.setattr(worker, "explain_code", failing_explain)

    exit_code = worker.main()
    captured = capsys.readouterr()
    assert exit_code == 1
    assert json.loads(captured.out.strip()) == {"ok": False}
    assert calls == 3
    assert CANARY not in captured.out
    assert CANARY not in captured.err

    saved = repo.result(claim.id)
    assert saved is not None
    assert saved.status == "failed"
    assert len(saved.answers) == 2
    assert len(saved.usage) == 2
    assert saved.failure is not None
    assert saved.failure.code == "timeout"
    assert saved.failure.case_id == third_case_id

    raw_record = repo._read(claim.id, "result", 524288)
    assert CANARY not in str(raw_record)


def test_worker_v2_unknown_failure_code_falls_back_to_unavailable(worker_env, monkeypatch, capsys):
    worker, repo = worker_env
    claim = repo.create("synthetic-file-overlap-v2")
    monkeypatch.setattr(sys, "argv", ["worker.py", str(claim.id)])

    cases = worker._cases("synthetic-file-overlap-v2")
    first_case_id = cases[0].id
    calls = 0
    malicious_code = f"<script>{CANARY}</script>"

    def failing_explain(preview, key):
        nonlocal calls
        calls += 1
        err = worker.AnalysisError("network")
        err.code = malicious_code
        raise err

    monkeypatch.setattr(worker, "explain_code", failing_explain)

    exit_code = worker.main()
    captured = capsys.readouterr()
    assert exit_code == 1
    assert json.loads(captured.out.strip()) == {"ok": False}
    assert calls == 1
    assert CANARY not in captured.out
    assert CANARY not in captured.err

    saved = repo.result(claim.id)
    assert saved is not None
    assert saved.status == "failed"
    assert len(saved.answers) == 0
    assert len(saved.usage) == 0
    assert saved.failure is not None
    assert saved.failure.code == "unavailable"
    assert saved.failure.case_id == first_case_id

    raw_record = repo._read(claim.id, "result", 524288)
    assert CANARY not in str(raw_record)
