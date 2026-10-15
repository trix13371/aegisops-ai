"""Strict local PDP/PEP contracts without service imports or execution authority."""

from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictInt, model_validator

from aegisops.domain.common import DTO, Identifier, ScalarMap, Sha256
from aegisops.domain.policy_release import PolicyReleaseCheck, policy_pair_reason

ActionName = Literal[
    "escalate_to_human", "collect_more_evidence", "restart_workload", "rollback_deployment"
]


class AuthorizationControl(DTO):
    generation: Annotated[StrictInt, Field(ge=1, le=2147483647)]
    global_blocked: StrictBool
    blocked_clusters: tuple[Identifier, ...] = Field(default=(), max_length=16)
    blocked_actions: tuple[ActionName, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def unique_scopes(self) -> Self:
        if len(set(self.blocked_clusters)) != len(self.blocked_clusters) or len(
            set(self.blocked_actions)
        ) != len(self.blocked_actions):
            raise ValueError("duplicate blocked scopes")
        return self


class OpaInput(DTO):
    policy_version: Literal["opa-l0-1"] = "opa-l0-1"
    bundle_hash: Sha256
    catalog_hash: Sha256
    input_hash: Sha256
    cluster_id: Identifier
    incident_id: Identifier
    recommendation_id: Identifier
    recommendation_incident_id: Identifier
    namespace: Identifier
    workload: Annotated[Identifier, Field(max_length=253)]
    action_id: Identifier
    params: ScalarMap
    reversibility: Literal["R1", "R2", "R3"]
    owner_kinds: tuple[Identifier, ...] = Field(max_length=8)
    autonomy_level: Literal["L0"] = "L0"
    execution_performed: Literal[False] = False
    requires_human_approval: Literal[True] = True


class OpaResult(DTO):
    policy_version: Literal["opa-l0-1"]
    bundle_hash: Sha256
    catalog_hash: Sha256
    input_hash: Sha256
    allow: StrictBool
    autonomy_level: Literal["L0"]
    execution_performed: Literal[False]


class PepReceipt(DTO):
    mode: Literal["opa-dry-run-v1", "opa-dry-run-v2"] = "opa-dry-run-v1"
    request: OpaInput
    python_allowed: StrictBool
    control_before: AuthorizationControl | None
    control_after: AuthorizationControl | None
    result: OpaResult | None
    bundle_valid: StrictBool | None = None
    release_before: PolicyReleaseCheck | None = None
    release_after: PolicyReleaseCheck | None = None
    allowed_for_dry_run: StrictBool
    reason: Literal[
        "allowed",
        "python_denied",
        "control_invalid",
        "kill_switch",
        "control_changed",
        "opa_unavailable",
        "opa_invalid",
        "opa_denied",
        "policy_missing",
        "policy_invalid",
        "policy_not_yet_valid",
        "policy_expired",
        "policy_revoked",
        "policy_rollback",
        "policy_mismatch",
        "policy_changed",
        "policy_clock_rollback",
    ]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        policy_reason = None
        if self.mode == "opa-dry-run-v1":
            if any(
                value is not None
                for value in (self.bundle_valid, self.release_before, self.release_after)
            ):
                raise ValueError("legacy receipt cannot contain policy-release fields")
        else:
            if (
                self.bundle_valid is None
                or self.release_before is None
                or self.release_after is None
            ):
                raise ValueError("signed policy checks required")
            for check in (self.release_before, self.release_after):
                check.validate_for(
                    bundle_hash=self.request.bundle_hash,
                    catalog_hash=self.request.catalog_hash,
                    policy_version=self.request.policy_version,
                )
            policy_reason = (
                "opa_invalid"
                if not self.bundle_valid
                else policy_pair_reason(self.release_before, self.release_after)
            )
        if self.result is not None and any(
            getattr(self.result, key) != getattr(self.request, key)
            for key in ("policy_version", "bundle_hash", "catalog_hash", "input_hash")
        ):
            raise ValueError("PDP response binding mismatch")
        blocked = any(
            control is not None
            and (
                control.global_blocked
                or self.request.cluster_id in control.blocked_clusters
                or self.request.action_id in control.blocked_actions
            )
            for control in (self.control_before, self.control_after)
        )
        if not self.python_allowed:
            expected = "python_denied"
        elif self.control_before is None or self.control_after is None:
            expected = "control_invalid"
        elif blocked:
            expected = "kill_switch"
        elif self.control_before != self.control_after:
            expected = "control_changed"
        elif policy_reason is not None:
            expected = policy_reason
        elif self.result is None:
            if self.reason not in {"opa_unavailable", "opa_invalid"}:
                raise ValueError("missing PDP response requires a safe error")
            expected = self.reason
        else:
            expected = "allowed" if self.result.allow else "opa_denied"
        if self.reason != expected or self.allowed_for_dry_run != (expected == "allowed"):
            raise ValueError("PEP verdict mismatch")
        return self
