"""One shared-snapshot full check: workflow marker -> static scan -> bounded runner.

This trusted local operator script composes an existing static project scan
with an existing hardened container check over a SINGLE captured snapshot.
It never reads live source after capture, never issues a second snapshot, and
never invents a success when the container result is unknown. The workflow
owned ``started.json`` marker (distinct from the runner's own ``started``
marker) guards against duplicate execution and cannot touch a pre-existing
run result or full-check report.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from project_runner_host import ROOT

from aegisops.domain.common import utc_now
from aegisops.project_checks.models import build_result
from aegisops.project_checks.repository import FullCheckRepository
from aegisops.project_checks.snapshot import scan_snapshot
from aegisops.project_runner.models import RunResult
from aegisops.project_runner.repository import RunRepository
from aegisops.project_runner.snapshot import snapshot_project
from aegisops.projects.models import ProjectError
from aegisops.projects.repository import ProjectRepository, plain_path


def load_runner():
    """Import the hyphen-named runner script as a module (no subprocess)."""
    path = ROOT / "scripts/project-runner-local.py"
    spec = importlib.util.spec_from_file_location("aegisops_project_runner_local", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow_marker(directory: Path, identifier: UUID) -> Path:
    """Resolve the workflow-owned marker path via the projects repository.

    ``plain_path`` validates the identifier and refuses symlinked or
    junctioned components, raising ``ProjectError`` instead of returning
    ``None``. The full marker path (including the file name) is validated
    before any write.
    """
    if type(identifier) is not UUID:
        raise ProjectError("unsupported arguments")
    base = plain_path(directory)
    marker = base / str(identifier) / "workflow-started.json"
    return plain_path(marker)


def _claim_workflow(directory: Path, identifier: UUID) -> bool:
    """Atomically create the workflow-owned marker with O_EXCL, mode 0600."""
    path = _workflow_marker(directory, identifier)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump({"claimed_at": utc_now().isoformat()}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return True


def _save_run_result(runs: RunRepository, claim, snapshot, report_status, reason):
    runs.save(
        RunResult(
            id=claim.id,
            project_id=claim.project_id,
            environment=claim.environment,
            profile=claim.profile,
            completed_at=utc_now(),
            status=report_status,
            reason=reason,
            snapshot=snapshot.info if snapshot else None,
            compatibility=snapshot.compatibility if snapshot else None,
            image_id=None,
            checks=(),
            container_removed=False,
            execution_scope="not_started",
        )
    )


def main():
    recorded = False
    try:
        if len(sys.argv) != 2:
            raise ValueError("unsupported arguments")
        identifier = UUID(sys.argv[1])

        projects = ProjectRepository(ROOT / ".local/projects")
        runs = RunRepository(ROOT / ".local/project-runs")
        checks = FullCheckRepository(ROOT / ".local/project-checks", projects, runs)

        claim = checks.claim(identifier)
        # Full-check claim must correspond to a real run claim (validated by
        # the repository); also refuse if a container run already exists or
        # if a previous runner already wrote its own started marker.
        if runs.result(identifier) is not None:
            print(json.dumps({"recorded": False}))
            return
        if checks.result(identifier) is not None:
            print(json.dumps({"recorded": False}))
            return
        if runs.path(identifier, "started").exists():
            print(json.dumps({"recorded": False}))
            return

        marker_dir = ROOT / ".local/project-checks"
        if not _claim_workflow(marker_dir, identifier):
            # Duplicate invocation: never touch preexisting state.
            print(json.dumps({"recorded": False}))
            return

        # Snapshot the project exactly once using the captured profile.
        snapshot = None
        report = None
        try:
            projects_local = projects.get(claim.project_id)
            snapshot = snapshot_project(projects_local, claim.profile)
            report = scan_snapshot(claim.project_id, snapshot)
            projects.save_report(report)
        except ProjectError:
            if runs.result(identifier) is None:
                _save_run_result(runs, claim, snapshot, "not_supported", "snapshot_rejected")
            run_result = runs.result(identifier)
            if run_result is None:
                print(json.dumps({"recorded": False}))
                return
            try:
                built = build_result(claim, None, run_result)
                checks.save(built)
                recorded = True
            except ProjectError:
                recorded = False
            print(json.dumps({"recorded": recorded}))
            return
        except Exception:
            if runs.result(identifier) is None:
                _save_run_result(runs, claim, snapshot, "not_supported", "snapshot_rejected")
            run_result = runs.result(identifier)
            if run_result is None:
                print(json.dumps({"recorded": False}))
                return
            try:
                built = build_result(claim, None, run_result)
                checks.save(built)
                recorded = True
            except ProjectError:
                recorded = False
            print(json.dumps({"recorded": recorded}))
            return

        # Static report is saved BEFORE the container run completes. The
        # prepared snapshot is passed through so the runner never re-reads
        # live source and any runner_unavailable result still carries the
        # captured snapshot info.
        runner = load_runner()
        try:
            runner.run_check(claim.id, snapshot)
        except Exception:  # noqa: S110 -- unknown runner state; never invent success
            pass
        run_result = runs.result(identifier)
        if run_result is None:
            # Leave the full-check pending (UNKNOWN); static report remains saved.
            print(json.dumps({"recorded": False}))
            return
        try:
            built = build_result(claim, report, run_result)
            checks.save(built)
            recorded = True
        except ProjectError:
            recorded = False
    except Exception:
        recorded = False
    print(json.dumps({"recorded": recorded}))


if __name__ == "__main__":
    main()
