"""Signed local releases are mandatory for new OPA-mode PEP decisions, never for legacy reads."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from aegisops.audit.validation import prepare_record, sanitize_record
from aegisops.authorization.bundle import load_config
from aegisops.authorization.pep import DryRunPEP
from aegisops.authorization.releases import load_release_check
from aegisops.domain.authorization import OpaResult, PepReceipt
from aegisops.domain.policy_release import PolicyReleaseCheck, release_hash
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.policy.checker import PolicyChecker
from aegisops.review.service import ReviewService

pytestmark = pytest.mark.safety
NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)


class PDP:
    def __init__(self, callback=None):
        self.calls, self.callback = 0, callback

    def evaluate(self, request):
        self.calls += 1
        if self.callback:
            self.callback()
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


class Store:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)


@pytest.fixture
def local_release(tmp_path, monkeypatch, policy_signer):
    monkeypatch.chdir(tmp_path)
    state, key = tmp_path / ".local/policy-releases", tmp_path / "private/policy.key"
    policy_signer.initialize(state, key, clock=lambda: NOW - timedelta(minutes=2))
    signed = policy_signer.issue(
        state, key, load_config(), clock=lambda: NOW - timedelta(minutes=1)
    )
    return state, key, signed


def authorize(authz_case, open_control, client, *, clock=lambda: NOW, reader=None):
    return DryRunPEP(
        client, control_reader=lambda: open_control, release_reader=reader, clock=clock
    ).authorize(*authz_case, PolicyChecker().evaluate(*authz_case))


def test_valid_real_dpapi_release_allows_gate(local_release, authz_case, open_control):
    client = PDP()
    receipt = authorize(authz_case, open_control, client)
    assert receipt.mode == "opa-dry-run-v2" and receipt.allowed_for_dry_run
    assert (
        client.calls == 1
        and receipt.release_before.status == receipt.release_after.status == "valid"
    )
    assert receipt.release_before.trust.public_key


@pytest.mark.parametrize(
    "fault", ["missing", "tamper", "wrong_content", "expired", "future", "revoked", "rollback"]
)
def test_invalid_release_blocks_before_pdp(
    local_release, authz_case, open_control, policy_signer, fault
):
    state, key, signed = local_release
    target = state / f"releases/{release_hash(signed)}.json"
    now, expected = NOW, "policy_invalid"
    if fault == "missing":
        target.unlink()
    elif fault in {"tamper", "wrong_content"}:
        raw = json.loads(target.read_text())
        if fault == "tamper":
            raw["signature"] = "0" * 128
            target.write_text(json.dumps(raw))
        else:
            config = load_config().model_copy(update={"bundle_hash": "0" * 64})
            policy_signer.issue(state, key, config, clock=lambda: NOW)
            expected = "policy_mismatch"
    elif fault == "expired":
        now, expected = signed.body.expires_at, "policy_expired"
    elif fault == "future":
        now, expected = signed.body.issued_at - timedelta(seconds=1), "policy_not_yet_valid"
    elif fault == "revoked":
        policy_signer.revoke(state, clock=lambda: NOW)
        expected = "policy_revoked"
    else:
        policy_signer.issue(state, key, load_config(), clock=lambda: NOW)
        path = state / "state.json"
        raw = json.loads(path.read_text())
        raw["active_release_hash"] = release_hash(signed)
        path.write_text(json.dumps(raw))
        expected = "policy_rollback"
    client = PDP()
    receipt = authorize(authz_case, open_control, client, clock=lambda: now)
    assert receipt.reason == expected and not receipt.allowed_for_dry_run
    assert client.calls == 0


@pytest.mark.parametrize(
    "change,expected",
    [
        ("revoke", "policy_revoked"),
        ("renew", "policy_changed"),
        ("expire", "policy_expired"),
        ("clock", "policy_clock_rollback"),
    ],
)
def test_policy_rechecked_after_pdp(
    local_release, authz_case, open_control, policy_signer, change, expected
):
    state, key, signed = local_release

    def update():
        if change == "revoke":
            policy_signer.revoke(state, clock=lambda: NOW)
        elif change == "renew":
            policy_signer.issue(state, key, load_config(), clock=lambda: NOW)

    client = PDP(update)
    times = iter(
        [
            NOW,
            signed.body.expires_at
            if change == "expire"
            else NOW - timedelta(seconds=1)
            if change == "clock"
            else NOW,
        ]
    )
    receipt = authorize(authz_case, open_control, client, clock=lambda: next(times))
    assert client.calls == 1 and receipt.result.allow is True
    assert receipt.reason == expected and not receipt.allowed_for_dry_run


def test_revocation_persists_and_renewal_increases_floor(local_release, policy_signer):
    state, key, old = local_release
    policy_signer.revoke(state, clock=lambda: NOW)
    renewed = policy_signer.issue(
        state, key, load_config(), clock=lambda: NOW + timedelta(seconds=1)
    )
    assert renewed.body.revision == 2
    check = load_release_check(load_config(), NOW + timedelta(seconds=2))
    assert check.status == "valid" and check.state.minimum_revision == 2
    assert check.state.revoked_revisions == (1,)
    assert old.body.revision < check.state.minimum_revision
    with pytest.raises(Exception):
        policy_signer.initialize(state, key)


def test_failed_activation_preserves_pinned_state(local_release, policy_signer, monkeypatch):
    state, key, _ = local_release
    original = (state / "state.json").read_bytes()

    def fail(*args):
        raise OSError("password=ONLY_TEST_CANARY")

    monkeypatch.setattr(policy_signer.os, "replace", fail)
    with pytest.raises(OSError):
        policy_signer.issue(state, key, load_config(), clock=lambda: NOW)
    assert (state / "state.json").read_bytes() == original
    assert not list(state.glob("state-*.tmp"))
    assert load_release_check(load_config(), NOW).status == "valid"


@pytest.mark.parametrize(
    "target,contents",
    [
        ("state.json", '{"generation":1,"generation":2}'),
        ("trust.json", "x" * 2049),
        ("state.json", "x" * 8193),
    ],
)
def test_duplicate_or_oversize_metadata_fails_closed(
    local_release, target, contents, authz_case, open_control
):
    state, _, _ = local_release
    (state / target).write_text(contents)
    assert load_release_check(load_config(), NOW).status == "policy_invalid"
    client = PDP()
    receipt = authorize(authz_case, open_control, client)
    assert receipt.reason == "policy_invalid" and client.calls == 0


def test_unsigned_initial_state_is_not_permission(
    tmp_path, monkeypatch, policy_signer, authz_case, open_control
):
    monkeypatch.chdir(tmp_path)
    client = PDP()
    assert authorize(authz_case, open_control, client).reason == "policy_missing"
    state, key = tmp_path / ".local/policy-releases", tmp_path / "key.blob"
    policy_signer.initialize(state, key, clock=lambda: NOW)
    assert authorize(authz_case, open_control, client).reason == "policy_missing"
    assert client.calls == 0


def test_fake_reader_cannot_claim_valid_without_proof(authz_case, open_control):
    client = PDP()
    receipt = authorize(
        authz_case,
        open_control,
        client,
        reader=lambda config, now: PolicyReleaseCheck(checked_at=now, status="valid"),
    )
    assert receipt.reason == "policy_invalid" and client.calls == 0


def test_reader_exception_becomes_valid_denial_receipt(authz_case, open_control):
    def broken(config, now):
        raise RuntimeError("password=ONLY_TEST_CANARY")

    client = PDP()
    receipt = authorize(authz_case, open_control, client, reader=broken)
    assert receipt.reason == "policy_invalid" and client.calls == 0
    assert receipt.release_before.release is None
    assert PepReceipt.model_validate_json(receipt.model_dump_json()) == receipt
    assert "ONLY_TEST_CANARY" not in receipt.model_dump_json()


def test_expired_release_can_only_be_renewed_at_higher_revision(local_release, policy_signer):
    state, key, old = local_release
    now = old.body.expires_at
    assert load_release_check(load_config(), now).status == "policy_expired"
    new = policy_signer.issue(state, key, load_config(), clock=lambda: now)
    assert new.body.revision > old.body.revision
    assert load_release_check(load_config(), now).status == "valid"


def test_policy_deny_is_audited_and_historical_receipt_needs_no_current_policy(
    local_release,
    authz_case,
    open_control,
    policy_signer,
):
    state, _, signed = local_release
    policy_signer.revoke(state, clock=lambda: NOW)
    store = Store()
    record = ReviewService(
        AnalysisEngine(FakeLLMClient()),
        store,
        pep=DryRunPEP(PDP(), control_reader=lambda: open_control, clock=lambda: NOW),
    ).review(authz_case[0])
    assert store.records == [record] and record.status == "blocked"
    assert record.pep_receipt.reason == "policy_revoked" and record.dry_run_result is None
    (state / f"releases/{release_hash(signed)}.json").unlink()
    assert sanitize_record(record) == prepare_record(record) == record


def test_receipt_cannot_omit_signed_checks_or_forge_allow(local_release, authz_case, open_control):
    good = authorize(authz_case, open_control, PDP()).model_dump()
    good["release_after"] = None
    with pytest.raises(ValueError):
        PepReceipt.model_validate(good)
    good = authorize(authz_case, open_control, PDP()).model_dump()
    good["release_after"]["release"]["signature"] = "0" * 128
    with pytest.raises(ValueError):
        PepReceipt.model_validate(good)


def test_historical_v1_receipt_remains_readable():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    receipt = PepReceipt.model_validate_json(
        (root / "examples/contracts/PepReceipt.json").read_text()
    )
    assert receipt.mode == "opa-dry-run-v1" and receipt.release_before is None
