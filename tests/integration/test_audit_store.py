"""Real INSERT/SELECT, duplicate rollback and CLI durability under the app role."""

import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from typer.testing import CliRunner

from aegisops.audit.base import AuditStoreError
from aegisops.audit.postgres import PostgresAuditStore
from aegisops.cli.app import app
from aegisops.db.models import AuditRun
from aegisops.detection.engine import DetectionEngine
from aegisops.domain.models import AuditRecord
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.review.service import ReviewService
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = pytest.mark.integration


@pytest.fixture
def url():
    value = os.environ.get("AEGISOPS_DATABASE_URL")
    if not value:
        pytest.skip("local PostgreSQL app connection not configured")
    return value


def test_real_audit_roundtrip_indexes_and_duplicate_rollback(url):
    source = DetectionEngine().detect([ScenarioRepository().load("inj-secret-canary")[1]])[0]
    with PostgresAuditStore(url) as store:
        record = ReviewService(AnalysisEngine(FakeLLMClient()), store).review(source)
        with pytest.raises(AuditStoreError, match="^audit operation failed$"):
            store.append(record)
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as conn:
            row = (
                conn.execute(select(AuditRun.__table__).where(AuditRun.run_id == record.run_id))
                .mappings()
                .one()
            )
        assert AuditRecord.model_validate(row["payload_json"]) == record
        assert row["incident_id"] == record.incident.incident_id
        assert row["context_hash"] == record.context_hash
        assert row["prompt_hash"] == record.llm.prompt_hash
        assert row["policy_version"] == record.policy_decision.policy_version
        assert row["completed_at"] == record.completed_at
        assert "DEMO_CANARY_NOT_A_REAL_CREDENTIAL" not in json.dumps(row["payload_json"])
        assert record.recommendation.execution_performed is False
    finally:
        engine.dispose()


def test_cli_review_returns_only_committed_record(url):
    result = CliRunner().invoke(app, ["review", "--scenario", "crashloop-oom"])
    assert result.exit_code == 0
    record = AuditRecord.model_validate_json(result.output)
    assert record.status == "completed" and record.dry_run_result.simulated is True
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as conn:
            payload = conn.execute(
                select(AuditRun.payload_json).where(AuditRun.run_id == record.run_id)
            ).scalar_one()
        assert AuditRecord.model_validate(payload) == record
    finally:
        engine.dispose()


def test_invalid_record_is_rejected_before_insert(url):
    identifier = uuid4()
    with PostgresAuditStore(url) as store:
        with pytest.raises(AuditStoreError):
            store.append(AuditRecord.model_construct(run_id=identifier, status="completed"))
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(select(AuditRun.run_id).where(AuditRun.run_id == identifier)).first()
                is None
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("status", ["failed", "blocked"])
def test_early_failure_or_environment_block_can_be_audited_without_incident(url, status):
    now = datetime.now(UTC)
    record = AuditRecord(
        run_id=uuid4(),
        started_at=now,
        completed_at=now,
        status=status,
        origin="synthetic_fixture",
        errors=({"code": "environment_rejected", "message": "Среда не прошла проверку"},),
    )
    with PostgresAuditStore(url) as store:
        store.append(record)
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as conn:
            payload = conn.execute(
                select(AuditRun.payload_json).where(AuditRun.run_id == record.run_id)
            ).scalar_one()
        assert AuditRecord.model_validate(payload) == record
    finally:
        engine.dispose()
