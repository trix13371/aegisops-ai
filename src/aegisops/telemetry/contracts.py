"""Signed internal observations, not execution requests or model instructions."""

import hashlib
from datetime import timedelta
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from aegisops.domain.common import DTO, Sha256, UTCDateTime
from aegisops.domain.models import Origin, PodSnapshot, RunStatus
from aegisops.domain.telemetry import TelemetryProvenance
from aegisops.integrity.models import Signature

MAX_PACKET_BYTES = 1024 * 1024
MAX_LIFETIME = timedelta(minutes=5)


class TelemetryError(ValueError):
    def __init__(self) -> None:
        super().__init__("local telemetry handoff rejected")


class CollectorTrust(DTO):
    format: Literal["aegisops-collector-v1"] = "aegisops-collector-v1"
    collector_id: Literal["local-kind-reader"] = "local-kind-reader"
    cluster_id: Literal["kind-aegisops-demo"] = "kind-aegisops-demo"
    registration_id: UUID
    public_key: Sha256
    key_id: Sha256

    @model_validator(mode="after")
    def identity(self) -> Self:
        if hashlib.sha256(bytes.fromhex(self.public_key)).hexdigest() != self.key_id:
            raise TelemetryError()
        return self


class TelemetryBatch(DTO):
    format: Literal["aegisops-observations-v1"] = "aegisops-observations-v1"
    batch_id: UUID
    collector_id: Literal["local-kind-reader"] = "local-kind-reader"
    cluster_id: Literal["kind-aegisops-demo"] = "kind-aegisops-demo"
    registration_id: UUID
    key_id: Sha256
    origin: Literal["local_kind"] = "local_kind"
    data_class: Literal["internal"] = "internal"
    issued_at: UTCDateTime
    expires_at: UTCDateTime
    snapshots: tuple[PodSnapshot, ...] = Field(max_length=200)

    @model_validator(mode="after")
    def scope(self) -> Self:
        if not timedelta(0) < self.expires_at - self.issued_at <= MAX_LIFETIME:
            raise TelemetryError()
        if len({item.snapshot_id for item in self.snapshots}) != len(self.snapshots):
            raise TelemetryError()
        for item in self.snapshots:
            if item.origin is not Origin.LOCAL_KIND or item.cluster_id != self.cluster_id:
                raise TelemetryError()
            if not self.issued_at - MAX_LIFETIME <= item.observed_at <= self.issued_at:
                raise TelemetryError()
        return self


class SignedTelemetryBatch(DTO):
    body: TelemetryBatch
    signature: Signature


class IngestionReceipt(DTO):
    format: Literal["aegisops-ingestion-v1"] = "aegisops-ingestion-v1"
    telemetry: TelemetryProvenance
    processed_at: UTCDateTime
    snapshot_count: int = Field(strict=True, ge=0, le=200)
    incident_count: int = Field(strict=True, ge=0, le=200)
    audit_run_ids: tuple[UUID, ...] = Field(min_length=1, max_length=200)
    status: RunStatus
    execution_performed: Literal[False] = False

    @model_validator(mode="after")
    def counts(self) -> Self:
        if len(set(self.audit_run_ids)) != len(self.audit_run_ids):
            raise TelemetryError()
        if len(self.audit_run_ids) != max(1, self.incident_count):
            raise TelemetryError()
        return self


class DeliveryResult(DTO):
    receipt: IngestionReceipt
    replayed: StrictBool
