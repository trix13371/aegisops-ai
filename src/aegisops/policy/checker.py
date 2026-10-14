"""Deterministic policy checker for AegisOps prototype-1.

Evaluation validates DTOs and returns a decision without network, database
or command execution. Construction may load the fixed packaged catalog.

All error messages are fixed constants; untrusted strings are never echoed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Final

from aegisops.actions.catalog import ActionCatalog, CatalogError, load_catalog
from aegisops.context.sanitizer import sanitize_data
from aegisops.domain.models import (
    ActionID,
    BlockedCapability,
    EvidenceSource,
    Incident,
    PolicyDecision,
    PolicyReason,
    Recommendation,
)

__all__ = ["POLICY_VERSION", "PolicyChecker", "PolicyError"]

POLICY_VERSION: Final[str] = "prototype-1"

_FIXED_ERROR: Final[str] = "policy evaluation failed"

_NAMESPACE: Final[str] = "aegisops-demo"

_BLOCKED_CAPABILITIES: Final[tuple[BlockedCapability, ...]] = (
    BlockedCapability.K8S_WRITE,
    BlockedCapability.SHELL_EXEC,
    BlockedCapability.NETWORK_EGRESS,
    BlockedCapability.SECRET_READ,
    BlockedCapability.DELETE_DATA,
    BlockedCapability.MODIFY_POLICY_OR_AUDIT,
)

_REASON_MESSAGES: Final[dict[str, str]] = {
    "namespace_denied": ("Пространство имён не допускается политикой."),
    "incident_mismatch": ("Рекомендация не соответствует инциденту."),
    "invalid_action": ("Предложенное действие недопустимо или неверно описано."),
    "reversibility_mismatch": ("Класс обратимости не совпадает каталогу."),
    "target_mismatch": ("Цель действия не совпадает с инцидентом."),
    "owner_not_deployment": ("Владелец пода не является Deployment."),
    "unsanitized_input": ("Входные данные не прошли очистку."),
    "descriptive_only": ("Допускается только описательное моделирование без выполнения."),
}


def _reason(code: str) -> PolicyReason:
    return PolicyReason(code=code, message=_REASON_MESSAGES[code])


class PolicyError(ValueError):
    """Raised for any invalid policy input. Message is fixed."""

    def __init__(self) -> None:
        super().__init__(_FIXED_ERROR)


def _load_default_catalog() -> ActionCatalog:
    try:
        return load_catalog()
    except CatalogError:
        raise PolicyError() from None
    except Exception:
        raise PolicyError() from None


class PolicyChecker:
    """Deterministic, default-deny policy checker.

    The catalog is revalidated at construction and on every evaluation. The
    checker never performs I/O beyond the initial fixed catalog load performed
    by the catalog loader.
    """

    def __init__(self, catalog: ActionCatalog | None = None) -> None:
        if catalog is None:
            catalog = _load_default_catalog()
        try:
            self._catalog = ActionCatalog.model_validate(catalog)
        except PolicyError:
            raise
        except Exception:
            raise PolicyError() from None

    def evaluate(
        self,
        incident: Incident,
        recommendation: Recommendation,
    ) -> PolicyDecision:
        """Evaluate a recommendation against the incident and catalog."""
        try:
            catalog = ActionCatalog.model_validate(self._catalog)
        except Exception:
            raise PolicyError() from None

        try:
            incident = Incident.model_validate(incident)
            recommendation = Recommendation.model_validate(recommendation)
        except Exception:
            raise PolicyError() from None

        try:
            proposal = catalog.validate_proposal(recommendation.proposed_action)
        except CatalogError:
            return self._denied(incident, recommendation, "invalid_action")
        except Exception:
            raise PolicyError() from None

        if incident.namespace != _NAMESPACE:
            return self._denied(incident, recommendation, "namespace_denied")

        if recommendation.incident_id != incident.incident_id:
            return self._denied(incident, recommendation, "incident_mismatch")

        try:
            data = {
                "incident": incident.model_dump(mode="json"),
                "recommendation": recommendation.model_dump(mode="json"),
            }
            if sanitize_data(data) != data:
                return self._denied(incident, recommendation, "unsanitized_input")
        except PolicyError:
            raise
        except Exception:
            raise PolicyError() from None

        definition = next(
            (item for item in catalog.actions if item.id == proposal.action_id),
            None,
        )
        if definition is None:
            return self._denied(incident, recommendation, "invalid_action")

        if definition.reversibility is not recommendation.reversibility_class:
            return self._denied(incident, recommendation, "reversibility_mismatch")

        action_id = proposal.action_id

        if action_id is ActionID.ESCALATE_TO_HUMAN:
            return self._allowed(incident, recommendation)

        if action_id is ActionID.COLLECT_MORE_EVIDENCE:
            return self._allowed(incident, recommendation)

        if action_id is ActionID.RESTART_WORKLOAD:
            params = proposal.params
            if params.get("namespace") != incident.namespace:
                return self._denied(incident, recommendation, "target_mismatch")
            if params.get("workload") != incident.workload_name:
                return self._denied(incident, recommendation, "target_mismatch")
            return self._allowed(incident, recommendation)

        if action_id is ActionID.ROLLBACK_DEPLOYMENT:
            params = proposal.params
            if params.get("namespace") != incident.namespace:
                return self._denied(incident, recommendation, "target_mismatch")
            if params.get("deployment") != incident.workload_name:
                return self._denied(incident, recommendation, "target_mismatch")
            owner_kinds = {
                item.attributes.get("owner_kind")
                for item in incident.evidence
                if item.source is EvidenceSource.POD_STATUS
            }
            if len(owner_kinds) != 1:
                return self._denied(incident, recommendation, "owner_not_deployment")
            if next(iter(owner_kinds)) != "Deployment":
                return self._denied(incident, recommendation, "owner_not_deployment")
            return self._allowed(incident, recommendation)

        return self._denied(incident, recommendation, "invalid_action")

    def _allowed(
        self,
        incident: Incident,
        recommendation: Recommendation,
    ) -> PolicyDecision:
        return PolicyDecision(
            incident_id=incident.incident_id,
            recommendation_id=recommendation.recommendation_id,
            policy_version=POLICY_VERSION,
            input_hash=self._input_hash(incident, recommendation),
            allowed_for_dry_run=True,
            reasons=(_reason("descriptive_only"),),
            blocked_capabilities=_BLOCKED_CAPABILITIES,
        )

    def _denied(
        self,
        incident: Incident,
        recommendation: Recommendation,
        code: str,
    ) -> PolicyDecision:
        return PolicyDecision(
            incident_id=incident.incident_id,
            recommendation_id=recommendation.recommendation_id,
            policy_version=POLICY_VERSION,
            input_hash=self._input_hash(incident, recommendation),
            allowed_for_dry_run=False,
            reasons=(_reason(code),),
            blocked_capabilities=_BLOCKED_CAPABILITIES,
        )

    def _input_hash(self, incident: Incident, recommendation: Recommendation) -> str:
        payload = {
            "incident": incident.model_dump(mode="json"),
            "recommendation": recommendation.model_dump(mode="json"),
            "catalog": self._catalog.model_dump(mode="json"),
            "policy_version": POLICY_VERSION,
        }
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
