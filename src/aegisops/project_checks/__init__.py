"""Shared-snapshot full-check workflow: static scan plus container run.

This package is intentionally narrow: it composes the existing project
registry, static scanner and container runner into one bounded workflow.
It never imports subprocess, network, credentials, AI, audit or cluster
modules, and it never executes user code outside the existing container
runner.
"""

from aegisops.project_checks.models import (
    FULL_CHECK_FORMAT,
    FullCheckIndex,
    FullCheckResult,
    build_result,
)
from aegisops.project_checks.repository import FullCheckRepository
from aegisops.project_checks.snapshot import scan_snapshot

__all__ = (
    "FULL_CHECK_FORMAT",
    "FullCheckIndex",
    "FullCheckRepository",
    "FullCheckResult",
    "build_result",
    "scan_snapshot",
)
