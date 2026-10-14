"""Full CLI with real local PostgreSQL, without a paid LLM or Kubernetes."""

import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, insert
from typer.testing import CliRunner

from aegisops.audit.base import AuditStoreError
from aegisops.audit.reader import PostgresAuditReader
from aegisops.cli.app import app
from aegisops.db.models import AuditRun
from aegisops.domain.common import utc_now
from aegisops.domain.models import AuditRecord
from aegisops.evaluation.suite import EvaluationReport
from aegisops.pipeline.models import PipelineResult
from aegisops.sources.env_guard import EnvironmentGuardError

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


@pytest.fixture
def url():
    value = os.environ.get("AEGISOPS_DATABASE_URL")
    if not value:
        pytest.skip("local PostgreSQL not configured")
    return value


@pytest.mark.parametrize("scenario", ["inj-secret-canary", "neg-healthy"])
def test_full_run_readback_and_cli_show(url, scenario):
    cli = CliRunner()
    result = cli.invoke(app, ["run", "--scenario", scenario])
    assert result.exit_code == 0, result.output
    batch = PipelineResult.model_validate_json(result.output)
    assert batch.incident_count == (scenario == "inj-secret-canary")
    assert "DEMO_CANARY_NOT_A_REAL_CREDENTIAL" not in result.output
    for record in batch.records:
        shown = cli.invoke(app, ["audit", "show", str(record.run_id)])
        assert shown.exit_code == 0
        assert json.loads(shown.output) == record.model_dump(mode="json")
    listed = cli.invoke(app, ["audit", "list", "--limit", "1"])
    assert listed.exit_code == 0 and len(json.loads(listed.output)) == 1


def test_guard_rejection_is_audited_before_llm(url, monkeypatch):
    def rejected(self):
        raise EnvironmentGuardError("password=FAKE_GUARD_CANARY")

    def forbidden_model(*args, **kwargs):
        pytest.fail("blocked source reached provider")

    monkeypatch.setattr("aegisops.sources.pipeline.LocalKindSource.collect", rejected)
    monkeypatch.setattr("aegisops.llm.fake.FakeLLMClient.generate_structured", forbidden_model)
    result = CliRunner().invoke(app, ["run", "--source", "local-kind"])
    assert result.exit_code == 1
    batch = PipelineResult.model_validate_json(result.output)
    assert batch.status == "blocked" and batch.incident_count == 0
    assert "FAKE_GUARD_CANARY" not in result.output
    with PostgresAuditReader(url) as reader:
        assert reader.show(batch.records[0].run_id) == batch.records[0]


def test_eval_cli_persists_all_ten_scenario_records(url):
    result = CliRunner().invoke(app, ["eval"])
    assert result.exit_code == 0
    report = EvaluationReport.model_validate_json(result.output)
    assert report.passed and report.fake_calls == 6
    with PostgresAuditReader(url) as reader:
        for case in report.scenarios:
            assert len(case.audit_run_ids) == 1
            record = reader.show(case.audit_run_ids[0])
            assert record.status == "completed"
            assert (record.incident is not None) == case.detected


def test_reader_limits_unknown_uuid_and_legacy_invalid_payload(url):
    identifier = uuid4()
    with PostgresAuditReader(url) as reader:
        for limit in (0, 101, True):
            with pytest.raises(AuditStoreError):
                reader.list(limit)
        with pytest.raises(AuditStoreError):
            reader.show(identifier)
    # Deliberate legacy S0-01-shaped row: valid SQL, not a valid current AuditRecord.
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(AuditRun).values(
                    run_id=identifier,
                    started_at=utc_now(),
                    status="completed",
                    origin="synthetic_fixture",
                    payload_json={"text": "password=FAKE_LEGACY_CANARY"},
                )
            )
        with PostgresAuditReader(url) as reader:
            with pytest.raises(AuditStoreError, match="^audit operation failed$"):
                reader.show(identifier)
    finally:
        engine.dispose()


@pytest.mark.kind_live
def test_live_kind_pipeline_through_durable_audit(url):
    if os.environ.get("AEGISOPS_KIND_LIVE") != "1":
        pytest.skip("explicit live kind pipeline disabled")
    result = CliRunner().invoke(app, ["run", "--source", "local-kind"])
    assert result.exit_code == 0, result.output
    batch = PipelineResult.model_validate_json(result.output)
    assert batch.origin == "local_kind" and batch.snapshot_count > 0
    assert batch.incident_count > 0 and not batch.execution_performed
    with PostgresAuditReader(url) as reader:
        for record in batch.records:
            assert reader.show(record.run_id) == record


def test_reader_checks_requested_uuid_matches_payload_uuid(url):
    row_id, payload_id = uuid4(), uuid4()
    now = utc_now()
    record = AuditRecord(
        run_id=payload_id,
        started_at=now,
        completed_at=now,
        status="completed",
        origin="synthetic_fixture",
        observation={"source": "synthetic", "snapshot_count": 0},
    )
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(AuditRun).values(
                    run_id=row_id,
                    started_at=now,
                    completed_at=now,
                    status="completed",
                    origin="synthetic_fixture",
                    payload_json=record.model_dump(mode="json"),
                )
            )
        with PostgresAuditReader(url) as reader:
            with pytest.raises(AuditStoreError):
                reader.show(row_id)
    finally:
        engine.dispose()


def test_reader_displays_historical_policy_without_replaying_current_policy(url):
    from aegisops.audit.validation import prepare_record

    result = CliRunner().invoke(app, ["run", "--scenario", "crashloop-oom"])
    assert result.exit_code == 0
    original = PipelineResult.model_validate_json(result.output).records[0]
    raw = original.model_dump()
    raw["run_id"] = uuid4()
    raw["policy_decision"]["policy_version"] = "historical-policy"
    historical = AuditRecord.model_validate(raw)
    with pytest.raises(AuditStoreError):
        prepare_record(historical)
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(AuditRun).values(
                    run_id=historical.run_id,
                    started_at=historical.started_at,
                    completed_at=historical.completed_at,
                    status=historical.status.value,
                    origin=historical.origin.value,
                    payload_json=historical.model_dump(mode="json"),
                    policy_version="historical-policy",
                )
            )
        with PostgresAuditReader(url) as reader:
            assert reader.show(historical.run_id) == historical
    finally:
        engine.dispose()
