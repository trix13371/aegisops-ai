package aegisops.authz

import rego.v1

default allow := false

common if {
	object.keys(input) == {"policy_version", "bundle_hash", "catalog_hash", "input_hash", "cluster_id", "incident_id", "recommendation_id", "recommendation_incident_id", "namespace", "workload", "action_id", "params", "reversibility", "owner_kinds", "autonomy_level", "execution_performed", "requires_human_approval"}
	input.policy_version == data.aegisops_config.policy_version
	input.bundle_hash == data.aegisops_config.bundle_hash
	input.catalog_hash == data.aegisops_config.catalog_hash
	regex.match("^[0-9a-f]{64}$", input.input_hash)
	input.incident_id == input.recommendation_incident_id
	input.namespace == "aegisops-demo"
	input.autonomy_level == "L0"
	input.execution_performed == false
	input.requires_human_approval == true
	every name in [input.cluster_id, input.incident_id, input.recommendation_id, input.recommendation_incident_id, input.namespace, input.action_id] {
		valid_identifier(name, 128)
	}
	valid_identifier(input.workload, 253)
	is_object(input.params)
	is_array(input.owner_kinds)
	count(input.owner_kinds) <= 8
	every kind in input.owner_kinds {
		valid_identifier(kind, 128)
	}
}

valid_identifier(value, limit) if {
	is_string(value)
	count(value) >= 1
	count(value) <= limit
	regex.match("\\S", value)
}

allow if {
	common
	input.action_id == "escalate_to_human"
	input.reversibility == "R1"
	object.keys(input.params) == set()
}

allow if {
	common
	input.action_id == "collect_more_evidence"
	input.reversibility == "R1"
	object.keys(input.params) == {"scope"}
	input.params.scope in {"events", "previous_logs", "pod_spec_summary"}
}

valid_target(name) if {
	is_string(name)
	count(name) >= 1
	count(name) <= 63
	regex.match("^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", name)
	name == input.workload
	input.params.namespace == input.namespace
}

allow if {
	common
	input.action_id == "restart_workload"
	input.reversibility == "R2"
	object.keys(input.params) == {"namespace", "workload"}
	valid_target(input.params.workload)
}

allow if {
	common
	input.action_id == "rollback_deployment"
	input.reversibility == "R1"
	object.keys(input.params) == {"namespace", "deployment"}
	valid_target(input.params.deployment)
	input.owner_kinds == ["Deployment"]
}

decision := {
	"allow": allow,
	"policy_version": data.aegisops_config.policy_version,
	"bundle_hash": data.aegisops_config.bundle_hash,
	"catalog_hash": data.aegisops_config.catalog_hash,
	"input_hash": object.get(input, "input_hash", ""),
	"autonomy_level": "L0",
	"execution_performed": false,
}
