"""Distinct incident comparisons must not turn unknown or partial labels into success."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from aegisops.detection.engine import DetectionEngine
from aegisops.domain.common import utc_now
from aegisops.domain.models import AuditRecord, CauseCategory, Origin
from aegisops.feedback.models import FeedbackError, FeedbackRecord
from aegisops.feedback.quality import compare_labels
from aegisops.feedback.quality_models import ComparisonStatus, PredictionKind, QualityReport
from aegisops.feedback.validation import recommendation_hash, sanitize_feedback
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.review.service import ReviewService
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = pytest.mark.unit


def audit():
    class Store:
        def append(self, record):
            pass

    incident = DetectionEngine().detect([ScenarioRepository().load("crashloop-oom")[1]])[0]
    return ReviewService(AnalysisEngine(FakeLLMClient()), Store()).review(incident)


def label(record, **changes):
    values = dict(
        feedback_id=uuid4(),
        run_id=record.run_id,
        origin=record.origin,
        recommendation_id=record.recommendation.recommendation_id,
        recommendation_hash=recommendation_hash(record.recommendation),
        reviewer="alice",
        assessment="useful",
        created_at=utc_now(),
        confirmed_cause="oom_killed",
        incident_outcome="resolved",
        reference="PM-42",
    )
    values.update(changes)
    return sanitize_feedback(values)


def compare(pairs, **changes):
    options = dict(origin=Origin.SYNTHETIC_FIXTURE, limit=100, has_more=False)
    options.update(changes)
    return compare_labels(tuple(pairs), **options)


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmed_cause": "unknown"},
        {"reference": None},
        {"reference": "bad reference"},
        {"reference": "PM-" + "x" * 63},
        {"incident_outcome": "fixed_by_ai"},
    ],
)
def test_bad_or_unreferenced_labels_rejected(changes):
    with pytest.raises(FeedbackError):
        label(audit(), **changes)


def test_old_feedback_without_labels_still_loads_and_is_unlabelled():
    record = audit()
    raw = label(record).model_dump()
    for name in ("confirmed_cause", "incident_outcome", "reference"):
        raw.pop(name)
    feedback = FeedbackRecord.model_validate(raw)
    result = compare([(feedback, record)])
    assert result.unlabelled == 1 and result.matches == result.resolved_incidents == 0


@pytest.mark.parametrize(
    "cause,expected",
    [
        (CauseCategory.OOM_KILLED, ComparisonStatus.MATCH),
        (CauseCategory.INVALID_COMMAND, ComparisonStatus.MISMATCH),
    ],
)
def test_concrete_cause_match_or_mismatch_and_fixture_identity(cause, expected):
    record = audit()
    result = compare([(label(record, confirmed_cause=cause), record)])
    assert len(result.cases) == 1 and result.cases[0].status is expected
    assert result.cases[0].prediction_kind is PredictionKind.FIXTURE
    assert result.cases[0].provider == "fake" and result.resolved_incidents == 1
    assert result.mode == "local_label_comparison_not_model_quality"


@pytest.mark.parametrize("multiple", [False, True])
def test_unknown_or_multiple_predicted_causes_abstain(multiple):
    raw = audit().model_dump()
    hypothesis = dict(raw["diagnosis"]["hypotheses"][0])
    hypothesis["cause_category"] = "unknown" if not multiple else "invalid_command"
    raw["diagnosis"]["hypotheses"] = (
        (hypothesis,) if not multiple else (*raw["diagnosis"]["hypotheses"], hypothesis)
    )
    record = AuditRecord.model_validate(raw)
    result = compare([(label(record), record)])
    assert result.abstentions == 1 and result.matches == result.mismatches == 0


def test_repeat_runs_and_reviewers_count_one_incident_using_latest_assessed_run():
    older, newer = audit(), audit()
    rows = [(label(older), older), (label(older, reviewer="bob"), older), (label(newer), newer)]
    result = compare(rows)
    assert result.sample_size == 3 and len(result.cases) == result.matches == 1
    assert result.cases[0].run_id == newer.run_id
    assert len(result.cases[0].feedback_ids) == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmed_cause": "invalid_command"},
        {"incident_outcome": "unresolved"},
    ],
)
def test_conflicting_causes_or_outcomes_do_not_produce_matches(changes):
    record = audit()
    result = compare([(label(record), record), (label(record, reviewer="bob", **changes), record)])
    assert result.conflicts == 1 and result.matches == result.resolved_incidents == 0
    assert result.cases[0].confirmed_cause is result.cases[0].incident_outcome is None


def test_incomplete_has_priority_over_apparent_consensus_and_conflicts():
    record = audit()
    rows = [
        (label(record), record),
        (label(record, reviewer="bob", confirmed_cause="invalid_command"), record),
    ]
    result = compare(
        rows, limit=2, has_more=True, incomplete_incidents=frozenset({record.incident.incident_id})
    )
    assert result.incomplete == 1 and result.conflicts == result.matches == 0
    assert result.resolved_incidents == 0


@pytest.mark.parametrize(
    "fallback,provider,expected",
    [
        (True, "local", PredictionKind.FALLBACK),
        (False, "local", PredictionKind.MODEL),
        (False, "openrouter", PredictionKind.MODEL),
        (None, "fake", PredictionKind.UNCLASSIFIED),
    ],
)
def test_prediction_provenance_is_explicit(fallback, provider, expected):
    raw = audit().model_dump()
    raw["llm"]["provider"] = provider
    raw["llm"]["parameters"]["used_fallback"] = fallback
    record = AuditRecord.model_validate(raw)
    assert compare([(label(record), record)]).cases[0].prediction_kind is expected


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": uuid4()},
        {"origin": "local_kind"},
        {"recommendation_id": "other"},
        {"recommendation_hash": "0" * 64},
    ],
)
def test_cross_run_origin_or_recommendation_bindings_fail(changes):
    record = audit()
    with pytest.raises(FeedbackError):
        compare([(label(record, **changes), record)])


def test_duplicate_or_superseded_input_is_rejected():
    record = audit()
    first = label(record)
    second = label(record, supersedes_id=first.feedback_id)
    for rows in ([(first, record)] * 2, [(first, record), (second, record)]):
        with pytest.raises(FeedbackError):
            compare(rows)


@pytest.mark.parametrize(
    "options",
    [
        {"limit": True},
        {"limit": 0},
        {"limit": 101},
        {"has_more": 1},
        {"origin": "synthetic_fixture"},
        {"incomplete_incidents": frozenset({"unknown-incident"}), "has_more": True, "limit": 1},
    ],
)
def test_malformed_sample_options_fail(options):
    record = audit()
    with pytest.raises(FeedbackError):
        compare([(label(record), record)], **options)


def test_report_rejects_inflated_counters_and_empty_report_is_valid():
    empty = compare([])
    assert empty.sample_size == 0 and empty.cases == ()
    record = audit()
    report = compare([(label(record), record)])
    for changes in ({"matches": 2}, {"sample_size": 2}, {"resolved_incidents": 2}):
        with pytest.raises(ValidationError):
            QualityReport.model_validate(dict(report.model_dump(), **changes))
