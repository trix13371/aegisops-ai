"""Runtime-key worker for one already claimed frozen selected-source preview."""

import json
import os
import sys
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from aegisops.domain.common import utc_now
from aegisops.project_code.models import CodeResult, preview_digest
from aegisops.project_code.repository import CodeRepository
from aegisops.project_code.transport import explain_code
from aegisops.projects.repository import ProjectRepository

ROOT = Path(__file__).resolve().parents[1]


def main():
    identifier = None
    owns_start = False
    projects = ProjectRepository(ROOT / ".local/projects")
    repository = CodeRepository(ROOT / ".local/project-code-ai", projects)
    try:
        if len(sys.argv) != 2:
            raise ValueError("invalid arguments")
        identifier = UUID(sys.argv[1])
        # Re-read the frozen preview and its hash; never trust a cached copy.
        preview = repository.preview(identifier)
        claim = repository.read_claim(identifier)
        if claim is None or claim.preview_sha256 != preview_digest(preview):
            raise ValueError("unclaimed preview")
        if repository.read_result(identifier) is not None:
            raise ValueError("completed request")
        # A restarted worker must not replay an uncertain paid attempt.
        repository.write(identifier, "started", "{}")
        owns_start = True
        current = repository.preview(identifier)
        if current != preview or preview_digest(current) != claim.preview_sha256:
            raise ValueError("preview changed before request")
        response, input_tokens, output_tokens = explain_code(
            preview, SecretStr(os.environ.get("AEGISOPS_LLM_API_KEY", ""))
        )
        current = repository.preview(identifier)
        if current != preview or preview_digest(current) != claim.preview_sha256:
            raise ValueError("preview changed during request")
        result = CodeResult(
            preview_id=identifier,
            preview_sha256=claim.preview_sha256,
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
