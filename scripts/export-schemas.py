"""Deterministic developer export; --check rejects missing/stale schemas."""

import argparse
import json
from pathlib import Path

from aegisops.domain.schemas import contract_schemas

parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
arguments = parser.parse_args()
destination = Path(__file__).resolve().parents[1] / "schemas"
schemas = contract_schemas()
if arguments.check:
    if {path.stem for path in destination.glob("*.json")} != set(schemas):
        raise SystemExit("Missing or unexpected exported schemas")
else:
    destination.mkdir(exist_ok=True)
for name, schema in schemas.items():
    expected = json.dumps(schema, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    path = destination / f"{name}.json"
    if arguments.check:
        if path.read_text(encoding="utf-8") != expected:
            raise SystemExit("Exported schemas differ from runtime contracts")
    else:
        path.write_text(expected, encoding="utf-8")
print(f"{len(schemas)} schemas {'verified' if arguments.check else 'exported'}.")
