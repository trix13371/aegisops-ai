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


def scan_document(project_case, document):
    root, _, project = project_case
    (root / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
    return scan_project(project)


@pytest.mark.parametrize("group", ["containers", "initContainers", "ephemeralContainers"])
def test_security_rules_cover_all_container_groups_and_exact_location(project_case, group):
    report = scan_document(
        project_case,
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "spec": {
                group: [
                    {
                        "name": "PRIVATE_SOURCE_CANARY",
                        "image": "registry:5000/app:latest",
                        "securityContext": {
                            "privileged": True,
                            "allowPrivilegeEscalation": True,
                            "capabilities": {"add": ["SYS_ADMIN"]},
                        },
                    }
                ]
            },
        },
    )
    assert report.rules_version == "local-static-v2"
    assert {item.code for item in report.findings} >= {
        "privileged",
        "privilege_escalation",
        "capabilities",
        "mutable_image",
    }
    assert all(
        item.location.startswith(f"document[1].spec.{group}[0].") and item.recommendation
        for item in report.findings
    )
    assert "PRIVATE_SOURCE_CANARY" not in report.model_dump_json()
    assert ("missing_limits" in {item.code for item in report.findings}) == (
        group != "ephemeralContainers"
    )


def test_list_cronjob_and_second_yaml_document_have_distinct_locations(project_case):
    root, _, project = project_case
    (root / "jobs.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\ndata: {}\n---\n"
        "apiVersion: v1\nkind: List\nitems:\n- apiVersion: batch/v1\n  kind: CronJob\n"
        "  spec:\n    jobTemplate:\n      spec:\n        template:\n          spec:\n"
        "            hostIPC: true\n            containers: []\n",
        encoding="utf-8",
    )
    report = scan_project(project)
    assert len(report.findings) == 1
    assert report.findings[0].location == (
        "document[2].items[0].spec.jobTemplate.spec.template.spec.hostIPC"
    )


@pytest.mark.parametrize(
    "volume",
    [
        "/var/run/docker.sock:/var/run/docker.sock:ro",
        {"type": "bind", "source": "/run/docker.sock", "target": "/socket", "read_only": True},
        {"type": "npipe", "source": "//./pipe/docker_engine", "target": "//./pipe/docker_engine"},
    ],
)
def test_compose_direct_socket_mounts_and_other_risks(project_case, volume):
    report = scan_document(
        project_case,
        {
            "services": {
                "PRIVATE_SERVICE_CANARY": {
                    "image": "app",
                    "network_mode": "host",
                    "pid": "host",
                    "ipc": "host",
                    "cap_add": ["CAP_SYS_ADMIN"],
                    "volumes": [volume],
                }
            }
        },
    )
    assert {item.code for item in report.findings} == {
        "mutable_image",
        "host_namespace",
        "capabilities",
        "docker_socket",
    }
    assert len([item for item in report.findings if item.code == "host_namespace"]) == 3
    assert any(item.location == "document[1].services[#1].volumes[0]" for item in report.findings)
    assert "PRIVATE_SERVICE_CANARY" not in report.model_dump_json()


def test_host_path_warning_does_not_copy_host_path_value(project_case):
    report = scan_document(
        project_case,
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "spec": {
                "volumes": [{"hostPath": {"path": "/PRIVATE_HOST_CANARY"}}],
            },
        },
    )
    assert len(report.findings) == 1 and report.findings[0].code == "host_path"
    assert "PRIVATE_HOST_CANARY" not in report.model_dump_json()


def test_safe_configs_do_not_generate_container_warnings(project_case):
    report = scan_document(
        project_case,
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "spec": {
                "template": {
                    "spec": {
                        "hostNetwork": False,
                        "containers": [
                            {
                                "image": "registry:5000/app@sha256:abcdef",
                                "securityContext": {
                                    "privileged": False,
                                    "allowPrivilegeEscalation": False,
                                    "capabilities": {"add": ["NET_BIND_SERVICE"]},
                                },
                                "resources": {"limits": {"cpu": "100m", "memory": "64Mi"}},
                            }
                        ],
                    }
                }
            },
        },
    )
    assert report.status == "passed" and not report.findings
    report = scan_document(
        project_case,
        {
            "services": {
                "app": {
                    "image": "app:1.2",
                    "network_mode": "bridge",
                    "cap_add": ["NET_BIND_SERVICE"],
                    "volumes": ["data:/var/run/docker.sock", {"type": "volume", "source": "data"}],
                }
            }
        },
    )
    assert report.status == "passed"


@pytest.mark.parametrize(
    "document",
    [
        {"apiVersion": "v1", "kind": "ConfigMap", "spec": {"hostNetwork": True}},
        {"apiVersion": "custom/v1", "kind": "Other", "spec": {"containers": [{}]}},
        {"apiVersion": "v1", "kind": [], "spec": []},
        {"services": {"app": {"volumes": [{"type": {}, "source": []}], "cap_add": [{}]}}},
    ],
)
def test_unrelated_or_wrong_shape_values_do_not_crash_or_masquerade_as_pods(project_case, document):
    assert scan_document(project_case, document).status == "passed"


@pytest.mark.parametrize("count", [101, 150])
def test_truncated_container_or_service_list_is_partial(project_case, count):
    report = scan_document(project_case, {"services": {str(i): {} for i in range(count)}})
    assert report.partial and report.status == "partial"
    report = scan_document(
        project_case,
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "spec": {
                "ephemeralContainers": [{}] * count,
            },
        },
    )
    assert report.partial and report.status == "partial"


def test_nested_lists_and_findings_remain_bounded(project_case):
    document = {"apiVersion": "v1", "kind": "Pod", "spec": {"hostPID": True}}
    for _ in range(10):
        document = {"apiVersion": "v1", "kind": "List", "items": [document]}
    assert scan_document(project_case, document).partial
    report = scan_document(
        project_case,
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "spec": {
                "containers": [{"image": "app", "securityContext": {"privileged": True}}] * 100,
            },
        },
    )
    assert report.partial and len(report.findings) == 200 and report.findings[-1].code == "limit"


def test_legacy_report_keeps_original_rules_and_reads_without_new_fields(project_case):
    _, repository, _ = project_case
    report = scan_document(project_case, {"services": {"app": {"privileged": True}}})
    repository.save_report(report)
    path = repository.directory / "reports" / f"{report.id}.json"
    payload = json.loads(path.read_text())
    payload.pop("rules_version")
    for finding in payload["findings"]:
        finding.pop("location")
        finding.pop("recommendation")
    path.write_text(json.dumps(payload))
    old = repository.read_report(report.id)
    assert old.rules_version == "local-static-v1"
    assert old.findings[0].recommendation is None and old.findings[0].location is None


def test_document_budget_cannot_hide_omitted_list_items_as_success(project_case):
    empty_pod = {"apiVersion": "v1", "kind": "Pod", "spec": {}}
    groups = [{"apiVersion": "v1", "kind": "List", "items": [empty_pod] * 100} for _ in range(11)]
    groups[-1]["items"][-1] = {"apiVersion": "v1", "kind": "Pod", "spec": {"hostPID": True}}
    report = scan_document(project_case, {"apiVersion": "v1", "kind": "List", "items": groups})
    assert report.partial and report.status == "partial"
    assert report.files_checked == 1 and report.findings[-1].code == "limit"
