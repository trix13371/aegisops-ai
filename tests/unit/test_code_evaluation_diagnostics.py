import json
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from aegisops.assistant.models import LEGACY
from aegisops.domain.common import utc_now
from aegisops.project_ai import transport
from aegisops.project_ai.transport import AnalysisError, _exchange
from aegisops.project_code import transport as code_transport
from aegisops.project_code.evaluation_v2 import (
    CASES,
    CaseUsage,
    EvalFailure,
    EvalResultV2,
    case_preview,
    fixture_digest,
    score_answers,
)
from aegisops.project_code.models import CodeReview

CANARY = "RAW_CANARY_SECRET_DATA_LEAK"


def test_analysis_error_closed_codes_and_canary_leak() -> None:
    err_unknown = AnalysisError("arbitrary_bad_code")
    assert err_unknown.code == "unavailable"

    err_list = AnalysisError(["timeout"])  # type: ignore[arg-type]
    assert err_list.code == "unavailable"

    err_dict = AnalysisError({"code": "timeout"})  # type: ignore[arg-type]
    assert err_dict.code == "unavailable"

    err_canary = AnalysisError(CANARY)
    assert err_canary.code == "unavailable"
    assert CANARY not in str(err_canary)
    assert CANARY not in repr(err_canary)

    err_valid = AnalysisError("timeout")
    assert err_valid.code == "timeout"


class FakeTimer:
    timer_instances: list["FakeTimer"] = []

    def __init__(self, interval: float, function: Any, *args: Any, **kwargs: Any) -> None:
        self.interval = interval
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.canceled = False
        self.started = False
        self.daemon = False
        FakeTimer.timer_instances.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.canceled = True


class FakeSocket:
    def __init__(self) -> None:
        self.closed = False
        self.timeout: float | None = None

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout

    def shutdown(self, how: int) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeHTTPResponse:
    def __init__(
        self,
        status: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
        read1_side_effect: Exception | None = None,
    ) -> None:
        self.status = status
        self._headers = headers or {}
        self._chunks = list(chunks) if chunks is not None else [b""]
        self._side_effect = read1_side_effect
        self.closed = False

    def getheader(self, name: str, default: str | None = None) -> str | None:
        for k, v in self._headers.items():
            if k.lower() == name.lower():
                return v
        return default

    def read1(self, n: int = -1) -> bytes:
        if self._side_effect:
            raise self._side_effect
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def close(self) -> None:
        self.closed = True


class FakeHTTPSConnection:
    connection_instances: list["FakeHTTPSConnection"] = []

    def __init__(
        self,
        host: str,
        port: int = 443,
        timeout: float = 30,
        context: Any = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.context = context
        self.sock: FakeSocket | None = FakeSocket()
        self.closed = False
        self.connect_count = 0
        self.request_count = 0
        self.getresponse_count = 0

        # Configurable actions
        self.connect_side_effect: Exception | None = None
        self.request_side_effect: Exception | None = None
        self.getresponse_side_effect: Exception | None = None
        self.response: FakeHTTPResponse | None = None

        FakeHTTPSConnection.connection_instances.append(self)

    def connect(self) -> None:
        self.connect_count += 1
        if self.connect_side_effect:
            raise self.connect_side_effect

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.request_count += 1
        if self.request_side_effect:
            raise self.request_side_effect

    def getresponse(self) -> FakeHTTPResponse:
        self.getresponse_count += 1
        if self.getresponse_side_effect:
            raise self.getresponse_side_effect
        if self.response is None:
            self.response = FakeHTTPResponse(status=200, chunks=[b"{}"])
        return self.response

    def close(self) -> None:
        self.closed = True
        if self.sock:
            self.sock.close()


@pytest.fixture(autouse=True)
def patch_exchange_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeHTTPSConnection.connection_instances.clear()
    FakeTimer.timer_instances.clear()
    monkeypatch.setattr(transport, "runtime_selection", lambda: LEGACY)
    monkeypatch.setattr(transport, "getproxies", lambda: {})
    monkeypatch.setattr(transport, "proxy_bypass", lambda host: True)
    monkeypatch.setattr(transport.http.client, "HTTPSConnection", FakeHTTPSConnection)
    monkeypatch.setattr(transport.threading, "Timer", FakeTimer)


def _valid_payload_bytes() -> bytes:
    return json.dumps(
        {
            "model": transport.MODEL,
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"summary": "ok", "actions": []}),
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 10,
            },
        }
    ).encode("utf-8")


def test_exchange_socket_timeout_scenarios(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Connect timeout (socket.timeout)
    conn_cls = FakeHTTPSConnection
    c1 = conn_cls("dummy")
    c1.connect_side_effect = TimeoutError("timed out connecting")
    monkeypatch.setattr(transport.http.client, "HTTPSConnection", lambda *args, **kwargs: c1)
    monkeypatch.setattr(transport.time, "monotonic", lambda: 10.0)

    with pytest.raises(AnalysisError) as exc_info:
        _exchange("{}", "instructions", SecretStr("valid_secret_key"))
    assert exc_info.value.code == "timeout"
    assert c1.closed
    assert c1.connect_count == 1

    # 2. Getresponse timeout (socket.timeout)
    c2 = conn_cls("dummy")
    c2.getresponse_side_effect = TimeoutError("timed out on response")
    monkeypatch.setattr(transport.http.client, "HTTPSConnection", lambda *args, **kwargs: c2)

    with pytest.raises(AnalysisError) as exc_info:
        _exchange("{}", "instructions", SecretStr("valid_secret_key"))
    assert exc_info.value.code == "timeout"
    assert c2.closed

    # 3. Read timeout (socket.timeout)
    c3 = conn_cls("dummy")
    c3.response = FakeHTTPResponse(status=200, read1_side_effect=TimeoutError("read timeout"))
    monkeypatch.setattr(transport.http.client, "HTTPSConnection", lambda *args, **kwargs: c3)

    with pytest.raises(AnalysisError) as exc_info:
        _exchange("{}", "instructions", SecretStr("valid_secret_key"))
    assert exc_info.value.code == "timeout"
    assert c3.closed
    assert c3.response.closed


def test_exchange_oserror_before_vs_after_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    # Start at monotonic 100, deadline will be 130
    current_time = [100.0]
    monkeypatch.setattr(transport.time, "monotonic", lambda: current_time[0])

    # Before deadline -> network error
    c1 = FakeHTTPSConnection("dummy")
    c1.connect_side_effect = OSError("connection refused")
    monkeypatch.setattr(transport.http.client, "HTTPSConnection", lambda *args, **kwargs: c1)

    with pytest.raises(AnalysisError) as exc_info:
        _exchange("{}", "instructions", SecretStr("valid_secret_key"))
    assert exc_info.value.code == "network"

    # After deadline -> timeout error
    c2 = FakeHTTPSConnection("dummy")

    def connect_after_deadline() -> None:
        current_time[0] = 135.0
        raise OSError("connection reset")

    c2.connect = connect_after_deadline  # type: ignore[method-assign]
    monkeypatch.setattr(transport.http.client, "HTTPSConnection", lambda *args, **kwargs: c2)

    with pytest.raises(AnalysisError) as exc_info:
        _exchange("{}", "instructions", SecretStr("valid_secret_key"))
    assert exc_info.value.code == "timeout"


def test_exchange_http_status_and_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    # HTTP 500 -> http_error
    c1 = FakeHTTPSConnection("dummy")
    c1.response = FakeHTTPResponse(status=500, chunks=[b"server error"])
    monkeypatch.setattr(transport.http.client, "HTTPSConnection", lambda *args, **kwargs: c1)

    with pytest.raises(AnalysisError) as exc_info:
        _exchange("{}", "instructions", SecretStr("valid_secret_key"))
    assert exc_info.value.code == "http_error"

    # Bad content-length -> invalid_response
    c2 = FakeHTTPSConnection("dummy")
    c2.response = FakeHTTPResponse(
        status=200, headers={"Content-Length": "invalid"}, chunks=[b"{}"]
    )
    monkeypatch.setattr(transport.http.client, "HTTPSConnection", lambda *args, **kwargs: c2)

    with pytest.raises(AnalysisError) as exc_info:
        _exchange("{}", "instructions", SecretStr("valid_secret_key"))
    assert exc_info.value.code == "invalid_response"

    # Oversized content-length -> invalid_response
    c3 = FakeHTTPSConnection("dummy")
    c3.response = FakeHTTPResponse(
        status=200, headers={"Content-Length": "9999999"}, chunks=[b"{}"]
    )
    monkeypatch.setattr(transport.http.client, "HTTPSConnection", lambda *args, **kwargs: c3)

    with pytest.raises(AnalysisError) as exc_info:
        _exchange("{}", "instructions", SecretStr("valid_secret_key"))
    assert exc_info.value.code == "invalid_response"


def test_exchange_invalid_payload_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    def check_invalid_response(body_bytes: bytes) -> None:
        c = FakeHTTPSConnection("dummy")
        c.response = FakeHTTPResponse(status=200, chunks=[body_bytes])
        monkeypatch.setattr(transport.http.client, "HTTPSConnection", lambda *args, **kwargs: c)
        with pytest.raises(AnalysisError) as exc_info:
            _exchange("{}", "instructions", SecretStr("valid_secret_key"))
        assert exc_info.value.code == "invalid_response"

    # 1. Invalid JSON reply
    check_invalid_response(b"not json")

    # 2. Truncated finish reason
    check_invalid_response(
        json.dumps(
            {
                "model": transport.MODEL,
                "choices": [{"finish_reason": "length", "message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        ).encode()
    )

    # 3. Message contains tool_calls
    check_invalid_response(
        json.dumps(
            {
                "model": transport.MODEL,
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "{}", "tool_calls": [{}]}}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        ).encode()
    )

    # 4. Content is invalid inner JSON
    check_invalid_response(
        json.dumps(
            {
                "model": transport.MODEL,
                "choices": [{"finish_reason": "stop", "message": {"content": "{unquoted}"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        ).encode()
    )

    # 5. Invalid usage limits
    check_invalid_response(
        json.dumps(
            {
                "model": transport.MODEL,
                "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                "usage": {"prompt_tokens": -5, "completion_tokens": 1},
            }
        ).encode()
    )


def test_exchange_lifecycle_no_retries_and_no_canary_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    canary_key = SecretStr(f"key_{CANARY}")
    canary_payload = f"user_data_{CANARY}"

    c = FakeHTTPSConnection("dummy")
    c.response = FakeHTTPResponse(status=500, chunks=[CANARY.encode()])
    monkeypatch.setattr(transport.http.client, "HTTPSConnection", lambda *args, **kwargs: c)

    with pytest.raises(AnalysisError) as exc_info:
        _exchange(canary_payload, "instructions", canary_key)

    err_str = str(exc_info.value)
    assert CANARY not in err_str
    assert CANARY not in exc_info.value.code
    # Verify single connection and no retries
    assert c.connect_count == 1
    assert c.request_count == 1
    assert len(FakeHTTPSConnection.connection_instances) == 1
    assert c.closed
    assert FakeTimer.timer_instances[-1].started
    assert FakeTimer.timer_instances[-1].canceled


def test_explain_code_preserves_analysis_error(monkeypatch: pytest.MonkeyPatch) -> None:
    suite_id = uuid4()
    preview = case_preview(CASES[0].id, suite_id)

    def raise_timeout(*args: Any, **kwargs: Any) -> Any:
        raise AnalysisError("timeout")

    monkeypatch.setattr(code_transport, "_exchange", raise_timeout)

    with pytest.raises(AnalysisError) as exc_info:
        code_transport.explain_code(preview, SecretStr("valid_key"))
    assert exc_info.value.code == "timeout"


def test_explain_code_maps_invalid_review_content(monkeypatch: pytest.MonkeyPatch) -> None:
    suite_id = uuid4()
    preview = case_preview(CASES[0].id, suite_id)

    # Return malformed content structure for CodeReview (e.g. wrong schema)
    def return_invalid_structure(*args: Any, **kwargs: Any) -> Any:
        return {"not_findings": 123}, 10, 10

    monkeypatch.setattr(code_transport, "_exchange", return_invalid_structure)

    with pytest.raises(AnalysisError) as exc_info:
        code_transport.explain_code(preview, SecretStr("valid_key"))
    assert exc_info.value.code == "invalid_review"


def test_eval_failure_and_result_v2_validation() -> None:
    # 1. EvalFailure validation with known vs unknown case_id
    failure_valid = EvalFailure(code="timeout", case_id=CASES[0].id)
    assert failure_valid.case_id == CASES[0].id

    with pytest.raises(ValidationError):
        EvalFailure(code="timeout", case_id="completely_unknown_case_id")

    # 2. Completed EvalResultV2 must reject failure
    digest = fixture_digest()
    answers = {item.id: CodeReview(summary="ok", limitations="none", findings=()) for item in CASES}
    usage = {item.id: CaseUsage(input_tokens=100, output_tokens=50) for item in CASES}
    report = score_answers(answers)

    with pytest.raises(ValidationError, match="completed must not carry failure"):
        EvalResultV2(
            id=uuid4(),
            completed_at=utc_now(),
            status="completed",
            fixture_sha256=digest,
            answers=answers,
            usage=usage,
            report=report,
            failure=EvalFailure(code="timeout"),
        )

    # 3. Failed result without failure parses failure as None
    failed_result = EvalResultV2(
        id=uuid4(),
        completed_at=utc_now(),
        status="failed",
        fixture_sha256=digest,
        answers={},
        usage={},
        report=score_answers({}),
        failure=None,
    )
    assert failed_result.failure is None
    assert failed_result.status == "failed"


def test_exchange_timebudget_deadline_and_connection_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ssl

    current_time = [100.0]
    monkeypatch.setattr(transport.time, "monotonic", lambda: current_time[0])

    # 1. source_review=False (budget 30s): elapsed 31s times out
    c_free = FakeHTTPSConnection("dummy")
    captured_kwargs_free: dict = {}

    def connect_delay_31() -> None:
        c_free.connect_count += 1
        current_time[0] = 131.0
        raise OSError("connect failed after delay")

    c_free.connect = connect_delay_31  # type: ignore[method-assign]

    def factory_free(*args, **kwargs):
        captured_kwargs_free.update(kwargs)
        return c_free

    monkeypatch.setattr(transport.http.client, "HTTPSConnection", factory_free)

    with pytest.raises(AnalysisError) as exc_info:
        _exchange("{}", "instructions", SecretStr("valid_secret_key"), source_review=False)
    assert exc_info.value.code == "timeout"
    assert captured_kwargs_free.get("timeout") == 30
    assert captured_kwargs_free.get("context").check_hostname is True
    assert captured_kwargs_free.get("context").verify_mode == ssl.CERT_REQUIRED
    assert c_free.connect_count == 1
    assert c_free.closed
    assert FakeTimer.timer_instances[-1].interval == 30
    assert FakeTimer.timer_instances[-1].canceled

    # 2. source_review=True (budget 90s): elapsed 31s connects successfully
    FakeHTTPSConnection.connection_instances.clear()
    FakeTimer.timer_instances.clear()
    current_time[0] = 100.0
    c_review = FakeHTTPSConnection("dummy")
    captured_kwargs_review: dict = {}

    def connect_delay_to_131() -> None:
        c_review.connect_count += 1
        current_time[0] = 131.0

    c_review.connect = connect_delay_to_131  # type: ignore[method-assign]
    c_review.response = FakeHTTPResponse(status=200, chunks=[_valid_payload_bytes()])

    def factory_review(*args, **kwargs):
        captured_kwargs_review.update(kwargs)
        return c_review

    monkeypatch.setattr(transport.http.client, "HTTPSConnection", factory_review)

    res = _exchange("{}", "instructions", SecretStr("valid_secret_key"), source_review=True)
    assert res == ({"summary": "ok", "actions": []}, 10, 10)
    assert captured_kwargs_review.get("timeout") == 90
    assert captured_kwargs_review.get("context").check_hostname is True
    assert captured_kwargs_review.get("context").verify_mode == ssl.CERT_REQUIRED
    assert c_review.connect_count == 1
    assert c_review.request_count == 1
    assert len(FakeHTTPSConnection.connection_instances) == 1
    assert c_review.closed
    assert FakeTimer.timer_instances[-1].interval == 90
    assert FakeTimer.timer_instances[-1].canceled

    # 3. source_review=True: elapsed >90s (91s) times out
    FakeHTTPSConnection.connection_instances.clear()
    FakeTimer.timer_instances.clear()
    current_time[0] = 100.0
    c_over = FakeHTTPSConnection("dummy")
    captured_kwargs_over: dict = {}

    def connect_delay_91() -> None:
        c_over.connect_count += 1
        current_time[0] = 191.0
        raise OSError("connect failed after 91s")

    c_over.connect = connect_delay_91  # type: ignore[method-assign]

    def factory_over(*args, **kwargs):
        captured_kwargs_over.update(kwargs)
        return c_over

    monkeypatch.setattr(transport.http.client, "HTTPSConnection", factory_over)

    with pytest.raises(AnalysisError) as exc_info:
        _exchange("{}", "instructions", SecretStr("valid_secret_key"), source_review=True)
    assert exc_info.value.code == "timeout"
    assert captured_kwargs_over.get("timeout") == 90
    assert captured_kwargs_over.get("context").check_hostname is True
    assert captured_kwargs_over.get("context").verify_mode == ssl.CERT_REQUIRED
    assert c_over.connect_count == 1
    assert c_over.closed
    assert FakeTimer.timer_instances[-1].interval == 90
    assert FakeTimer.timer_instances[-1].canceled
