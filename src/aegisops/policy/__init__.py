"""Policy evaluation package for AegisOps.

Exposes the policy version constant, the fixed-message policy error, and the
:class:`PolicyChecker` used to decide whether a recommendation may proceed to
a descriptive dry run.
"""

from __future__ import annotations

from aegisops.policy.checker import POLICY_VERSION, PolicyChecker, PolicyError

__all__ = ["POLICY_VERSION", "PolicyChecker", "PolicyError"]
