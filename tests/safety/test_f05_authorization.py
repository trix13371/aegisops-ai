"""A PDP failure or emergency control can never fall back to a Python allow."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aegisops.audit.base import AuditStoreError
from aegisops.audit.validation import prepare_record, sanitize_record
from aegisops.authorization.bundle import AuthorizationError, load_config
from aegisops.authorization.control import load_control
from aegisops.authorization.http import OpaError
from aegisops.authorization.pep import DryRunPEP
from aegisops.authorization.projection import build_input
from aegisops.cli.app import app
from aegisops.domain.authorization import AuthorizationControl, PepReceipt
from aegisops.domain.models import AuditRecord, Recommendation
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.policy.checker import PolicyChecker
from aegisops.review.service import ReviewService

pytestmark = pytest.mark.safety


class Client:
    def __init__(self, behavior="allow"):
        self.behavior, self.calls = behavior, 0

    def evaluate(self, request):
        self.calls += 1
        if self.behavior == "outage":
            raise OpaError("unavailable")
        if self.behavior == "exception":
            raise RuntimeError("password=DO_NOT_RECORD_THIS")
        value = {
            key: getattr(request, key)
            for key in (
                "policy_version",
                "bundle_hash",
                "catalog_hash",
                "input_hash",
            )
        }
        value.update(allow=self.behavior != "deny", autonomy_level="L0", execution_performed=False)
        if self.behavior == "mismatch":
            value["input_hash"] = "0" * 64
        if self.behavior == "malformed":
            value["allow"] = "true"
        return value


class Store:
    def __init__(self):
        self.records = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def append(self, record):
        self.records.append(record)


def gate(case, client, reader):
    incident, recommendation = case
    return DryRunPEP(client, control_reader=reader).authorize(
        incident, recommendation, PolicyChecker().evaluate(incident, recommendation)
    )


@pytest.mark.parametrize(
    "behavior,reason",
    [
        ("allow", "allowed"),
        ("deny", "opa_denied"),
        ("outage", "opa_unavailable"),
        ("exception", "opa_invalid"),
        ("mismatch", "opa_invalid"),
        ("malformed", "opa_invalid"),
    ],
)
def test_both_checks_and_strict_response_required(authz_case, open_control, behavior, reason):
    client = Client(behavior)
    receipt = gate(authz_case, client, lambda: open_control)
    assert receipt.reason == reason and client.calls == 1
    assert receipt.allowed_for_dry_run is (reason == "allowed")
    assert "DO_NOT_RECORD_THIS" not in receipt.model_dump_json()


@pytest.mark.parametrize("scope", ["global", "cluster", "action", "invalid"])
def test_kill_switch_prevents_pdp_contact(authz_case, open_control, scope):
    raw = open_control.model_dump()
    if scope == "global":
        raw["global_blocked"] = True
    elif scope == "cluster":
        raw["blocked_clusters"] = (authz_case[0].cluster_id,)
    elif scope == "action":
        raw["blocked_actions"] = (authz_case[1].proposed_action.action_id.value,)
    client = Client()
    receipt = gate(authz_case, client, lambda: None if scope == "invalid" else raw)
    assert receipt.reason == ("control_invalid" if scope == "invalid" else "kill_switch")
    assert client.calls == 0 and not receipt.allowed_for_dry_run


@pytest.mark.parametrize(
    "change,reason",
    [
        ("generation", "control_changed"),
        ("global", "kill_switch"),
        ("invalid", "control_invalid"),
    ],
)
def test_control_rechecked_after_pdp(authz_case, open_control, change, reason):
    raw = open_control.model_dump()
    raw["generation"] = 2
    if change == "global":
        raw["global_blocked"] = True
    controls = iter([open_control, None if change == "invalid" else raw])
    client = Client()
    receipt = gate(authz_case, client, lambda: next(controls))
    assert client.calls == 1 and receipt.result.allow is True
    assert receipt.reason == reason and not receipt.allowed_for_dry_run


def test_unrelated_scopes_do_not_deny(authz_case):
    control = AuthorizationControl(
        generation=7,
        global_blocked=False,
        blocked_clusters=("other",),
        blocked_actions=("restart_workload",),
    )
    assert gate(authz_case, Client(), lambda: control).allowed_for_dry_run


def test_python_denial_cannot_be_overridden(authz_case, open_control):
    incident, recommendation = authz_case
    raw = recommendation.model_dump()
    raw["reversibility_class"] = "R3"
    case = incident, Recommendation.model_validate(raw)
    client = Client()
    receipt = gate(case, client, lambda: open_control)
    assert receipt.reason == "python_denied" and client.calls == 0
    decision = PolicyChecker().evaluate(*case).model_copy(update={"allowed_for_dry_run": True})
    with pytest.raises(AuthorizationError):
        DryRunPEP(client, control_reader=lambda: open_control).authorize(*case, decision)


def test_corrupt_policy_identity_is_denied_without_contact(monkeypatch, authz_case, open_control):
    def broken():
        raise AuthorizationError()

    monkeypatch.setattr("aegisops.authorization.pep.load_config", broken)
    client = Client()
    receipt = gate(authz_case, client, lambda: open_control)
    assert receipt.reason == "opa_invalid" and client.calls == 0


@pytest.mark.parametrize("behavior", ["allow", "deny", "outage"])
def test_review_preserves_bound_receipt_and_audit(authz_case, open_control, behavior):
    store = Store()
    record = ReviewService(
        AnalysisEngine(FakeLLMClient()),
        store,
        pep=DryRunPEP(Client(behavior), control_reader=lambda: open_control),
    ).review(authz_case[0])
    assert store.records == [record]
    assert record.status == ("completed" if behavior == "allow" else "blocked")
    assert (record.dry_run_result is not None) is (behavior == "allow")
    assert record.context and record.llm and record.diagnosis
    assert prepare_record(record) == sanitize_record(record) == record
    raw = record.model_dump()
    raw["pep_receipt"]["request"]["cluster_id"] = "different-cluster"
    with pytest.raises(AuditStoreError):
        prepare_record(AuditRecord.model_validate(raw))


def test_receipt_cannot_claim_allow_after_denial(authz_case, open_control):
    raw = gate(authz_case, Client("deny"), lambda: open_control).model_dump()
    raw["allowed_for_dry_run"] = True
    with pytest.raises(ValueError):
        PepReceipt.model_validate(raw)


def test_projection_excludes_context_and_model_free_text(authz_case):
    decision = PolicyChecker().evaluate(*authz_case)
    request = build_input(*authz_case, decision, load_config())
    assert not set(request.model_dump()) & {"evidence", "logs", "rationale", "title", "prompt"}
    assert request.input_hash == decision.input_hash


@pytest.mark.parametrize("contents", [None, "{}", '{"generation":1,"generation":2}', "x" * 4097])
def test_control_missing_corrupt_duplicate_or_oversize_fails_closed(
    tmp_path, monkeypatch, contents
):
    monkeypatch.chdir(tmp_path)
    if contents is not None:
        path = tmp_path / ".local/authorization/control.json"
        path.parent.mkdir(parents=True)
        path.write_text(contents)
    with pytest.raises(AuthorizationError):
        load_control()


def test_cli_explicit_mode_cannot_silently_fall_back(monkeypatch):
    store = Store()
    monkeypatch.setattr("aegisops.cli.app.PostgresAuditStore", lambda: store)
    monkeypatch.setattr("aegisops.cli.app.OpaClient", lambda: Client("outage"))
    # Real reader replaced only to avoid depending on the operator's local switch.
    monkeypatch.setattr(
        "aegisops.authorization.pep.DryRunPEP._control",
        lambda _: AuthorizationControl(
            generation=1,
            global_blocked=False,
        ),
    )
    for command in (["review"], ["run", "--scenario", "crashloop-oom"]):
        result = CliRunner().invoke(app, [*command, "--authorization", "opa"])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        record = payload["records"][0] if "records" in payload else payload
        assert record["status"] == "blocked"
        assert record["pep_receipt"]["reason"] == "opa_unavailable"
    assert CliRunner().invoke(app, ["review", "--authorization", "typo"]).exit_code == 1


def test_bundle_identity_tracks_rules_and_management_guard(tmp_path):
    from aegisops.authorization.bundle import compute_config

    source = Path(__file__).resolve().parents[2] / "policies/opa"
    for name in ("decision.rego", "api_authz.rego"):
        (tmp_path / name).write_bytes((source / name).read_bytes())
    original = compute_config(tmp_path)
    with (tmp_path / "api_authz.rego").open("a") as stream:
        stream.write("\n# changed\n")
    assert compute_config(tmp_path).bundle_hash != original.bundle_hash
