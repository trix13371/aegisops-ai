"""Paid fix-proposal worker for one already claimed frozen preview.

Exactly one proposal attempt per preview. The worker never touches the live
project folder: it only reads the frozen preview/review, asks the paid model
for a single proposal, and stores the result through the lab repository.
"""

import json
import os
import sys
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from aegisops.assistant.runtime import runtime_model
from aegisops.domain.common import utc_now
from aegisops.project_code.lab import LabRepository, LabResult
from aegisops.project_code.lab_transport import ProposalError, explain_proposal
from aegisops.project_code.repository import CodeRepository
from aegisops.projects.repository import ProjectRepository

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    identifier = None
    owns_start = False
    projects = ProjectRepository(ROOT / ".local/projects")
    code = CodeRepository(ROOT / ".local/project-code-ai", projects)
    lab = LabRepository(ROOT / ".local/project-code-lab", code)
    try:
        if len(sys.argv) != 2:
            raise ValueError("invalid arguments")
        identifier = UUID(sys.argv[1])
        # Re-read the frozen preview and its saved review; never trust a cache.
        preview, code_result = lab.evidence(identifier)
        claim = lab.read_claim(identifier)
        if claim is None:
            raise ValueError("unclaimed preview")
        if lab.result(identifier) is not None:
            raise ValueError("completed request")
        # A restarted worker must not replay an uncertain paid attempt.
        lab.store.write(identifier, "started", "{}")
        owns_start = True
        before_preview, before_result = lab.evidence(identifier)
        before_claim = lab.read_claim(identifier)
        if before_preview != preview or before_result != code_result or before_claim != claim:
            raise ValueError("evidence changed before request")
        if code_result.response is None:
            raise ValueError("missing saved review")
        proposal, input_tokens, output_tokens = explain_proposal(
            preview,
            code_result.response,
            claim.finding_index,
            SecretStr(os.environ.get("AEGISOPS_LLM_API_KEY", "")),
        )
        after_preview, after_result = lab.evidence(identifier)
        after_claim = lab.read_claim(identifier)
        if after_preview != preview or after_result != code_result or after_claim != claim:
            raise ValueError("evidence changed during request")
        result = LabResult(
            preview_id=identifier,
            preview_sha256=claim.preview_sha256,
            review_sha256=claim.review_sha256,
            completed_at=utc_now(),
            status="completed",
            model=runtime_model(),
            proposal=proposal,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        lab.store.write(identifier, "result", result.model_dump_json())
        if lab.result(identifier) != result:
            raise ValueError("readback failed")
        print(json.dumps({"ok": True}))
        return 0
    except ProposalError as error:
        # Never overwrite another worker's result, nor finalize a start we do not own.
        if identifier is not None and owns_start:
            try:
                lab.fail(identifier, failure_code=error.reason, model=runtime_model())
            except Exception:  # noqa: S110 -- fixed output even if private storage is corrupt
                pass
        print(json.dumps({"ok": False}))
        return 1
    except Exception:
        # Never overwrite another worker's result, nor finalize a start we do not own.
        if identifier is not None and owns_start:
            try:
                lab.fail(identifier, model=runtime_model())
            except Exception:  # noqa: S110 -- fixed output even if private storage is corrupt
                pass
        print(json.dumps({"ok": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
