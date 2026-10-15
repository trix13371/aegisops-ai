"""Focused parameterized unit tests for the public-only signed policy verifier.

These tests exercise ``release_status`` and related contracts using an
ephemeral Ed25519 key. No filesystem, database, secrets, or external tools
are used.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from aegisops.domain.policy_release import (
    PolicyReleaseBody,
    PolicyReleaseState,
    SignedPolicyRelease,
    release_bytes,
    release_hash,
    release_status,
)
from aegisops.integrity.models import TrustAnchor

# ---------------------------------------------------------------------------
# Fixed clock and ephemeral key material
# ---------------------------------------------------------------------------

NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
ISSUED_AT = NOW - timedelta(minutes=1)
EXPIRES_AT = NOW + timedelta(hours=1)

_PRIVATE_KEY = Ed25519PrivateKey.generate()
_PUBLIC_KEY_HEX = _PRIVATE_KEY.public_key().public_bytes_raw().hex()
_KEY_ID = hashlib.sha256(bytes.fromhex(_PUBLIC_KEY_HEX)).hexdigest()

_OTHER_PRIVATE_KEY = Ed25519PrivateKey.generate()
_OTHER_PUBLIC_KEY_HEX = _OTHER_PRIVATE_KEY.public_key().public_bytes_raw().hex()
_OTHER_KEY_ID = hashlib.sha256(bytes.fromhex(_OTHER_PUBLIC_KEY_HEX)).hexdigest()

BUNDLE_HASH = "a" * 64
CATALOG_HASH = "b" * 64
POLICY_VERSION = "opa-l0-1"

RELEASE_ID = UUID("00000000-0000-0000-0000-000000000001")


def _sign(body: PolicyReleaseBody, identity: Ed25519PrivateKey | None = None) -> str:
    signer = _PRIVATE_KEY if identity is None else identity
    return signer.sign(release_bytes(body)).hex()


def _make_body(**overrides: object) -> PolicyReleaseBody:
    data: dict[str, object] = {
        "release_id": RELEASE_ID,
        "revision": 2,
        "bundle_hash": BUNDLE_HASH,
        "catalog_hash": CATALOG_HASH,
        "key_id": _KEY_ID,
        "issued_at": ISSUED_AT,
        "expires_at": EXPIRES_AT,
    }
    data.update(overrides)
    return PolicyReleaseBody(**data)


def _make_release(body: PolicyReleaseBody | None = None) -> SignedPolicyRelease:
    body = body if body is not None else _make_body()
    return SignedPolicyRelease(body=body, signature=_sign(body))


def _make_trust() -> TrustAnchor:
    return TrustAnchor(public_key=_PUBLIC_KEY_HEX, key_id=_KEY_ID)


def _make_state(
    *,
    active_release_hash: str | None,
    minimum_revision: int = 2,
    generation: int = 2,
    revoked_revisions: tuple[int, ...] = (),
    changed_at: datetime = ISSUED_AT,
) -> PolicyReleaseState:
    return PolicyReleaseState(
        generation=generation,
        minimum_revision=minimum_revision,
        active_release_hash=active_release_hash,
        revoked_revisions=revoked_revisions,
        changed_at=changed_at,
    )


def _state_for(release: SignedPolicyRelease, **overrides: object) -> PolicyReleaseState:
    params: dict[str, object] = {
        "active_release_hash": release_hash(release),
        "minimum_revision": release.body.revision,
        "generation": release.body.revision,
        "changed_at": release.body.issued_at,
    }
    params.update(overrides)
    return _make_state(**params)  # type: ignore[arg-type]


def _status(
    release: SignedPolicyRelease,
    state: PolicyReleaseState,
    trust: TrustAnchor,
    checked_at: datetime = NOW,
    *,
    bundle_hash: str = BUNDLE_HASH,
    catalog_hash: str = CATALOG_HASH,
    policy_version: str = POLICY_VERSION,
) -> str:
    return release_status(
        release,
        state,
        trust,
        checked_at,
        bundle_hash=bundle_hash,
        catalog_hash=catalog_hash,
        policy_version=policy_version,
    )


# ---------------------------------------------------------------------------
# Baseline sanity
# ---------------------------------------------------------------------------


def test_baseline_release_is_valid() -> None:
    release = _make_release()
    state = _state_for(release)
    assert _status(release, state, _make_trust()) == "valid"


# ---------------------------------------------------------------------------
# Time boundary tests
# ---------------------------------------------------------------------------


def test_expiry_exact_boundary_is_expired() -> None:
    release = _make_release()
    state = _state_for(release)
    assert _status(release, state, _make_trust(), checked_at=EXPIRES_AT) == "policy_expired"


def test_one_microsecond_before_expiry_is_valid() -> None:
    release = _make_release()
    state = _state_for(release)
    checked_at = EXPIRES_AT - timedelta(microseconds=1)
    assert _status(release, state, _make_trust(), checked_at=checked_at) == "valid"


def test_checked_at_before_issued_at_is_not_yet_valid() -> None:
    release = _make_release()
    state = _state_for(release)
    checked_at = ISSUED_AT - timedelta(microseconds=1)
    assert _status(release, state, _make_trust(), checked_at=checked_at) == "policy_not_yet_valid"


def test_checked_at_exactly_issued_at_is_valid() -> None:
    release = _make_release()
    state = _state_for(release)
    assert _status(release, state, _make_trust(), checked_at=ISSUED_AT) == "valid"


def test_checked_at_before_state_changed_at_is_not_yet_valid() -> None:
    release = _make_release()
    state = _state_for(release, changed_at=NOW + timedelta(minutes=5))
    assert _status(release, state, _make_trust()) == "policy_not_yet_valid"


# ---------------------------------------------------------------------------
# Revocation and rollback
# ---------------------------------------------------------------------------


def test_revoked_revision_is_revoked() -> None:
    release = _make_release()
    state = _state_for(release, revoked_revisions=(2,))
    assert _status(release, state, _make_trust()) == "policy_revoked"


def test_older_revision_below_floor_is_rollback() -> None:
    release = _make_release()
    state = _state_for(release, minimum_revision=3, generation=3)
    assert _status(release, state, _make_trust()) == "policy_rollback"


# ---------------------------------------------------------------------------
# Hash / catalog / bundle mismatches
# ---------------------------------------------------------------------------


def test_wrong_active_release_hash_is_mismatch() -> None:
    release = _make_release()
    state = _state_for(release, active_release_hash="c" * 64)
    assert _status(release, state, _make_trust()) == "policy_mismatch"


@pytest.mark.parametrize(
    "bundle_hash,catalog_hash,policy_version",
    [
        ("d" * 64, CATALOG_HASH, POLICY_VERSION),
        (BUNDLE_HASH, "e" * 64, POLICY_VERSION),
        (BUNDLE_HASH, CATALOG_HASH, "opa-l0-2"),
    ],
)
def test_wrong_catalog_or_bundle_is_mismatch(
    bundle_hash: str, catalog_hash: str, policy_version: str
) -> None:
    release = _make_release()
    state = _state_for(release)
    assert (
        _status(
            release,
            state,
            _make_trust(),
            bundle_hash=bundle_hash,
            catalog_hash=catalog_hash,
            policy_version=policy_version,
        )
        == "policy_mismatch"
    )


# ---------------------------------------------------------------------------
# Signature and key handling
# ---------------------------------------------------------------------------


def test_invalid_signature_is_invalid_with_matching_state() -> None:
    body = _make_body()
    release = SignedPolicyRelease(body=body, signature="00" * 64)
    # State is derived from the *mutated* release so the hash still matches;
    # only the signature is wrong.
    state = _state_for(release)
    assert _status(release, state, _make_trust()) == "policy_invalid"


def test_wrong_key_is_invalid() -> None:
    body = _make_body(key_id=_OTHER_KEY_ID)
    release = SignedPolicyRelease(body=body, signature=_sign(body, _OTHER_PRIVATE_KEY))
    state = _state_for(release)
    assert _status(release, state, _make_trust()) == "policy_invalid"


def test_signature_from_wrong_key_is_invalid() -> None:
    body = _make_body()
    release = SignedPolicyRelease(body=body, signature=_sign(body, _OTHER_PRIVATE_KEY))
    state = _state_for(release)
    assert _status(release, state, _make_trust()) == "policy_invalid"


# ---------------------------------------------------------------------------
# Semantic status tests re-sign mutated bodies
# ---------------------------------------------------------------------------


def test_resigned_mutated_body_reports_semantic_status() -> None:
    # Mutate the body (bundle hash) and re-sign so the signature is valid;
    # the mismatch is purely semantic.
    body = _make_body(bundle_hash="f" * 64)
    release = _make_release(body)
    state = _state_for(release)
    assert _status(release, state, _make_trust()) == "policy_mismatch"


def test_resigned_revoked_body_reports_revoked() -> None:
    body = _make_body(revision=2)
    release = _make_release(body)
    state = _state_for(release, revoked_revisions=(2,))
    assert _status(release, state, _make_trust()) == "policy_revoked"


# ---------------------------------------------------------------------------
# Bounded lifetime and strict revision validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "issued_at,expires_at",
    [
        (ISSUED_AT, ISSUED_AT),  # zero lifetime
        (ISSUED_AT, ISSUED_AT - timedelta(seconds=1)),  # negative lifetime
        (ISSUED_AT, ISSUED_AT + timedelta(hours=24, seconds=1)),  # too long
    ],
)
def test_bounded_lifetime_rejects_invalid_windows(
    issued_at: datetime, expires_at: datetime
) -> None:
    with pytest.raises(ValidationError):
        _make_body(issued_at=issued_at, expires_at=expires_at)


def test_bounded_lifetime_accepts_exactly_24_hours() -> None:
    body = _make_body(issued_at=ISSUED_AT, expires_at=ISSUED_AT + timedelta(hours=24))
    assert body.expires_at - body.issued_at == timedelta(hours=24)


@pytest.mark.parametrize("revision", [True, False])
def test_strict_revision_rejects_bool(revision: bool) -> None:
    with pytest.raises(ValidationError):
        _make_body(revision=revision)


@pytest.mark.parametrize("revision", [0, -1, 2147483648])
def test_revision_bounds(revision: int) -> None:
    with pytest.raises(ValidationError):
        _make_body(revision=revision)


# ---------------------------------------------------------------------------
# State contract validation
# ---------------------------------------------------------------------------


def test_initial_state_requires_minimum_revision_one() -> None:
    with pytest.raises(ValidationError):
        _make_state(active_release_hash=None, minimum_revision=2, generation=1)


def test_initial_state_rejects_revocations() -> None:
    with pytest.raises(ValidationError):
        _make_state(
            active_release_hash=None, minimum_revision=1, generation=1, revoked_revisions=(1,)
        )


def test_revocations_must_be_sorted_and_unique() -> None:
    with pytest.raises(ValidationError):
        _make_state(active_release_hash="a" * 64, revoked_revisions=(2, 1))


def test_future_revisions_cannot_be_revoked() -> None:
    with pytest.raises(ValidationError):
        _make_state(active_release_hash="a" * 64, minimum_revision=2, revoked_revisions=(3,))


# ---------------------------------------------------------------------------
