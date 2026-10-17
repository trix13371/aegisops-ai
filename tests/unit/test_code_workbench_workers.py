"""Offline integration tests for code workbench, verify, and eval workers."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import test_code_workbench as base_wb

from aegisops.domain.common import utc_now
from aegisops.project_code.evaluation import CASES
from aegisops.project_code.evaluation_repository import EvalRepository
from aegisops.project_code.lab import (
    LabRepository,
    Proposal,
    validate_proposal,
    verification_snapshots,
)
from aegisops.project_code.models import (
    CodeClaim,
    CodeFinding,
    CodeResult,
    CodeReview,
    preview_digest,
)
from aegisops.project_code.repository import CodeRepository
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.repository import ProjectRepository

ROOT_DIR = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    p = ROOT_DIR / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _setup_claimed_preview(tmp_path: Path):
    p_dir = tmp_path / "proj"
    p_dir.mkdir(parents=True, exist_ok=True)
    code_file = p_dir / "run.py"
    code_file.write_text("def run():\n    return 0\n")
    p_repo = ProjectRepository(tmp_path / ".local/projects")
    proj = p_repo.add("test_p", str(p_dir))
    c_repo = CodeRepository(tmp_path / ".local/project-code-ai", p_repo)
    prev = c_repo.create(proj.id, ["run.py"])
    c_now = utc_now()
    c_repo.write(
        prev.id,
        "claim",
        CodeClaim(
            preview_id=prev.id, preview_sha256=preview_digest(prev), created_at=c_now
        ).model_dump_json(),
    )
    rev = base_wb._make_review(prev.files[0].id, "return 0", start=2, end=2)
    c_repo.write(
        prev.id,
        "result",
        CodeResult(
            preview_id=prev.id,
            preview_sha256=preview_digest(prev),
            completed_at=c_now,
            status="completed",
            response=rev,
        ).model_dump_json(),
    )
    lab = LabRepository(tmp_path / ".local/project-code-lab", c_repo)
    lab.claim(prev.id, 0)
    return proj, prev, rev, lab


def test_fix_worker_replay_and_failure(tmp_path: Path, monkeypatch) -> None:
    proj, prev, rev, lab = _setup_claimed_preview(tmp_path)
    worker = _load_script("project-code-fix-worker")
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["worker", str(prev.id)])

    valid_p = Proposal(
        file_id=prev.files[0].id,
        replacement="def run():\n    return 1\n",
        test_language="python",
        test_code="def test_run():\n    pass\n",
        explanation="Fixed return",
    )
    mock_exchange = MagicMock(return_value=(valid_p.model_dump(mode="json"), 12, 34))
    monkeypatch.setattr("aegisops.project_code.lab_transport._exchange", mock_exchange)

    assert worker.main() == 0
    assert mock_exchange.call_count == 1
    res = lab.result(prev.id)
    assert res is not None and res.status == "completed"
    assert res.proposal == valid_p

    # Replay: should exit without second exchange
    assert worker.main() == 1
    assert mock_exchange.call_count == 1

    # Failed proposal validation marks failed
    _, prev2, _, lab2 = _setup_claimed_preview(tmp_path / "other")
    monkeypatch.setattr(worker, "ROOT", tmp_path / "other")
    monkeypatch.setattr(sys, "argv", ["worker", str(prev2.id)])
    monkeypatch.setattr(
        "aegisops.project_code.lab_transport._exchange",
        MagicMock(side_effect=RuntimeError("transport fail")),
    )
    assert worker.main() == 1
    res2 = lab2.result(prev2.id)
    assert res2 is not None and res2.status == "failed"


def test_verify_worker_lifecycle_and_replay(tmp_path: Path, monkeypatch) -> None:
    proj, prev, rev, lab = _setup_claimed_preview(tmp_path)
    prop = Proposal(
        file_id=prev.files[0].id,
        replacement="def run():\n    return 1\n",
        test_language="python",
        test_code="def test_run():\n    pass\n",
        explanation="Fixed",
    )
    valid_p = validate_proposal(prev, prop, 0, rev)
    lab_res = base_wb.LabResult(
        preview_id=prev.id,
        preview_sha256=preview_digest(prev),
        review_sha256=lab.read_claim(prev.id).review_sha256,
        completed_at=utc_now(),
        status="completed",
        proposal=valid_p,
    )
    lab._write(prev.id, "result", lab_res.model_dump_json())
    v_claim = lab.verify_claim(prev.id, "minimal")

    worker = _load_script("project-code-verify-worker")
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["worker", str(prev.id)])

    b_snap, a_snap = verification_snapshots(prev, valid_p, "minimal")
    runs_repo = RunRepository(tmp_path / ".local/project-runs")
    run_calls = []

    def fake_run_check(run_id, prepared_snapshot):
        run_calls.append(prepared_snapshot.info.sha256)
        is_before = prepared_snapshot.info.sha256 == b_snap.info.sha256
        st = "failed" if is_before else "passed"
        ec = 1 if is_before else 0
        rr = base_wb._make_run(
            run_id, proj.id, "minimal", prepared_snapshot.info.sha256, st, ec, utc_now()
        )
        runs_repo.write(run_id, "result", rr.model_dump_json())
        return rr

    monkeypatch.setattr(worker, "_load_runner", lambda: SimpleNamespace(run_check=fake_run_check))

    assert worker.main() == 0
    assert len(run_calls) == 2
    v_res = lab.verification(prev.id)
    assert v_res is not None and v_res["status"] == "completed"
    v_obj = base_wb.VerificationResult.model_validate(v_res["result"])
    assert v_obj.status == "reproduced"
    base_wb.validate_verification(prev, valid_p, v_claim, v_obj)

    # Replay: should not run runner again
    assert worker.main() == 1
    assert len(run_calls) == 2


def test_eval_worker_saves_three_cases_and_no_replay(tmp_path: Path, monkeypatch) -> None:
    p_repo = ProjectRepository(tmp_path / ".local/projects")
    eval_repo = EvalRepository(tmp_path / ".local/project-code-eval", p_repo)
    claim = eval_repo.create()

    worker = _load_script("project-code-evaluation-worker")
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["worker", str(claim.id)])

    def fake_explain_code(prev, key):
        if "range(len(values) + 1)" in prev.files[0].content:
            fid = prev.files[0].id
            f = CodeFinding(
                file_id=fid,
                start_line=3,
                end_line=4,
                severity="error",
                category="correctness",
                title="Loop",
                evidence="total += values[index]",
                explanation="past end",
                recommendation="fix",
            )
            return CodeReview(summary="bug", findings=(f,), limitations="none"), 5, 5
        return CodeReview(summary="clean", findings=(), limitations="none"), 5, 5

    monkeypatch.setattr(worker, "explain_code", fake_explain_code)

    assert worker.main() == 0
    saved = eval_repo.result(claim.id)
    assert saved is not None and saved.status == "completed"
    assert len(saved.answers) == len(CASES) == 3
    assert saved.report["complete"] is True

    # Replay refuses run
    assert worker.main() == 1


def test_fix_worker_failure_codes_and_no_replay(tmp_path: Path, monkeypatch) -> None:
    worker = _load_script("project-code-fix-worker")
    cases = [
        (RuntimeError("PRIVATE_CANARY"), "transport"),
        (({}, 1, 1), "invalid_response"),
        ("unchanged_replacement", "invalid_proposal"),
    ]
    for i, (mock_val, expected_code) in enumerate(cases):
        sub_dir = tmp_path / f"case_{i}"
        _, prev, _, lab = _setup_claimed_preview(sub_dir)
        monkeypatch.setattr(worker, "ROOT", sub_dir)
        monkeypatch.setattr(sys, "argv", ["worker", str(prev.id)])
        if mock_val == "unchanged_replacement":
            p_dict = Proposal(
                file_id=prev.files[0].id,
                replacement=prev.files[0].content,
                test_language="python",
                test_code="def test_run():\n    pass\n",
                explanation="Unchanged",
            ).model_dump(mode="json")
            exchange_mock = MagicMock(return_value=(p_dict, 10, 20))
        elif isinstance(mock_val, Exception):
            exchange_mock = MagicMock(side_effect=mock_val)
        else:
            exchange_mock = MagicMock(return_value=mock_val)
        monkeypatch.setattr("aegisops.project_code.lab_transport._exchange", exchange_mock)
        assert worker.main() == 1
        res = lab.result(prev.id)
        assert res is not None and res.status == "failed"
        assert res.failure_code == expected_code
        assert "PRIVATE_CANARY" not in res.model_dump_json()
        calls_before = exchange_mock.call_count
        assert worker.main() == 1
        assert exchange_mock.call_count == calls_before
