"""Exercise real PostgreSQL grants and migration parity, never SQLite."""

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from aegisops.db.health import DatabaseHealthError, check_database
from aegisops.db.models import AuditRun, Base

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    value = os.environ.get("AEGISOPS_DATABASE_URL")
    if not value:
        pytest.skip("local PostgreSQL app connection not configured")
    return value


def test_doctor_real_database(database_url: str) -> None:
    check_database(database_url)


def test_migration_matches_model(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_server_default": True})
            assert compare_metadata(context, Base.metadata) == []
    finally:
        engine.dispose()


def test_application_can_insert_and_select(database_url: str) -> None:
    engine = create_engine(database_url)
    identifier = uuid.uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(AuditRun).values(
                    run_id=identifier,
                    started_at=datetime.now(UTC),
                    status="completed",
                    origin="synthetic_fixture",
                    payload_json={"execution_performed": False},
                )
            )
        with engine.connect() as connection:
            row = connection.execute(select(AuditRun.run_id).where(AuditRun.run_id == identifier))
            assert row.scalar_one() == identifier
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE public.audit_runs SET status = 'failed' WHERE false",
        "DELETE FROM public.audit_runs WHERE false",
        "TRUNCATE public.audit_runs",
        "CREATE TABLE public.forbidden_table (id integer)",
        "CREATE TEMP TABLE forbidden_temporary (id integer)",
        "SET ROLE aegisops_owner",
        "ALTER TABLE public.audit_runs ADD COLUMN forbidden integer",
    ],
)
def test_application_mutations_denied(database_url: str, statement: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            with pytest.raises(DBAPIError) as failure:
                connection.execute(text(statement))
            assert getattr(failure.value.orig, "sqlstate", None) == "42501"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "invalid",
    [
        {"status": "anything"},
        {"autonomy_level": "L2"},
        {"origin": "production"},
        {"schema_version": 0},
    ],
)
def test_database_rejects_invalid_audit_contract(
    database_url: str, invalid: dict[str, Any]
) -> None:
    values: dict[str, Any] = {
        "run_id": uuid.uuid4(),
        "started_at": datetime.now(UTC),
        "status": "completed",
        "autonomy_level": "L0",
        "origin": "synthetic_fixture",
        "schema_version": 1,
        "payload_json": {},
    }
    values.update(invalid)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            with pytest.raises(DBAPIError) as failure:
                connection.execute(insert(AuditRun).values(**values))
            assert getattr(failure.value.orig, "sqlstate", None) == "23514"
    finally:
        engine.dispose()


def test_doctor_detects_excess_grants(database_url: str) -> None:
    owner_url = os.environ.get("AEGISOPS_MIGRATION_URL")
    if not owner_url:
        pytest.skip("local migration owner not configured")
    owner = create_engine(owner_url)
    try:
        with owner.begin() as connection:
            connection.execute(text("GRANT UPDATE ON public.audit_runs TO aegisops_app"))
        try:
            with pytest.raises(DatabaseHealthError, match="privilege"):
                check_database(database_url)
        finally:
            with owner.begin() as connection:
                connection.execute(text("REVOKE UPDATE ON public.audit_runs FROM aegisops_app"))
    finally:
        owner.dispose()


@pytest.mark.parametrize("danger", ["membership", "role_attribute", "temp_privilege"])
def test_doctor_rejects_privilege_escalation(database_url: str, danger: str) -> None:
    bootstrap_password = os.environ.get("AEGISOPS_BOOTSTRAP_PASSWORD")
    if not bootstrap_password:
        pytest.skip("local bootstrap identity not configured")
    admin_url = make_url(database_url).set(
        username="aegisops_bootstrap", password=bootstrap_password
    )
    database_name = "aegisops_test" if admin_url.database == "aegisops_test" else "aegisops_local"
    statements = {
        "membership": (
            "GRANT aegisops_owner TO aegisops_app",
            "REVOKE aegisops_owner FROM aegisops_app",
        ),
        "role_attribute": (
            "ALTER ROLE aegisops_app CREATEDB",
            "ALTER ROLE aegisops_app NOCREATEDB",
        ),
        "temp_privilege": (
            "GRANT TEMP ON DATABASE " + database_name + " TO aegisops_app",
            "REVOKE TEMP ON DATABASE " + database_name + " FROM aegisops_app",
        ),
    }
    grant, revoke = statements[danger]
    admin = create_engine(admin_url)
    try:
        with admin.begin() as connection:
            connection.execute(text(grant))
        try:
            with pytest.raises(DatabaseHealthError):
                check_database(database_url)
        finally:
            with admin.begin() as connection:
                connection.execute(text(revoke))
    finally:
        admin.dispose()
