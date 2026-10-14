"""Early storage failures, partial batches and read-only CLI bounds."""

import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from aegisops.audit.base import AuditStoreError
from aegisops.cli.app import app
from aegisops.domain.models import Origin, Recommendation
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.pipeline.engine import Pipeline
from aegisops.sources.pipeline import SyntheticBatchSource
from aegisops.sources.safe_client import ReadOnlyError

pytestmark = pytest.mark.safety


@pytest.mark.parametrize("mode", ["empty", "origin", "source_error"])
def test_early_write_failure_is_attempted_once_and_propagates(mode):
    class Source:
        def collect(self):
            if mode == "empty":
                return []
            if mode == "source_error":
                raise ReadOnlyError("password=FAKE_SOURCE_CANARY")
            snapshot = SyntheticBatchSource("crashloop-oom").collect()[0]
            payload = snapshot.model_dump()
            payload["origin"] = "local_kind"
            return [type(snapshot).model_validate(payload)]

    class BrokenStore:
        calls = 0

        def append(self, record):
            self.calls += 1
            raise AuditStoreError()

    store = BrokenStore()
    client = FakeLLMClient()
    with pytest.raises(AuditStoreError):
        Pipeline(AnalysisEngine(client), store).run(Source(), origin=Origin.SYNTHETIC_FIXTURE)
    assert store.calls == 1 and client.calls == 0


def test_later_insert_failure_keeps_earlier_record_without_returning_success():
    class PartialStore:
        def __init__(self):
            self.rows = []

        def append(self, record):
            if self.rows:
                raise AuditStoreError()
            self.rows.append(record)

    store = PartialStore()
    with pytest.raises(AuditStoreError):
        Pipeline(AnalysisEngine(FakeLLMClient()), store).run(
            SyntheticBatchSource(), origin=Origin.SYNTHETIC_FIXTURE
        )
    assert len(store.rows) == 1 and store.rows[0].status == "completed"


def test_run_failed_audit_never_prints_completed_json(monkeypatch):
    class Store:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def append(self, record):
            raise AuditStoreError()

    monkeypatch.setattr("aegisops.cli.app.PostgresAuditStore", Store)
    result = CliRunner().invoke(app, ["run"])
    assert result.exit_code == 1 and result.output.startswith("run: FAILED")
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.output)


@pytest.mark.parametrize(
    "arguments",
    [
        ["run", "--source", "production"],
        ["run", "--source", "local-kind", "--scenario", ""],
        ["audit", "show", "invalid-uuid"],
        ["audit", "list", "--limit", "101"],
    ],
)
def test_invalid_cli_arguments_cannot_access_local_services(monkeypatch, arguments):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid argument reached service")

    monkeypatch.setattr("aegisops.cli.app.PostgresAuditStore", forbidden)
    monkeypatch.setattr("aegisops.cli.app.PostgresAuditReader", forbidden)
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code != 0


@pytest.mark.parametrize("failure,expected", [("technical", "failed"), ("policy", "blocked")])
def test_batch_preserves_successes_and_aggregates_non_success(failure, expected):
    class MixedAnalysis(AnalysisEngine):
        def analyze(self, incident, context):
            if incident.workload_name == "crashloop-oom":
                if failure == "technical":
                    raise RuntimeError("password=FAKE_AGGREGATE_CANARY")
                result = super().analyze(incident, context)
                data = result.recommendation.model_dump()
                data["reversibility_class"] = "R3"
                return replace(result, recommendation=Recommendation.model_validate(data))
            return super().analyze(incident, context)

    class Store:
        def __init__(self):
            self.records = []

        def append(self, record):
            self.records.append(record)

    store = Store()
    result = Pipeline(MixedAnalysis(FakeLLMClient()), store).run(
        SyntheticBatchSource(), origin=Origin.SYNTHETIC_FIXTURE
    )
    assert result.status == expected
    assert len(store.records) == result.incident_count == 6
    assert sum(record.status == "completed" for record in store.records) == 5
    assert "FAKE_AGGREGATE_CANARY" not in result.model_dump_json()
