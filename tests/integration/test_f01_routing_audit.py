"""Routing refusal still produces a durable, explicitly marked L0 fallback."""

import os
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from aegisops.audit.reader import PostgresAuditReader
from aegisops.cli.app import app
from aegisops.domain.models import Origin, PodSnapshot
from aegisops.pipeline.models import PipelineResult
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = [pytest.mark.integration, pytest.mark.safety]


@pytest.mark.parametrize("live_origin", [False, True])
def test_denied_route_is_saved_before_successful_output(monkeypatch, live_origin):
    url = os.environ.get("AEGISOPS_DATABASE_URL")
    if not url:
        pytest.skip("local PostgreSQL not configured")
    if live_origin:
        raw = ScenarioRepository().load("crashloop-oom")[1].model_dump()
        raw["origin"] = Origin.LOCAL_KIND
        snapshots = [PodSnapshot.model_validate(raw)]
        monkeypatch.setattr(
            "aegisops.sources.pipeline.LocalKindSource.collect", lambda self: snapshots
        )
        monkeypatch.setenv("AEGISOPS_LLM_API_KEY", "F01_FAKE_KEY_NEVER_REAL")
        arguments = ["run", "--source", "local-kind", "--llm", "openrouter"]
        reason, classification = "external_data_denied", "internal"
    else:
        arguments = ["run", "--scenario", "crashloop-oom", "--data-class", "restricted"]
        reason, classification = "restricted_data", "restricted"
    with (
        patch("aegisops.llm.transport.OpenAICompatibleClient.generate_structured") as transport,
        patch("aegisops.llm.fake.FakeLLMClient.generate_structured") as fake,
    ):
        result = CliRunner().invoke(app, arguments)
        transport.assert_not_called()
        fake.assert_not_called()
    assert result.exit_code == 0, result.output
    batch = PipelineResult.model_validate_json(result.output)
    assert batch.status == "completed" and batch.incident_count == 1
    record = batch.records[0]
    assert record.llm is not None
    parameters = record.llm.parameters
    assert parameters["route_allowed"] is False
    assert parameters["data_class"] == classification
    assert parameters["route_reason"] == reason
    assert parameters["fallback_reason"] == "routing_denied"
    assert parameters["used_fallback"] is True
    assert parameters["calls_this_incident"] == parameters["reserved_tokens"] == 0
    assert record.recommendation.execution_performed is False
    with PostgresAuditReader(url) as reader:
        assert reader.show(record.run_id) == record
