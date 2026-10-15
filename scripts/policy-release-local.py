"""Operator-only signed release/renewal and monotonic activation/revocation of local OPA policy."""

import argparse
import importlib.util
import json
import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from aegisops.authorization.bundle import load_config
from aegisops.authorization.releases import load_release_check
from aegisops.domain.common import utc_now
from aegisops.domain.policy_release import (
    PolicyReleaseBody,
    PolicyReleaseState,
    SignedPolicyRelease,
    authenticate_release,
    release_bytes,
    release_hash,
)
from aegisops.integrity.models import TrustAnchor
from aegisops.io import read_text, unique_object

# Reuse the existing operator-only DPAPI/0600 storage and exclusive writer primitives.
# Neither script is imported by application code or receives instructions from a model.
spec = importlib.util.spec_from_file_location(
    "aegisops_local_operator_keys", Path(__file__).with_name("audit-integrity-local.py")
)
keys = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keys)


def read_model(state, name, model, limit):
    return model.model_validate(
        json.loads(read_text(keys.safe_path(state / name), limit), object_pairs_hook=unique_object)
    )


def replace_state(state, value):
    temporary = state / f"state-{uuid4()}.tmp"
    keys.write_new(temporary, value.model_dump_json().encode("utf-8"))
    try:
        keys.safe_path(state / "state.json")
        os.replace(temporary, state / "state.json")
    finally:
        if temporary.exists():
            temporary.unlink()


def initialize(state, private_path, *, clock=utc_now):
    state = keys.safe_path(state)
    if (state / "state.json").exists():
        raise ValueError("policy state already exists")
    keys.initialize(state, private_path)
    with keys.writer_lock(state):
        initial = PolicyReleaseState(
            generation=1, minimum_revision=1, active_release_hash=None, changed_at=clock()
        )
        keys.write_new(state / "state.json", initial.model_dump_json().encode("utf-8"))


def current(state):
    trust = read_model(state, "trust.json", TrustAnchor, 2048)
    control = read_model(state, "state.json", PolicyReleaseState, 8192)
    if control.active_release_hash is not None:
        release = read_model(
            state, f"releases/{control.active_release_hash}.json", SignedPolicyRelease, 8192
        )
        authenticate_release(release, trust)
        if (
            release_hash(release) != control.active_release_hash
            or release.body.revision != control.minimum_revision
        ):
            raise ValueError("pinned policy state mismatch")
    return trust, control


def issue(state, private_path, config, *, ttl_hours=24, clock=utc_now):
    state = keys.safe_path(state)
    with keys.writer_lock(state):
        trust, control = current(state)
        now = clock()
        if now < control.changed_at or type(ttl_hours) is not int or not 1 <= ttl_hours <= 24:
            raise ValueError("invalid release lifetime or clock")
        revision = control.minimum_revision + int(control.active_release_hash is not None)
        body = PolicyReleaseBody(
            release_id=uuid4(),
            revision=revision,
            key_id=trust.key_id,
            bundle_hash=config.bundle_hash,
            catalog_hash=config.catalog_hash,
            issued_at=now,
            expires_at=now + timedelta(hours=ttl_hours),
        )
        key = keys.read_key(private_path, trust)
        signed = SignedPolicyRelease(body=body, signature=key.sign(release_bytes(body)).hex())
        authenticate_release(signed, trust)
        updated = PolicyReleaseState(
            generation=control.generation + 1,
            minimum_revision=revision,
            active_release_hash=release_hash(signed),
            revoked_revisions=control.revoked_revisions,
            changed_at=now,
        )
        directory = keys.safe_path(state / "releases")
        directory.mkdir(exist_ok=True)
        keys.write_new(
            directory / f"{release_hash(signed)}.json", signed.model_dump_json().encode("utf-8")
        )
        replace_state(state, updated)
        return signed


def revoke(state, revision=None, *, clock=utc_now):
    state = keys.safe_path(state)
    with keys.writer_lock(state):
        _, control = current(state)
        revision = control.minimum_revision if revision is None else revision
        now = clock()
        if (
            control.active_release_hash is None
            or type(revision) is not int
            or not 1 <= revision <= control.minimum_revision
            or now < control.changed_at
        ):
            raise ValueError("invalid revocation")
        if revision in control.revoked_revisions:
            return control
        updated = PolicyReleaseState(
            generation=control.generation + 1,
            minimum_revision=control.minimum_revision,
            active_release_hash=control.active_release_hash,
            revoked_revisions=tuple(sorted((*control.revoked_revisions, revision))),
            changed_at=now,
        )
        replace_state(state, updated)
        return updated


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    state = root / ".local/policy-releases"
    private_path = root.parents[1] / "work/policy-signing/aegisops-policy-ed25519.key"
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init", "issue", "revoke", "check"))
    parser.add_argument("--ttl-hours", type=int, default=24, choices=range(1, 25))
    parser.add_argument("--revision", type=int)
    args = parser.parse_args()
    try:
        if args.command == "init":
            initialize(state, private_path)
            print("Policy trust initialized with a separate protected key; no active release.")
        elif args.command == "issue":
            release = issue(state, private_path, load_config(), ttl_hours=args.ttl_hours)
            print(
                json.dumps(
                    {
                        "revision": release.body.revision,
                        "expires_at": release.body.expires_at.isoformat(),
                        "release_hash": release_hash(release),
                    },
                    indent=2,
                )
            )
        elif args.command == "revoke":
            print(revoke(state, args.revision).model_dump_json(indent=2))
        else:
            check = load_release_check(load_config(), utc_now())
            print(
                json.dumps(
                    {
                        "status": check.status,
                        "revision": check.release.body.revision if check.release else None,
                        "expires_at": check.release.body.expires_at.isoformat()
                        if check.release
                        else None,
                    },
                    indent=2,
                )
            )
            if check.status != "valid":
                raise ValueError("policy not valid")
    except Exception:
        raise SystemExit(
            "Policy operation rejected; inspect trusted release state before retrying."
        ) from None


if __name__ == "__main__":
    main()
