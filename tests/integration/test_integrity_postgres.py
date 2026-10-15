"""Real physical-row verification; tamper exercises are rolled back, never committed."""

import json
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, update
from typer.testing import CliRunner

from aegisops.audit.postgres import PostgresAuditStore, create_audit_engine
from aegisops.cli.app import app
from aegisops.db.models import AuditRun
from aegisops.domain.common import utc_now
from aegisops.domain.models import AuditRecord
from aegisops.integrity.chain import checked_rows, verify_rows
from aegisops.integrity.models import IntegrityError
from aegisops.integrity.postgres import read_rows, snapshot
from aegisops.integrity.repository import load_checkpoint

pytestmark = pytest.mark.integration


@pytest.fixture
def integrity_engine():
    if not os.environ.get("AEGISOPS_DATABASE_URL"):
        pytest.skip("local PostgreSQL not configured")
    engine = create_audit_engine()
    try:
        yield engine
    finally:
        engine.dispose()


def append_observation():
    now = utc_now()
    record = AuditRecord(
        run_id=uuid4(),
        started_at=now,
        completed_at=now,
        status="completed",
        origin="synthetic_fixture",
        observation={"source": "synthetic", "snapshot_count": 0},
    )
    with PostgresAuditStore() as store:
        store.append(record)
    return record


def test_real_cli_snapshot_new_record_and_reanchor(
    integrity_engine, integrity_signer, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    state, key = tmp_path / ".local/audit-integrity", tmp_path / "private/key.blob"
    integrity_signer.initialize(state, key)
    before = snapshot(integrity_engine)
    assert (
        checked_rows(before) == before
    )  # PostgreSQL byte order equals canonical UUID string order.
    head = integrity_signer.publish(before, state, key)
    result = CliRunner().invoke(app, ["audit", "verify"])
    assert result.exit_code == 0 and json.loads(result.output)["status"] == "verified"
    record = append_observation()
    after = snapshot(integrity_engine)
    assert len(after) == len(before) + 1 and record.run_id in {row.run_id for row in after}
    result = CliRunner().invoke(app, ["audit", "verify"])
    assert result.exit_code == 1 and json.loads(result.output)["unanchored_records"] == 1
    new_head = integrity_signer.publish(after, state, key)
    assert head.checkpoint_hash != new_head.checkpoint_hash
    result = CliRunner().invoke(app, ["audit", "verify"])
    assert result.exit_code == 0 and json.loads(result.output)["status"] == "verified"


@pytest.mark.parametrize("change", ["payload", "metadata", "delete", "oversize"])
def test_actual_database_tampering_is_detected_and_rolled_back(
    integrity_engine,
    integrity_signer,
    tmp_path,
    change,
):
    owner_url = os.environ.get("AEGISOPS_MIGRATION_URL")
    if not owner_url:
        pytest.skip("local migration owner not configured")
    record = append_observation()
    original = snapshot(integrity_engine)
    state, key = tmp_path / "points", tmp_path / "private/key.blob"
    integrity_signer.initialize(state, key)
    integrity_signer.publish(original, state, key)
    checkpoint, trust, head = load_checkpoint(state)
    owner = create_engine(owner_url, hide_parameters=True)
    try:
        with owner.connect() as connection:
            transaction = connection.begin()
            try:
                target = AuditRun.run_id == record.run_id
                if change == "delete":
                    connection.execute(delete(AuditRun).where(target))
                elif change == "metadata":
                    connection.execute(
                        update(AuditRun)
                        .where(target)
                        .values(started_at=record.started_at + timedelta(seconds=1))
                    )
                else:
                    payload = {"untrusted": "password=ONLY_TEST_CANARY"}
                    if change == "oversize":
                        payload = {"too_large": "x" * (1024 * 1024 + 1)}
                    connection.execute(update(AuditRun).where(target).values(payload_json=payload))
                if change == "oversize":
                    with pytest.raises(IntegrityError):
                        read_rows(connection)
                else:
                    report = verify_rows(read_rows(connection), checkpoint, trust, head)
                    assert report.status == "failed"
                    assert report.missing_records == (change == "delete")
                    assert report.modified_records == (change != "delete")
                    assert "ONLY_TEST_CANARY" not in report.model_dump_json()
            finally:
                transaction.rollback()
        assert verify_rows(snapshot(integrity_engine), checkpoint, trust, head).status == "verified"
    finally:
        owner.dispose()


@pytest.mark.parametrize("limit", ["rows", "bytes"])
def test_oversized_history_is_rejected_without_partial_success(
    integrity_engine, monkeypatch, limit
):
    append_observation()
    monkeypatch.setattr(
        "aegisops.integrity.postgres." + ("MAX_ROWS" if limit == "rows" else "MAX_SNAPSHOT_BYTES"),
        0,
    )
    with pytest.raises(IntegrityError, match="^audit integrity verification rejected$"):
        snapshot(integrity_engine)
