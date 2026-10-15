"""Source-free export, bounded model output and durable single-attempt behavior."""

import importlib.util
import json
import ssl
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from aegisops.domain.common import utc_now
from aegisops.project_ai import transport
from aegisops.project_ai.models import MODEL, Explanation, Result
from aegisops.project_ai.projection import project_summary, report_digest
from aegisops.project_ai.repository import AnalysisRepository
from aegisops.projects.models import Finding, ProjectError, ProjectReport
from aegisops.projects.repository import ProjectRepository
from aegisops.projects.scanner import scan_project

pytestmark = [pytest.mark.unit, pytest.mark.safety]
ROOT = Path(__file__).resolve().parents[2]
CANARY = "PRIVATE_SOURCE_CANARY"
ADVICE = {
    "summary": "Есть замечания конфигурации.",
    "actions": [
        {
            "code": "privileged",
            "priority": "high",
            "explanation": "Расширены полномочия.",
            "next_step": "Проверьте, требуются ли эти полномочия.",
        }
    ],
    "limitations": "Исходники не читались; это объяснение статического отчёта.",
}


@pytest.fixture
def case(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "compose.yaml").write_text("services:\n  app:\n    privileged: true\n")
    projects = ProjectRepository(tmp_path / ".local/projects")
    project = projects.add(CANARY, str(root))
    report = scan_project(project)
    projects.save_report(report)
    analyses = AnalysisRepository(tmp_path / ".local/project-ai")
    return projects, report, analyses


def test_export_is_closed_projection_and_never_copies_source_strings(case):
    _, report, _ = case
    payload = report.model_dump()
    payload["findings"] = (
        Finding(
            severity="warning",
            code="privileged",
            file=CANARY,
            location=CANARY,
            message=CANARY,
            recommendation=CANARY,
        ),
    )
    payload["stacks"] = (CANARY,)
    private_report = ProjectReport.model_validate(payload)
    summary = project_summary(private_report)
    assert set(summary.model_dump()) == {
        "profile",
        "rules_version",
        "status",
        "files_checked",
        "skipped",
        "categories",
    }
    assert CANARY not in summary.model_dump_json()
    assert str(report.id) not in summary.model_dump_json()
    assert summary.categories[0].code == "privileged"
    assert report_digest(private_report) != report_digest(report)
    forged = summary.model_copy(update={"profile": CANARY})
    with pytest.raises(ValidationError):
        type(summary).model_validate(forged)


def test_claim_survives_restart_and_result_is_bound_to_report(case):
    _, report, analyses = case
    claim = analyses.claim(report)
    reopened = AnalysisRepository(analyses.directory)
    assert reopened.state(report)["status"] == "pending"
    with pytest.raises(ProjectError, match="уже запрошен"):
        reopened.claim(report)
    result = Result(
        report_id=report.id,
        report_sha256=claim.report_sha256,
        completed_at=utc_now(),
        status="completed",
        response=Explanation.model_validate(ADVICE),
        input_tokens=30,
        output_tokens=40,
    )
    analyses.write(report.id, "result", result.model_dump_json())
    reopened.fail(report.id)
    assert reopened.read_result(report.id) == result
    changed = ProjectReport.model_validate({**report.model_dump(), "files_seen": 2})
    with pytest.raises(ProjectError, match="изменился"):
        reopened.state(changed)


def test_failed_or_corrupt_state_is_never_automatically_replaced(case):
    _, report, analyses = case
    analyses.claim(report)
    analyses.fail(report.id)
    assert analyses.state(report)["status"] == "failed"
    with pytest.raises(ProjectError):
        analyses.claim(report)
    path = analyses.path(report.id, "result")
    path.write_text('{"invalid":"PRIVATE_SOURCE_CANARY"}')
    before = path.read_bytes()
    with pytest.raises(ValidationError):
        analyses.read_result(report.id)
    assert path.read_bytes() == before


@pytest.fixture
def wire(monkeypatch):
    reply = {
        "model": MODEL,
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(ADVICE)}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 200},
    }
    state = {"reply": reply, "status": 200, "length": None, "calls": []}

    class Response:
        def __init__(self):
            self.status = state["status"]
            self.data = state.get("raw", json.dumps(state["reply"]).encode())

        def getheader(self, name):
            return state["length"]

        def read1(self, size):
            chunk, self.data = self.data[:size], self.data[size:]
            return chunk

        def close(self):
            pass

    class Connection:
        sock = None

        def __init__(self, host, port, *, timeout, context):
            assert host == "openrouter.ai" and port == 443 and timeout == 30
            assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname

        def connect(self):
            pass

        def request(self, method, path, *, body, headers):
            assert method == "POST" and path == "/api/v1/chat/completions"
            assert headers["Authorization"] == "Bearer test-runtime-key"
            state["calls"].append(json.loads(body))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(transport, "getproxies", lambda: {})
    monkeypatch.setattr(transport.http.client, "HTTPSConnection", Connection)
    return state


def test_transport_sends_only_summary_and_fixed_model_without_tools(case, wire):
    _, report, _ = case
    response, input_tokens, output_tokens = transport.explain(
        project_summary(report), SecretStr("test-runtime-key")
    )
    assert response == Explanation.model_validate(ADVICE)
    assert (input_tokens, output_tokens) == (100, 200)
    request = wire["calls"][0]
    assert request["model"] == MODEL and request["max_tokens"] == 1600
    assert request["provider"] == {"allow_fallbacks": False}
    assert "tools" not in request and "plugins" not in request
    assert CANARY not in json.dumps(request) and str(report.id) not in json.dumps(request)
    assert json.loads(request["messages"][1]["content"]) == project_summary(report).model_dump(
        mode="json"
    )


@pytest.mark.parametrize(
    "failure",
    [
        "redirect",
        "http",
        "oversize_header",
        "oversize_body",
        "model",
        "truncated",
        "tools",
        "refusal",
        "bad_json",
        "duplicate_json",
        "unknown_code",
        "extra",
        "usage_bool",
        "usage_limit",
    ],
)
def test_invalid_provider_responses_are_fixed_failures_without_retry(case, wire, failure):
    _, report, _ = case
    choice = wire["reply"]["choices"][0]
    if failure == "redirect":
        wire["status"] = 302
    elif failure == "http":
        wire["status"] = 429
    elif failure == "oversize_header":
        wire["length"] = "65537"
    elif failure == "oversize_body":
        wire["raw"] = b" " * 65537
    elif failure == "model":
        wire["reply"]["model"] = "unknown"
    elif failure == "truncated":
        choice["finish_reason"] = "length"
    elif failure == "tools":
        choice["message"]["tool_calls"] = [{"name": CANARY}]
    elif failure == "refusal":
        choice["message"]["refusal"] = CANARY
    elif failure == "bad_json":
        choice["message"]["content"] = CANARY
    elif failure == "duplicate_json":
        choice["message"]["content"] = '{"summary":"a","summary":"b"}'
    elif failure == "unknown_code":
        advice = json.loads(choice["message"]["content"])
        advice["actions"][0]["code"] = "docker_socket"
        choice["message"]["content"] = json.dumps(advice)
    elif failure == "extra":
        advice = {**ADVICE, "command": CANARY}
        choice["message"]["content"] = json.dumps(advice)
    elif failure == "usage_bool":
        wire["reply"]["usage"]["prompt_tokens"] = True
    elif failure == "usage_limit":
        wire["reply"]["usage"]["completion_tokens"] = 1601
    with pytest.raises(transport.AnalysisError) as caught:
        transport.explain(project_summary(report), SecretStr("test-runtime-key"))
    assert CANARY not in str(caught.value) and len(wire["calls"]) == 1


def test_worker_once_only_no_project_code_imported_or_modified(case, tmp_path, monkeypatch):
    projects, report, analyses = case
    before = Path(projects.get(report.project_id).path, "compose.yaml").read_bytes()
    spec = importlib.util.spec_from_file_location(
        "project_ai_worker", ROOT / "scripts/project-ai-worker.py"
    )
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(worker.sys, "argv", ["worker", str(report.id)])
    calls = []

    def provider(summary, key):
        calls.append(summary)
        return Explanation.model_validate(ADVICE), 100, 200

    monkeypatch.setattr(worker, "explain", provider)
    analyses.claim(report)
    assert worker.main() == 0 and len(calls) == 1
    assert worker.main() == 1 and len(calls) == 1
    assert analyses.state(report)["status"] == "completed"
    assert Path(projects.get(report.project_id).path, "compose.yaml").read_bytes() == before


def test_uncertain_worker_start_cannot_be_replayed_or_marked_as_completed(
    case, tmp_path, monkeypatch
):
    _, report, analyses = case
    spec = importlib.util.spec_from_file_location(
        "project_ai_uncertain", ROOT / "scripts/project-ai-worker.py"
    )
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(worker.sys, "argv", ["worker", str(report.id)])

    def forbidden(*args):
        pytest.fail("uncertain attempt was replayed")

    monkeypatch.setattr(worker, "explain", forbidden)
    analyses.claim(report)
    analyses.write(report.id, "started", "{}")
    assert worker.main() == 1 and analyses.state(report)["status"] == "pending"
