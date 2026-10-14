import pytest

from aegisops.actions.catalog import load_catalog
from aegisops.context.builder import ContextBuilder
from aegisops.detection.engine import DetectionEngine
from aegisops.llm.base import AnalysisDraft, LLMError, LLMLimits, LLMReply, RunBudget
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.llm.prompt import build_request
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = pytest.mark.unit

FIXTURES = [
    "crashloop-exit-1",
    "crashloop-missing-env",
    "crashloop-bad-command",
    "crashloop-oom",
    "inj-log-instruction",
    "inj-secret-canary",
]


def _setup(scenario_name="crashloop-oom"):
    snapshot = ScenarioRepository().load(scenario_name)[1]
    incident = DetectionEngine().detect([snapshot])[0]
    context = ContextBuilder().build(incident)
    request = build_request(incident, context, load_catalog())
    draft = FakeLLMClient().generate_structured(request, LLMLimits()).data
    return incident, context, request, draft


@pytest.mark.parametrize("fixture_name", FIXTURES)
def test_fixtures_success(fixture_name):
    incident, context, _, _ = _setup(fixture_name)
    client = FakeLLMClient()
    result = AnalysisEngine(client).analyze(incident, context)
    assert not result.used_fallback and result.fallback_reason is None
    assert result.metadata.parameters["calls_this_incident"] == 1
    assert result.recommendation.proposed_action.action_id == "escalate_to_human"
    assert result.recommendation.execution_performed is False
    assert result.recommendation.requires_human_approval is True
    assert result.recommendation.reversibility_class == "R1"
    context_ids = {e.evidence_id for e in context.evidence}
    assert all(
        cite in context_ids
        for hypothesis in result.diagnosis.hypotheses
        for cite in hypothesis.supporting_evidence_ids
    )


def test_retry_on_initial_error():
    incident, context, _, draft = _setup()

    class RetryClient(FakeLLMClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def generate_structured(self, req, limits):
            self.calls += 1
            if self.calls == 1:
                raise LLMError()
            return LLMReply(draft, 10, 20)

    client = RetryClient()
    client.provider, client.model_name = "fake", "test-model"
    result = AnalysisEngine(client).analyze(incident, context)
    assert client.calls == 2
    assert not result.used_fallback


def test_always_failing_client_uses_fallback():
    incident, context, _, _ = _setup()

    class FailClient(FakeLLMClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def generate_structured(self, req, limits):
            self.calls += 1
            raise LLMError()

    client = FailClient()
    result = AnalysisEngine(client).analyze(incident, context)
    assert client.calls == 2 and result.used_fallback
    assert result.diagnosis.hypotheses[0].confidence == "low"
    assert result.diagnosis.hypotheses[0].cause_category == "unknown"
    assert result.recommendation.proposed_action.action_id == "escalate_to_human"
    assert result.metadata.parameters["usage_complete"] is False


def test_shared_run_budget_exhaustion():
    incident, context, _, _ = _setup()
    budget = RunBudget(LLMLimits(max_calls=1))
    engine = AnalysisEngine(FakeLLMClient(), budget=budget, limits=budget.limits)
    res1 = engine.analyze(incident, context)
    assert not res1.used_fallback
    client2 = FakeLLMClient()
    res2 = AnalysisEngine(client2, budget=budget, limits=budget.limits).analyze(incident, context)
    assert res2.used_fallback and res2.fallback_reason == "budget_exhausted"
    assert getattr(client2, "calls", 0) == 0


def test_input_byte_limit_refusal():
    incident, context, _, _ = _setup()
    client = FakeLLMClient()
    result = AnalysisEngine(client, limits=LLMLimits(max_input_bytes=1024)).analyze(
        incident, context
    )
    assert result.used_fallback
    assert getattr(client, "calls", 0) == 0


def test_invalid_evidence_id_fallback():
    incident, context, _, draft = _setup()
    data = draft.model_dump()
    data["diagnosis"]["hypotheses"][0]["supporting_evidence_ids"] = ["invented-nonexistent-id"]
    bad_draft = AnalysisDraft.model_validate(data)

    class BadIdClient(FakeLLMClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def generate_structured(self, req, limits):
            self.calls += 1
            return LLMReply(bad_draft, 10, 20)

    client = BadIdClient()
    result = AnalysisEngine(client).analyze(incident, context)
    assert client.calls == 2 and result.used_fallback


def test_invalid_workload_target_rejected():
    incident, context, _, draft = _setup()
    data = draft.model_dump()
    data["recommendation"]["proposed_action"] = {
        "action_id": "restart_workload",
        "params": {"namespace": "aegisops-demo", "workload": "wrong-workload"},
    }
    bad_draft = AnalysisDraft.model_validate(data)

    class ActionClient(FakeLLMClient):
        def generate_structured(self, req, limits):
            return LLMReply(bad_draft, 10, 20)

    result = AnalysisEngine(ActionClient()).analyze(incident, context)
    assert result.used_fallback
    assert result.recommendation.proposed_action.params.get("workload") != "wrong-workload"


def test_corrupted_context_hash_raises():
    incident, context, _, _ = _setup()
    c_data = context.model_dump()
    c_data["context_hash"] = "f" * 64
    bad_context = type(context).model_validate(c_data)
    client = FakeLLMClient()
    with pytest.raises(LLMError):
        AnalysisEngine(client).analyze(incident, bad_context)
    assert getattr(client, "calls", 0) == 0


def test_sanitization_removes_canary():
    incident, context, _, draft = _setup()
    data = draft.model_dump()
    data["diagnosis"]["summary"] = "issue password=FAKE_CREDENTIAL_CANARY found"
    data["recommendation"]["rationale"] = "action password=FAKE_CREDENTIAL_CANARY needed"
    clean_draft = AnalysisDraft.model_validate(data)

    class CanaryClient(FakeLLMClient):
        def generate_structured(self, req, limits):
            return LLMReply(clean_draft, 10, 20)

    result = AnalysisEngine(CanaryClient()).analyze(incident, context)
    assert not result.used_fallback
    serialized = result.diagnosis.model_dump_json() + result.recommendation.model_dump_json()
    assert "FAKE_CREDENTIAL_CANARY" not in serialized


def test_strict_bool_validation_in_limits():
    with pytest.raises(LLMError):
        LLMLimits(max_calls=True)


def test_large_valid_context_falls_back_without_provider_call():
    from aegisops.domain.models import PodSnapshot

    raw = ScenarioRepository().load("crashloop-oom")[1].model_dump()
    raw["events"] = [
        {
            "reason": "BackOff",
            "type": "Warning",
            "message": "A\n" * 2048,
            "count": 1,
            "last_seen": raw["observed_at"],
        }
        for _ in range(31)
    ]
    raw["log_tail"] = None
    incident = DetectionEngine().detect([PodSnapshot.model_validate(raw)])[0]
    context = ContextBuilder().build(incident)
    client = FakeLLMClient()
    result = AnalysisEngine(client).analyze(incident, context)
    assert result.used_fallback and result.fallback_reason == "budget_exhausted"
    assert client.calls == 0
