"""Read local operator-pinned policy releases; never sign or update trusted state."""

import json
from datetime import datetime
from pathlib import Path

from aegisops.authorization.bundle import BundleConfig
from aegisops.domain.policy_release import (
    PolicyReleaseCheck,
    PolicyReleaseState,
    SignedPolicyRelease,
    release_status,
)
from aegisops.integrity.models import TrustAnchor
from aegisops.io import contained_file, read_text, unique_object


def load_release_check(config: BundleConfig, now: datetime) -> PolicyReleaseCheck:
    release = state = trust = None
    status = "policy_invalid"
    try:
        root = Path.cwd()
        base = ".local/policy-releases"
        trust = TrustAnchor.model_validate(
            json.loads(
                read_text(contained_file(root, f"{base}/trust.json"), 2048),
                object_pairs_hook=unique_object,
            )
        )
        state = PolicyReleaseState.model_validate(
            json.loads(
                read_text(contained_file(root, f"{base}/state.json"), 8192),
                object_pairs_hook=unique_object,
            )
        )
        if state.active_release_hash is None:
            return PolicyReleaseCheck(
                checked_at=now, status="policy_missing", state=state, trust=trust
            )
        release = SignedPolicyRelease.model_validate(
            json.loads(
                read_text(
                    contained_file(root, f"{base}/releases/{state.active_release_hash}.json"), 8192
                ),
                object_pairs_hook=unique_object,
            )
        )
        status = release_status(
            release,
            state,
            trust,
            now,
            bundle_hash=config.bundle_hash,
            catalog_hash=config.catalog_hash,
            policy_version=config.policy_version,
        )
    except Exception:
        # Filesystem/parser details never enter audit. Missing files are a safe denial too.
        status = (
            "policy_missing"
            if not (Path.cwd() / ".local/policy-releases").exists()
            else "policy_invalid"
        )
    return PolicyReleaseCheck.model_validate(
        {"checked_at": now, "status": status, "release": release, "state": state, "trust": trust}
    )
