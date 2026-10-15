"""Stable schema registry: custom relational validators remain runtime checks."""

from typing import Any

from aegisops.actions.catalog import ActionCatalog, ActionDefinition, ParameterRule
from aegisops.audit.reader import AuditEntry
from aegisops.domain.authorization import AuthorizationControl, OpaInput, OpaResult, PepReceipt
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
    NoIncidentObservation,
    Owner,
    PodSnapshot,
    PolicyDecision,
    PolicyReason,
    Recommendation,
    RecommendationDraft,
)
from aegisops.domain.policy_release import (
    PolicyReleaseBody,
    PolicyReleaseCheck,
    PolicyReleaseState,
    SignedPolicyRelease,
)
from aegisops.domain.telemetry import TelemetryProvenance
from aegisops.evaluation.suite import EvaluationReport
from aegisops.feedback.models import FeedbackRecord, FeedbackSummary
from aegisops.feedback.quality_models import QualityCase, QualityReport
from aegisops.integrity.models import (
    ChainLink,
    CheckpointBody,
    CheckpointHead,
    RowDigest,
    SignedCheckpoint,
    TrustAnchor,
    VerificationReport,
)
from aegisops.llm.base import AnalysisDraft
from aegisops.pipeline.models import PipelineResult
from aegisops.runbooks.models import RunbookCatalog, RunbookDocument, RunbookSelection
from aegisops.sources.synthetic import ScenarioSpec
from aegisops.telemetry.contracts import (
    CollectorTrust,
    DeliveryResult,
    IngestionReceipt,
    SignedTelemetryBatch,
    TelemetryBatch,
)

CONTRACTS: tuple[type[DTO], ...] = (
    TelemetryProvenance,
    CollectorTrust,
    TelemetryBatch,
    SignedTelemetryBatch,
    IngestionReceipt,
    DeliveryResult,
    PolicyReleaseBody,
    SignedPolicyRelease,
    PolicyReleaseState,
    PolicyReleaseCheck,
    RowDigest,
    ChainLink,
    CheckpointBody,
    SignedCheckpoint,
    TrustAnchor,
    CheckpointHead,
    VerificationReport,
    AuthorizationControl,
    OpaInput,
    OpaResult,
    PepReceipt,
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
    AnalysisDraft,
    NoIncidentObservation,
    PipelineResult,
    EvaluationReport,
    AuditEntry,
    FeedbackRecord,
    FeedbackSummary,
    QualityCase,
    QualityReport,
    RunbookDocument,
    RunbookCatalog,
    RunbookSelection,
)


def contract_schemas() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for model in CONTRACTS:
        schema = model.model_json_schema(mode="validation")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://aegisops.invalid/schemas/v1/{model.__name__}.json"
        result[model.__name__] = schema
    return result
