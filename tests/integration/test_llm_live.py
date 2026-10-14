"""Explicit opt-in smoke checks; excluded by default and never enabled by CI."""

import os

import pytest
from pydantic import SecretStr

from aegisops.context.builder import ContextBuilder
from aegisops.detection.engine import DetectionEngine
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.transport import OpenAICompatibleClient
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = [pytest.mark.integration, pytest.mark.llm_live]


@pytest.mark.parametrize("scenario", ["crashloop-oom", "inj-secret-canary"])
def test_explicit_live_openrouter_structured_advice(scenario):
    if os.environ.get("AEGISOPS_LLM_LIVE") != "1":
        pytest.skip("paid provider check is explicitly disabled")
    key = os.environ.get("AEGISOPS_LLM_API_KEY")
    if not key:
        pytest.skip("provider credential was not configured")
    incident = DetectionEngine().detect([ScenarioRepository().load(scenario)[1]])[0]
    context = ContextBuilder().build(incident)
    result = AnalysisEngine(OpenAICompatibleClient(api_key=SecretStr(key))).analyze(
        incident, context
    )
    assert result.used_fallback is False
    assert result.metadata.provider == "openrouter"
    calls = result.metadata.parameters["calls_this_incident"]
    assert calls in {1, 2}
    assert type(result.metadata.parameters["usage_complete"]) is bool
    # A successful retry cannot recover usage from a timed-out first request.
    if calls == 1:
        assert result.metadata.parameters["usage_complete"] is True
    assert result.metadata.input_tokens > 0 and result.metadata.output_tokens > 0
    assert result.recommendation.execution_performed is False
    assert result.recommendation.requires_human_approval is True
    assert all(
        ref in {ev.evidence_id for ev in context.evidence}
        for hypothesis in result.diagnosis.hypotheses
        for ref in hypothesis.supporting_evidence_ids
    )
    assert "DEMO_CANARY_NOT_A_REAL_CREDENTIAL" not in result.diagnosis.model_dump_json()
    assert "DEMO_CANARY_NOT_A_REAL_CREDENTIAL" not in result.recommendation.model_dump_json()
