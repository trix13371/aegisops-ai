"""Unit and safety tests for the shared-snapshot full check worker."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from aegisops.domain.common import utc_now
from aegisops.project_checks.repository import FullCheckRepository
from aegisops.project_runner.models import RunResult, ToolCheck
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.models import ProjectError
from aegisops.projects.repository import ProjectRepository

pytestmark = [pytest.mark.unit, pytest.mark.safety]
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def check_worker(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts"))
    return _load_script(REPO_ROOT / "scripts/project-check-local.py", "check_worker_script")


@pytest.fixture
def runner_script(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts"))
    return _load_script(REPO_ROOT / "scripts/project-runner-local.py", "runner_worker_script")


@pytest.fixture
def env(tmp_path, monkeypatch, check_worker):
    projects = ProjectRepository(tmp_path / ".local/projects")
    runs = RunRepository(tmp_path / ".local/project-runs")
    checks = FullCheckRepository(tmp_path / ".local/project-checks", projects, runs)
    monkeypatch.setattr(check_worker, "ROOT", tmp_path)

    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    (proj_dir / "main.py").write_text("print('hello')\n")
    project = projects.add("Sample", str(proj_dir))
    claim = checks.create(project.id, "minimal")
    return SimpleNamespace(
        tmp_path=tmp_path,
        projects=projects,
        runs=runs,
        checks=checks,
        project=project,
        claim=claim,
        proj_dir=proj_dir,
    )


def test_worker_composed_workflow_single_snapshot_and_live_tamper_isolation(
    env, check_worker, monkeypatch
):
    snapshot_invocations = []
    orig_snapshot_project = check_worker.snapshot_project

    def wrapped_snapshot(proj, profile):
        res = orig_snapshot_project(proj, profile)
        snapshot_invocations.append(res)
        return res

    monkeypatch.setattr(check_worker, "snapshot_project", wrapped_snapshot)

    scanned_snapshots = []
    orig_scan = check_worker.scan_snapshot

    def wrapped_scan(proj_id, snap):
        scanned_snapshots.append(snap)
        return orig_scan(proj_id, snap)

    monkeypatch.setattr(check_worker, "scan_snapshot", wrapped_scan)

    runner_snapshots = []

    def fake_run_check(cid, prepared_snapshot):
        runner_snapshots.append(prepared_snapshot)
        # Live file modification between static scan and runner execution
        (env.proj_dir / "main.py").write_text("MUTATED = True\n")
        result = RunResult(
            id=cid,
            project_id=env.claim.project_id,
            environment=env.claim.environment,
            profile=env.claim.profile,
            completed_at=utc_now(),
            status="passed",
            reason="checks_completed",
            snapshot=prepared_snapshot.info,
            image_id="sha256:" + "a" * 64,
            checks=(
                ToolCheck(
                    tool="ruff", status="passed", exit_code=0, output="", output_truncated=False
                ),
                ToolCheck(
                    tool="pytest", status="passed", exit_code=0, output="", output_truncated=False
                ),
            ),
            container_removed=True,
            execution_scope="container",
        )
        env.runs.save(result)

    monkeypatch.setattr(
        check_worker, "load_runner", lambda: SimpleNamespace(run_check=fake_run_check)
    )
    monkeypatch.setattr(check_worker.sys, "argv", ["project-check-local.py", str(env.claim.id)])

    check_worker.main()

    assert len(snapshot_invocations) == 1
    assert len(scanned_snapshots) == 1
    assert len(runner_snapshots) == 1
    assert scanned_snapshots[0] is runner_snapshots[0]
    assert runner_snapshots[0].data == snapshot_invocations[0].data

    final_check = env.checks.result(env.claim.id)
    assert final_check is not None and final_check.status == "passed"
    assert final_check.snapshot.sha256 == snapshot_invocations[0].info.sha256

    # Second invocation: duplicate claim marker refuses repeat and leaves records unchanged
    check_worker.main()
    assert len(snapshot_invocations) == 1
    assert env.checks.result(env.claim.id) == final_check


def test_snapshot_project_error_records_rejected_run_and_incomplete_check(
    env, check_worker, monkeypatch
):
    def failing_snapshot(proj, profile):
        raise ProjectError("snapshot failed")

    monkeypatch.setattr(check_worker, "snapshot_project", failing_snapshot)
    called_runner = False

    def fake_runner(*args, **kwargs):
        nonlocal called_runner
        called_runner = True

    monkeypatch.setattr(check_worker, "load_runner", lambda: SimpleNamespace(run_check=fake_runner))
    monkeypatch.setattr(check_worker.sys, "argv", ["project-check-local.py", str(env.claim.id)])

    check_worker.main()

    assert not called_runner
    run_res = env.runs.result(env.claim.id)
    assert run_res is not None
    assert run_res.status == "not_supported" and run_res.reason == "snapshot_rejected"
    assert run_res.snapshot is None

    check_res = env.checks.result(env.claim.id)
    assert check_res is not None
    assert check_res.status == "incomplete"
    assert check_res.static_report_id is None


def test_existing_runs_started_refuses_snapshot(env, check_worker, monkeypatch):
    env.runs.write(env.claim.id, "started", "{}")
    called_snapshot = False

    def fake_snapshot(proj, profile):
        nonlocal called_snapshot
        called_snapshot = True

    monkeypatch.setattr(check_worker, "snapshot_project", fake_snapshot)
    monkeypatch.setattr(check_worker.sys, "argv", ["project-check-local.py", str(env.claim.id)])

    check_worker.main()
    assert not called_snapshot
    assert env.checks.result(env.claim.id) is None


def test_unknown_runner_return_leaves_check_pending(env, check_worker, monkeypatch):
    monkeypatch.setattr(
        check_worker, "load_runner", lambda: SimpleNamespace(run_check=lambda cid, snap: None)
    )
    monkeypatch.setattr(check_worker.sys, "argv", ["project-check-local.py", str(env.claim.id)])

    check_worker.main()
    assert env.checks.result(env.claim.id) is None
    reports = env.projects.history(env.project.id)
    assert len(reports) == 1


def test_real_runner_missing_image_failure_preserves_static_report(
    env, check_worker, runner_script, monkeypatch
):
    monkeypatch.setattr(runner_script, "ROOT", env.tmp_path)

    def failing_read_image(environment):
        raise runner_script.RunnerFailure("missing image")

    monkeypatch.setattr(runner_script, "read_image", failing_read_image)
    monkeypatch.setattr(
        runner_script, "docker", lambda *args, **kwargs: pytest.fail("docker should not be called")
    )
    monkeypatch.setattr(check_worker, "load_runner", lambda: runner_script)
    monkeypatch.setattr(check_worker.sys, "argv", ["project-check-local.py", str(env.claim.id)])

    check_worker.main()
    run_res = env.runs.result(env.claim.id)
    assert run_res is not None
    assert run_res.reason == "runner_unavailable"
    assert run_res.snapshot is not None

    check_res = env.checks.result(env.claim.id)
    assert check_res is not None
    assert check_res.status == "incomplete"
    assert check_res.static_report_id is not None
    assert len(env.projects.history(env.project.id)) == 1


def test_real_runner_prepared_invalid_hash_rejects_without_reading_live_folder(
    env, runner_script, check_worker, monkeypatch
):
    orig_snap = check_worker.snapshot_project(
        env.projects.get(env.claim.project_id), env.claim.profile
    )
    corrupted_data = orig_snap.data + b"corrupted"
    from aegisops.project_runner.snapshot import Snapshot

    tampered_snapshot = Snapshot(
        data=corrupted_data, info=orig_snap.info, compatibility=orig_snap.compatibility
    )

    monkeypatch.setattr(runner_script, "ROOT", env.tmp_path)
    monkeypatch.setattr(
        runner_script,
        "snapshot_project",
        lambda *args, **kwargs: pytest.fail("live folder read forbidden"),
    )
    monkeypatch.setattr(
        runner_script, "docker", lambda *args, **kwargs: pytest.fail("docker invocation forbidden")
    )

    runner_script.run_check(env.claim.id, tampered_snapshot)
    run_res = env.runs.result(env.claim.id)
    assert run_res is not None
    assert run_res.status == "not_supported"
    assert run_res.reason == "snapshot_rejected"


def test_scan_snapshot_adapter_reject_records_incomplete_check(env, check_worker, monkeypatch):
    def failing_scan(proj_id, snap):
        raise ProjectError("scan failed")

    monkeypatch.setattr(check_worker, "scan_snapshot", failing_scan)
    monkeypatch.setattr(
        check_worker,
        "load_runner",
        lambda: pytest.fail("load_runner must not be called when scan fails"),
    )
    monkeypatch.setattr(check_worker.sys, "argv", ["project-check-local.py", str(env.claim.id)])

    check_worker.main()

    run_res = env.runs.result(env.claim.id)
    assert run_res is not None
    assert run_res.status == "not_supported"
    assert run_res.reason == "snapshot_rejected"
    assert run_res.execution_scope == "not_started"
    assert run_res.checks == ()
    check_res = env.checks.result(env.claim.id)
    assert check_res is not None
    assert check_res.status == "incomplete"
    assert check_res.static_report_id is None


def test_interrupted_workflow_marker_prevents_repeat_execution(env, check_worker, monkeypatch):
    snapshot_calls = 0
    orig_snapshot = check_worker.snapshot_project

    def counting_snapshot(proj, profile):
        nonlocal snapshot_calls
        snapshot_calls += 1
        return orig_snapshot(proj, profile)

    monkeypatch.setattr(check_worker, "snapshot_project", counting_snapshot)

    scan_calls = 0
    orig_scan = check_worker.scan_snapshot

    def counting_scan(proj_id, snap):
        nonlocal scan_calls
        scan_calls += 1
        return orig_scan(proj_id, snap)

    monkeypatch.setattr(check_worker, "scan_snapshot", counting_scan)
    monkeypatch.setattr(
        check_worker, "load_runner", lambda: SimpleNamespace(run_check=lambda cid, snap: None)
    )
    monkeypatch.setattr(check_worker.sys, "argv", ["project-check-local.py", str(env.claim.id)])

    # First invocation: runner returns None leaving check pending
    check_worker.main()
    assert env.checks.result(env.claim.id) is None
    assert snapshot_calls == 1
    assert scan_calls == 1

    # Second invocation: interrupted/pending marker must refuse repeat execution
    check_worker.main()
    assert snapshot_calls == 1
    assert scan_calls == 1
    assert env.checks.result(env.claim.id) is None


def test_invalid_cli_arguments_fail_cleanly_without_leak(check_worker, monkeypatch):
    monkeypatch.setattr(check_worker.sys, "argv", ["project-check-local.py", "invalid-uuid"])
    check_worker.main()

    monkeypatch.setattr(check_worker.sys, "argv", ["project-check-local.py"])
    check_worker.main()
