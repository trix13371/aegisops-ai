"""Deterministic CrashLoopBackOff detector producing bounded Incident DTOs."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from aegisops.context.sanitizer import (
    SanitizationError,
    bound_log,
    sanitize_attributes,
    sanitize_text,
)
from aegisops.domain.common import Scalar
from aegisops.domain.models import (
    ContainerSnapshot,
    ContextLimits,
    Evidence,
    EvidenceSource,
    Incident,
    IncidentType,
    KubernetesEvent,
    PodSnapshot,
    Severity,
)

_CRASH_REASON: Final = "CrashLoopBackOff"
_STATUS_SUMMARY: Final = "CrashLoopBackOff observed"
_CHUNK_CHARS: Final = 4096


class DetectionError(ValueError):
    """Raised for any malformed input or exceeded bound; message is fixed and safe."""

    def __init__(self) -> None:
        super().__init__("detection failed")


@dataclass(frozen=True)
class DetectionConfig:
    window_seconds: int = 300
    min_restart_count: int = 0
    max_snapshots: int = 200
    max_incidents: int = 200

    def __post_init__(self) -> None:
        _check_int("window_seconds", self.window_seconds, 1, 86400)
        _check_int("min_restart_count", self.min_restart_count, 0, 2**31 - 1)
        _check_int("max_snapshots", self.max_snapshots, 1, 200)
        _check_int("max_incidents", self.max_incidents, 1, 200)


def _check_int(name: str, value: object, low: int, high: int) -> None:
    if type(value) is not int:
        raise DetectionError()
    if value < low or value > high:
        raise DetectionError()


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_name(value: str, limit: int) -> str:
    cleaned = sanitize_text(value)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit]
    if not cleaned.strip():
        raise DetectionError()
    return cleaned


def _safe_short(value: str, limit: int) -> str:
    cleaned = sanitize_text(value)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit]
    return cleaned


def _incident_key(snapshot: PodSnapshot, container: ContainerSnapshot, window: int) -> str:
    bucket = math.floor(snapshot.observed_at.astimezone(UTC).timestamp() / window)
    payload = [
        str(snapshot.origin),
        snapshot.cluster_id,
        snapshot.namespace,
        snapshot.owner.kind,
        snapshot.owner.name,
        container.name,
        str(IncidentType.CRASH_LOOP_BACK_OFF),
        bucket,
    ]
    return _sha256_hex(_canonical(payload))


def _is_crashing(container: ContainerSnapshot, min_restart: int) -> bool:
    return container.waiting_reason == _CRASH_REASON and container.restart_count >= min_restart


def _status_attributes(container: ContainerSnapshot) -> dict[str, Scalar]:
    attrs: dict[str, Scalar] = {
        "waiting_reason": _CRASH_REASON,
        "is_init": container.is_init,
        "restart_count": container.restart_count,
        "owner_kind": None,
        "untrusted": True,
    }
    if container.last_terminated_reason is not None:
        attrs["last_terminated_reason"] = _safe_short(container.last_terminated_reason, 512)
    if container.last_exit_code is not None:
        attrs["last_exit_code"] = container.last_exit_code
    return attrs


def _log_chunks(text: str, limits: ContextLimits) -> list[str]:
    bounded = bound_log(text, max_bytes=limits.max_log_bytes, max_lines=limits.max_log_lines)
    chunks: list[str] = []
    for start in range(0, len(bounded), _CHUNK_CHARS):
        piece = bounded[start : start + _CHUNK_CHARS]
        if piece.strip():
            chunks.append(piece)
    return chunks


def _event_sort_key(event: KubernetesEvent) -> tuple[float, str, str, str, int]:
    return (
        -event.last_seen.timestamp(),
        event.reason,
        event.type,
        event.message,
        event.count,
    )


def _build_evidence(
    incident_id: str,
    snapshot: PodSnapshot,
    container: ContainerSnapshot,
    limits: ContextLimits,
) -> tuple[Evidence, ...]:
    observed = snapshot.observed_at
    items: list[tuple[EvidenceSource, str, dict[str, Scalar], datetime]] = []

    status_attrs = _status_attributes(container)
    status_attrs["owner_kind"] = snapshot.owner.kind
    items.append((EvidenceSource.POD_STATUS, _STATUS_SUMMARY, status_attrs, observed))

    if snapshot.log_tail is not None:
        for chunk in _log_chunks(snapshot.log_tail, limits):
            items.append(
                (
                    EvidenceSource.CONTAINER_LOG,
                    chunk,
                    {"scope": "pod", "untrusted": True},
                    observed,
                )
            )

    events = sorted(snapshot.events, key=_event_sort_key)
    for event in events[: limits.max_events]:
        message = _safe_short(event.message, 4096)
        if not message.strip():
            continue
        attrs: dict[str, Scalar] = {
            "reason": _safe_short(event.reason, 512),
            "type": event.type,
            "count": event.count,
            "last_seen": event.last_seen.astimezone(UTC).isoformat(),
            "untrusted": True,
        }
        items.append((EvidenceSource.K8S_EVENT, message, attrs, event.last_seen))

    evidence: list[Evidence] = []
    for ordinal, (source, summary, attrs, evidence_time) in enumerate(items):
        if len(evidence) >= limits.max_evidence:
            break
        safe_summary = _safe_short(summary, 4096)
        if not safe_summary.strip():
            continue
        safe_attrs = sanitize_attributes(attrs)
        observed_iso = evidence_time.astimezone(UTC).isoformat()
        digest = _sha256_hex(
            _canonical([incident_id, str(source), ordinal, safe_summary, safe_attrs, observed_iso])
        )
        evidence.append(
            Evidence.model_validate(
                {
                    "evidence_id": f"evidence-{digest}",
                    "source": source,
                    "summary": safe_summary,
                    "observed_at": evidence_time,
                    "attributes": safe_attrs,
                }
            )
        )
    return tuple(evidence)


class DetectionEngine:
    """Stateless deterministic detector; each call is independent of prior calls."""

    def __init__(
        self,
        config: DetectionConfig | None = None,
        limits: ContextLimits | None = None,
    ) -> None:
        try:
            self._config = config if config is not None else DetectionConfig()
            self._limits = limits if limits is not None else ContextLimits()
            if type(self._config) is not DetectionConfig:
                raise DetectionError()
            self._config.__post_init__()
            if type(self._limits) is not ContextLimits:
                raise DetectionError()
            self._limits = ContextLimits.model_validate(self._limits)
        except DetectionError:
            raise
        except Exception:
            raise DetectionError() from None

    def detect(self, snapshots: object) -> list[Incident]:
        try:
            return self._detect(snapshots)
        except DetectionError:
            raise
        except SanitizationError:
            raise DetectionError() from None
        except Exception:
            raise DetectionError() from None

    def _detect(self, snapshots: object) -> list[Incident]:
        if not isinstance(snapshots, (list, tuple)):
            raise DetectionError()
        if len(snapshots) > self._config.max_snapshots:
            raise DetectionError()

        validated: list[PodSnapshot] = []
        for raw in snapshots:
            if not isinstance(raw, PodSnapshot):
                raise DetectionError()
            validated.append(PodSnapshot.model_validate(raw))

        best: dict[str, tuple[PodSnapshot, ContainerSnapshot]] = {}
        for snapshot in validated:
            for container in snapshot.containers:
                if not _is_crashing(container, self._config.min_restart_count):
                    continue
                key = _incident_key(snapshot, container, self._config.window_seconds)
                current = best.get(key)
                if current is None or _tie_break(snapshot, current[0]) < 0:
                    best[key] = (snapshot, container)

        if len(best) > self._config.max_incidents:
            raise DetectionError()

        incidents: list[Incident] = []
        for key, (snapshot, container) in best.items():
            incident_id = f"incident-{key}"
            evidence = _build_evidence(incident_id, snapshot, container, self._limits)
            if not evidence:
                raise DetectionError()
            incidents.append(
                Incident.model_validate(
                    {
                        "incident_id": incident_id,
                        "incident_type": IncidentType.CRASH_LOOP_BACK_OFF,
                        "origin": snapshot.origin,
                        "cluster_id": _safe_name(snapshot.cluster_id, 128),
                        "namespace": snapshot.namespace,
                        "workload_name": _safe_name(snapshot.owner.name, 253),
                        "pod_name": _safe_name(snapshot.pod_name, 253),
                        "container_name": _safe_name(container.name, 63),
                        "severity": Severity.MEDIUM,
                        "observed_at": snapshot.observed_at,
                        "evidence": evidence,
                    }
                )
            )

        incidents.sort(key=lambda item: item.incident_id)
        return incidents


def _tie_break(candidate: PodSnapshot, current: PodSnapshot) -> int:
    if candidate.observed_at != current.observed_at:
        return -1 if candidate.observed_at > current.observed_at else 1
    if candidate.pod_name != current.pod_name:
        return -1 if candidate.pod_name < current.pod_name else 1
    if candidate.snapshot_id != current.snapshot_id:
        return -1 if candidate.snapshot_id < current.snapshot_id else 1
    # Identical observation identifiers with conflicting data must not make
    # the result depend on input order.
    if candidate != current:
        raise DetectionError()
    return 0
