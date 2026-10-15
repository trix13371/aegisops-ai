"""Signed handoff with real PostgreSQL and pinned mTLS OPA; no paid model."""

import os
from datetime import timedelta

import pytest

from aegisops.audit.postgres import PostgresAuditStore
from aegisops.audit.reader import PostgresAuditReader
from aegisops.authorization.http import OpaClient
from aegisops.authorization.pep import DryRunPEP
from aegisops.controlplane.service import IngestionService
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import UnavailableLLMClient
from aegisops.llm.routing import DataClass

pytestmark = [pytest.mark.integration, pytest.mark.opa_live]


@pytest.mark.parametrize("blocked", [False, True])
def test_signed_batch_persisted_and_replayed_without_processing(
    telemetry_case, open_control, valid_policy_reader, blocked, monkeypatch
):
    if not os.environ.get("AEGISOPS_DATABASE_URL") or os.environ.get("AEGISOPS_OPA_LIVE") != "1":
        pytest.skip("explicit PostgreSQL/mTLS OPA integration disabled")
    case = telemetry_case
    packet = case.make()
    control = open_control.model_copy(update={"global_blocked": blocked})
    pep = DryRunPEP(OpaClient(), control_reader=lambda: control, release_reader=valid_policy_reader)
    with PostgresAuditStore() as store, PostgresAuditReader() as reader:
        delivery = IngestionService(
            case.root,
            AnalysisEngine(UnavailableLLMClient(), data_class=DataClass.INTERNAL),
            store,
            reader,
            pep=pep,
            clock=lambda: case.now,
        ).consume(packet)
        assert delivery.receipt.status == ("blocked" if blocked else "completed")
        assert delivery.replayed is False
        for run_id in delivery.receipt.audit_run_ids:
            record = reader.show(run_id)
            assert record.telemetry == delivery.receipt.telemetry
            assert record.pep_receipt.reason == ("kill_switch" if blocked else "allowed")
            if blocked:
                assert record.dry_run_result is None
            else:
                assert record.dry_run_result.execution_performed is False

    def forbidden(*args, **kwargs):
        pytest.fail("historical replay attempted new analysis or audit write")

    monkeypatch.setattr(AnalysisEngine, "analyze", forbidden)
    monkeypatch.setattr(PostgresAuditStore, "append", forbidden)
    with PostgresAuditStore() as store, PostgresAuditReader() as reader:
        replay = IngestionService(
            case.root,
            AnalysisEngine(UnavailableLLMClient()),
            store,
            reader,
            pep=pep,
            clock=lambda: case.now + timedelta(days=1),
        ).consume(packet)
    assert replay.replayed is True and replay.receipt == delivery.receipt
