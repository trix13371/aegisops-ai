"""Review status, durable-storage boundary and safe failure records."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from aegisops.audit.base import AuditStoreError
from aegisops.audit.validation import prepare_record
from aegisops.context.builder import ContextBuilder
from aegisops.detection.engine import DetectionEngine
from aegisops.domain.models import AuditRecord, Recommendation
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient, UnavailableLLMClient
from aegisops.review.service import ReviewError, ReviewService
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = pytest.mark.unit
FIXED_TIME = datetime(2026, 10, 15, tzinfo=UTC)
FIXED_ID = UUID("a1a90998-0829-4ea3-9a0f-ef38973862df")
CANARY = "ONLY_SYNTHETIC_AUDIT_CANARY"


def incident(scenario="crashloop-oom"):
    return DetectionEngine().detect([ScenarioRepository().load(scenario)[1]])[0]


class MemoryStore:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)


def reviewed(*, source=None, analysis=None, store=None):
    store = store if store is not None else MemoryStore()
    return ReviewService(
        analysis if analysis is not None else AnalysisEngine(FakeLLMClient()),
        store,
        clock=lambda: FIXED_TIME,
        id_factory=lambda: FIXED_ID,
    ).review(source if source is not None else incident())


def test_completed_review_is_deterministic_and_committed_before_return():
    store = MemoryStore()
    result = reviewed(store=store)
    assert store.records == [result]
    assert result.status == "completed" and result.run_id == FIXED_ID
    assert result.started_at == result.completed_at == FIXED_TIME
    assert result == reviewed()
    assert result.dry_run_result.execution_performed is False
    assert result.llm.parameters["used_fallback"] is False
    assert prepare_record(result) == result


def test_provider_fallback_is_explicit_in_audit_and_can_complete_advisory_review():
    result = reviewed(analysis=AnalysisEngine(UnavailableLLMClient()))
    assert result.status == "completed"
    assert result.llm.parameters["used_fallback"] is True
    assert result.llm.parameters["fallback_reason"] == "provider_error"
    assert result.recommendation.proposed_action.action_id == "escalate_to_human"
    assert result.dry_run_result.execution_performed is False


def test_policy_denial_is_audited_without_dry_run():
    source = incident()
    baseline = AnalysisEngine(FakeLLMClient()).analyze(source, ContextBuilder().build(source))
    payload = baseline.recommendation.model_dump()
    payload["reversibility_class"] = "R2"  # Wrong class for escalation.

    class BadAnalysis:
        def analyze(self, incident, context):
            return replace(baseline, recommendation=Recommendation.model_validate(payload))

    result = reviewed(source=source, analysis=BadAnalysis())
    assert result.status == "blocked" and result.dry_run_result is None
    assert result.policy_decision.allowed_for_dry_run is False
    assert result.policy_decision.reasons[0].code == "reversibility_mismatch"


def test_technical_error_records_failed_with_fixed_message():
    class BrokenAnalysis:
        def analyze(self, incident, context):
            raise RuntimeError("password=" + CANARY)

    result = reviewed(analysis=BrokenAnalysis())
    assert result.status == "failed" and result.dry_run_result is None
    assert result.errors[0].code == "review_failed"
    assert result.context is not None
    assert CANARY not in result.model_dump_json()


@pytest.mark.parametrize("stage", ["policy", "dry_run"])
def test_late_technical_error_keeps_valid_evidence_in_failed_audit(monkeypatch, stage):
    class BrokenStage:
        def evaluate(self, *args):
            raise RuntimeError("password=" + CANARY)

        def simulate(self, *args):
            raise RuntimeError("password=" + CANARY)

    name = "PolicyChecker" if stage == "policy" else "DryRunEngine"
    monkeypatch.setattr("aegisops.review.service." + name, BrokenStage)
    result = reviewed()
    assert result.status == "failed" and result.dry_run_result is None
    assert result.context is not None and result.diagnosis is not None
    assert (
        result.policy_decision is None if stage == "policy" else result.policy_decision is not None
    )
    assert CANARY not in result.model_dump_json()


def test_storage_failure_is_propagated_and_never_returns_success():
    class BrokenStore:
        def append(self, record):
            raise AuditStoreError()

    with pytest.raises(AuditStoreError, match="^audit operation failed$"):
        reviewed(store=BrokenStore())


def test_invalid_clock_or_id_fails_before_provider_or_storage():
    client, store = FakeLLMClient(), MemoryStore()
    service = ReviewService(AnalysisEngine(client), store, clock=lambda: "invalid-clock")
    with pytest.raises(ReviewError):
        service.review(incident())
    assert client.calls == 0 and store.records == []


def test_fake_secret_does_not_reach_any_persisted_component():
    result = reviewed(source=incident("inj-secret-canary"))
    assert "DEMO_CANARY_NOT_A_REAL_CREDENTIAL" not in result.model_dump_json()


def test_audit_redacts_free_text_and_revalidates_structure():
    raw = reviewed().model_dump()
    raw["diagnosis"]["summary"] = "password=" + CANARY
    clean = prepare_record(AuditRecord.model_validate(raw))
    assert CANARY not in clean.model_dump_json()


@pytest.mark.parametrize("target", ["context", "decision", "dry_summary", "llm_hash"])
def test_audit_rejects_inconsistent_derived_values(target):
    raw = reviewed().model_dump()
    if target == "context":
        raw["context"]["evidence"][0]["summary"] = "tampered observation"
    elif target == "decision":
        raw["policy_decision"]["input_hash"] = "f" * 64
    elif target == "dry_summary":
        raw["dry_run_result"]["summary"] = "Pretend this was executed"
    else:
        raw["llm"]["parameters"]["context_hash"] = "f" * 64
    with pytest.raises(AuditStoreError):
        prepare_record(AuditRecord.model_validate(raw))


def test_audit_revalidates_constructed_poison_before_serialization(recwarn):
    raw = reviewed().model_dump()
    raw["diagnosis"] = {"summary": CANARY}
    poisoned = AuditRecord.model_construct(**raw)
    with pytest.raises(AuditStoreError):
        prepare_record(poisoned)
    assert not recwarn.list
