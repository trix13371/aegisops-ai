"""Collect, detect, review and persist; never mask a storage failure."""

from collections.abc import Callable
from datetime import datetime
from uuid import UUID, uuid4

from aegisops.audit.base import AuditStore
from aegisops.audit.validation import prepare_record
from aegisops.detection.engine import DetectionEngine
from aegisops.domain.common import utc_now
from aegisops.domain.models import (
    AuditError,
    AuditRecord,
    NoIncidentObservation,
    Origin,
    PodSnapshot,
    RunStatus,
)
from aegisops.llm.engine import AnalysisEngine
from aegisops.pipeline.models import PipelineResult
from aegisops.review.service import ReviewService
from aegisops.sources.base import ObservationSource
from aegisops.sources.env_guard import EnvironmentGuardError
from aegisops.sources.kubernetes_readonly import CollectionError
from aegisops.sources.safe_client import ReadOnlyError

_MESSAGE = {
    "environment_rejected": "Локальная среда не прошла проверку",
    "collection_failed": "Сбор наблюдений не завершён",
    "origin_mismatch": "Источник наблюдений не соответствует режиму",
    "detection_failed": "Обнаружение инцидентов не завершено",
}


class PipelineError(ValueError):
    def __init__(self) -> None:
        super().__init__("pipeline input rejected")


class Pipeline:
    def __init__(
        self,
        analysis: AnalysisEngine,
        store: AuditStore,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._analysis, self._store = analysis, store
        self._clock, self._id_factory = clock, id_factory

    def _scan_record(self, origin: Origin, count: int, code: str | None) -> PipelineResult:
        # Only actual scan/early-error records allocate an ID, not successful incident batches.
        try:
            status = (
                RunStatus.COMPLETED
                if code is None
                else RunStatus.BLOCKED
                if code in {"environment_rejected", "origin_mismatch"}
                else RunStatus.FAILED
            )
            record = AuditRecord(
                run_id=self._id_factory(),
                started_at=self._clock(),
                completed_at=self._clock(),
                status=status,
                origin=origin,
                observation=NoIncidentObservation(
                    source="synthetic" if origin is Origin.SYNTHETIC_FIXTURE else "local-kind",
                    snapshot_count=count,
                )
                if code is None
                else None,
                errors=() if code is None else (AuditError(code=code, message=_MESSAGE[code]),),
            )
        except Exception:
            raise PipelineError() from None
        clean = prepare_record(record)
        self._store.append(clean)
        return PipelineResult(
            origin=origin, snapshot_count=count, incident_count=0, records=(clean,), status=status
        )

    def run(self, source: ObservationSource, *, origin: Origin) -> PipelineResult:
        if type(origin) is not Origin:
            raise PipelineError()
        try:
            raw = source.collect()
        except (EnvironmentGuardError, ReadOnlyError, CollectionError):
            return self._scan_record(origin, 0, "environment_rejected")
        except Exception:
            return self._scan_record(origin, 0, "collection_failed")
        if type(raw) is not list or len(raw) > 200:
            return self._scan_record(origin, 0, "collection_failed")
        try:
            validated = [PodSnapshot.model_validate(item) for item in raw]
        except Exception:
            return self._scan_record(origin, 0, "collection_failed")
        # No storage calls inside collection/validation exception handlers' try blocks.
        if any(snapshot.origin is not origin for snapshot in validated):
            return self._scan_record(origin, 0, "origin_mismatch")
        count = len(validated)
        try:
            incidents = DetectionEngine().detect(validated)
        except Exception:
            return self._scan_record(origin, count, "detection_failed")
        if not incidents:
            return self._scan_record(origin, count, None)
        service = ReviewService(
            self._analysis, self._store, clock=self._clock, id_factory=self._id_factory
        )
        records = tuple(service.review(incident) for incident in incidents)
        statuses = {record.status for record in records}
        status = (
            RunStatus.FAILED
            if RunStatus.FAILED in statuses
            else RunStatus.BLOCKED
            if RunStatus.BLOCKED in statuses
            else RunStatus.COMPLETED
        )
        return PipelineResult(
            origin=origin,
            snapshot_count=count,
            incident_count=len(incidents),
            records=records,
            status=status,
        )
