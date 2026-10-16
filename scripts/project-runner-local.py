"""One accepted project check: snapshot -> hardened local Docker -> bounded local report."""

import json
import re
import sys
from uuid import UUID

from project_runner_host import (
    ROOT,
    RunnerFailure,
    container_arguments,
    docker,
    inspect_owned,
    read_image,
    remove_owned,
    validate_container,
)

from aegisops.domain.common import utc_now
from aegisops.io import unique_object
from aegisops.project_runner.compatibility_gate import environment_issues
from aegisops.project_runner.models import RunResult, ToolCheck, tools_for
from aegisops.project_runner.repository import RunRepository
from aegisops.project_runner.snapshot import snapshot_project
from aegisops.projects.models import ProjectError
from aegisops.projects.repository import ProjectRepository


def main():
    repository = RunRepository(ROOT / ".local/project-runs")
    claim = container_id = snapshot = image_id = None
    started = executing = removed = False
    checks = ()
    compatibility_issues = ()
    status, reason = "error", "uncertain"
    try:
        if len(sys.argv) != 2:
            raise ValueError("unsupported arguments")
        identifier = UUID(sys.argv[1])
        claim = repository.claim(identifier)
        if repository.result(identifier) is not None:
            raise ValueError("already finished")
        repository.write(identifier, "started", "{}")
        started = True
        try:
            image_id = read_image(claim.environment)
            docker(["image", "inspect", image_id])
        except Exception:
            reason = "runner_unavailable"
            raise
        projects = ProjectRepository(ROOT / ".local/projects")
        try:
            snapshot = snapshot_project(projects.get(claim.project_id), claim.profile)
        except ProjectError:
            status, reason = "not_supported", "snapshot_rejected"
            raise
        if snapshot.compatibility is not None:
            compatibility_issues = environment_issues(snapshot.compatibility, claim.environment)
            if snapshot.environment_directive:
                compatibility_issues = (*compatibility_issues, "environment_directive")
            if compatibility_issues:
                status, reason = "not_supported", "project_incompatible"
                raise ValueError("incompatible preset")
        # Refuse to start alongside a leftover owned container from an interrupted job.
        active = docker(["ps", "--quiet", "--filter", "label=org.aegisops.project-run"]).strip()
        if active:
            raise RunnerFailure("sandbox_failed")
        created = docker(container_arguments(identifier, image_id, claim.profile)).decode().strip()
        if re.fullmatch(r"[0-9a-f]{64}", created) is None:
            raise RunnerFailure("sandbox_failed")
        container_id = created
        inspect = inspect_owned(container_id, identifier, claim.profile)
        validate_container(inspect, image_id)
        executing = True
        raw = docker(
            ["start", "--attach", "--interactive", container_id],
            data=snapshot.data,
            timeout=75,
            maximum=131072,
        )
        inspect = inspect_owned(container_id, identifier, claim.profile)
        if inspect["State"].get("OOMKilled") or inspect["State"].get("ExitCode") != 0:
            raise RunnerFailure("sandbox_failed")
        value = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(value, dict) or set(value) != {"checks"}:
            raise RunnerFailure("sandbox_failed")
        checks = tuple(ToolCheck.model_validate(item) for item in value["checks"])
        if tuple(item.tool for item in checks) != tools_for(claim.profile):
            checks = ()
            raise RunnerFailure("sandbox_failed")
        status = (
            "timeout"
            if any(item.status == "timeout" for item in checks)
            else "error"
            if any(item.status == "error" for item in checks)
            else "issues"
            if any(item.status in {"failed", "no_tests"} for item in checks)
            else "passed"
        )
        reason = "checks_completed"
    except RunnerFailure as error:
        status = "timeout" if str(error) == "deadline" else "error"
        reason = str(error) if str(error) in {"deadline", "sandbox_failed"} else "uncertain"
    except Exception:
        if reason == "uncertain" and executing:
            status, reason, checks = "error", "sandbox_failed", ()
    finally:
        if container_id is not None and claim is not None:
            try:
                removed = remove_owned(container_id, claim.id, claim.profile)
            except Exception:
                status, reason = "error", "sandbox_failed"
        if claim is not None and started:
            try:
                result = RunResult(
                    id=claim.id,
                    project_id=claim.project_id,
                    environment=claim.environment,
                    profile=claim.profile,
                    completed_at=utc_now(),
                    status=status,
                    reason=reason,
                    snapshot=snapshot.info if snapshot else None,
                    compatibility=snapshot.compatibility if snapshot else None,
                    compatibility_issues=compatibility_issues,
                    image_id=image_id,
                    checks=checks,
                    container_removed=removed,
                    execution_scope="container" if executing else "not_started",
                )
                repository.save(result)
            except Exception:  # noqa: S110 -- preserve unknown state instead of unverified success
                pass  # Unknown state stays pending, never converted to success or rerun.
    print(json.dumps({"recorded": claim is not None and repository.result(claim.id) is not None}))


def cleanup(identifier):
    repository = RunRepository(ROOT / ".local/project-runs")
    claim = repository.claim(identifier)
    found = (
        docker(
            [
                "ps",
                "--all",
                "--quiet",
                "--no-trunc",
                "--filter",
                f"label=org.aegisops.project-run={claim.id}",
            ]
        )
        .decode()
        .split()
    )
    for container in found:
        remove_owned(container, identifier, claim.profile)
    if repository.result(identifier) is None:
        repository.save(
            RunResult(
                id=claim.id,
                project_id=claim.project_id,
                environment=claim.environment,
                profile=claim.profile,
                completed_at=utc_now(),
                status="error",
                reason="uncertain",
                container_removed=True,
                execution_scope="container",
            )
        )
    print(json.dumps({"cleaned": True}))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--cleanup":
        try:
            cleanup(UUID(sys.argv[2]))
        except Exception:
            print('{"cleaned":false}')
            raise SystemExit(1) from None
    else:
        main()
