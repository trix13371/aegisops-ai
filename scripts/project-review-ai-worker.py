"""Runtime-key worker for one already claimed immutable selected-evidence review."""

import json
import os
import sys
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from aegisops.domain.common import utc_now
from aegisops.project_ai.review_projection import review_summary
from aegisops.project_ai.review_repository import ReviewAnalysisRepository, ReviewResult
from aegisops.project_ai.review_transport import explain_review
from aegisops.project_reviews.projection import source_digest
from aegisops.project_reviews.repository import ReviewRepository
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.repository import ProjectRepository

ROOT = Path(__file__).resolve().parents[1]


def main():
    identifier = None
    owns_start = False
    repository = ReviewAnalysisRepository(ROOT / ".local/project-review-ai")
    try:
        if len(sys.argv) != 2:
            raise ValueError("invalid arguments")
        identifier = UUID(sys.argv[1])
        projects = ProjectRepository(ROOT / ".local/projects")
        runs = RunRepository(ROOT / ".local/project-runs")
        reviews = ReviewRepository(ROOT / ".local/project-reviews", projects, runs)
        # Re-read each saved reference and hash; never trust the frozen summary alone.
        review = reviews.get(identifier)
        claim = repository.read_claim(identifier)
        if claim is None or claim.review_sha256 != source_digest(review):
            raise ValueError("unclaimed review")
        if (
            claim.payload != review_summary(review)
            or repository.read_result(identifier) is not None
        ):
            raise ValueError("changed or completed request")
        # A restarted worker must not replay an uncertain paid attempt.
        repository.write(identifier, "started", "{}")
        owns_start = True
        if reviews.get(identifier) != review:
            raise ValueError("evidence changed before request")
        response, input_tokens, output_tokens = explain_review(
            claim.payload, SecretStr(os.environ.get("AEGISOPS_LLM_API_KEY", ""))
        )
        if reviews.get(identifier) != review:
            raise ValueError("evidence changed during request")
        result = ReviewResult(
            review_id=identifier,
            review_sha256=claim.review_sha256,
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
