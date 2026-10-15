"""One processing attempt per signed batch; ambiguous claims are never retried."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from aegisops.audit.base import AuditStore
from aegisops.authorization.pep import DryRunPEP
from aegisops.domain.common import utc_now
from aegisops.domain.models import AuditRecord, Origin
from aegisops.domain.telemetry import TelemetryProvenance
from aegisops.llm.engine import AnalysisEngine
from aegisops.pipeline.models import PipelineResult
from aegisops.pipeline.processing import BatchProcessor
from aegisops.telemetry.contracts import (
    DeliveryResult,
    IngestionReceipt,
    SignedTelemetryBatch,
    TelemetryError,
)
from aegisops.telemetry.files import load_trust, read_object, safe_path, write_new
from aegisops.telemetry.verification import require_fresh, verify_packet


class RecordReader(Protocol):
    def show(self, run_id: UUID) -> AuditRecord: ...


class IngestionService:
    def __init__(
        self,
        root: Path,
        analysis: AnalysisEngine,
        store: AuditStore,
        reader: RecordReader,
        *,
        pep: DryRunPEP,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.root, self.analysis, self.store, self.reader = root, analysis, store, reader
        self.pep, self.clock = pep, clock

    def _readback(
        self, receipt: IngestionReceipt, provenance: TelemetryProvenance, snapshot_count: int
    ) -> tuple[AuditRecord, ...]:
        records = tuple(self.reader.show(identifier) for identifier in receipt.audit_run_ids)
        if (
            receipt.telemetry != provenance
            or receipt.snapshot_count != snapshot_count
            or any(record.telemetry != provenance for record in records)
        ):
            raise TelemetryError()
        PipelineResult(
            origin=Origin.LOCAL_KIND,
            snapshot_count=snapshot_count,
            incident_count=receipt.incident_count,
            records=records,
            status=receipt.status,
        )
        return records

    def consume(self, packet: SignedTelemetryBatch) -> DeliveryResult:
        try:
            packet = SignedTelemetryBatch.model_validate(packet)
            provenance = verify_packet(packet, load_trust(self.root))
            body = packet.body
            state = safe_path(self.root / ".local/control-plane/inbox")
            receipt_path = safe_path(state / f"{body.batch_id}.receipt.json")
            if receipt_path.exists():
                receipt = IngestionReceipt.model_validate(read_object(receipt_path, 16384))
                self._readback(receipt, provenance, len(body.snapshots))
                return DeliveryResult(receipt=receipt, replayed=True)
            require_fresh(body, self.clock())
            # Exclusive durable claim happens before analysis/audit. Never remove or take it over.
            write_new(state / f"{body.batch_id}.claim.json", provenance.model_dump_json().encode())
            result = BatchProcessor(
                self.analysis, self.store, pep=self.pep, telemetry=provenance
            ).process(list(body.snapshots), origin=Origin.LOCAL_KIND)
            receipt = IngestionReceipt(
                telemetry=provenance,
                processed_at=self.clock(),
                snapshot_count=result.snapshot_count,
                incident_count=result.incident_count,
                audit_run_ids=tuple(record.run_id for record in result.records),
                status=result.status,
            )
            # No successful receipt for lost/corrupt/partially committed audit.
            if self._readback(receipt, provenance, len(body.snapshots)) != result.records:
                raise TelemetryError()
            write_new(receipt_path, receipt.model_dump_json().encode())
            return DeliveryResult(receipt=receipt, replayed=False)
        except Exception:
            raise TelemetryError() from None
