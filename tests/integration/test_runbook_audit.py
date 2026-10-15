"""Runbook references survive the full 16-field metadata envelope and durable audit."""

import os

import pytest

from aegisops.actions.catalog import load_catalog
from aegisops.audit.postgres import PostgresAuditStore
from aegisops.audit.reader import PostgresAuditReader
from aegisops.context.builder import ContextBuilder
from aegisops.detection.engine import DetectionEngine
from aegisops.llm.base import LLMReply
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.llm.prompt import build_request, request_runbooks
from aegisops.review.service import ReviewService
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = [pytest.mark.integration, pytest.mark.safety]


def test_runbook_references_and_returned_model_fit_full_audit_metadata():
    url = os.environ.get("AEGISOPS_DATABASE_URL")
    if not url:
        pytest.skip("local PostgreSQL not configured")

    class ReturningClient(FakeLLMClient):
        def generate_structured(self, request, limits):
            reply = super().generate_structured(request, limits)
            return LLMReply(reply.data, response_model="fixture-observed-v2")

    incident = DetectionEngine().detect([ScenarioRepository().load("crashloop-oom")[1]])[0]
    selected = request_runbooks(
        build_request(incident, ContextBuilder().build(incident), load_catalog())
    )
    with PostgresAuditStore(url) as store:
        record = ReviewService(AnalysisEngine(ReturningClient()), store).review(incident)
    assert record.status == "completed" and record.llm.prompt_id == "analysis-v2"
    parameters = record.llm.parameters
    assert len(parameters) == 16
    assert parameters["returned_model"] == "fixture-observed-v2"
    assert parameters["runbook_selection_hash"] == selected.selection_hash
    assert parameters["runbook_catalog_version"] == "1.0.0"
    assert parameters["runbook_refs"] == "crashloop-triage@1.0.0,oom-triage@1.0.0"
    assert parameters["used_fallback"] is False
    with PostgresAuditReader(url) as reader:
        assert reader.show(record.run_id) == record
