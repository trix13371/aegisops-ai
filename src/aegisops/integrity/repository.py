"""Bounded reads of an operator-pinned trust anchor and current checkpoint, never a signing key."""

import json
from pathlib import Path

from aegisops.integrity.chain import authenticate
from aegisops.integrity.models import CheckpointHead, IntegrityError, SignedCheckpoint, TrustAnchor
from aegisops.io import contained_file, read_text, unique_object

MAX_CHECKPOINT_BYTES = 4 * 1024 * 1024
MAX_CHECKPOINT_HISTORY = 64
MAX_HISTORY_BYTES = 32 * 1024 * 1024


def location() -> Path:
    return Path.cwd() / ".local" / "audit-integrity"


def load_checkpoint(
    root: Path | None = None,
    *,
    expected_head: CheckpointHead | None = None,
) -> tuple[SignedCheckpoint, TrustAnchor, CheckpointHead]:
    try:
        root = location() if root is None else root
        # Check from cwd to reject a symbolic .local directory too in the normal CLI path.
        if root == location() and expected_head is None:
            contained_file(Path.cwd(), ".local/audit-integrity/HEAD.json")
        trust = TrustAnchor.model_validate(
            json.loads(
                read_text(contained_file(root, "trust.json"), 2048), object_pairs_hook=unique_object
            )
        )
        head = (
            CheckpointHead.model_validate(expected_head)
            if expected_head is not None
            else CheckpointHead.model_validate(
                json.loads(
                    read_text(contained_file(root, "HEAD.json"), 2048),
                    object_pairs_hook=unique_object,
                )
            )
        )
        current_hash: str | None = head.checkpoint_hash
        seen: set[str] = set()
        total = 0
        latest = later = None
        while current_hash is not None:
            if current_hash in seen or len(seen) >= MAX_CHECKPOINT_HISTORY:
                raise IntegrityError()
            seen.add(current_hash)
            content = read_text(
                contained_file(root, f"checkpoints/{current_hash}.json"), MAX_CHECKPOINT_BYTES
            )
            total += len(content.encode("utf-8"))
            if total > MAX_HISTORY_BYTES:
                raise IntegrityError()
            checkpoint = SignedCheckpoint.model_validate(
                json.loads(content, object_pairs_hook=unique_object)
            )
            expected_head = (
                head
                if latest is None
                else CheckpointHead(
                    chain_id=head.chain_id,
                    checkpoint_id=checkpoint.body.checkpoint_id,
                    checkpoint_hash=current_hash,
                )
            )
            authenticate(checkpoint, trust, expected_head)
            if later is not None:
                if checkpoint.body.created_at >= later.body.created_at:
                    raise IntegrityError()
                rows = {entry.run_id: entry.row_hash for entry in later.body.entries}
                if any(
                    rows.get(entry.run_id) != entry.row_hash for entry in checkpoint.body.entries
                ):
                    raise IntegrityError()
            latest = latest or checkpoint
            later = checkpoint
            current_hash = checkpoint.body.previous_checkpoint_hash
        if latest is None:
            raise IntegrityError()
        return latest, trust, head
    except Exception:
        raise IntegrityError() from None
