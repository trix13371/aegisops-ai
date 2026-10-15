"""Operator enrollment for one local collector; independent Ed25519 key, no cluster calls."""

import argparse
import hashlib
import importlib.util
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aegisops.telemetry.contracts import CollectorTrust, TelemetryError
from aegisops.telemetry.files import load_trust, read_object, safe_path, write_new


def initialize(root: Path, registration_id: UUID) -> CollectorTrust:
    root = safe_path(root)
    state = safe_path(root / ".local/telemetry")
    state.mkdir(parents=True, exist_ok=True)
    lock = state / ".init.lock"
    handle = lock.open("xb")
    try:
        if (state / "trust.json").exists():
            raise TelemetryError()
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes_raw()
        trust = CollectorTrust(
            registration_id=registration_id,
            public_key=public.hex(),
            key_id=hashlib.sha256(public).hexdigest(),
        )
        directory = safe_path(root.parents[1] / "work/collector-signing")
        directory.mkdir(parents=True, exist_ok=True)
        # Reuse the operator ACL helper, never import this script into either runtime.
        spec = importlib.util.spec_from_file_location(
            "local_tls_operator", Path(__file__).with_name("opa-tls-local.py")
        )
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        helper.restrict_directory(directory)
        write_new(
            directory / f"{trust.key_id}.pem",
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        write_new(state / "trust.json", trust.model_dump_json(indent=2).encode())
        return trust
    finally:
        handle.close()
        lock.unlink()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init", "check"))
    args = parser.parse_args()
    try:
        if args.command == "init":
            state = read_object(root / ".local/kind/state.json", 4096)
            if not isinstance(state, dict) or state.get("cluster_name") != "aegisops-demo":
                raise TelemetryError()
            initialize(root, UUID(str(state["instance_id"])))
            print(
                "Collector enrolled; separate key outside Git. Existing trust is never overwritten."
            )
        else:
            load_trust(root)
            print("Collector public trust valid; no cluster, model or database request.")
    except Exception:
        raise SystemExit("Collector enrollment rejected. Do not delete existing trust.") from None


if __name__ == "__main__":
    main()
