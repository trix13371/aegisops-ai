"""Local UI capability/CSRF boundaries, fixed job commands and safe failure presentation."""

import importlib.util
import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from uuid import UUID

import pytest

from aegisops.dashboard.view import present_record
from aegisops.domain.models import AuditRecord
from aegisops.project_ai.repository import AnalysisRepository
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.repository import ProjectRepository

pytestmark = pytest.mark.safety
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def operator():
    spec = importlib.util.spec_from_file_location(
        "local_ui_operator", ROOT / "scripts/dashboard-local.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def host(operator, monkeypatch, tmp_path):
    dashboard = operator.Dashboard()
    dashboard.projects = ProjectRepository(tmp_path / "private-registry")
    dashboard.analyses = AnalysisRepository(tmp_path / "private-ai")
    dashboard.runs = RunRepository(tmp_path / "private-runs")
    monkeypatch.setattr(dashboard, "overview", lambda: {"records": [], "private": False})
    monkeypatch.setattr(dashboard, "run_job", lambda *args: None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), operator.Handler)
    server.dashboard = dashboard
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    def request(path, *, method="GET", auth=False, body=None, headers=None):
        combined = {}
        if auth:
            combined["Cookie"] = f"aegisops_session={dashboard.token}"
        if method == "POST":
            combined.update(Origin=origin, **{"Content-Type": "application/json"})
        combined.update(headers or {})
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=combined)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    yield dashboard, request
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_bootstrap_exchanges_capability_for_http_only_cookie_and_clean_url(host):
    dashboard, request = host
    status, headers, body = request("/session/" + dashboard.token)
    assert status == 303 and headers["Location"] == "/" and body == b""
    assert "HttpOnly" in headers["Set-Cookie"] and "SameSite=Strict" in headers["Set-Cookie"]
    assert dashboard.token.encode() not in body
    assert request("/session/wrong")[0] == 403


@pytest.mark.parametrize("path", ["/api/overview", "/api/job", "/api/ping", "/api/audit/bad"])
def test_api_requires_current_session(host, path):
    _, request = host
    assert request(path)[0] == 401
    assert request(path, headers={"Cookie": "aegisops_session=wrong"})[0] == 401


@pytest.mark.parametrize("path", ["/", "/api/overview", "/session/anything"])
def test_dns_rebinding_host_is_rejected(host, path):
    _, request = host
    assert request(path, auth=True, headers={"Host": "attacker.example"})[0] == 403


def test_static_assets_have_no_store_csp_and_no_path_arbitrary_file_read(host):
    _, request = host
    status, headers, body = request("/")
    assert status == 200 and "ЦЕНТР НАБЛЮДЕНИЯ".encode() in body
    assert headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert headers["Referrer-Policy"] == "no-referrer"
    for path in ("/.env", "/../.env", "/%2e%2e/.env", "/style.css?anything"):
        assert request(path, auth=True)[0] == 404


@pytest.mark.parametrize("origin", ["https://attacker.example", "null", ""])
def test_cookie_does_not_authorize_cross_origin_jobs(host, origin):
    _, request = host
    assert (
        request(
            "/api/jobs",
            method="POST",
            auth=True,
            body='{"mode":"local_kind"}',
            headers={"Origin": origin},
        )[0]
        == 403
    )


@pytest.mark.parametrize(
    "body,headers",
    [
        ('{"mode":"local_kind"}', {"Content-Type": "text/plain"}),
        ('{"mode":"local_kind"}', {"Transfer-Encoding": "chunked"}),
        ('{"mode":"local_kind","mode":"local_kind"}', {}),
        ('{"mode":"local_kind","command":"whoami"}', {}),
        ('{"mode":"synthetic","scenario":"../../private"}', {}),
        ('{"mode":"synthetic","scenario":"crashloop-oom","llm":"openrouter"}', {}),
        ("[]", {}),
        (" " * 1025, {}),
    ],
)
def test_jobs_reject_malformed_or_capability_expanding_input(host, body, headers):
    _, request = host
    assert request("/api/jobs", method="POST", auth=True, body=body, headers=headers)[0] == 400


def test_single_inflight_job_and_no_external_control_commands(host):
    dashboard, request = host
    assert request("/api/jobs", method="POST", body='{"mode":"local_kind"}')[0] == 401
    status, _, body = request("/api/jobs", method="POST", auth=True, body='{"mode":"local_kind"}')
    assert status == 202
    first = json.loads(body)["job"]
    UUID(first["id"])
    status, _, body = request("/api/jobs", method="POST", auth=True, body='{"mode":"local_kind"}')
    assert status == 409 and json.loads(body)["job"]["id"] == first["id"]
    assert dashboard.current_job()["state"] == "running"
    assert request("/api/stop", method="POST", auth=True, body="{}")[0] == 409
    for path in ("/api/resume", "/api/policy/issue", "/api/exec", "/api/shutdown"):
        assert request(path, method="POST", auth=True, body="{}")[0] == 404


def test_stop_only_accepts_an_idle_authenticated_local_session(host):
    dashboard, request = host
    assert request("/api/stop", method="POST", body="{}")[0] == 401
    assert request("/api/stop", method="POST", auth=True, body='{"force":true}')[0] == 400
    assert request("/api/stop", method="POST", auth=True, body="{}")[0] == 202
    assert dashboard.stopping is True
    with pytest.raises(ValueError):
        dashboard.start({"mode": "local_kind"})


def test_failure_is_safe_and_never_automatically_retried(operator, monkeypatch):
    dashboard = operator.Dashboard()
    dashboard.job = {"state": "running"}
    calls = []

    def rejected(arguments, **kwargs):
        calls.append(arguments)
        raise RuntimeError("PRIVATE_FAILURE_CANARY")

    monkeypatch.setattr(dashboard, "child", rejected)
    dashboard.run_job("local_kind", None)
    assert len(calls) == 1 and dashboard.current_job()["state"] == "failed"
    assert "PRIVATE_FAILURE_CANARY" not in json.dumps(dashboard.current_job())


@pytest.mark.parametrize("mode", ["local_kind", "synthetic"])
def test_fixed_worker_commands_preserve_opa_and_avoid_paid_models(operator, monkeypatch, mode):
    dashboard = operator.Dashboard()
    dashboard.job = {"state": "running"}
    commands = []
    delivery = json.loads((ROOT / "examples/contracts/DeliveryResult.json").read_text())
    batch_id = delivery["receipt"]["telemetry"]["batch_id"]
    pipeline = json.loads((ROOT / "examples/contracts/PipelineResult.json").read_text())

    def child(arguments, **kwargs):
        commands.append((arguments, kwargs))
        if "refresh-reader" in arguments:
            return None
        if "aegisops.collector.cli" in arguments:
            return {"batch_id": batch_id}
        return delivery if mode == "local_kind" else pipeline

    monkeypatch.setattr(dashboard, "child", child)
    dashboard.run_job(mode, "neg-healthy" if mode == "synthetic" else None)
    assert dashboard.current_job()["state"] == "completed"
    assert all("openrouter" not in args and "resume" not in args for args, _ in commands)
    if mode == "local_kind":
        assert commands[1][1].get("database", False) is False
        assert commands[-1][0] == ["-m", "aegisops.controlplane.cli", "consume", batch_id]
    else:
        assert commands[0][0][-4:] == ["--llm", "none", "--authorization", "opa"]
    assert commands[-1][1]["database"] is True


def test_role_environment_never_copies_owner_cloud_or_kube_settings(
    operator, tmp_path, monkeypatch
):
    monkeypatch.setattr(operator, "ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        "AEGISOPS_DATABASE_URL=APP_ROLE_CANARY\nAEGISOPS_MIGRATION_URL=OWNER_CANARY\n"
    )
    for key in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "PGPASSWORD", "KUBECONFIG"):
        monkeypatch.setenv(key, "FOREIGN_ROLE_CANARY")
    collector = operator.role_environment()
    control = operator.role_environment(database=True)
    assert "APP_ROLE_CANARY" not in collector.values()
    assert "OWNER_CANARY" not in control.values()
    assert "FOREIGN_ROLE_CANARY" not in collector.values()
    assert "FOREIGN_ROLE_CANARY" not in control.values()
    assert control["AEGISOPS_DATABASE_URL"] == "APP_ROLE_CANARY"


def test_audit_projection_excludes_raw_context_and_provider_metadata():
    record = AuditRecord.model_validate_json(
        (ROOT / "examples/contracts/AuditRecord.json").read_text()
    )
    result = present_record(record, detail=True)
    assert result["id"] == str(record.run_id) and result["execution_performed"] is False
    assert not {"context", "llm", "telemetry", "log_tail", "prompt", "payload_json"}.intersection(
        result
    )


def test_project_registration_scan_and_report_api_stay_local(host, tmp_path):
    from aegisops.projects.scanner import scan_project

    dashboard, request = host
    root = tmp_path / "chosen-project"
    root.mkdir()
    (root / "main.py").write_text("def broken(:\n pass\n")
    body = json.dumps({"name": "Мой сервис", "path": str(root)}, ensure_ascii=True)
    assert request("/api/projects")[0] == 401
    assert request("/api/projects", method="POST", body=body)[0] == 401
    assert (
        request(
            "/api/projects",
            method="POST",
            auth=True,
            body=body,
            headers={"Origin": "https://attacker.example"},
        )[0]
        == 403
    )
    status, _, data = request("/api/projects", method="POST", auth=True, body=body)
    assert status == 201
    identifier = UUID(json.loads(data)["project_id"])
    listed = json.loads(request("/api/projects", auth=True)[2])["projects"]
    assert len(listed) == 1 and listed[0]["name"] == "Мой сервис"
    assert "device" not in listed[0] and "inode" not in listed[0]
    project = dashboard.projects.get(identifier)
    report = scan_project(project)
    dashboard.projects.save_report(report)
    history = json.loads(request(f"/api/projects/{identifier}", auth=True)[2])["reports"]
    assert history[0]["id"] == str(report.id) and "findings" not in history[0]
    status, _, result = request(f"/api/project-reports/{report.id}", auth=True)
    assert status == 200 and json.loads(result)["findings"][0]["code"] == "syntax"
    assert json.loads(result)["execution_performed"] is False
    assert json.loads(result)["rules_version"] == "local-static-v2"
    assert json.loads(result)["findings"][0]["recommendation"]
    job = json.dumps({"mode": "project", "project_id": str(identifier)})
    assert request("/api/jobs", method="POST", auth=True, body=job)[0] == 202
    remove = json.dumps({"project_id": str(identifier)})
    assert request("/api/projects/remove", method="POST", auth=True, body=remove)[0] == 400
    dashboard.job["state"] = "completed"
    assert request("/api/projects/remove", method="POST", auth=True, body=remove)[0] == 200
    assert (root / "main.py").exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "x", "path": "https://github.com/a/b"},
        {"name": "x", "path": "relative"},
        {"name": "x", "path": "C:/missing", "command": "whoami"},
        {"name": "x", "path": "C:/missing", "llm": "openrouter"},
    ],
)
def test_project_api_rejects_remote_paths_and_extra_capabilities(host, payload):
    _, request = host
    assert request("/api/projects", method="POST", auth=True, body=json.dumps(payload))[0] == 400


def test_project_worker_does_not_launch_any_child_process(operator, tmp_path, monkeypatch):
    dashboard = operator.Dashboard()
    dashboard.projects = ProjectRepository(tmp_path / "registry")
    root = tmp_path / "project"
    root.mkdir()
    (root / "main.py").write_text("print('DO_NOT_EXECUTE')\n")
    project = dashboard.projects.add("Local", str(root))
    dashboard.job = {"state": "running"}

    def forbidden(*args, **kwargs):
        pytest.fail("project check attempted a child process")

    monkeypatch.setattr(dashboard, "child", forbidden)
    dashboard.run_job("project", str(project.id))
    assert dashboard.job["state"] == "passed"
    assert dashboard.projects.history(project.id)[0].files_checked == 1


def ai_report(dashboard, tmp_path):
    from aegisops.projects.scanner import scan_project

    root = tmp_path / "ai-source"
    root.mkdir()
    (root / "compose.yaml").write_text("services:\n  PRIVATE_NAME_CANARY:\n    privileged: true\n")
    project = dashboard.projects.add("PRIVATE_NAME_CANARY", str(root))
    report = scan_project(project)
    dashboard.projects.save_report(report)
    return report


def test_project_ai_preview_is_authenticated_source_free_and_never_calls_provider(host, tmp_path):
    dashboard, request = host
    report = ai_report(dashboard, tmp_path)
    path = f"/api/project-ai/{report.id}"
    assert request(path)[0] == 401
    status, _, body = request(path, auth=True)
    data = json.loads(body)
    assert status == 200 and data["analysis"] == {"status": "not_requested"}
    assert data["payload"]["categories"] == [
        {"code": "privileged", "severity": "warning", "count": 1}
    ]
    assert b"PRIVATE_NAME_CANARY" not in body and b"compose.yaml" not in body
    assert dashboard.analyses.read_claim(report.id) is None


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"consent": False},
        {"consent": "other"},
        {"consent": "project-summary-v1", "model": "other"},
        {"consent": "project-summary-v1", "endpoint": "http://attacker"},
        {"consent": "project-summary-v1", "api_key": "PRIVATE_KEY_CANARY"},
        {"consent": "project-summary-v1", "payload": {"source": "PRIVATE_SOURCE_CANARY"}},
    ],
)
def test_project_ai_cannot_be_requested_without_consent_or_expand_payload(host, tmp_path, extra):
    dashboard, request = host
    report = ai_report(dashboard, tmp_path)
    body = json.dumps({"mode": "project_ai", "report_id": str(report.id), **extra})
    assert request("/api/jobs", method="POST", auth=True, body=body)[0] == 400
    assert dashboard.analyses.read_claim(report.id) is None


def test_project_ai_claim_blocks_duplicate_even_after_idle_or_restart(host, tmp_path, operator):
    dashboard, request = host
    report = ai_report(dashboard, tmp_path)
    payload = {"mode": "project_ai", "report_id": str(report.id), "consent": "project-summary-v1"}
    assert request("/api/jobs", method="POST", body=json.dumps(payload))[0] == 401
    assert (
        request(
            "/api/jobs",
            method="POST",
            auth=True,
            body=json.dumps(payload),
            headers={"Origin": "https://attacker.example"},
        )[0]
        == 403
    )
    assert request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))[0] == 202
    assert dashboard.analyses.state(report)["status"] == "pending"
    assert request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))[0] == 409
    dashboard.job["state"] = "failed"
    assert request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))[0] == 400
    reopened = operator.Dashboard()
    reopened.projects, reopened.analyses = dashboard.projects, dashboard.analyses
    with pytest.raises(Exception, match="уже запрошен"):
        reopened.start(payload)


def test_project_ai_failure_preserves_static_report_and_never_leaks_provider_details(
    host,
    tmp_path,
    monkeypatch,
):
    dashboard, _ = host
    report = ai_report(dashboard, tmp_path)
    dashboard.analyses.claim(report)
    dashboard.job = {"state": "running"}
    calls = []

    def rejected(identifier):
        calls.append(identifier)
        raise RuntimeError("PRIVATE_PROVIDER_CANARY")

    monkeypatch.setattr(dashboard, "project_ai_child", rejected)
    type(dashboard).run_job(dashboard, "project_ai", str(report.id))
    assert len(calls) == 1 and dashboard.job["state"] == "failed"
    assert dashboard.analyses.state(report)["status"] == "failed"
    assert "PRIVATE_PROVIDER_CANARY" not in json.dumps(dashboard.job)
    assert dashboard.projects.read_report(report.id) == report


def test_project_ai_completion_is_read_back_from_private_result(host, tmp_path, monkeypatch):
    from aegisops.domain.common import utc_now
    from aegisops.project_ai.models import Explanation, Result

    dashboard, request = host
    report = ai_report(dashboard, tmp_path)
    claim = dashboard.analyses.claim(report)
    dashboard.job = {"state": "running"}

    def completed(identifier):
        result = Result(
            report_id=identifier,
            report_sha256=claim.report_sha256,
            completed_at=utc_now(),
            status="completed",
            response=Explanation(summary="Разбор", actions=(), limitations="Сводка"),
        )
        dashboard.analyses.write(identifier, "result", result.model_dump_json())

    monkeypatch.setattr(dashboard, "project_ai_child", completed)
    type(dashboard).run_job(dashboard, "project_ai", str(report.id))
    assert dashboard.job["state"] == "completed"
    status, _, body = request(f"/api/project-ai/{report.id}", auth=True)
    assert status == 200 and json.loads(body)["analysis"]["response"]["summary"] == "Разбор"


def test_project_ai_fixed_child_never_inherits_dev_or_database_credentials(operator, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    if operator.os.name != "nt":
        pytest.skip("Windows runtime credential launcher")
    for name in (
        "AEGISOPS_LLM_API_KEY",
        "AEGISOPS_RUNTIME_LLM_API_KEY",
        "PGPASSWORD",
        "KUBECONFIG",
    ):
        monkeypatch.setenv(name, "PRIVATE_ROLE_CANARY")
    monkeypatch.setenv("PSModulePath", "INCOMPATIBLE_SHELL_MODULE_PATH")
    calls = []

    def child(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"ok":true}')

    monkeypatch.setattr(operator.subprocess, "run", child)
    identifier = uuid4()
    operator.Dashboard().project_ai_child(identifier)
    command, options = calls[0]
    assert command[0] == "powershell.exe"
    assert command[-4:] == ["-Mode", "project-report", "-ReportId", str(identifier)]
    assert "PRIVATE_ROLE_CANARY" not in options["env"].values()
    assert not any(key.upper() == "PSMODULEPATH" for key in options["env"])
    assert (
        options["timeout"] == 80
        and options["creationflags"] == operator.subprocess.CREATE_NO_WINDOW
    )


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"consent": False},
        {"consent": "other"},
        {"consent": "python-checks-v1", "command": "whoami"},
        {"consent": "python-checks-v1", "image": "user-image"},
        {"consent": "python-checks-v1", "mount": "/host"},
        {"consent": "python-checks-v1", "network": "host"},
        {"consent": "python-checks-v1", "environment": "arbitrary"},
        {"consent": "python-checks-v1", "environment": ["python-data-v1"]},
        {"consent": "python-checks-v1", "environment": "../image"},
    ],
)
def test_runner_api_requires_consent_and_rejects_extra_authority(host, tmp_path, extra):
    dashboard, request = host
    report = ai_report(dashboard, tmp_path)
    payload = {"mode": "project_runner", "project_id": str(report.project_id), **extra}
    assert request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))[0] == 400
    assert dashboard.runs.history(report.project_id) == []


def test_runner_history_and_job_lock_are_authenticated_and_persisted(host, tmp_path):
    dashboard, request = host
    report = ai_report(dashboard, tmp_path)
    path = f"/api/project-runs/{report.project_id}"
    assert request(path)[0] == 401
    payload = json.dumps(
        {
            "mode": "project_runner",
            "project_id": str(report.project_id),
            "consent": "python-checks-v1",
        }
    )
    assert request("/api/jobs", method="POST", body=payload)[0] == 401
    assert (
        request(
            "/api/jobs",
            method="POST",
            auth=True,
            body=payload,
            headers={"Origin": "https://attacker.example"},
        )[0]
        == 403
    )
    assert request("/api/jobs", method="POST", auth=True, body=payload)[0] == 202
    identifier = UUID(dashboard.job["run_id"])
    assert request("/api/jobs", method="POST", auth=True, body=payload)[0] == 409
    history = json.loads(request(path, auth=True)[2])["runs"]
    assert len(history) == 1 and history[0]["claim"]["id"] == str(identifier)
    assert request(f"/api/project-run/{identifier}")[0] == 401
    assert json.loads(request(f"/api/project-run/{identifier}", auth=True)[2])["result"] is None


def test_runner_worker_uses_fixed_script_and_preserves_unknown_state(host, tmp_path, monkeypatch):
    dashboard, _ = host
    report = ai_report(dashboard, tmp_path)
    claim = dashboard.runs.create(report.project_id)
    dashboard.job = {"state": "running", "run_id": str(claim.id)}
    calls = []

    def child(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return {"recorded": False}

    monkeypatch.setattr(dashboard, "child", child)
    type(dashboard).run_job(dashboard, "project_runner", str(claim.id))
    assert calls == [(["scripts/project-runner-local.py", str(claim.id)], {"timeout": 220})]
    assert dashboard.job["state"] == "failed" and dashboard.runs.result(claim.id) is None


def test_runner_api_accepts_only_fixed_dependency_environment(host, tmp_path):
    dashboard, request = host
    report = ai_report(dashboard, tmp_path)
    payload = {
        "mode": "project_runner",
        "project_id": str(report.project_id),
        "consent": "python-checks-v1",
        "environment": "python-data-v1",
    }
    assert request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))[0] == 202
    claim = dashboard.runs.claim(UUID(dashboard.job["run_id"]))
    assert claim.environment == "python-data-v1"


def test_static_comparison_api_reads_saved_reports_without_new_jobs(host, tmp_path, monkeypatch):
    from aegisops.projects.scanner import scan_project

    dashboard, request = host
    first = ai_report(dashboard, tmp_path)
    project = dashboard.projects.get(first.project_id)
    Path(project.path, "compose.yaml").write_text("services:\n  app:\n    image: app:1.2\n")
    second = scan_project(project)
    dashboard.projects.save_report(second)

    def forbidden(*args, **kwargs):
        pytest.fail("comparison started a child or model")

    monkeypatch.setattr(dashboard, "child", forbidden)
    monkeypatch.setattr(dashboard, "project_ai_child", forbidden)
    path = f"/api/project-comparison/{first.id}/{second.id}"
    assert request(path)[0] == 401
    status, _, body = request(path, auth=True)
    data = json.loads(body)
    assert status == 200 and data["comparable"]
    assert len(data["removed"]) == 1 and not data["added"]
    assert dashboard.job is None
    assert request(f"/api/project-comparison/{first.id}/{first.id}", auth=True)[0] == 503
    other_root = tmp_path / "other-project"
    other_root.mkdir()
    (other_root / "main.py").write_text("pass\n")
    other = dashboard.projects.add("Other", str(other_root))
    other_report = scan_project(other)
    dashboard.projects.save_report(other_report)
    assert request(f"/api/project-comparison/{first.id}/{other_report.id}", auth=True)[0] == 503
    dashboard.projects.remove(first.project_id)
    assert request(path, auth=True)[0] == 503


def test_run_comparison_api_handles_unfinished_and_removed_projects(host, tmp_path):
    dashboard, request = host
    report = ai_report(dashboard, tmp_path)
    first = dashboard.runs.create(report.project_id)
    second = dashboard.runs.create(report.project_id)
    path = f"/api/run-comparison/{first.id}/{second.id}"
    assert request(path)[0] == 401
    status, _, body = request(path, auth=True)
    data = json.loads(body)
    assert status == 200 and data["reason"] == "unfinished" and not data["transitions"]
    assert request(f"/api/run-comparison/{first.id}/{first.id}", auth=True)[0] == 503
    dashboard.projects.remove(report.project_id)
    assert request(path, auth=True)[0] == 503


@pytest.mark.parametrize(
    "path",
    [
        "/api/project-comparison/not-a-uuid/missing",
        "/api/project-comparison/too/many/segments",
        "/api/run-comparison/not-a-uuid/missing",
    ],
)
def test_comparison_malformed_ids_return_safe_read_failure(host, path):
    _, request = host
    status, _, body = request(path, auth=True)
    assert status == 503 and json.loads(body) == {"error": "Данные временно недоступны"}
