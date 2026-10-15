"""Public-key-only local policy-release contracts and deterministic verification."""

import hashlib
import json
from datetime import timedelta
from typing import Annotated, Literal, Self
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import Field, StrictInt, model_validator

from aegisops.domain.common import DTO, Sha256, UTCDateTime
from aegisops.integrity.models import Signature, TrustAnchor

Revision = Annotated[StrictInt, Field(ge=1, le=2147483647)]
PolicyStatus = Literal[
    "valid",
    "policy_missing",
    "policy_invalid",
    "policy_not_yet_valid",
    "policy_expired",
    "policy_revoked",
    "policy_rollback",
    "policy_mismatch",
]


class PolicyReleaseBody(DTO):
    format: Literal["aegisops-local-policy-v1"] = "aegisops-local-policy-v1"
    release_id: UUID
    revision: Revision
    policy_version: Literal["opa-l0-1"] = "opa-l0-1"
    bundle_hash: Sha256
    catalog_hash: Sha256
    key_id: Sha256
    issued_at: UTCDateTime
    expires_at: UTCDateTime

    @model_validator(mode="after")
    def bounded_lifetime(self) -> Self:
        if not timedelta(0) < self.expires_at - self.issued_at <= timedelta(hours=24):
            raise ValueError("policy release lifetime must be within 24 hours")
        return self


class SignedPolicyRelease(DTO):
    body: PolicyReleaseBody
    signature: Signature


class PolicyReleaseState(DTO):
    generation: Revision
    minimum_revision: Revision
    active_release_hash: Sha256 | None
    revoked_revisions: tuple[Revision, ...] = Field(default=(), max_length=128)
    changed_at: UTCDateTime

    @model_validator(mode="after")
    def sorted_revocations(self) -> Self:
        if self.active_release_hash is None and (
            self.minimum_revision != 1 or self.revoked_revisions
        ):
            raise ValueError("invalid initial release state")
        if list(self.revoked_revisions) != sorted(set(self.revoked_revisions)):
            raise ValueError("revocations must be unique and ordered")
        if any(revision > self.minimum_revision for revision in self.revoked_revisions):
            raise ValueError("future revisions cannot be revoked")
        return self


def release_bytes(body: PolicyReleaseBody) -> bytes:
    return b"aegisops-policy-release-signature-v1\x00" + json.dumps(
        body.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def release_hash(release: SignedPolicyRelease) -> str:
    value = json.dumps(
        release.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(b"aegisops-signed-policy-release-v1\x00" + value).hexdigest()


def authenticate_release(release: SignedPolicyRelease, trust: TrustAnchor) -> None:
    public = bytes.fromhex(trust.public_key)
    if hashlib.sha256(public).hexdigest() != trust.key_id or release.body.key_id != trust.key_id:
        raise ValueError("policy key mismatch")
    Ed25519PublicKey.from_public_bytes(public).verify(
        bytes.fromhex(release.signature), release_bytes(release.body)
    )


def release_status(
    release: SignedPolicyRelease,
    state: PolicyReleaseState,
    trust: TrustAnchor,
    checked_at: UTCDateTime,
    *,
    bundle_hash: str,
    catalog_hash: str,
    policy_version: str,
) -> PolicyStatus:
    try:
        release = SignedPolicyRelease.model_validate(release)
        state = PolicyReleaseState.model_validate(state)
        trust = TrustAnchor.model_validate(trust)
        body = release.body
        authenticate_release(release, trust)
        if body.revision in state.revoked_revisions:
            return "policy_revoked"
        if body.revision < state.minimum_revision:
            return "policy_rollback"
        if (
            body.revision != state.minimum_revision
            or release_hash(release) != state.active_release_hash
        ):
            return "policy_mismatch"
        if (body.bundle_hash, body.catalog_hash, body.policy_version) != (
            bundle_hash,
            catalog_hash,
            policy_version,
        ):
            return "policy_mismatch"
        if checked_at < body.issued_at or checked_at < state.changed_at:
            return "policy_not_yet_valid"
        if checked_at >= body.expires_at:
            return "policy_expired"
        return "valid"
    except Exception:
        return "policy_invalid"


class PolicyReleaseCheck(DTO):
    checked_at: UTCDateTime
    status: PolicyStatus
    release: SignedPolicyRelease | None = None
    state: PolicyReleaseState | None = None
    trust: TrustAnchor | None = None

    def validate_for(self, *, bundle_hash: str, catalog_hash: str, policy_version: str) -> None:
        if self.release is None or self.state is None or self.trust is None:
            if self.status not in {"policy_missing", "policy_invalid"}:
                raise ValueError("policy proof missing")
            return
        expected = release_status(
            self.release,
            self.state,
            self.trust,
            self.checked_at,
            bundle_hash=bundle_hash,
            catalog_hash=catalog_hash,
            policy_version=policy_version,
        )
        if self.status != expected:
            raise ValueError("policy proof verdict mismatch")


def policy_pair_reason(before: PolicyReleaseCheck, after: PolicyReleaseCheck) -> str | None:
    if before.status != "valid":
        return before.status
    if after.status != "valid":
        return after.status
    if after.checked_at < before.checked_at:
        return "policy_clock_rollback"
    if (before.release, before.state, before.trust) != (after.release, after.state, after.trust):
        return "policy_changed"
    return None
