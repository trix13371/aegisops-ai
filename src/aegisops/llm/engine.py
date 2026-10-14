"""S0-06 analysis engine: bounded LLM calls with deterministic rule fallback."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from aegisops.actions.catalog import ActionCatalog, CatalogError, load_catalog
from aegisops.context.sanitizer import sanitize_data, sanitize_text
from aegisops.domain.binding import (
    ContractError,
    bind_diagnosis,
    bind_recommendation,
)
from aegisops.domain.common import Scalar
from aegisops.domain.models import (
    Diagnosis,
    DiagnosisContext,
    DiagnosisDraft,
    Incident,
    LLMMetadata,
    Recommendation,
    RecommendationDraft,
)
from aegisops.llm.base import (
    AnalysisDraft,
    LLMClient,
    LLMError,
    LLMLimits,
    LLMReply,
    LLMRequest,
    RunBudget,
)
from aegisops.llm.fallback import RuleBasedRecommender, unknown_diagnosis
from aegisops.llm.prompt import build_request

__all__ = ["AnalysisEngine", "AnalysisResult"]

_MAX_METADATA_NAME = 128


def _safe_metadata_name(value: object) -> str:
    if type(value) is not str:
        raise LLMError()
    if not 1 <= len(value) <= _MAX_METADATA_NAME or not value.strip():
        raise LLMError()
    if sanitize_text(value) != value:
        raise LLMError()
    return value


def _digest(*parts: str) -> str:
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode("utf-8"))
        hasher.update(b"\x1f")
    return hasher.hexdigest()


@dataclass(frozen=True, repr=False)
class AnalysisResult:
    diagnosis: Diagnosis
    recommendation: Recommendation
    metadata: LLMMetadata
    used_fallback: bool
    fallback_reason: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnosis", Diagnosis.model_validate(self.diagnosis))
        object.__setattr__(
            self, "recommendation", Recommendation.model_validate(self.recommendation)
        )
        object.__setattr__(self, "metadata", LLMMetadata.model_validate(self.metadata))
        if type(self.used_fallback) is not bool:
            raise LLMError()
        if self.fallback_reason is not None and type(self.fallback_reason) is not str:
            raise LLMError()


class AnalysisEngine:
    """Two-call-maximum analysis with deterministic fallback on any failure."""

    def __init__(
        self,
        client: LLMClient,
        *,
        catalog: ActionCatalog | None = None,
        limits: LLMLimits | None = None,
        budget: RunBudget | None = None,
    ) -> None:
        try:
            self._initialize(client, catalog, limits, budget)
        except Exception:
            raise LLMError() from None

    def _initialize(
        self,
        client: LLMClient,
        catalog: ActionCatalog | None,
        limits: LLMLimits | None,
        budget: RunBudget | None,
    ) -> None:
        self._client = client
        self._catalog = (
            ActionCatalog.model_validate(catalog) if catalog is not None else load_catalog()
        )
        self._limits = limits if limits is not None else (budget.limits if budget else LLMLimits())
        if type(self._limits) is not LLMLimits:
            raise LLMError()
        self._limits.__post_init__()
        self._budget = budget if budget is not None else RunBudget(self._limits)
        if self._budget.limits != self._limits:
            raise LLMError()
        self._provider = _safe_metadata_name(client.provider)
        self._model_name = _safe_metadata_name(client.model_name)
        self._recommender = RuleBasedRecommender()

    def analyze(self, incident: Incident, context: DiagnosisContext) -> AnalysisResult:
        try:
            trusted_incident = Incident.model_validate(incident)
            trusted_context = DiagnosisContext.model_validate(context)
        except (ValueError, TypeError):
            raise LLMError() from None

        try:
            request = build_request(trusted_incident, trusted_context, self._catalog)
        except (ValueError, TypeError):
            raise LLMError() from None

        context_hash = trusted_context.context_hash
        catalog_version = self._catalog.catalog_version
        known_evidence = {item.evidence_id for item in trusted_context.evidence}

        calls = 0
        reserved_before = self._budget.reserved_tokens
        input_tokens = 0
        output_tokens = 0
        usage_complete = True
        returned_model: str | None = None
        last_valid_diagnosis: Diagnosis | None = None
        fallback_reason: str | None = None

        for attempt in range(2):
            try:
                self._budget.reserve(request)
            except LLMError:
                fallback_reason = "budget_exhausted"
                break
            calls += 1
            try:
                reply = self._client.generate_structured(request, self._limits)
            except LLMError:
                usage_complete = False
                fallback_reason = "provider_error"
                continue
            except Exception:
                usage_complete = False
                fallback_reason = "provider_error"
                continue

            try:
                if type(reply) is not LLMReply:
                    raise LLMError()
                reply.__post_init__()
                trusted_reply = reply
            except (ValueError, TypeError):
                usage_complete = False
                fallback_reason = "invalid_reply"
                continue

            input_tokens += trusted_reply.input_tokens
            output_tokens += trusted_reply.output_tokens
            if (
                trusted_reply.output_tokens > self._limits.max_output_tokens
                or trusted_reply.input_tokens > request.input_bytes + 128
            ):
                fallback_reason = "invalid_reply"
                continue
            if trusted_reply.response_model is not None:
                try:
                    returned_model = _safe_metadata_name(trusted_reply.response_model)
                except LLMError:
                    returned_model = None

            try:
                draft = AnalysisDraft.model_validate(trusted_reply.data)
                sanitized = sanitize_data(draft.model_dump(mode="json"))
                clean = AnalysisDraft.model_validate(sanitized)
            except (ValueError, TypeError):
                fallback_reason = "invalid_reply"
                continue

            diagnosis = self._bind_diagnosis(
                clean.diagnosis, trusted_incident, known_evidence, context_hash, catalog_version
            )
            if diagnosis is None:
                fallback_reason = "invalid_reply"
                continue
            last_valid_diagnosis = diagnosis

            recommendation = self._bind_recommendation(
                clean.recommendation,
                trusted_incident,
                diagnosis,
                context_hash,
                catalog_version,
            )
            if recommendation is None:
                fallback_reason = "invalid_reply"
                continue

            metadata = self._metadata(
                request=request,
                context_hash=context_hash,
                calls=calls,
                reserved_before=reserved_before,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                usage_complete=usage_complete,
                returned_model=returned_model,
            )
            return AnalysisResult(
                diagnosis=diagnosis,
                recommendation=recommendation,
                metadata=metadata,
                used_fallback=False,
                fallback_reason=None,
            )

        if fallback_reason is None:
            fallback_reason = "invalid_reply"

        diagnosis = last_valid_diagnosis
        if diagnosis is None:
            diagnosis = unknown_diagnosis(trusted_incident, trusted_context)

        recommendation = self._recommender.recommend(trusted_incident, diagnosis, self._catalog)
        metadata = self._metadata(
            request=request,
            context_hash=context_hash,
            calls=calls,
            reserved_before=reserved_before,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_complete=usage_complete,
            returned_model=returned_model,
        )
        return AnalysisResult(
            diagnosis=diagnosis,
            recommendation=recommendation,
            metadata=metadata,
            used_fallback=True,
            fallback_reason=fallback_reason,
        )

    def _bind_diagnosis(
        self,
        draft: DiagnosisDraft,
        incident: Incident,
        known_evidence: set[str],
        context_hash: str,
        catalog_version: str,
    ) -> Diagnosis | None:
        for hypothesis in draft.hypotheses:
            for ref in hypothesis.supporting_evidence_ids:
                if ref not in known_evidence:
                    return None
        diagnosis_id = "diagnosis-" + _digest(
            context_hash, _canonical(draft.model_dump(mode="json")), catalog_version
        )
        try:
            return bind_diagnosis(draft, incident, diagnosis_id)
        except (ContractError, ValueError, TypeError):
            return None

    def _bind_recommendation(
        self,
        draft: RecommendationDraft,
        incident: Incident,
        diagnosis: Diagnosis,
        context_hash: str,
        catalog_version: str,
    ) -> Recommendation | None:
        try:
            payload = RecommendationDraft.model_validate(draft).model_dump(mode="json")
        except AttributeError:
            return None
        action = payload.get("proposed_action")
        if not isinstance(action, dict):
            return None
        action_id = action.get("action_id")
        params = action.get("params")
        if not isinstance(params, dict):
            return None
        if not self._target_ok(action_id, params, incident):
            return None
        recommendation_id = "recommendation-" + _digest(
            context_hash, _canonical(payload), catalog_version, diagnosis.diagnosis_id
        )
        try:
            return bind_recommendation(draft, incident, diagnosis, self._catalog, recommendation_id)
        except (ContractError, CatalogError, ValueError, TypeError):
            return None

    @staticmethod
    def _target_ok(action_id: object, params: dict[str, object], incident: Incident) -> bool:
        if action_id == "restart_workload":
            return (
                params.get("workload") == incident.workload_name
                and params.get("namespace") == incident.namespace
            )
        if action_id == "rollback_deployment":
            if params.get("deployment") != incident.workload_name:
                return False
            if params.get("namespace") != incident.namespace:
                return False
            return _owner_is_deployment(incident)
        return True

    def _metadata(
        self,
        *,
        request: LLMRequest,
        context_hash: str,
        calls: int,
        reserved_before: int,
        input_tokens: int,
        output_tokens: int,
        usage_complete: bool,
        returned_model: str | None,
    ) -> LLMMetadata:
        parameters: dict[str, Scalar] = {
            "temperature": 0,
            "max_tokens": self._limits.max_output_tokens,
            "context_hash": context_hash,
            "calls_this_incident": calls,
            "reserved_tokens": self._budget.reserved_tokens - reserved_before,
            "usage_complete": usage_complete,
        }
        if returned_model is not None:
            parameters["returned_model"] = returned_model
        return LLMMetadata(
            provider=self._provider,
            model_name=self._model_name,
            prompt_id=request.prompt_id,
            prompt_hash=request.prompt_hash,
            parameters=parameters,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def _owner_is_deployment(incident: Incident) -> bool:
    for evidence in incident.evidence:
        if evidence.source.value != "pod_status":
            continue
        owner = evidence.attributes.get("owner_kind")
        if owner == "Deployment":
            return True
    return False


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
