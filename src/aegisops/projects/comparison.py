"""Compare saved findings as multisets; no rescanning, source reads or model calls."""

from collections import defaultdict, deque
from typing import Literal
from uuid import UUID

from pydantic import Field

from aegisops.domain.common import DTO
from aegisops.projects.models import Finding, ProjectError, ProjectReport


class ReportComparison(DTO):
    project_id: UUID
    base_id: UUID
    target_id: UUID
    base_rules: Literal["local-static-v1", "local-static-v2"]
    target_rules: Literal["local-static-v1", "local-static-v2"]
    comparable: bool
    reason: Literal["same_rules", "rules_changed", "partial"]
    base_total: int
    target_total: int
    added: tuple[Finding, ...] = Field(default=(), max_length=200)
    removed: tuple[Finding, ...] = Field(default=(), max_length=200)
    unchanged: int | None = None


def identity(finding: Finding) -> tuple[str, str, str, str | None, int | None]:
    # Line shifts/renames are observations at new coordinates, not proof of a fix.
    return finding.file, finding.code, finding.severity, finding.location, finding.line


def compare_reports(base: ProjectReport, target: ProjectReport) -> ReportComparison:
    base, target = ProjectReport.model_validate(base), ProjectReport.model_validate(target)
    if base.project_id != target.project_id or base.id == target.id:
        raise ProjectError("Выберите два разных отчёта одного проекта")
    reason: Literal["same_rules", "rules_changed", "partial"] = (
        "rules_changed"
        if base.rules_version != target.rules_version
        else "partial"
        if base.partial or target.partial
        else "same_rules"
    )
    buckets: dict[tuple[str, str, str, str | None, int | None], deque[Finding]] = defaultdict(deque)
    added: list[Finding] = []
    removed: list[Finding] = []
    unchanged = 0
    if reason == "same_rules":
        for finding in base.findings:
            buckets[identity(finding)].append(finding)
        for finding in target.findings:
            previous = buckets[identity(finding)]
            if previous:
                previous.popleft()
                unchanged += 1
            else:
                added.append(finding)
        removed = [item for bucket in buckets.values() for item in bucket]
    return ReportComparison(
        project_id=base.project_id,
        base_id=base.id,
        target_id=target.id,
        base_rules=base.rules_version,
        target_rules=target.rules_version,
        comparable=reason == "same_rules",
        reason=reason,
        base_total=len(base.findings),
        target_total=len(target.findings),
        added=tuple(added),
        removed=tuple(removed),
        unchanged=unchanged if reason == "same_rules" else None,
    )
