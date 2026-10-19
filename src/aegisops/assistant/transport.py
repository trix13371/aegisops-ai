"""Direct-provider assistant wire boundary (OpenAI-compatible and Gemini).

Only fixed catalog hosts/paths/models are contacted. No redirects, retries,
streaming, tools or provider fallbacks. Failures surface as a single safe
exception so that provider details, keys and raw errors never leak.
"""

import http.client
import json
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

from pydantic import SecretStr

from aegisops.assistant.budgets import request_timeout
from aegisops.assistant.models import CATALOG, Selection
from aegisops.io import unique_object


class AssistantTransportError(ValueError):
    """Safe sentinel: no provider, key or raw wire detail is exposed."""

    def __init__(self) -> None:
        super().__init__("Нейросетевой разбор недоступен. Повтор автоматически не выполняется")


def _reject_constant(value: str) -> None:
    raise AssistantTransportError()


def _parse_json(value: str | bytes) -> object:
    return json.loads(value, object_pairs_hook=unique_object, parse_constant=_reject_constant)


def _strict_int(value: object, upper: int) -> int:
    if type(value) is not int or not 0 <= value <= upper:
        raise AssistantTransportError()
    return value


def _check_key(key: SecretStr) -> str:
    try:
        secret = key.get_secret_value()
    except Exception:
        raise AssistantTransportError() from None
    if (
        not isinstance(secret, str)
        or not secret
        or len(secret) > 4096
        or any(not 33 <= ord(char) <= 126 for char in secret)
    ):
        raise AssistantTransportError()
    return secret


def _proxy_endpoint(host: str) -> tuple[str, int] | None:
    """Bounded environment CONNECT proxy, credentials/query/fragment rejected."""
    try:
        raw = getproxies().get("https")
        if not raw or proxy_bypass(host):
            return None
        endpoint = urlsplit(raw)
        if (
            endpoint.scheme != "http"
            or endpoint.hostname is None
            or endpoint.username
            or endpoint.password
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
        ):
            raise AssistantTransportError()
        return endpoint.hostname, endpoint.port or 80
    except AssistantTransportError:
        raise
    except Exception:
        raise AssistantTransportError() from None


def _post(
    host: str,
    path: str,
    body: bytes,
    headers: dict[str, str],
    *,
    max_raw: int = 65536,
    source_review: bool = False,
) -> tuple[int, object]:
    """Verified-TLS POST with fixed timer, no redirects/retries/streaming."""
    connection: http.client.HTTPSConnection | None = None
    wire_socket: socket.socket | None = None
    timer: threading.Timer | None = None
    try:
        budget = request_timeout(source_review)
        proxy = _proxy_endpoint(host)
        connect_host = proxy[0] if proxy else host
        connect_port = proxy[1] if proxy else 443
        connection = http.client.HTTPSConnection(
            connect_host,
            connect_port,
            timeout=budget,
            context=ssl.create_default_context(),
        )
        if proxy:
            connection.set_tunnel(host, 443)
        deadline = time.monotonic() + budget

        def abort() -> None:
            try:
                active = wire_socket or (connection.sock if connection is not None else None)
                if active is not None:
                    active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(budget, abort)
        timer.daemon = True
        timer.start()
        connection.connect()
        wire_socket = connection.sock
        if time.monotonic() >= deadline:
            raise AssistantTransportError()
        connection.request("POST", path, body=body, headers=headers)
        response = connection.getresponse()
        try:
            length = response.getheader("Content-Length")
            if (
                response.status != 200
                or length is not None
                and (not length.isdecimal() or int(length) > max_raw)
            ):
                raise AssistantTransportError()
            data = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise AssistantTransportError()
                if connection.sock is not None:
                    connection.sock.settimeout(max(0.01, deadline - time.monotonic()))
                chunk = response.read1(min(4096, max_raw + 1 - len(data)))
                data.extend(chunk)
                if len(data) > max_raw:
                    raise AssistantTransportError()
                if not chunk:
                    break
            status = response.status
        finally:
            response.close()
        return status, _parse_json(bytes(data))
    except AssistantTransportError:
        raise
    except Exception:
        raise AssistantTransportError() from None
    finally:
        if timer is not None:
            timer.cancel()
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: S110 -- never expose provider exception
                pass


def _openai_payload(
    entry_model: str, provider: str, instructions: str, summary_json: str, max_output: int
) -> dict[str, object]:
    messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": summary_json},
    ]
    if provider == "deepseek":
        return {
            "model": entry_model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_output,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "stream": False,
        }
    # OpenAI gpt-5.4-mini: no temperature, reasoning off, no storage.
    return {
        "model": entry_model,
        "messages": messages,
        "max_completion_tokens": max_output,
        "reasoning_effort": "none",
        "store": False,
        "response_format": {"type": "json_object"},
        "stream": False,
    }


def _parse_openai(
    reply: object, requested: str, provider: str, max_output: int
) -> tuple[object, int, int]:
    if not isinstance(reply, dict):
        raise AssistantTransportError()
    model = reply.get("model")
    if not isinstance(model, str) or not _model_ok(model, requested, provider):
        raise AssistantTransportError()
    choices = reply.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise AssistantTransportError()
    choice = choices[0]
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        raise AssistantTransportError()
    message, usage = choice.get("message"), reply.get("usage")
    if (
        not isinstance(message, dict)
        or not isinstance(usage, dict)
        or message.get("tool_calls")
        or message.get("function_call")
        or message.get("refusal")
        or not isinstance(message.get("content"), str)
    ):
        raise AssistantTransportError()
    content = _parse_json(message["content"])
    input_tokens = _strict_int(usage.get("prompt_tokens"), 100000)
    output_tokens = _strict_int(usage.get("completion_tokens"), max_output)
    return content, input_tokens, output_tokens


def _model_ok(model: str, requested: str, provider: str) -> bool:
    if model == requested:
        return True
    if provider == "openai":
        # Accept dated snapshot prefix of the same approved model.
        return (
            model.startswith(requested + "-")
            and model[len(requested) + 1 :].replace("-", "").isdigit()
        )
    if provider == "deepseek":
        # Alias deepseek-flash may resolve to DeepSeek-V4.1-Flash-<date>.
        lowered = model.lower()
        prefix = "deepseek-v4.1-flash-"
        if not lowered.startswith(prefix):
            return False
        suffix = lowered[len(prefix) :]
        return (
            bool(suffix)
            and all(char.isdigit() or char == "-" for char in suffix)
            and any(char.isdigit() for char in suffix)
        )
    return False


def _gemini_payload(instructions: str, summary_json: str, max_output: int) -> dict[str, object]:
    return {
        "systemInstruction": {"parts": [{"text": instructions}]},
        "contents": [{"role": "user", "parts": [{"text": summary_json}]}],
        "generationConfig": {
            "maxOutputTokens": max_output,
            "responseMimeType": "application/json",
            "candidateCount": 1,
            "thinkingConfig": {"thinkingLevel": "LOW", "includeThoughts": False},
        },
    }


def _parse_gemini(reply: object, requested: str, max_output: int) -> tuple[object, int, int]:
    if not isinstance(reply, dict):
        raise AssistantTransportError()
    model_version = reply.get("modelVersion")
    if not isinstance(model_version, str) or not (
        model_version == requested
        or model_version.startswith(requested + "-")
        and model_version[len(requested) + 1 :].replace("-", "").isdigit()
    ):
        raise AssistantTransportError()
    feedback = reply.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        raise AssistantTransportError()
    usage = reply.get("usageMetadata")
    if not isinstance(usage, dict):
        raise AssistantTransportError()
    prompt_tokens = _strict_int(usage.get("promptTokenCount"), 100000)
    thoughts = usage.get("thoughtsTokenCount", 0)
    output_tokens = _strict_int(usage.get("candidatesTokenCount"), max_output)
    output_tokens += _strict_int(thoughts, max_output)
    if output_tokens > max_output:
        raise AssistantTransportError()
    candidates = reply.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise AssistantTransportError()
    candidate = candidates[0]
    if not isinstance(candidate, dict) or candidate.get("finishReason") != "STOP":
        raise AssistantTransportError()
    parts = (
        candidate.get("content", {}).get("parts")
        if isinstance(candidate.get("content"), dict)
        else None
    )
    if not isinstance(parts, list) or not parts:
        raise AssistantTransportError()
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            raise AssistantTransportError()
        if (
            part.get("thought")
            or "functionCall" in part
            or "executableCode" in part
            or "inlineData" in part
            or "fileData" in part
            or "codeExecutionResult" in part
        ):
            raise AssistantTransportError()
        text = part.get("text")
        if not isinstance(text, str):
            raise AssistantTransportError()
        texts.append(text)
    content = _parse_json("".join(texts))
    return content, prompt_tokens, output_tokens


def exchange(
    summary_json: str,
    instructions: str,
    key: SecretStr,
    selection: Selection,
    *,
    source_review: bool = False,
) -> tuple[object, int, int]:
    """Fixed direct-provider exchange. `openrouter` is never served here."""
    connection_started = False
    try:
        if not isinstance(summary_json, str) or not isinstance(instructions, str):
            raise AssistantTransportError()
        try:
            selection = Selection.model_validate(selection)
        except Exception:
            raise AssistantTransportError() from None
        if selection.provider == "openrouter":
            raise AssistantTransportError()
        entry = CATALOG.get(selection.provider)
        if entry is None or entry.model != selection.model:
            raise AssistantTransportError()
        secret = _check_key(key)
        max_request = 65536 if source_review else 16384
        max_output = 2800 if source_review else 1600
        if entry.protocol == "openai-chat":
            payload = _openai_payload(
                entry.model, selection.provider, instructions, summary_json, max_output
            )
        else:
            payload = _gemini_payload(instructions, summary_json, max_output)
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(body) > max_request:
            raise AssistantTransportError()
        path = entry.inference_path.replace("{model}", entry.model)
        if entry.protocol == "gemini-generate":
            headers = {
                "x-goog-api-key": secret,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "AegisOps/0.1",
            }
        else:
            headers = {
                "Authorization": "Bearer " + secret,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "AegisOps/0.1",
            }
        connection_started = True
        _, reply = _post(entry.host, path, body, headers, source_review=source_review)
        if entry.protocol == "gemini-generate":
            return _parse_gemini(reply, entry.model, max_output)
        return _parse_openai(reply, entry.model, selection.provider, max_output)
    except AssistantTransportError:
        raise
    except Exception:
        # Fail closed once the connection/timer are cleaned up by _post.
        raise AssistantTransportError() from None
    finally:
        del connection_started
