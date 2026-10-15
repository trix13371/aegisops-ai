from __future__ import annotations

from aegisops.domain.models import AuditRecord, CauseCategory
from aegisops.feedback.models import FeedbackRecord, IncidentOutcome
from aegisops.feedback.quality_models import (
    ComparisonStatus,
    PredictionKind,
    QualityCase,
)


def _prediction_kind(audit: AuditRecord) -> PredictionKind:
    llm = audit.llm
    if llm is None:
        return PredictionKind.UNCLASSIFIED
    used_fallback = llm.parameters.get("used_fallback")
    if used_fallback is True:
        return PredictionKind.FALLBACK
    if used_fallback is False:
        if llm.provider == "fake":
            return PredictionKind.FIXTURE
        if llm.provider in {"local", "openrouter"}:
            return PredictionKind.MODEL
    return PredictionKind.UNCLASSIFIED


def _predicted_categories(audit: AuditRecord) -> tuple[CauseCategory, ...]:
    diagnosis = audit.diagnosis
    if diagnosis is None:
        return ()
    categories = {
        hypothesis.cause_category
        for hypothesis in diagnosis.hypotheses
        if hypothesis.cause_category is not None
    }
    return tuple(sorted(categories, key=lambda category: category.value))


def build_case(
    pairs: tuple[tuple[FeedbackRecord, AuditRecord], ...],
    *,
    incomplete: bool,
) -> QualityCase:
    selected = max(
        (audit for _, audit in pairs),
        key=lambda audit: (audit.started_at, str(audit.run_id)),
    )

    predicted_categories = _predicted_categories(selected)
    prediction_kind = _prediction_kind(selected)
    provider = selected.llm.provider if selected.llm is not None else None
    model_name = selected.llm.model_name if selected.llm is not None else None

    causes = {
        feedback.confirmed_cause for feedback, _ in pairs if feedback.confirmed_cause is not None
    }
    outcomes = {
        feedback.incident_outcome
        for feedback, _ in pairs
        if feedback.incident_outcome is not None
        and feedback.incident_outcome is not IncidentOutcome.UNKNOWN
    }

    confirmed_cause: CauseCategory | None = None
    incident_outcome: IncidentOutcome | None = None

    if incomplete:
        status = ComparisonStatus.SAMPLE_INCOMPLETE
    elif len(causes) > 1 or len(outcomes) > 1:
        status = ComparisonStatus.CONFLICT
    else:
        confirmed_cause = next(iter(causes), None)
        incident_outcome = next(iter(outcomes), None)
        if confirmed_cause is None:
            status = ComparisonStatus.UNLABELLED
        elif len(predicted_categories) != 1 or predicted_categories[0] is CauseCategory.UNKNOWN:
            status = ComparisonStatus.ABSTAINED
        elif predicted_categories[0] is confirmed_cause:
            status = ComparisonStatus.MATCH
        else:
            status = ComparisonStatus.MISMATCH

    feedback_ids = tuple(
        sorted(
            {feedback.feedback_id for feedback, _ in pairs},
            key=str,
        )
    )

    incident = selected.incident
    if incident is None:
        raise ValueError("incident required")
    return QualityCase(
        incident_id=incident.incident_id,
        run_id=selected.run_id,
        feedback_ids=feedback_ids,
        confirmed_cause=confirmed_cause,
        incident_outcome=incident_outcome,
        predicted_categories=predicted_categories,
        status=status,
        prediction_kind=prediction_kind,
        provider=provider,
        model_name=model_name,
    )
