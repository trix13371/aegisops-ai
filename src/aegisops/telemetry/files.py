"""Fixed local handoff paths, bounded reads and exclusive durable file creation."""

import json
import os
from pathlib import Path
from uuid import UUID

from aegisops.io import read_text, unique_object
from aegisops.telemetry.contracts import CollectorTrust, SignedTelemetryBatch, TelemetryError


def safe_path(path: Path) -> Path:
    path = path.absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise TelemetryError()
    return path


def read_object(path: Path, maximum: int) -> object:
    try:
        return json.loads(read_text(safe_path(path), maximum), object_pairs_hook=unique_object)
    except Exception:
        raise TelemetryError() from None


def load_trust(root: Path) -> CollectorTrust:
    try:
        return CollectorTrust.model_validate(
            read_object(root / ".local/telemetry/trust.json", 2048)
        )
    except Exception:
        raise TelemetryError() from None


def packet_path(root: Path, batch_id: UUID) -> Path:
    if type(batch_id) is not UUID:
        raise TelemetryError()
    return safe_path(root / ".local/telemetry/outbox" / f"{batch_id}.json")


def write_new(path: Path, contents: bytes) -> None:
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())


def read_packet(root: Path, batch_id: UUID) -> SignedTelemetryBatch:
    from aegisops.telemetry.contracts import MAX_PACKET_BYTES

    try:
        packet = SignedTelemetryBatch.model_validate(
            read_object(packet_path(root, batch_id), MAX_PACKET_BYTES)
        )
        if packet.body.batch_id != batch_id:
            raise TelemetryError()
        return packet
    except Exception:
        raise TelemetryError() from None
