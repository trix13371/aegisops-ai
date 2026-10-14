"""Create a private local environment. Never overwrite or print credentials."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / ".env"
passwords = {name: secrets.token_hex(24) for name in ("BOOTSTRAP", "OWNER", "APP")}
lines = [f"AEGISOPS_{name}_PASSWORD={value}" for name, value in passwords.items()]
lines += [
    "AEGISOPS_PG_PORT=55432",
    "AEGISOPS_DATABASE_URL=postgresql+psycopg://aegisops_app:"
    + passwords["APP"]
    + "@127.0.0.1:55432/aegisops_local",
    "AEGISOPS_MIGRATION_URL=postgresql+psycopg://aegisops_owner:"
    + passwords["OWNER"]
    + "@127.0.0.1:55432/aegisops_local",
]
try:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    raise SystemExit("Private .env already exists; kept unchanged.") from None
with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
    stream.write("\n".join(lines) + "\n")
print("Private local .env created. Credentials were not displayed.")
