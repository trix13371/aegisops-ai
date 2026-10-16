"""Fixed OpenRouter endpoint, source-free input, bounded response, no retries or tools."""

import http.client
import json
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass

from pydantic import SecretStr

from aegisops.io import unique_object
from aegisops.project_ai.models import MODEL, Explanation, Summary


class AnalysisError(ValueError):
    def __init__(self) -> None:
        super().__init__("Нейросетевой разбор недоступен. Повтор автоматически не выполняется")


def reject_constant(value: str) -> None:
    raise AnalysisError()


def parse_json(value: str | bytes) -> object:
    return json.loads(value, object_pairs_hook=unique_object, parse_constant=reject_constant)


def explain(summary: Summary, key: SecretStr) -> tuple[Explanation, int, int]:
    try:
        summary = Summary.model_validate(summary)
        instructions = (
            "Ты объясняешь сводку статической проверки на русском языке. Ты не видел код, "
            "файлы или имена проекта. Нельзя утверждать, что приложение протестировано или "
            "безопасно, выдавать предположения за доказанные ошибки или предлагать исполнение "
            "команд. Дай краткий разбор, не более пяти приоритетных действий, их обоснование "
            "и следующий шаг для ручной проверки. Коды действий должны присутствовать во входе; "
            "если замечаний нет, actions пуст. Учитывай partial и ограничения профиля. "
            "Возврати только JSON по схеме: "
            + json.dumps(Explanation.model_json_schema(), ensure_ascii=False)
        )
        content, input_tokens, output_tokens = _exchange(
            summary.model_dump_json(), instructions, key
        )
        result = Explanation.model_validate(content)
        allowed = {item.code for item in summary.categories}
        if any(item.code not in allowed for item in result.actions):
            raise AnalysisError()
        return result, input_tokens, output_tokens
    except Exception:
        raise AnalysisError() from None


def _exchange(summary_json: str, instructions: str, key: SecretStr) -> tuple[object, int, int]:
    """Private wire boundary for fixed, locally validated explanation profiles only."""
    connection: http.client.HTTPSConnection | None = None
    wire_socket: socket.socket | None = None
    timer: threading.Timer | None = None
    try:
        secret = key.get_secret_value()
        if not secret or len(secret) > 4096 or any(not 33 <= ord(char) <= 126 for char in secret):
            raise AnalysisError()
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": summary_json},
            ],
            "max_tokens": 1600,
            "temperature": 0,
            "stream": False,
            "reasoning": {"enabled": False},
            "response_format": {"type": "json_object"},
            "provider": {"allow_fallbacks": False},
        }
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if len(body) > 16384:
            raise AnalysisError()
        host = "openrouter.ai"
        proxy = getproxies().get("https")
        endpoint = urlsplit(proxy) if proxy and not proxy_bypass(host) else None
        if endpoint is not None and (
            endpoint.scheme != "http"
            or endpoint.hostname is None
            or endpoint.username
            or endpoint.password
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
        ):
            raise AnalysisError()
        proxy_host = endpoint.hostname if endpoint else host
        if proxy_host is None:
            raise AnalysisError()
        connection = http.client.HTTPSConnection(
            proxy_host,
            (endpoint.port or 80) if endpoint else 443,
            timeout=30,
            context=ssl.create_default_context(),
        )
        if endpoint:
            connection.set_tunnel(host, 443)
        deadline = time.monotonic() + 30

        def abort() -> None:
            try:
                active = wire_socket or (connection.sock if connection is not None else None)
                if active is not None:
                    active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(30, abort)
        timer.daemon = True
        timer.start()
        connection.connect()
        wire_socket = connection.sock
        if time.monotonic() >= deadline:
            raise AnalysisError()
        connection.request(
            "POST",
            "/api/v1/chat/completions",
            body=body,
            headers={
                "Authorization": "Bearer " + secret,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "AegisOps/0.1",
            },
        )
        response = connection.getresponse()
        try:
            length = response.getheader("Content-Length")
            if (
                response.status != 200
                or length is not None
                and (not length.isdecimal() or int(length) > 65536)
            ):
                raise AnalysisError()
            data = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise AnalysisError()
                if connection.sock is not None:
                    connection.sock.settimeout(max(0.01, deadline - time.monotonic()))
                chunk = response.read1(min(4096, 65537 - len(data)))
                data.extend(chunk)
                if len(data) > 65536:
                    raise AnalysisError()
                if not chunk:
                    break
            reply = parse_json(bytes(data))
        finally:
            response.close()
        if not isinstance(reply, dict) or reply.get("model") != MODEL:
            raise AnalysisError()
        choices = reply.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise AnalysisError()
        choice = choices[0]
        if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
            raise AnalysisError()
        message, usage = choice.get("message"), reply.get("usage")
        if (
            not isinstance(message, dict)
            or not isinstance(usage, dict)
            or message.get("tool_calls")
            or message.get("function_call")
            or message.get("refusal")
            or not isinstance(message.get("content"), str)
        ):
            raise AnalysisError()
        content = parse_json(message["content"])
        input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if (
            type(input_tokens) is not int
            or not 0 <= input_tokens <= 100000
            or type(output_tokens) is not int
            or not 0 <= output_tokens <= 1600
        ):
            raise AnalysisError()
        return content, input_tokens, output_tokens
    except Exception:
        raise AnalysisError() from None
    finally:
        if timer is not None:
            timer.cancel()
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: S110 -- never expose provider exception/credential
                pass
