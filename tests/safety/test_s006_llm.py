"""Provider output cannot grant authority or cite evidence it never received."""

import json

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from aegisops.actions.catalog import load_catalog
from aegisops.cli.app import app
from aegisops.context.builder import ContextBuilder
from aegisops.detection.engine import DetectionEngine
from aegisops.domain.models import ContextLimits, PodSnapshot
from aegisops.llm.base import AnalysisDraft, LLMLimits, LLMReply
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.llm.prompt import build_request
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = pytest.mark.safety


def prepared(scenario="crashloop-oom", *, owner_kind=None):
    raw = ScenarioRepository().load(scenario)[1].model_dump()
    if owner_kind:
        raw["owner"]["kind"] = owner_kind
    incident = DetectionEngine().detect([PodSnapshot.model_validate(raw)])[0]
    context = ContextBuilder().build(incident)
    reference = ScenarioRepository().load(scenario)[1]
    reference_incident = DetectionEngine().detect([reference])[0]
    reference_context = ContextBuilder().build(reference_incident)
    draft = (
        FakeLLMClient()
        .generate_structured(
            build_request(reference_incident, reference_context, load_catalog()), LLMLimits()
        )
        .data.model_dump()
    )
    draft["diagnosis"]["hypotheses"][0]["supporting_evidence_ids"] = [
        context.evidence[0].evidence_id
    ]
    return incident, context, draft


class StubClient:
    provider, model_name = "fake", "controlled-test"

    def __init__(self, payload):
        self.payload, self.calls = payload, 0

    def generate_structured(self, request, limits):
        self.calls += 1
        return LLMReply(AnalysisDraft.model_validate(self.payload), 10, 20)


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_performed", True),
        ("requires_human_approval", False),
        ("reversibility_class", "R1"),
        ("recommendation_id", "forged"),
    ],
)
def test_model_cannot_set_security_fields(field, value):
    incident, context, data = prepared()
    data["recommendation"][field] = value
    client = StubClient(data)
    result = AnalysisEngine(client).analyze(incident, context)
    assert client.calls == 2 and result.used_fallback
    assert result.recommendation.execution_performed is False
    assert result.recommendation.requires_human_approval is True
    assert result.recommendation.proposed_action.action_id == "escalate_to_human"


def test_known_incident_evidence_not_sent_in_context_is_rejected():
    incident, _, data = prepared()
    assert len(incident.evidence) > 1
    context = ContextBuilder(ContextLimits(max_evidence=1)).build(incident)
    data["diagnosis"]["hypotheses"][0]["supporting_evidence_ids"] = [
        incident.evidence[-1].evidence_id
    ]
    client = StubClient(data)
    result = AnalysisEngine(client).analyze(incident, context)
    assert result.used_fallback and client.calls == 2


@pytest.mark.parametrize(
    "kind,action,accepted",
    [
        ("Deployment", "restart_workload", True),
        ("Deployment", "rollback_deployment", True),
        ("ReplicaSet", "rollback_deployment", False),
    ],
)
def test_catalog_class_and_actual_target_owner_determine_result(kind, action, accepted):
    incident, context, data = prepared(owner_kind=kind)
    target_key = "workload" if action == "restart_workload" else "deployment"
    data["recommendation"]["proposed_action"] = {
        "action_id": action,
        "params": {"namespace": "aegisops-demo", target_key: incident.workload_name},
    }
    result = AnalysisEngine(StubClient(data)).analyze(incident, context)
    assert result.used_fallback is not accepted
    if accepted:
        assert result.recommendation.reversibility_class == (
            "R2" if action == "restart_workload" else "R1"
        )
    assert result.recommendation.execution_performed is False
    assert result.recommendation.requires_human_approval is True


def test_prompt_boundary_removes_canary_and_keeps_injection_only_as_json():
    incident, context, _ = prepared("inj-secret-canary")
    request = build_request(incident, context, load_catalog())
    assert "DEMO_CANARY_NOT_A_REAL_CREDENTIAL" not in request.system + request.user
    injected, injected_context, _ = prepared("inj-log-instruction")
    packet = build_request(injected, injected_context, load_catalog())
    assert "execute shell" in packet.user and "execute shell" not in packet.system
    assert packet.user.count("\nEND_UNTRUSTED_OBSERVATIONS_JSON") == 1
    assert "not_a_catalog_action" not in packet.system


def test_invalid_extra_action_and_boolean_citations_do_not_validate():
    _, _, data = prepared()
    data["recommendation"]["proposed_action"] = {"action_id": "execute_shell", "params": {}}
    with pytest.raises(ValidationError):
        AnalysisDraft.model_validate(data)


@pytest.mark.parametrize("mode", ["fake", "none"])
def test_analysis_cli_is_free_by_default_and_never_claims_execution(mode, monkeypatch):
    monkeypatch.delenv("AEGISOPS_LLM_API_KEY", raising=False)
    result = CliRunner().invoke(app, ["analyze", "--scenario", "inj-secret-canary", "--llm", mode])
    assert result.exit_code == 0
    assert "DEMO_CANARY_NOT_A_REAL_CREDENTIAL" not in result.output
    payload = json.loads(result.output)
    assert payload["execution_available"] is False
    row = payload["results"][0]
    assert row["recommendation"]["execution_performed"] is False
    assert row["used_fallback"] is (mode == "none")


@pytest.mark.parametrize(
    "action",
    ["escalate_to_human", "collect_more_evidence", "restart_workload", "rollback_deployment"],
)
def test_catalog_rejects_extra_parameters_after_target_check(action):
    incident, context, data = prepared()
    params = {"unexpected": "model cannot add a command"}
    if action == "collect_more_evidence":
        params["scope"] = "events"
    elif action in {"restart_workload", "rollback_deployment"}:
        params["namespace"] = "aegisops-demo"
        params["workload" if action == "restart_workload" else "deployment"] = (
            incident.workload_name
        )
    data["recommendation"]["proposed_action"] = {"action_id": action, "params": params}
    client = StubClient(data)
    result = AnalysisEngine(client).analyze(incident, context)
    assert result.used_fallback and client.calls == 2
    assert "unexpected" not in result.recommendation.proposed_action.params
    assert result.recommendation.execution_performed is False
