"""Public provenance attached by the consumer, never supplied by an LLM."""

from typing import Literal
from uuid import UUID

from aegisops.domain.common import DTO, Sha256


class TelemetryProvenance(DTO):
    batch_id: UUID
    batch_hash: Sha256
    collector_id: Literal["local-kind-reader"] = "local-kind-reader"
    registration_id: UUID
    cluster_id: Literal["kind-aegisops-demo"] = "kind-aegisops-demo"
    key_id: Sha256
