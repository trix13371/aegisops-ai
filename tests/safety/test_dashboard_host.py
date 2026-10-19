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
from aegisops.project_ai.full_repository import FullAnalysisRepository
from aegisops.project_ai.repository import AnalysisRepository
from aegisops.project_ai.review_repository import ReviewAnalysisRepository
from aegisops.project_reviews.repository import ReviewRepository
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.repository import ProjectRepository

pytestmark = pytest.mark.safety
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def operator(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "local_ui_operator", ROOT / "scripts/dashboard-local.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from aegisops.assistant.repository import AssistantRepository as RealAssistantRepository

    monkeypatch.setattr(
        module,
        "AssistantRepository",
        lambda *args, **kwargs: RealAssistantRepository(
            tmp_path / "assistant.json", tmp_path / "attempts"
        ),
    )
    return module


@pytest.fixture
def host(operator, monkeypatch, tmp_path):
    dashboard = operator.Dashboard()
    dashboard.projects = ProjectRepository(tmp_path / "private-registry")
    dashboard.analyses = AnalysisRepository(tmp_path / "private-ai")
    dashboard.runs = RunRepository(tmp_path / "private-runs")
    from aegisops.project_checks.repository import FullCheckRepository

    dashboard.checks = FullCheckRepository(
        tmp_path / "private-checks", dashboard.projects, dashboard.runs
    )
    dashboard.full_analyses = FullAnalysisRepository(tmp_path / "private-full-ai", dashboard.checks)
    from aegisops.project_code.evaluation_repository import EvalRepository
    from aegisops.project_code.lab import LabRepository
    from aegisops.project_code.repository import CodeRepository

    dashboard.code_analyses = CodeRepository(tmp_path / "private-code-ai", dashboard.projects)
    dashboard.code_lab = LabRepository(tmp_path / "private-lab", dashboard.code_analyses)
    dashboard.code_evaluation = EvalRepository(tmp_path / "private-eval", dashboard.projects)
    dashboard.reviews = ReviewRepository(
        tmp_path / "private-reviews", dashboard.projects, dashboard.runs
    )
    dashboard.review_analyses = ReviewAnalysisRepository(tmp_path / "private-review-ai")
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


@pytest.mark.parametrize(
    "path",
    [
        "/api/overview",
        "/api/job",
        "/api/ping",
        "/api/audit/bad",
        "/api/project-reviews/bad",
        "/api/project-review/bad",
    ],
)
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
        {"consent": "python-checks-v1", "environment": "node-js-v1"},
        {"consent": "javascript-checks-v1", "environment": "minimal"},
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


def test_javascript_api_requires_matching_consent_and_persists_profile(host, tmp_path):
    dashboard, request = host
    report = ai_report(dashboard, tmp_path)
    payload = {
        "mode": "project_runner",
        "project_id": str(report.project_id),
        "consent": "javascript-checks-v1",
        "environment": "node-js-v1",
    }
    assert request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))[0] == 202
    identifier = UUID(dashboard.job["run_id"])
    value = json.loads(request(f"/api/project-run/{identifier}", auth=True)[2])
    assert value["claim"]["profile"] == "javascript-checks-v1"
    assert value["claim"]["environment"] == "node-js-v1"


def test_package_compatibility_api_is_authenticated_bounded_and_does_not_execute(
    host, tmp_path, monkeypatch
):
    dashboard, request = host
    report = ai_report(dashboard, tmp_path)
    root = Path(dashboard.projects.get(report.project_id).path)
    (root / "package.json").write_text(
        json.dumps(
            {
                "devDependencies": {"vitest": "^4", "typescript": "^5"},
                "scripts": {"test": "PRIVATE_SCRIPT_MUST_NOT_EXECUTE"},
            }
        )
    )
    monkeypatch.setattr(dashboard, "child", lambda *args, **kwargs: pytest.fail("executed project"))
    path = f"/api/project-compatibility/{report.project_id}"
    assert request(path)[0] == 401
    status, _, body = request(path, auth=True)
    value = json.loads(body)
    assert status == 200 and value["framework"] == "vitest"
    assert value["suggested_environment"] == "node-vitest-v1"
    assert "dependency_versions_unverified" in value["reasons"]
    assert b"PRIVATE_SCRIPT" not in body and not dashboard.current_job()
    assert dashboard.runs.history(report.project_id) == []
    (root / "package.json").write_text('{"dependencies":{},"dependencies":{}}')
    value = json.loads(request(path, auth=True)[2])
    assert value["status"] == "unsupported" and value["reasons"] == ["package_invalid"]


def test_vitest_job_uses_its_own_fixed_consent_and_environment(host, tmp_path):
    dashboard, request = host
    report = ai_report(dashboard, tmp_path)
    payload = {
        "mode": "project_runner",
        "project_id": str(report.project_id),
        "environment": "node-vitest-v1",
        "consent": "javascript-checks-v1",
    }
    assert request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))[0] == 400
    payload["consent"] = "javascript-vitest-v1"
    assert request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))[0] == 202
    claim = dashboard.runs.claim(UUID(dashboard.job["run_id"]))
    assert claim.profile == "javascript-vitest-v1" and claim.environment == "node-vitest-v1"


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


def create_review(dashboard, request, tmp_path):
    report = ai_report(dashboard, tmp_path)
    payload = {
        "project_id": str(report.project_id),
        "static_report_id": str(report.id),
        "run_id": None,
    }
    status, _, body = request(
        "/api/project-reviews", method="POST", auth=True, body=json.dumps(payload)
    )
    assert status == 201
    return report, json.loads(body)["review"], payload


def test_combined_review_create_reads_and_dedup_never_start_children(host, tmp_path, monkeypatch):
    dashboard, request = host

    def forbidden(*args, **kwargs):
        pytest.fail("review read or creation attempted a child, model or scan")

    monkeypatch.setattr(dashboard, "child", forbidden)
    monkeypatch.setattr(dashboard, "project_ai_child", forbidden)
    monkeypatch.setattr(dashboard, "project_review_ai_child", forbidden)
    report, review, payload = create_review(dashboard, request, tmp_path)
    monkeypatch.setattr(dashboard, "run_job", forbidden)
    status, _, body = request(
        "/api/project-reviews", method="POST", auth=True, body=json.dumps(payload)
    )
    assert status == 201 and json.loads(body)["review"]["id"] == review["id"]
    status, _, body = request(f"/api/project-reviews/{report.project_id}", auth=True)
    assert status == 200 and json.loads(body)["reviews"] == [review]
    status, _, body = request(f"/api/project-review/{review['id']}", auth=True)
    data = json.loads(body)
    assert status == 200 and data["review"] == review
    assert data["analysis"] == {"status": "not_requested"}
    assert data["ai_profile"] == "project-review-summary-v1"
    assert data["payload"]["categories"] == [
        {"code": "privileged", "severity": "warning", "count": 1}
    ]
    assert b"PRIVATE_NAME_CANARY" not in body and b"compose.yaml" not in body
    assert "missing_runner" in review["limitations"]
    assert dashboard.current_job() is None
    assert dashboard.review_analyses.read_claim(UUID(review["id"])) is None


@pytest.mark.parametrize("extra", [{"command": "whoami"}, {"model": "other"}, {"source": "x"}])
def test_combined_review_post_is_session_origin_and_exact_schema_bound(host, tmp_path, extra):
    dashboard, request = host
    report = ai_report(dashboard, tmp_path)
    payload = {
        "project_id": str(report.project_id),
        "static_report_id": str(report.id),
        "run_id": None,
    }
    path = "/api/project-reviews"
    assert request(path, method="POST", body=json.dumps(payload))[0] == 401
    assert (
        request(
            path,
            method="POST",
            auth=True,
            body=json.dumps(payload),
            headers={"Origin": "https://attacker.example"},
        )[0]
        == 403
    )
    assert request(path, method="POST", auth=True, body=json.dumps({**payload, **extra}))[0] == 400
    assert dashboard.reviews.history(report.project_id) == ()


def test_combined_review_rejects_empty_pending_and_other_project_evidence(host, tmp_path):
    from aegisops.projects.scanner import scan_project

    dashboard, request = host
    report = ai_report(dashboard, tmp_path)
    payload = {"project_id": str(report.project_id), "static_report_id": None, "run_id": None}
    assert (
        request("/api/project-reviews", method="POST", auth=True, body=json.dumps(payload))[0]
        == 400
    )
    pending = dashboard.runs.create(report.project_id)
    payload["run_id"] = str(pending.id)
    assert (
        request("/api/project-reviews", method="POST", auth=True, body=json.dumps(payload))[0]
        == 400
    )
    other_root = tmp_path / "other-review-project"
    other_root.mkdir()
    (other_root / "main.py").write_text("pass\n")
    other = dashboard.projects.add("Other", str(other_root))
    other_report = scan_project(other)
    dashboard.projects.save_report(other_report)
    payload.update(static_report_id=str(other_report.id), run_id=None)
    assert (
        request("/api/project-reviews", method="POST", auth=True, body=json.dumps(payload))[0]
        == 400
    )
    assert dashboard.reviews.history(report.project_id) == ()


def test_combined_review_runner_only_preview_omits_logs_hashes_and_identifiers(host, tmp_path):
    from aegisops.domain.common import utc_now
    from aegisops.project_runner.models import RunResult, SnapshotInfo, ToolCheck

    dashboard, request = host
    report = ai_report(dashboard, tmp_path)
    claim = dashboard.runs.create(report.project_id)
    result = RunResult(
        id=claim.id,
        project_id=claim.project_id,
        completed_at=utc_now(),
        status="issues",
        reason="checks_completed",
        container_removed=True,
        execution_scope="container",
        image_id="sha256:" + "a" * 64,
        snapshot=SnapshotInfo(sha256="b" * 64, files=1, python_files=1, bytes=10, skipped=0),
        checks=(
            ToolCheck(tool="ruff", status="passed", exit_code=0, output="", output_truncated=False),
            ToolCheck(
                tool="pytest",
                status="failed",
                exit_code=1,
                output="PRIVATE_LOG_CANARY arbitrary_package",
                output_truncated=False,
            ),
        ),
    )
    dashboard.runs.save(result)
    payload = {
        "project_id": str(report.project_id),
        "static_report_id": None,
        "run_id": str(claim.id),
    }
    status, _, body = request(
        "/api/project-reviews", method="POST", auth=True, body=json.dumps(payload)
    )
    review = json.loads(body)["review"]
    assert status == 201 and review["static"] is None
    assert review["runner"]["checks"][1]["status"] == "failed"
    status, _, body = request(f"/api/project-review/{review['id']}", auth=True)
    preview = json.dumps(json.loads(body)["payload"])
    assert status == 200 and "runner_failed" in preview
    for omitted in (
        str(claim.id),
        str(report.project_id),
        "b" * 64,
        "PRIVATE_LOG",
        "arbitrary_package",
    ):
        assert omitted not in preview
    assert b"PRIVATE_LOG_CANARY" not in body


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"consent": False},
        {"consent": "project-summary-v1"},
        {"consent": "project-review-summary-v1", "endpoint": "http://attacker"},
        {"consent": "project-review-summary-v1", "model": "other"},
        {"consent": "project-review-summary-v1", "payload": {"source": "PRIVATE_SOURCE"}},
    ],
)
def test_combined_review_ai_requires_separate_consent_and_no_extra_authority(host, tmp_path, extra):
    dashboard, request = host
    _, review, _ = create_review(dashboard, request, tmp_path)
    payload = {"mode": "project_review_ai", "review_id": review["id"], **extra}
    assert request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))[0] == 400
    assert dashboard.review_analyses.read_claim(UUID(review["id"])) is None


def test_combined_review_ai_global_lock_and_durable_claim_survive_duplicate_bundle(host, tmp_path):
    dashboard, request = host
    _, review, selection = create_review(dashboard, request, tmp_path)
    payload = {
        "mode": "project_review_ai",
        "review_id": review["id"],
        "consent": "project-review-summary-v1",
    }
    dashboard.job = {"id": "other-job", "state": "running"}
    assert request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))[0] == 409
    assert dashboard.review_analyses.read_claim(UUID(review["id"])) is None
    dashboard.job = None
    status, _, body = request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))
    assert status == 202 and json.loads(body)["job"]["review_id"] == review["id"]
    dashboard.job["state"] = "failed"
    duplicate = json.loads(
        request("/api/project-reviews", method="POST", auth=True, body=json.dumps(selection))[2]
    )["review"]
    assert duplicate["id"] == review["id"]
    assert request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))[0] == 400
    state = json.loads(request(f"/api/project-review/{review['id']}", auth=True)[2])
    assert state["analysis"]["status"] == "pending"


def test_combined_review_ai_failure_is_safe_and_preserves_evidence(host, tmp_path, monkeypatch):
    dashboard, request = host
    _, review, _ = create_review(dashboard, request, tmp_path)
    identifier = UUID(review["id"])
    dashboard.review_analyses.claim(dashboard.reviews.get(identifier))
    dashboard.job = {"state": "running"}
    calls = []

    def rejected(value):
        calls.append(value)
        raise RuntimeError("PRIVATE_PROVIDER_REVIEW_CANARY")

    monkeypatch.setattr(dashboard, "project_review_ai_child", rejected)
    type(dashboard).run_job(dashboard, "project_review_ai", str(identifier))
    assert calls == [identifier] and dashboard.job["state"] == "failed"
    assert "PRIVATE_PROVIDER" not in json.dumps(dashboard.job)
    assert dashboard.review_analyses.state(dashboard.reviews.get(identifier))["status"] == "failed"


def test_combined_review_ai_completion_reads_back_result(host, tmp_path, monkeypatch):
    from aegisops.domain.common import utc_now
    from aegisops.project_ai.review_models import ReviewExplanation
    from aegisops.project_ai.review_repository import ReviewResult

    dashboard, request = host
    _, review, _ = create_review(dashboard, request, tmp_path)
    identifier = UUID(review["id"])
    claim = dashboard.review_analyses.claim(dashboard.reviews.get(identifier))
    dashboard.job = {"state": "running"}

    def completed(value):
        result = ReviewResult(
            review_id=value,
            review_sha256=claim.review_sha256,
            completed_at=utc_now(),
            status="completed",
            response=ReviewExplanation(
                summary="Разбор сводки", actions=(), limitations="Только выбранные проверки"
            ),
        )
        dashboard.review_analyses.write(value, "result", result.model_dump_json())

    monkeypatch.setattr(dashboard, "project_review_ai_child", completed)
    type(dashboard).run_job(dashboard, "project_review_ai", str(identifier))
    assert dashboard.job["state"] == "completed"
    status, _, body = request(f"/api/project-review/{identifier}", auth=True)
    assert status == 200 and json.loads(body)["analysis"]["response"]["summary"] == "Разбор сводки"


def test_combined_review_child_uses_fixed_mode_and_role_environment(operator, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    if operator.os.name != "nt":
        pytest.skip("Windows runtime credential launcher")
    monkeypatch.setenv("OPENROUTER_API_KEY", "PRIVATE_REVIEW_KEY_CANARY")
    monkeypatch.setenv("PSModulePath", "FOREIGN_MODULE_PATH")
    calls = []

    def child(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"ok":true}')

    monkeypatch.setattr(operator.subprocess, "run", child)
    identifier = uuid4()
    operator.Dashboard().project_review_ai_child(identifier)
    command, options = calls[0]
    assert command[-4:] == ["-Mode", "project-review", "-ReviewId", str(identifier)]
    assert "PRIVATE_REVIEW_KEY_CANARY" not in options["env"].values()
    assert not any(name.upper() == "PSMODULEPATH" for name in options["env"])
    assert options["timeout"] == 80


def test_combined_review_removed_or_changed_evidence_is_not_presented_as_valid(host, tmp_path):
    dashboard, request = host
    report, review, _ = create_review(dashboard, request, tmp_path)
    path = f"/api/project-review/{review['id']}"
    dashboard.projects.remove(report.project_id)
    status, _, body = request(path, auth=True)
    assert status == 503 and json.loads(body) == {"error": "Данные временно недоступны"}


@pytest.mark.parametrize(
    "env,consent",
    [
        ("minimal", "project-full-check-v1"),
        ("python-data-v1", "project-full-check-v1"),
        ("node-js-v1", "project-full-check-v1"),
        ("node-vitest-v1", "project-full-check-v1"),
    ],
)
def test_full_check_api_accepts_valid_environments(host, tmp_path, env, consent):
    dashboard, request = host
    root = tmp_path / f"proj-{env}"
    root.mkdir()
    proj = dashboard.projects.add(f"proj-{env}", str(root))
    payload = {
        "mode": "project_full_check",
        "project_id": str(proj.id),
        "consent": consent,
        "environment": env,
    }
    status, _, body = request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))
    assert status == 202
    data = json.loads(body)["job"]
    assert data["mode"] == "project_full_check"
    check_id = UUID(data["check_id"])
    claim = dashboard.checks.claim(check_id)
    assert claim.environment == env


@pytest.mark.parametrize(
    "extra",
    [
        {"consent": "python-checks-v1"},
        {"consent": "project-full-check-v1", "command": "whoami"},
        {"consent": "project-full-check-v1", "path": "/etc/passwd"},
        {"consent": "project-full-check-v1", "model": "openrouter"},
        {"consent": "project-full-check-v1", "environment": "invalid-env"},
    ],
)
def test_full_check_rejects_invalid_consent_or_extra_keys(host, tmp_path, extra):
    dashboard, request = host
    root = tmp_path / "proj-rej"
    root.mkdir()
    proj = dashboard.projects.add("proj-rej", str(root))
    payload = {"mode": "project_full_check", "project_id": str(proj.id), **extra}
    status, _, _ = request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))
    assert status == 400
    assert dashboard.checks.history(proj.id) == []


def test_full_check_auth_origin_unregistered_and_deleted_project(host, tmp_path):
    dashboard, request = host
    root = tmp_path / "proj-auth"
    root.mkdir()
    proj = dashboard.projects.add("proj-auth", str(root))
    payload = {
        "mode": "project_full_check",
        "project_id": str(proj.id),
        "consent": "project-full-check-v1",
    }
    body = json.dumps(payload)
    assert request("/api/jobs", method="POST", body=body)[0] == 401
    assert (
        request(
            "/api/jobs", method="POST", auth=True, body=body, headers={"Origin": "http://attacker"}
        )[0]
        == 403
    )
    unreg = json.dumps(
        {
            "mode": "project_full_check",
            "project_id": "00000000-0000-0000-0000-000000000000",
            "consent": "project-full-check-v1",
        }
    )
    assert request("/api/jobs", method="POST", auth=True, body=unreg)[0] == 400
    status, _, resp_body = request("/api/jobs", method="POST", auth=True, body=body)
    assert status == 202
    check_id = json.loads(resp_body)["job"]["check_id"]
    dashboard.job["state"] = "completed"
    dashboard.projects.remove(proj.id)
    assert request(f"/api/project-checks/{proj.id}", auth=True)[0] == 503
    assert request(f"/api/project-check/{check_id}", auth=True)[0] == 503


def test_full_check_get_endpoints_no_model_invocation_and_duplicate_lock(
    host, tmp_path, monkeypatch
):
    dashboard, request = host
    root = tmp_path / "proj-get"
    root.mkdir()
    proj = dashboard.projects.add("proj-get", str(root))
    claim = dashboard.checks.create(proj.id, "minimal")

    def fail_child(*args, **kwargs):
        pytest.fail("GET endpoint must not spawn child/model")

    monkeypatch.setattr(dashboard, "child", fail_child)
    monkeypatch.setattr(dashboard, "project_ai_child", fail_child)
    monkeypatch.setattr(dashboard, "project_review_ai_child", fail_child)

    assert request(f"/api/project-checks/{proj.id}")[0] == 401
    assert request(f"/api/project-check/{claim.id}")[0] == 401
    status, _, hist_data = request(f"/api/project-checks/{proj.id}", auth=True)
    assert status == 200
    history = json.loads(hist_data)["checks"]
    assert len(history) == 1 and history[0]["claim"]["id"] == str(claim.id)
    assert history[0]["result"] is None
    status, _, detail_data = request(f"/api/project-check/{claim.id}", auth=True)
    assert status == 200
    detail = json.loads(detail_data)
    assert detail["claim"]["id"] == str(claim.id) and detail["result"] is None

    for bad in [
        "/api/project-checks/bad",
        "/api/project-check/not-a-uuid",
        "/api/project-checks/../file",
    ]:
        st, _, b = request(bad, auth=True)
        assert st == 503 and json.loads(b) == {"error": "Данные временно недоступны"}

    payload = {
        "mode": "project_full_check",
        "project_id": str(proj.id),
        "consent": "project-full-check-v1",
    }
    status1, _, body1 = request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))
    assert status1 == 202
    job1 = json.loads(body1)["job"]
    status2, _, body2 = request("/api/jobs", method="POST", auth=True, body=json.dumps(payload))
    assert status2 == 409
    assert json.loads(body2)["job"]["id"] == job1["id"]
    assert len(dashboard.checks.history(proj.id)) == 2


def test_full_check_run_job_execution_isolated(operator, tmp_path, monkeypatch):
    dashboard = operator.Dashboard()
    dashboard.projects = ProjectRepository(tmp_path / "reg")
    dashboard.runs = RunRepository(tmp_path / "runs")
    from aegisops.project_checks.repository import FullCheckRepository

    dashboard.checks = FullCheckRepository(tmp_path / "checks", dashboard.projects, dashboard.runs)
    root = tmp_path / "work"
    root.mkdir()
    proj = dashboard.projects.add("work", str(root))
    claim = dashboard.checks.create(proj.id, "minimal")
    dashboard.job = {"state": "running", "check_id": str(claim.id)}

    calls = []

    def fake_child(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return None

    monkeypatch.setattr(dashboard, "child", fake_child)
    operator.Dashboard.run_job(dashboard, "project_full_check", str(claim.id))
    assert calls == [(["scripts/project-check-local.py", str(claim.id)], {"timeout": 250})]
    assert dashboard.job["state"] == "failed"
    assert dashboard.checks.result(claim.id) is None


@pytest.fixture
def full_evidence(host, tmp_path):
    dashboard, request = host
    project_dir = tmp_path / "sample-proj"
    project_dir.mkdir()
    (project_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (project_dir / "compose.yaml").write_text(
        "services:\n  app:\n    image: test\n", encoding="utf-8"
    )
    project = dashboard.projects.add("Sample", str(project_dir))
    from aegisops.domain.common import utc_now
    from aegisops.project_checks.models import build_result
    from aegisops.project_checks.snapshot import scan_snapshot
    from aegisops.project_runner.models import RunResult, ToolCheck
    from aegisops.project_runner.snapshot import snapshot_project

    claim = dashboard.checks.create(project.id, "minimal")
    snap = snapshot_project(project, claim.profile)
    static_rep = scan_snapshot(project.id, snap)
    dashboard.projects.save_report(static_rep)
    tools = (
        ToolCheck(tool="ruff", status="passed", exit_code=0, output="ok", output_truncated=False),
        ToolCheck(tool="pytest", status="passed", exit_code=0, output="ok", output_truncated=False),
    )
    run_res = RunResult(
        id=claim.id,
        project_id=project.id,
        completed_at=utc_now(),
        status="passed",
        reason="checks_completed",
        snapshot=snap.info,
        image_id="sha256:" + "a" * 64,
        checks=tools,
        container_removed=True,
        execution_scope="container",
    )
    dashboard.runs.save(run_res)
    check_res = build_result(claim, static_rep, run_res)
    dashboard.checks.save(check_res)
    return dashboard, request, check_res


def test_full_check_ai_get_flow(full_evidence):
    dashboard, request, check = full_evidence
    status, _, data = request(f"/api/project-check-ai/{check.id}")
    assert status == 401
    status, _, data = request(
        f"/api/project-check-ai/{check.id}", auth=True, headers={"Host": "evil.com"}
    )
    assert status == 403
    status, _, data = request("/api/project-check-ai/not-a-uuid", auth=True)
    assert status == 503
    assert json.loads(data) == {"error": "Данные временно недоступны"}
    status, _, data = request(f"/api/project-check-ai/{check.id}", auth=True)
    assert status == 200
    parsed = json.loads(data)
    assert parsed["check_id"] == str(check.id)
    assert parsed["analysis"] == {"status": "not_requested"}
    assert parsed["profile"] == "project-full-check-summary-v1"
    assert parsed["model"] == "deepseek/deepseek-v4.1-flash"
    raw_pl = json.dumps(parsed["payload"])
    assert str(check.id) not in raw_pl
    assert "sample-proj" not in raw_pl
    assert "stdout" not in raw_pl
    assert dashboard.full_analyses.read_claim(check.id) is None


def test_full_check_ai_post_validation(full_evidence):
    dashboard, request, check = full_evidence
    for bad in [
        {"mode": "project_check_ai", "check_id": str(check.id), "consent": "project-review-v1"},
        {
            "mode": "project_check_ai",
            "check_id": str(check.id),
            "consent": "project-full-check-summary-v1",
            "extra": "val",
        },
        {"mode": "project_check_ai", "check_id": str(check.id)},
    ]:
        status, _, _ = request("/api/jobs", method="POST", auth=True, body=json.dumps(bad))
        assert status == 400
        assert dashboard.full_analyses.read_claim(check.id) is None

    good = {
        "mode": "project_check_ai",
        "check_id": str(check.id),
        "consent": "project-full-check-summary-v1",
    }
    status, _, data = request("/api/jobs", method="POST", auth=True, body=json.dumps(good))
    assert status == 202
    job = json.loads(data)["job"]
    assert job["check_id"] == str(check.id)
    assert dashboard.full_analyses.read_claim(check.id) is not None

    dashboard.job["state"] = "completed"
    status2, _, _ = request("/api/jobs", method="POST", auth=True, body=json.dumps(good))
    assert status2 == 400


def test_full_check_ai_concurrency_and_unconfirmed(full_evidence, host):
    dashboard, request, check = full_evidence
    dashboard.job = {"id": "job-running", "mode": "other", "state": "running", "stage": "work"}
    body = json.dumps(
        {
            "mode": "project_check_ai",
            "check_id": str(check.id),
            "consent": "project-full-check-summary-v1",
        }
    )
    status, _, data = request("/api/jobs", method="POST", auth=True, body=body)
    assert status == 409
    assert json.loads(data)["job"]["id"] == "job-running"
    assert dashboard.full_analyses.read_claim(check.id) is None
    dashboard.job = None

    claim_pending = dashboard.checks.create(check.project_id, "minimal")
    pen_body = json.dumps(
        {
            "mode": "project_check_ai",
            "check_id": str(claim_pending.id),
            "consent": "project-full-check-summary-v1",
        }
    )
    assert request("/api/jobs", method="POST", auth=True, body=pen_body)[0] == 400
    assert request(f"/api/project-check-ai/{claim_pending.id}", auth=True)[0] == 503

    path = dashboard.runs.path(check.id, "result")
    path.write_text("corrupted", encoding="utf-8")
    assert request(f"/api/project-check-ai/{check.id}", auth=True)[0] == 503
    assert request("/api/jobs", method="POST", auth=True, body=body)[0] == 400

    dashboard.projects.remove(check.project_id)
    assert request(f"/api/project-check-ai/{check.id}", auth=True)[0] == 503


def test_full_check_ai_child_execution(operator, tmp_path, monkeypatch):
    dashboard = operator.Dashboard()
    dashboard.projects = ProjectRepository(tmp_path / "pr")
    dashboard.runs = RunRepository(tmp_path / "rn")
    from aegisops.project_checks.repository import FullCheckRepository

    dashboard.checks = FullCheckRepository(tmp_path / "ck", dashboard.projects, dashboard.runs)
    dashboard.full_analyses = FullAnalysisRepository(tmp_path / "fa", dashboard.checks)
    monkeypatch.setenv("AEGISOPS_SECRET", "canary")
    monkeypatch.setenv("OPENROUTER_KEY", "secret")
    recorded = {}

    class FakeResult:
        returncode = 0
        stdout = json.dumps({"ok": True})

    def fake_run(args, cwd, env, **kwargs):
        recorded["args"] = args
        recorded["env"] = env
        return FakeResult()

    monkeypatch.setattr(operator.subprocess, "run", fake_run)
    fake_id = UUID("12345678-1234-5678-1234-567812345678")
    dashboard.project_check_ai_child(fake_id)
    assert recorded["args"][5:9] == ["-Mode", "project-check", "-CheckId", str(fake_id)]
    assert "AEGISOPS_SECRET" not in recorded["env"]
    assert "OPENROUTER_KEY" not in recorded["env"]


def test_run_job_failure_safe_handling(operator, full_evidence, monkeypatch):
    dashboard, _, check = full_evidence
    claim = dashboard.full_analyses.claim(check.id)
    assert claim is not None
    dashboard.job = {"id": "job1", "state": "running", "stage": "collecting"}

    def fail_child(cid):
        raise RuntimeError("CANARY_SECRET_LEAK")

    monkeypatch.setattr(dashboard, "project_check_ai_child", fail_child)
    operator.Dashboard.run_job(dashboard, "project_check_ai", str(check.id))
    assert dashboard.job["state"] == "failed"
    assert "CANARY" not in dashboard.job["message"]
    state = dashboard.full_analyses.state(check.id)
    assert state["status"] == "failed"


@pytest.fixture
def comparison_pair(full_evidence):
    dashboard, request, check1 = full_evidence
    from aegisops.domain.common import utc_now
    from aegisops.project_checks.models import build_result
    from aegisops.project_checks.snapshot import scan_snapshot
    from aegisops.project_runner.models import RunResult, ToolCheck
    from aegisops.project_runner.snapshot import snapshot_project

    def _create_second(environment="minimal"):
        project = dashboard.projects.get(check1.project_id)
        claim2 = dashboard.checks.create(project.id, environment)
        snap2 = snapshot_project(project, claim2.profile)
        rep2 = scan_snapshot(project.id, snap2)
        dashboard.projects.save_report(rep2)
        tools = (
            ToolCheck(
                tool="ruff", status="passed", exit_code=0, output="ok", output_truncated=False
            ),
            ToolCheck(
                tool="pytest", status="passed", exit_code=0, output="ok", output_truncated=False
            ),
        )
        run2 = RunResult(
            id=claim2.id,
            project_id=project.id,
            environment=environment,
            profile=claim2.profile,
            completed_at=utc_now(),
            status="passed",
            reason="checks_completed",
            snapshot=snap2.info,
            image_id="sha256:" + "a" * 64,
            checks=tools,
            container_removed=True,
            execution_scope="container",
        )
        dashboard.runs.save(run2)
        check2 = build_result(claim2, rep2, run2)
        dashboard.checks.save(check2)
        return check2

    return dashboard, request, check1, _create_second


def test_comparison_endpoint_auth_and_routing_boundaries(comparison_pair):
    dashboard, request, check1, create_second = comparison_pair
    check2 = create_second()
    path = f"/api/project-check-comparison/{check1.id}/{check2.id}"
    status, _, _ = request(path, auth=False)
    assert status == 401
    status, _, _ = request(path, auth=True, headers={"Host": "attacker.local"})
    assert status == 403
    status, _, _ = request(f"/api/project-check-comparison/{check1.id}/not-a-uuid", auth=True)
    assert status == 503
    status, _, _ = request(f"{path}/extra", auth=True)
    assert status == 503


def test_comparison_endpoint_success_read_only(comparison_pair, monkeypatch):
    dashboard, request, check1, create_second = comparison_pair
    check2 = create_second()
    monkeypatch.setattr(dashboard.analyses, "claim", pytest.fail)
    monkeypatch.setattr(dashboard.full_analyses, "claim", pytest.fail)
    monkeypatch.setattr(dashboard.runs, "create", pytest.fail)

    def _tree_bytes(base_dir):
        return {p: p.read_bytes() for p in base_dir.rglob("*") if p.is_file()}

    before = {
        "checks": _tree_bytes(dashboard.checks.directory),
        "runs": _tree_bytes(dashboard.runs.directory),
        "projects": _tree_bytes(dashboard.projects.directory),
    }
    status, _, body = request(f"/api/project-check-comparison/{check1.id}/{check2.id}", auth=True)
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["comparable"] is True
    assert data["reasons"] == []
    assert data["static"] is not None and data["runner"] is not None
    assert data["base"]["id"] == str(check1.id)
    assert data["target"]["id"] == str(check2.id)
    after = {
        "checks": _tree_bytes(dashboard.checks.directory),
        "runs": _tree_bytes(dashboard.runs.directory),
        "projects": _tree_bytes(dashboard.projects.directory),
    }
    assert before == after


def test_comparison_endpoint_distinct_same_project_constraints(comparison_pair, tmp_path):
    dashboard, request, check1, create_second = comparison_pair
    check2 = create_second()
    status, _, _ = request(f"/api/project-check-comparison/{check1.id}/{check1.id}", auth=True)
    assert status == 503
    other_dir = tmp_path / "other-proj"
    other_dir.mkdir()
    (other_dir / "main.py").write_text("print('other')\n", encoding="utf-8")
    (other_dir / "compose.yaml").write_text(
        "services:\n  app:\n    image: test\n", encoding="utf-8"
    )
    other_proj = dashboard.projects.add("Other", str(other_dir))
    other_claim = dashboard.checks.create(other_proj.id, "minimal")
    status, _, _ = request(f"/api/project-check-comparison/{check1.id}/{other_claim.id}", auth=True)
    assert status == 503
    from uuid import uuid4

    status, _, _ = request(f"/api/project-check-comparison/{check1.id}/{uuid4()}", auth=True)
    assert status == 503
    dashboard.projects.remove(check1.project_id)
    status, _, _ = request(f"/api/project-check-comparison/{check1.id}/{check2.id}", auth=True)
    assert status == 503


def test_comparison_tampered_source_rejected(comparison_pair):
    dashboard, request, check1, create_second = comparison_pair
    check2 = create_second()
    report_path = dashboard.projects.directory / "reports" / f"{check1.static_report_id}.json"
    report_data = json.loads(report_path.read_text(encoding="utf-8"))
    report_data["created_at"] = "2020-01-01T00:00:00Z"
    report_path.write_text(json.dumps(report_data), encoding="utf-8")
    status, _, _ = request(f"/api/project-check-comparison/{check1.id}/{check2.id}", auth=True)
    assert status == 503


def test_comparison_environment_changed_non_comparable(comparison_pair):
    dashboard, request, check1, create_second = comparison_pair
    check2 = create_second(environment="python-data-v1")
    status, _, body = request(f"/api/project-check-comparison/{check1.id}/{check2.id}", auth=True)
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["comparable"] is False
    assert data["reasons"] == ["environment_changed"]
    assert data["static"] is None
    assert data["runner"] is None


def test_project_code_preview_requires_auth_origin_and_host(host, tmp_path):
    dashboard, request = host
    root = tmp_path / "code-preview-auth"
    root.mkdir()
    (root / "app.py").write_text("x = 1\n")
    project = dashboard.projects.add("AuthTest", str(root))
    payload = json.dumps({"project_id": str(project.id), "files": ["app.py"]})
    assert request("/api/project-code-preview", method="POST", body=payload)[0] == 401
    status, _, _ = request(
        "/api/project-code-preview",
        method="POST",
        auth=True,
        body=payload,
        headers={"Origin": "https://evil.example"},
    )
    assert status == 403
    assert (
        request(
            f"/api/project-code-files/{project.id}",
            auth=True,
            headers={"Host": "attacker.example"},
        )[0]
        == 403
    )


def test_project_code_preview_rejects_extra_fields_wrong_types_and_invalid_shapes(host, tmp_path):
    dashboard, request = host
    root = tmp_path / "code-preview-shapes"
    root.mkdir()
    (root / "app.py").write_text("x = 1\n")
    project = dashboard.projects.add("ShapeTest", str(root))
    pid = str(project.id)
    bad_payloads = [
        "not-a-json-object",
        123,
        {"project_id": pid, "files": ["app.py"], "extra": "canary"},
        {"project_id": pid, "files": []},
        {"project_id": pid, "files": ["1", "2", "3", "4", "5", "6"]},
        {"project_id": pid, "files": [123]},
        {"files": ["app.py"]},
    ]
    for bp in bad_payloads:
        status, _, _ = request(
            "/api/project-code-preview",
            method="POST",
            auth=True,
            body=json.dumps(bp) if not isinstance(bp, str) else bp,
        )
        assert status == 400


def test_project_code_preview_rejects_secret_or_escaping_path(host, tmp_path):
    dashboard, request = host
    root = tmp_path / "code-preview-escape"
    root.mkdir()
    (root / "safe.py").write_text("y = 2\n")
    (root / ".env").write_text("SECRET=123\n")
    project = dashboard.projects.add("EscapeTest", str(root))
    pid = str(project.id)
    for escape in ["../outside.py", "..\\outside.py", ".env"]:
        status, _, _ = request(
            "/api/project-code-preview",
            method="POST",
            auth=True,
            body=json.dumps({"project_id": pid, "files": [escape]}),
        )
        assert status == 400


def test_project_code_catalog_and_frozen_bytes_get(host, tmp_path):
    dashboard, request = host
    root = tmp_path / "code-catalog-frozen"
    root.mkdir()
    target = root / "main.py"
    target.write_text("original_content = 1\n")
    project = dashboard.projects.add("CatalogTest", str(root))
    status, _, body = request(f"/api/project-code-files/{project.id}", auth=True)
    assert status == 200
    catalog = json.loads(body)
    assert isinstance(catalog, dict)
    assert "files" in catalog and "partial" in catalog
    assert "main.py" in catalog["files"]
    preview = dashboard.code_analyses.create(project.id, ["main.py"])
    target.write_text("mutated_content = 999\n")
    status, _, body = request(f"/api/project-code/{preview.id}", auth=True)
    assert status == 200
    preview_data = json.loads(body)
    assert preview_data["status"] == "not_requested"
    file_record = (
        preview_data["files"][0] if "files" in preview_data else preview_data["preview"]["files"][0]
    )
    assert file_record["path"] == "main.py"
    content = file_record.get("content") or file_record.get("text", "")
    assert "original_content = 1" in content
    assert "mutated_content" not in content
    wire = preview_data["wire_payload"]
    assert not any(
        key in wire for key in ("status", "id", "preview_id", "created_at", "project_id")
    )
    assert set(wire["files"][0].keys()) == {"id", "language", "content"}
    assert wire["files"][0]["content"] == file_record["content"]


def test_project_code_ai_wrong_consent_reject_no_claim(host, tmp_path):
    dashboard, request = host
    root = tmp_path / "code-ai-wrong-consent"
    root.mkdir()
    (root / "app.py").write_text("a = 10\n")
    project = dashboard.projects.add("ConsentTest", str(root))
    preview = dashboard.code_analyses.create(project.id, ["app.py"])
    bad_body = json.dumps(
        {
            "mode": "project_code_ai",
            "preview_id": str(preview.id),
            "consent": "wrong-consent-token",
        }
    )
    status, _, _ = request("/api/jobs", method="POST", auth=True, body=bad_body)
    assert status == 400
    assert dashboard.code_analyses.read_claim(preview.id) is None


def test_project_code_ai_creates_exact_one_claim_when_run_job_stubbed(host, tmp_path, monkeypatch):
    dashboard, request = host
    monkeypatch.setattr(dashboard, "run_job", lambda *args: None)
    root = tmp_path / "code-ai-claim"
    root.mkdir()
    (root / "app.py").write_text("b = 20\n")
    project = dashboard.projects.add("OneClaimTest", str(root))
    preview = dashboard.code_analyses.create(project.id, ["app.py"])
    valid_body = json.dumps(
        {
            "mode": "project_code_ai",
            "preview_id": str(preview.id),
            "consent": "project-code-review-v1",
        }
    )
    status, _, _ = request("/api/jobs", method="POST", auth=True, body=valid_body)
    assert status == 202
    claim = dashboard.code_analyses.read_claim(preview.id)
    assert claim is not None
    assert str(claim.preview_id) == str(preview.id)


def test_project_code_ai_duplicate_post_no_fallback_and_mutex_no_duplicate(
    host, tmp_path, monkeypatch
):
    dashboard, request = host
    monkeypatch.setattr(dashboard, "run_job", lambda *args: None)
    root = tmp_path / "code-ai-dupe"
    root.mkdir()
    (root / "app.py").write_text("c = 30\n")
    project = dashboard.projects.add("DupeTest", str(root))
    preview = dashboard.code_analyses.create(project.id, ["app.py"])
    body = json.dumps(
        {
            "mode": "project_code_ai",
            "preview_id": str(preview.id),
            "consent": "project-code-review-v1",
        }
    )
    status1, _, _ = request("/api/jobs", method="POST", auth=True, body=body)
    assert status1 == 202
    status2, _, _ = request("/api/jobs", method="POST", auth=True, body=body)
    assert status2 == 409
    dashboard.job["state"] = "completed"
    status3, _, body3 = request("/api/jobs", method="POST", auth=True, body=body)
    assert status3 == 400
    assert "уже запрошен" in json.loads(body3)["error"]


def test_project_code_ai_child_failure_persistent_failed_and_generic_message(
    host, tmp_path, monkeypatch
):
    dashboard, _ = host
    root = tmp_path / "code-ai-child-fail"
    root.mkdir()
    (root / "app.py").write_text("d = 40\n")
    project = dashboard.projects.add("FailTest", str(root))
    preview = dashboard.code_analyses.create(project.id, ["app.py"])
    dashboard.code_analyses.claim(preview.id)
    dashboard.job = {"state": "running"}

    def fail_child(identifier):
        raise RuntimeError("SUPER_SECRET_LEAK_CANARY")

    monkeypatch.setattr(dashboard, "project_code_ai_child", fail_child)
    type(dashboard).run_job(dashboard, "project_code_ai", str(preview.id))
    assert dashboard.job["state"] == "failed"
    assert "SUPER_SECRET_LEAK_CANARY" not in json.dumps(dashboard.job)
    assert dashboard.job["message"] == "Разбор кода проекта не подтверждён. Повтора нет."
    assert dashboard.code_analyses.state(preview.id)["status"] == "failed"


def test_project_code_ai_child_fixed_launcher_args_and_env_psmodulepath_stripped(
    operator, monkeypatch
):
    dashboard = operator.Dashboard()
    recorded = {}

    def fake_run(args, **kwargs):
        recorded["args"] = args
        recorded["env"] = kwargs.get("env", {})

        class FakeResult:
            returncode = 0
            stdout = '{"ok": true}'

        return FakeResult()

    monkeypatch.setattr(operator.subprocess, "run", fake_run)
    if operator.os.name != "nt":

        class FakeOS:
            name = "nt"
            environ = operator.os.environ

            def __getattr__(self, item):
                return getattr(operator.os, item)

        monkeypatch.setattr(operator, "os", FakeOS())
    monkeypatch.setenv("PSModulePath", "C:\\forbidden\\module\\path")
    test_uuid = "11111111-2222-3333-4444-555555555555"
    dashboard.project_code_ai_child(test_uuid)
    assert recorded["args"][0] == "powershell.exe"
    assert recorded["args"][1] == "-NoProfile"
    assert recorded["args"][2] == "-NonInteractive"
    assert recorded["args"][3] == "-File"
    assert recorded["args"][5] == "-Mode"
    assert recorded["args"][6] == "project-code"
    assert recorded["args"][7] == "-CodeId"
    assert recorded["args"][8] == test_uuid
    assert "PSMODULEPATH" not in {k.upper() for k in recorded["env"]}


def test_ui17_code_map_auth_host_and_listing(host, tmp_path):
    dashboard, request = host
    folder = tmp_path / "demo_src"
    folder.mkdir()
    (folder / "a.py").write_text("print('hello')")
    dashboard.projects.add("demo", str(folder))

    path = f"/api/project-code-map/{dashboard.projects.list()[0].id}"
    assert request(path)[0] == 401
    assert request(path, auth=True, headers={"Host": "evil.com"})[0] == 403

    status, _, body = request(path, auth=True)
    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    paths = [item["path"] for item in payload.get("files", [])]
    assert "a.py" in paths


def test_ui17_code_context_suggestions_and_validation(host, tmp_path):
    dashboard, request = host
    folder = tmp_path / "demo_context"
    folder.mkdir()
    (folder / "a.py").write_text("import b\n")
    (folder / "b.py").write_text("x = 1\n")
    dashboard.projects.add("demo", str(folder))

    path = "/api/project-code-context"
    body = json.dumps({"project_id": str(dashboard.projects.list()[0].id), "files": ["a.py"]})
    status, _, resp_body = request(path, method="POST", auth=True, body=body)
    assert status == 200
    data = json.loads(resp_body.decode("utf-8"))
    assert "map" in data and "suggestions" in data and "selected" in data
    assert data["selected"] == ["a.py"]
    suggested_paths = [item["path"] for item in data["suggestions"]]
    assert "b.py" in suggested_paths

    invalid_body = json.dumps({"paths": "not-a-list"})
    inv_status, _, _ = request(path, method="POST", auth=True, body=invalid_body)
    assert inv_status == 400


def test_ui17_code_evaluation_get_contract(host, tmp_path):
    dashboard, request = host
    folder = tmp_path / "demo_eval_get"
    folder.mkdir()
    (folder / "a.py").write_text("# eval demo")
    dashboard.projects.add("demo", str(folder))

    path = "/api/project-code-evaluation"
    status, _, body = request(path, auth=True)
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    cases = data.get("cases", [])
    assert len(cases) == 3
    for case in cases:
        assert {"id", "title", "content", "expected"}.issubset(case.keys())
    assert data.get("history") == []


def test_ui17_code_evaluation_post_consent_and_mutex(host, tmp_path):
    dashboard, request = host
    folder = tmp_path / "demo_eval_post"
    folder.mkdir()
    (folder / "a.py").write_text("# eval post")
    dashboard.projects.add("demo", str(folder))

    eval_path = "/api/project-code-evaluation"
    wrong_payload = json.dumps(
        {"mode": "project_code_evaluation", "consent": "wrongstring", "case_id": "case-1"}
    )
    status, _, _ = request("/api/jobs", method="POST", auth=True, body=wrong_payload)
    assert status == 400
    _, _, get_body = request(eval_path, auth=True)
    assert json.loads(get_body.decode("utf-8")).get("history") == []

    correct_payload = json.dumps(
        {
            "mode": "project_code_evaluation",
            "consent": "project-code-evaluation-v1",
        }
    )
    status_ok, _, _ = request("/api/jobs", method="POST", auth=True, body=correct_payload)
    assert status_ok == 202
    _, _, get_body2 = request(eval_path, auth=True)
    assert len(json.loads(get_body2.decode("utf-8")).get("history", [])) == 1

    status_conflict, _, _ = request("/api/jobs", method="POST", auth=True, body=correct_payload)
    assert status_conflict == 409
    _, _, get_body3 = request(eval_path, auth=True)
    assert len(json.loads(get_body3.decode("utf-8")).get("history", [])) == 1


def test_assistant_api_auth_defaults_and_file_configuration(host):
    dashboard, request = host
    assert request("/api/assistant")[0] == 401
    status, _, body = request("/api/assistant", auth=True)
    assert status == 200
    payload = json.loads(body.decode())
    assert payload["selection"] == {
        "provider": "openrouter",
        "model": "deepseek/deepseek-v4.1-flash",
    }
    assert len(payload["providers"]) == 4
    dashboard.assistant.config_path.parent.mkdir(parents=True, exist_ok=True)
    (dashboard.assistant.config_path.parent / "deepseek.dpapi").write_bytes(b"opaque-secret-bytes")
    dashboard.assistant.config_path.write_text(
        json.dumps({"provider": "deepseek", "model": "deepseek-flash"})
    )
    status, _, body = request("/api/assistant", auth=True)
    assert status == 200
    assert b"opaque-secret-bytes" not in body
    data = json.loads(body.decode())
    assert data["selection"] == {"provider": "deepseek", "model": "deepseek-flash"}
    assert data["configured"] is True
    dashboard.assistant.config_path.write_text("{invalid-json")
    assert request("/api/assistant", auth=True)[0] == 503


def test_code_evaluation_job_binds_active_selection_persistently(host):
    dashboard, request = host
    dashboard.assistant.config_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard.assistant.config_path.write_text(
        json.dumps({"provider": "deepseek", "model": "deepseek-flash"})
    )
    status, _, body = request(
        "/api/jobs",
        method="POST",
        auth=True,
        body=json.dumps(
            {"mode": "project_code_evaluation", "consent": "project-code-evaluation-v1"}
        ),
    )
    assert status == 202
    job_resp = json.loads(body.decode())
    eval_id = UUID(job_resp["job"]["evaluation_id"])
    binding = dashboard.assistant.read_binding("project_code_evaluation", eval_id)
    assert binding.provider == "deepseek" and binding.model == "deepseek-flash"
    dashboard.assistant.config_path.write_text(
        json.dumps({"provider": "gemini", "model": "gemini-3.8-flash"})
    )
    binding_after = dashboard.assistant.read_binding("project_code_evaluation", eval_id)
    assert binding_after.provider == "deepseek" and binding_after.model == "deepseek-flash"


def test_assistant_model_state_resolution_rules(host):
    dashboard, _ = host
    from uuid import uuid4

    uid = uuid4()
    assert (
        dashboard.assistant_model(
            "project_ai", uid, {"status": "completed", "model": "gpt-5.4-mini"}
        )
        == "gpt-5.4-mini"
    )
    assert (
        dashboard.assistant_model(
            "project_ai", uid, {"result": {"status": "completed", "model": "gpt-5.4-mini"}}
        )
        == "gpt-5.4-mini"
    )
    nested_state = {"state": {"result": {"status": "completed", "model": "gemini-3.8-flash"}}}
    assert dashboard.assistant_model("project_ai", uid, nested_state) == "gemini-3.8-flash"
    dashboard.assistant.config_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard.assistant.config_path.write_text(
        json.dumps({"provider": "deepseek", "model": "deepseek-flash"})
    )
    assert (
        dashboard.assistant_model("project_ai", uid, {"status": "pending"})
        == "deepseek/deepseek-v4.1-flash"
    )


def _seed_completed_project_export(dashboard, tmp_path):
    from aegisops.domain.common import utc_now
    from aegisops.project_code.lab import LabResult, Proposal
    from aegisops.project_code.models import CodeFinding, CodeResult, CodeReview, preview_digest

    proj_dir = tmp_path / "demo_worktree"
    proj_dir.mkdir(parents=True, exist_ok=True)
    target_file = proj_dir / "main.py"
    target_file.write_text("def calc():\n    return 0\n", encoding="utf-8")

    project = dashboard.projects.add("demo", str(proj_dir))
    preview = dashboard.code_analyses.create(project.id, ["main.py"])
    dashboard.code_analyses.claim(preview.id)

    finding = CodeFinding(
        file_id="f1",
        start_line=2,
        end_line=2,
        severity="warning",
        category="reliability",
        title="Calculation check",
        evidence="return 0",
        explanation="Объяснение дефекта",
        recommendation="Рекомендация по правке",
    )
    review = CodeReview(summary="Обзор решения", findings=(finding,), limitations="None")
    code_res = CodeResult(
        preview_id=preview.id,
        preview_sha256=preview_digest(preview),
        completed_at=utc_now(),
        status="completed",
        model="gemini-3.8-flash",
        response=review,
    )
    dashboard.code_analyses.write(preview.id, "result", code_res.model_dump_json())

    lab_claim = dashboard.code_lab.claim(preview.id, 0)
    proposal = Proposal(
        file_id="f1",
        replacement="def calc():\n    return 1\n",
        test_language="python",
        test_code="def test_calc():\n    pass\n",
        explanation="Исправление дефекта",
    )
    lab_res = LabResult(
        preview_id=preview.id,
        preview_sha256=lab_claim.preview_sha256,
        review_sha256=lab_claim.review_sha256,
        completed_at=utc_now(),
        status="completed",
        model="gemini-3.8-flash",
        proposal=proposal,
    )
    dashboard.code_lab._write(preview.id, "result", lab_res.model_dump_json())
    return preview.id, proj_dir


@pytest.mark.parametrize("route_prefix", ["/api/project-code-export/", "/api/project-fix-export/"])
def test_export_routes_auth_evil_host_and_method_boundaries(host, route_prefix):
    from uuid import uuid4

    dashboard, request = host
    target_uuid = str(uuid4())
    path = f"{route_prefix}{target_uuid}"

    status, headers, body = request(path, method="GET", auth=False)
    assert status == 401

    status, headers, body = request(
        path, method="GET", auth=True, headers={"Host": "evil.example.com"}
    )
    assert status == 403

    status, headers, body = request(path, method="POST", auth=True, body=b"{}")
    assert status == 404


def test_completed_export_downloads_and_artifacts(host, tmp_path):
    import io
    import zipfile

    dashboard, request = host
    analysis_id, proj_dir = _seed_completed_project_export(dashboard, tmp_path)

    code_path = f"/api/project-code-export/{analysis_id}"
    status, headers, body_md = request(code_path, method="GET", auth=True)
    assert status == 200
    assert "no-store" in headers.get("Cache-Control", "")
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert (
        headers.get("Content-Disposition")
        == f'attachment; filename="aegis-review-{analysis_id}.md"'
    )
    assert headers.get("Content-Length") == str(len(body_md))
    assert "Обзор решения" in body_md.decode("utf-8")
    assert "gemini-3.8-flash" in body_md.decode("utf-8")

    status2, _, repeat_md = request(code_path, method="GET", auth=True)
    assert status2 == 200
    assert repeat_md == body_md

    fix_path = f"/api/project-fix-export/{analysis_id}"
    status, headers, body_zip = request(fix_path, method="GET", auth=True)
    assert status == 200
    assert "no-store" in headers.get("Cache-Control", "")
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert (
        headers.get("Content-Disposition") == f'attachment; filename="aegis-fix-{analysis_id}.zip"'
    )
    assert headers.get("Content-Length") == str(len(body_zip))

    with zipfile.ZipFile(io.BytesIO(body_zip), "r") as zf:
        names = zf.namelist()
        assert len(names) == 5

    status2, _, repeat_zip = request(fix_path, method="GET", auth=True)
    assert status2 == 200
    assert repeat_zip == body_zip

    assert set(p.name for p in proj_dir.iterdir()) == {"main.py"}


def test_pending_code_preview_downloads_fail_safely(host, tmp_path):
    dashboard, request = host
    proj_dir = tmp_path / "pending_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "main.py").write_text("def calc():\n    return 0\n", encoding="utf-8")
    project = dashboard.projects.add("pending_demo", str(proj_dir))
    preview = dashboard.code_analyses.create(project.id, ["main.py"])

    status, headers, body = request(
        f"/api/project-code-export/{preview.id}", method="GET", auth=True
    )
    assert status == 503
    assert "Content-Disposition" not in headers
    payload = json.loads(body.decode("utf-8"))
    assert "error" in payload or "detail" in payload or "status" in payload

    status, headers, body = request(
        f"/api/project-fix-export/{preview.id}", method="GET", auth=True
    )
    assert status == 503
    assert "Content-Disposition" not in headers
    payload = json.loads(body.decode("utf-8"))
    assert "error" in payload or "detail" in payload or "status" in payload


def test_tampered_preview_hash_fails_safely_without_traceback(host, tmp_path):
    dashboard, request = host
    analysis_id, _ = _seed_completed_project_export(dashboard, tmp_path)

    preview_file = dashboard.code_analyses.path(analysis_id, "preview")
    data = json.loads(preview_file.read_text(encoding="utf-8"))
    data["files"][0]["sha256"] = "0000000000000000000000000000000000000000000000000000000000000000"
    preview_file.write_text(json.dumps(data), encoding="utf-8")

    for path in (
        f"/api/project-code-export/{analysis_id}",
        f"/api/project-fix-export/{analysis_id}",
    ):
        status, headers, body = request(path, method="GET", auth=True)
        assert status == 503
        assert "Content-Disposition" not in headers
        text = body.decode("utf-8")
        assert "Traceback" not in text
        payload = json.loads(text)
        assert isinstance(payload, dict)


def test_project_code_recommendation_auth_host_and_method_guards(host, tmp_path):
    dashboard, request = host
    project_dir = tmp_path / "proj_guards"
    project_dir.mkdir()
    (project_dir / "main.py").write_text("import util\n", encoding="utf-8")
    (project_dir / "util.py").write_text("def run(): return 1\n", encoding="utf-8")
    (project_dir / "test_main.py").write_text("import main\n", encoding="utf-8")
    (project_dir / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '0.1.0'\n", encoding="utf-8"
    )
    project = dashboard.projects.add("demo", str(project_dir))
    endpoint = f"/api/project-code-recommendation/{project.id}"

    status, _, _ = request(endpoint, auth=False)
    assert status == 401

    status, _, _ = request(endpoint, auth=True, headers={"Host": "evil.com"})
    assert status == 403

    status, _, _ = request(endpoint, method="POST", auth=True, body=b"{}")
    assert status == 404


def test_project_code_recommendation_success_and_immutability(host, tmp_path, monkeypatch):
    import hashlib

    dashboard, request = host
    project_dir = tmp_path / "proj_success"
    project_dir.mkdir()
    files = {
        "main.py": "import util\n",
        "util.py": "def helper(): return True\n",
        "test_main.py": "import main\n",
        "pyproject.toml": "[project]\nname = 'demo'\nversion = '0.1.0'\n",
    }
    expected_hashes = {}
    expected_bytes = {}
    for rel_path, content in files.items():
        raw = content.encode("utf-8")
        (project_dir / rel_path).write_bytes(raw)
        expected_hashes[rel_path] = hashlib.sha256(raw).hexdigest()
        expected_bytes[rel_path] = len(raw)

    project = dashboard.projects.add("demo", str(project_dir))
    endpoint = f"/api/project-code-recommendation/{project.id}"

    def fail_create(*args, **kwargs):
        raise AssertionError("create must not be called")

    monkeypatch.setattr(dashboard.code_analyses, "create", fail_create)

    status, headers, body = request(endpoint, auth=True)
    assert status == 200
    assert "no-store" in headers.get("Cache-Control", "").lower()
    assert headers.get("X-Content-Type-Options", "").lower() == "nosniff"

    data = json.loads(body.decode("utf-8"))
    assert data["profile"] == "project-code-selection-v1"
    assert isinstance(data["files"], list)
    assert data["total_bytes"] <= 24576
    assert data["max_files"] == 5
    assert data["max_total_bytes"] == 24576
    assert isinstance(data["partial"], bool)
    assert isinstance(data["limitations"], list)

    returned_files = data["files"]
    assert len(returned_files) <= 5
    calculated_total = 0
    for item in returned_files:
        assert not item["path"].startswith("/")
        assert not item["path"].startswith("\\")
        assert "content" not in item
        assert "payload" not in item
        assert item["path"] in expected_hashes
        assert item["sha256"] == expected_hashes[item["path"]]
        assert item["bytes"] == expected_bytes[item["path"]]
        calculated_total += item["bytes"]
        assert "reason" in item
    assert data["total_bytes"] == calculated_total

    for rel_path, content in files.items():
        assert (project_dir / rel_path).read_bytes() == content.encode("utf-8")

    status2, _, body2 = request(endpoint, auth=True)
    assert status2 == 200
    assert json.loads(body2.decode("utf-8")) == data

    idx = dashboard.code_analyses.index(project.id)
    assert getattr(idx, "previews", ()) == ()
    assert dashboard.current_job() is None


def test_project_code_recommendation_malformed_or_missing_project_safe_503(host):
    dashboard, request = host
    from uuid import uuid4

    for bad_id in ["not-a-uuid", str(uuid4())]:
        status, _, body = request(f"/api/project-code-recommendation/{bad_id}", auth=True)
        assert status == 503
        data = json.loads(body.decode("utf-8"))
        assert isinstance(data, dict)
        assert "error" in data or "message" in data or "safe" in str(data).lower()


def test_code_evaluation_v2_endpoint_get_metadata_and_boundaries(host, tmp_path):
    dashboard, request = host
    folder = tmp_path / "demo_eval_v2"
    folder.mkdir()
    (folder / "main.py").write_text("# code content\n")
    dashboard.projects.add("demo", str(folder))

    assert request("/api/project-code-evaluation-v2")[0] == 401
    assert (
        request("/api/project-code-evaluation-v2", auth=True, headers={"Host": "attacker.example"})[
            0
        ]
        == 403
    )
    assert request("/api/project-code-evaluation-v2", method="POST", auth=True, body="{}")[0] == 404

    status, _, body = request("/api/project-code-evaluation-v2", auth=True)
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["history"] == []
    assert data["scope"] == "synthetic-file-overlap-v2"
    assert data["max_requests"] == 9
    assert data["max_output_tokens"] == 25200
    cases = data.get("cases", [])
    assert len(cases) == 9
    languages = set()
    for case in cases:
        assert {"id", "title", "files", "expected"}.issubset(case.keys())
        files = case["files"]
        assert 1 <= len(files) <= 3
        for f in files:
            assert {"id", "path", "language", "content"}.issubset(f.keys())
            languages.add(f["language"])
    assert len(languages) == 3
    assert dashboard.code_evaluation.history() == []


def test_code_evaluation_v2_post_consent_binding_and_mutex(host, tmp_path):
    dashboard, request = host
    folder = tmp_path / "demo_eval_post_v2"
    folder.mkdir()
    (folder / "test.py").write_text("# test post\n")
    dashboard.projects.add("demo", str(folder))

    eval_path = "/api/project-code-evaluation-v2"
    wrong_payload = json.dumps(
        {"mode": "project_code_evaluation", "consent": "project-code-evaluation-v2-wrong"}
    )
    status, _, _ = request("/api/jobs", method="POST", auth=True, body=wrong_payload)
    assert status == 400
    _, _, get_body_empty = request(eval_path, auth=True)
    assert json.loads(get_body_empty.decode("utf-8")).get("history") == []

    dashboard.assistant.config_path.parent.mkdir(parents=True, exist_ok=True)
    dashboard.assistant.config_path.write_text(
        json.dumps({"provider": "deepseek", "model": "deepseek-flash"})
    )

    correct_payload = json.dumps(
        {"mode": "project_code_evaluation", "consent": "project-code-evaluation-v2"}
    )
    status_ok, _, post_body = request("/api/jobs", method="POST", auth=True, body=correct_payload)
    assert status_ok == 202
    job_resp = json.loads(post_body.decode("utf-8"))
    eval_id = UUID(job_resp["job"]["evaluation_id"])
    claim = dashboard.code_evaluation.read_claim(eval_id)
    assert claim.suite_profile == "synthetic-file-overlap-v2"

    binding = dashboard.assistant.read_binding("project_code_evaluation", eval_id)
    assert binding.provider == "deepseek" and binding.model == "deepseek-flash"
    dashboard.assistant.config_path.write_text(
        json.dumps({"provider": "gemini", "model": "gemini-3.8-flash"})
    )
    binding_after = dashboard.assistant.read_binding("project_code_evaluation", eval_id)
    assert binding_after.provider == "deepseek" and binding_after.model == "deepseek-flash"

    _, _, get_body_after = request(eval_path, auth=True)
    assert len(json.loads(get_body_after.decode("utf-8")).get("history", [])) == 1

    status_conflict, _, _ = request("/api/jobs", method="POST", auth=True, body=correct_payload)
    assert status_conflict == 409
    status_conflict_v1, _, _ = request(
        "/api/jobs",
        method="POST",
        auth=True,
        body=json.dumps(
            {"mode": "project_code_evaluation", "consent": "project-code-evaluation-v1"}
        ),
    )
    assert status_conflict_v1 == 409


def test_code_evaluation_rejects_extra_case_and_profile_keys(host, tmp_path):
    dashboard, request = host
    folder = tmp_path / "demo_eval_extra"
    folder.mkdir()
    (folder / "lib.py").write_text("# extra keys\n")
    dashboard.projects.add("demo", str(folder))

    payload_case = json.dumps(
        {
            "mode": "project_code_evaluation",
            "consent": "project-code-evaluation-v2",
            "case_id": "case-1",
        }
    )
    status_case, _, _ = request("/api/jobs", method="POST", auth=True, body=payload_case)
    assert status_case == 400

    payload_profile = json.dumps(
        {
            "mode": "project_code_evaluation",
            "consent": "project-code-evaluation-v2",
            "profile": "fast",
        }
    )
    status_prof, _, _ = request("/api/jobs", method="POST", auth=True, body=payload_profile)
    assert status_prof == 400

    payload_query = json.dumps(
        {"mode": "project_code_evaluation", "consent": "project-code-evaluation-v2", "version": 2}
    )
    status_q, _, _ = request("/api/jobs", method="POST", auth=True, body=payload_query)
    assert status_q == 400


def test_code_evaluation_get_does_not_create_claim_or_history(host, tmp_path):
    dashboard, request = host
    folder = tmp_path / "demo_eval_noclaim"
    folder.mkdir()
    (folder / "test.py").write_text("# harmless\n")
    dashboard.projects.add("demo", str(folder))

    s1, _, b1 = request("/api/project-code-evaluation", auth=True)
    assert s1 == 200
    assert json.loads(b1.decode("utf-8")).get("history") == []

    s2, _, b2 = request("/api/project-code-evaluation-v2", auth=True)
    assert s2 == 200
    assert json.loads(b2.decode("utf-8")).get("history") == []
