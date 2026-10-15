"""Pure AegisOps runbook selector.

Derives active runbook signals from a DiagnosisContext and selects matching
runbook documents from a catalog. No IO, DB, network, or LLM access.
"""

from __future__ import annotations

from aegisops.domain.models import DiagnosisContext, Evidence, EvidenceSource
from aegisops.runbooks.models import (
    RunbookCatalog,
    RunbookError,
    RunbookSelection,
    RunbookSignal,
    selection_hash,
)

_CRASHLOOP = "CrashLoopBackOff"
_OOMKILLED = "OOMKilled"


def _crashing_records(context: DiagnosisContext) -> list[Evidence]:
    records: list[Evidence] = []
    for evidence in context.evidence:
        if evidence.source is not EvidenceSource.POD_STATUS:
            continue
        attrs = evidence.attributes
        if attrs.get("waiting_reason") == _CRASHLOOP:
            records.append(evidence)
    return records


def active_signals(context: DiagnosisContext) -> frozenset[RunbookSignal]:
    """Return the set of runbook signals active for the given context."""
    try:
        validated = DiagnosisContext.model_validate(context)
        crashing = _crashing_records(validated)
        if not crashing:
            return frozenset()
        signals: set[RunbookSignal] = {RunbookSignal.CRASHLOOP}
        oom = False
        nonzero = False
        for evidence in crashing:
            attrs = evidence.attributes
            if attrs.get("last_terminated_reason") == _OOMKILLED:
                oom = True
            code = attrs.get("last_exit_code")
            if isinstance(code, int) and not isinstance(code, bool) and code != 0:
                nonzero = True
        if oom:
            signals.add(RunbookSignal.OOM_KILLED)
        elif nonzero:
            signals.add(RunbookSignal.EXIT_NONZERO)
        return frozenset(signals)
    except RunbookError:
        raise
    except Exception:
        raise RunbookError() from None


def select_runbooks(context: DiagnosisContext, catalog: RunbookCatalog) -> RunbookSelection:
    """Select runbook documents matching the active signals."""
    try:
        validated_catalog = RunbookCatalog.model_validate(catalog)
        signals = active_signals(context)
        docs = [doc for doc in validated_catalog.documents if doc.signal in signals]
        docs.sort(key=lambda doc: doc.signal.value)
        documents = tuple(docs)
        return RunbookSelection(
            catalog_version=validated_catalog.catalog_version,
            documents=documents,
            selection_hash=selection_hash(validated_catalog.catalog_version, documents),
        )
    except RunbookError:
        raise
    except Exception:
        raise RunbookError() from None


def validate_selection(context: DiagnosisContext, selection: RunbookSelection) -> None:
    """Validate that a selection is consistent with the context."""
    try:
        validated = RunbookSelection.model_validate(selection)
        signals = active_signals(context)
        for doc in validated.documents:
            if doc.signal not in signals:
                raise RunbookError()
        has_crashloop = RunbookSignal.CRASHLOOP in signals
        selected_crashloop = any(
            doc.signal is RunbookSignal.CRASHLOOP for doc in validated.documents
        )
        if has_crashloop != selected_crashloop:
            raise RunbookError()
    except RunbookError:
        raise
    except Exception:
        raise RunbookError() from None
