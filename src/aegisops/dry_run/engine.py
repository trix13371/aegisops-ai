"""Descriptive dry-run engine for AegisOps prototype-1.

Simulation validates DTOs and recomputes policy without network, database
or command execution. Construction may load the fixed packaged catalog.
The result is a Russian description with no executor or operational effect.
"""

from __future__ import annotations

from typing import Final

from aegisops.actions.catalog import ActionCatalog
from aegisops.domain.models import (
    ActionID,
    DryRunResult,
    Incident,
    PolicyDecision,
    Recommendation,
)
from aegisops.policy.checker import PolicyChecker, PolicyError

__all__ = ["DryRunEngine", "DryRunError"]

_FIXED_ERROR: Final[str] = "dry run simulation failed"

_SUFFIX: Final[str] = " действие не выполнялось; эффект и предусловия не проверялись."

_ESCALATE_SUMMARY: Final[str] = (
    "Описание: эскалация к человеку для пространства имён {namespace} "
    "и рабочей нагрузки {workload};"
)

_COLLECT_SUMMARY: Final[str] = (
    "Описание: сбор дополнительных данных в области {scope} "
    "для пространства имён {namespace} и рабочей нагрузки {workload};"
)

_RESTART_SUMMARY: Final[str] = (
    "Описание: перезапуск рабочей нагрузки {workload} в пространстве имён {namespace};"
)

_ROLLBACK_SUMMARY: Final[str] = (
    "Описание: откат развёртывания {deployment} в пространстве имён {namespace};"
)


class DryRunError(ValueError):
    """Raised for any invalid dry-run input. Message is fixed."""

    def __init__(self) -> None:
        super().__init__(_FIXED_ERROR)


class DryRunEngine:
    """Descriptive dry-run engine with no executor and no side effects."""

    def __init__(self, catalog: ActionCatalog | None = None) -> None:
        try:
            self._checker = PolicyChecker(catalog)
        except PolicyError:
            raise DryRunError() from None
        except Exception:
            raise DryRunError() from None

    def simulate(
        self,
        incident: Incident,
        recommendation: Recommendation,
        decision: PolicyDecision,
    ) -> DryRunResult:
        """Produce a static descriptive summary for an allowed decision."""
        try:
            incident = Incident.model_validate(incident)
            recommendation = Recommendation.model_validate(recommendation)
            decision = PolicyDecision.model_validate(decision)
        except Exception:
            raise DryRunError() from None

        try:
            current = self._checker.evaluate(incident, recommendation)
        except PolicyError:
            raise DryRunError() from None
        except Exception:
            raise DryRunError() from None

        if current != decision:
            raise DryRunError()

        if not decision.allowed_for_dry_run:
            raise DryRunError()

        if decision.incident_id != incident.incident_id:
            raise DryRunError()

        if decision.recommendation_id != recommendation.recommendation_id:
            raise DryRunError()

        action_id = recommendation.proposed_action.action_id
        params = recommendation.proposed_action.params

        if action_id is ActionID.ESCALATE_TO_HUMAN:
            body = _ESCALATE_SUMMARY.format(
                namespace=incident.namespace,
                workload=incident.workload_name,
            )
        elif action_id is ActionID.COLLECT_MORE_EVIDENCE:
            scope = params.get("scope")
            if not isinstance(scope, str):
                raise DryRunError()
            body = _COLLECT_SUMMARY.format(
                scope=scope,
                namespace=incident.namespace,
                workload=incident.workload_name,
            )
        elif action_id is ActionID.RESTART_WORKLOAD:
            workload = params.get("workload")
            if not isinstance(workload, str):
                raise DryRunError()
            body = _RESTART_SUMMARY.format(
                workload=workload,
                namespace=incident.namespace,
            )
        elif action_id is ActionID.ROLLBACK_DEPLOYMENT:
            deployment = params.get("deployment")
            if not isinstance(deployment, str):
                raise DryRunError()
            body = _ROLLBACK_SUMMARY.format(
                deployment=deployment,
                namespace=incident.namespace,
            )
        else:
            raise DryRunError()

        summary = f"{body}{_SUFFIX}"

        try:
            return DryRunResult(
                incident_id=incident.incident_id,
                recommendation_id=recommendation.recommendation_id,
                summary=summary,
            )
        except Exception:
            raise DryRunError() from None
