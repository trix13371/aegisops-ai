"""Developer-only generation/check of the packaged OPA policy identity."""

import argparse
import json
from pathlib import Path

from aegisops.authorization.bundle import compute_config

parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1] / "policies" / "opa"
payload = {"aegisops_config": compute_config(root).model_dump(mode="json")}
destination = root / "data.json"
if args.check:
    if json.loads(destination.read_text(encoding="utf-8")) != payload:
        raise SystemExit("OPA policy metadata differs from canonical inputs")
else:
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print("OPA policy metadata verified." if args.check else "OPA policy metadata prepared.")
