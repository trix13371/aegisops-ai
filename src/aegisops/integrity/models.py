"""Versioned snapshot contracts. Hash-chain and signatures are additional runtime checks."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictInt, StrictStr, model_validator

from aegisops.domain.common import DTO, Sha256, UTCDateTime

MAX_ROWS = 10000
Count = Annotated[StrictInt, Field(ge=0, le=MAX_ROWS)]
Signature = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{128}$")]


class IntegrityError(ValueError):
    def __init__(self) -> None:
        super().__init__("audit integrity verification rejected")


class RowDigest(DTO):
    run_id: UUID
    row_hash: Sha256


class ChainLink(RowDigest):
    prev_hash: Sha256
    entry_hash: Sha256


class CheckpointBody(DTO):
    format: Literal["aegisops-audit-snapshot-v1"] = "aegisops-audit-snapshot-v1"
    chain_id: UUID
    checkpoint_id: UUID
    created_at: UTCDateTime
    key_id: Sha256
    previous_checkpoint_hash: Sha256 | None
    entries: tuple[ChainLink, ...] = Field(max_length=MAX_ROWS)
    record_count: Count
    head_hash: Sha256


class SignedCheckpoint(DTO):
    body: CheckpointBody
    signature: Signature


class TrustAnchor(DTO):
    public_key: Sha256  # 32 raw public bytes encoded as lowercase hex; not a secret.
    key_id: Sha256


class CheckpointHead(DTO):
    chain_id: UUID
    checkpoint_id: UUID
    checkpoint_hash: Sha256


class VerificationReport(DTO):
    format: Literal["aegisops-audit-verification-v1"] = "aegisops-audit-verification-v1"
    status: Literal["verified", "unanchored", "failed"]
    chain_id: UUID
    checkpoint_id: UUID
    checkpoint_hash: Sha256
    protected_records: Count
    current_records: Count
    missing_records: Count
    modified_records: Count
    unanchored_records: Count

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.missing_records + self.modified_records > self.protected_records:
            raise ValueError("invalid integrity counts")
        if (
            self.current_records
            != self.protected_records - self.missing_records + self.unanchored_records
        ):
            raise ValueError("invalid current count")
        status = (
            "failed"
            if self.missing_records or self.modified_records
            else ("unanchored" if self.unanchored_records else "verified")
        )
        if self.status != status:
            raise ValueError("invalid integrity verdict")
        return self
