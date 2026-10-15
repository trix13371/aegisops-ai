"""Review one detected incident; storage failure is always propagated."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

from aegisops.audit.base import AuditStore
from aegisops.audit.validation import prepare_record
from aegisops.authorization.pep import DryRunPEP
from aegisops.context.builder import ContextBuilder
from aegisops.domain.common import utc_now
from aegisops.domain.models import AuditError, AuditRecord, Incident, LLMMetadata, RunStatus
from aegisops.dry_run.engine import DryRunEngine
from aegisops.llm.engine import AnalysisEngine
from aegisops.policy.checker import PolicyChecker


class ReviewError(ValueError):
    def __init__(self) -> None:
        super().__init__("review input rejected")


class ReviewService:
    def __init__(
        self,
        analysis: AnalysisEngine,
        store: AuditStore,
        *,
        pep: DryRunPEP | None = None,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._analysis, self._store = analysis, store
        self._clock, self._id_factory = clock, id_factory
        self._pep = pep

    def review(self, incident: Incident) -> AuditRecord:
        try:
            incident = Incident.model_validate(incident)
            started_at, run_id = self._clock(), self._id_factory()
            # Validate injectable clock/ID before model work or storage.
            AuditRecord(
                run_id=run_id,
                started_at=started_at,
                completed_at=started_at,
                status=RunStatus.FAILED,
                origin=incident.origin,
                errors=(AuditError(code="preflight", message="Проверка входа"),),
            )
        except Exception:
            raise ReviewError() from None
        context = diagnosis = recommendation = decision = dry_run = llm = receipt = None
        errors: tuple[AuditError, ...] = ()
        status = RunStatus.FAILED
        try:
            context = ContextBuilder().build(incident)
            result = self._analysis.analyze(incident, context)
            diagnosis, recommendation = result.diagnosis, result.recommendation
            metadata = result.metadata.model_dump()
            metadata["parameters"] = {
                **metadata["parameters"],
                "used_fallback": result.used_fallback,
                "fallback_reason": result.fallback_reason,
            }
            llm = LLMMetadata.model_validate(metadata)
            decision = PolicyChecker().evaluate(incident, recommendation)
            if self._pep is not None:
                receipt = self._pep.authorize(incident, recommendation, decision)
            if decision.allowed_for_dry_run and (receipt is None or receipt.allowed_for_dry_run):
                dry_run = DryRunEngine().simulate(incident, recommendation, decision)
                status = RunStatus.COMPLETED
            else:
                status = RunStatus.BLOCKED
        except Exception:
            errors = (AuditError(code="review_failed", message="Обработка инцидента не завершена"),)
        try:
            record = AuditRecord(
                run_id=run_id,
                started_at=started_at,
                completed_at=self._clock(),
                status=status,
                origin=incident.origin,
                incident=incident,
                context=context,
                context_hash=context.context_hash if context else None,
                diagnosis=diagnosis,
                recommendation=recommendation,
                policy_decision=decision,
                pep_receipt=receipt,
                dry_run_result=dry_run,
                llm=llm,
                errors=errors,
            )
        except Exception:
            raise ReviewError() from None
        clean = prepare_record(record)
        self._store.append(clean)
        return clean
