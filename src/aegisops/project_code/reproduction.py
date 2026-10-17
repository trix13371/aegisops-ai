"""Deterministic reproduction snapshots and verification validation.

Pure helpers: no code execution, no network, no live project folder access.
Snapshots are built from frozen previews only.
"""

import hashlib
import io
import json
import re
import tarfile
from pathlib import Path
from typing import Final

from aegisops.project_code.lab import (
    Proposal,
    VerificationResult,
    VerifyClaim,
    classify_runs,
    patched_preview,
)
from aegisops.project_code.models import CodePreview
from aegisops.project_runner.compatibility import inspect_package
from aegisops.project_runner.models import Environment, SnapshotInfo, profile_for
from aegisops.project_runner.snapshot import (
    JS_SUFFIXES,
    SUFFIXES,
    Snapshot,
)

MAX_SNAPSHOT_BYTES: Final = 1024 * 1024
MAX_SNAPSHOT_FILES: Final = 1000

PY_TEST_NAME: Final = "test_aegis_repro.py"
JS_TEST_NAME: Final = "aegis-repro.test.mjs"

_ENV_DIRECTIVE = re.compile(rb"@(?:vitest|jest)-environment\b", re.IGNORECASE)


def _suffixes(profile: str) -> frozenset[str]:
    if profile == "python-checks-v1":
        return SUFFIXES
    return (SUFFIXES - {".py"}) | JS_SUFFIXES


def _test_name(language: str) -> str:
    return PY_TEST_NAME if language == "python" else JS_TEST_NAME


def _build(
    preview: CodePreview,
    proposal: Proposal,
    environment: Environment,
) -> Snapshot:
    checked = CodePreview.model_validate(preview)
    result = Proposal.model_validate(proposal)
    suffixes = _suffixes(profile_for(environment))
    target = next((item for item in checked.files if item.id == result.file_id), None)
    if target is None:
        raise ValueError("unknown file id")
    test_name = _test_name(result.test_language)
    contents: list[tuple[str, bytes]] = []
    seen = 0
    total = 0
    python_files = 0
    javascript_files = 0
    for item in checked.files:
        path = Path(item.path)
        if path.suffix.lower() not in suffixes:
            continue
        relative = path.as_posix()
        if relative == test_name:
            raise ValueError("generated test collides with a preview file")
        data = item.content.encode("utf-8")
        seen += 1
        total += len(data)
        if seen > MAX_SNAPSHOT_FILES or total > MAX_SNAPSHOT_BYTES:
            raise ValueError("snapshot exceeds budget")
        python_files += path.suffix.lower() == ".py"
        javascript_files += path.suffix.lower() in JS_SUFFIXES
        contents.append((relative, data))
    test_bytes = result.test_code.encode("utf-8")
    total += len(test_bytes)
    if total > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds budget")
    if test_name.endswith(".py"):
        python_files += 1
    else:
        javascript_files += 1
    contents.append((test_name, test_bytes))
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for relative, data in sorted(contents):
            member = tarfile.TarInfo(relative)
            member.size, member.mode = len(data), 0o600
            archive.addfile(member, io.BytesIO(data))
    data = output.getvalue()
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot exceeds budget")
    compatibility = None
    environment_directive = False
    if profile_for(environment) != "python-checks-v1":
        raw_package = next((body for name, body in contents if name == "package.json"), None)
        try:
            compatibility = inspect_package(
                raw_package.decode("utf-8") if raw_package is not None else None
            )
        except UnicodeError:
            compatibility = inspect_package("")
        environment_directive = (
            any(
                _ENV_DIRECTIVE.search(body)
                for name, body in contents
                if Path(name).suffix in JS_SUFFIXES
            )
            and environment == "node-vitest-v1"
        )
    return Snapshot(
        data=data,
        compatibility=compatibility,
        environment_directive=environment_directive,
        info=SnapshotInfo(
            sha256=hashlib.sha256(data).hexdigest(),
            files=len(contents),
            python_files=python_files,
            javascript_files=javascript_files,
            bytes=total,
            skipped=0,
        ),
    )


def verification_snapshots(
    preview: CodePreview,
    proposal: Proposal,
    environment: Environment,
) -> tuple[Snapshot, Snapshot]:
    """Return (before, after) snapshots; only the proposal file changes."""
    checked = CodePreview.model_validate(preview)
    result = Proposal.model_validate(proposal)
    before = _build(checked, result, environment)
    after = _build(patched_preview(checked, result), result, environment)
    return before, after


def _canonical(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: object) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def validate_verification(
    preview: CodePreview,
    proposal: Proposal,
    claim: VerifyClaim,
    result: VerificationResult,
) -> None:
    """Validate a verification result against the frozen preview and claim."""
    checked = CodePreview.model_validate(preview)
    proposal_checked = Proposal.model_validate(proposal)
    claim_checked = VerifyClaim.model_validate(claim)
    result_checked = VerificationResult.model_validate(result)
    if claim_checked.preview_id != checked.id:
        raise ValueError("claim preview id mismatch")
    if result_checked.preview_id != checked.id:
        raise ValueError("result preview id mismatch")
    if result_checked.proposal_sha256 != claim_checked.proposal_sha256:
        raise ValueError("proposal hash mismatch")
    if result_checked.completed_at < claim_checked.created_at:
        raise ValueError("result predates claim")
    if result_checked.before.completed_at < claim_checked.created_at:
        raise ValueError("before run predates claim")
    if result_checked.after.completed_at < claim_checked.created_at:
        raise ValueError("after run predates claim")
    if (
        result_checked.before.environment != claim_checked.environment
        or result_checked.after.environment != claim_checked.environment
    ):
        raise ValueError("environment mismatch")
    if result_checked.before.id == result_checked.after.id:
        raise ValueError("run ids must be distinct")
    if (
        result_checked.before.project_id != checked.project_id
        or result_checked.after.project_id != checked.project_id
    ):
        raise ValueError("project id mismatch")
    before_snapshot, after_snapshot = verification_snapshots(
        checked, proposal_checked, claim_checked.environment
    )
    expected_before = before_snapshot.info.sha256
    expected_after = after_snapshot.info.sha256
    if result_checked.before.snapshot is not None:
        if result_checked.before.snapshot.sha256 != expected_before:
            raise ValueError("before snapshot hash mismatch")
    if result_checked.after.snapshot is not None:
        if result_checked.after.snapshot.sha256 != expected_after:
            raise ValueError("after snapshot hash mismatch")
    expected_status = classify_runs(
        result_checked.before,
        result_checked.after,
        expected_before,
        expected_after,
    )
    if result_checked.status != expected_status:
        raise ValueError("verification status mismatch")


__all__ = (
    "JS_TEST_NAME",
    "PY_TEST_NAME",
    "validate_verification",
    "verification_snapshots",
)
