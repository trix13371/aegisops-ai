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
def host(operator, monkeypatch):
    dashboard = operator.Dashboard()
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
