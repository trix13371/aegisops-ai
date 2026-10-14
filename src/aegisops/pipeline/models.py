"""Bounded batch outcome; every outcome references committed audit records."""

from typing import Literal, Self

from pydantic import Field, model_validator

from aegisops.domain.common import DTO
from aegisops.domain.models import AuditRecord, Origin, RunStatus


class PipelineResult(DTO):
    origin: Origin
    snapshot_count: int = Field(strict=True, ge=0, le=200)
    incident_count: int = Field(strict=True, ge=0, le=200)
    records: tuple[AuditRecord, ...] = Field(min_length=1, max_length=200)
    status: RunStatus
    execution_performed: Literal[False] = False

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if any(record.origin is not self.origin for record in self.records):
            raise ValueError("record origin mismatch")
        count = sum(record.incident is not None for record in self.records)
        if count != self.incident_count:
            raise ValueError("incident count mismatch")
        if count == 0 and len(self.records) != 1:
            raise ValueError("scan requires one audit record")
        if count and count != len(self.records):
            raise ValueError("mixed scan and incident records")
        for record in self.records:
            if record.observation and record.observation.snapshot_count != self.snapshot_count:
                raise ValueError("observation count mismatch")
        statuses = {record.status for record in self.records}
        expected = (
            RunStatus.FAILED
            if RunStatus.FAILED in statuses
            else RunStatus.BLOCKED
            if RunStatus.BLOCKED in statuses
            else RunStatus.COMPLETED
        )
        if self.status is not expected:
            raise ValueError("aggregate status mismatch")
        return self
