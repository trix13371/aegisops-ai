package system.authz

import rego.v1

default allow := false

allow if {
	input.method == "POST"
	input.path == ["v1", "data", "aegisops", "authz", "decision"]
}
