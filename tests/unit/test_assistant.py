from __future__ import annotations

import json
import ssl
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr

from aegisops.assistant.models import (
    CATALOG,
    LEGACY,
    Selection,
    selected,
)
from aegisops.assistant.repository import AssistantRepository
from aegisops.assistant.runtime import runtime_model, runtime_selection
from aegisops.assistant.transport import (
    AssistantTransportError,
    exchange,
)
from aegisops.projects.models import ProjectError

pytestmark = pytest.mark.unit


class FakeHTTPResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None):
        self.status = status
        self._body = body
        self._headers = headers or {"Content-Length": str(len(body))}
        self._offset = 0

    def getheader(self, name: str, default: Any = None) -> Any:
        return self._headers.get(name, default)

    def read1(self, size: int = 4096) -> bytes:
        if self._offset >= len(self._body):
            return b""
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        pass


class FakeHTTPSConnection:
    captured: list[dict[str, Any]] = []
    next_response: FakeHTTPResponse | None = None
    fail_on_connect: bool = False

    def __init__(self, host: str, port: int, timeout: int, context: ssl.SSLContext):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.sock: Any = None
        self.tunnel_target: tuple[str, int] | None = None

    def set_tunnel(self, host: str, port: int) -> None:
        self.tunnel_target = (host, port)

    def connect(self) -> None:
        if FakeHTTPSConnection.fail_on_connect:
            raise ConnectionError("connect failed")

    def request(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> None:
        FakeHTTPSConnection.captured.append(
            {
                "host": self.host,
                "port": self.port,
                "timeout": self.timeout,
                "context": self.context,
                "tunnel": self.tunnel_target,
                "method": method,
                "path": path,
                "body": body,
                "headers": headers,
            }
        )

    def getresponse(self) -> FakeHTTPResponse:
        if FakeHTTPSConnection.next_response is None:
            raise RuntimeError("no fake response configured")
        return FakeHTTPSConnection.next_response

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def reset_transport_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeHTTPSConnection.captured.clear()
    FakeHTTPSConnection.next_response = None
    FakeHTTPSConnection.fail_on_connect = False
    monkeypatch.setattr("http.client.HTTPSConnection", FakeHTTPSConnection)
    monkeypatch.setattr("aegisops.assistant.transport.getproxies", lambda: {})


# --- 1. Pure catalog and models tests ---


def test_catalog_four_approved_pairs() -> None:
    assert len(CATALOG) == 4
    assert set(CATALOG.keys()) == {"openrouter", "deepseek", "gemini", "openai"}
    for provider, entry in CATALOG.items():
        sel = selected(provider)
        assert sel.provider == provider
        assert sel.model == entry.model


def test_selection_rejects_wrong_pair_or_unknown() -> None:
    with pytest.raises(ValueError, match="approved catalog"):
        Selection(provider="deepseek", model="gpt-5.4-mini")
    with pytest.raises(ValueError, match="approved catalog"):
        Selection(provider="openai", model="gemini-3.8-flash")
    with pytest.raises(ValueError, match="approved catalog"):
        selected("unknown_provider")  # type: ignore[arg-type]


# --- 2. Repository tests ---


def test_repo_active_missing_returns_none(tmp_path: Path) -> None:
    cfg = tmp_path / "assistant.json"
    repo = AssistantRepository(cfg, tmp_path / "attempts")
    assert repo.active() is None


def test_repo_active_malformed_fails_closed(tmp_path: Path) -> None:
    cfg = tmp_path / "assistant.json"
    cfg.write_text("not valid json", encoding="utf-8", newline="\n")
    repo = AssistantRepository(cfg, tmp_path / "attempts")
    with pytest.raises(ProjectError):
        repo.active()


def test_repo_active_duplicate_json_keys_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "assistant.json"
    cfg.write_text(
        '{"provider":"deepseek","model":"deepseek-flash","model":"gpt-5.4-mini"}',
        encoding="utf-8",
        newline="\n",
    )
    repo = AssistantRepository(cfg, tmp_path / "attempts")
    with pytest.raises(ProjectError):
        repo.active()


def test_repo_traversal_mode_and_uuid_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "assistant.json"
    cfg.write_text(
        '{"provider":"deepseek","model":"deepseek-flash"}',
        encoding="utf-8",
        newline="\n",
    )
    repo = AssistantRepository(cfg, tmp_path / "attempts")
    with pytest.raises(ProjectError, match="Неизвестный режим"):
        repo.bind("../traversal", str(uuid4()))
    with pytest.raises(ProjectError, match="Некорректный идентификатор"):
        repo.bind("project_ai", "not-a-valid-uuid")


def test_repo_frozen_bind_across_active_changes_and_duplicate_bind_never_overwrites(
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "assistant.json"
    repo = AssistantRepository(cfg, tmp_path / "attempts")
    job_id = str(uuid4())

    # Phase 1: Deepseek active
    cfg.write_text(
        '{"provider":"deepseek","model":"deepseek-flash"}',
        encoding="utf-8",
        newline="\n",
    )
    bound1 = repo.bind("project_ai", job_id)
    assert bound1 == Selection(provider="deepseek", model="deepseek-flash")

    # Phase 2: Active changed to OpenAI, attempt to rebind must keep original binding
    cfg.write_text(
        '{"provider":"openai","model":"gpt-5.4-mini"}',
        encoding="utf-8",
        newline="\n",
    )
    bound2 = repo.bind("project_ai", job_id)
    assert bound2 == Selection(provider="deepseek", model="deepseek-flash")
    assert repo.read_binding("project_ai", job_id) == Selection(
        provider="deepseek", model="deepseek-flash"
    )


def test_repo_legacy_resolve_ignores_new_active_config(tmp_path: Path) -> None:
    cfg = tmp_path / "assistant.json"
    repo = AssistantRepository(cfg, tmp_path / "attempts")
    job_id = str(uuid4())

    # No config -> legacy bind is None
    assert repo.bind("project_ai", job_id) is None
    assert repo.read_binding("project_ai", job_id) is None

    # Activate new provider
    cfg.write_text(
        '{"provider":"openai","model":"gpt-5.4-mini"}',
        encoding="utf-8",
        newline="\n",
    )
    # Resolving unresolved job always returns LEGACY without consulting active config
    assert repo.resolve("project_ai", job_id) == LEGACY


# --- 3. Runtime environment tests ---


def test_runtime_both_absent_defaults_to_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEGISOPS_ASSISTANT_PROVIDER", raising=False)
    monkeypatch.delenv("AEGISOPS_ASSISTANT_MODEL", raising=False)
    assert runtime_selection() == LEGACY
    assert runtime_model() == LEGACY.model


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("", ""),
        ("   ", "   "),
        ("deepseek", ""),
        ("", "deepseek-flash"),
        ("deepseek", None),
        (None, "deepseek-flash"),
        ("deepseek", "invalid-model"),
        ("unsupported", "model-x"),
    ],
)
def test_runtime_partial_invalid_blank_fails_closed(
    monkeypatch: pytest.MonkeyPatch, provider: str | None, model: str | None
) -> None:
    if provider is None:
        monkeypatch.delenv("AEGISOPS_ASSISTANT_PROVIDER", raising=False)
    else:
        monkeypatch.setenv("AEGISOPS_ASSISTANT_PROVIDER", provider)

    if model is None:
        monkeypatch.delenv("AEGISOPS_ASSISTANT_MODEL", raising=False)
    else:
        monkeypatch.setenv("AEGISOPS_ASSISTANT_MODEL", model)

    if provider is None and model is None:
        assert runtime_selection() == LEGACY
    else:
        with pytest.raises(ProjectError):
            runtime_selection()


def test_runtime_valid_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AEGISOPS_ASSISTANT_PROVIDER", "gemini")
    monkeypatch.setenv("AEGISOPS_ASSISTANT_MODEL", "gemini-3.8-flash")
    assert runtime_selection() == Selection(provider="gemini", model="gemini-3.8-flash")
    assert runtime_model() == "gemini-3.8-flash"


# --- 4. Transport tests ---


def test_transport_rejects_openrouter_legacy() -> None:
    with pytest.raises(AssistantTransportError) as exc_info:
        exchange("{}", "instruct", SecretStr("sk-test-key-123"), LEGACY)
    assert "openrouter" not in str(exc_info.value)
    assert "sk-test-key-123" not in str(exc_info.value)


def test_transport_deepseek_payload_and_headers() -> None:
    reply_payload = {
        "model": "deepseek-flash",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps({"status": "ok"})},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }
    FakeHTTPSConnection.next_response = FakeHTTPResponse(
        200, json.dumps(reply_payload).encode("utf-8")
    )

    sel = Selection(provider="deepseek", model="deepseek-flash")
    content, inp, outp = exchange('{"query":"hello"}', "inst", SecretStr("ds-secret-123"), sel)

    assert content == {"status": "ok"}
    assert inp == 10
    assert outp == 20
    assert len(FakeHTTPSConnection.captured) == 1
    req = FakeHTTPSConnection.captured[0]
    assert req["host"] == "api.deepseek.com"
    assert req["path"] == "/chat/completions"
    assert req["headers"]["Authorization"] == "Bearer ds-secret-123"
    assert isinstance(req["context"], ssl.SSLContext)

    body = json.loads(req["body"].decode("utf-8"))
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0
    assert body["stream"] is False
    assert body["max_tokens"] == 1600


def test_transport_openai_payload_headers_and_date_alias() -> None:
    reply_payload = {
        "model": "gpt-5.4-mini-2026-03-01",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps({"openai": "success"})},
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 60},
    }
    FakeHTTPSConnection.next_response = FakeHTTPResponse(
        200, json.dumps(reply_payload).encode("utf-8")
    )

    sel = Selection(provider="openai", model="gpt-5.4-mini")
    content, inp, outp = exchange(
        '{"task":"test"}', "sys", SecretStr("oa-secret-999"), sel, source_review=True
    )

    assert content == {"openai": "success"}
    assert inp == 50
    assert outp == 60
    req = FakeHTTPSConnection.captured[0]
    assert req["host"] == "api.openai.com"
    assert req["path"] == "/v1/chat/completions"
    assert req["headers"]["Authorization"] == "Bearer oa-secret-999"

    body = json.loads(req["body"].decode("utf-8"))
    assert body["max_completion_tokens"] == 2800
    assert body["reasoning_effort"] == "none"
    assert body["store"] is False
    assert "temperature" not in body


def test_transport_gemini_headers_no_query_and_thinking_low() -> None:
    reply_payload = {
        "modelVersion": "gemini-3.8-flash",
        "usageMetadata": {
            "promptTokenCount": 12,
            "candidatesTokenCount": 25,
            "thoughtsTokenCount": 5,
        },
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {"parts": [{"text": json.dumps({"gemini": "answer"})}]},
            }
        ],
    }
    FakeHTTPSConnection.next_response = FakeHTTPResponse(
        200, json.dumps(reply_payload).encode("utf-8")
    )

    sel = Selection(provider="gemini", model="gemini-3.8-flash")
    content, inp, outp = exchange(
        '{"gemini":"q"}', "instruct-gemini", SecretStr("gemini-key-1"), sel
    )

    assert content == {"gemini": "answer"}
    assert inp == 12
    assert outp == 30  # 25 + 5
    req = FakeHTTPSConnection.captured[0]
    assert req["host"] == "generativelanguage.googleapis.com"
    assert req["path"] == "/v1beta/models/gemini-3.8-flash:generateContent"
    assert req["headers"]["x-goog-api-key"] == "gemini-key-1"
    assert "Authorization" not in req["headers"]
    assert "?" not in req["path"]

    body = json.loads(req["body"].decode("utf-8"))
    cfg = body["generationConfig"]
    assert cfg["thinkingConfig"] == {"thinkingLevel": "LOW", "includeThoughts": False}
    assert cfg["candidateCount"] == 1
    assert cfg["maxOutputTokens"] == 1600


@pytest.mark.parametrize(
    "bad_reply",
    [
        {
            "model": "wrong-model",
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        {
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "length", "message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        {
            "model": "deepseek-flash",
            "choices": [
                {"finish_reason": "stop", "message": {"content": "{}", "tool_calls": ["tool"]}}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        {
            "model": "deepseek-flash",
            "choices": [
                {"finish_reason": "stop", "message": {"content": "{}", "refusal": "blocked"}}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        {
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": "not-json"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        {
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": '{"a":1,"a":2}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        {
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            "usage": {"prompt_tokens": -1, "completion_tokens": 1},
        },
        {
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 9999999},
        },
        {"model": "deepseek-flash", "choices": []},
    ],
)
def test_transport_openai_rejected_fail_closed(bad_reply: dict[str, Any]) -> None:
    FakeHTTPSConnection.next_response = FakeHTTPResponse(200, json.dumps(bad_reply).encode("utf-8"))
    sel = Selection(provider="deepseek", model="deepseek-flash")
    with pytest.raises(AssistantTransportError) as exc_info:
        exchange("{}", "sys", SecretStr("ds-secret"), sel)
    assert "ds-secret" not in str(exc_info.value)
    assert "deepseek" not in str(exc_info.value).lower()


@pytest.mark.parametrize(
    "bad_gemini",
    [
        {"modelVersion": "gemini-3.8-flash", "promptFeedback": {"blockReason": "SAFETY"}},
        {
            "modelVersion": "gemini-3.8-flash",
            "candidates": [{"finishReason": "SAFETY", "content": {"parts": [{"text": "{}"}]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        },
        {
            "modelVersion": "gemini-3.8-flash",
            "candidates": [
                {"finishReason": "STOP", "content": {"parts": [{"text": "{}", "thought": True}]}}
            ],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        },
        {
            "modelVersion": "gemini-3.8-flash",
            "candidates": [
                {"finishReason": "STOP", "content": {"parts": [{"text": "{}", "functionCall": {}}]}}
            ],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        },
        {
            "modelVersion": "gemini-3.8-flash",
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "{}"}]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1601},
        },
    ],
)
def test_transport_gemini_rejected_fail_closed(bad_gemini: dict[str, Any]) -> None:
    FakeHTTPSConnection.next_response = FakeHTTPResponse(
        200, json.dumps(bad_gemini).encode("utf-8")
    )
    sel = Selection(provider="gemini", model="gemini-3.8-flash")
    with pytest.raises(AssistantTransportError):
        exchange("{}", "sys", SecretStr("gem-secret"), sel)


def test_transport_non_200_and_network_fail_no_retry() -> None:
    FakeHTTPSConnection.next_response = FakeHTTPResponse(500, b'{"error":"internal"}')
    sel = Selection(provider="deepseek", model="deepseek-flash")
    with pytest.raises(AssistantTransportError):
        exchange("{}", "sys", SecretStr("ds-secret"), sel)
    assert len(FakeHTTPSConnection.captured) == 1

    FakeHTTPSConnection.captured.clear()
    FakeHTTPSConnection.fail_on_connect = True
    with pytest.raises(AssistantTransportError):
        exchange("{}", "sys", SecretStr("ds-secret"), sel)
    assert len(FakeHTTPSConnection.captured) == 0


@pytest.mark.parametrize(
    "invalid_content",
    [
        "{}",
        '{"provider": "openrouter"}',
        '{"model": "deepseek/deepseek-v4.1-flash"}',
    ],
)
def test_invalid_assistant_configurations_raise_error(tmp_path, invalid_content):
    repo = AssistantRepository(tmp_path / "assistant.json", tmp_path / "attempts")

    assistant_config = tmp_path / "assistant.json"
    assistant_config.write_text(invalid_content, encoding="utf-8")
    with pytest.raises(ProjectError):
        repo.active()

    ident = uuid4()
    actual_binding_path = tmp_path / "attempts" / "project_ai" / f"{ident}.json"
    actual_binding_path.parent.mkdir(parents=True, exist_ok=True)
    actual_binding_path.write_text(invalid_content, encoding="utf-8")
    with pytest.raises(ProjectError):
        repo.read_binding("project_ai", ident)
