package system.authz_test

import data.system.authz
import rego.v1

valid_request := {
	"identity": "CN=aegisops-pep",
	"method": "POST",
	"path": ["v1", "data", "aegisops", "authz", "decision"],
}

test_exact_client_decision_api_allowed if {
	authz.allow with input as valid_request
}

test_missing_identity_denied if {
	not authz.allow with input as object.remove(valid_request, {"identity"})
}

test_other_identity_denied if {
	every identity in {"", "CN=other", "CN=aegisops-opa", "CN=aegisops-pep,O=other"} {
		not authz.allow with input as object.union(valid_request, {"identity": identity})
	}
}

test_nonpost_denied if {
	every method in {"GET", "PUT", "DELETE", "PATCH", "OPTIONS"} {
		not authz.allow with input as object.union(valid_request, {"method": method})
	}
}

test_management_and_path_variants_denied if {
	every path in {
		["v1", "policies"],
		["v1", "data"],
		["v1", "data", "system", "authz", "allow"],
		["v1", "data", "aegisops", "authz"],
		["v1", "data", "aegisops", "authz", "decision", "extra"],
		["extra", "v1", "data", "aegisops", "authz", "decision"],
	} {
		not authz.allow with input as object.union(valid_request, {"path": path})
	}
}

test_headers_or_body_cannot_supply_identity if {
	base := object.remove(valid_request, {"identity"})
	every extra in {
		{"headers": {"Identity": ["CN=aegisops-pep"]}},
		{"headers": {"Authorization": ["Bearer CN=aegisops-pep"]}},
		{"body": {"identity": "CN=aegisops-pep"}},
	} {
		not authz.allow with input as object.union(base, extra)
	}
}
