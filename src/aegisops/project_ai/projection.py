"""Allowlisted release profile. Neither source strings nor identities cross this boundary."""

import hashlib
import json
from collections import Counter

from aegisops.project_ai.models import Category, Summary
from aegisops.projects.models import ProjectReport


def project_summary(report: ProjectReport) -> Summary:
    report = ProjectReport.model_validate(report)
    counts = Counter((finding.code, finding.severity) for finding in report.findings)
    return Summary(
        rules_version=report.rules_version,
        status=report.status,
        files_checked=report.files_checked,
        skipped=report.skipped,
        categories=tuple(
            Category(code=code, severity=severity, count=count)
            for (code, severity), count in sorted(counts.items())
        ),
    )


def report_digest(report: ProjectReport) -> str:
    data = json.dumps(report.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()
