"""Only a bounded, strictly bound decision response from the fixed loopback endpoint."""

import json

import pytest

from aegisops.authorization.bundle import load_config
from aegisops.authorization.http import OPA_PATH, OpaClient, OpaError
from aegisops.authorization.projection import build_input
from aegisops.policy.checker import PolicyChecker

pytestmark = pytest.mark.unit


@pytest.fixture
def opa_request(authz_case):
    return build_input(*authz_case, PolicyChecker().evaluate(*authz_case), load_config())


def response_payload(opa_request):
    return {
        "result": {
            **{
                key: getattr(opa_request, key)
                for key in (
                    "policy_version",
                    "bundle_hash",
                    "catalog_hash",
                    "input_hash",
                )
            },
            "allow": True,
            "autonomy_level": "L0",
            "execution_performed": False,
        }
    }


def install(monkeypatch, payload, *, status=200, length=None):
    class Response:
        def __init__(self):
            self.status, self.body = status, payload

        def getheader(self, name):
            return length

        def read1(self, size):
            chunk, self.body = self.body[:size], self.body[size:]
            return chunk

        def close(self):
            pass

    class Connection:
        sock = None
        calls = []

        def __init__(self, host, port, **kwargs):
            assert (host, port, kwargs) == ("127.0.0.1", 18181, {"timeout": 2})

        def connect(self):
            pass

        def request(self, method, path, **kwargs):
            self.calls.append((method, path, kwargs))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("aegisops.authorization.http.http.client.HTTPConnection", Connection)
    return Connection


def test_fixed_endpoint_ignores_proxy_environment(monkeypatch, opa_request):
    monkeypatch.setenv("HTTP_PROXY", "http://198.51.100.1:9090")
    connection = install(monkeypatch, json.dumps(response_payload(opa_request)).encode())
    assert OpaClient().evaluate(opa_request).allow is True
    method, path, kwargs = connection.calls[0]
    assert (method, path) == ("POST", OPA_PATH)
    assert json.loads(kwargs["body"]) == {"input": opa_request.model_dump(mode="json")}


@pytest.mark.parametrize(
    "fault",
    [
        "undefined",
        "null",
        "numeric_allow",
        "extra",
        "version",
        "bundle_hash",
        "catalog_hash",
        "input_hash",
        "numeric_execution",
        "autonomy",
        "duplicate",
        "malformed",
        "oversized",
    ],
)
def test_strict_response_contract(monkeypatch, opa_request, fault):
    payload = response_payload(opa_request)
    if fault == "undefined":
        payload = {}
    elif fault == "null":
        payload = {"result": None}
    elif fault == "numeric_allow":
        payload["result"]["allow"] = 1
    elif fault == "extra":
        payload["result"]["command"] = "forbidden"
    elif fault == "version":
        payload["result"]["policy_version"] = "unknown"
    elif fault in {"bundle_hash", "catalog_hash", "input_hash"}:
        payload["result"][fault] = "0" * 64
    elif fault == "numeric_execution":
        payload["result"]["execution_performed"] = 0
    elif fault == "autonomy":
        payload["result"]["autonomy_level"] = "L1"
    body = json.dumps(payload).encode()
    if fault == "duplicate":
        body = b'{"result":{},"result":{}}'
    elif fault == "malformed":
        body = b"wrong json"
    elif fault == "oversized":
        body = b" " * 8193
    install(monkeypatch, body)
    with pytest.raises(OpaError, match="^local policy service rejected$"):
        OpaClient().evaluate(opa_request)


@pytest.mark.parametrize(
    "status,length", [(302, None), (403, None), (500, None), (200, "8193"), (200, "NaN")]
)
def test_redirect_status_and_content_length_denied(monkeypatch, opa_request, status, length):
    install(
        monkeypatch,
        json.dumps(response_payload(opa_request)).encode(),
        status=status,
        length=length,
    )
    with pytest.raises(OpaError):
        OpaClient().evaluate(opa_request)


def test_connection_failure_and_deadline_are_fixed_errors(monkeypatch, opa_request):
    def unavailable(*args, **kwargs):
        raise ConnectionRefusedError("password=NOT_FOR_OUTPUT")

    monkeypatch.setattr("aegisops.authorization.http.http.client.HTTPConnection", unavailable)
    with pytest.raises(OpaError) as error:
        OpaClient().evaluate(opa_request)
    assert error.value.code == "unavailable" and "NOT_FOR_OUTPUT" not in str(error.value)
    install(monkeypatch, b"")
    clock = iter([10, 13])
    monkeypatch.setattr("aegisops.authorization.http.time.monotonic", lambda: next(clock))
    with pytest.raises(OpaError) as error:
        OpaClient().evaluate(opa_request)
    assert error.value.code == "unavailable"
