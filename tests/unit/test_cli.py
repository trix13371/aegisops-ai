from __future__ import annotations

import pytest
from typer.testing import CliRunner

from aegisops.cli.app import app
from aegisops.db.health import DatabaseHealthError

runner = CliRunner()
pytestmark = pytest.mark.unit


def test_doctor_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AEGISOPS_DATABASE_URL",
        "postgresql+psycopg://aegisops_app:canary@127.0.0.1:5432/aegisops_local",
    )
    monkeypatch.setattr("aegisops.cli.app.check_database", lambda url: None)
    res = runner.invoke(app, ["doctor"])
    assert res.exit_code == 0
    assert "doctor: OK" in res.output


def test_doctor_health_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AEGISOPS_DATABASE_URL",
        "postgresql+psycopg://aegisops_app:canary@127.0.0.1:5432/aegisops_local",
    )

    def _raise_err(url: str) -> None:
        raise DatabaseHealthError("unreachable")

    monkeypatch.setattr("aegisops.cli.app.check_database", _raise_err)
    res = runner.invoke(app, ["doctor"])
    assert res.exit_code == 1
    assert "doctor: FAILED" in res.output


def test_doctor_missing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AEGISOPS_DATABASE_URL", raising=False)
    res = runner.invoke(app, ["doctor"])
    assert res.exit_code == 1
    assert "doctor: FAILED (local database not configured)" in res.output


def test_cli_has_no_execution_options() -> None:
    for sub in ["apply", "execute", "fix", "cluster"]:
        res = runner.invoke(app, [sub])
        assert res.exit_code != 0


def test_doctor_has_no_cluster_override_options() -> None:
    help_text = runner.invoke(app, ["doctor", "--help"]).output
    for option in ("--kubeconfig", "--context", "--server"):
        assert option not in help_text
    result = runner.invoke(app, ["doctor", "--source", "production"])
    assert result.exit_code == 1
