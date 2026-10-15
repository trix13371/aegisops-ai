"""Human labels remain bounded data and never become execution permissions."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aegisops.domain.models import Recommendation
from aegisops.feedback.models import Assessment, FeedbackError, FeedbackRecord, FeedbackSummary
from aegisops.feedback.validation import recommendation_hash, sanitize_feedback

pytestmark = pytest.mark.unit


def seed(**changes):
    values = dict(
        feedback_id=uuid4(),
        run_id=uuid4(),
        origin="synthetic_fixture",
        recommendation_id="recommendation-example",
        recommendation_hash="a" * 64,
        reviewer="local-user",
        assessment="uncertain",
        created_at=datetime.now(UTC),
    )
    values.update(changes)
    return values


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": 1.0},
        {"assessment": "approved"},
        {"reviewer": ""},
        {"reviewer": "operator\n"},
        {"reviewer": "оператор"},
        {"reviewer": "x" * 65},
        {"comment": "x" * 2001},
        {"comment": 123},
        {"recommendation_hash": "unknown"},
        {"origin": "production"},
        {"created_at": datetime(2026, 10, 15)},
        {"execution_performed": True},
        {"requires_human_approval": False},
    ],
)
def test_invalid_feedback_is_rejected(changes):
    with pytest.raises(FeedbackError, match="^feedback operation failed$"):
        sanitize_feedback(seed(**changes))


def test_no_self_correction_or_incomplete_constructed_model():
    identifier = uuid4()
    with pytest.raises(FeedbackError):
        sanitize_feedback(seed(feedback_id=identifier, supersedes_id=identifier))
    with pytest.raises(FeedbackError):
        sanitize_feedback(FeedbackRecord.model_construct(comment="password=FAKE_CANARY"))


def test_comment_is_redacted_idempotently_and_identity_not_silently_changed():
    record = sanitize_feedback(seed(comment="Пароль: password=F02_FAKE_CANARY"))
    assert "F02_FAKE_CANARY" not in record.comment
    assert "[REDACTED]" in record.comment
    assert sanitize_feedback(record) == record
    with pytest.raises(FeedbackError):
        sanitize_feedback(seed(recommendation_id="password=F02_FAKE_CANARY"))
    with pytest.raises(ValidationError):
        record.assessment = Assessment.UNSAFE


@pytest.mark.parametrize(
    "changes",
    [
        {"sample_size": True},
        {"limit": 0},
        {"limit": 101},
        {"useful": -1},
        {"useful": True},
        {"sample_size": 1},
        {"has_more": True},
        {"sample_size": 2, "useful": 2, "limit": 1},
    ],
)
def test_summary_cannot_overstate_its_sample(changes):
    values = dict(
        origin="synthetic_fixture",
        limit=10,
        sample_size=0,
        has_more=False,
        useful=0,
        incorrect=0,
        unsafe=0,
        uncertain=0,
    )
    with pytest.raises(ValidationError):
        FeedbackSummary.model_validate(dict(values, **changes))


def test_hash_is_canonical_and_revalidates_before_dump():
    values = dict(
        title="Совет",
        rationale="Причина",
        proposed_action={"action_id": "escalate_to_human"},
        recommendation_id="rec-test",
        incident_id="incident-test",
        diagnosis_id="diagnosis-test",
        reversibility_class="R1",
        requires_human_approval=True,
        execution_performed=False,
    )
    recommendation = Recommendation.model_validate(values)
    reordered = Recommendation.model_validate(dict(reversed(list(values.items()))))
    assert recommendation_hash(recommendation) == recommendation_hash(reordered)
    changed = Recommendation.model_validate(dict(values, title="Другой совет"))
    assert recommendation_hash(changed) != recommendation_hash(recommendation)
    with pytest.raises(FeedbackError):
        recommendation_hash(Recommendation.model_construct(execution_performed=True))
