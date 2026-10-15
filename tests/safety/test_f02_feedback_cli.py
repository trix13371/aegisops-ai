"""No feedback success without storage, execution surface or model involvement."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from aegisops.cli.app import app
from aegisops.feedback.models import Assessment, FeedbackError
from aegisops.feedback.postgres import PostgresFeedbackStore

pytestmark = pytest.mark.safety


def test_failed_durable_write_prints_no_record_or_raw_error(monkeypatch):
    store = Mock()
    store.__enter__ = Mock(return_value=store)
    store.__exit__ = Mock(return_value=None)
    store.add.side_effect = FeedbackError()
    monkeypatch.setattr("aegisops.cli.app.PostgresFeedbackStore", lambda: store)
    result = CliRunner().invoke(
        app,
        [
            "feedback",
            "add",
            str(uuid4()),
            "--reviewer",
            "operator",
            "--assessment",
            "useful",
            "--comment",
            "password=F02_FAKE_FAILURE_CANARY",
        ],
    )
    assert result.exit_code == 1 and "feedback: FAILED" in result.output
    assert "F02_FAKE_FAILURE_CANARY" not in result.output
    assert '"feedback_id"' not in result.output


@pytest.mark.parametrize("command", ["list", "summary"])
def test_read_failure_has_no_partial_output(monkeypatch, command):
    store = Mock()
    store.__enter__ = Mock(return_value=store)
    store.__exit__ = Mock(return_value=None)
    getattr(store, command).side_effect = FeedbackError()
    monkeypatch.setattr("aegisops.cli.app.PostgresFeedbackStore", lambda: store)
    result = CliRunner().invoke(app, ["feedback", command])
    assert result.exit_code == 1 and result.output.startswith("feedback: FAILED")


def test_invalid_feedback_rejected_before_database_query():
    # Avoid construction/health connection; only exercise the preflight boundary.
    store = object.__new__(PostgresFeedbackStore)
    store._engine = Mock()
    for fields in ({"comment": "x" * 2001}, {"reviewer": "invalid user"}):
        values = dict(reviewer="operator", assessment=Assessment.USEFUL)
        values.update(fields)
        with pytest.raises(FeedbackError):
            store.add(uuid4(), **values)
    store._engine.begin.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        ["add", "not-a-uuid", "--reviewer", "operator", "--assessment", "useful"],
        ["list", "--run-id", "not-a-uuid"],
        ["summary", "--source", "production"],
        ["approve"],
        ["execute"],
        ["delete"],
    ],
)
def test_invalid_options_and_mutation_commands_do_not_open_store(monkeypatch, arguments):
    factory = Mock()
    monkeypatch.setattr("aegisops.cli.app.PostgresFeedbackStore", factory)
    result = CliRunner().invoke(app, ["feedback", *arguments])
    assert result.exit_code != 0
    factory.assert_not_called()


def test_feedback_remote_or_owner_url_never_connects(monkeypatch):
    health = Mock()
    monkeypatch.setattr("aegisops.audit.postgres.check_database", health)
    for url in (
        "postgresql+psycopg://aegisops_app:fake@198.51.100.1:5432/aegisops_local",
        "postgresql+psycopg://aegisops_owner:fake@127.0.0.1:5432/aegisops_local",
    ):
        with pytest.raises(FeedbackError):
            PostgresFeedbackStore(url)
    health.assert_not_called()
