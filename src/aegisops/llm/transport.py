"""Bounded JSON HTTP; origin TLS remains verified through a configured CONNECT proxy."""

import http.client
import json
import re
import socket
import ssl
import threading
import time
from typing import Any
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

from pydantic import SecretStr

from aegisops.context.sanitizer import sanitize_text
from aegisops.io import unique_object
from aegisops.llm.base import AnalysisDraft, LLMError, LLMLimits, LLMReply, LLMRequest
from aegisops.llm.prompt import verify_request
from aegisops.llm.routing import decide_route

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}")


def build_payload(
    request: LLMRequest, limits: LLMLimits, model: str, provider: str
) -> dict[str, Any]:
    verify_request(request)
    if not decide_route(request.origin, provider, request.data_class).allowed:
        raise LLMError()
    limits.__post_init__()
    if request.input_bytes > limits.max_input_bytes or _MODEL.fullmatch(model) is None:
        raise LLMError()
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ],
        "temperature": 0,
        "max_tokens": limits.max_output_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    if provider == "openrouter":
        payload["reasoning"] = (
            {"enabled": False} if model.startswith("deepseek/") else {"effort": "low"}
        )
    return payload


def _reject_constant(value: str) -> None:
    raise LLMError()


def _json(raw: bytes | str) -> object:
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=_reject_constant)


class OpenAICompatibleClient:
    """Only OpenRouter HTTPS or an explicitly chosen literal-loopback local endpoint."""

    def __init__(
        self,
        *,
        provider: str = "openrouter",
        model_name: str = DEFAULT_MODEL,
        endpoint: str = OPENROUTER_URL,
        api_key: SecretStr | None = None,
    ) -> None:
        try:
            if type(endpoint) is not str or any(ord(ch) < 32 or ord(ch) == 127 for ch in endpoint):
                raise LLMError()
            parsed = urlsplit(endpoint)
            if (
                _MODEL.fullmatch(model_name) is None
                or sanitize_text(model_name) != model_name
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path != "/v1/chat/completions"
                and provider == "local"
            ):
                raise LLMError()
            if provider == "openrouter":
                if endpoint != OPENROUTER_URL or api_key is None:
                    raise LLMError()
            elif provider == "local":
                if (
                    parsed.hostname not in {"127.0.0.1", "::1"}
                    or parsed.scheme not in {"http", "https"}
                    or parsed.port is None
                    or not 1 <= parsed.port <= 65535
                ):
                    raise LLMError()
            else:
                raise LLMError()
            if api_key is not None:
                key = api_key.get_secret_value()
                if not key or len(key) > 4096 or any(ord(ch) < 33 or ord(ch) > 126 for ch in key):
                    raise LLMError()
            self.provider = provider
            self.model_name = model_name
            self._endpoint = parsed
            self._key = api_key
        except Exception:
            raise LLMError() from None

    def generate_structured(self, request: LLMRequest, limits: LLMLimits) -> LLMReply:
        connection: http.client.HTTPConnection | None = None
        wire_socket: socket.socket | None = None
        timer: threading.Timer | None = None
        try:
            payload = build_payload(request, limits, self.model_name, self.provider)
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": "AegisOps/0.1",
            }
            if self._key is not None:
                headers["Authorization"] = "Bearer " + self._key.get_secret_value()
            host = self._endpoint.hostname
            if host is None:
                raise LLMError()
            if self._endpoint.scheme == "https":
                proxy = getproxies().get("https") if self.provider == "openrouter" else None
                proxy_endpoint = urlsplit(proxy) if proxy and not proxy_bypass(host) else None
                if proxy_endpoint is not None and (
                    proxy_endpoint.scheme != "http"
                    or proxy_endpoint.hostname is None
                    or proxy_endpoint.username
                    or proxy_endpoint.password
                    or proxy_endpoint.path not in {"", "/"}
                    or proxy_endpoint.query
                    or proxy_endpoint.fragment
                ):
                    raise LLMError()
                proxy_host = proxy_endpoint.hostname if proxy_endpoint is not None else host
                if proxy_host is None:
                    raise LLMError()
                connection = http.client.HTTPSConnection(
                    proxy_host,
                    (proxy_endpoint.port or 80)
                    if proxy_endpoint is not None
                    else self._endpoint.port,
                    timeout=limits.timeout_seconds,
                    context=ssl.create_default_context(),
                )
                if proxy_endpoint is not None:
                    connection.set_tunnel(host, self._endpoint.port or 443)
            else:
                connection = http.client.HTTPConnection(
                    host, self._endpoint.port, timeout=limits.timeout_seconds
                )
            deadline = time.monotonic() + limits.timeout_seconds

            def abort() -> None:
                try:
                    active = wire_socket or (connection.sock if connection is not None else None)
                    if active is not None:
                        active.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            timer = threading.Timer(limits.timeout_seconds, abort)
            timer.daemon = True
            timer.start()
            connection.connect()
            wire_socket = connection.sock
            if time.monotonic() >= deadline:
                raise LLMError()
            connection.request("POST", self._endpoint.path, body=body, headers=headers)
            response = connection.getresponse()
            try:
                if response.status != 200:
                    raise LLMError()
                length = response.getheader("Content-Length")
                if length is not None and (
                    not length.isdecimal() or int(length) > limits.max_response_bytes
                ):
                    raise LLMError()
                data = bytearray()
                while True:
                    if time.monotonic() >= deadline:
                        raise LLMError()
                    if connection.sock is not None:
                        connection.sock.settimeout(max(0.01, deadline - time.monotonic()))
                    chunk = response.read1(min(4096, limits.max_response_bytes + 1 - len(data)))
                    data.extend(chunk)
                    if len(data) > limits.max_response_bytes:
                        raise LLMError()
                    if not chunk:
                        break
                reply = _json(bytes(data).decode("utf-8"))
            finally:
                response.close()
            if not isinstance(reply, dict):
                raise LLMError()
            choices = reply.get("choices")
            if not isinstance(choices, list) or len(choices) != 1:
                raise LLMError()
            choice = choices[0]
            if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
                raise LLMError()
            message = choice.get("message")
            if (
                not isinstance(message, dict)
                or message.get("tool_calls")
                or message.get("function_call")
                or message.get("refusal")
            ):
                raise LLMError()
            content = message.get("content")
            if not isinstance(content, str):
                raise LLMError()
            usage = reply.get("usage")
            if not isinstance(usage, dict):
                raise LLMError()
            input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if type(input_tokens) is not int or type(output_tokens) is not int:
                raise LLMError()
            returned_model = reply.get("model", self.model_name)
            if type(returned_model) is not str or _MODEL.fullmatch(returned_model) is None:
                raise LLMError()
            return LLMReply(
                AnalysisDraft.model_validate(_json(content)),
                input_tokens,
                output_tokens,
                returned_model,
            )
        except Exception:
            raise LLMError() from None
        finally:
            if timer is not None:
                timer.cancel()
            if connection is not None:
                try:
                    connection.close()
                except Exception:  # noqa: S110 - never log a credential-bearing transport error
                    pass
