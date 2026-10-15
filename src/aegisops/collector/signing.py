"""Collector-only signing key access. The Control Plane imports only the verifier."""

import os
import stat
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aegisops.io import read_text
from aegisops.telemetry.contracts import (
    CollectorTrust,
    SignedTelemetryBatch,
    TelemetryBatch,
    TelemetryError,
)
from aegisops.telemetry.files import safe_path
from aegisops.telemetry.verification import signing_bytes, verify_packet


def seal(body: TelemetryBatch, trust: CollectorTrust, root: Path) -> SignedTelemetryBatch:
    try:
        path = safe_path(root.parents[1] / "work/collector-signing" / f"{trust.key_id}.pem")
        if os.name != "nt" and (
            stat.S_IMODE(path.stat().st_mode) != 0o600
            or path.stat().st_uid != getattr(os, "getuid")()
        ):
            raise TelemetryError()
        key = serialization.load_pem_private_key(
            read_text(path, 4096).encode("ascii"), password=None
        )
        if (
            not isinstance(key, Ed25519PrivateKey)
            or key.public_key().public_bytes_raw().hex() != trust.public_key
        ):
            raise TelemetryError()
        packet = SignedTelemetryBatch(body=body, signature=key.sign(signing_bytes(body)).hex())
        verify_packet(packet, trust)
        return packet
    except Exception:
        raise TelemetryError() from None
