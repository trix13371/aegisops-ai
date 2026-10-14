"""An unavailable durable audit must not produce successful review output."""

import json

import pytest
from typer.testing import CliRunner

from aegisops.audit.base import AuditStoreError
from aegisops.cli.app import app

pytestmark = pytest.mark.safety


def test_cli_missing_database_fails_without_model_or_secret(monkeypatch):
    monkeypatch.delenv("AEGISOPS_DATABASE_URL", raising=False)
    monkeypatch.setenv("AEGISOPS_LLM_API_KEY", "fake-sensitive-provider-key")
    result = CliRunner().invoke(app, ["review", "--llm", "openrouter"])
    assert result.exit_code == 1 and "review: FAILED" in result.output
    assert "fake-sensitive-provider-key" not in result.output
    assert '"status": "completed"' not in result.output


def test_cli_failed_insert_does_not_print_completed_record(monkeypatch):
    class FailingStore:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def append(self, record):
            raise AuditStoreError()

    monkeypatch.setattr("aegisops.cli.app.PostgresAuditStore", FailingStore)
    result = CliRunner().invoke(app, ["review"])
    assert result.exit_code == 1 and "review: FAILED" in result.output
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.output)


def test_audit_store_rejects_remote_or_owner_url_without_connect(monkeypatch):
    from aegisops.audit.postgres import PostgresAuditStore

    def forbidden_connect(*args, **kwargs):
        raise AssertionError("invalid URL reached database")

    monkeypatch.setattr("aegisops.audit.postgres.check_database", forbidden_connect)
    for url in (
        "postgresql+psycopg://aegisops_app:fake@198.51.100.1:5432/aegisops_local",
        "postgresql+psycopg://aegisops_owner:fake@127.0.0.1:5432/aegisops_local",
    ):
        with pytest.raises(AuditStoreError, match="^audit operation failed$"):
            PostgresAuditStore(url)
