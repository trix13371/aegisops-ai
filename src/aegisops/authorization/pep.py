"""Independent dry-run gate: Python + OPA + fresh local emergency controls."""

from collections.abc import Callable
from typing import Protocol

from aegisops.actions.catalog import load_catalog
from aegisops.authorization.bundle import AuthorizationError, BundleConfig, digest, load_config
from aegisops.authorization.control import load_control
from aegisops.authorization.projection import build_input
from aegisops.domain.authorization import AuthorizationControl, OpaInput, OpaResult, PepReceipt
from aegisops.domain.models import Incident, PolicyDecision, Recommendation
from aegisops.policy.checker import PolicyChecker


class DecisionClient(Protocol):
    def evaluate(self, request: OpaInput) -> OpaResult: ...


class DryRunPEP:
    def __init__(
        self,
        client: DecisionClient,
        *,
        control_reader: Callable[[], AuthorizationControl] = load_control,
    ) -> None:
        self._client, self._read_control = client, control_reader

    def _control(self) -> AuthorizationControl | None:
        try:
            return AuthorizationControl.model_validate(self._read_control())
        except Exception:
            return None

    def authorize(
        self, incident: Incident, recommendation: Recommendation, decision: PolicyDecision
    ) -> PepReceipt:
        if PolicyChecker().evaluate(incident, recommendation) != decision:
            raise AuthorizationError()
        valid_bundle = True
        try:
            config = load_config()
        except AuthorizationError:
            valid_bundle = False
            config = BundleConfig(
                bundle_hash="0" * 64, catalog_hash=digest(load_catalog().model_dump(mode="json"))
            )
        request = build_input(incident, recommendation, decision, config)
        before = self._control()
        result = None
        error = "opa_invalid"
        if (
            decision.allowed_for_dry_run
            and before is not None
            and not (
                before.global_blocked
                or request.cluster_id in before.blocked_clusters
                or request.action_id in before.blocked_actions
            )
            and valid_bundle
        ):
            try:
                result = OpaResult.model_validate(self._client.evaluate(request))
                if any(
                    getattr(result, key) != getattr(request, key)
                    for key in ("policy_version", "bundle_hash", "catalog_hash", "input_hash")
                ):
                    result = None
            except Exception as exc:
                error = (
                    "opa_unavailable"
                    if getattr(exc, "code", None) == "unavailable"
                    else "opa_invalid"
                )
        after = self._control()
        if not decision.allowed_for_dry_run:
            reason = "python_denied"
        elif before is None or after is None:
            reason = "control_invalid"
        elif any(
            control.global_blocked
            or request.cluster_id in control.blocked_clusters
            or request.action_id in control.blocked_actions
            for control in (before, after)
        ):
            reason = "kill_switch"
        elif before != after:
            reason = "control_changed"
        elif result is None:
            reason = error
        else:
            reason = "allowed" if result.allow else "opa_denied"
        return PepReceipt.model_validate(
            {
                "request": request,
                "python_allowed": decision.allowed_for_dry_run,
                "control_before": before,
                "control_after": after,
                "result": result,
                "allowed_for_dry_run": reason == "allowed",
                "reason": reason,
            }
        )
