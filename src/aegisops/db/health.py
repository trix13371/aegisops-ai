"""Read-only database health checks for AegisOps S0-01.

All checks are read-only. No inserts, updates, or schema changes occur.
Error messages are fixed and safe; they never include exception text or the
connection URL. This check is not a comprehensive security attestation.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

_EXPECTED_USER = "aegisops_app"
_ALLOWED_DATABASES = frozenset({"aegisops_local", "aegisops_test"})
_EXPECTED_ALEMBIC_VERSION = "0002_feedback"
_APP_TABLES = ("public.audit_runs", "public.recommendation_feedback")

_SAFE_CONNECT_FAILED = "database health check failed: could not connect"
_SAFE_QUERY_FAILED = "database health check failed: query error"
_SAFE_USER_MISMATCH = "database health check failed: unexpected current user"
_SAFE_DB_MISMATCH = "database health check failed: unexpected current database"
_SAFE_ALEMBIC_MISSING = "database health check failed: alembic_version missing"
_SAFE_ALEMBIC_MULTI = "database health check failed: alembic_version not single"
_SAFE_ALEMBIC_VALUE = "database health check failed: unexpected alembic version"
_SAFE_TABLE_MISSING = "database health check failed: required table missing"
_SAFE_PRIVILEGE = "database health check failed: unexpected table privileges"
_SAFE_ROLE_ATTRS = "database health check failed: unsafe role attributes"
_SAFE_ROLE_MEMBERSHIP = "database health check failed: unexpected role membership"
_SAFE_OWNERSHIP = "database health check failed: unexpected ownership"
_SAFE_SCHEMA_CREATE = "database health check failed: schema create not denied"
_SAFE_DB_PRIVILEGE = "database health check failed: unexpected database privileges"


class DatabaseHealthError(RuntimeError):
    """Raised when a read-only database health check fails."""


def _engine(url: str) -> Engine:
    return create_engine(
        url,
        connect_args={"connect_timeout": 3},
        pool_pre_ping=True,
    )


def _scalar(conn: Connection, statement: str) -> object:
    return conn.execute(text(statement)).scalar()


def _check_identity(conn: Connection) -> None:
    current_user = _scalar(conn, "SELECT current_user")
    if current_user != _EXPECTED_USER:
        raise DatabaseHealthError(_SAFE_USER_MISMATCH)

    current_database = _scalar(conn, "SELECT current_database()")
    if current_database not in _ALLOWED_DATABASES:
        raise DatabaseHealthError(_SAFE_DB_MISMATCH)


def _check_alembic(conn: Connection) -> None:
    exists = _scalar(
        conn,
        "SELECT to_regclass('public.alembic_version') IS NOT NULL",
    )
    if not exists:
        raise DatabaseHealthError(_SAFE_ALEMBIC_MISSING)

    rows = conn.execute(text("SELECT version_num FROM public.alembic_version")).all()
    if len(rows) != 1:
        raise DatabaseHealthError(_SAFE_ALEMBIC_MULTI)
    if rows[0][0] != _EXPECTED_ALEMBIC_VERSION:
        raise DatabaseHealthError(_SAFE_ALEMBIC_VALUE)


def _check_audit_runs_table(conn: Connection) -> None:
    for table in _APP_TABLES:
        exists = conn.execute(
            text("SELECT to_regclass(:table) IS NOT NULL"), {"table": table}
        ).scalar()
        if not exists:
            raise DatabaseHealthError(_SAFE_TABLE_MISSING)


def _check_table_privileges(conn: Connection) -> None:
    for table in _APP_TABLES:
        for privilege in (
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
        ):
            granted = conn.execute(
                text("SELECT has_table_privilege(current_user, :table, :priv)"),
                {"table": table, "priv": privilege},
            ).scalar()
            if bool(granted) != (privilege in {"SELECT", "INSERT"}):
                raise DatabaseHealthError(_SAFE_PRIVILEGE)


def _check_role_attributes(conn: Connection) -> None:
    row = conn.execute(
        text(
            "SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user"
        )
    ).first()
    if row is None:
        raise DatabaseHealthError(_SAFE_ROLE_ATTRS)
    if any(bool(value) for value in row):
        raise DatabaseHealthError(_SAFE_ROLE_ATTRS)


def _check_role_membership(conn: Connection) -> None:
    member_of = _scalar(
        conn,
        "SELECT EXISTS ("
        "SELECT 1 FROM pg_auth_members m "
        "JOIN pg_roles r ON r.oid = m.roleid "
        "WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = current_user)"
        ")",
    )
    if member_of:
        raise DatabaseHealthError(_SAFE_ROLE_MEMBERSHIP)


def _check_ownership(conn: Connection) -> None:
    for table in _APP_TABLES:
        table_owner = conn.execute(
            text("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = to_regclass(:table)"),
            {"table": table},
        ).scalar()
        if table_owner == _EXPECTED_USER:
            raise DatabaseHealthError(_SAFE_OWNERSHIP)

    schema_owner = _scalar(
        conn,
        "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public'",
    )
    if schema_owner == _EXPECTED_USER:
        raise DatabaseHealthError(_SAFE_OWNERSHIP)

    database_owner = _scalar(
        conn,
        "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()",
    )
    if database_owner == _EXPECTED_USER:
        raise DatabaseHealthError(_SAFE_OWNERSHIP)


def _check_schema_create_denied(conn: Connection) -> None:
    can_create = _scalar(
        conn,
        "SELECT has_schema_privilege(current_user, 'public', 'CREATE')",
    )
    if can_create:
        raise DatabaseHealthError(_SAFE_SCHEMA_CREATE)


def _check_database_privileges(conn: Connection) -> None:
    can_create = _scalar(
        conn,
        "SELECT has_database_privilege(current_user, current_database(), 'CREATE')",
    )
    can_temp = _scalar(
        conn,
        "SELECT has_database_privilege(current_user, current_database(), 'TEMP')",
    )
    if can_create or can_temp:
        raise DatabaseHealthError(_SAFE_DB_PRIVILEGE)


def check_database(url: str) -> None:
    """Run read-only health checks against the local database.

    Raises :class:`DatabaseHealthError` with a fixed safe message on any
    failure. The engine is always disposed.
    """

    try:
        engine = _engine(url)
    except SQLAlchemyError:
        raise DatabaseHealthError(_SAFE_CONNECT_FAILED) from None

    try:
        try:
            with engine.connect() as conn:
                _check_identity(conn)
                _check_alembic(conn)
                _check_audit_runs_table(conn)
                _check_table_privileges(conn)
                _check_role_attributes(conn)
                _check_role_membership(conn)
                _check_ownership(conn)
                _check_schema_create_denied(conn)
                _check_database_privileges(conn)
        except DatabaseHealthError:
            raise
        except SQLAlchemyError:
            raise DatabaseHealthError(_SAFE_QUERY_FAILED) from None
    finally:
        engine.dispose()
