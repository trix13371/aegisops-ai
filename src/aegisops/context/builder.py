"""Deterministic context builder: sanitize, prioritize, bound, and hash evidence."""

from __future__ import annotations

import hashlib
import json

from aegisops.context.sanitizer import (
    SanitizationError,
    sanitize_attributes,
    sanitize_text,
)
from aegisops.domain.common import Scalar
from aegisops.domain.models import (
    ContextLimits,
    DiagnosisContext,
    Evidence,
    EvidenceSource,
    Incident,
)

_PRIORITY: dict[EvidenceSource, int] = {
    EvidenceSource.POD_STATUS: 0,
    EvidenceSource.CONTAINER_LOG: 1,
    EvidenceSource.K8S_EVENT: 2,
}
_MAX_SUMMARY: int = 4096


class ContextError(ValueError):
    """Raised for malformed input or exceeded bounds; message is fixed and safe."""

    def __init__(self) -> None:
        super().__init__("context build failed")


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_context_hash(
    incident_id: str,
    evidence: tuple[Evidence, ...],
    limits: ContextLimits,
) -> str:
    try:
        payload = {
            "incident_id": incident_id,
            "evidence": [
                Evidence.model_validate(item).model_dump(mode="json") for item in evidence
            ],
            "limits": ContextLimits.model_validate(limits).model_dump(mode="json"),
        }
        return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    except Exception:
        raise ContextError() from None


def _priority(source: EvidenceSource) -> int:
    return _PRIORITY.get(source, 3)


def _sanitize_summary(value: str) -> str:
    cleaned = sanitize_text(value)
    if len(cleaned) > _MAX_SUMMARY:
        cleaned = cleaned[:_MAX_SUMMARY]
    return cleaned


def _sanitize_attrs(attrs: dict[str, Scalar]) -> dict[str, Scalar]:
    cleaned = sanitize_attributes(attrs)
    cleaned["untrusted"] = True
    return cleaned


def _bounded_log_summaries(incident: Incident, limits: ContextLimits) -> dict[str, str]:
    logs = [item for item in incident.evidence if item.source is EvidenceSource.CONTAINER_LOG]
    sanitized = {item.evidence_id: sanitize_text(item.summary) for item in logs}
    # Reject a recognizable credential split across evidence identities instead
    # of changing those identities or allowing its fragments into the context.
    if sanitize_text("".join(item.summary for item in logs)) != "".join(
        sanitized[item.evidence_id] for item in logs
    ):
        raise ContextError()
    status_count = sum(item.source is EvidenceSource.POD_STATUS for item in incident.evidence)
    slots = max(0, limits.max_evidence - status_count)
    remaining_bytes, remaining_lines = limits.max_log_bytes, limits.max_log_lines
    selected: dict[str, str] = {}
    # Allocate the quota from the tail while retaining each original citation.
    for item in reversed(logs):
        if slots == 0 or remaining_bytes == 0 or remaining_lines == 0:
            break
        # Keep separators at citation boundaries; normalizing each fragment
        # independently would concatenate lines that were separate in the log.
        lines = sanitized[item.evidence_id].splitlines(keepends=True)
        text = "".join(lines[-remaining_lines:])
        text = text.encode("utf-8")[-remaining_bytes:].decode("utf-8", errors="ignore")
        if not text:
            break
        remaining_bytes -= len(text.encode("utf-8"))
        remaining_lines -= len(text.splitlines())
        if not text.strip():
            continue
        selected[item.evidence_id] = text
        slots -= 1
    combined = "".join(selected.get(item.evidence_id, "") for item in logs)
    if sanitize_text(combined) != combined:
        raise ContextError()
    return selected


class ContextBuilder:
    """Builds a bounded DiagnosisContext from a validated Incident."""

    def __init__(self, limits: ContextLimits | None = None) -> None:
        try:
            base = limits if limits is not None else ContextLimits()
            if type(base) is not ContextLimits:
                raise ContextError()
            self._limits = ContextLimits.model_validate(base)
        except ContextError:
            raise
        except Exception:
            raise ContextError() from None

    def build(self, incident: object) -> DiagnosisContext:
        try:
            return self._build(incident)
        except ContextError:
            raise
        except SanitizationError:
            raise ContextError() from None
        except Exception:
            raise ContextError() from None

    def _build(self, incident: object) -> DiagnosisContext:
        if not isinstance(incident, Incident):
            raise ContextError()
        validated = Incident.model_validate(incident)

        if sanitize_text(validated.incident_id) != validated.incident_id:
            raise ContextError()
        for item in validated.evidence:
            if sanitize_text(item.evidence_id) != item.evidence_id:
                raise ContextError()

        ordered = sorted(
            enumerate(validated.evidence),
            key=lambda pair: (_priority(pair[1].source), pair[0]),
        )

        logs = _bounded_log_summaries(validated, self._limits)
        selected: list[Evidence] = []
        event_count = 0

        for _, item in ordered:
            if len(selected) >= self._limits.max_evidence:
                break
            if item.source is EvidenceSource.K8S_EVENT:
                if event_count >= self._limits.max_events:
                    continue
                event_count += 1
            if item.source is EvidenceSource.CONTAINER_LOG:
                bounded = logs.get(item.evidence_id)
                if bounded is None:
                    continue
                summary = bounded
            else:
                summary = _sanitize_summary(item.summary)
                if not summary.strip():
                    continue
            attrs = _sanitize_attrs(dict(item.attributes))
            selected.append(
                Evidence.model_validate(
                    {
                        "evidence_id": item.evidence_id,
                        "source": item.source,
                        "summary": summary,
                        "observed_at": item.observed_at,
                        "attributes": attrs,
                    }
                )
            )

        if not selected:
            raise ContextError()

        evidence_tuple = tuple(selected)
        context_hash = compute_context_hash(validated.incident_id, evidence_tuple, self._limits)
        return DiagnosisContext.model_validate(
            {
                "incident_id": validated.incident_id,
                "context_hash": context_hash,
                "evidence": evidence_tuple,
                "limits": self._limits,
            }
        )
