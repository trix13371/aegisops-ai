"""Developer wrapper: load private .env literally, never print values.

This is outside src/aegisops. It only runs explicitly allowed developer checks.
"""

import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
allowed = {
    "aegisops",
    "aegisops-control",
    "alembic",
    "pytest",
    "ruff",
    "mypy",
    "pip_audit",
    "pre_commit",
}
if len(sys.argv) < 2 or sys.argv[1] not in allowed:
    raise SystemExit("Specify an allowed development tool")
environment = os.environ.copy()
try:
    local_lines = (root / ".env").read_text(encoding="utf-8").splitlines()
except OSError:
    raise SystemExit("Create private .env with scripts/init-local-env.py first") from None
for line in local_lines:
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        raise SystemExit("Malformed local environment file; values were not displayed")
    key, value = line.split("=", 1)
    if not key.startswith("AEGISOPS_"):
        raise SystemExit("Unexpected environment key")
    environment[key] = value
arguments = sys.argv[1:]
if arguments[0] == "aegisops":
    # Typer app entry is a console script; use a fixed import, no command interpolation.
    command = [sys.executable, "-c", "from aegisops.cli.app import app; app()", *arguments[1:]]
elif arguments[0] == "aegisops-control":
    environment = {
        key: value
        for key, value in environment.items()
        if not (
            (
                key.upper().startswith(("AEGISOPS_", "PG", "OPENAI_", "OPENROUTER_"))
                and key != "AEGISOPS_DATABASE_URL"
            )
            or key.upper() == "KUBECONFIG"
        )
    }
    command = [sys.executable, "-m", "aegisops.controlplane.cli", *arguments[1:]]
else:
    command = [sys.executable, "-m", *arguments]
result = subprocess.run(command, cwd=root, env=environment, check=False)  # noqa: S603
raise SystemExit(result.returncode)
