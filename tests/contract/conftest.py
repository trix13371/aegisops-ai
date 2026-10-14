"""Golden complete records, using application-controlled IDs and clocks."""

import copy
import hashlib
import json
from typing import Any

import pytest

from aegisops.actions.catalog import load_catalog
from aegisops.domain.binding import bind_diagnosis, bind_recommendation
from aegisops.domain.models import AuditRecord, Incident


@pytest.fixture
def incident_data() -> dict[str, Any]:
    return {
        "incident_id": "incident-fixed",
        "incident_type": "crash_loop_back_off",
        "origin": "synthetic_fixture",
        "cluster_id": "kind-aegisops-demo",
        "namespace": "aegisops-demo",
        "workload_name": "demo",
        "pod_name": "demo-pod",
        "container_name": "app",
        "severity": "medium",
        "observed_at": "2026-10-10T12:00:00Z",
        "evidence": [
            {
                "evidence_id": "ev-001",
                "source": "pod_status",
                "summary": "CrashLoopBackOff",
                "observed_at": "2026-10-10T12:00:00Z",
                "attributes": {"exit_code": 1},
            }
        ],
    }


@pytest.fixture
def diagnosis_draft() -> dict[str, Any]:
    return {
        "summary": "Application exited",
        "hypotheses": [
            {
                "cause_category": "application_exit_nonzero",
                "cause": "Nonzero exit",
                "rationale": "Observed status",
                "confidence": "medium",
                "supporting_evidence_ids": ["ev-001"],
            }
        ],
        "missing_evidence": [],
    }


@pytest.fixture
def audit_data(incident_data: dict[str, Any], diagnosis_draft: dict[str, Any]) -> dict[str, Any]:
    incident = Incident.model_validate(incident_data)
    diagnosis = bind_diagnosis(diagnosis_draft, incident, "diagnosis-fixed")
    recommendation = bind_recommendation(
        {
            "title": "Ask human",
            "rationale": "Prototype only",
            "proposed_action": {"action_id": "escalate_to_human", "params": {}},
        },
        incident,
        diagnosis,
        load_catalog(),
        "recommendation-fixed",
    )
    digest = hashlib.sha256(
        json.dumps(incident_data["evidence"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    record = {
        "schema_version": 1,
        "run_id": "12345678-1234-5678-1234-567812345678",
        "started_at": "2026-10-10T12:00:00Z",
        "completed_at": "2026-10-10T12:00:01Z",
        "status": "completed",
        "autonomy_level": "L0",
        "origin": "synthetic_fixture",
        "incident": incident_data,
        "context": {
            "incident_id": "incident-fixed",
            "context_hash": digest,
            "evidence": copy.deepcopy(incident_data["evidence"]),
        },
        "context_hash": digest,
        "diagnosis": diagnosis.model_dump(mode="json"),
        "recommendation": recommendation.model_dump(mode="json"),
        "policy_decision": {
            "incident_id": "incident-fixed",
            "recommendation_id": "recommendation-fixed",
            "policy_version": "prototype-1",
            "autonomy_level": "L0",
            "allowed_for_dry_run": True,
            "reasons": [],
        },
        "dry_run_result": {
            "incident_id": "incident-fixed",
            "recommendation_id": "recommendation-fixed",
            "simulated": True,
            "execution_performed": False,
            "summary": "Only simulated",
        },
    }
    # Validate positive seed before any fault injection or serialized schema checks.
    AuditRecord.model_validate(record)
    return record
