"""Verification worker for one already claimed fix proposal.

Runs the fixed trusted local runner against two frozen snapshots (before and
after) inside the existing hardened container. Never executes snapshots on the
host, never installs anything, and never fabricates a verdict: an uncertain
outcome stays pending.
"""

import importlib.util
import json
import sys
from pathlib import Path
from uuid import UUID

from aegisops.domain.common import utc_now
from aegisops.project_code.lab import (
    LabRepository,
    VerificationResult,
    classify_runs,
    verification_snapshots,
)
from aegisops.project_code.repository import CodeRepository
from aegisops.project_runner.models import RunResult
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.repository import ProjectRepository

ROOT = Path(__file__).resolve().parents[1]


def _load_runner():
    path = Path(__file__).resolve().parent / "project-runner-local.py"
    spec = importlib.util.spec_from_file_location("aegisops_project_runner_local", path)
    if spec is None or spec.loader is None:
        raise ValueError("runner unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = ROOT
    return module


def main() -> int:
    identifier = None
    owns_start = False
    projects = ProjectRepository(ROOT / ".local/projects")
    code = CodeRepository(ROOT / ".local/project-code-ai", projects)
    lab = LabRepository(ROOT / ".local/project-code-lab", code)
    runs = RunRepository(ROOT / ".local/project-runs")
    try:
        if len(sys.argv) != 2:
            raise ValueError("invalid arguments")
        identifier = UUID(sys.argv[1])
        claim = lab.read_verify_claim(identifier)
        if claim is None:
            raise ValueError("unclaimed verification")
        result = lab.result(identifier)
        if result is None or result.status != "completed" or result.proposal is None:
            raise ValueError("no completed proposal")
        preview, _ = lab.evidence(identifier)
        lab.verification_store.write(identifier, "started", "{}")
        owns_start = True
        before_snapshot, after_snapshot = verification_snapshots(
            preview, result.proposal, claim.environment
        )
        before_run = runs.create(preview.project_id, claim.environment)
        after_run = runs.create(preview.project_id, claim.environment)
        if before_run.id == after_run.id:
            raise ValueError("run claims collided")
        module = _load_runner()
        before_result = module.run_check(before_run.id, prepared_snapshot=before_snapshot)
        if not isinstance(before_result, RunResult):
            raise ValueError("runner returned no result")
        # Revalidate the frozen evidence between the two runs.
        if lab.read_verify_claim(identifier) != claim or lab.result(identifier) != result:
            raise ValueError("evidence changed between runs")
        after_result = module.run_check(after_run.id, prepared_snapshot=after_snapshot)
        if not isinstance(after_result, RunResult):
            raise ValueError("runner returned no result")
        if lab.read_verify_claim(identifier) != claim or lab.result(identifier) != result:
            raise ValueError("evidence changed after runs")
        status = classify_runs(
            before_result,
            after_result,
            before_snapshot.info.sha256,
            after_snapshot.info.sha256,
        )
        verification = VerificationResult(
            preview_id=identifier,
            proposal_sha256=claim.proposal_sha256,
            completed_at=utc_now(),
            status=status,
            before=before_result,
            after=after_result,
        )
        lab.verify_save(identifier, verification)
        print(json.dumps({"ok": True}))
        return 0
    except Exception:
        # An uncertain verification stays pending; never fabricate a verdict.
        if identifier is not None and owns_start:
            try:
                if lab.verification(identifier) is not None:
                    pass
            except Exception:  # noqa: S110 -- fixed output even if private storage is corrupt
                pass
        print(json.dumps({"ok": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
