"""Descriptive dry-run package for AegisOps.

Exposes the fixed-message dry-run error and the :class:`DryRunEngine` that
produces a static, non-executing summary for an allowed policy decision.
"""

from __future__ import annotations

from aegisops.dry_run.engine import DryRunEngine, DryRunError

__all__ = ["DryRunEngine", "DryRunError"]
