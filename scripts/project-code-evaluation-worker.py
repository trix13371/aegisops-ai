"""Runtime-key worker for the synthetic project-code evaluation suite."""

import json
import os
import sys
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from aegisops.domain.common import utc_now
from aegisops.project_code.evaluation import (
    CASES,
    EvalResult,
    case_preview,
    fixture_digest,
    score_answers,
)
from aegisops.project_code.evaluation_repository import EvalRepository
from aegisops.project_code.transport import explain_code
from aegisops.projects.repository import ProjectRepository

ROOT = Path(__file__).resolve().parents[1]


def main():
    identifier = None
    owns_start = False
    answers: dict[str, object] = {}
    projects = ProjectRepository(ROOT / ".local/projects")
    repository = EvalRepository(ROOT / ".local/project-code-eval", projects)
    try:
        if len(sys.argv) != 2:
            raise ValueError("invalid arguments")
        identifier = UUID(sys.argv[1])
        claim = repository.read_claim(identifier)
        if claim is None or claim.fixture_sha256 != fixture_digest():
            raise ValueError("unclaimed suite")
        if repository.result(identifier) is not None:
            raise ValueError("completed suite")
        repository.store.write(identifier, "started", "{}")
        owns_start = True
        if fixture_digest() != claim.fixture_sha256:
            raise ValueError("fixture changed before request")
        for case in CASES:
            if fixture_digest() != claim.fixture_sha256:
                raise ValueError("fixture changed before case")
            preview = case_preview(case.id, identifier)
            review, _, _ = explain_code(
                preview, SecretStr(os.environ.get("AEGISOPS_LLM_API_KEY", ""))
            )
            answers[case.id] = review
            if fixture_digest() != claim.fixture_sha256:
                raise ValueError("fixture changed during request")
        if fixture_digest() != claim.fixture_sha256:
            raise ValueError("fixture changed during request")
        result = EvalResult(
            id=identifier,
            completed_at=utc_now(),
            status="completed",
            fixture_sha256=claim.fixture_sha256,
            answers=answers,
            report=score_answers(answers),
        )
        repository.save(result)
        print(json.dumps({"ok": True}))
        return 0
    except Exception:
        if identifier is not None and owns_start:
            try:
                claim = repository.read_claim(identifier)
                if claim is not None and repository.result(identifier) is None:
                    failed = EvalResult(
                        id=identifier,
                        completed_at=utc_now(),
                        status="failed",
                        fixture_sha256=claim.fixture_sha256,
                        answers=answers,
                        report=score_answers(answers),
                    )
                    repository.save(failed)
            except Exception:  # noqa: S110 -- fixed output even if storage is corrupt
                pass
        print(json.dumps({"ok": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
