"""Pydantic v2 domain contracts for AegisOps.

This module defines the immutable, strictly-validated data contracts used at
the boundary between untrusted model output and trusted application logic.
All models inherit :class:`aegisops.domain.common.DTO` and therefore forbid
unknown fields, are frozen, and revalidate instances.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from aegisops.domain.common import (
    DTO,
    Identifier,
    NonNegativeInt,
    ScalarMap,
    Sha256,
    ShortText,
    Text,
    UTCDateTime,
)

__all__ = [
    "ActionID",
    "ActionProposal",
    "AuditError",
    "AuditRecord",
    "AutonomyLevel",
    "BlockedCapability",
    "CauseCategory",
    "Confidence",
    "ContainerSnapshot",
    "ContextLimits",
    "Diagnosis",
    "DiagnosisContext",
    "DiagnosisDraft",
    "DiagnosisHypothesis",
    "DryRunResult",
    "Evidence",
    "EvidenceSource",
    "Incident",
    "IncidentType",
    "KubernetesEvent",
    "LLMMetadata",
    "Origin",
    "Owner",
    "PodSnapshot",
    "PolicyDecision",
    "PolicyReason",
    "Recommendation",
    "RecommendationDraft",
    "ReversibilityClass",
    "RunStatus",
    "Severity",
    "validate_diagnosis",
]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Origin(StrEnum):
    SYNTHETIC_FIXTURE = "synthetic_fixture"
    LOCAL_KIND = "local_kind"


class AutonomyLevel(StrEnum):
    L0 = "L0"


class ReversibilityClass(StrEnum):
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"


class IncidentType(StrEnum):
    CRASH_LOOP_BACK_OFF = "crash_loop_back_off"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceSource(StrEnum):
    POD_STATUS = "pod_status"
    K8S_EVENT = "k8s_event"
    CONTAINER_LOG = "container_log"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class CauseCategory(StrEnum):
    APPLICATION_EXIT_NONZERO = "application_exit_nonzero"
    MISSING_RUNTIME_CONFIG = "missing_runtime_config"
    INVALID_COMMAND = "invalid_command"
    OOM_KILLED = "oom_killed"
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BlockedCapability(StrEnum):
    K8S_WRITE = "k8s_write"
    SHELL_EXEC = "shell_exec"
    NETWORK_EGRESS = "network_egress"
    SECRET_READ = "secret_read"  # noqa: S105 - capability enum, not a credential
    DELETE_DATA = "delete_data"
    MODIFY_POLICY_OR_AUDIT = "modify_policy_or_audit"


class RunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class ActionID(StrEnum):
    ESCALATE_TO_HUMAN = "escalate_to_human"
    COLLECT_MORE_EVIDENCE = "collect_more_evidence"
    RESTART_WORKLOAD = "restart_workload"
    ROLLBACK_DEPLOYMENT = "rollback_deployment"


# ---------------------------------------------------------------------------
# Constrained scalar aliases
# ---------------------------------------------------------------------------

Name63 = Annotated[Identifier, Field(max_length=63)]
Name253 = Annotated[Identifier, Field(max_length=253)]
ExitCode = Annotated[StrictInt, Field(ge=0, le=255)]


# ---------------------------------------------------------------------------
# Snapshot models
# ---------------------------------------------------------------------------


class Owner(DTO):
    kind: Literal["Deployment", "ReplicaSet", "StatefulSet", "DaemonSet", "Job", "Pod"]
    name: Name253


class ContainerSnapshot(DTO):
    name: Name63
    is_init: StrictBool = False
    waiting_reason: ShortText | None = None
    last_terminated_reason: ShortText | None = None
    last_exit_code: ExitCode | None = None
    restart_count: NonNegativeInt = 0


class KubernetesEvent(DTO):
    reason: ShortText
    type: Literal["Normal", "Warning"]
    message: Text
    count: NonNegativeInt = 1
    last_seen: UTCDateTime


class PodSnapshot(DTO):
    snapshot_id: Identifier
    origin: Origin
    cluster_id: Identifier
    namespace: Literal["aegisops-demo"]
    pod_name: Name253
    owner: Owner
    containers: tuple[ContainerSnapshot, ...] = Field(min_length=1, max_length=32)
    events: tuple[KubernetesEvent, ...] = Field(default=(), max_length=64)
    log_tail: str | None = Field(default=None, max_length=16384)
    observed_at: UTCDateTime

    @field_validator("log_tail")
    @classmethod
    def _check_log_tail(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value.encode("utf-8")) > 16384:
            raise ValueError("log_tail exceeds maximum encoded size")
        if len(value.splitlines()) > 100:
            raise ValueError("log_tail exceeds maximum line count")
        return value

    @model_validator(mode="after")
    def _check_unique_containers(self) -> Self:
        names = [container.name for container in self.containers]
        if len(set(names)) != len(names):
            raise ValueError("container names must be unique")
        return self


# ---------------------------------------------------------------------------
# Evidence and incident models
# ---------------------------------------------------------------------------


class Evidence(DTO):
    evidence_id: Identifier
    source: EvidenceSource
    summary: Text
    observed_at: UTCDateTime
    attributes: ScalarMap = Field(default_factory=dict)


class Incident(DTO):
    incident_id: Identifier
    incident_type: IncidentType
    origin: Origin
    cluster_id: Identifier
    namespace: Literal["aegisops-demo"]
    workload_name: Name253
    pod_name: Name253
    container_name: Identifier
    severity: Severity
    observed_at: UTCDateTime
    evidence: tuple[Evidence, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _check_unique_evidence(self) -> Self:
        ids = [item.evidence_id for item in self.evidence]
        if len(set(ids)) != len(ids):
            raise ValueError("evidence ids must be unique")
        return self


# ---------------------------------------------------------------------------
# Diagnosis context
# ---------------------------------------------------------------------------


class ContextLimits(DTO):
    max_log_bytes: NonNegativeInt = Field(default=16384, ge=1, le=16384)
    max_log_lines: NonNegativeInt = Field(default=100, ge=1, le=100)
    max_events: NonNegativeInt = Field(default=64, ge=1, le=64)
    max_evidence: NonNegativeInt = Field(default=32, ge=1, le=32)


class DiagnosisContext(DTO):
    incident_id: Identifier
    context_hash: Sha256
    evidence: tuple[Evidence, ...] = Field(min_length=1, max_length=32)
    limits: ContextLimits = Field(default_factory=ContextLimits)

    @model_validator(mode="after")
    def _check_evidence(self) -> Self:
        ids = [item.evidence_id for item in self.evidence]
        if len(set(ids)) != len(ids):
            raise ValueError("evidence ids must be unique")
        if len(self.evidence) > self.limits.max_evidence:
            raise ValueError("evidence exceeds configured limit")
        return self


# ---------------------------------------------------------------------------
# Diagnosis models
# ---------------------------------------------------------------------------


class DiagnosisHypothesis(DTO):
    cause_category: CauseCategory
    cause: Text
    rationale: Text
    confidence: Confidence
    supporting_evidence_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _check_unique_refs(self) -> Self:
        refs = list(self.supporting_evidence_ids)
        if len(set(refs)) != len(refs):
            raise ValueError("supporting evidence ids must be unique")
        return self


class Diagnosis(DTO):
    diagnosis_id: Identifier
    incident_id: Identifier
    summary: Text
    hypotheses: tuple[DiagnosisHypothesis, ...] = Field(min_length=1, max_length=8)
    missing_evidence: tuple[ShortText, ...] = Field(default=(), max_length=16)


class DiagnosisDraft(DTO):
    summary: Text
    hypotheses: tuple[DiagnosisHypothesis, ...] = Field(min_length=1, max_length=8)
    missing_evidence: tuple[ShortText, ...] = Field(default=(), max_length=16)


def validate_diagnosis(data: object, incident: Incident) -> Diagnosis:
    """Validate untrusted diagnosis data against a trusted incident.

    Structural validation is performed by :class:`Diagnosis`; this helper
    additionally enforces that the diagnosis refers to the supplied incident
    and that every referenced evidence id exists in that incident.
    """
    incident = Incident.model_validate(incident)
    diagnosis = Diagnosis.model_validate(data)
    if diagnosis.incident_id != incident.incident_id:
        raise ValueError("diagnosis does not match incident")
    known = {item.evidence_id for item in incident.evidence}
    for hypothesis in diagnosis.hypotheses:
        for ref in hypothesis.supporting_evidence_ids:
            if ref not in known:
                raise ValueError("diagnosis references unknown evidence")
    return diagnosis


# ---------------------------------------------------------------------------
# Recommendation models
# ---------------------------------------------------------------------------


class ActionProposal(DTO):
    action_id: ActionID
    params: ScalarMap = Field(default_factory=dict)


class RecommendationDraft(DTO):
    title: ShortText
    rationale: Text
    proposed_action: ActionProposal
    explanation_steps: tuple[ShortText, ...] = Field(default=(), max_length=16)


class Recommendation(RecommendationDraft):
    recommendation_id: Identifier
    incident_id: Identifier
    diagnosis_id: Identifier
    reversibility_class: ReversibilityClass
    requires_human_approval: Literal[True] = True
    execution_performed: Literal[False] = False

    @field_validator("requires_human_approval", mode="before")
    @classmethod
    def _reject_numeric_true(cls, value: object) -> object:
        if isinstance(value, int) and not isinstance(value, bool):
            raise ValueError("requires_human_approval must be boolean true")
        return value


# ---------------------------------------------------------------------------
# Policy models
# ---------------------------------------------------------------------------


class PolicyReason(DTO):
    code: Identifier
    message: ShortText


class PolicyDecision(DTO):
    incident_id: Identifier
    recommendation_id: Identifier
    policy_version: Identifier
    input_hash: Sha256 | None = None
    autonomy_level: AutonomyLevel = AutonomyLevel.L0
    allowed_for_dry_run: StrictBool
    reasons: tuple[PolicyReason, ...] = Field(max_length=32)
    blocked_capabilities: tuple[BlockedCapability, ...] = Field(default=(), max_length=6)

    @model_validator(mode="after")
    def _check_denied_reasons(self) -> Self:
        if not self.allowed_for_dry_run and not self.reasons:
            raise ValueError("denied decision requires at least one reason")
        return self


class DryRunResult(DTO):
    incident_id: Identifier
    recommendation_id: Identifier
    simulated: Literal[True] = True
    execution_performed: Literal[False] = False
    summary: Text


# ---------------------------------------------------------------------------
# LLM metadata
# ---------------------------------------------------------------------------


class LLMMetadata(DTO):
    provider: Identifier
    model_name: Identifier
    prompt_id: Identifier
    prompt_hash: Sha256
    parameters: ScalarMap = Field(default_factory=dict)
    input_tokens: NonNegativeInt = 0
    output_tokens: NonNegativeInt = 0


# ---------------------------------------------------------------------------
# Audit models
# ---------------------------------------------------------------------------


class AuditError(DTO):
    code: Identifier
    message: ShortText


class AuditRecord(DTO):
    schema_version: Literal[1] = 1
    run_id: UUID
    started_at: UTCDateTime
    completed_at: UTCDateTime
    status: RunStatus
    autonomy_level: AutonomyLevel = AutonomyLevel.L0
    origin: Origin
    incident: Incident | None = None
    context: DiagnosisContext | None = None
    diagnosis: Diagnosis | None = None
    recommendation: Recommendation | None = None
    policy_decision: PolicyDecision | None = None
    dry_run_result: DryRunResult | None = None
    llm: LLMMetadata | None = None
    context_hash: Sha256 | None = None
    errors: tuple[AuditError, ...] = Field(default=(), max_length=16)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _version_integer(cls, value: object) -> int:
        if type(value) is not int:
            raise ValueError("schema version must be an integer")
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at precedes started_at")

        if self.incident is not None and self.incident.origin != self.origin:
            raise ValueError("incident origin does not match record origin")

        if self.context is not None and self.incident is None:
            raise ValueError("context requires incident")
        if self.diagnosis is not None and self.incident is None:
            raise ValueError("diagnosis requires incident")
        if self.recommendation is not None and self.diagnosis is None:
            raise ValueError("recommendation requires diagnosis")
        if self.policy_decision is not None and self.recommendation is None:
            raise ValueError("policy decision requires recommendation")
        if self.dry_run_result is not None and self.recommendation is None:
            raise ValueError("dry run result requires recommendation")

        incident_id = self.incident.incident_id if self.incident is not None else None

        if self.context is not None:
            if incident_id is not None and self.context.incident_id != incident_id:
                raise ValueError("context incident id mismatch")
            if self.context.context_hash != self.context_hash:
                raise ValueError("context hash mismatch")
            if self.incident is not None:
                known = {item.evidence_id for item in self.incident.evidence}
                for item in self.context.evidence:
                    if item.evidence_id not in known:
                        raise ValueError("context references unknown evidence")

        if self.diagnosis is not None:
            if incident_id is not None and self.diagnosis.incident_id != incident_id:
                raise ValueError("diagnosis incident id mismatch")
            if self.incident is not None:
                known = {item.evidence_id for item in self.incident.evidence}
                for hypothesis in self.diagnosis.hypotheses:
                    for ref in hypothesis.supporting_evidence_ids:
                        if ref not in known:
                            raise ValueError("diagnosis references unknown evidence")

        if self.recommendation is not None:
            if incident_id is not None and self.recommendation.incident_id != incident_id:
                raise ValueError("recommendation incident id mismatch")
            if (
                self.diagnosis is not None
                and self.recommendation.diagnosis_id != self.diagnosis.diagnosis_id
            ):
                raise ValueError("recommendation diagnosis id mismatch")

        if self.policy_decision is not None:
            if incident_id is not None and self.policy_decision.incident_id != incident_id:
                raise ValueError("policy decision incident id mismatch")
            if (
                self.recommendation is not None
                and self.policy_decision.recommendation_id != self.recommendation.recommendation_id
            ):
                raise ValueError("policy decision recommendation id mismatch")

        if self.dry_run_result is not None:
            if incident_id is not None and self.dry_run_result.incident_id != incident_id:
                raise ValueError("dry run incident id mismatch")
            if (
                self.recommendation is not None
                and self.dry_run_result.recommendation_id != self.recommendation.recommendation_id
            ):
                raise ValueError("dry run recommendation id mismatch")

        if self.status is RunStatus.COMPLETED:
            if self.errors:
                raise ValueError("completed record must not contain errors")
            required = (
                self.incident,
                self.context,
                self.diagnosis,
                self.recommendation,
                self.policy_decision,
                self.dry_run_result,
            )
            if any(item is None for item in required):
                raise ValueError("completed record is missing required pieces")
            if self.policy_decision is not None and not self.policy_decision.allowed_for_dry_run:
                raise ValueError("completed record requires allowed decision")

        if self.status is RunStatus.FAILED and not self.errors:
            raise ValueError("failed record requires at least one error")

        if self.status is RunStatus.BLOCKED:
            if self.policy_decision is not None and self.policy_decision.allowed_for_dry_run:
                raise ValueError("blocked record requires denied decision")

        if self.status in (RunStatus.BLOCKED, RunStatus.FAILED) and self.dry_run_result is not None:
            raise ValueError("blocked or failed record must not contain dry run result")

        return self
