"""Actual loopback HTTP compatibility without a paid provider or external service."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aegisops.actions.catalog import load_catalog
from aegisops.context.builder import ContextBuilder
from aegisops.detection.engine import DetectionEngine
from aegisops.llm.base import LLMLimits
from aegisops.llm.fake import FakeLLMClient
from aegisops.llm.prompt import build_request
from aegisops.llm.transport import OpenAICompatibleClient
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = pytest.mark.integration


def test_real_loopback_http_sends_only_sanitized_json():
    incident = DetectionEngine().detect([ScenarioRepository().load("inj-secret-canary")[1]])[0]
    request = build_request(incident, ContextBuilder().build(incident), load_catalog())
    draft = FakeLLMClient().generate_structured(request, LLMLimits()).data
    received = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, body, self.headers.get("Authorization")))
            response = json.dumps(
                {
                    "model": "fixture-local",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json.dumps(
                                    draft.model_dump(mode="json"), ensure_ascii=False
                                )
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        client = OpenAICompatibleClient(
            provider="local",
            model_name="fixture-local",
            endpoint=f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
        )
        result = client.generate_structured(request, LLMLimits(timeout_seconds=5))
        assert result.data == draft
        assert len(received) == 1
        path, body, authorization = received[0]
        assert path == "/v1/chat/completions" and authorization is None
        assert b"DEMO_CANARY_NOT_A_REAL_CREDENTIAL" not in body
        payload = json.loads(body)
        assert payload["response_format"] == {"type": "json_object"}
        assert "tools" not in payload and "functions" not in payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
