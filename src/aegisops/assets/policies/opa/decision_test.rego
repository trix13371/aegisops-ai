package aegisops.authz_test

import rego.v1

import data.aegisops.authz

# ---------------------------------------------------------------------------
# Base input object. All fields required by the policy's `common` rule.
# ---------------------------------------------------------------------------

base_input := {
	"policy_version": data.aegisops_config.policy_version,
	"bundle_hash": data.aegisops_config.bundle_hash,
	"catalog_hash": data.aegisops_config.catalog_hash,
	"input_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
	"cluster_id": "cluster-a",
	"incident_id": "inc-0001",
	"recommendation_id": "rec-0001",
	"recommendation_incident_id": "inc-0001",
	"namespace": "aegisops-demo",
	"workload": "web",
	"action_id": "escalate_to_human",
	"params": {},
	"reversibility": "R1",
	"owner_kinds": [],
	"autonomy_level": "L0",
	"execution_performed": false,
	"requires_human_approval": true,
}

# ---------------------------------------------------------------------------
# Positive cases: all four actions allowed.
# ---------------------------------------------------------------------------

test_allow_escalate_to_human if {
	inp := base_input
	authz.allow with input as inp
}

test_allow_collect_more_evidence if {
	inp := object.union(base_input, {
		"action_id": "collect_more_evidence",
		"params": {"scope": "events"},
	})
	authz.allow with input as inp
}

test_allow_restart_workload if {
	inp := object.union(base_input, {
		"action_id": "restart_workload",
		"reversibility": "R2",
		"params": {"namespace": "aegisops-demo", "workload": "web"},
	})
	authz.allow with input as inp
}

test_allow_rollback_deployment if {
	inp := object.union(base_input, {
		"action_id": "rollback_deployment",
		"params": {"namespace": "aegisops-demo", "deployment": "web"},
		"owner_kinds": ["Deployment"],
	})
	authz.allow with input as inp
}

# ---------------------------------------------------------------------------
# Negative: wrong namespace.
# ---------------------------------------------------------------------------

test_deny_wrong_namespace if {
	inp := object.union(base_input, {"namespace": "other-ns"})
	not authz.allow with input as inp
}

# ---------------------------------------------------------------------------
# Negative: wrong class (autonomy_level).
# ---------------------------------------------------------------------------

test_deny_wrong_autonomy_level if {
	inp := object.union(base_input, {"autonomy_level": "L1"})
	not authz.allow with input as inp
}

# ---------------------------------------------------------------------------
# Negative: wrong target (workload mismatch / invalid name).
# ---------------------------------------------------------------------------

test_deny_restart_wrong_target if {
	inp := object.union(base_input, {
		"action_id": "restart_workload",
		"reversibility": "R2",
		"params": {"namespace": "aegisops-demo", "workload": "api"},
	})
	not authz.allow with input as inp
}

test_deny_restart_invalid_target_name if {
	inp := object.union(base_input, {
		"action_id": "restart_workload",
		"reversibility": "R2",
		"params": {"namespace": "aegisops-demo", "workload": "Web"},
	})
	not authz.allow with input as inp
}

# ---------------------------------------------------------------------------
# Negative: unknown action.
# ---------------------------------------------------------------------------

test_deny_unknown_action if {
	inp := object.union(base_input, {"action_id": "delete_everything"})
	not authz.allow with input as inp
}

# ---------------------------------------------------------------------------
# Negative: extra params.
# ---------------------------------------------------------------------------

test_deny_extra_params_escalate if {
	inp := object.union(base_input, {"params": {"extra": "x"}})
	not authz.allow with input as inp
}

test_deny_extra_params_collect if {
	inp := object.union(base_input, {
		"action_id": "collect_more_evidence",
		"params": {"scope": "events", "extra": "x"},
	})
	not authz.allow with input as inp
}

test_deny_extra_params_restart if {
	inp := object.union(base_input, {
		"action_id": "restart_workload",
		"reversibility": "R2",
		"params": {"namespace": "aegisops-demo", "workload": "web", "extra": "x"},
	})
	not authz.allow with input as inp
}

# ---------------------------------------------------------------------------
# Negative: changed bundle hash.
# ---------------------------------------------------------------------------

test_deny_changed_bundle_hash if {
	inp := object.union(base_input, {"bundle_hash": "deadbeef"})
	not authz.allow with input as inp
}

# ---------------------------------------------------------------------------
# Negative: different incident ID (mismatch with recommendation_incident_id).
# ---------------------------------------------------------------------------

test_deny_different_incident_id if {
	inp := object.union(base_input, {"incident_id": "inc-9999"})
	not authz.allow with input as inp
}

# ---------------------------------------------------------------------------
# Negative: execution_performed = true.
# ---------------------------------------------------------------------------

test_deny_execution_performed if {
	inp := object.union(base_input, {"execution_performed": true})
	not authz.allow with input as inp
}

# ---------------------------------------------------------------------------
# Negative: requires_human_approval = false.
# ---------------------------------------------------------------------------

test_deny_approval_false if {
	inp := object.union(base_input, {"requires_human_approval": false})
	not authz.allow with input as inp
}

# ---------------------------------------------------------------------------
# Negative: rollback owner_kinds variants.
# ---------------------------------------------------------------------------

rollback_input(owner_kinds) := object.union(base_input, {
	"action_id": "rollback_deployment",
	"params": {"namespace": "aegisops-demo", "deployment": "web"},
	"owner_kinds": owner_kinds,
})

test_deny_rollback_owner_replicaset if {
	inp := rollback_input(["ReplicaSet"])
	not authz.allow with input as inp
}

test_deny_rollback_owner_empty if {
	inp := rollback_input([])
	not authz.allow with input as inp
}

test_deny_rollback_owner_conflicting if {
	inp := rollback_input(["Deployment", "ReplicaSet"])
	not authz.allow with input as inp
}

# ---------------------------------------------------------------------------
# Parameterized: collect_more_evidence scope values.
# ---------------------------------------------------------------------------

valid_scopes := {"events", "previous_logs", "pod_spec_summary"}

test_allow_collect_valid_scopes if {
	every scope in valid_scopes {
		inp := object.union(base_input, {
			"action_id": "collect_more_evidence",
			"params": {"scope": scope},
		})
		authz.allow with input as inp
	}
}

test_deny_collect_invalid_scope if {
	inp := object.union(base_input, {
		"action_id": "collect_more_evidence",
		"params": {"scope": "secrets"},
	})
	not authz.allow with input as inp
}

# ---------------------------------------------------------------------------
# Parameterized: missing required keys in input object.
# ---------------------------------------------------------------------------

required_keys := {
	"policy_version",
	"bundle_hash",
	"catalog_hash",
	"input_hash",
	"cluster_id",
	"incident_id",
	"recommendation_id",
	"recommendation_incident_id",
	"namespace",
	"workload",
	"action_id",
	"params",
	"reversibility",
	"owner_kinds",
	"autonomy_level",
	"execution_performed",
	"requires_human_approval",
}

test_deny_missing_required_key if {
	every key in required_keys {
		inp := object.remove(base_input, {key})
		not authz.allow with input as inp
	}
}

test_deny_wrong_reversibility if {
	every action in {"escalate_to_human", "collect_more_evidence", "restart_workload", "rollback_deployment"} {
		inp := object.union(base_input, {"action_id": action, "reversibility": "R3"})
		not authz.allow with input as inp
	}
}

# ---------------------------------------------------------------------------
# Decision object shape.
# ---------------------------------------------------------------------------

test_decision_allow_true if {
	inp := base_input
	result := authz.decision with input as inp
	result.allow == true
	result.policy_version == data.aegisops_config.policy_version
	result.bundle_hash == data.aegisops_config.bundle_hash
	result.catalog_hash == data.aegisops_config.catalog_hash
	result.input_hash == inp.input_hash
	result.autonomy_level == "L0"
	result.execution_performed == false
}

test_decision_allow_false_on_deny if {
	inp := object.union(base_input, {"namespace": "other-ns"})
	result := authz.decision with input as inp
	result.allow == false
}
