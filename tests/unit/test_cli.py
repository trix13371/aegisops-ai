from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from aegisops.cli.app import app
from aegisops.db.health import DatabaseHealthError
from aegisops.sources.kubernetes_readonly import CollectionError
from aegisops.sources.synthetic import ScenarioRepository

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


@pytest.mark.parametrize("failure", [False, True])
def test_observe_hides_raw_content_and_closes_client(monkeypatch, failure):
    reader = MagicMock()
    source = MagicMock()
    snapshot = ScenarioRepository().load("inj-secret-canary")[1]
    assert snapshot.log_tail
    source.collect.return_value = [snapshot]
    if failure:
        source.collect.side_effect = CollectionError("synthetic error canary")
    monkeypatch.setattr("aegisops.cli.app.load_reader_identity", lambda: object())
    monkeypatch.setattr("aegisops.cli.app.ReadOnlyClient", lambda identity: reader)
    monkeypatch.setattr("aegisops.cli.app.KubernetesReadOnlySource", lambda *a, **kw: source)
    monkeypatch.delenv("AEGISOPS_DATABASE_URL", raising=False)
    result = runner.invoke(app, ["observe", "--include-logs"])
    reader.close.assert_called_once()
    assert snapshot.log_tail not in result.output
    for event in snapshot.events:
        assert event.message not in result.output
    assert "synthetic error canary" not in result.output
    if failure:
        assert result.exit_code == 1
        assert result.output.strip() == "observe: FAILED (local kind collection rejected)"
    else:
        assert result.exit_code == 0
        projected = json.loads(result.output)
        assert set(projected) == {
            "origin",
            "snapshots",
            "containers",
            "events",
            "log_tails",
            "execution_available",
        }
        assert projected["snapshots"] == 1
        assert projected["execution_available"] is False


def test_detect_all_fixtures_without_database(monkeypatch):
    monkeypatch.delenv("AEGISOPS_DATABASE_URL", raising=False)
    result = runner.invoke(app, ["detect"])
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["snapshots"] == 10
    assert len(output["incidents"]) == 6
    assert output["execution_available"] is False


@pytest.mark.parametrize(
    "arguments",
    [
        ["--source", "production"],
        ["--scenario", "../private"],
        ["--source", "local-kind", "--scenario", "crashloop-exit-1"],
    ],
)
def test_detect_invalid_source_or_scenario_cannot_open_reader(monkeypatch, arguments):
    reader_factory = MagicMock()
    monkeypatch.setattr("aegisops.cli.app.ReadOnlyClient", reader_factory)
    result = runner.invoke(app, ["detect", *arguments])
    assert result.exit_code == 1
    assert result.output.strip() == "detect: FAILED (observation or context rejected)"
    reader_factory.assert_not_called()
