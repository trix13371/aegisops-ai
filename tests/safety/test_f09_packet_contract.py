from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from aegisops.domain.models import ContainerSnapshot, Origin, Owner, PodSnapshot
from aegisops.telemetry.contracts import TelemetryBatch


@pytest.fixture
def base_snapshot() -> PodSnapshot:
    return PodSnapshot(
        snapshot_id="snap-01",
        origin=Origin.LOCAL_KIND,
        cluster_id="kind-aegisops-demo",
        namespace="aegisops-demo",
        pod_name="demo-pod",
        owner=Owner(kind="Deployment", name="demo-deploy"),
        containers=(ContainerSnapshot(name="main"),),
        observed_at=datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def batch_payload(base_snapshot: PodSnapshot) -> dict:
    issued = datetime(2026, 10, 15, 12, 1, 0, tzinfo=UTC)
    return {
        "batch_id": uuid4(),
        "registration_id": uuid4(),
        "key_id": "a" * 64,
        "issued_at": issued,
        "expires_at": issued + timedelta(minutes=4),
        "snapshots": (base_snapshot,),
    }


def test_telemetry_batch_positive_baseline(batch_payload: dict):
    batch = TelemetryBatch(**batch_payload)
    assert batch.format == "aegisops-observations-v1"
    assert batch.collector_id == "local-kind-reader"
    assert batch.cluster_id == "kind-aegisops-demo"
    assert batch.origin == "local_kind"
    assert batch.data_class == "internal"
    assert len(batch.snapshots) == 1


@pytest.mark.parametrize(
    ("field", "bad_val"),
    [
        ("origin", "synthetic"),
        ("origin", Origin.SYNTHETIC_FIXTURE),
        ("data_class", "public"),
        ("collector_id", "other-collector"),
        ("cluster_id", "other-cluster"),
    ],
)
def test_telemetry_batch_literal_restrictions(batch_payload: dict, field: str, bad_val: str):
    payload = {**batch_payload, field: bad_val}
    with pytest.raises(ValueError):
        TelemetryBatch(**payload)


def test_telemetry_batch_rejects_unknown_fields(batch_payload: dict):
    payload = {**batch_payload, "extra_instruction": "untrusted instruction"}
    with pytest.raises(ValueError):
        TelemetryBatch(**payload)


@pytest.mark.parametrize(
    "delta",
    [
        timedelta(0),
        timedelta(seconds=-1),
        timedelta(minutes=5, seconds=1),
        timedelta(hours=1),
    ],
)
def test_telemetry_batch_expiry_bounds(batch_payload: dict, delta: timedelta):
    payload = {**batch_payload, "expires_at": batch_payload["issued_at"] + delta}
    with pytest.raises(ValueError):
        TelemetryBatch(**payload)


def test_telemetry_batch_rejects_duplicate_snapshot_ids(
    batch_payload: dict, base_snapshot: PodSnapshot
):
    payload = {**batch_payload, "snapshots": (base_snapshot, base_snapshot)}
    with pytest.raises(ValueError):
        TelemetryBatch(**payload)


def test_telemetry_batch_rejects_more_than_200_snapshots(
    batch_payload: dict, base_snapshot: PodSnapshot
):
    snaps = tuple(base_snapshot.model_copy(update={"snapshot_id": f"snap-{i}"}) for i in range(201))
    payload = {**batch_payload, "snapshots": snaps}
    with pytest.raises(ValueError):
        TelemetryBatch(**payload)


@pytest.mark.parametrize(
    ("update_key", "update_val"),
    [
        ("cluster_id", "other-cluster"),
        ("origin", Origin.SYNTHETIC_FIXTURE),
        ("observed_at", datetime(2026, 10, 15, 11, 55, 59, tzinfo=UTC)),
        ("observed_at", datetime(2026, 10, 15, 12, 1, 1, tzinfo=UTC)),
    ],
)
def test_telemetry_batch_snapshot_consistency(
    batch_payload: dict, base_snapshot: PodSnapshot, update_key: str, update_val
):
    bad_snap = base_snapshot.model_copy(update={update_key: update_val})
    payload = {**batch_payload, "snapshots": (bad_snap,)}
    with pytest.raises(ValueError):
        TelemetryBatch(**payload)


pytestmark = pytest.mark.safety
