"""Closed preset compatibility checks, without dependency resolution or installation."""

from aegisops.project_runner.compatibility import (
    PREPARED_VITEST_DEPENDENCIES,
    PackageCompatibility,
)
from aegisops.project_runner.models import CompatibilityIssue, Environment


def environment_issues(
    package: PackageCompatibility, environment: Environment
) -> tuple[CompatibilityIssue, ...]:
    if environment not in {"node-js-v1", "node-vitest-v1"}:
        return ()
    issues: list[CompatibilityIssue] = []
    if package.status == "unsupported":
        issues.append("package_unsupported")
    expected = "vitest" if environment == "node-vitest-v1" else "node"
    if package.framework != expected:
        issues.append("framework_mismatch")
    allowed = (
        PREPARED_VITEST_DEPENDENCIES
        if environment == "node-vitest-v1"
        else frozenset({"typescript", "@types/node"})
    )
    if any(name not in allowed for name in package.dependencies):
        issues.append("dependencies_unavailable")
    return tuple(issues)
