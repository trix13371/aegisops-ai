"""Deterministic prompt construction for analysis-v2 with bounded runbooks.

This module builds a fully-verified :class:`LLMRequest` from trusted domain
objects.  All untrusted free text is sanitized and embedded as data inside a
delimited JSON envelope; it is never interpreted as instructions.
"""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path

from aegisops.actions.catalog import ActionCatalog
from aegisops.context.builder import ContextBuilder, compute_context_hash
from aegisops.context.sanitizer import sanitize_data, sanitize_text
from aegisops.domain.common import DTO
from aegisops.domain.models import DiagnosisContext, Incident, Origin
from aegisops.io import contained_file, read_text, unique_object
from aegisops.llm.base import AnalysisDraft, LLMError, LLMRequest
from aegisops.llm.routing import DataClass, resolve_data_class
from aegisops.runbooks.models import RunbookCatalog, RunbookSelection
from aegisops.runbooks.repository import RunbookRepository
from aegisops.runbooks.selector import select_runbooks, validate_selection

__all__ = ["build_request", "verify_request", "request_runbooks"]

_PROMPT_ID = "analysis-v2"
_MAX_SYSTEM_BYTES = 8192
# Bounded domain JSON can expand control characters sixfold. Construction has
# room for that expansion; the much smaller wire limit is enforced by RunBudget.
_MAX_FIELD_CHARS = 4 * 1024 * 1024
_VALID_ORIGINS = frozenset({Origin.SYNTHETIC_FIXTURE, Origin.LOCAL_KIND})


def _canonical(payload: object) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _read_system_prompt() -> str:
    try:
        root = Path(str(resources.files("aegisops"))) / "assets"
        return read_text(contained_file(root, "prompts/v2/analysis.system.txt"), _MAX_SYSTEM_BYTES)
    except Exception:
        raise LLMError() from None


def _require_dto[T: DTO](value: object, expected: type[T]) -> T:
    if type(value) is not expected:
        raise LLMError()
    try:
        return expected.model_validate(value)
    except Exception:
        raise LLMError() from None


def _catalog_summary(catalog: ActionCatalog) -> dict[str, object]:
    try:
        dumped: dict[str, object] = {
            "catalog_version": catalog.catalog_version,
            "actions": [
                {
                    "id": action.id.value,
                    "reversibility": action.reversibility.value,
                    "params": {
                        key: rule.model_dump(mode="json") for key, rule in action.params.items()
                    },
                }
                for action in catalog.actions
            ],
        }
    except Exception:
        raise LLMError() from None
    if not isinstance(dumped, dict):
        raise LLMError()
    return dumped


def _catalog_version(catalog: ActionCatalog) -> str:
    for attr in ("catalog_version", "version"):
        value = getattr(catalog, attr, None)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def _incident_projection(incident: Incident) -> dict[str, object]:
    return {
        "incident_id": incident.incident_id,
        "origin": str(incident.origin),
        "cluster_id": incident.cluster_id,
        "namespace": incident.namespace,
        "workload_name": incident.workload_name,
        "pod_name": incident.pod_name,
        "container_name": incident.container_name,
        "incident_type": str(incident.incident_type),
    }


def _build_system(catalog: ActionCatalog) -> str:
    base = _read_system_prompt()
    try:
        schema = AnalysisDraft.model_json_schema()
    except Exception:
        raise LLMError() from None
    summary = _catalog_summary(catalog)
    version = _catalog_version(catalog)
    try:
        schema_text = _canonical(schema)
        catalog_text = _canonical(summary)
    except Exception:
        raise LLMError() from None
    return (
        base
        + "\n\nANALYSIS_DRAFT_JSON_SCHEMA\n"
        + schema_text
        + "\nEND_ANALYSIS_DRAFT_JSON_SCHEMA\n"
        + "\nACTION_CATALOG_VERSION\n"
        + version
        + "\nEND_ACTION_CATALOG_VERSION\n"
        + "\nACTION_CATALOG_SUMMARY\n"
        + catalog_text
        + "\nEND_ACTION_CATALOG_SUMMARY\n"
    )


def _build_user(incident: Incident, context: DiagnosisContext, selection: RunbookSelection) -> str:
    try:
        context_dump = context.model_dump(mode="json")
    except Exception:
        raise LLMError() from None
    payload = {
        "incident": _incident_projection(incident),
        "diagnosis_context": context_dump,
        "runbooks": selection.model_dump(mode="json"),
    }
    try:
        sanitized = sanitize_data(payload)
        body = _canonical(sanitized)
    except Exception:
        raise LLMError() from None
    return "UNTRUSTED_OBSERVATIONS_JSON\n" + body + "\nEND_UNTRUSTED_OBSERVATIONS_JSON"


def _compute_prompt_hash(
    prompt_id: str,
    system: str,
    user: str,
    origin: Origin,
    data_class: DataClass | None = None,
) -> str:
    try:
        payload = {
            "prompt_id": prompt_id,
            "system": system,
            "user": user,
            "origin": str(origin),
        }
        if data_class is not None:
            payload["data_class"] = data_class.value
        return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    except Exception:
        raise LLMError() from None


def build_request(
    incident: Incident,
    context: DiagnosisContext,
    catalog: ActionCatalog,
    *,
    data_class: DataClass | None = None,
    runbooks: RunbookCatalog | None = None,
) -> LLMRequest:
    """Build a verified analysis-v2 request; runbooks never become observation evidence."""
    incident = _require_dto(incident, Incident)
    context = _require_dto(context, DiagnosisContext)
    catalog = _require_dto(catalog, ActionCatalog)

    if context.incident_id != incident.incident_id:
        raise LLMError()

    try:
        rebuilt = ContextBuilder(context.limits).build(incident)
    except Exception:
        raise LLMError() from None
    if rebuilt != context:
        raise LLMError()

    try:
        expected_hash = compute_context_hash(context.incident_id, context.evidence, context.limits)
    except Exception:
        raise LLMError() from None
    if expected_hash != context.context_hash:
        raise LLMError()

    if incident.origin not in _VALID_ORIGINS:
        raise LLMError()

    try:
        resolved = resolve_data_class(incident.origin, data_class)
        classification = None if resolved == resolve_data_class(incident.origin) else resolved
    except ValueError:
        raise LLMError() from None

    system = _build_system(catalog)
    try:
        runbook_catalog = (
            RunbookRepository().catalog
            if runbooks is None
            else _require_dto(runbooks, RunbookCatalog)
        )
        selection = select_runbooks(context, runbook_catalog)
        user = _build_user(incident, context, selection)
    except Exception:
        raise LLMError() from None

    if len(system) > _MAX_FIELD_CHARS or len(user) > _MAX_FIELD_CHARS:
        raise LLMError()

    prompt_hash = _compute_prompt_hash(_PROMPT_ID, system, user, incident.origin, classification)

    request = LLMRequest(
        prompt_id=_PROMPT_ID,
        prompt_hash=prompt_hash,
        system=system,
        user=user,
        origin=incident.origin,
        data_class=classification,
    )
    verify_request(request)
    return request


def verify_request(request: LLMRequest) -> None:
    """Recompute and verify a request; raise :class:`LLMError` on any mismatch."""
    if type(request) is not LLMRequest:
        raise LLMError()
    if request.prompt_id != _PROMPT_ID:
        raise LLMError()
    if type(request.origin) is not Origin or request.origin not in _VALID_ORIGINS:
        raise LLMError()
    try:
        resolve_data_class(request.origin, request.data_class)
    except ValueError:
        raise LLMError() from None
    if type(request.system) is not str or type(request.user) is not str:
        raise LLMError()
    if len(request.system) > _MAX_FIELD_CHARS or len(request.user) > _MAX_FIELD_CHARS:
        raise LLMError()
    if type(request.prompt_hash) is not str:
        raise LLMError()
    expected = _compute_prompt_hash(
        request.prompt_id, request.system, request.user, request.origin, request.data_class
    )
    if expected != request.prompt_hash:
        raise LLMError()
    try:
        if sanitize_text(request.system) != request.system:
            raise LLMError()
        prefix, suffix = "UNTRUSTED_OBSERVATIONS_JSON\n", "\nEND_UNTRUSTED_OBSERVATIONS_JSON"
        if not request.user.startswith(prefix) or not request.user.endswith(suffix):
            raise LLMError()
        packet = json.loads(
            request.user[len(prefix) : -len(suffix)], object_pairs_hook=unique_object
        )
        if not isinstance(packet, dict) or set(packet) != {
            "incident",
            "diagnosis_context",
            "runbooks",
        }:
            raise LLMError()
        header = packet["incident"]
        if not isinstance(header, dict) or set(header) != {
            "incident_id",
            "origin",
            "cluster_id",
            "namespace",
            "workload_name",
            "pod_name",
            "container_name",
            "incident_type",
        }:
            raise LLMError()
        if header["origin"] != request.origin.value or header["namespace"] != "aegisops-demo":
            raise LLMError()
        context = DiagnosisContext.model_validate(packet["diagnosis_context"])
        if context.incident_id != header[
            "incident_id"
        ] or context.context_hash != compute_context_hash(
            context.incident_id, context.evidence, context.limits
        ):
            raise LLMError()
        validate_selection(context, RunbookSelection.model_validate(packet["runbooks"]))
        if sanitize_data(packet) != packet or not all(
            evidence.attributes.get("untrusted") is True for evidence in context.evidence
        ):
            raise LLMError()
    except Exception:
        raise LLMError() from None


def request_runbooks(request: LLMRequest) -> RunbookSelection:
    """Return validated references actually selected for this immutable request."""
    verify_request(request)
    try:
        prefix, suffix = "UNTRUSTED_OBSERVATIONS_JSON\n", "\nEND_UNTRUSTED_OBSERVATIONS_JSON"
        packet = json.loads(
            request.user[len(prefix) : -len(suffix)], object_pairs_hook=unique_object
        )
        return RunbookSelection.model_validate(packet["runbooks"])
    except Exception:
        raise LLMError() from None
