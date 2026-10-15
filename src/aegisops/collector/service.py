"""Close the read-only Kubernetes session before sealing a sanitized handoff."""

from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

from aegisops.collector.signing import seal
from aegisops.context.sanitizer import sanitize_data
from aegisops.domain.common import utc_now
from aegisops.domain.models import PodSnapshot
from aegisops.sources.env_guard import load_reader_identity
from aegisops.sources.kubernetes_readonly import KubernetesReadOnlySource
from aegisops.sources.safe_client import ReadOnlyClient
from aegisops.telemetry.contracts import (
    MAX_LIFETIME,
    SignedTelemetryBatch,
    TelemetryBatch,
    TelemetryError,
)
from aegisops.telemetry.files import load_trust, packet_path, write_new


def collect_batch(root: Path, *, id_factory: Callable[[], UUID] = uuid4) -> SignedTelemetryBatch:
    try:
        trust = load_trust(root)
        identity = load_reader_identity(root)
        if identity.instance_id != str(trust.registration_id):
            raise TelemetryError()
        reader = ReadOnlyClient(identity)
        try:
            raw = KubernetesReadOnlySource(reader, include_logs=True).collect()
        finally:
            reader.close()
        snapshots = tuple(
            PodSnapshot.model_validate(sanitize_data(item.model_dump(mode="json"))) for item in raw
        )
        now = utc_now()
        body = TelemetryBatch(
            batch_id=id_factory(),
            registration_id=trust.registration_id,
            key_id=trust.key_id,
            issued_at=now,
            expires_at=now + MAX_LIFETIME,
            snapshots=snapshots,
        )
        packet = seal(body, trust, root)
        write_new(packet_path(root, body.batch_id), packet.model_dump_json().encode("utf-8"))
        return packet
    except Exception:
        raise TelemetryError() from None
