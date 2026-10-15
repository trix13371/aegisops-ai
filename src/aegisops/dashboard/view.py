"""Curated, sanitized audit presentation; never return credentials or raw contexts."""

from typing import Any

from aegisops.domain.models import AuditRecord


def present_record(record: AuditRecord, *, detail: bool = False) -> dict[str, Any]:
    incident = record.incident
    result: dict[str, Any] = {
        "id": str(record.run_id),
        "time": record.started_at.isoformat(),
        "status": record.status.value,
        "origin": record.origin.value,
        "title": incident.workload_name
        if incident
        else "Проверка без инцидентов"
        if record.observation
        else "Проверка среды",
        "signal": "CrashLoopBackOff"
        if incident
        else "Наблюдение"
        if record.observation
        else "Проверка остановлена",
        "namespace": incident.namespace if incident else None,
        "incident": incident is not None,
        "summary": record.diagnosis.summary
        if record.diagnosis
        else record.errors[0].message
        if record.errors
        else "",
        "batch_id": str(record.telemetry.batch_id) if record.telemetry else None,
    }
    if detail:
        recommendation = record.recommendation
        result.update(
            pod=incident.pod_name if incident else None,
            cluster=incident.cluster_id if incident else None,
            diagnosis=record.diagnosis.model_dump(mode="json") if record.diagnosis else None,
            recommendation=recommendation.model_dump(mode="json") if recommendation else None,
            policy_reason=record.pep_receipt.reason if record.pep_receipt else None,
            policy_version=record.policy_decision.policy_version
            if record.policy_decision
            else None,
            evidence=[
                {"source": item.source.value, "summary": item.summary} for item in incident.evidence
            ]
            if incident
            else [],
            errors=[item.message for item in record.errors],
            execution_performed=False,
        )
    return result
