"""Canonical hashes, ordered snapshot chains and public-key-only verification."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from aegisops.integrity.models import (
    MAX_ROWS,
    ChainLink,
    CheckpointBody,
    CheckpointHead,
    IntegrityError,
    RowDigest,
    SignedCheckpoint,
    TrustAnchor,
    VerificationReport,
)

ZERO_HASH = "0" * 64
SIGNING_DOMAIN = b"aegisops-audit-checkpoint-v1\x00"


def canonical(value: object) -> bytes:
    def encode(item: object) -> str:
        if isinstance(item, UUID):
            return str(item)
        if isinstance(item, datetime) and item.tzinfo is not None:
            return item.astimezone(UTC).isoformat(timespec="microseconds")
        raise IntegrityError()

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=encode,
    ).encode("utf-8")


def digest(domain: str, value: object) -> str:
    return hashlib.sha256(domain.encode("ascii") + b"\x00" + canonical(value)).hexdigest()


def key_id(public_key: str) -> str:
    return hashlib.sha256(bytes.fromhex(public_key)).hexdigest()


def checkpoint_hash(checkpoint: SignedCheckpoint) -> str:
    return digest("aegisops-audit-signed-checkpoint-v1", checkpoint.model_dump(mode="json"))


def signing_bytes(body: CheckpointBody) -> bytes:
    return SIGNING_DOMAIN + canonical(body.model_dump(mode="json"))


def checked_rows(rows: tuple[RowDigest, ...]) -> tuple[RowDigest, ...]:
    rows = tuple(RowDigest.model_validate(row) for row in rows)
    ids = [str(row.run_id) for row in rows]
    if len(rows) > MAX_ROWS or ids != sorted(set(ids)):
        raise IntegrityError()
    return rows


def build_body(
    rows: tuple[RowDigest, ...],
    *,
    chain_id: UUID,
    checkpoint_id: UUID,
    created_at: datetime,
    key: str,
    previous_checkpoint_hash: str | None,
) -> CheckpointBody:
    rows = checked_rows(rows)
    previous = previous_checkpoint_hash or ZERO_HASH
    links = []
    for index, row in enumerate(rows, 1):
        hashed = digest(
            "aegisops-audit-snapshot-link-v1",
            {
                "chain_id": str(chain_id),
                "index": index,
                "run_id": str(row.run_id),
                "row_hash": row.row_hash,
                "prev_hash": previous,
            },
        )
        links.append(ChainLink(**row.model_dump(), prev_hash=previous, entry_hash=hashed))
        previous = hashed
    return CheckpointBody(
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
        key_id=key,
        previous_checkpoint_hash=previous_checkpoint_hash,
        entries=tuple(links),
        record_count=len(links),
        head_hash=previous,
    )


def authenticate(
    checkpoint: SignedCheckpoint, trust: TrustAnchor, head: CheckpointHead
) -> SignedCheckpoint:
    try:
        checkpoint = SignedCheckpoint.model_validate(checkpoint)
        trust = TrustAnchor.model_validate(trust)
        head = CheckpointHead.model_validate(head)
        body = checkpoint.body
        if (
            key_id(trust.public_key) != trust.key_id
            or body.key_id != trust.key_id
            or head.chain_id != body.chain_id
            or head.checkpoint_id != body.checkpoint_id
            or head.checkpoint_hash != checkpoint_hash(checkpoint)
        ):
            raise IntegrityError()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(trust.public_key)).verify(
            bytes.fromhex(checkpoint.signature), signing_bytes(body)
        )
        expected = build_body(
            tuple(
                RowDigest(run_id=entry.run_id, row_hash=entry.row_hash) for entry in body.entries
            ),
            chain_id=body.chain_id,
            checkpoint_id=body.checkpoint_id,
            created_at=body.created_at,
            key=body.key_id,
            previous_checkpoint_hash=body.previous_checkpoint_hash,
        )
        if expected != body:
            raise IntegrityError()
        return checkpoint
    except Exception:
        raise IntegrityError() from None


def verify_rows(
    rows: tuple[RowDigest, ...],
    checkpoint: SignedCheckpoint,
    trust: TrustAnchor,
    head: CheckpointHead,
) -> VerificationReport:
    try:
        checkpoint = authenticate(checkpoint, trust, head)
        current = {row.run_id: row.row_hash for row in checked_rows(rows)}
        protected = {entry.run_id: entry.row_hash for entry in checkpoint.body.entries}
        missing = len(protected.keys() - current.keys())
        modified = sum(
            current[run] != hashed for run, hashed in protected.items() if run in current
        )
        unanchored = len(current.keys() - protected.keys())
        return VerificationReport(
            status="failed" if missing or modified else "unanchored" if unanchored else "verified",
            chain_id=head.chain_id,
            checkpoint_id=head.checkpoint_id,
            checkpoint_hash=head.checkpoint_hash,
            protected_records=len(protected),
            current_records=len(current),
            missing_records=missing,
            modified_records=modified,
            unanchored_records=unanchored,
        )
    except Exception:
        raise IntegrityError() from None
