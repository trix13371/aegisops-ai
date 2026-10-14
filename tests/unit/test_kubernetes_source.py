from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from aegisops.domain.models import Origin
from aegisops.sources.kubernetes_readonly import (
    CollectionError,
    KubernetesReadOnlySource,
    default_snapshot_id,
    normalize_pod,
)
from aegisops.sources.safe_client import LogUnavailableError, ReadOnlyClient, ReadOnlyError

pytestmark = pytest.mark.unit


def _trap_spec():
    def getter():
        raise AssertionError("forbidden spec access")

    return property(lambda self: getter())


class TrapPod:
    spec = _trap_spec()
    annotations = _trap_spec()
    labels = _trap_spec()
    to_dict = _trap_spec()

    def __init__(self, metadata, status):
        self.metadata = metadata
        self.status = status


class TrapMetadata(SimpleNamespace):
    annotations = _trap_spec()
    labels = _trap_spec()
    to_dict = _trap_spec()


def _make_pod(
    name: str = "demo-pod",
    uid: str = "uid-1",
    rv: str = "100",
    owners: list | None = None,
    init_statuses: list | None = None,
    container_statuses: list | None = None,
    namespace: str = "aegisops-demo",
) -> TrapPod:
    meta = TrapMetadata(
        name=name,
        namespace=namespace,
        uid=uid,
        resource_version=rv,
        owner_references=owners,
    )
    status = SimpleNamespace(
        init_container_statuses=init_statuses,
        container_statuses=container_statuses,
    )
    return TrapPod(metadata=meta, status=status)


def _make_container_status(
    name: str = "app",
    restarts: int = 0,
    waiting_reason: str | None = None,
    terminated_reason: str | None = None,
    exit_code: int | None = None,
    current_terminated: bool = False,
) -> SimpleNamespace:
    state = SimpleNamespace()
    if waiting_reason is not None:
        state.waiting = SimpleNamespace(reason=waiting_reason)
    if current_terminated:
        state.terminated = SimpleNamespace(reason=terminated_reason, exit_code=exit_code)
    last_state = SimpleNamespace()
    if not current_terminated and terminated_reason is not None:
        last_state.terminated = SimpleNamespace(reason=terminated_reason, exit_code=exit_code)
    return SimpleNamespace(
        name=name,
        restart_count=restarts,
        state=state,
        last_state=last_state,
    )


def _make_event(
    reason: str = "Started",
    etype: str = "Normal",
    message: str = "started container",
    count: int = 1,
    last_timestamp: datetime | None = None,
    uid: str = "uid-1",
    namespace: str = "aegisops-demo",
) -> SimpleNamespace:
    ts = last_timestamp or datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
    return SimpleNamespace(
        metadata=SimpleNamespace(namespace=namespace),
        involved_object=SimpleNamespace(uid=uid, namespace=namespace),
        reason=reason,
        type=etype,
        message=message,
        count=count,
        last_timestamp=ts,
    )


def test_normalize_pod_owner_selection_and_containers_ordering():
    fixed_time = datetime(2025, 2, 1, 10, 0, tzinfo=UTC)
    rs_owner = SimpleNamespace(controller=True, kind="ReplicaSet", name="demo-rs-1")
    init_c = _make_container_status(name="init-z")
    norm_b = _make_container_status(name="app-b")
    norm_a = _make_container_status(
        name="app-a", restarts=1, terminated_reason="OOMKilled", exit_code=137
    )
    pod = _make_pod(
        owners=[rs_owner],
        init_statuses=[init_c],
        container_statuses=[norm_b, norm_a],
    )

    snap = normalize_pod(
        pod,
        events=[],
        log_tail="line1\nline2",
        observed_at=fixed_time,
        snapshot_id="snap-123",
    )
    assert snap is not None
    assert snap.snapshot_id == "snap-123"
    assert snap.origin == Origin.LOCAL_KIND
    assert snap.owner.kind == "ReplicaSet"
    assert snap.owner.name == "demo-rs-1"
    # init container first, then normal containers sorted alphabetically
    assert [c.name for c in snap.containers] == ["init-z", "app-a", "app-b"]
    assert snap.containers[0].is_init is True
    assert snap.containers[1].is_init is False
    assert snap.containers[1].last_terminated_reason == "OOMKilled"
    assert snap.containers[1].last_exit_code == 137
    assert snap.containers[1].restart_count == 1


def test_normalize_pod_pending_without_statuses_returns_none():
    pod = _make_pod(init_statuses=None, container_statuses=None)
    fixed_time = datetime(2025, 2, 1, 10, 0, tzinfo=UTC)
    result = normalize_pod(pod, events=[], log_tail=None, observed_at=fixed_time, snapshot_id="s-1")
    assert result is None


@pytest.mark.parametrize(
    "bad_owner",
    [
        [SimpleNamespace(controller=True, kind="UnknownKind", name="x")],
        [
            SimpleNamespace(controller=True, kind="ReplicaSet", name="r1"),
            SimpleNamespace(controller=True, kind="ReplicaSet", name="r2"),
        ],
    ],
)
def test_normalize_pod_invalid_owner_raises_collection_error(bad_owner):
    pod = _make_pod(owners=bad_owner, container_statuses=[_make_container_status()])
    with pytest.raises(CollectionError):
        normalize_pod(
            pod, events=[], log_tail=None, observed_at=datetime.now(UTC), snapshot_id="s-1"
        )


def test_event_filtering_cap_and_sorting():
    pod_uid = "pod-abc"
    pod = _make_pod(uid=pod_uid, container_statuses=[_make_container_status()])
    valid_events = []
    for idx in range(70):
        valid_events.append(
            _make_event(
                reason=f"R{idx:02d}",
                message=f"msg-{idx}",
                count=1,
                last_timestamp=datetime(2025, 1, 1, 12, idx // 60, idx % 60, tzinfo=UTC),
                uid=pod_uid,
            )
        )
    # Extraneous foreign event that should be ignored
    alien_event = _make_event(uid="other-pod")
    snap = normalize_pod(
        pod,
        events=valid_events + [alien_event],
        log_tail=None,
        observed_at=datetime(2025, 2, 1, 10, 0, tzinfo=UTC),
        snapshot_id="snap-event-test",
    )
    assert snap is not None
    assert len(snap.events) == 64
    # Retained latest 64 events (from index 6 to 69)
    assert snap.events[0].reason == "R06"
    assert snap.events[-1].reason == "R69"


def test_collect_pagination_two_pages_success():
    client = MagicMock(spec=ReadOnlyClient)
    pod1 = _make_pod(name="pod-1", uid="uid-1", container_statuses=[_make_container_status()])
    pod2 = _make_pod(name="pod-2", uid="uid-2", container_statuses=[_make_container_status()])

    client.list_pods.side_effect = [
        SimpleNamespace(items=[pod1], metadata=SimpleNamespace(_continue="tok1")),
        SimpleNamespace(items=[pod2], metadata=SimpleNamespace(_continue="")),
    ]
    client.list_events.return_value = SimpleNamespace(
        items=[], metadata=SimpleNamespace(_continue=None)
    )

    fixed_now = datetime(2025, 2, 1, 12, 0, 0, tzinfo=UTC)
    captured_seeds = []

    def id_gen(seed: str) -> str:
        captured_seeds.append(seed)
        return default_snapshot_id(seed)

    source = KubernetesReadOnlySource(client, clock=lambda: fixed_now, id_factory=id_gen)
    snaps = source.collect()

    assert len(snaps) == 2
    assert [s.pod_name for s in snaps] == ["pod-1", "pod-2"]
    assert client.verify_environment.call_count == 2
    assert client.list_pods.call_count == 2
    assert client.close.call_count == 0  # Client lifecycle owned by caller
    assert len(captured_seeds) == 2
    assert "uid-1|100|2025-02-01T12:00:00+00:00" in captured_seeds[0]


def test_collect_failing_end_guard_discards_partial():
    client = MagicMock(spec=ReadOnlyClient)
    pod1 = _make_pod(name="pod-1", uid="uid-1", container_statuses=[_make_container_status()])
    client.list_pods.return_value = SimpleNamespace(
        items=[pod1], metadata=SimpleNamespace(_continue=None)
    )
    client.list_events.return_value = SimpleNamespace(
        items=[], metadata=SimpleNamespace(_continue=None)
    )
    # First call succeeds, second call fails
    client.verify_environment.side_effect = [None, ReadOnlyError("env marker lost")]

    source = KubernetesReadOnlySource(client, clock=lambda: datetime(2025, 1, 1, 0, 0, tzinfo=UTC))
    with pytest.raises(ReadOnlyError):
        source.collect()


def test_collect_repeated_continuation_token_aborts():
    client = MagicMock(spec=ReadOnlyClient)
    client.list_pods.return_value = SimpleNamespace(
        items=[], metadata=SimpleNamespace(_continue="infinite-token")
    )
    source = KubernetesReadOnlySource(client, clock=lambda: datetime(2025, 1, 1, 0, 0, tzinfo=UTC))
    with pytest.raises(CollectionError):
        source.collect()


def test_log_collection_previous_fallback_and_auth_error_abort():
    client = MagicMock(spec=ReadOnlyClient)
    c = _make_container_status(
        name="app", restarts=1, terminated_reason="CrashLoopBackOff", exit_code=1
    )
    pod = _make_pod(name="pod-crash", uid="uid-crash", container_statuses=[c])

    client.list_pods.return_value = SimpleNamespace(
        items=[pod], metadata=SimpleNamespace(_continue=None)
    )
    client.list_events.return_value = SimpleNamespace(
        items=[], metadata=SimpleNamespace(_continue=None)
    )

    # Fallback case: previous=True raises LogUnavailableError -> falls back to previous=False
    client.read_logs.side_effect = [
        LogUnavailableError("no previous logs"),
        "current log stream",
    ]
    source = KubernetesReadOnlySource(
        client,
        clock=lambda: datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
        include_logs=True,
    )
    snaps = source.collect()
    assert len(snaps) == 1
    assert snaps[0].log_tail == "current log stream"
    assert client.read_logs.call_count == 2

    # Auth error case: read_logs raises ReadOnlyError -> must abort collection
    client.read_logs.side_effect = ReadOnlyError("forbidden")
    with pytest.raises(ReadOnlyError):
        source.collect()


@pytest.mark.parametrize("bad_flag", [1, 0, "true", None, 1.0])
def test_source_init_rejects_non_bool_include_logs(bad_flag):
    client = MagicMock(spec=ReadOnlyClient)
    with pytest.raises(CollectionError):
        KubernetesReadOnlySource(client, include_logs=bad_flag)


FIXED = datetime(2026, 10, 14, 10, tzinfo=UTC)


def normalize(pod, **kwargs):
    return normalize_pod(
        pod,
        events=kwargs.get("events", []),
        log_tail=kwargs.get("log_tail"),
        observed_at=kwargs.get("observed_at", FIXED),
        snapshot_id="snapshot-test",
    )


def fake_client(pods):
    client = MagicMock(spec=ReadOnlyClient)
    client.list_pods.return_value = SimpleNamespace(
        items=pods, metadata=SimpleNamespace(_continue=None)
    )
    client.list_events.return_value = SimpleNamespace(
        items=[], metadata=SimpleNamespace(_continue=None)
    )
    return client


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("restart_count", True),
        ("restart_count", -1),
        ("restart_count", "2"),
        ("state", SimpleNamespace(waiting=SimpleNamespace(reason=42))),
        ("last_state", SimpleNamespace(terminated=SimpleNamespace(reason="Error", exit_code=True))),
    ],
)
def test_malformed_container_fields_fail_without_coercion(attribute, value):
    container = _make_container_status()
    setattr(container, attribute, value)
    with pytest.raises(CollectionError):
        normalize(_make_pod(container_statuses=[container]))


@pytest.mark.parametrize("bad_statuses", [False, 0, {}, "canary-status"])
def test_malformed_empty_statuses_not_silently_skipped(bad_statuses):
    with pytest.raises(CollectionError):
        normalize(_make_pod(container_statuses=bad_statuses))


def test_pending_skip_and_empty_collection_still_verify_environment():
    clock = MagicMock(return_value=FIXED)
    client = fake_client([_make_pod()])
    assert KubernetesReadOnlySource(client, clock=clock, include_logs=True).collect() == []
    assert clock.call_count == 1
    assert client.verify_environment.call_count == 2
    client.list_events.assert_not_called()
    client.read_logs.assert_not_called()
    client.list_pods.return_value.items = []
    assert KubernetesReadOnlySource(client, clock=lambda: FIXED).collect() == []
    assert client.verify_environment.call_count == 4


def test_foreign_pending_pod_is_rejected():
    client = fake_client([_make_pod(namespace="kube-system")])
    with pytest.raises(CollectionError):
        KubernetesReadOnlySource(client, clock=lambda: FIXED).collect()
    client.list_events.assert_not_called()


def test_current_terminated_standalone_pod_and_multibyte_log_bounds():
    pod = _make_pod(
        container_statuses=[
            _make_container_status(
                terminated_reason="Completed", exit_code=0, current_terminated=True
            )
        ]
    )
    snap = normalize(pod, log_tail="\n".join(["старый" * 400] * 110 + ["последняя строка"]))
    assert snap is not None
    assert snap.owner.kind == "Pod" and snap.owner.name == "demo-pod"
    assert snap.containers[0].last_exit_code == 0
    assert snap.containers[0].last_terminated_reason == "Completed"
    assert snap.log_tail.endswith("последняя строка")
    assert len(snap.log_tail.encode("utf-8")) <= 16384
    assert len(snap.log_tail.splitlines()) <= 100
    assert "�" not in snap.log_tail


def test_series_event_count_time_and_stale_message_never_read():
    class StaleEvent(SimpleNamespace):
        message = _trap_spec()

    stale = StaleEvent(
        metadata=SimpleNamespace(namespace="aegisops-demo"),
        involved_object=SimpleNamespace(uid="old-uid", namespace="aegisops-demo"),
    )
    event = _make_event(count=2)
    event.series = SimpleNamespace(
        count=7, last_observed_time=FIXED.astimezone(timezone(timedelta(hours=3)))
    )
    snap = normalize(
        _make_pod(container_statuses=[_make_container_status()]), events=[stale, event]
    )
    assert snap is not None and len(snap.events) == 1
    assert snap.events[0].count == 7
    assert snap.events[0].last_seen == FIXED
    assert snap.events[0].last_seen.utcoffset() == timedelta(0)


def test_invalid_event_is_safe_error_and_foreign_namespace_ignored():
    pod = _make_pod(container_statuses=[_make_container_status()])
    event = _make_event(message="fake-canary", count=True)
    with pytest.raises(CollectionError) as error:
        normalize(pod, events=[event])
    assert str(error.value) == "local kind collection failed"
    event.metadata.namespace = "kube-system"
    assert normalize(pod, events=[event]).events == ()


@pytest.mark.parametrize(
    "bad_page",
    [
        SimpleNamespace(items=[]),
        SimpleNamespace(items="wrong", metadata=SimpleNamespace(_continue=None)),
        SimpleNamespace(items=[], metadata=SimpleNamespace(_continue=42)),
        SimpleNamespace(items=[], metadata=SimpleNamespace(_continue="x" * 4097)),
    ],
)
def test_invalid_page_shapes_fail_closed(bad_page):
    client = fake_client([])
    client.list_pods.return_value = bad_page
    with pytest.raises(CollectionError):
        KubernetesReadOnlySource(client, clock=lambda: FIXED).collect()


@pytest.mark.parametrize("duplicate", ["name", "uid", "snapshot_id"])
def test_duplicate_identity_aborts_whole_collection(duplicate):
    one = _make_pod(name="one", uid="uid-1", container_statuses=[_make_container_status()])
    two = _make_pod(name="two", uid="uid-2", container_statuses=[_make_container_status()])
    if duplicate == "name":
        two.metadata.name = one.metadata.name
    if duplicate == "uid":
        two.metadata.uid = one.metadata.uid
    client = fake_client([one, two])
    kwargs = {"id_factory": lambda seed: "same-id"} if duplicate == "snapshot_id" else {}
    with pytest.raises(CollectionError):
        KubernetesReadOnlySource(client, clock=lambda: FIXED, **kwargs).collect()


def test_page_limits_and_events_pagination():
    client = fake_client([])
    client.list_pods.side_effect = [
        SimpleNamespace(items=[], metadata=SimpleNamespace(_continue="first")),
        SimpleNamespace(items=[], metadata=SimpleNamespace(_continue="second")),
    ]
    with pytest.raises(CollectionError):
        KubernetesReadOnlySource(client, clock=lambda: FIXED).collect()
    assert client.list_pods.call_count == 2
    client = fake_client([_make_pod(container_statuses=[_make_container_status()])])
    client.list_events.side_effect = [
        SimpleNamespace(
            items=[_make_event(reason="First")], metadata=SimpleNamespace(_continue="next")
        ),
        SimpleNamespace(
            items=[_make_event(reason="Second")], metadata=SimpleNamespace(_continue=None)
        ),
    ]
    snaps = KubernetesReadOnlySource(client, clock=lambda: FIXED).collect()
    assert [event.reason for event in snaps[0].events] == ["First", "Second"]
    assert client.list_events.call_args.kwargs == {"pod_uid": "uid-1", "continue_token": "next"}
    client.read_logs.assert_not_called()


def test_injected_clock_is_aware_and_ids_repeat_for_same_observation():
    client = fake_client([_make_pod(container_statuses=[_make_container_status()])])
    source = KubernetesReadOnlySource(client, clock=lambda: FIXED)
    assert source.collect() == source.collect()
    client.list_pods.return_value.items[0].metadata.resource_version = "101"
    assert source.collect()[0].snapshot_id != default_snapshot_id(
        "kind-aegisops-demo|uid-1|100|" + FIXED.isoformat()
    )
    with pytest.raises(CollectionError):
        KubernetesReadOnlySource(client, clock=lambda: FIXED.replace(tzinfo=None)).collect()


@pytest.mark.parametrize("overflow", ["oversized_page", "repeated_cursor", "page_limit"])
def test_event_pagination_refuses_truncated_success(overflow):
    client = fake_client([_make_pod(container_statuses=[_make_container_status()])])
    if overflow == "oversized_page":
        client.list_events.return_value.items = [_make_event()] * 65
    elif overflow == "repeated_cursor":
        client.list_events.return_value.metadata._continue = "same-cursor"
    else:
        client.list_events.side_effect = [
            SimpleNamespace(items=[], metadata=SimpleNamespace(_continue=f"page-{index}"))
            for index in range(4)
        ]
    with pytest.raises(CollectionError):
        KubernetesReadOnlySource(client, clock=lambda: FIXED).collect()
    assert client.list_events.call_count <= 4


def test_actual_owner_name_can_exceed_container_name_limit():
    name = "replicaset-" + "a" * 80
    pod = _make_pod(
        owners=[SimpleNamespace(controller=True, kind="ReplicaSet", name=name)],
        container_statuses=[_make_container_status()],
    )
    assert normalize(pod).owner.name == name
