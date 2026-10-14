"""Read-only Kubernetes observation source for the local kind demo.

Consumes a supplied :class:`ReadOnlyClient`; never imports the Kubernetes SDK,
never spawns subprocesses, and never performs network I/O of its own.  The
caller owns the client lifecycle and must close it in a ``finally`` block.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

from aegisops.domain.common import utc_now
from aegisops.domain.models import (
    ContainerSnapshot,
    KubernetesEvent,
    Origin,
    Owner,
    PodSnapshot,
)
from aegisops.sources.safe_client import LogUnavailableError, ReadOnlyClient, ReadOnlyError

__all__ = [
    "CollectionError",
    "KubernetesReadOnlySource",
    "default_snapshot_id",
    "normalize_pod",
]

_NAMESPACE: Final[str] = "aegisops-demo"
_CLUSTER_ID: Final[str] = "kind-aegisops-demo"
_UID_RE: Final[re.Pattern[str]] = re.compile(r"[a-zA-Z0-9-]{1,128}")
_MAX_POD_PAGES: Final[int] = 2
_MAX_PODS: Final[int] = 200
_MAX_EVENT_PAGES: Final[int] = 4
_MAX_EVENTS: Final[int] = 256
_MAX_RETAINED_EVENTS: Final[int] = 64
_MAX_TOKEN: Final[int] = 4096
_MAX_LOG_BYTES: Final[int] = 16384
_MAX_LOG_LINES: Final[int] = 100
_OWNER_KINDS: Final[frozenset[str]] = frozenset(
    {"Deployment", "ReplicaSet", "StatefulSet", "DaemonSet", "Job", "Pod"}
)


def _fail() -> CollectionError:
    return CollectionError("local kind collection failed")


class CollectionError(RuntimeError):
    """Raised for any failed or rejected collection; message is fixed."""

    def __init__(self, message: str = "local kind collection failed") -> None:
        super().__init__(message)


def default_snapshot_id(seed: str) -> str:
    """Derive a deterministic snapshot id from a seed string."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return "snapshot-" + digest


def _get(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _as_aware(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _fail()
    return value.astimezone(UTC)


def _token_of(page: Any) -> str | None:
    metadata = _get(page, "metadata")
    if metadata is None:
        raise _fail()
    token = _get(metadata, "_continue") if metadata is not None else None
    if token is None or token == "":
        return None
    if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN:
        raise _fail()
    return token


def _items_of(page: Any) -> list[Any]:
    items = _get(page, "items")
    if not isinstance(items, (list, tuple)):
        raise _fail()
    return list(items)


def _select_owner(pod: Any, pod_name: str) -> Owner:
    refs = _get(_get(pod, "metadata"), "owner_references")
    if refs is None:
        refs = []
    if not isinstance(refs, (list, tuple)):
        raise _fail()
    if len(refs) > 32 or any(
        _get(ref, "controller") is not None and type(_get(ref, "controller")) is not bool
        for ref in refs
    ):
        raise _fail()
    controllers = [ref for ref in refs if _get(ref, "controller") is True]
    if len(controllers) > 1:
        raise _fail()
    if not controllers:
        return Owner(kind="Pod", name=pod_name)
    ref = controllers[0]
    kind = _get(ref, "kind")
    name = _get(ref, "name")
    if not isinstance(kind, str) or kind not in _OWNER_KINDS:
        raise _fail()
    if not isinstance(name, str) or not name.strip():
        raise _fail()
    return Owner.model_validate({"kind": kind, "name": name})


def _containers(pod: Any) -> list[ContainerSnapshot]:
    status = _get(pod, "status")
    init = _get(status, "init_container_statuses")
    normal = _get(status, "container_statuses")
    init = [] if init is None else init
    normal = [] if normal is None else normal
    if not isinstance(init, (list, tuple)) or not isinstance(normal, (list, tuple)):
        raise _fail()
    if not init and not normal:
        return []
    if len(init) + len(normal) > 32:
        raise _fail()
    out: list[ContainerSnapshot] = []
    for is_init, group in ((True, init), (False, normal)):
        for item in group:
            name = _get(item, "name")
            if not isinstance(name, str) or not name.strip():
                raise _fail()
            restart = _get(item, "restart_count")
            if type(restart) is not int or restart < 0:
                raise _fail()
            state = _get(item, "state")
            waiting = _get(state, "waiting") if state is not None else None
            reason = _get(waiting, "reason") if waiting is not None else None
            last_state = _get(item, "last_state")
            terminated = _get(last_state, "terminated") if last_state is not None else None
            if terminated is None and state is not None:
                terminated = _get(state, "terminated")
            term_reason = _get(terminated, "reason") if terminated is not None else None
            exit_code = _get(terminated, "exit_code") if terminated is not None else None
            out.append(
                ContainerSnapshot(
                    name=name,
                    is_init=is_init,
                    waiting_reason=reason,
                    last_terminated_reason=term_reason,
                    last_exit_code=exit_code,
                    restart_count=restart,
                )
            )
    if not out or len(out) > 32:
        raise _fail()
    return sorted(out, key=lambda item: (not item.is_init, item.name))


def _event_time(event: Any) -> datetime:
    series = _get(event, "series")
    candidates = [
        _get(series, "last_observed_time") if series is not None else None,
        _get(event, "last_timestamp"),
        _get(event, "event_time"),
        _get(event, "first_timestamp"),
        _get(_get(event, "metadata"), "creation_timestamp"),
    ]
    for value in candidates:
        if value is not None:
            return _as_aware(value)
    raise _fail()


def _normalize_event(event: Any, pod_uid: str) -> KubernetesEvent | None:
    involved = _get(event, "involved_object")
    if (
        _get(_get(event, "metadata"), "namespace") != _NAMESPACE
        or _get(involved, "uid") != pod_uid
        or _get(involved, "namespace") != _NAMESPACE
    ):
        return None
    reason = _get(event, "reason")
    etype = _get(event, "type")
    message = _get(event, "message")
    if not isinstance(reason, str) or not reason.strip():
        raise _fail()
    if etype not in ("Normal", "Warning"):
        raise _fail()
    if not isinstance(message, str) or not message.strip():
        raise _fail()
    series = _get(event, "series")
    count = _get(series, "count") if series is not None else None
    if count is None:
        count = _get(event, "count")
    if count is None:
        count = 1
    if type(count) is not int or count < 0:
        raise _fail()
    return KubernetesEvent(
        reason=reason[:512],
        type=etype,
        message=message[:4096],
        count=count,
        last_seen=_event_time(event),
    )


def _trim_log(text: str) -> str:
    lines = text.splitlines()[-_MAX_LOG_LINES:]
    joined = "\n".join(lines)
    encoded = joined.encode("utf-8")
    if len(encoded) > _MAX_LOG_BYTES:
        encoded = encoded[-_MAX_LOG_BYTES:]
        while encoded and (encoded[0] & 0xC0) == 0x80:
            encoded = encoded[1:]
        joined = encoded.decode("utf-8", errors="ignore")
    return joined


def normalize_pod(
    pod: Any,
    *,
    events: list[Any],
    log_tail: str | None,
    observed_at: datetime,
    snapshot_id: str,
) -> PodSnapshot | None:
    """Normalize a raw pod object into a validated snapshot."""
    try:
        observed_at = _as_aware(observed_at)
        if not isinstance(events, list) or len(events) > _MAX_EVENTS:
            raise _fail()
        metadata = _get(pod, "metadata")
        name = _get(metadata, "name")
        namespace = _get(metadata, "namespace")
        uid = _get(metadata, "uid")
        resource_version = _get(metadata, "resource_version")
        if namespace != _NAMESPACE:
            raise _fail()
        if not isinstance(name, str) or not name.strip():
            raise _fail()
        if not isinstance(uid, str) or _UID_RE.fullmatch(uid) is None:
            raise _fail()
        if (
            not isinstance(resource_version, str)
            or not resource_version.strip()
            or len(resource_version) > 128
        ):
            raise _fail()
        containers = _containers(pod)
        if not containers:
            return None
        owner = _select_owner(pod, name)
        normalized: list[KubernetesEvent] = []
        for event in events:
            item = _normalize_event(event, uid)
            if item is not None:
                normalized.append(item)
        normalized.sort(key=lambda e: (e.last_seen, e.reason, e.message, e.type, e.count))
        retained = tuple(normalized[-_MAX_RETAINED_EVENTS:])
        trimmed = _trim_log(log_tail) if log_tail is not None else None
        return PodSnapshot(
            snapshot_id=snapshot_id,
            origin=Origin.LOCAL_KIND,
            cluster_id=_CLUSTER_ID,
            namespace="aegisops-demo",
            pod_name=name,
            owner=owner,
            containers=tuple(containers),
            events=retained,
            log_tail=trimmed,
            observed_at=observed_at,
        )
    except CollectionError:
        raise
    except Exception:
        raise _fail() from None


class KubernetesReadOnlySource:
    """Collect validated pod snapshots from a supplied read-only client."""

    def __init__(
        self,
        client: ReadOnlyClient,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[str], str] = default_snapshot_id,
        include_logs: bool = False,
    ) -> None:
        if type(include_logs) is not bool:
            raise _fail()
        self._client = client
        self._clock = clock
        self._id_factory = id_factory
        self._include_logs = include_logs

    def _list_pods(self) -> list[Any]:
        pods: list[Any] = []
        seen_uids: set[str] = set()
        seen_names: set[str] = set()
        token = ""
        seen_tokens: set[str] = set()
        for _ in range(_MAX_POD_PAGES):
            page = self._client.list_pods(continue_token=token)
            items = _items_of(page)
            if len(items) > 100:
                raise _fail()
            for item in items:
                metadata = _get(item, "metadata")
                uid = _get(metadata, "uid")
                name = _get(metadata, "name")
                if not isinstance(uid, str) or _UID_RE.fullmatch(uid) is None:
                    raise _fail()
                if not isinstance(name, str) or not name.strip():
                    raise _fail()
                if uid in seen_uids or name in seen_names:
                    raise _fail()
                seen_uids.add(uid)
                seen_names.add(name)
                pods.append(item)
            if len(pods) > _MAX_PODS:
                raise _fail()
            next_token = _token_of(page)
            if next_token is None:
                return pods
            if next_token in seen_tokens:
                raise _fail()
            seen_tokens.add(next_token)
            token = next_token
        raise _fail()

    def _list_events(self, pod_uid: str) -> list[Any]:
        events: list[Any] = []
        token = ""
        seen_tokens: set[str] = set()
        for _ in range(_MAX_EVENT_PAGES):
            page = self._client.list_events(pod_uid=pod_uid, continue_token=token)
            items = _items_of(page)
            if len(items) > 64:
                raise _fail()
            events.extend(items)
            if len(events) > _MAX_EVENTS:
                raise _fail()
            next_token = _token_of(page)
            if next_token is None:
                return events
            if next_token in seen_tokens:
                raise _fail()
            seen_tokens.add(next_token)
            token = next_token
        raise _fail()

    def _read_logs(self, pod_name: str, containers: list[ContainerSnapshot]) -> str | None:
        first = containers[0]
        previous = (
            first.last_terminated_reason is not None or first.last_exit_code is not None
        ) and first.restart_count > 0
        try:
            return self._client.read_logs(pod_name, first.name, previous)
        except LogUnavailableError:
            if previous:
                try:
                    return self._client.read_logs(pod_name, first.name, False)
                except LogUnavailableError:
                    return None
            return None

    def collect(self) -> list[PodSnapshot]:
        """Collect all snapshots; never returns partial results on error."""
        try:
            self._client.verify_environment()
            observed_at = _as_aware(self._clock())
            raw_pods = self._list_pods()
            snapshots: list[PodSnapshot] = []
            seen_ids: set[str] = set()
            for pod in raw_pods:
                metadata = _get(pod, "metadata")
                name = _get(metadata, "name")
                uid = _get(metadata, "uid")
                resource_version = _get(metadata, "resource_version")
                seed = "|".join((_CLUSTER_ID, uid, resource_version, observed_at.isoformat()))
                snapshot_id = self._id_factory(seed)
                if not isinstance(snapshot_id, str) or not snapshot_id.strip():
                    raise _fail()
                if snapshot_id in seen_ids:
                    raise _fail()
                seen_ids.add(snapshot_id)
                baseline = normalize_pod(
                    pod,
                    events=[],
                    log_tail=None,
                    observed_at=observed_at,
                    snapshot_id=snapshot_id,
                )
                if baseline is None:
                    continue
                events = self._list_events(uid)
                log_tail: str | None = None
                if self._include_logs:
                    log_tail = self._read_logs(name, list(baseline.containers))
                snapshot = normalize_pod(
                    pod,
                    events=events,
                    log_tail=log_tail,
                    observed_at=observed_at,
                    snapshot_id=snapshot_id,
                )
                if snapshot is not None:
                    snapshots.append(snapshot)
            self._client.verify_environment()
            snapshots.sort(key=lambda s: s.pod_name)
            return snapshots
        except ReadOnlyError:
            raise
        except CollectionError:
            raise
        except Exception:
            raise _fail() from None
