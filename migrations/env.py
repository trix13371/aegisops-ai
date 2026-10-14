"""Owner-only migrations: never take the application connection implicitly."""

import os
from urllib.parse import unquote

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from aegisops.db.models import Base


def migration_url() -> str:
    value = os.environ.get("AEGISOPS_MIGRATION_URL", "")
    try:
        parsed = make_url(value)
        valid = (
            parsed.drivername == "postgresql+psycopg"
            and parsed.host in {"127.0.0.1", "::1"}
            and parsed.username == "aegisops_owner"
            and parsed.database in {"aegisops_local", "aegisops_test"}
            and bool(parsed.password)
            and parsed.port is not None
            and 0 < parsed.port < 65536
            and not parsed.query
            and "\n" not in value
            and "\r" not in value
            and not any(ord(char) < 32 or ord(char) == 127 for char in unquote(value))
        )
    except (ValueError, TypeError, ArgumentError):
        valid = False
    if not valid:
        raise RuntimeError("Configure a local owner migration connection") from None
    return value


target_metadata = Base.metadata
if context.is_offline_mode():
    context.configure(url=migration_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(migration_url(), connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
