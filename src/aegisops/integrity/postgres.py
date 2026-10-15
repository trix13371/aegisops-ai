"""Bounded REPEATABLE READ snapshot of physical audit_runs rows, without rewriting history."""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Text, case, cast, func, select, text
from sqlalchemy.engine import Connection, Engine

from aegisops.audit.postgres import create_audit_engine
from aegisops.audit.validation import MAX_AUDIT_BYTES
from aegisops.db.models import AuditRun
from aegisops.integrity.chain import canonical, digest, verify_rows
from aegisops.integrity.models import MAX_ROWS, IntegrityError, RowDigest, VerificationReport
from aegisops.integrity.repository import load_checkpoint

MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024


def hash_row(row: Mapping[str, Any]) -> RowDigest:
    # Raw stored row: redaction/DTO normalization here would conceal modifications.
    value = dict(row)
    if set(value) != set(AuditRun.__table__.columns.keys()):
        raise IntegrityError()
    if len(canonical(value)) > MAX_AUDIT_BYTES * 2 + 8192:
        raise IntegrityError()
    return RowDigest(run_id=value["run_id"], row_hash=digest("aegisops-audit-row-v1", value))


def read_rows(connection: Connection) -> tuple[RowDigest, ...]:
    """Read inside an existing transaction; production wrapper enforces read-only isolation."""
    try:
        payload_size = func.octet_length(cast(AuditRun.payload_json, Text))
        columns = [column for column in AuditRun.__table__.columns if column.name != "payload_json"]
        statement = (
            select(
                *columns,
                case(
                    (payload_size <= MAX_AUDIT_BYTES * 2, AuditRun.payload_json), else_=None
                ).label("payload_json"),
                payload_size.label("_payload_bytes"),
            )
            .order_by(AuditRun.run_id)
            .limit(MAX_ROWS + 1)
        )
        rows: list[RowDigest] = []
        total = 0
        with connection.execution_options(yield_per=100).execute(statement) as result:
            for row in result.mappings():
                value = dict(row)
                size = value.pop("_payload_bytes")
                if size is None or size > MAX_AUDIT_BYTES * 2 or len(rows) >= MAX_ROWS:
                    raise IntegrityError()
                total += len(canonical(value))
                if total > MAX_SNAPSHOT_BYTES:
                    raise IntegrityError()
                rows.append(hash_row(value))
        return tuple(rows)
    except Exception:
        raise IntegrityError() from None


def snapshot(engine: Engine) -> tuple[RowDigest, ...]:
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
            with connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                connection.execute(text("SET LOCAL statement_timeout = '10s'"))
                return read_rows(connection)
    except Exception:
        raise IntegrityError() from None


def verify_database() -> VerificationReport:
    engine = None
    try:
        checkpoint, trust, head = load_checkpoint()
        engine = create_audit_engine()
        return verify_rows(snapshot(engine), checkpoint, trust, head)
    except Exception:
        raise IntegrityError() from None
    finally:
        if engine is not None:
            engine.dispose()
