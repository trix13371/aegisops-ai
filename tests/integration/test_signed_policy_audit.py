"""Durable policy denials and optional real OPA with an actual operator-issued local release."""

import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from aegisops.audit.postgres import PostgresAuditStore
from aegisops.audit.reader import PostgresAuditReader
from aegisops.authorization.bundle import load_config
from aegisops.authorization.http import OpaClient
from aegisops.authorization.pep import DryRunPEP
from aegisops.cli.app import app
from aegisops.domain.authorization import OpaResult
from aegisops.domain.policy_release import release_hash
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.review.service import ReviewService

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def needs_database():
    if not os.environ.get("AEGISOPS_DATABASE_URL"):
        pytest.skip("local PostgreSQL not configured")


class PDP:
    calls = 0

    def evaluate(self, request):
        self.calls += 1
        return OpaResult(
            **{
                key: getattr(request, key)
                for key in (
                    "policy_version",
                    "bundle_hash",
                    "catalog_hash",
                    "input_hash",
                )
            },
            allow=True,
            autonomy_level="L0",
            execution_performed=False,
        )


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("none", "allowed"),
        ("expired", "policy_expired"),
        ("revoked", "policy_revoked"),
        ("rollback", "policy_rollback"),
        ("missing", "policy_invalid"),
        ("tamper", "policy_invalid"),
    ],
)
def test_actual_release_and_denial_receipts_persist(
    tmp_path,
    monkeypatch,
    policy_signer,
    authz_case,
    open_control,
    fault,
    reason,
):
    monkeypatch.chdir(tmp_path)
    state, key = tmp_path / ".local/policy-releases", tmp_path / "private/policy.key"
    policy_signer.initialize(state, key, clock=lambda: NOW - timedelta(minutes=2))
    signed = policy_signer.issue(
        state, key, load_config(), clock=lambda: NOW - timedelta(minutes=1)
    )
    now = NOW
    path = state / f"releases/{release_hash(signed)}.json"
    if fault == "expired":
        now = signed.body.expires_at
    elif fault == "revoked":
        policy_signer.revoke(state, clock=lambda: NOW)
    elif fault == "rollback":
        policy_signer.issue(state, key, load_config(), clock=lambda: NOW)
        raw = json.loads((state / "state.json").read_text())
        raw["active_release_hash"] = release_hash(signed)
        (state / "state.json").write_text(json.dumps(raw))
    elif fault == "missing":
        path.unlink()
    elif fault == "tamper":
        raw = json.loads(path.read_text())
        raw["signature"] = "0" * 128
        path.write_text(json.dumps(raw))
    client = PDP()
    with PostgresAuditStore() as store:
        record = ReviewService(
            AnalysisEngine(FakeLLMClient()),
            store,
            pep=DryRunPEP(client, control_reader=lambda: open_control, clock=lambda: now),
        ).review(authz_case[0])
    assert record.status == ("completed" if fault == "none" else "blocked")
    assert record.pep_receipt.reason == reason and record.pep_receipt.mode == "opa-dry-run-v2"
    assert client.calls == int(fault == "none")
    assert (record.dry_run_result is not None) is (fault == "none")
    (state / "state.json").unlink()
    with PostgresAuditReader() as reader:
        assert reader.show(record.run_id) == record


def test_cli_missing_signed_release_is_audited(tmp_path, monkeypatch, open_control):
    monkeypatch.chdir(tmp_path)
    control = tmp_path / ".local/authorization/control.json"
    control.parent.mkdir(parents=True)
    control.write_text(open_control.model_dump_json())
    result = CliRunner().invoke(app, ["review", "--authorization", "opa"])
    assert result.exit_code == 1
    raw = json.loads(result.output)
    assert raw["status"] == "blocked" and raw["pep_receipt"]["reason"] == "policy_missing"


@pytest.mark.opa_live
def test_real_opa_with_real_signed_release(
    tmp_path, monkeypatch, policy_signer, authz_case, open_control
):
    if os.environ.get("AEGISOPS_OPA_LIVE") != "1":
        pytest.skip("explicit OPA opt-in required")
    monkeypatch.chdir(tmp_path)
    state, key = tmp_path / ".local/policy-releases", tmp_path / "private/policy.key"
    policy_signer.initialize(state, key, clock=lambda: NOW - timedelta(minutes=2))
    policy_signer.issue(state, key, load_config(), clock=lambda: NOW - timedelta(minutes=1))
    with PostgresAuditStore() as store:
        record = ReviewService(
            AnalysisEngine(FakeLLMClient()),
            store,
            pep=DryRunPEP(OpaClient(), control_reader=lambda: open_control, clock=lambda: NOW),
        ).review(authz_case[0])
    assert record.status == "completed" and record.pep_receipt.result.allow is True
    assert (
        record.pep_receipt.release_before.status
        == record.pep_receipt.release_after.status
        == "valid"
    )
    with PostgresAuditReader() as reader:
        assert reader.show(record.run_id) == record
