from __future__ import annotations

import json
import ssl
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from aegisops.actions.catalog import load_catalog
from aegisops.context.builder import ContextBuilder
from aegisops.detection.engine import DetectionEngine
from aegisops.llm.base import AnalysisDraft, LLMError, LLMLimits, LLMRequest
from aegisops.llm.fake import FakeLLMClient
from aegisops.llm.prompt import build_request
from aegisops.llm.transport import (
    OPENROUTER_URL,
    OpenAICompatibleClient,
    build_payload,
)
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = pytest.mark.unit

CANARY_FAKE_KEY = "S006_FAKE_KEY_NEVER_REAL"  # noqa: S105


@pytest.fixture(autouse=True)
def no_system_proxy(monkeypatch):
    monkeypatch.setattr("aegisops.llm.transport.getproxies", lambda: {})


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        chunks: list[bytes] | None = None,
        content_length: str | None = None,
    ) -> None:
        self.status = status
        self._chunks = list(chunks) if chunks is not None else [b""]
        self._headers = {}
        if content_length is not None:
            self._headers["Content-Length"] = content_length
        self.closed = False

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return self._headers.get(name, default)

    def read1(self, n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def close(self) -> None:
        self.closed = True


class MockConnection:
    def __init__(
        self,
        host: str,
        port: int | None = None,
        timeout: int = 30,
        context: ssl.SSLContext | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.sock = MagicMock()
        self.connected = False
        self.closed = False
        self.requested_method: str | None = None
        self.requested_path: str | None = None
        self.requested_body: bytes | None = None
        self.requested_headers: dict[str, str] | None = None
        self.response = FakeResponse()

    def connect(self) -> None:
        self.connected = True

    def set_tunnel(self, host, port):
        self.tunnel = (host, port)

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.requested_method = method
        self.requested_path = path
        self.requested_body = body
        self.requested_headers = headers

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def valid_llm_request() -> LLMRequest:
    scenario = ScenarioRepository().load("crashloop-oom")[1]
    incident = DetectionEngine().detect([scenario])[0]
    diagnosis_context = ContextBuilder().build(incident)
    catalog = load_catalog()
    return build_request(incident, diagnosis_context, catalog)


@pytest.fixture
def valid_analysis_draft(valid_llm_request: LLMRequest) -> AnalysisDraft:
    fake_client = FakeLLMClient()
    reply = fake_client.generate_structured(valid_llm_request, LLMLimits())
    return reply.data


def make_envelope(draft: AnalysisDraft, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps(draft.model_dump(mode="json"))},
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        "model": "deepseek/deepseek-v4.1-flash",
    }
    payload.update(overrides)
    return payload


def test_happy_path_openrouter(
    valid_llm_request: LLMRequest, valid_analysis_draft: AnalysisDraft
) -> None:
    envelope = make_envelope(valid_analysis_draft)
    mock_conn = MockConnection("openrouter.ai")
    mock_conn.response = FakeResponse(200, [json.dumps(envelope).encode("utf-8"), b""])

    with patch("http.client.HTTPSConnection", return_value=mock_conn) as mock_https:
        client = OpenAICompatibleClient(
            provider="openrouter",
            api_key=SecretStr(CANARY_FAKE_KEY),
        )
        reply = client.generate_structured(valid_llm_request, LLMLimits())

        assert reply.data == valid_analysis_draft
        assert reply.input_tokens == 10
        assert reply.output_tokens == 20
        assert reply.response_model == "deepseek/deepseek-v4.1-flash"
        assert mock_conn.closed
        assert mock_conn.response.closed
        assert mock_conn.requested_method == "POST"
        assert mock_conn.requested_path == "/api/v1/chat/completions"
        assert mock_conn.requested_headers["Authorization"] == f"Bearer {CANARY_FAKE_KEY}"
        assert CANARY_FAKE_KEY.encode("utf-8") not in (mock_conn.requested_body or b"")
        mock_https.assert_called_once()
        assert isinstance(mock_https.call_args.kwargs["context"], ssl.SSLContext)


def test_russian_roundtrip(
    valid_llm_request: LLMRequest, valid_analysis_draft: AnalysisDraft
) -> None:
    draft_data = valid_analysis_draft.model_dump(mode="json")
    draft_data["diagnosis"]["summary"] = "Сбой памяти OOMKilled"
    draft_data["diagnosis"]["hypotheses"][0]["rationale"] = "Контейнер завершился с кодом 137"
    russian_draft = AnalysisDraft.model_validate(draft_data)

    envelope = make_envelope(russian_draft)
    mock_conn = MockConnection("openrouter.ai")
    mock_conn.response = FakeResponse(
        200, [json.dumps(envelope, ensure_ascii=False).encode("utf-8"), b""]
    )

    with patch("http.client.HTTPSConnection", return_value=mock_conn):
        client = OpenAICompatibleClient(provider="openrouter", api_key=SecretStr(CANARY_FAKE_KEY))
        reply = client.generate_structured(valid_llm_request, LLMLimits())
        assert reply.data.diagnosis.summary == "Сбой памяти OOMKilled"
        assert reply.data.diagnosis.hypotheses[0].rationale == "Контейнер завершился с кодом 137"


def test_payload_contract(valid_llm_request: LLMRequest) -> None:
    limits = LLMLimits(max_output_tokens=500)
    payload = build_payload(valid_llm_request, limits, "deepseek/deepseek-v4.1-flash", "openrouter")
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 500
    assert payload["stream"] is False
    assert payload["response_format"] == {"type": "json_object"}
    assert "tools" not in payload
    assert "functions" not in payload
    assert "function_call" not in payload


@pytest.mark.parametrize(
    "provider,endpoint,key",
    [
        ("remote", "https://example.com/v1/chat/completions", SecretStr(CANARY_FAKE_KEY)),
        ("openrouter", "https://openrouter.ai/v1/wrong", SecretStr(CANARY_FAKE_KEY)),
        ("openrouter", "http://openrouter.ai/api/v1/chat/completions", SecretStr(CANARY_FAKE_KEY)),
        ("openrouter", OPENROUTER_URL, None),
        ("local", "http://localhost:8080/v1/chat/completions", None),
        ("local", "http://127.0.0.1:0/v1/chat/completions", None),
        ("local", "http://127.0.0.1/v1/chat/completions", None),
        ("local", "http://127.0.0.1:8080/wrong/path", None),
        ("local", "http://127.0.0.1:8080/v1/chat/completions?query=1", None),
        ("local", "http://user:pass@127.0.0.1:8080/v1/chat/completions", None),
        ("local", "http://127.0.0.1:8080/v1/chat/completions#frag", None),
        ("local", "ftp://127.0.0.1:8080/v1/chat/completions", None),
    ],
)
def test_invalid_client_configs_rejected(
    provider: str, endpoint: str, key: SecretStr | None
) -> None:
    with pytest.raises(LLMError):
        OpenAICompatibleClient(provider=provider, endpoint=endpoint, api_key=key)


@pytest.mark.parametrize(
    "endpoint,is_ssl",
    [
        ("http://127.0.0.1:8000/v1/chat/completions", False),
        ("https://[::1]:8443/v1/chat/completions", True),
    ],
)
def test_valid_local_endpoints(
    endpoint: str, is_ssl: bool, valid_llm_request: LLMRequest, valid_analysis_draft: AnalysisDraft
) -> None:
    envelope = make_envelope(valid_analysis_draft)
    mock_conn = MockConnection("127.0.0.1")
    mock_conn.response = FakeResponse(200, [json.dumps(envelope).encode("utf-8"), b""])

    target_patch = "http.client.HTTPSConnection" if is_ssl else "http.client.HTTPConnection"
    with patch(target_patch, return_value=mock_conn) as mock_cls:
        client = OpenAICompatibleClient(provider="local", endpoint=endpoint)
        reply = client.generate_structured(valid_llm_request, LLMLimits())
        assert reply.data == valid_analysis_draft
        mock_cls.assert_called_once()


@pytest.mark.parametrize("status_code", [301, 302, 307, 308, 401, 403, 500])
def test_http_error_statuses_raise_llm_error_without_leak(
    status_code: int, valid_llm_request: LLMRequest
) -> None:
    mock_conn = MockConnection("openrouter.ai")
    mock_conn.response = FakeResponse(status_code, [b"fake provider error body"])

    with patch("http.client.HTTPSConnection", return_value=mock_conn):
        client = OpenAICompatibleClient(provider="openrouter", api_key=SecretStr(CANARY_FAKE_KEY))
        with pytest.raises(LLMError) as exc_info:
            client.generate_structured(valid_llm_request, LLMLimits())
        assert CANARY_FAKE_KEY not in str(exc_info.value)
        assert "secret error body" not in str(exc_info.value)
        assert mock_conn.closed
        assert mock_conn.response.closed


@pytest.mark.parametrize(
    "kind",
    [
        "bad_json",
        "duplicate",
        "nan",
        "no_choices",
        "length",
        "tools",
        "refusal",
        "content_type",
        "invalid_draft",
        "usage_type",
        "no_usage",
    ],
)
def test_malformed_responses_raise_llm_error(kind, valid_llm_request, valid_analysis_draft):
    payload = make_envelope(valid_analysis_draft)
    choice = payload["choices"][0]
    if kind == "no_choices":
        payload["choices"] = []
    elif kind == "length":
        choice["finish_reason"] = "length"
    elif kind == "tools":
        choice["message"]["tool_calls"] = [{"function": {"name": "execute"}}]
    elif kind == "refusal":
        choice["message"]["refusal"] = "no"
    elif kind == "content_type":
        choice["message"]["content"] = 123
    elif kind == "invalid_draft":
        choice["message"]["content"] = json.dumps({"unexpected": 1})
    elif kind == "usage_type":
        payload["usage"]["prompt_tokens"] = True
    elif kind == "no_usage":
        del payload["usage"]
    elif kind == "nan":
        payload["usage"]["prompt_tokens"] = float("nan")
    raw = json.dumps(payload).encode("utf-8")
    if kind == "bad_json":
        raw = b"not-json"
    elif kind == "duplicate":
        raw = b'{"choices":[],"choices":[]}'
    mock_conn = MockConnection("openrouter.ai")
    mock_conn.response = FakeResponse(200, [raw, b""])
    with patch("http.client.HTTPSConnection", return_value=mock_conn):
        client = OpenAICompatibleClient(provider="openrouter", api_key=SecretStr(CANARY_FAKE_KEY))
        with pytest.raises(LLMError, match="^model operation failed$"):
            client.generate_structured(valid_llm_request, LLMLimits())
    assert mock_conn.closed
    assert mock_conn.response.closed


def test_content_length_and_actual_bytes_capped(
    valid_llm_request: LLMRequest, valid_analysis_draft: AnalysisDraft
) -> None:
    envelope_bytes = json.dumps(make_envelope(valid_analysis_draft)).encode("utf-8")
    limits = LLMLimits(max_response_bytes=len(envelope_bytes) - 5)

    mock_conn = MockConnection("openrouter.ai")
    mock_conn.response = FakeResponse(
        200, [envelope_bytes], content_length=str(len(envelope_bytes))
    )
    with patch("http.client.HTTPSConnection", return_value=mock_conn):
        client = OpenAICompatibleClient(provider="openrouter", api_key=SecretStr(CANARY_FAKE_KEY))
        with pytest.raises(LLMError):
            client.generate_structured(valid_llm_request, limits)

    mock_conn2 = MockConnection("openrouter.ai")
    mock_conn2.response = FakeResponse(200, [envelope_bytes], content_length=None)
    with patch("http.client.HTTPSConnection", return_value=mock_conn2):
        client = OpenAICompatibleClient(provider="openrouter", api_key=SecretStr(CANARY_FAKE_KEY))
        with pytest.raises(LLMError):
            client.generate_structured(valid_llm_request, limits)


def test_tampered_request_hash_rejected_before_connect(valid_llm_request: LLMRequest) -> None:
    tampered = LLMRequest(
        prompt_id=valid_llm_request.prompt_id,
        prompt_hash="0" * 64,
        system=valid_llm_request.system,
        user=valid_llm_request.user,
        origin=valid_llm_request.origin,
    )
    mock_conn = MockConnection("openrouter.ai")
    with patch("http.client.HTTPSConnection", return_value=mock_conn):
        client = OpenAICompatibleClient(provider="openrouter", api_key=SecretStr(CANARY_FAKE_KEY))
        with pytest.raises(LLMError):
            client.generate_structured(tampered, LLMLimits())
        assert not mock_conn.connected


def test_input_bytes_cap_guard_before_connect(valid_llm_request: LLMRequest) -> None:
    mock_conn = MockConnection("openrouter.ai")
    with patch("http.client.HTTPSConnection", return_value=mock_conn):
        client = OpenAICompatibleClient(provider="openrouter", api_key=SecretStr(CANARY_FAKE_KEY))
        with pytest.raises(LLMError):
            client.generate_structured(valid_llm_request, LLMLimits(max_input_bytes=1024))
        assert not mock_conn.connected


def test_system_proxy_uses_origin_tls_tunnel(monkeypatch, valid_llm_request, valid_analysis_draft):
    monkeypatch.setattr(
        "aegisops.llm.transport.getproxies",
        lambda: {"https": "http://configured-proxy.invalid:8080"},
    )
    monkeypatch.setattr("aegisops.llm.transport.proxy_bypass", lambda host: False)
    conn = MockConnection("configured-proxy.invalid")
    conn.response = FakeResponse(200, [json.dumps(make_envelope(valid_analysis_draft)).encode()])
    with patch("http.client.HTTPSConnection", return_value=conn) as factory:
        OpenAICompatibleClient(api_key=SecretStr(CANARY_FAKE_KEY)).generate_structured(
            valid_llm_request, LLMLimits()
        )
    assert factory.call_args.args[:2] == ("configured-proxy.invalid", 8080)
    assert conn.tunnel == ("openrouter.ai", 443)
    assert factory.call_args.kwargs["context"].check_hostname is True
    assert factory.call_args.kwargs["context"].verify_mode == ssl.CERT_REQUIRED


def test_local_https_never_uses_environment_proxy(
    monkeypatch, valid_llm_request, valid_analysis_draft
):
    def forbidden_proxy():
        raise AssertionError("local request must bypass proxy discovery")

    monkeypatch.setattr("aegisops.llm.transport.getproxies", forbidden_proxy)
    conn = MockConnection("127.0.0.1")
    conn.response = FakeResponse(200, [json.dumps(make_envelope(valid_analysis_draft)).encode()])
    with patch("http.client.HTTPSConnection", return_value=conn):
        result = OpenAICompatibleClient(
            provider="local", endpoint="https://127.0.0.1:8443/v1/chat/completions"
        ).generate_structured(valid_llm_request, LLMLimits())
    assert result.data == valid_analysis_draft
    assert "Authorization" not in conn.requested_headers
