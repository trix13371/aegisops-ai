"""Combined summaries remain closed, source-free and isolated from static AI v1."""

import json
import ssl

import pytest
from pydantic import SecretStr, ValidationError

from aegisops.project_ai import review_transport, transport
from aegisops.project_ai.models import MODEL, Explanation, Summary
from aegisops.project_ai.review_models import (
    CheckSummary,
    ReviewExplanation,
    ReviewSummary,
    RunnerSummary,
)

pytestmark = [pytest.mark.unit, pytest.mark.safety]
CANARY = "PRIVATE_SOURCE_OR_PACKAGE_CANARY"
ADVICE = {
    "summary": "Есть замечания независимых проверок.",
    "actions": [
        {
            "code": "runner_failed",
            "priority": "high",
            "explanation": "Инструмент сообщил об ошибке.",
            "next_step": "Изучите сохранённый локальный результат инструмента.",
        }
    ],
    "limitations": "Совпадение версий исходников не подтверждено.",
}


@pytest.fixture
def summary():
    return ReviewSummary.model_validate(
        {
            "status": "attention",
            "source_match": "unverified",
            "categories": [{"code": "runner_failed", "severity": "error", "count": 1}],
            "static": {
                "status": "passed",
                "rules_version": "local-static-v2",
                "files_checked": 2,
                "skipped": 0,
                "partial": False,
            },
            "runner": {
                "profile": "python-checks-v1",
                "environment": "minimal",
                "state": "issues",
                "reason": "checks_completed",
                "checks": [
                    {"tool": "ruff", "status": "passed", "output_truncated": False},
                    {"tool": "pytest", "status": "failed", "output_truncated": False},
                ],
                "compatibility_issues": [],
                "execution_scope": "container",
                "container_removed": True,
            },
            "limitations": ["unverified_source_match", "selected_profile_only"],
        }
    )


@pytest.fixture
def wire(monkeypatch):
    state = {
        "calls": [],
        "status": 200,
        "length": None,
        "reply": {
            "model": MODEL,
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(ADVICE)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200},
        },
    }

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
            assert len(body) <= 16384
            state["calls"].append(json.loads(body))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(transport, "getproxies", lambda: {})
    monkeypatch.setattr(transport.http.client, "HTTPSConnection", Connection)
    return state


def test_review_wire_has_only_closed_summary_and_fixed_request(summary, wire):
    response, input_tokens, output_tokens = review_transport.explain_review(
        summary, SecretStr("test-runtime-key")
    )
    assert response == ReviewExplanation.model_validate(ADVICE)
    assert (input_tokens, output_tokens) == (100, 200)
    assert len(wire["calls"]) == 1
    request = wire["calls"][0]
    assert set(request) == {
        "model",
        "messages",
        "max_tokens",
        "temperature",
        "stream",
        "reasoning",
        "response_format",
        "provider",
    }
    assert request["model"] == MODEL and request["max_tokens"] == 1600
    assert request["provider"] == {"allow_fallbacks": False}
    assert CANARY not in json.dumps(request)
    assert json.loads(request["messages"][1]["content"]) == summary.model_dump(mode="json")
    assert set(summary.model_dump()) == {
        "profile",
        "status",
        "source_match",
        "categories",
        "static",
        "runner",
        "limitations",
    }
    assert set(summary.runner.model_dump()) == {
        "profile",
        "environment",
        "state",
        "reason",
        "checks",
        "compatibility_issues",
        "execution_scope",
        "container_removed",
    }


@pytest.mark.parametrize(
    "field", ["source", "path", "logs", "project_name", "id", "sha256", "created_at", "packages"]
)
def test_review_input_cannot_accept_private_fields(summary, field):
    with pytest.raises(ValidationError):
        ReviewSummary.model_validate({**summary.model_dump(), field: CANARY})
    with pytest.raises(ValidationError):
        RunnerSummary.model_validate({**summary.runner.model_dump(), field: CANARY})
    with pytest.raises(ValidationError):
        CheckSummary.model_validate({**summary.runner.checks[0].model_dump(), field: CANARY})


@pytest.mark.parametrize(
    "failure",
    [
        "unknown_code",
        "absent_code",
        "duplicate_code",
        "extra",
        "bad_json",
        "duplicate_json",
        "truncated",
        "tools",
        "function_call",
        "refusal",
        "model",
        "redirect",
        "http",
        "oversize_header",
        "oversize_body",
        "usage_bool",
        "usage_limit",
    ],
)
def test_bad_review_output_is_fixed_failure_without_retry(summary, wire, failure):
    choice = wire["reply"]["choices"][0]
    advice = json.loads(choice["message"]["content"])
    if failure in {"unknown_code", "absent_code"}:
        advice["actions"][0]["code"] = CANARY if failure == "unknown_code" else "privileged"
    elif failure == "duplicate_code":
        advice["actions"].append(dict(advice["actions"][0]))
    elif failure == "extra":
        advice["command"] = CANARY
    elif failure == "bad_json":
        choice["message"]["content"] = CANARY
    elif failure == "duplicate_json":
        choice["message"]["content"] = '{"summary":"a","summary":"b"}'
    elif failure == "truncated":
        choice["finish_reason"] = "length"
    elif failure in {"tools", "function_call", "refusal"}:
        field = {"tools": "tool_calls", "function_call": "function_call", "refusal": "refusal"}[
            failure
        ]
        choice["message"][field] = CANARY
    elif failure == "model":
        wire["reply"]["model"] = CANARY
    elif failure in {"redirect", "http"}:
        wire["status"] = 302 if failure == "redirect" else 429
    elif failure == "oversize_header":
        wire["length"] = "65537"
    elif failure == "oversize_body":
        wire["raw"] = b" " * 65537
    elif failure == "usage_bool":
        wire["reply"]["usage"]["prompt_tokens"] = True
    elif failure == "usage_limit":
        wire["reply"]["usage"]["completion_tokens"] = 1601
    if failure in {"unknown_code", "absent_code", "duplicate_code", "extra"}:
        choice["message"]["content"] = json.dumps(advice)
    with pytest.raises(transport.AnalysisError) as caught:
        review_transport.explain_review(summary, SecretStr("test-runtime-key"))
    assert CANARY not in str(caught.value)
    assert len(wire["calls"]) == 1


def test_empty_categories_cannot_return_advice(summary, wire):
    empty = ReviewSummary.model_validate({**summary.model_dump(), "categories": []})
    with pytest.raises(transport.AnalysisError):
        review_transport.explain_review(empty, SecretStr("test-runtime-key"))
    wire["reply"]["choices"][0]["message"]["content"] = json.dumps({**ADVICE, "actions": []})
    response, _, _ = review_transport.explain_review(empty, SecretStr("test-runtime-key"))
    assert not response.actions


@pytest.mark.parametrize("field", ["profile", "source_match", "limitations"])
def test_forged_review_instance_revalidated_before_wire(summary, wire, field):
    forged = summary.model_copy(update={field: CANARY})
    with pytest.raises(transport.AnalysisError):
        review_transport.explain_review(forged, SecretStr("test-runtime-key"))
    assert not wire["calls"]


def test_forged_nested_instance_revalidated_before_wire(summary, wire):
    nested = summary.runner.model_copy(update={"environment": CANARY})
    forged = summary.model_copy(update={"runner": nested})
    with pytest.raises(transport.AnalysisError):
        review_transport.explain_review(forged, SecretStr("test-runtime-key"))
    assert not wire["calls"]


def test_review_requires_some_input_and_unverified_source_warning(summary):
    for updates in (
        {"static": None, "runner": None},
        {"limitations": []},
        {"categories": [summary.categories[0], summary.categories[0]]},
        {"limitations": ["unverified_source_match", "unverified_source_match"]},
    ):
        with pytest.raises(ValidationError):
            ReviewSummary.model_validate({**summary.model_dump(), **updates})


def test_static_v1_does_not_accept_review_profile_or_runner_advice(summary):
    with pytest.raises(ValidationError):
        Summary.model_validate(summary.model_dump())
    with pytest.raises(ValidationError):
        Explanation.model_validate(ADVICE)
