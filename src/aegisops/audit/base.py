"""Storage boundary shared with orchestration, without database imports."""

from typing import Protocol

from aegisops.domain.models import AuditRecord


class AuditStoreError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("audit operation failed")


class AuditStore(Protocol):
    def append(self, record: AuditRecord) -> None: ...
