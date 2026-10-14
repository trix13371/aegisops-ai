"""Revalidate and redact the entire record before it reaches storage."""

from aegisops.audit.base import AuditStoreError
from aegisops.context.builder import ContextBuilder
from aegisops.context.sanitizer import sanitize_data
from aegisops.domain.models import AuditRecord, RunStatus
from aegisops.dry_run.engine import DryRunEngine
from aegisops.policy.checker import PolicyChecker

MAX_AUDIT_BYTES = 512 * 1024


def sanitize_record(record: object) -> AuditRecord:
    try:
        validated = AuditRecord.model_validate(record)
        clean = AuditRecord.model_validate(sanitize_data(validated.model_dump(mode="json")))
        if len(clean.model_dump_json().encode("utf-8")) > MAX_AUDIT_BYTES:
            raise AuditStoreError()
        if clean.context is not None:
            if clean.incident is None:
                raise AuditStoreError()
            rebuilt = ContextBuilder(clean.context.limits).build(clean.incident)
            if rebuilt != clean.context:
                raise AuditStoreError()
            if clean.diagnosis is not None:
                known = {item.evidence_id for item in clean.context.evidence}
                if any(
                    ref not in known
                    for hypothesis in clean.diagnosis.hypotheses
                    for ref in hypothesis.supporting_evidence_ids
                ):
                    raise AuditStoreError()
        if clean.llm is not None:
            if (
                clean.context is None
                or clean.llm.parameters.get("context_hash") != clean.context_hash
            ):
                raise AuditStoreError()
        if clean.status is RunStatus.COMPLETED and clean.incident is not None and clean.llm is None:
            raise AuditStoreError()
        if clean.status is RunStatus.BLOCKED and clean.policy_decision is None and not clean.errors:
            raise AuditStoreError()
        return clean
    except Exception:
        raise AuditStoreError() from None


def prepare_record(record: AuditRecord) -> AuditRecord:
    try:
        clean = sanitize_record(record)
        if clean.policy_decision is not None:
            if clean.incident is None or clean.recommendation is None:
                raise AuditStoreError()
            expected = PolicyChecker().evaluate(clean.incident, clean.recommendation)
            if clean.policy_decision != expected:
                raise AuditStoreError()
        if clean.dry_run_result is not None:
            if (
                clean.incident is None
                or clean.recommendation is None
                or clean.policy_decision is None
            ):
                raise AuditStoreError()
            expected_dry = DryRunEngine().simulate(
                clean.incident, clean.recommendation, clean.policy_decision
            )
            if clean.dry_run_result != expected_dry:
                raise AuditStoreError()
        return clean
    except Exception:
        raise AuditStoreError() from None
