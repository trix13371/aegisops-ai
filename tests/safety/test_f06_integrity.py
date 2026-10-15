"""Pinned public trust, bounded files, crash-safe publication and no signing authority in app."""

import ast
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from typer.testing import CliRunner

from aegisops.cli.app import app
from aegisops.integrity.chain import verify_rows
from aegisops.integrity.models import IntegrityError, RowDigest
from aegisops.integrity.repository import load_checkpoint

pytestmark = pytest.mark.safety
ROOT = Path(__file__).resolve().parents[2]
TIME = datetime(2026, 10, 15, tzinfo=UTC)


@pytest.fixture
def signer():
    spec = importlib.util.spec_from_file_location(
        "local_audit_signer", ROOT / "scripts/audit-integrity-local.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def signing_state(tmp_path, signer):
    state, key = tmp_path / "external-points", tmp_path / "private" / "key.blob"
    signer.initialize(state, key)
    rows = (RowDigest(run_id=UUID(int=1), row_hash="a" * 64),)
    head = signer.publish(rows, state, key, clock=lambda: TIME)
    return state, key, rows, head


def test_real_key_round_trip_and_no_overwrite(signing_state, signer):
    state, key, rows, head = signing_state
    checkpoint, trust, loaded = load_checkpoint(state)
    assert loaded == head and verify_rows(rows, checkpoint, trust, head).status == "verified"
    original = key.read_bytes()
    with pytest.raises(IntegrityError):
        signer.initialize(state, key)
    assert key.read_bytes() == original
    assert not (state / ".writer.lock").exists()


def test_checkpoint_chain_and_unanchored_status(signing_state, signer):
    state, key, rows, head = signing_state
    added = (*rows, RowDigest(run_id=UUID(int=2), row_hash="b" * 64))
    old, trust, loaded = load_checkpoint(state)
    report = verify_rows(added, old, trust, loaded)
    assert report.status == "unanchored" and report.unanchored_records == 1
    new_head = signer.publish(added, state, key, clock=lambda: TIME + timedelta(seconds=1))
    new, trust, loaded = load_checkpoint(state)
    assert loaded == new_head and new_head.chain_id == head.chain_id
    assert new.body.previous_checkpoint_hash == head.checkpoint_hash
    assert verify_rows(added, new, trust, loaded).status == "verified"


@pytest.mark.parametrize("change", ["modified", "deleted"])
def test_signer_refuses_to_bless_corrupted_history(signing_state, signer, change):
    state, key, rows, head = signing_state
    changed = () if change == "deleted" else (rows[0].model_copy(update={"row_hash": "b" * 64}),)
    with pytest.raises(IntegrityError):
        signer.publish(changed, state, key, clock=lambda: TIME + timedelta(seconds=1))
    assert load_checkpoint(state)[2] == head


def test_head_publish_failure_keeps_last_trusted_point(signing_state, signer, monkeypatch):
    state, key, rows, head = signing_state
    replace = signer.os.replace

    def fail(*args):
        raise OSError("password=DO_NOT_PRINT")

    monkeypatch.setattr(signer.os, "replace", fail)
    with pytest.raises(OSError):
        signer.publish(rows, state, key, clock=lambda: TIME + timedelta(seconds=1))
    assert load_checkpoint(state)[2] == head
    assert not (state / ".writer.lock").exists()
    assert not list(state.glob("HEAD-*.tmp"))
    monkeypatch.setattr(signer.os, "replace", replace)
    new = signer.publish(rows, state, key, clock=lambda: TIME + timedelta(seconds=2))
    assert load_checkpoint(state)[2] == new


def test_missing_older_checkpoint_rejected(signing_state, signer):
    state, key, rows, head = signing_state
    signer.publish(rows, state, key, clock=lambda: TIME + timedelta(seconds=1))
    (state / "checkpoints" / f"{head.checkpoint_hash}.json").unlink()
    with pytest.raises(IntegrityError):
        load_checkpoint(state)


@pytest.mark.parametrize("target", ["trust", "head", "checkpoint"])
def test_modified_trust_or_checkpoint_rejected(signing_state, target):
    state, _, _, head = signing_state
    path = state / ("trust.json" if target == "trust" else "HEAD.json")
    if target == "checkpoint":
        path = state / "checkpoints" / f"{head.checkpoint_hash}.json"
    payload = json.loads(path.read_text())
    if target == "trust":
        payload["public_key"] = "0" * 64
    elif target == "head":
        payload["checkpoint_hash"] = "0" * 64
    else:
        payload["signature"] = "0" * 128
    path.write_text(json.dumps(payload))
    with pytest.raises(IntegrityError):
        load_checkpoint(state)


def test_missing_duplicate_or_oversize_files_fail_closed(signing_state, monkeypatch):
    state, _, _, _ = signing_state
    monkeypatch.setattr("aegisops.integrity.repository.MAX_CHECKPOINT_BYTES", 16)
    with pytest.raises(IntegrityError):
        load_checkpoint(state)
    (state / "HEAD.json").write_text('{"chain_id":1,"chain_id":2}')
    with pytest.raises(IntegrityError):
        load_checkpoint(state)
    (state / "HEAD.json").unlink()
    with pytest.raises(IntegrityError):
        load_checkpoint(state)


def test_history_limits_and_symbolic_root(signing_state, monkeypatch):
    state, _, _, _ = signing_state
    monkeypatch.setattr("aegisops.integrity.repository.MAX_CHECKPOINT_HISTORY", 0)
    with pytest.raises(IntegrityError):
        load_checkpoint(state)
    monkeypatch.setattr("aegisops.integrity.repository.MAX_CHECKPOINT_HISTORY", 64)
    monkeypatch.setattr("aegisops.integrity.repository.MAX_HISTORY_BYTES", 16)
    with pytest.raises(IntegrityError):
        load_checkpoint(state)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == state or original(path))
    with pytest.raises(IntegrityError):
        load_checkpoint(state)


def test_writer_lock_and_rollback_clock_refuse_publication(signing_state, signer):
    state, key, rows, _ = signing_state
    with pytest.raises(IntegrityError):
        signer.publish(rows, state, key, clock=lambda: TIME)
    lock = state / ".writer.lock"
    lock.write_text("abandoned")
    with pytest.raises(FileExistsError):
        signer.publish(rows, state, key)
    assert lock.read_text() == "abandoned"


def test_archive_limit_does_not_advance_head(signing_state, signer, monkeypatch):
    state, key, rows, head = signing_state
    monkeypatch.setattr("aegisops.integrity.repository.MAX_CHECKPOINT_HISTORY", 1)
    with pytest.raises(IntegrityError):
        signer.publish(rows, state, key, clock=lambda: TIME + timedelta(seconds=1))
    assert load_checkpoint(state)[2] == head


def test_signer_rejects_symbolic_parent(signing_state, signer, monkeypatch):
    state, key, rows, _ = signing_state
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == state.parent or original(path))
    with pytest.raises(IntegrityError):
        signer.publish(rows, state, key, clock=lambda: TIME + timedelta(seconds=1))


@pytest.mark.parametrize("new_row", [False, True])
def test_cli_verified_and_unanchored_exit_codes(signing_state, monkeypatch, new_row):
    state, _, rows, _ = signing_state
    checkpoint, trust, head = load_checkpoint(state)
    current = (*rows, RowDigest(run_id=UUID(int=2), row_hash="b" * 64)) if new_row else rows
    report = verify_rows(current, checkpoint, trust, head)
    monkeypatch.setattr("aegisops.cli.app.verify_database", lambda: report)
    result = CliRunner().invoke(app, ["audit", "verify"])
    assert result.exit_code == int(new_row)
    assert json.loads(result.output)["status"] == ("unanchored" if new_row else "verified")


def test_cli_does_not_print_raw_storage_error(monkeypatch):
    def fail():
        raise IntegrityError()

    monkeypatch.setattr("aegisops.cli.app.verify_database", fail)
    result = CliRunner().invoke(app, ["audit", "verify"])
    assert result.exit_code == 1 and "FAILED" in result.output
    assert all(name not in result.output for name in ("Traceback", "private", "payload_json"))


def test_application_cannot_sign_or_publish_checkpoints():
    source = ROOT / "src/aegisops/integrity"
    for path in source.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert all(name.name != "Ed25519PrivateKey" for name in node.names)
            if isinstance(node, ast.Call):
                assert getattr(node.func, "attr", "") not in {
                    "sign",
                    "write_text",
                    "write_bytes",
                    "replace",
                    "unlink",
                }
