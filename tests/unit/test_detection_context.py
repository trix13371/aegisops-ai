"""Unit tests for CrashLoopBackOff detection and sanitized context building."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from aegisops.context.builder import ContextBuilder, ContextError
from aegisops.detection.engine import DetectionConfig, DetectionEngine, DetectionError
from aegisops.domain.models import ContextLimits, Incident, PodSnapshot
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def repo() -> ScenarioRepository:
    return ScenarioRepository()


@pytest.mark.parametrize(
    "scenario_id",
    [
        "crashloop-exit-1",
        "crashloop-missing-env",
        "crashloop-bad-command",
        "crashloop-oom",
        "inj-log-instruction",
        "inj-secret-canary",
    ],
)
def test_detection_engine_positive_fixtures(repo: ScenarioRepository, scenario_id: str) -> None:
    scenario, snapshot = repo.load(scenario_id)
    assert scenario.should_detect is True
    engine = DetectionEngine()
    incidents = engine.detect([snapshot])
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.workload_name == scenario_id
    assert inc.container_name == "app"
    assert inc.observed_at == datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
    status_ev = [ev for ev in inc.evidence if ev.source == "pod_status"]
    assert len(status_ev) == 1
    assert status_ev[0].attributes["waiting_reason"] == "CrashLoopBackOff"
    assert status_ev[0].attributes["untrusted"] is True


@pytest.mark.parametrize(
    "scenario_id",
    [
        "neg-healthy",
        "neg-imagepull",
        "neg-missing-configmap-ref",
        "neg-old-restarts",
    ],
)
def test_detection_engine_negative_fixtures(repo: ScenarioRepository, scenario_id: str) -> None:
    scenario, snapshot = repo.load(scenario_id)
    assert scenario.should_detect is False
    engine = DetectionEngine()
    incidents = engine.detect([snapshot])
    assert incidents == []


def test_init_container_crashing_detected_even_with_zero_restarts(repo: ScenarioRepository) -> None:
    _, base = repo.load("crashloop-exit-1")
    raw = base.model_dump(mode="json")
    raw["containers"] = [
        {
            "name": "init-setup",
            "is_init": True,
            "waiting_reason": "CrashLoopBackOff",
            "last_terminated_reason": "Error",
            "last_exit_code": 1,
            "restart_count": 0,
        }
    ]
    pod = PodSnapshot.model_validate(raw)
    incidents = DetectionEngine().detect([pod])
    assert len(incidents) == 1
    assert incidents[0].container_name == "init-setup"
    status_ev = [ev for ev in incidents[0].evidence if ev.source == "pod_status"][0]
    assert status_ev.attributes["is_init"] is True
    assert status_ev.attributes["restart_count"] == 0


def test_dedup_replaced_pod_same_incident_id(repo: ScenarioRepository) -> None:
    _, base = repo.load("crashloop-exit-1")
    raw1 = base.model_dump(mode="json")
    raw1["pod_name"] = "app-worker-aaa"
    raw1["snapshot_id"] = "snap-001"
    raw1["observed_at"] = "2026-10-10T12:00:10Z"

    raw2 = base.model_dump(mode="json")
    raw2["pod_name"] = "app-worker-bbb"
    raw2["snapshot_id"] = "snap-002"
    raw2["observed_at"] = "2026-10-10T12:01:00Z"

    p1 = PodSnapshot.model_validate(raw1)
    p2 = PodSnapshot.model_validate(raw2)

    # Order 1: older first then newer
    inc_forward = DetectionEngine().detect([p1, p2])
    # Order 2: newer first then older
    inc_reverse = DetectionEngine().detect([p2, p1])

    assert len(inc_forward) == 1
    assert len(inc_reverse) == 1
    assert inc_forward[0].incident_id == inc_reverse[0].incident_id
    # Latest crash snapshot selected regardless of input order
    assert inc_forward[0].pod_name == "app-worker-bbb"
    assert inc_reverse[0].pod_name == "app-worker-bbb"
    assert inc_forward[0].observed_at == datetime(2026, 10, 10, 12, 1, 0, tzinfo=UTC)


def test_healthy_replica_does_not_hide_crashing_replica(repo: ScenarioRepository) -> None:
    _, crashing = repo.load("crashloop-exit-1")
    _, healthy_base = repo.load("neg-healthy")
    raw_healthy = healthy_base.model_dump(mode="json")
    raw_healthy["owner"] = crashing.owner.model_dump(mode="json")
    raw_healthy["pod_name"] = "app-healthy-replica"
    healthy_pod = PodSnapshot.model_validate(raw_healthy)

    incidents = DetectionEngine().detect([healthy_pod, crashing])
    assert len(incidents) == 1
    assert incidents[0].pod_name == crashing.pod_name


def test_tie_break_lexical_pod_name_on_identical_timestamp(repo: ScenarioRepository) -> None:
    _, base = repo.load("crashloop-exit-1")
    raw_z = base.model_dump(mode="json")
    raw_z["pod_name"] = "pod-zeta"
    raw_a = base.model_dump(mode="json")
    raw_a["pod_name"] = "pod-alpha"

    pod_z = PodSnapshot.model_validate(raw_z)
    pod_a = PodSnapshot.model_validate(raw_a)

    res1 = DetectionEngine().detect([pod_z, pod_a])
    res2 = DetectionEngine().detect([pod_a, pod_z])
    assert res1[0].pod_name == "pod-alpha"
    assert res2[0].pod_name == "pod-alpha"


def test_conflicting_identical_observation_tuple_raises_detection_error(
    repo: ScenarioRepository,
) -> None:
    _, base = repo.load("crashloop-exit-1")
    raw1 = base.model_dump(mode="json")
    raw2 = base.model_dump(mode="json")
    raw1["log_tail"] = "log variation one"
    raw2["log_tail"] = "log variation two"

    pod1 = PodSnapshot.model_validate(raw1)
    pod2 = PodSnapshot.model_validate(raw2)
    with pytest.raises(DetectionError):
        DetectionEngine().detect([pod1, pod2])


def test_incident_key_bucket_boundary_and_multi_container(repo: ScenarioRepository) -> None:
    _, base = repo.load("crashloop-exit-1")
    raw_in = base.model_dump(mode="json")
    raw_in["observed_at"] = "2026-10-10T12:00:00Z"
    raw_out = base.model_dump(mode="json")
    raw_out["observed_at"] = "2026-10-10T12:06:00Z"  # A different 300-second bucket.

    pod_in = PodSnapshot.model_validate(raw_in)
    pod_out = PodSnapshot.model_validate(raw_out)
    engine = DetectionEngine()
    incidents = engine.detect([pod_in, pod_out])
    assert len(incidents) == 2
    assert incidents[0].incident_id != incidents[1].incident_id

    # Multiple crashing containers in same snapshot produce distinct incidents
    raw_multi = base.model_dump(mode="json")
    raw_multi["containers"] = [
        raw_multi["containers"][0],
        {
            "name": "sidecar",
            "is_init": False,
            "waiting_reason": "CrashLoopBackOff",
            "last_terminated_reason": "Error",
            "last_exit_code": 2,
            "restart_count": 3,
        },
    ]
    multi_pod = PodSnapshot.model_validate(raw_multi)
    multi_incidents = engine.detect([multi_pod])
    assert len(multi_incidents) == 2
    containers = {i.container_name for i in multi_incidents}
    assert containers == {"app", "sidecar"}


@pytest.mark.parametrize(
    "bad_cfg",
    [
        {"window_seconds": 0},
        {"window_seconds": 86401},
        {"window_seconds": True},
        {"min_restart_count": -1},
        {"max_snapshots": 201},
        {"max_snapshots": 0},
        {"max_incidents": 201},
    ],
)
def test_detection_config_strict_validation(bad_cfg: dict) -> None:
    with pytest.raises(DetectionError):
        DetectionConfig(**bad_cfg)


def test_detection_engine_overflow_bounds_fail_safe(repo: ScenarioRepository) -> None:
    _, base = repo.load("crashloop-exit-1")
    engine = DetectionEngine(DetectionConfig(max_snapshots=2))
    with pytest.raises(DetectionError):
        engine.detect([base, base, base])

    # Incident count limit exceeded
    raw = base.model_dump(mode="json")
    snapshots = []
    for idx in range(3):
        raw_copy = dict(raw)
        raw_copy["owner"] = {"kind": "Deployment", "name": f"deploy-{idx}"}
        snapshots.append(PodSnapshot.model_validate(raw_copy))
    tight_engine = DetectionEngine(DetectionConfig(max_incidents=2))
    with pytest.raises(DetectionError):
        tight_engine.detect(snapshots)


def test_evidence_sanitization_and_untrusted_flag(repo: ScenarioRepository) -> None:
    _, canary = repo.load("inj-secret-canary")
    incidents = DetectionEngine().detect([canary])
    assert len(incidents) == 1
    inc = incidents[0]
    # Canary secret should not appear in serialized context
    inc_json = inc.model_dump_json()
    assert "DEMO_CANARY_NOT_A_REAL_CREDENTIAL" not in inc_json

    builder = ContextBuilder()
    ctx = builder.build(inc)
    ctx_json = ctx.model_dump_json()
    assert "DEMO_CANARY_NOT_A_REAL_CREDENTIAL" not in ctx_json
    for ev in ctx.evidence:
        assert ev.attributes.get("untrusted") is True


def test_context_builder_evidence_priority_and_limits(repo: ScenarioRepository) -> None:
    _, base = repo.load("crashloop-exit-1")
    inc = DetectionEngine().detect([base])[0]

    # ContextLimits with max_evidence=1 must only retain pod_status
    strict_limits = ContextLimits(max_evidence=1)
    builder = ContextBuilder(limits=strict_limits)
    ctx = builder.build(inc)
    assert len(ctx.evidence) == 1
    assert ctx.evidence[0].source == "pod_status"


def test_context_hash_deterministic_and_canonical_recompute(repo: ScenarioRepository) -> None:
    _, base = repo.load("crashloop-exit-1")
    inc = DetectionEngine().detect([base])[0]
    builder = ContextBuilder()
    ctx = builder.build(inc)

    # Hash remains identical across multiple invocations
    ctx2 = builder.build(inc)
    assert ctx.context_hash == ctx2.context_hash

    # Hash changes when limits change
    ctx_diff_limits = ContextBuilder(limits=ContextLimits(max_events=10)).build(inc)
    assert ctx.context_hash != ctx_diff_limits.context_hash

    # Manual canonical recompute
    payload = {
        "incident_id": inc.incident_id,
        "evidence": [item.model_dump(mode="json") for item in ctx.evidence],
        "limits": ctx.limits.model_dump(mode="json"),
    }
    expected_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
    assert ctx.context_hash == expected_hash


def test_context_builder_enforces_max_events_and_log_budget(repo: ScenarioRepository) -> None:
    _, base = repo.load("crashloop-exit-1")
    inc = DetectionEngine().detect([base])[0]

    # Raw incident may have multiple events; builder restricts to max_events
    tight_event_builder = ContextBuilder(limits=ContextLimits(max_events=1))
    ctx_tight_events = tight_event_builder.build(inc)
    k8s_events = [ev for ev in ctx_tight_events.evidence if ev.source == "k8s_event"]
    assert len(k8s_events) <= 1


def test_context_builder_rejects_dirty_incident_and_evidence_ids(repo: ScenarioRepository) -> None:
    _, base = repo.load("crashloop-exit-1")
    inc = DetectionEngine().detect([base])[0]

    # A credential-shaped ID cannot be exported or changed into a broken reference.
    dirty_inc_raw = inc.model_dump(mode="json")
    dirty_inc_raw["incident_id"] = "api_key=DIRTY_CANARY_NOT_REAL"
    dirty_incident = Incident.model_validate(dirty_inc_raw)
    with pytest.raises(ContextError):
        ContextBuilder().build(dirty_incident)

    dirty_ev_raw = inc.model_dump(mode="json")
    dirty_ev_raw["evidence"][0]["evidence_id"] = "token=DIRTY_CANARY_NOT_REAL"
    with pytest.raises(ContextError):
        ContextBuilder().build(Incident.model_validate(dirty_ev_raw))

    # Invalid type passed to build
    with pytest.raises(ContextError):
        ContextBuilder().build("not-an-incident")
    with pytest.raises(DetectionError):
        DetectionEngine().detect("not-a-list")


def test_origin_owner_kind_and_restart_threshold_are_separate(repo):
    base = repo.load("crashloop-exit-1")[1]
    live = base.model_dump()
    live["origin"] = "local_kind"
    replica = base.model_dump()
    replica["owner"]["kind"] = "ReplicaSet"
    result = DetectionEngine().detect(
        [base, PodSnapshot.model_validate(live), PodSnapshot.model_validate(replica)]
    )
    assert len({incident.incident_id for incident in result}) == 3
    assert DetectionEngine(DetectionConfig(min_restart_count=4)).detect([base]) == []


def test_completed_job_and_non_crash_pending_do_not_trigger(repo):
    raw = repo.load("crashloop-exit-1")[1].model_dump()
    raw["owner"]["kind"] = "Job"
    raw["containers"][0].update(
        waiting_reason=None, last_terminated_reason="Completed", last_exit_code=0
    )
    assert DetectionEngine().detect([PodSnapshot.model_validate(raw)]) == []
    raw["containers"][0]["waiting_reason"] = "ContainerCreating"
    assert DetectionEngine().detect([PodSnapshot.model_validate(raw)]) == []


def test_context_preserves_newline_at_log_citation_boundary(repo):
    raw = repo.load("crashloop-exit-1")[1].model_dump()
    raw["log_tail"] = "a" * 4095 + "\nNEXT_DIAGNOSTIC"
    incident = DetectionEngine().detect([PodSnapshot.model_validate(raw)])[0]
    logs = [
        item for item in ContextBuilder().build(incident).evidence if item.source == "container_log"
    ]
    assert "".join(item.summary for item in logs) == raw["log_tail"]
