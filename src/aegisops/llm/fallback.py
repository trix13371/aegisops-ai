"""Deterministic rule-based recommendation fallback; no model authority."""

from __future__ import annotations

import hashlib

from aegisops.actions.catalog import ActionCatalog
from aegisops.context.sanitizer import sanitize_text
from aegisops.domain.binding import bind_diagnosis, bind_recommendation
from aegisops.domain.common import Scalar
from aegisops.domain.models import (
    ActionProposal,
    CauseCategory,
    Confidence,
    Diagnosis,
    DiagnosisContext,
    DiagnosisDraft,
    DiagnosisHypothesis,
    Incident,
    Recommendation,
    RecommendationDraft,
)

__all__ = ["RuleBasedRecommender", "unknown_diagnosis"]

_EXPLANATION = "Рекомендация сформирована детерминированным правилом; действие не выполнялось."


def _digest(*parts: str) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode("utf-8"))
        hasher.update(b"\x1f")
    return hasher.hexdigest()


def unknown_diagnosis(incident: Incident, context: DiagnosisContext) -> Diagnosis:
    """Construct a conservative unknown-cause diagnosis from trusted context."""
    trusted_incident = Incident.model_validate(incident)
    trusted_context = DiagnosisContext.model_validate(context)
    first_evidence = trusted_context.evidence[0].evidence_id
    diagnosis_id = "diagnosis-" + _digest(trusted_context.context_hash, "rule-based-diagnosis-v1")
    draft = DiagnosisDraft(
        summary="Причина не установлена.",
        hypotheses=(
            DiagnosisHypothesis(
                cause_category=CauseCategory.UNKNOWN,
                cause="Неизвестная причина",
                rationale="Проверенного ответа модели нет; причина требует проверки.",
                confidence=Confidence.LOW,
                supporting_evidence_ids=(first_evidence,),
            ),
        ),
        missing_evidence=("Требуется дополнительная проверка оператором",),
    )
    return bind_diagnosis(draft, trusted_incident, diagnosis_id)


class RuleBasedRecommender:
    """Map a diagnosis cause category to a safe, non-destructive action."""

    def recommend(
        self, incident: Incident, diagnosis: Diagnosis, catalog: ActionCatalog
    ) -> Recommendation:
        trusted_incident = Incident.model_validate(incident)
        trusted_catalog = ActionCatalog.model_validate(catalog)
        trusted_diagnosis = Diagnosis.model_validate(diagnosis)

        category = self._category(trusted_diagnosis)
        action_id, params = self._action(category)

        title = sanitize_text("Безопасное действие")
        rationale = sanitize_text(_EXPLANATION)
        draft = RecommendationDraft(
            title=title,
            rationale=rationale,
            proposed_action=ActionProposal.model_validate(
                {"action_id": action_id, "params": params}
            ),
            explanation_steps=(sanitize_text(_EXPLANATION),),
        )
        recommendation_id = "recommendation-" + _digest(
            trusted_diagnosis.diagnosis_id,
            str(category),
            trusted_catalog.catalog_version,
            trusted_incident.incident_id,
        )
        return bind_recommendation(
            draft, trusted_incident, trusted_diagnosis, trusted_catalog, recommendation_id
        )

    @staticmethod
    def _category(diagnosis: Diagnosis) -> CauseCategory:
        categories = {hypothesis.cause_category for hypothesis in diagnosis.hypotheses}
        if len(categories) != 1:
            return CauseCategory.UNKNOWN
        return next(iter(categories))

    @staticmethod
    def _action(category: CauseCategory) -> tuple[str, dict[str, Scalar]]:
        if category is CauseCategory.APPLICATION_EXIT_NONZERO:
            return "collect_more_evidence", {"scope": "previous_logs"}
        if category is CauseCategory.OOM_KILLED:
            return "collect_more_evidence", {"scope": "events"}
        return "escalate_to_human", {}
