import copy
from typing import Any

import pytest
from pydantic import ValidationError

from aegisops.actions.catalog import load_catalog
from aegisops.domain.binding import ContractError, bind_diagnosis, bind_recommendation
from aegisops.domain.models import AuditRecord, Incident

pytestmark = pytest.mark.contract


def test_bound_ids_and_catalog_control(
    incident_data: dict[str, Any], diagnosis_draft: dict[str, Any]
) -> None:
    incident = Incident.model_validate(incident_data)
    diagnosis = bind_diagnosis(diagnosis_draft, incident, "application-id")
    draft = {
        "title": "Restart",
        "rationale": "Proposal only",
        "proposed_action": {
            "action_id": "restart_workload",
            "params": {"namespace": "aegisops-demo", "workload": "demo"},
        },
    }
    recommendation = bind_recommendation(draft, incident, diagnosis, load_catalog(), "app-rec")
    assert recommendation.reversibility_class.value == "R2"
    assert recommendation.execution_performed is False
    assert recommendation.requires_human_approval is True
    assert recommendation.incident_id == incident.incident_id


@pytest.mark.parametrize("references", [["unknown-ev"], ["ev-001", "ev-001"]])
def test_unknown_or_duplicate_evidence_rejected(
    incident_data: dict[str, Any], diagnosis_draft: dict[str, Any], references: list[str]
) -> None:
    diagnosis_draft["hypotheses"][0]["supporting_evidence_ids"] = references
    with pytest.raises(ContractError):
        bind_diagnosis(diagnosis_draft, Incident.model_validate(incident_data), "app-id")


@pytest.mark.parametrize(
    "piece,field",
    [
        ("context", "incident_id"),
        ("diagnosis", "incident_id"),
        ("recommendation", "incident_id"),
        ("recommendation", "diagnosis_id"),
        ("policy_decision", "incident_id"),
        ("policy_decision", "recommendation_id"),
        ("dry_run_result", "incident_id"),
        ("dry_run_result", "recommendation_id"),
    ],
)
def test_record_cross_links(audit_data: dict[str, Any], piece: str, field: str) -> None:
    audit_data[piece][field] = "another-record"
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(audit_data)


@pytest.mark.parametrize(
    "fault",
    [
        "missing-context",
        "wrong-hash",
        "missing-hash",
        "wrong-origin",
        "backwards-time",
        "unknown-evidence",
        "duplicate-evidence",
        "denied-completed",
        "executed",
        "integer-flag",
        "bool-schema-version",
        "unsafe-level",
        "fabricated-context-evidence",
        "incomplete-completed",
    ],
)
def test_record_faults(audit_data: dict[str, Any], fault: str) -> None:
    if fault == "missing-context":
        audit_data["context"] = None
    if fault == "wrong-hash":
        audit_data["context_hash"] = "0" * 64
    if fault == "missing-hash":
        audit_data["context_hash"] = None
    if fault == "wrong-origin":
        audit_data["origin"] = "local_kind"
    if fault == "backwards-time":
        audit_data["completed_at"] = "2026-10-09T12:00:00Z"
    if fault == "unknown-evidence":
        audit_data["diagnosis"]["hypotheses"][0]["supporting_evidence_ids"] = ["fake-ev"]
    if fault == "duplicate-evidence":
        audit_data["incident"]["evidence"] *= 2
    if fault == "denied-completed":
        audit_data["policy_decision"].update(
            allowed_for_dry_run=False, reasons=[{"code": "deny", "message": "blocked"}]
        )
    if fault == "executed":
        audit_data["dry_run_result"]["execution_performed"] = True
    if fault == "integer-flag":
        audit_data["dry_run_result"]["execution_performed"] = 0
    if fault == "bool-schema-version":
        audit_data["schema_version"] = True
    if fault == "unsafe-level":
        audit_data["autonomy_level"] = "L2"
    if fault == "fabricated-context-evidence":
        audit_data["context"]["evidence"][0]["evidence_id"] = "fake-context"
    if fault == "incomplete-completed":
        audit_data["diagnosis"] = None
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(audit_data)


def test_partial_failed_and_blocked_records(audit_data: dict[str, Any]) -> None:
    minimal = {
        key: audit_data[key]
        for key in (
            "schema_version",
            "run_id",
            "started_at",
            "completed_at",
            "autonomy_level",
            "origin",
        )
    }
    blocked = AuditRecord.model_validate(dict(minimal, status="blocked"))
    failed = AuditRecord.model_validate(
        dict(minimal, status="failed", errors=[{"code": "timeout", "message": "failed"}])
    )
    assert blocked.dry_run_result is None and failed.dry_run_result is None
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(dict(minimal, status="failed"))


def test_validated_mapping_cannot_change(audit_data: dict[str, Any]) -> None:
    record = AuditRecord.model_validate(copy.deepcopy(audit_data))
    assert record.incident is not None
    with pytest.raises(TypeError):
        record.incident.evidence[0].attributes["exit_code"] = 0
    with pytest.raises(ValidationError):
        record.incident.severity = "low"


def test_bind_helpers_accept_validated_instances(
    incident_data: dict[str, Any], diagnosis_draft: dict[str, Any]
) -> None:
    from aegisops.domain.models import DiagnosisDraft, RecommendationDraft

    incident = Incident.model_validate(incident_data)
    diagnosis = bind_diagnosis(DiagnosisDraft.model_validate(diagnosis_draft), incident, "app-id")
    draft = RecommendationDraft.model_validate(
        {
            "title": "Review",
            "rationale": "Instance input",
            "proposed_action": {"action_id": "escalate_to_human", "params": {}},
        }
    )
    assert (
        bind_recommendation(
            draft, incident, diagnosis, load_catalog(), "rec-id"
        ).execution_performed
        is False
    )


def test_sanitized_context_may_differ_from_raw_summary(audit_data: dict[str, Any]) -> None:
    audit_data["context"]["evidence"][0]["summary"] = "Sanitized observation"
    assert AuditRecord.model_validate(audit_data).context is not None
