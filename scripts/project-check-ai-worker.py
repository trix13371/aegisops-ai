"""Runtime-key worker for one already claimed immutable full-check summary."""

import json
import os
import sys
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from aegisops.domain.common import utc_now
from aegisops.project_ai.full_repository import FullAnalysisRepository, FullResult
from aegisops.project_ai.full_transport import explain_full
from aegisops.project_checks.models import canonical_digest
from aegisops.project_checks.repository import FullCheckRepository
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.repository import ProjectRepository

ROOT = Path(__file__).resolve().parents[1]


def main():
    identifier = None
    owns_start = False
    projects = ProjectRepository(ROOT / ".local/projects")
    runs = RunRepository(ROOT / ".local/project-runs")
    checks = FullCheckRepository(ROOT / ".local/project-checks", projects, runs)
    repository = FullAnalysisRepository(ROOT / ".local/project-check-ai", checks)
    try:
        if len(sys.argv) != 2:
            raise ValueError("invalid arguments")
        identifier = UUID(sys.argv[1])
        # Re-read each saved reference and hash; never trust the frozen summary alone.
        check, summary = repository.evidence(identifier)
        claim = repository.read_claim(identifier)
        if claim is None or claim.check_sha256 != canonical_digest(check):
            raise ValueError("unclaimed check")
        if claim.payload != summary or repository.read_result(identifier) is not None:
            raise ValueError("changed or completed request")
        # A restarted worker must not replay an uncertain paid attempt.
        repository.write(identifier, "started", "{}")
        owns_start = True
        current, current_summary = repository.evidence(identifier)
        if current != check or current_summary != summary:
            raise ValueError("evidence changed before request")
        response, input_tokens, output_tokens = explain_full(
            claim.payload, SecretStr(os.environ.get("AEGISOPS_LLM_API_KEY", ""))
        )
        current, current_summary = repository.evidence(identifier)
        if current != check or current_summary != summary:
            raise ValueError("evidence changed during request")
        result = FullResult(
            check_id=identifier,
            check_sha256=claim.check_sha256,
            completed_at=utc_now(),
            status="completed",
            response=response,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        repository.write(identifier, "result", result.model_dump_json())
        if repository.read_result(identifier) != result:
            raise ValueError("readback failed")
        print(json.dumps({"ok": True}))
        return 0
    except Exception:
        # Never overwrite another worker's result, nor finalize a start we do not own.
        if identifier is not None and owns_start:
            try:
                repository.fail(identifier)
            except Exception:  # noqa: S110 -- fixed output even if private storage is corrupt
                pass
        print(json.dumps({"ok": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
