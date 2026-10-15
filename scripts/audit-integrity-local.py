"""Operator-only signing tool. The application can verify but cannot call this tool.

Local trust model: one trusted host/user; no KMS, WORM or protection against that user.
"""

import argparse
import ctypes
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aegisops.audit.postgres import create_audit_engine
from aegisops.domain.common import utc_now
from aegisops.integrity.chain import (
    authenticate,
    build_body,
    canonical,
    checkpoint_hash,
    key_id,
    signing_bytes,
    verify_rows,
)
from aegisops.integrity.models import CheckpointHead, IntegrityError, SignedCheckpoint, TrustAnchor
from aegisops.integrity.postgres import snapshot
from aegisops.integrity.repository import MAX_CHECKPOINT_BYTES, load_checkpoint
from aegisops.io import read_text, unique_object


def safe_path(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise IntegrityError()
    return path


def dpapi(data, *, decrypt=False):
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; current Windows user, no machine-wide flag.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise IntegrityError()
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def write_new(path, data):
    path = safe_path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


@contextmanager
def writer_lock(state):
    state = safe_path(state)
    state.mkdir(parents=True, exist_ok=True)
    lock = state / ".writer.lock"
    # Never take over a stale lock automatically after a crash.
    descriptor = lock.open("xb")
    try:
        yield
    finally:
        descriptor.close()
        lock.unlink()


def initialize(state, private_path):
    state, private_path = safe_path(state), safe_path(private_path)
    with writer_lock(state):
        if (
            private_path.exists()
            or (state / "trust.json").exists()
            or (state / "HEAD.json").exists()
        ):
            raise IntegrityError()
        private_path.parent.mkdir(parents=True, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        raw = key.private_bytes_raw()
        protected = b"DPAPI-v1\x00" + dpapi(raw) if os.name == "nt" else b"POSIX-v1\x00" + raw
        public = key.public_key().public_bytes_raw().hex()
        trust = TrustAnchor(public_key=public, key_id=key_id(public))
        write_new(private_path, protected)
        write_new(state / "trust.json", canonical(trust.model_dump(mode="json")))


def read_key(private_path, trust):
    private_path = safe_path(private_path)
    if os.name != "nt" and (
        stat.S_IMODE(private_path.stat().st_mode) != 0o600
        or private_path.stat().st_uid != os.getuid()
    ):
        raise IntegrityError()
    with private_path.open("rb") as stream:
        protected = stream.read(4097)
    if len(protected) > 4096:
        raise IntegrityError()
    prefix = b"DPAPI-v1\x00" if os.name == "nt" else b"POSIX-v1\x00"
    if not protected.startswith(prefix):
        raise IntegrityError()
    raw = protected[len(prefix) :]
    key = Ed25519PrivateKey.from_private_bytes(dpapi(raw, decrypt=True) if os.name == "nt" else raw)
    if key.public_key().public_bytes_raw().hex() != trust.public_key:
        raise IntegrityError()
    return key


def publish(rows, state, private_path, *, clock=utc_now):
    state = safe_path(state)
    with writer_lock(state):
        trust = TrustAnchor.model_validate(
            json.loads(
                read_text(safe_path(state / "trust.json"), 2048), object_pairs_hook=unique_object
            )
        )
        if trust.key_id != key_id(trust.public_key):
            raise IntegrityError()
        previous = None
        chain_id = uuid4()
        now = clock()
        if (state / "HEAD.json").exists():
            old, old_trust, old_head = load_checkpoint(state)
            if old_trust != trust or verify_rows(rows, old, trust, old_head).status == "failed":
                raise IntegrityError()
            if now <= old.body.created_at:
                raise IntegrityError()
            previous, chain_id = old_head.checkpoint_hash, old_head.chain_id
        key = read_key(private_path, trust)
        body = build_body(
            rows,
            chain_id=chain_id,
            checkpoint_id=uuid4(),
            created_at=now,
            key=trust.key_id,
            previous_checkpoint_hash=previous,
        )
        signed = SignedCheckpoint(body=body, signature=key.sign(signing_bytes(body)).hex())
        head = CheckpointHead(
            chain_id=chain_id,
            checkpoint_id=body.checkpoint_id,
            checkpoint_hash=checkpoint_hash(signed),
        )
        authenticate(signed, trust, head)
        content = canonical(signed.model_dump(mode="json"))
        if len(content) > MAX_CHECKPOINT_BYTES:
            raise IntegrityError()
        directory = safe_path(state / "checkpoints")
        directory.mkdir(exist_ok=True)
        write_new(directory / f"{head.checkpoint_hash}.json", content)
        # Validate the prospective archive before advancing the pinned pointer, including bounds.
        load_checkpoint(state, expected_head=head)
        temporary = state / f"HEAD-{uuid4()}.tmp"
        write_new(temporary, canonical(head.model_dump(mode="json")))
        try:
            safe_path(state / "HEAD.json")
            os.replace(temporary, state / "HEAD.json")
        finally:
            if temporary.exists():
                temporary.unlink()
        return head


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    state = root / ".local" / "audit-integrity"
    private_path = root.parents[1] / "work" / "audit-signing" / "aegisops-ed25519.key"
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("init", "checkpoint"))
    args = parser.parse_args()
    engine = None
    try:
        if args.command == "init":
            initialize(state, private_path)
            print("Audit signing initialized; protected key outside repository. No checkpoint yet.")
            return
        for line in (root / ".env").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                name, value = line.split("=", 1)
                if not name.startswith("AEGISOPS_"):
                    raise IntegrityError()
                os.environ[name] = value
        engine = create_audit_engine()
        head = publish(snapshot(engine), state, private_path)
        print(head.model_dump_json(indent=2))
    except Exception:
        raise SystemExit("Audit signing rejected; verify trusted HEAD before retrying.") from None
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
