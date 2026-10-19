"""Runtime-key worker for the synthetic project-code evaluation suites.

Dispatches on the stored claim profile: the legacy three-case v1 suite or
``synthetic-file-overlap-v2`` (nine cases). The existing argv contract
(UUID, runtime key, frozen binding) is unchanged. No new commands, tools,
credential paths or settings are introduced; models never receive expected
labels. On the first error the worker fails closed without retry and saves
a partial result with the answers and usage accumulated so far.
"""

import json
import os
import sys
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr

from aegisops.assistant.runtime import runtime_model
from aegisops.domain.common import utc_now
from aegisops.project_ai.transport import AnalysisError
from aegisops.project_code import evaluation as _v1
from aegisops.project_code import evaluation_v2 as _v2
from aegisops.project_code.evaluation_repository import EvalRepository
from aegisops.project_code.transport import explain_code
from aegisops.projects.repository import ProjectRepository

ROOT = Path(__file__).resolve().parents[1]

_V1_PROFILE = "synthetic-line-overlap-v1"
_V2_PROFILE = "synthetic-file-overlap-v2"


def _profile_of(claim) -> str:
    profile = getattr(claim, "suite_profile", None)
    if profile is None:
        return _V1_PROFILE
    if profile not in (_V1_PROFILE, _V2_PROFILE):
        raise ValueError("unknown profile")
    return profile


def _fixture_digest(profile: str) -> str:
    if profile == _V1_PROFILE:
        return _v1.fixture_digest()
    if profile == _V2_PROFILE:
        return _v2.fixture_digest()
    raise ValueError("unknown profile")


def _cases(profile: str):
    if profile == _V1_PROFILE:
        return _v1.CASES
    if profile == _V2_PROFILE:
        return _v2.CASES
    raise ValueError("unknown profile")


def _case_preview(profile: str, case_id: str, suite_id: UUID):
    if profile == _V1_PROFILE:
        return _v1.case_preview(case_id, suite_id)
    if profile == _V2_PROFILE:
        return _v2.case_preview(case_id, suite_id)
    raise ValueError("unknown profile")


def _score(profile: str, answers):
    if profile == _V1_PROFILE:
        return _v1.score_answers(answers)
    if profile == _V2_PROFILE:
        return _v2.score_answers(answers)
    raise ValueError("unknown profile")


def _result_class(profile: str):
    if profile == _V1_PROFILE:
        return _v1.EvalResult
    if profile == _V2_PROFILE:
        return _v2.EvalResultV2
    raise ValueError("unknown profile")


def _max_requests(profile: str) -> int:
    if profile == _V1_PROFILE:
        return 3
    if profile == _V2_PROFILE:
        return _v2.MAX_REQUESTS
    raise ValueError("unknown profile")


def _build_result(profile: str, identifier, claim, answers, usage, status, failure=None):
    result_class = _result_class(profile)
    if profile == _V1_PROFILE:
        return result_class(
            id=identifier,
            completed_at=utc_now(),
            status=status,
            model=runtime_model(),
            fixture_sha256=claim.fixture_sha256,
            answers=answers,
            report=_score(profile, answers),
        )
    return result_class(
        suite_profile=_V2_PROFILE,
        id=identifier,
        completed_at=utc_now(),
        status=status,
        model=runtime_model(),
        fixture_sha256=claim.fixture_sha256,
        answers=answers,
        usage=usage,
        report=_score(profile, answers),
        failure=failure,
    )


def main():
    identifier = None
    owns_start = False
    answers: dict[str, object] = {}
    usage: dict[str, object] = {}
    profile = _V1_PROFILE
    current_case_id: str | None = None
    failure_code = "unavailable"
    projects = ProjectRepository(ROOT / ".local/projects")
    repository = EvalRepository(ROOT / ".local/project-code-eval", projects)
    try:
        if len(sys.argv) != 2:
            raise ValueError("invalid arguments")
        identifier = UUID(sys.argv[1])
        claim = repository.read_claim(identifier)
        if claim is None:
            raise ValueError("unclaimed suite")
        profile = _profile_of(claim)
        if claim.fixture_sha256 != _fixture_digest(profile):
            raise ValueError("unclaimed suite")
        if repository.result(identifier) is not None:
            raise ValueError("completed suite")
        repository.store.write(identifier, "started", "{}")
        owns_start = True
        if _fixture_digest(profile) != claim.fixture_sha256:
            raise ValueError("fixture changed before request")
        cases = _cases(profile)
        if len(cases) > _max_requests(profile):
            raise ValueError("too many cases")
        for case in cases:
            if _fixture_digest(profile) != claim.fixture_sha256:
                raise ValueError("fixture changed before case")
            preview = _case_preview(profile, case.id, identifier)
            current_case_id = case.id
            try:
                review, input_tokens, output_tokens = explain_code(
                    preview, SecretStr(os.environ.get("AEGISOPS_LLM_API_KEY", ""))
                )
            except AnalysisError as exc:
                failure_code = (
                    exc.code
                    if type(exc.code) is str and exc.code in _v2.FAILURE_CODES
                    else "unavailable"
                )
                raise
            except Exception:
                failure_code = "unavailable"
                raise
            if profile == _V2_PROFILE:
                usage[case.id] = _v2.CaseUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            answers[case.id] = review
            if _fixture_digest(profile) != claim.fixture_sha256:
                raise ValueError("fixture changed during request")
        if _fixture_digest(profile) != claim.fixture_sha256:
            raise ValueError("fixture changed during request")
        result = _build_result(profile, identifier, claim, answers, usage, "completed")
        repository.save(result)
        print(json.dumps({"ok": True}))
        return 0
    except Exception:
        if identifier is not None and owns_start:
            try:
                claim = repository.read_claim(identifier)
                if claim is not None and repository.result(identifier) is None:
                    failure = None
                    if profile == _V2_PROFILE:
                        failure = _v2.EvalFailure(
                            code=failure_code,
                            case_id=current_case_id,
                        )
                    failed = _build_result(
                        profile, identifier, claim, answers, usage, "failed", failure
                    )
                    repository.save(failed)
            except Exception:  # noqa: S110 -- fixed output even if storage is corrupt
                pass
        print(json.dumps({"ok": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
