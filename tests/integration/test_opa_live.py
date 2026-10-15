"""Explicit real OPA parity, HTTP management denial and durable audit checks."""

import http.client
import os

import pytest

from aegisops.audit.postgres import PostgresAuditStore
from aegisops.audit.reader import PostgresAuditReader
from aegisops.authorization.bundle import load_config
from aegisops.authorization.http import OpaClient
from aegisops.authorization.pep import DryRunPEP
from aegisops.authorization.projection import build_input
from aegisops.domain.models import Incident, Recommendation
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.policy.checker import PolicyChecker
from aegisops.review.service import ReviewService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.opa_live,
    pytest.mark.skipif(
        os.environ.get("AEGISOPS_OPA_LIVE") != "1", reason="explicit OPA opt-in required"
    ),
]


@pytest.mark.parametrize(
    "action,params,reversibility,owner,expected",
    [
        ("escalate_to_human", {}, "R1", "Deployment", True),
        ("escalate_to_human", {"extra": "x"}, "R1", "Deployment", False),
        ("collect_more_evidence", {"scope": "events"}, "R1", "Deployment", True),
        ("collect_more_evidence", {"scope": "secrets"}, "R1", "Deployment", False),
        (
            "restart_workload",
            {"namespace": "aegisops-demo", "workload": "crashloop-oom"},
            "R2",
            "ReplicaSet",
            True,
        ),
        (
            "restart_workload",
            {"namespace": "aegisops-demo", "workload": "other"},
            "R2",
            "Deployment",
            False,
        ),
        (
            "restart_workload",
            {"namespace": "other", "workload": "crashloop-oom"},
            "R2",
            "Deployment",
            False,
        ),
        (
            "restart_workload",
            {"namespace": "aegisops-demo", "workload": "crashloop-oom"},
            "R1",
            "Deployment",
            False,
        ),
        (
            "rollback_deployment",
            {"namespace": "aegisops-demo", "deployment": "crashloop-oom"},
            "R1",
            "Deployment",
            True,
        ),
        (
            "rollback_deployment",
            {"namespace": "aegisops-demo", "deployment": "crashloop-oom"},
            "R1",
            "ReplicaSet",
            False,
        ),
    ],
)
def test_real_python_opa_parity(authz_case, action, params, reversibility, owner, expected):
    incident, recommendation = authz_case
    raw = incident.model_dump()
    for evidence in raw["evidence"]:
        if evidence["source"] == "pod_status":
            evidence["attributes"]["owner_kind"] = owner
    incident = Incident.model_validate(raw)
    raw = recommendation.model_dump()
    raw.update(
        proposed_action={"action_id": action, "params": params}, reversibility_class=reversibility
    )
    recommendation = Recommendation.model_validate(raw)
    decision = PolicyChecker().evaluate(incident, recommendation)
    response = OpaClient().evaluate(build_input(incident, recommendation, decision, load_config()))
    assert decision.allowed_for_dry_run is response.allow is expected


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/v1/policies"),
        ("PUT", "/v1/data/forbidden"),
        ("DELETE", "/v1/data/aegisops_config"),
        ("POST", "/v1/data/system/authz/allow"),
        ("GET", "/v1/data/aegisops/authz/decision"),
    ],
)
def test_management_apis_forbidden(method, path):
    connection = http.client.HTTPConnection("127.0.0.1", 18181, timeout=2)
    try:
        connection.request(method, path, body=b"{}", headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 401
        response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("blocked", [False, True])
def test_real_pdp_receipt_survives_postgres_round_trip(authz_case, open_control, blocked):
    if not os.environ.get("AEGISOPS_DATABASE_URL"):
        pytest.skip("local PostgreSQL not configured")
    control = open_control.model_copy(update={"global_blocked": blocked})
    with PostgresAuditStore() as store:
        record = ReviewService(
            AnalysisEngine(FakeLLMClient()),
            store,
            pep=DryRunPEP(OpaClient(), control_reader=lambda: control),
        ).review(authz_case[0])
    assert record.status == ("blocked" if blocked else "completed")
    assert record.pep_receipt.reason == ("kill_switch" if blocked else "allowed")
    with PostgresAuditReader() as reader:
        assert reader.show(record.run_id) == record
