"""A no-incident scan must not invent diagnosis, policy or simulation stages."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aegisops.domain.models import AuditRecord, NoIncidentObservation

pytestmark = pytest.mark.contract


def scan():
    now = datetime(2026, 10, 15, tzinfo=UTC)
    return {
        "run_id": uuid4(),
        "started_at": now,
        "completed_at": now,
        "status": "completed",
        "origin": "synthetic_fixture",
        "observation": {"source": "synthetic", "snapshot_count": 1},
    }


@pytest.mark.parametrize("value", [True, False, 1, "0"])
def test_no_incident_count_is_exact_integer_zero(value):
    with pytest.raises(ValidationError):
        NoIncidentObservation(source="synthetic", snapshot_count=1, detected_incidents=value)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "failed", "errors": [{"code": "fail", "message": "failed"}]},
        {"origin": "local_kind"},
        {"context_hash": "f" * 64},
        {"errors": [{"code": "fail", "message": "failed"}]},
        {"observation": None},
    ],
)
def test_scan_rejects_inconsistent_state(change):
    with pytest.raises(ValidationError):
        AuditRecord.model_validate({**scan(), **change})


def test_completed_empty_scan_is_valid_and_explicitly_has_no_model():
    data = scan()
    data["observation"]["snapshot_count"] = 0
    record = AuditRecord.model_validate(data)
    assert record.llm is record.incident is record.dry_run_result is None
