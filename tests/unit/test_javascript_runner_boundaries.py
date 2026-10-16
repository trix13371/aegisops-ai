"""Worker rejection paths and persisted-history compatibility, without Docker."""

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

from aegisops.domain.common import utc_now
from aegisops.project_runner.comparison import compare_runs
from aegisops.project_runner.models import JS_PROFILE, RunClaim, RunResult, SnapshotInfo, ToolCheck
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.repository import ProjectRepository

pytestmark = [pytest.mark.unit, pytest.mark.safety]
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def worker_case(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    project = ProjectRepository(tmp_path / ".local/projects").add("JavaScript", str(source))
    runs = RunRepository(tmp_path / ".local/project-runs")
    claim = runs.create(project.id, "node-js-v1")
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "js_boundary_worker", ROOT / "scripts/project-runner-local.py"
    )
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(worker.sys, "argv", ["worker", str(claim.id)])
    monkeypatch.setattr(worker, "read_image", lambda environment: "sha256:" + "1" * 64)
    return source, runs, claim, worker


@pytest.mark.parametrize(
    "tools", [("ruff", "pytest"), ("typescript",), ("node-test", "typescript")]
)
def test_js_worker_discards_foreign_partial_or_reordered_guest_checks_and_cleans(
    worker_case, monkeypatch, tools
):
    source, runs, claim, worker = worker_case
    (source / "app.js").write_text("export const answer = 42;\n")
    identifier, removed = "2" * 64, []
    payload = {
        "checks": [
            {
                "tool": tool,
                "status": "passed",
                "exit_code": 0,
                "output": "UNTRUSTED_GUEST_SUCCESS",
                "output_truncated": False,
            }
            for tool in tools
        ]
    }

    def docker(arguments, **kwargs):
        if arguments[0] == "create":
            return identifier.encode()
        if arguments[0] == "start":
            return json.dumps(payload).encode()
        return b""

    monkeypatch.setattr(worker, "docker", docker)
    monkeypatch.setattr(
        worker, "inspect_owned", lambda *args: {"State": {"OOMKilled": False, "ExitCode": 0}}
    )
    monkeypatch.setattr(worker, "validate_container", lambda *args: None)

    def remove(container, run_id, profile):
        removed.append((container, run_id, profile))
        return True

    monkeypatch.setattr(worker, "remove_owned", remove)
    worker.main()
    result = runs.result(claim.id)
    assert result.status == "error" and result.reason == "sandbox_failed"
    assert not result.checks and result.container_removed
    assert "UNTRUSTED_GUEST_SUCCESS" not in result.model_dump_json()
    assert removed == [(identifier, claim.id, JS_PROFILE)]


@pytest.mark.parametrize("mode", ["wrong_language", "oversized_fixture"])
def test_js_worker_rejects_unsupported_or_incomplete_snapshot_before_container_creation(
    worker_case, monkeypatch, mode
):
    source, runs, claim, worker = worker_case
    if mode == "wrong_language":
        (source / "app.py").write_text("raise RuntimeError('MUST_NOT_EXECUTE')\n")
    else:
        (source / "app.js").write_text("throw new Error('MUST_NOT_EXECUTE');\n")
        (source / "fixture.json").write_bytes(b" " * 262145)
    calls = []

    def docker(arguments, **kwargs):
        calls.append(arguments)
        assert arguments[:2] == ["image", "inspect"], "Container requested for rejected snapshot"
        return b""

    monkeypatch.setattr(worker, "docker", docker)
    worker.main()
    result = runs.result(claim.id)
    assert result.status == "not_supported" and result.reason == "snapshot_rejected"
    assert result.execution_scope == "not_started" and result.snapshot is None
    assert not result.checks and not result.container_removed
    assert len(calls) == 1


def test_pre_javascript_python_history_loads_without_rewriting_old_files(tmp_path):
    runs = RunRepository(tmp_path)
    claim = RunClaim(id=uuid4(), project_id=uuid4(), created_at=utc_now())
    result = RunResult(
        id=claim.id,
        project_id=claim.project_id,
        completed_at=utc_now(),
        status="passed",
        reason="checks_completed",
        snapshot=SnapshotInfo(sha256="1" * 64, files=1, python_files=1, bytes=1, skipped=0),
        image_id="sha256:" + "1" * 64,
        checks=tuple(
            ToolCheck(tool=tool, status="passed", exit_code=0, output="", output_truncated=False)
            for tool in ("ruff", "pytest")
        ),
        container_removed=True,
        execution_scope="container",
    )
    old_claim = claim.model_dump(mode="json", exclude={"profile", "environment"})
    old_result = result.model_dump(mode="json", exclude={"profile", "environment"})
    old_result["snapshot"].pop("javascript_files")
    runs.write(claim.id, "claim", json.dumps(old_claim))
    runs.write(claim.id, "result", json.dumps(old_result))
    original = runs.path(claim.id, "result").read_bytes()
    state = RunRepository(tmp_path).state(claim.id)
    assert state["claim"]["profile"] == "python-checks-v1"
    assert state["result"]["snapshot"]["javascript_files"] == 0
    assert state["result"]["status"] == "passed"
    assert runs.path(claim.id, "result").read_bytes() == original


def test_switching_language_never_reports_tool_transitions_even_for_the_same_image():
    project = uuid4()
    pairs = []
    for environment, profile, tools in (
        ("minimal", "python-checks-v1", ("ruff", "pytest")),
        ("node-js-v1", JS_PROFILE, ("typescript", "node-test")),
    ):
        claim = RunClaim(
            id=uuid4(),
            project_id=project,
            created_at=utc_now(),
            environment=environment,
            profile=profile,
        )
        result = RunResult(
            id=claim.id,
            project_id=project,
            environment=environment,
            profile=profile,
            completed_at=utc_now(),
            status="passed",
            reason="checks_completed",
            snapshot=SnapshotInfo(
                sha256="1" * 64,
                files=1,
                python_files=int(environment == "minimal"),
                javascript_files=int(environment == "node-js-v1"),
                bytes=1,
                skipped=0,
            ),
            image_id="sha256:" + "1" * 64,
            checks=tuple(
                ToolCheck(
                    tool=tool, status="passed", exit_code=0, output="", output_truncated=False
                )
                for tool in tools
            ),
            container_removed=True,
            execution_scope="container",
        )
        pairs.append((claim, result))
    comparison = compare_runs(pairs[0][0], pairs[1][0], pairs[0][1], pairs[1][1])
    assert not comparison.comparable and comparison.reason == "environment_changed"
    assert not comparison.transitions and comparison.same_snapshot is None
