"""Single-record INSERT transaction under the constrained local app role."""

from types import TracebackType

from sqlalchemy import create_engine, insert
from sqlalchemy.engine import Engine

from aegisops.audit.base import AuditStoreError
from aegisops.audit.validation import prepare_record
from aegisops.config import load_database_url, validate_database_url
from aegisops.db.health import check_database
from aegisops.db.models import AuditRun
from aegisops.domain.models import AuditRecord


def create_audit_engine(database_url: str | None = None) -> Engine:
    try:
        url = load_database_url() if database_url is None else validate_database_url(database_url)
        check_database(url)
        return create_engine(
            url, connect_args={"connect_timeout": 3}, hide_parameters=True, echo=False
        )
    except Exception:
        raise AuditStoreError() from None


class PostgresAuditStore:
    def __init__(self, database_url: str | None = None) -> None:
        self._engine = create_audit_engine(database_url)

    def append(self, record: AuditRecord) -> None:
        try:
            clean = prepare_record(record)
            values = {
                "run_id": clean.run_id,
                "schema_version": clean.schema_version,
                "started_at": clean.started_at,
                "completed_at": clean.completed_at,
                "incident_id": clean.incident.incident_id if clean.incident else None,
                "status": clean.status.value,
                "autonomy_level": clean.autonomy_level.value,
                "origin": clean.origin.value,
                "payload_json": clean.model_dump(mode="json"),
                "model_name": clean.llm.model_name if clean.llm else None,
                "prompt_id": clean.llm.prompt_id if clean.llm else None,
                "prompt_hash": clean.llm.prompt_hash if clean.llm else None,
                "policy_version": clean.policy_decision.policy_version
                if clean.policy_decision
                else None,
                "context_hash": clean.context_hash,
            }
            with self._engine.begin() as connection:
                connection.execute(insert(AuditRun).values(**values))
        except Exception:
            raise AuditStoreError() from None

    def close(self) -> None:
        self._engine.dispose()

    def __enter__(self) -> "PostgresAuditStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
