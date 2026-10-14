"""Fixture mechanics and honest rejection metrics, without external API calls."""

import pytest

from aegisops.audit.base import AuditStoreError
from aegisops.audit.validation import prepare_record
from aegisops.evaluation.suite import EvaluationError, evaluate_suite
from aegisops.policy.checker import PolicyChecker

pytestmark = pytest.mark.unit


class Store:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(prepare_record(record))


def test_evaluation_covers_all_fixtures_and_reports_metrics_separately():
    store = Store()
    result = evaluate_suite(store)
    assert result.passed
    assert (result.true_positives, result.true_negatives) == (6, 4)
    assert result.false_positives == result.false_negatives == 0
    assert result.cause_matches.numerator == result.cause_matches.denominator == 6
    assert result.allowed_actions.numerator == result.allowed_actions.denominator == 6
    assert result.adversarial_policy_probe_denials.numerator == 6
    # Harmless fake replies do not demonstrate policy denial of a malicious LLM reply.
    assert result.injection_fixture_policy_denials.numerator == 0
    assert result.injection_fixture_policy_denials.denominator == 1
    assert result.fake_calls == 6 and result.canary_leaks == result.execution_count == 0
    assert len(store.records) == 10
    assert len({record.run_id for record in store.records}) == 10
    assert sum(record.observation is not None for record in store.records) == 4
    assert "not_model_quality" in result.mode


def test_missing_case_cannot_silently_pass_evaluation(monkeypatch):
    monkeypatch.setattr("aegisops.evaluation.suite.ScenarioRepository.list_ids", lambda self: ())
    store = Store()
    with pytest.raises(EvaluationError):
        evaluate_suite(store)
    assert store.records == []


def test_evaluation_propagates_storage_failure():
    class BrokenStore:
        def append(self, record):
            raise AuditStoreError()

    with pytest.raises(AuditStoreError):
        evaluate_suite(BrokenStore())


def test_unsafe_policy_probe_regression_fails_evaluation(monkeypatch):
    original = PolicyChecker.evaluate

    def faulty_evaluate(self, incident, recommendation):
        if recommendation.reversibility_class == "R3":
            # Deliberately broken decision on the policy-only probe, not normal stored cases.
            safe = recommendation.model_dump()
            safe["reversibility_class"] = "R1"
            from aegisops.domain.models import Recommendation

            return original(self, incident, Recommendation.model_validate(safe))
        return original(self, incident, recommendation)

    monkeypatch.setattr(PolicyChecker, "evaluate", faulty_evaluate)
    result = evaluate_suite(Store())
    assert not result.passed
    assert result.adversarial_policy_probe_denials.numerator == 5
