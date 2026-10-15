"""Real static checks, bounded reads and persistence, with no project code execution."""

import json
from pathlib import Path

import pytest

from aegisops.projects.models import ProjectError
from aegisops.projects.repository import ProjectRepository
from aegisops.projects.scanner import scan_project

pytestmark = [pytest.mark.unit, pytest.mark.safety]


@pytest.fixture
def project_case(tmp_path):
    root = tmp_path / "user-project"
    root.mkdir()
    repository = ProjectRepository(tmp_path / "registry")
    project = repository.add("Мой проект", str(root))
    return root, repository, project


def test_register_and_reports_survive_restart_without_modifying_project(project_case):
    root, repository, project = project_case
    source = root / "main.py"
    source.write_text("answer = 42\n")
    before = source.read_bytes()
    report = scan_project(project)
    repository.save_report(report)
    reopened = ProjectRepository(repository.directory)
    assert reopened.get(project.id).path == str(root)
    assert reopened.history(project.id) == (report,)
    assert report.status == "passed" and report.files_checked == 1
    assert source.read_bytes() == before and report.execution_performed is False
    reopened.remove(project.id)
    assert reopened.list() == () and source.read_bytes() == before
    assert reopened.read_report(report.id) == report


@pytest.mark.parametrize("path", ["relative/path", "https://github.com/a/b", "//server/share", ""])
def test_only_local_absolute_paths_are_accepted(tmp_path, path):
    with pytest.raises(ProjectError):
        ProjectRepository(tmp_path / "registry").add("Project", path)


def test_explorer_copy_as_path_quotes_are_accepted(tmp_path):
    root = tmp_path / "project with spaces"
    root.mkdir()
    project = ProjectRepository(tmp_path / "registry").add("Project", f'"{root}"')
    assert project.path == str(root)


def test_duplicate_or_replaced_directory_is_rejected(project_case):
    root, repository, project = project_case
    with pytest.raises(ProjectError, match="уже добавлена"):
        repository.add("Другой псевдоним", str(root))
    root.rename(root.with_name("moved-project"))
    root.mkdir()
    with pytest.raises(ProjectError, match="заменена"):
        scan_project(project)


def test_corrupt_registry_does_not_reset_or_overwrite(project_case):
    root, repository, project = project_case
    path = repository.directory / "registry.json"
    path.write_text('{"invalid":"DO_NOT_SHOW"}')
    before = path.read_bytes()
    with pytest.raises(ProjectError):
        repository.list()
    with pytest.raises(ProjectError):
        repository.add("Other", str(root.parent))
    assert path.read_bytes() == before


def test_untrusted_source_is_parsed_but_never_imported_or_run(project_case):
    root, _, project = project_case
    marker = root / "EXECUTED.txt"
    (root / "main.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    (root / "package.json").write_text('{"scripts":{"test":"DO_NOT_RUN"}}')
    report = scan_project(project)
    assert report.status == "passed" and not marker.exists()
    assert set(report.stacks) == {"Python", "JavaScript / TypeScript"}


@pytest.mark.parametrize(
    "filename,contents",
    [
        ("main.py", "def broken(:\n pass\n"),
        ("config.json", '{"a":1,"a":2}'),
        ("nan.json", '{"a":NaN}'),
        ("deploy.yaml", "spec: ["),
        ("pyproject.toml", "[broken"),
    ],
)
def test_format_errors_are_real_and_no_source_text_is_saved(project_case, filename, contents):
    root, _, project = project_case
    (root / filename).write_text(contents, encoding="utf-8")
    report = scan_project(project)
    assert report.status == "issues" and report.files_checked == 1
    assert any(item.code == "syntax" and item.file == filename for item in report.findings)
    assert contents not in report.model_dump_json()


def test_secret_and_dependency_files_are_excluded_before_open(project_case, monkeypatch):
    root, _, project = project_case
    (root / "main.py").write_text("pass\n")
    secret = root / ".env"
    secret.write_text("PRIVATE_SOURCE_CANARY")
    dependencies = root / "node_modules"
    dependencies.mkdir()
    (dependencies / "bad.py").write_text("INVALID")
    original = Path.open

    def guarded(path, *args, **kwargs):
        if path == secret or dependencies in path.parents:
            pytest.fail("excluded content was opened")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    report = scan_project(project)
    assert report.status == "passed" and report.files_checked == 1
    assert "PRIVATE_SOURCE_CANARY" not in report.model_dump_json()


def test_multiple_yaml_documents_and_container_findings(project_case):
    root, _, project = project_case
    (root / "deploy.yaml").write_text(
        "apiVersion: v1\nkind: Pod\nspec:\n  hostNetwork: true\n  containers:\n"
        "    - name: app\n      image: nginx:latest\n      securityContext:\n"
        "        privileged: true\n---\nservices:\n  web:\n    privileged: true\n"
    )
    report = scan_project(project)
    assert report.status == "issues" and report.files_checked == 1
    assert {item.code for item in report.findings} >= {
        "privileged",
        "host_namespace",
        "mutable_image",
        "missing_limits",
    }
    assert set(report.stacks) == {"Kubernetes", "Docker Compose"}


def test_oversize_aliases_and_no_supported_files_are_explicitly_partial(project_case):
    root, _, project = project_case
    (root / "main.py").write_bytes(b" " * 262145)
    (root / "compose.yaml").write_text("a: &value {}\nb: *value\n")
    report = scan_project(project)
    assert report.partial and report.status == "partial" and report.files_checked == 0
    assert {item.code for item in report.findings} >= {"yaml_alias", "limit"}


def test_total_byte_budget_stops_reading_additional_files(project_case, monkeypatch):
    import aegisops.projects.scanner as module

    root, _, project = project_case
    for name in ("a.py", "b.py", "c.py"):
        (root / name).write_text("pass\n")
    monkeypatch.setattr(module, "MAX_TOTAL_BYTES", 6)
    report = module.scan_project(project)
    assert report.partial and report.files_checked == 1 and report.files_seen <= 2


def test_history_keeps_latest_twenty_and_corrupt_report_is_not_success(project_case):
    root, repository, project = project_case
    (root / "main.py").write_text("pass\n")
    reports = [scan_project(project) for _ in range(22)]
    for report in reports:
        repository.save_report(report)
    assert repository.history(project.id) == tuple(reversed(reports[-20:]))
    path = repository.directory / "reports" / f"{reports[-1].id}.json"
    path.write_text(json.dumps({"text": "not a report"}))
    with pytest.raises(ProjectError):
        repository.history(project.id)


def test_symlink_files_are_never_followed(project_case, tmp_path):
    root, _, project = project_case
    outside = tmp_path / "outside.py"
    outside.write_text("INVALID")
    try:
        (root / "linked.py").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    report = scan_project(project)
    assert not any(item.code == "syntax" for item in report.findings)


def test_incomplete_report_cannot_be_relabelled_as_passed(project_case):
    _, repository, project = project_case
    report = scan_project(project)
    assert report.partial
    repository.save_report(report)
    path = repository.directory / "reports" / f"{report.id}.json"
    data = json.loads(path.read_text())
    data["status"] = "passed"
    path.write_text(json.dumps(data))
    with pytest.raises(ProjectError):
        repository.history(project.id)
