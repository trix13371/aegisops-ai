"""Bounded SELECT interface, separated from append-only writer."""

from types import TracebackType
from uuid import UUID

from sqlalchemy import Text, cast, func, select
from sqlalchemy.engine import Connection

from aegisops.audit.base import AuditStoreError
from aegisops.audit.postgres import create_audit_engine
from aegisops.audit.validation import MAX_AUDIT_BYTES, sanitize_record
from aegisops.context.sanitizer import sanitize_data
from aegisops.db.models import AuditRun
from aegisops.domain.common import DTO, Identifier, UTCDateTime
from aegisops.domain.models import AuditRecord, Origin, RunStatus


class AuditEntry(DTO):
    run_id: UUID
    started_at: UTCDateTime
    completed_at: UTCDateTime | None
    status: RunStatus
    origin: Origin
    incident_id: Identifier | None
    model_name: Identifier | None
    policy_version: Identifier | None


def read_audit_record(connection: Connection, run_id: UUID) -> AuditRecord:
    """Read a validated historical record inside an existing local transaction."""
    try:
        if type(run_id) is not UUID:
            raise AuditStoreError()
        statement = select(AuditRun.payload_json).where(
            AuditRun.run_id == run_id,
            func.octet_length(cast(AuditRun.payload_json, Text)) <= MAX_AUDIT_BYTES * 2,
        )
        payload = connection.execute(statement).scalar_one()
        record = sanitize_record(payload)
        if record.run_id != run_id:
            raise AuditStoreError()
        return record
    except Exception:
        raise AuditStoreError() from None


class PostgresAuditReader:
    def __init__(self, database_url: str | None = None) -> None:
        self._engine = create_audit_engine(database_url)

    def list(self, limit: int = 25) -> tuple[AuditEntry, ...]:
        try:
            if type(limit) is not int or not 1 <= limit <= 100:
                raise AuditStoreError()
            statement = (
                select(
                    AuditRun.run_id,
                    AuditRun.started_at,
                    AuditRun.completed_at,
                    AuditRun.status,
                    AuditRun.origin,
                    AuditRun.incident_id,
                    AuditRun.model_name,
                    AuditRun.policy_version,
                )
                .order_by(AuditRun.started_at.desc(), AuditRun.run_id.desc())
                .limit(limit)
            )
            with self._engine.connect() as connection:
                rows = connection.execute(statement).mappings().all()
            entries = tuple(AuditEntry.model_validate(dict(row)) for row in rows)
            return tuple(
                AuditEntry.model_validate(sanitize_data(entry.model_dump(mode="json")))
                for entry in entries
            )
        except Exception:
            raise AuditStoreError() from None

    def show(self, run_id: UUID) -> AuditRecord:
        try:
            with self._engine.connect() as connection:
                return read_audit_record(connection, run_id)
        except Exception:
            raise AuditStoreError() from None

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> "PostgresAuditReader":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
