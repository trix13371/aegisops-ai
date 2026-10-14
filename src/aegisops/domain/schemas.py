"""Stable schema registry: custom relational validators remain runtime checks."""

from typing import Any

from aegisops.actions.catalog import ActionCatalog, ActionDefinition, ParameterRule
from aegisops.domain.common import DTO
from aegisops.domain.models import (
    ActionProposal,
    AuditError,
    AuditRecord,
    ContainerSnapshot,
    ContextLimits,
    Diagnosis,
    DiagnosisContext,
    DiagnosisDraft,
    DiagnosisHypothesis,
    DryRunResult,
    Evidence,
    Incident,
    KubernetesEvent,
    LLMMetadata,
    Owner,
    PodSnapshot,
    PolicyDecision,
    PolicyReason,
    Recommendation,
    RecommendationDraft,
)
from aegisops.sources.synthetic import ScenarioSpec

CONTRACTS: tuple[type[DTO], ...] = (
    Owner,
    ContainerSnapshot,
    KubernetesEvent,
    PodSnapshot,
    Evidence,
    Incident,
    ContextLimits,
    DiagnosisContext,
    DiagnosisHypothesis,
    Diagnosis,
    DiagnosisDraft,
    ActionProposal,
    RecommendationDraft,
    Recommendation,
    PolicyReason,
    PolicyDecision,
    DryRunResult,
    LLMMetadata,
    AuditError,
    AuditRecord,
    ParameterRule,
    ActionDefinition,
    ActionCatalog,
    ScenarioSpec,
)


def contract_schemas() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for model in CONTRACTS:
        schema = model.model_json_schema(mode="validation")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://aegisops.invalid/schemas/v1/{model.__name__}.json"
        result[model.__name__] = schema
    return result
