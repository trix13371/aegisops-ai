"""Actual loopback HTTP compatibility without a paid provider or external service."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aegisops.actions.catalog import load_catalog
from aegisops.context.builder import ContextBuilder
from aegisops.detection.engine import DetectionEngine
from aegisops.domain.models import Origin, PodSnapshot
from aegisops.llm.base import LLMLimits
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.llm.prompt import build_request
from aegisops.llm.routing import DataClass
from aegisops.llm.transport import OpenAICompatibleClient
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "origin,data_class",
    [
        (Origin.SYNTHETIC_FIXTURE, None),
        (Origin.LOCAL_KIND, None),
        (Origin.LOCAL_KIND, DataClass.CONFIDENTIAL),
    ],
)
def test_real_loopback_http_sends_only_sanitized_json(origin, data_class):
    snapshot = ScenarioRepository().load("inj-secret-canary")[1]
    reference = DetectionEngine().detect([snapshot])[0]
    request = build_request(reference, ContextBuilder().build(reference), load_catalog())
    draft = FakeLLMClient().generate_structured(request, LLMLimits()).data
    raw = snapshot.model_dump()
    raw["origin"] = origin
    incident = DetectionEngine().detect([PodSnapshot.model_validate(raw)])[0]
    context = ContextBuilder().build(incident)
    response_draft = draft.model_dump(mode="json")
    response_draft["diagnosis"]["hypotheses"][0]["supporting_evidence_ids"] = [
        context.evidence[0].evidence_id
    ]
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
                            "message": {"content": json.dumps(response_draft, ensure_ascii=False)},
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
        result = AnalysisEngine(
            client, data_class=data_class, limits=LLMLimits(timeout_seconds=5)
        ).analyze(incident, context)
        assert not result.used_fallback
        assert result.metadata.parameters["route_allowed"] is True
        assert result.metadata.parameters["data_class"] == (
            data_class.value
            if data_class
            else ("internal" if origin == Origin.LOCAL_KIND else "public")
        )
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
