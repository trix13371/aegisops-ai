"""Called only by runtime credential launcher for an already claimed local report."""

import json
import os
import sys
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from aegisops.domain.common import utc_now
from aegisops.project_ai.models import Result
from aegisops.project_ai.projection import project_summary, report_digest
from aegisops.project_ai.repository import AnalysisRepository
from aegisops.project_ai.transport import explain
from aegisops.projects.repository import ProjectRepository

ROOT = Path(__file__).resolve().parents[1]


def main():
    identifier = None
    owns_start = False
    repository = AnalysisRepository(ROOT / ".local/project-ai")
    try:
        if len(sys.argv) != 2:
            raise ValueError("invalid arguments")
        identifier = UUID(sys.argv[1])
        projects = ProjectRepository(ROOT / ".local/projects")
        report = projects.read_report(identifier)
        projects.get(report.project_id)
        claim = repository.read_claim(identifier)
        if claim is None or claim.report_sha256 != report_digest(report):
            raise ValueError("unclaimed report")
        if (
            claim.payload != project_summary(report)
            or repository.read_result(identifier) is not None
        ):
            raise ValueError("changed or completed request")
        # O_EXCL survives host restarts: even an uncertain request cannot be resent.
        repository.write(identifier, "started", "{}")
        owns_start = True
        response, input_tokens, output_tokens = explain(
            claim.payload, SecretStr(os.environ.get("AEGISOPS_LLM_API_KEY", ""))
        )
        result = Result(
            report_id=identifier,
            report_sha256=claim.report_sha256,
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
        # Host handles a preflight failure too. Never overwrite an existing result.
        if identifier is not None and owns_start:
            try:
                repository.fail(identifier)
            except Exception:  # noqa: S110 -- output must remain fixed, including storage failures
                pass
        print(json.dumps({"ok": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
