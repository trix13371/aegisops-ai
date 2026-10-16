"""Prepared Vitest gates reject unsupported projects before creating a container."""

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aegisops.domain.common import utc_now
from aegisops.project_runner.comparison import compare_runs
from aegisops.project_runner.compatibility import MAX_PACKAGE_BYTES, inspect_package
from aegisops.project_runner.compatibility_gate import environment_issues
from aegisops.project_runner.inspection import inspect_project_package
from aegisops.project_runner.models import (
    VITEST_PROFILE,
    RunClaim,
    RunResult,
    SnapshotInfo,
    ToolCheck,
    profile_for,
    tools_for,
)
from aegisops.project_runner.repository import RunRepository
from aegisops.project_runner.snapshot import snapshot_project
from aegisops.projects.repository import ProjectRepository

pytestmark = [pytest.mark.unit, pytest.mark.safety]
ROOT = Path(__file__).resolve().parents[2]
PREPARED = {"vitest": "4", "vite": "8", "typescript": "6", "@types/node": "24"}


@pytest.fixture
def project_case(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.test.ts").write_text("throw new Error('MUST_NOT_EXECUTE_ON_HOST');\n")
    project = ProjectRepository(tmp_path / ".local/projects").add("Vitest", str(source))
    return source, project


@pytest.fixture
def worker_case(tmp_path, monkeypatch, project_case):
    source, project = project_case
    runs = RunRepository(tmp_path / ".local/project-runs")
    claim = runs.create(project.id, "node-vitest-v1")
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "vitest_boundary_worker", ROOT / "scripts/project-runner-local.py"
    )
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(worker.sys, "argv", ["worker", str(claim.id)])
    monkeypatch.setattr(worker, "read_image", lambda environment: "sha256:" + "1" * 64)
    calls = []

    def image_only(arguments, **kwargs):
        calls.append(arguments)
        assert arguments[:2] == ["image", "inspect"], "Rejected project reached container creation"
        return b""

    monkeypatch.setattr(worker, "docker", image_only)
    return source, project, runs, claim, worker, calls


def test_only_prepared_direct_dependencies_are_accepted_by_vitest_preset():
    compatibility = inspect_package(json.dumps({"devDependencies": PREPARED}))
    assert compatibility.status == "limited"
    assert compatibility.reasons == ("dependency_versions_unverified",)
    assert not environment_issues(compatibility, "node-vitest-v1")
    assert environment_issues(compatibility, "node-js-v1") == (
        "framework_mismatch",
        "dependencies_unavailable",
    )
    node = inspect_package(
        json.dumps({"devDependencies": {"typescript": "6", "@types/node": "24"}})
    )
    assert not environment_issues(node, "node-js-v1")
    assert environment_issues(node, "node-vitest-v1") == ("framework_mismatch",)


@pytest.mark.parametrize(
    "section", ["dependencies", "devDependencies", "optionalDependencies", "peerDependencies"]
)
def test_unavailable_direct_dependency_in_any_section_is_blocked(section):
    compatibility = inspect_package(
        json.dumps({"devDependencies": PREPARED, section: {**PREPARED, "react": "19"}})
    )
    assert "additional_dependencies" in compatibility.reasons
    assert environment_issues(compatibility, "node-vitest-v1") == ("dependencies_unavailable",)


@pytest.mark.parametrize("content", [b"{", b"\xff", b'{"dependencies":null}'])
def test_root_package_invalid_input_is_closed(project_case, content):
    source, project = project_case
    (source / "package.json").write_bytes(content)
    result = inspect_project_package(project)
    assert result.status == "unsupported" and result.reasons == ("package_invalid",)
    assert result.dependencies == ()


def test_package_inspection_does_not_follow_link_or_open_its_target(project_case, monkeypatch):
    source, project = project_case
    package = source / "package.json"
    package.write_text('{"dependencies":{"vitest":"4"}}')
    original_link, original_open = Path.is_symlink, Path.open

    def linked(path):
        return path == package or original_link(path)

    def guarded_open(path, *args, **kwargs):
        assert path != package, "Link target was opened"
        return original_open(path, *args, **kwargs)

    # Model a link on Windows without requiring elevated symlink creation privileges.
    monkeypatch.setattr(Path, "is_symlink", linked)
    monkeypatch.setattr(Path, "open", guarded_open)
    assert inspect_project_package(project).reasons == ("package_invalid",)


def test_package_inspection_limits_the_actual_read_and_ignores_nested_package(
    project_case, monkeypatch
):
    source, project = project_case
    package = source / "package.json"
    package.write_bytes(b" " * (MAX_PACKAGE_BYTES + 200))
    nested = source / "nested"
    nested.mkdir()
    (nested / "package.json").write_text("MUST_NOT_READ")
    original_open, reads = Path.open, []

    class BoundedRead:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, maximum):
            reads.append(maximum)
            return self.stream.read(maximum)

        def fileno(self):
            return self.stream.fileno()

    def monitored_open(path, *args, **kwargs):
        assert path != nested / "package.json", "Nested project was inspected"
        stream = original_open(path, *args, **kwargs)
        return BoundedRead(stream) if path == package else stream

    monkeypatch.setattr(Path, "open", monitored_open)
    result = inspect_project_package(project)
    assert result.reasons == ("package_invalid",)
    assert reads == [MAX_PACKAGE_BYTES + 1]


@pytest.mark.parametrize("mode", ["extra_peer", "invalid_package", "workspace", "node_framework"])
def test_worker_rejects_incompatible_snapshot_before_any_container_operation(worker_case, mode):
    source, _, runs, claim, worker, calls = worker_case
    package = {"devDependencies": PREPARED}
    if mode == "extra_peer":
        package["peerDependencies"] = {"react": "19"}
    elif mode == "workspace":
        package["workspaces"] = ["packages/*"]
    elif mode == "node_framework":
        package = {}
    (source / "package.json").write_text("{" if mode == "invalid_package" else json.dumps(package))
    worker.main()
    result = runs.result(claim.id)
    assert result is not None
    assert result.status == "not_supported" and result.reason == "project_incompatible"
    assert result.execution_scope == "not_started" and not result.checks
    assert result.compatibility is not None and result.compatibility_issues
    assert result.snapshot is not None and not result.container_removed
    assert len(calls) == 1


def test_worker_uses_archived_package_not_changed_live_advisory(worker_case, monkeypatch):
    source, project, runs, claim, worker, calls = worker_case
    package = source / "package.json"
    package.write_text(json.dumps({"devDependencies": PREPARED}))
    assert not environment_issues(inspect_project_package(project), claim.environment)
    package.write_text(json.dumps({"devDependencies": {**PREPARED, "react": "19"}}))
    rejected_snapshot = snapshot_project(project, VITEST_PROFILE)
    # The file becomes compatible again after capture; the copied bytes remain authoritative.
    package.write_text(json.dumps({"devDependencies": PREPARED}))
    monkeypatch.setattr(worker, "snapshot_project", lambda *args: rejected_snapshot)
    worker.main()
    result = runs.result(claim.id)
    assert result.reason == "project_incompatible"
    assert result.compatibility_issues == ("dependencies_unavailable",)
    assert "react" in result.compatibility.dependencies
    assert len(calls) == 1


@pytest.mark.parametrize("directive", ["@VITEST-ENVIRONMENT", "@JEST-ENVIRONMENT"])
def test_uppercase_environment_override_is_blocked_before_container_creation(
    worker_case, directive
):
    source, _, runs, claim, worker, calls = worker_case
    (source / "package.json").write_text(json.dumps({"devDependencies": PREPARED}))
    (source / "app.test.ts").write_text(f"/** {directive} jsdom */\nthrow new Error('NEVER');\n")
    worker.main()
    result = runs.result(claim.id)
    assert result.reason == "project_incompatible"
    assert result.compatibility_issues == ("environment_directive",)
    assert result.execution_scope == "not_started" and not result.checks
    assert len(calls) == 1


def _completed(claim):
    return RunResult(
        id=claim.id,
        project_id=claim.project_id,
        environment=claim.environment,
        profile=claim.profile,
        completed_at=utc_now(),
        status="passed",
        reason="checks_completed",
        snapshot=SnapshotInfo(
            sha256="1" * 64, files=1, python_files=0, javascript_files=1, bytes=1, skipped=0
        ),
        image_id="sha256:" + "1" * 64,
        checks=tuple(
            ToolCheck(tool=tool, status="passed", exit_code=0, output="", output_truncated=False)
            for tool in tools_for(claim.profile)
        ),
        container_removed=True,
        execution_scope="container",
    )


def test_report_cannot_mix_compatibility_rejection_and_passed_guest_checks():
    claim = RunClaim(
        id=uuid4(),
        project_id=uuid4(),
        created_at=utc_now(),
        environment="node-vitest-v1",
        profile=VITEST_PROFILE,
    )
    completed = _completed(claim).model_dump()
    with pytest.raises(ValidationError, match="invalid compatibility rejection"):
        RunResult.model_validate(
            {
                **completed,
                "compatibility": inspect_package('{"dependencies":{"vitest":"4"}}'),
                "compatibility_issues": ("environment_directive",),
            }
        )


def test_vitest_and_node_results_never_report_transitions_with_same_image_and_snapshot():
    project = uuid4()
    claims = tuple(
        RunClaim(
            id=uuid4(),
            project_id=project,
            created_at=utc_now(),
            environment=environment,
            profile=profile_for(environment),
        )
        for environment in ("node-js-v1", "node-vitest-v1")
    )
    result = compare_runs(claims[0], claims[1], *(_completed(claim) for claim in claims))
    assert not result.comparable and result.reason == "environment_changed"
    assert not result.transitions and result.same_snapshot is None
