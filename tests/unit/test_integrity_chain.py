"""Focused unit tests for the pure snapshot verifier.

No database, no filesystem, no external secrets: Ed25519 keys are generated
ephemerally per test via the ``cryptography`` library.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aegisops.integrity.chain import (
    build_body,
    checkpoint_hash,
    key_id,
    signing_bytes,
    verify_rows,
)
from aegisops.integrity.models import (
    CheckpointBody,
    CheckpointHead,
    IntegrityError,
    RowDigest,
    SignedCheckpoint,
    TrustAnchor,
    VerificationReport,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _row_hash(seed: str) -> str:
    return _sha256_hex(seed.encode("utf-8"))


def _make_rows(n: int) -> tuple[RowDigest, ...]:
    """Build ``n`` rows with deterministic UUIDs, already sorted ascending."""
    rows = [RowDigest(run_id=UUID(int=i + 1), row_hash=_row_hash(f"row-{i}")) for i in range(n)]
    rows.sort(key=lambda r: str(r.run_id))
    return tuple(rows)


class _Key:
    """Ephemeral Ed25519 keypair plus derived trust anchor."""

    def __init__(self) -> None:
        self.private = Ed25519PrivateKey.generate()
        from cryptography.hazmat.primitives import serialization

        raw = self.private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.public_hex = raw.hex()
        self.key_id = key_id(self.public_hex)

    def anchor(self) -> TrustAnchor:
        return TrustAnchor(public_key=self.public_hex, key_id=self.key_id)

    def sign(self, body: CheckpointBody) -> SignedCheckpoint:
        sig = self.private.sign(signing_bytes(body)).hex()
        return SignedCheckpoint(body=body, signature=sig)


@pytest.fixture()
def key() -> _Key:
    return _Key()


@pytest.fixture()
def other_key() -> _Key:
    return _Key()


@pytest.fixture()
def chain_id() -> UUID:
    return uuid4()


@pytest.fixture()
def checkpoint_id() -> UUID:
    return uuid4()


@pytest.fixture()
def created_at() -> datetime:
    return datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def _build_signed(
    rows: tuple[RowDigest, ...],
    key: _Key,
    *,
    chain_id: UUID,
    checkpoint_id: UUID,
    created_at: datetime,
    previous_checkpoint_hash: str | None = None,
) -> SignedCheckpoint:
    body = build_body(
        rows,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
        key=key.key_id,
        previous_checkpoint_hash=previous_checkpoint_hash,
    )
    return key.sign(body)


def _head_for(checkpoint: SignedCheckpoint) -> CheckpointHead:
    return CheckpointHead(
        chain_id=checkpoint.body.chain_id,
        checkpoint_id=checkpoint.body.checkpoint_id,
        checkpoint_hash=checkpoint_hash(checkpoint),
    )


# ---------------------------------------------------------------------------
# Valid / empty
# ---------------------------------------------------------------------------


def test_valid_snapshot_verifies(key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(5)
    checkpoint = _build_signed(
        rows,
        key,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
    )
    report = verify_rows(rows, checkpoint, key.anchor(), _head_for(checkpoint))

    assert isinstance(report, VerificationReport)
    assert report.status == "verified"
    assert report.chain_id == chain_id
    assert report.checkpoint_id == checkpoint_id
    assert report.checkpoint_hash == checkpoint_hash(checkpoint)
    assert report.protected_records == 5
    assert report.current_records == 5
    assert report.missing_records == 0
    assert report.modified_records == 0
    assert report.unanchored_records == 0


def test_empty_snapshot_verifies(key, chain_id, checkpoint_id, created_at):
    checkpoint = _build_signed(
        (),
        key,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
    )
    report = verify_rows((), checkpoint, key.anchor(), _head_for(checkpoint))

    assert report.status == "verified"
    assert report.protected_records == 0
    assert report.current_records == 0
    assert report.missing_records == 0
    assert report.modified_records == 0
    assert report.unanchored_records == 0


# ---------------------------------------------------------------------------
# Modified / missing rows
# ---------------------------------------------------------------------------


def test_modified_row_is_failed(key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(4)
    checkpoint = _build_signed(
        rows,
        key,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
    )
    tampered = list(rows)
    tampered[1] = RowDigest(run_id=tampered[1].run_id, row_hash=_row_hash("tampered"))

    report = verify_rows(tuple(tampered), checkpoint, key.anchor(), _head_for(checkpoint))

    assert report.status == "failed"
    assert report.protected_records == 4
    assert report.current_records == 4
    assert report.missing_records == 0
    assert report.modified_records == 1
    assert report.unanchored_records == 0


def test_missing_middle_row_is_failed(key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(4)
    checkpoint = _build_signed(
        rows,
        key,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
    )
    current = (rows[0], rows[2], rows[3])

    report = verify_rows(current, checkpoint, key.anchor(), _head_for(checkpoint))

    assert report.status == "failed"
    assert report.protected_records == 4
    assert report.current_records == 3
    assert report.missing_records == 1
    assert report.modified_records == 0
    assert report.unanchored_records == 0


def test_missing_final_row_is_failed(key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(4)
    checkpoint = _build_signed(
        rows,
        key,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
    )
    current = rows[:-1]

    report = verify_rows(current, checkpoint, key.anchor(), _head_for(checkpoint))

    assert report.status == "failed"
    assert report.missing_records == 1
    assert report.modified_records == 0
    assert report.unanchored_records == 0


def test_missing_all_rows_is_failed(key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(3)
    checkpoint = _build_signed(
        rows,
        key,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
    )

    report = verify_rows((), checkpoint, key.anchor(), _head_for(checkpoint))

    assert report.status == "failed"
    assert report.protected_records == 3
    assert report.current_records == 0
    assert report.missing_records == 3
    assert report.modified_records == 0
    assert report.unanchored_records == 0


# ---------------------------------------------------------------------------
# Unanchored rows
# ---------------------------------------------------------------------------


def test_appended_unanchored_row_is_unanchored(key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(3)
    checkpoint = _build_signed(
        rows,
        key,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
    )
    extra = RowDigest(run_id=UUID(int=9999), row_hash=_row_hash("extra"))
    current = tuple(sorted((*rows, extra), key=lambda r: str(r.run_id)))

    report = verify_rows(current, checkpoint, key.anchor(), _head_for(checkpoint))

    assert report.status == "unanchored"
    assert report.protected_records == 3
    assert report.current_records == 4
    assert report.missing_records == 0
    assert report.modified_records == 0
    assert report.unanchored_records == 1


# ---------------------------------------------------------------------------
# Signature / trust / replay
# ---------------------------------------------------------------------------


def test_invalid_signature_is_rejected(key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(2)
    checkpoint = _build_signed(
        rows,
        key,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
    )
    bad = SignedCheckpoint(body=checkpoint.body, signature="0" * 128)

    with pytest.raises(IntegrityError):
        verify_rows(rows, bad, key.anchor(), _head_for(bad))


def test_different_trusted_key_is_rejected(key, other_key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(2)
    checkpoint = _build_signed(
        rows,
        key,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
    )

    with pytest.raises(IntegrityError):
        verify_rows(rows, checkpoint, other_key.anchor(), _head_for(checkpoint))


def test_old_checkpoint_with_new_pinned_head_is_rejected(key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(2)
    old = _build_signed(
        rows,
        key,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
    )
    new = _build_signed(
        rows,
        key,
        chain_id=chain_id,
        checkpoint_id=uuid4(),
        created_at=created_at,
    )

    with pytest.raises(IntegrityError):
        verify_rows(rows, old, key.anchor(), _head_for(new))


# ---------------------------------------------------------------------------
# Reorder / duplicate rows
# ---------------------------------------------------------------------------


def test_reordered_rows_are_rejected(key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(3)
    checkpoint = _build_signed(
        rows,
        key,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
    )
    reordered = (rows[1], rows[0], rows[2])

    with pytest.raises(IntegrityError):
        verify_rows(reordered, checkpoint, key.anchor(), _head_for(checkpoint))


def test_duplicate_rows_are_rejected(key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(3)
    checkpoint = _build_signed(
        rows,
        key,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
    )
    duplicated = (rows[0], rows[0], rows[1], rows[2])

    with pytest.raises(IntegrityError):
        verify_rows(duplicated, checkpoint, key.anchor(), _head_for(checkpoint))


# ---------------------------------------------------------------------------
# Broken chain / head / count even with a VALID re-signature
# ---------------------------------------------------------------------------


def _resign(body: CheckpointBody, key: _Key) -> SignedCheckpoint:
    """Re-sign a (possibly tampered) body with a valid key."""
    return key.sign(body)


def test_broken_chain_with_valid_resignature_is_rejected(key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(3)
    body = build_body(
        rows,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
        key=key.key_id,
        previous_checkpoint_hash=None,
    )
    # Tamper an entry_hash in the middle of the chain, then re-sign validly.
    entries = list(body.entries)
    entries[1] = entries[1].model_copy(update={"entry_hash": _row_hash("broken")})
    tampered = body.model_copy(update={"entries": tuple(entries)})
    resigned = _resign(tampered, key)

    with pytest.raises(IntegrityError):
        verify_rows(rows, resigned, key.anchor(), _head_for(resigned))


def test_broken_head_with_valid_resignature_is_rejected(key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(3)
    body = build_body(
        rows,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
        key=key.key_id,
        previous_checkpoint_hash=None,
    )
    tampered = body.model_copy(update={"head_hash": _row_hash("broken-head")})
    resigned = _resign(tampered, key)

    with pytest.raises(IntegrityError):
        verify_rows(rows, resigned, key.anchor(), _head_for(resigned))


def test_broken_count_with_valid_resignature_is_rejected(key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(3)
    body = build_body(
        rows,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
        key=key.key_id,
        previous_checkpoint_hash=None,
    )
    tampered = body.model_copy(update={"record_count": body.record_count + 1})
    resigned = _resign(tampered, key)

    with pytest.raises(IntegrityError):
        verify_rows(rows, resigned, key.anchor(), _head_for(resigned))


# ---------------------------------------------------------------------------
# Report invariants: strict numeric fields
# ---------------------------------------------------------------------------


def test_report_numeric_fields_are_strict_ints(key, chain_id, checkpoint_id, created_at):
    rows = _make_rows(2)
    checkpoint = _build_signed(
        rows,
        key,
        chain_id=chain_id,
        checkpoint_id=checkpoint_id,
        created_at=created_at,
    )
    report = verify_rows(rows, checkpoint, key.anchor(), _head_for(checkpoint))

    for field in (
        "protected_records",
        "current_records",
        "missing_records",
        "modified_records",
        "unanchored_records",
    ):
        value = getattr(report, field)
        assert type(value) is int
        assert value >= 0


def test_report_rejects_inconsistent_counts():
    with pytest.raises(ValueError):
        VerificationReport(
            status="verified",
            chain_id=uuid4(),
            checkpoint_id=uuid4(),
            checkpoint_hash="0" * 64,
            protected_records=1,
            current_records=1,
            missing_records=1,
            modified_records=1,
            unanchored_records=0,
        )


def test_report_rejects_wrong_status_for_counts():
    with pytest.raises(ValueError):
        VerificationReport(
            status="verified",
            chain_id=uuid4(),
            checkpoint_id=uuid4(),
            checkpoint_hash="0" * 64,
            protected_records=2,
            current_records=1,
            missing_records=1,
            modified_records=0,
            unanchored_records=0,
        )


def test_report_rejects_inconsistent_current_count():
    with pytest.raises(ValueError):
        VerificationReport(
            status="unanchored",
            chain_id=uuid4(),
            checkpoint_id=uuid4(),
            checkpoint_hash="0" * 64,
            protected_records=2,
            current_records=99,
            missing_records=0,
            modified_records=0,
            unanchored_records=1,
        )


@pytest.mark.parametrize("value", [True, 1.0, "1", -1, 10001])
def test_report_rejects_noninteger_or_out_of_bounds_counts(value):
    with pytest.raises(ValueError):
        VerificationReport(
            status="verified",
            chain_id=uuid4(),
            checkpoint_id=uuid4(),
            checkpoint_hash="a" * 64,
            protected_records=value,
            current_records=value,
            missing_records=0,
            modified_records=0,
            unanchored_records=0,
        )


def test_uuid_binary_and_fixed_string_order_are_identical():
    values = [UUID(int=0), UUID(int=127), UUID(int=128), UUID(int=2**128 - 1)]
    values.extend(uuid4() for _ in range(1000))
    assert sorted(values, key=lambda value: value.bytes) == sorted(values, key=str)
