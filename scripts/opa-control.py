"""Developer-only local emergency controls; never available to a model or app."""

import argparse
import os
from pathlib import Path

from aegisops.authorization.control import load_control
from aegisops.domain.authorization import AuthorizationControl
from aegisops.io import contained_file

root = Path(__file__).resolve().parents[1]
os.chdir(root)
parser = argparse.ArgumentParser()
parser.add_argument(
    "command",
    choices=(
        "init",
        "status",
        "block",
        "resume",
        "block-cluster",
        "allow-cluster",
        "block-action",
        "allow-action",
    ),
)
parser.add_argument("scope", nargs="?")
args = parser.parse_args()
try:
    path = root / ".local" / "authorization" / "control.json"
    # Reject symbolic components even when initializing a new file.
    for parent in (root, root / ".local", path.parent, path):
        if parent.is_symlink():
            raise ValueError("symbolic control path")
    if args.command == "init" and not path.exists():
        control = AuthorizationControl(generation=1, global_blocked=True)
    else:
        control = load_control()
    if args.command not in {"init", "status"}:
        raw = control.model_dump(mode="json")
        raw["generation"] += 1
        if args.command in {"block", "resume"}:
            if args.scope is not None:
                raise ValueError("unexpected scope")
            raw["global_blocked"] = args.command == "block"
        else:
            if not args.scope:
                raise ValueError("missing scope")
            key = "blocked_clusters" if args.command.endswith("cluster") else "blocked_actions"
            values = set(raw[key])
            if args.command.startswith("block"):
                values.add(args.scope)
            else:
                values.discard(args.scope)
            raw[key] = sorted(values)
        control = AuthorizationControl.model_validate(raw)
    if args.command != "status":
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        # Exclusive create also rejects a planted symbolic temporary path.
        with temporary.open("x", encoding="utf-8") as output:
            output.write(control.model_dump_json(indent=2) + "\n")
        temporary.replace(path)
    contained_file(root, ".local/authorization/control.json")
    print(control.model_dump_json(indent=2))
except Exception:
    raise SystemExit("Local authorization control rejected; no permission granted.") from None
