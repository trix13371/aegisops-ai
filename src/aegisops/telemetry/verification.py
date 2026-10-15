"""Public-key-only packet verification; no Kubernetes, database or issuer access."""

import hashlib
import json
from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from aegisops.context.sanitizer import sanitize_data
from aegisops.domain.telemetry import TelemetryProvenance
from aegisops.telemetry.contracts import (
    MAX_PACKET_BYTES,
    CollectorTrust,
    SignedTelemetryBatch,
    TelemetryBatch,
    TelemetryError,
)

DOMAIN = b"aegisops-telemetry-envelope-v1\x00"


def signing_bytes(body: TelemetryBatch) -> bytes:
    return DOMAIN + json.dumps(
        body.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def verify_packet(packet: SignedTelemetryBatch, trust: CollectorTrust) -> TelemetryProvenance:
    try:
        packet = SignedTelemetryBatch.model_validate(packet)
        trust = CollectorTrust.model_validate(trust)
        body = packet.body
        if (body.collector_id, body.cluster_id, body.registration_id, body.key_id) != (
            trust.collector_id,
            trust.cluster_id,
            trust.registration_id,
            trust.key_id,
        ):
            raise TelemetryError()
        data = signing_bytes(body)
        if len(packet.model_dump_json().encode()) > MAX_PACKET_BYTES:
            raise TelemetryError()
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(trust.public_key)).verify(
            bytes.fromhex(packet.signature), data
        )
        for item in body.snapshots:
            raw = item.model_dump(mode="json")
            if sanitize_data(raw) != raw:
                raise TelemetryError()
        return TelemetryProvenance(
            batch_id=body.batch_id,
            batch_hash=hashlib.sha256(data).hexdigest(),
            collector_id=body.collector_id,
            cluster_id=body.cluster_id,
            registration_id=body.registration_id,
            key_id=body.key_id,
        )
    except Exception:
        raise TelemetryError() from None


def require_fresh(body: TelemetryBatch, now: datetime) -> None:
    if not body.issued_at <= now < body.expires_at:
        raise TelemetryError()
