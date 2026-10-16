"""Snapshot boundaries and durable run reports. No Docker or project code execution."""

import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from aegisops.domain.common import utc_now
from aegisops.project_runner.models import RunResult, ToolCheck
from aegisops.project_runner.repository import RunRepository
from aegisops.project_runner.snapshot import snapshot_project
from aegisops.projects.models import ProjectError
from aegisops.projects.repository import ProjectRepository

pytestmark = [pytest.mark.unit, pytest.mark.safety]
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    projects = ProjectRepository(tmp_path / ".local/projects")
    project = projects.add("Project", str(root))
    runs = RunRepository(tmp_path / ".local/project-runs")
    return root, project, runs


def test_snapshot_excludes_secrets_links_dependencies_and_never_executes(case, monkeypatch):
    root, project, _ = case
    (root / "main.py").write_text("raise RuntimeError('MUST_NOT_EXECUTE_ON_HOST')\n")
    (root / "fixture.json").write_text('{"answer":42}')
    (root / ".env").write_text("PRIVATE_KEY_CANARY")
    (root / "node_modules").mkdir()
    (root / "node_modules/bad.py").write_text("PRIVATE_DEPENDENCY_CANARY")
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path.name == ".env" or "node_modules" in path.parts:
            pytest.fail("private input opened")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    snapshot = snapshot_project(project)
    assert b"PRIVATE_KEY_CANARY" not in snapshot.data
    with tarfile.open(fileobj=io.BytesIO(snapshot.data)) as archive:
        assert archive.getnames() == ["fixture.json", "main.py"]
        assert all(item.isfile() for item in archive)
    assert snapshot.info.files == 2 and snapshot.info.python_files == 1
    assert snapshot_project(project).info.sha256 == snapshot.info.sha256


@pytest.mark.parametrize("mode", ["oversize", "no_python", "deep"])
def test_incomplete_or_unsupported_snapshot_is_blocked_not_silently_truncated(case, mode):
    root, project, _ = case
    if mode == "oversize":
        (root / "main.py").write_bytes(b" " * 262145)
    elif mode == "no_python":
        (root / "app.js").write_text("let x=1")
    else:
        nested = root.joinpath(*["nested"] * 14)
        nested.mkdir(parents=True)
        (nested / "main.py").write_text("pass\n")
    with pytest.raises(ProjectError):
        snapshot_project(project)


def test_history_survives_restart_and_never_accepts_false_success(case):
    _, project, runs = case
    claim = runs.create(project.id)
    assert RunRepository(runs.directory).history(project.id)[0]["result"] is None
    with pytest.raises(ValidationError):
        RunResult(
            id=claim.id,
            project_id=project.id,
            completed_at=utc_now(),
            status="passed",
            reason="checks_completed",
        )
    report = RunResult(
        id=claim.id,
        project_id=project.id,
        completed_at=utc_now(),
        status="error",
        reason="runner_unavailable",
    )
    runs.save(report)
    assert RunRepository(runs.directory).result(claim.id) == report
    with pytest.raises(FileExistsError):
        runs.save(report)
    runs.path(claim.id, "result").write_text('{"invalid":"DO_NOT_RENDER"}')
    with pytest.raises(ValidationError):
        runs.result(claim.id)


def test_no_tests_and_missing_environment_are_not_passed():
    with pytest.raises(ValidationError):
        ToolCheck(tool="pytest", status="passed", exit_code=5, output="", output_truncated=False)
    assert (
        ToolCheck(
            tool="pytest", status="no_tests", exit_code=5, output="", output_truncated=False
        ).status
        == "no_tests"
    )
    with pytest.raises(ValidationError):
        ToolCheck(tool="ruff", status="no_tests", exit_code=5, output="", output_truncated=False)


@pytest.mark.parametrize(
    "profile", ["python-checks-v1", "javascript-checks-v1", "javascript-vitest-v1"]
)
def test_fixed_container_profile_has_no_mounts_keys_or_remote_daemon(monkeypatch, profile):
    from uuid import uuid4

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import project_runner_host as host

    arguments = host.container_arguments(uuid4(), "sha256:" + "1" * 64, profile)
    assert all(
        flag in arguments
        for flag in (
            "--network=none",
            "--read-only",
            "--user=65532:65532",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--cpus=1",
            "--memory=512m",
            "--pids-limit=64",
        )
    )
    assert not any(
        value in arguments for value in ("--mount", "--volume", "--env-file", "--privileged")
    )
    assert host.ENGINE.startswith(("npipe:", "unix:"))
    with pytest.raises(host.RunnerFailure):
        host.container_arguments(uuid4(), "user-chosen-image:latest")


def test_profile_context_changes_invalidate_image_pin(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import project_runner_host as host

    monkeypatch.setattr(host, "ROOT", tmp_path)
    context = tmp_path / "runner/python-v1"
    context.mkdir(parents=True)
    for name in ("Dockerfile", "entry.py", "requirements.txt", "data-requirements.txt"):
        (context / name).write_text("trusted")
    state = tmp_path / ".local/project-runner"
    state.mkdir(parents=True)
    (state / "image.json").write_text(
        json.dumps(
            {
                "profile": host.PROFILE,
                "image_id": "sha256:" + "1" * 64,
                "context_sha256": host.context_hash(),
            }
        )
    )
    assert host.read_image() == "sha256:" + "1" * 64
    (context / "entry.py").write_text("changed")
    with pytest.raises(host.RunnerFailure):
        host.read_image()


def test_unknown_worker_start_is_not_replayed(case, tmp_path, monkeypatch):
    _, project, runs = case
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "project_runner_worker", ROOT / "scripts/project-runner-local.py"
    )
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    claim = runs.create(project.id)
    runs.write(claim.id, "started", "{}")
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(worker.sys, "argv", ["worker", str(claim.id)])
    monkeypatch.setattr(worker, "docker", lambda *a, **k: pytest.fail("replayed container request"))
    worker.main()
    assert runs.result(claim.id) is None


@pytest.mark.parametrize("mutation", ["label", "profile", "name", "none"])
def test_cleanup_only_removes_exact_owned_container(monkeypatch, mutation):
    from uuid import uuid4

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import project_runner_host as host

    identifier, run_id = "1" * 64, uuid4()
    value = {
        "Id": identifier,
        "Name": f"/aegisops-check-{run_id}",
        "Config": {
            "Labels": {
                host.LABEL: str(run_id),
                "org.aegisops.profile": host.PROFILE,
            }
        },
    }
    if mutation == "label":
        value["Config"]["Labels"][host.LABEL] = "someone-else"
    elif mutation == "profile":
        value["Config"]["Labels"]["org.aegisops.profile"] = "foreign"
    elif mutation == "name":
        value["Name"] = "/foreign-container"
    calls = []

    def docker(arguments, **kwargs):
        calls.append(arguments)
        return json.dumps([value]).encode() if arguments[0] == "inspect" else b""

    monkeypatch.setattr(host, "docker", docker)
    if mutation != "none":
        with pytest.raises(host.RunnerFailure):
            host.remove_owned(identifier, run_id)
        assert calls == [["inspect", identifier]]
    else:
        assert host.remove_owned(identifier, run_id)
        assert calls[-1] == ["rm", "--force", identifier]


def test_cli_boundary_filters_keys_and_bounds_output(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import project_runner_host as host

    for name in (
        "OPENROUTER_API_KEY",
        "AEGISOPS_LLM_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "DOCKER_HOST",
    ):
        monkeypatch.setenv(name, "PRIVATE_ENV_CANARY")
    captured = []

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            captured.append((command, kwargs))
            self.stdout, self.stderr = io.BytesIO(b"X" * 100), io.BytesIO(b"")

        def wait(self, **kwargs):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(host.subprocess, "Popen", Process)
    assert host.docker(["version"]) == b"X" * 100
    command, options = captured[0]
    assert command[:3] == ["docker", "--host", host.ENGINE]
    assert "PRIVATE_ENV_CANARY" not in options["env"].values()
    with pytest.raises(host.RunnerFailure):
        host.docker(["version"], maximum=10)


def test_worker_host_deadline_still_cleans_container_and_records_timeout(
    case, tmp_path, monkeypatch
):
    root, project, runs = case
    (root / "main.py").write_text("pass\n")
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "timed_runner", ROOT / "scripts/project-runner-local.py"
    )
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    claim = runs.create(project.id)
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(worker.sys, "argv", ["worker", str(claim.id)])
    monkeypatch.setattr(worker, "read_image", lambda environment="minimal": "sha256:" + "1" * 64)
    identifier, removed = "2" * 64, []

    def docker(arguments, **kwargs):
        if arguments[0] == "create":
            return identifier.encode()
        if arguments[0] == "start":
            raise worker.RunnerFailure("deadline")
        return b""

    monkeypatch.setattr(worker, "docker", docker)
    monkeypatch.setattr(worker, "inspect_owned", lambda *args: {})
    monkeypatch.setattr(worker, "validate_container", lambda *args: None)

    def remove(container, run_id, profile="python-checks-v1"):
        removed.append((container, run_id))
        return True

    monkeypatch.setattr(worker, "remove_owned", remove)
    worker.main()
    result = runs.result(claim.id)
    assert result.status == "timeout" and result.container_removed
    assert removed == [(identifier, claim.id)]


def test_environment_is_persisted_and_cannot_be_changed_in_result(case):
    _, project, runs = case
    claim = runs.create(project.id, "python-data-v1")
    assert RunRepository(runs.directory).claim(claim.id).environment == "python-data-v1"
    with pytest.raises(ProjectError):
        runs.create(project.id, "user-image")
    wrong = RunResult(
        id=claim.id,
        project_id=project.id,
        completed_at=utc_now(),
        status="error",
        reason="runner_unavailable",
    )
    with pytest.raises(ProjectError):
        runs.save(wrong)
    assert not runs.path(claim.id, "result").exists()
    # Read validation rejects a result from another environment, including old default.
    runs.write(claim.id, "result", wrong.model_dump_json())
    with pytest.raises(ProjectError):
        runs.result(claim.id)


@pytest.mark.parametrize("status", ["error", "failed"])
def test_missing_import_hints_are_bounded_deduplicated_and_not_package_install_requests(status):
    from aegisops.project_runner.diagnostics import missing_modules

    check = ToolCheck(
        tool="pytest",
        status=status,
        exit_code=2 if status == "error" else 1,
        output="E   ModuleNotFoundError: No module named 'yaml'\n"
        "ModuleNotFoundError: No module named 'local.module'\n"
        "ModuleNotFoundError: No module named 'yaml'\n"
        "ModuleNotFoundError: No module named 'https://evil.invalid/a'\n"
        "ModuleNotFoundError: No module named 'pip; bad'\n",
        output_truncated=False,
    )
    assert missing_modules(check) == ("yaml", "local.module")
    assert not missing_modules(ToolCheck.model_validate({**check.model_dump(), "tool": "ruff"}))


def test_extended_image_pin_cannot_substitute_minimal_or_unknown_environment(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import project_runner_host as host

    monkeypatch.setattr(host, "ROOT", tmp_path)
    context = tmp_path / "runner/python-v1"
    context.mkdir(parents=True)
    for name in ("Dockerfile", "entry.py", "requirements.txt", "data-requirements.txt"):
        (context / name).write_text("trusted")
    state = tmp_path / ".local/project-runner"
    state.mkdir(parents=True)
    value = {
        "profile": host.PROFILE,
        "environment": "python-data-v1",
        "image_id": "sha256:" + "1" * 64,
        "context_sha256": host.context_hash("python-data-v1"),
    }
    (state / "python-data-v1.json").write_text(json.dumps(value))
    assert host.read_image("python-data-v1") == value["image_id"]
    (state / "image.json").write_text(json.dumps(value))
    with pytest.raises(host.RunnerFailure):
        host.read_image()
    with pytest.raises(host.RunnerFailure):
        host.read_image("../python-data-v1")
    (context / "data-requirements.txt").write_text("changed")
    with pytest.raises(host.RunnerFailure):
        host.read_image("python-data-v1")


def test_javascript_snapshot_keeps_code_and_data_but_excludes_secrets_and_dependencies(case):
    from aegisops.project_runner.models import JS_PROFILE

    root, project, _ = case
    for name in ("app.ts", "main.js", "test.test.mts", "module.cts", "types.d.ts"):
        (root / name).write_text("// fixture\n")
    (root / "package.json").write_text('{"scripts":{"test":"MUST_NOT_EXECUTE"}}')
    (root / "other_language.py").write_text("PYTHON_NOT_FOR_THIS_PROFILE")
    (root / ".env").write_text("PRIVATE_JS_CANARY")
    (root / "node_modules").mkdir()
    (root / "node_modules/dependency.js").write_text("PRIVATE_DEP_CANARY")
    snapshot = snapshot_project(project, JS_PROFILE)
    assert snapshot.info.javascript_files == 5 and snapshot.info.python_files == 0
    with tarfile.open(fileobj=io.BytesIO(snapshot.data)) as archive:
        assert archive.getnames() == [
            "app.ts",
            "main.js",
            "module.cts",
            "package.json",
            "test.test.mts",
            "types.d.ts",
        ]
    assert b"PRIVATE_" not in snapshot.data and b"PYTHON_NOT_FOR_THIS_PROFILE" not in snapshot.data
    python = snapshot_project(project)
    assert python.info.python_files == 1 and python.info.javascript_files == 0


def test_language_claim_and_checks_cannot_be_mixed(case):
    from aegisops.project_runner.models import JS_PROFILE, RunClaim, SnapshotInfo

    _, project, runs = case
    claim = runs.create(project.id, "node-js-v1")
    assert claim.profile == JS_PROFILE and runs.claim(claim.id) == claim
    with pytest.raises(ValidationError):
        RunClaim.model_validate({**claim.model_dump(), "profile": "python-checks-v1"})
    checks = tuple(
        ToolCheck(tool=tool, status="passed", exit_code=0, output="ok", output_truncated=False)
        for tool in ("typescript", "node-test")
    )
    result = RunResult(
        id=claim.id,
        project_id=project.id,
        completed_at=utc_now(),
        environment="node-js-v1",
        profile=JS_PROFILE,
        status="passed",
        reason="checks_completed",
        checks=checks,
        snapshot=SnapshotInfo(
            sha256="1" * 64, files=1, python_files=0, javascript_files=1, bytes=1, skipped=0
        ),
        image_id="sha256:" + "1" * 64,
        container_removed=True,
        execution_scope="container",
    )
    runs.save(result)
    assert runs.result(claim.id) == result
    assert not runs.state(claim.id)["missing_modules"]
    with pytest.raises(ValidationError):
        RunResult.model_validate(
            {
                **result.model_dump(),
                "checks": [
                    {**item.model_dump(), "tool": tool}
                    for item, tool in zip(checks, ("ruff", "pytest"), strict=True)
                ],
            }
        )
    with pytest.raises(ValidationError):
        RunResult.model_validate(
            {
                **result.model_dump(),
                "snapshot": {
                    **result.snapshot.model_dump(),
                    "javascript_files": 0,
                    "python_files": 1,
                },
            }
        )


def test_js_cleanup_requires_the_exact_js_profile_label(monkeypatch):
    from uuid import uuid4

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import project_runner_host as host

    run_id, identifier = uuid4(), "1" * 64
    value = {
        "Id": identifier,
        "Name": f"/aegisops-check-{run_id}",
        "Config": {
            "Labels": {host.LABEL: str(run_id), "org.aegisops.profile": "javascript-checks-v1"}
        },
    }
    calls = []

    def docker(arguments, **kwargs):
        calls.append(arguments)
        return json.dumps([value]).encode() if arguments[0] == "inspect" else b""

    monkeypatch.setattr(host, "docker", docker)
    with pytest.raises(host.RunnerFailure):
        host.remove_owned(identifier, run_id)
    assert not any(call[0] == "rm" for call in calls)
    assert host.remove_owned(identifier, run_id, "javascript-checks-v1")
    assert calls[-1] == ["rm", "--force", identifier]


def test_js_image_pin_includes_shared_guest_entry_and_its_own_tools(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import project_runner_host as host

    monkeypatch.setattr(host, "ROOT", tmp_path)
    for name in (
        ".dockerignore",
        "python-v1/entry.py",
        "javascript-v1/Dockerfile",
        "javascript-v1/package.json",
        "javascript-v1/package-lock.json",
        "javascript-v1/check.mjs",
        "javascript-v1/entry.py",
    ):
        target = tmp_path / "runner" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("trusted")
    state = tmp_path / ".local/project-runner"
    state.mkdir(parents=True)
    (state / "node-js-v1.json").write_text(
        json.dumps(
            {
                "profile": "javascript-checks-v1",
                "environment": "node-js-v1",
                "image_id": "sha256:" + "1" * 64,
                "context_sha256": host.context_hash("node-js-v1"),
            }
        )
    )
    assert host.read_image("node-js-v1") == "sha256:" + "1" * 64
    (tmp_path / "runner/python-v1/entry.py").write_text("changed shared capture/unpack")
    with pytest.raises(host.RunnerFailure):
        host.read_image("node-js-v1")


@pytest.mark.parametrize("name", ["BAD.TS", "bad.test.JS", "source.MtS", "module.CJS"])
def test_noncanonical_js_extension_blocks_mixed_snapshot_instead_of_silent_skip(case, name):
    from aegisops.project_runner.models import JS_PROFILE

    root, project, _ = case
    (root / "passing.test.mjs").write_text("// normal test file\n")
    (root / name).write_text("const broken = ;\n")
    with pytest.raises(ProjectError, match="нижнем регистре"):
        snapshot_project(project, JS_PROFILE)
