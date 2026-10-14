"""Create test roles on the disposable CI service; refuses outside GitHub CI."""

import os

import psycopg

if os.environ.get("GITHUB_ACTIONS") != "true":
    raise SystemExit("Only the isolated GitHub Actions test service is supported")

with psycopg.connect(
    host="127.0.0.1",
    port=5432,
    dbname="aegisops_test",
    user="aegisops_bootstrap",
    password="ci-local-only",  # noqa: S106 - disposable CI fixture, never a user credential
) as connection:
    connection.execute(
        "CREATE ROLE aegisops_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
        "NOINHERIT NOBYPASSRLS PASSWORD 'ci-local-owner'"
    )
    connection.execute(
        "CREATE ROLE aegisops_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
        "NOINHERIT NOBYPASSRLS PASSWORD 'ci-local-app'"
    )
    connection.execute("REVOKE ALL ON DATABASE aegisops_test FROM PUBLIC")
    connection.execute("GRANT CONNECT ON DATABASE aegisops_test TO aegisops_owner, aegisops_app")
    connection.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    connection.execute("ALTER SCHEMA public OWNER TO aegisops_owner")
    connection.execute("GRANT USAGE ON SCHEMA public TO aegisops_app")
