"""Application-owned identifiers and safety fields; no model authority."""

from pydantic import ValidationError

from aegisops.actions.catalog import ActionCatalog, CatalogError
from aegisops.domain.models import (
    Diagnosis,
    DiagnosisDraft,
    Incident,
    Recommendation,
    RecommendationDraft,
    validate_diagnosis,
)


class ContractError(ValueError):
    """Boundary diagnostics contain no raw model output."""


def bind_diagnosis(data: object, incident: Incident, diagnosis_id: str) -> Diagnosis:
    """Bind a validated draft to explicit application-owned identifiers."""
    try:
        trusted_incident = Incident.model_validate(incident)
        draft = DiagnosisDraft.model_validate(data)
        payload = draft.model_dump(mode="json")
        payload.update(diagnosis_id=diagnosis_id, incident_id=trusted_incident.incident_id)
        return validate_diagnosis(payload, trusted_incident)
    except (ValidationError, ValueError):
        raise ContractError("diagnosis draft violates contract") from None


def bind_recommendation(
    data: object,
    incident: Incident,
    diagnosis: Diagnosis,
    catalog: ActionCatalog,
    recommendation_id: str,
) -> Recommendation:
    """Set reversibility from catalog; never accept safety fields from LLM."""
    try:
        trusted_incident = Incident.model_validate(incident)
        trusted_diagnosis = validate_diagnosis(diagnosis, trusted_incident)
        trusted_catalog = ActionCatalog.model_validate(catalog)
        draft = RecommendationDraft.model_validate(data)
        proposal = trusted_catalog.validate_proposal(draft.proposed_action)
        definition = next(item for item in trusted_catalog.actions if item.id == proposal.action_id)
        payload = draft.model_dump(mode="json")
        payload.update(
            proposed_action=proposal.model_dump(mode="json"),
            recommendation_id=recommendation_id,
            incident_id=trusted_incident.incident_id,
            diagnosis_id=trusted_diagnosis.diagnosis_id,
            reversibility_class=definition.reversibility.value,
            requires_human_approval=True,
            execution_performed=False,
        )
        return Recommendation.model_validate(payload)
    except (ValidationError, ValueError, CatalogError, StopIteration):
        raise ContractError("recommendation draft violates contract") from None
