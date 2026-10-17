"""UI17 lab: paid fix proposal, verification claim and diff helpers.

Pure models, repository and diff helpers. No code execution, network or
credentials. Frozen previews are the only source of truth; the live project
folder is never modified.
"""

import ast
import difflib
import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import Field, StrictInt, StrictStr, model_validator

from aegisops.domain.common import DTO, Sha256, UTCDateTime, utc_now
from aegisops.io import read_text, unique_object
from aegisops.project_code.models import (
    MAX_FILE_BYTES,
    MAX_LINES,
    MAX_TOTAL_BYTES,
    CodeFile,
    CodePreview,
    CodeResult,
    CodeReview,
    FileId,
    _check_content,
    _check_text,
    _line_count,
    preview_digest,
    validate_review,
)
from aegisops.project_code.repository import CodeRepository
from aegisops.project_code.source import _secret_marker
from aegisops.project_runner.models import Environment, RunResult, tools_for
from aegisops.project_runner.snapshot import Snapshot
from aegisops.projects.models import ProjectError
from aegisops.projects.repository import plain_path

CODE_FIX_PROFILE: Final[Literal["project-code-fix-v1"]] = "project-code-fix-v1"

MAX_REPLACEMENT_BYTES = MAX_FILE_BYTES
MAX_TEST_BYTES = 8192
MAX_EXPLANATION = 1200
MAX_FINDINGS = 5

FailureCode = Literal["transport", "invalid_response", "invalid_proposal", "unknown"]

_TEST_FILENAMES: Final[dict[str, str]] = {
    "python": "test_aegis_repro.py",
    "javascript": "aegis-repro.test.mjs",
}

TargetLanguage = Literal["python", "javascript", "typescript"]
TestLanguage = Literal["python", "javascript"]

Replacement = Annotated[StrictStr, Field(min_length=1, max_length=MAX_REPLACEMENT_BYTES)]
TestCode = Annotated[StrictStr, Field(min_length=1, max_length=MAX_TEST_BYTES)]
Explanation = Annotated[StrictStr, Field(min_length=1, max_length=MAX_EXPLANATION)]
FindingIndex = Annotated[StrictInt, Field(ge=0, le=MAX_FINDINGS - 1)]


def _canonical(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: object) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _check_replacement(value: str) -> str:
    _check_content(value)
    if _line_count(value) > MAX_LINES:
        raise ValueError("replacement exceeds line budget")
    return value


def _check_test(value: str) -> str:
    _check_content(value)
    if _line_count(value) > MAX_LINES:
        raise ValueError("test exceeds line budget")
    return value


class Proposal(DTO):
    file_id: FileId
    replacement: Replacement
    test_language: TestLanguage
    test_code: TestCode
    explanation: Explanation

    @model_validator(mode="after")
    def consistent(self) -> Self:
        _check_replacement(self.replacement)
        _check_test(self.test_code)
        _check_text(self.explanation)
        if _secret_marker(self.replacement) or _secret_marker(self.test_code):
            raise ValueError("generated text must not contain secret markers")
        return self


def _target_language(preview: CodePreview, file_id: str) -> TargetLanguage:
    for item in preview.files:
        if item.id == file_id:
            if item.language not in {"python", "javascript", "typescript"}:
                raise ValueError("target language is not supported")
            return item.language  # type: ignore[return-value]
    raise ValueError("unknown file id")


def _expected_test_language(target: TargetLanguage) -> TestLanguage:
    return "python" if target == "python" else "javascript"


def _check_python_syntax(source: str) -> None:
    try:
        ast.parse(source)
    except SyntaxError:
        raise ValueError("python syntax error") from None


def proposal_payload(
    preview: CodePreview,
    review: CodeReview,
    finding_index: int,
) -> dict[str, object] | None:
    """Shared pure builder for the exact fix-proposal payload shown by GET.

    Revalidates the frozen preview and saved review, checks the finding index,
    target language support and test-filename collision. Returns the fixed
    profile/files/finding payload, or None when the source target is unsupported.
    """
    try:
        checked = CodePreview.model_validate(preview)
        saved = validate_review(checked, review)
        if type(finding_index) is not int or not 0 <= finding_index < len(saved.findings):
            return None
        finding = saved.findings[finding_index]
        target = _target_language(checked, finding.file_id)
        test_language = "python" if target == "python" else "javascript"
        test_filename = _TEST_FILENAMES[test_language]
        for item in checked.files:
            if item.path.casefold() == test_filename.casefold():
                return None
        files = [
            {
                "id": item.id,
                "path": item.path,
                "language": item.language,
                "content": item.content,
            }
            for item in checked.files
        ]
        return {
            "profile": "project-code-fix-v1",
            "files": files,
            "finding": finding.model_dump(mode="json"),
        }
    except Exception:
        return None


def validate_proposal(
    preview: CodePreview,
    proposal: Proposal,
    finding_index: int,
    review: CodeReview,
) -> Proposal:
    """Revalidate a proposal against the frozen preview and saved review."""
    checked = CodePreview.model_validate(preview)
    result = Proposal.model_validate(proposal)
    saved = validate_review(checked, review)
    if type(finding_index) is not int or not 0 <= finding_index < len(saved.findings):
        raise ValueError("invalid finding index")
    finding = saved.findings[finding_index]
    if result.file_id != finding.file_id:
        raise ValueError("proposal file does not match finding")
    target = _target_language(checked, result.file_id)
    if result.test_language != _expected_test_language(target):
        raise ValueError("test language does not match target")
    source = next(item for item in checked.files if item.id == result.file_id)
    if result.replacement == source.content:
        raise ValueError("replacement must change the target")
    if target == "python":
        _check_python_syntax(result.replacement)
        _check_python_syntax(result.test_code)
    total = sum(
        len(item.content.encode("utf-8")) for item in checked.files if item.id != result.file_id
    ) + len(result.replacement.encode("utf-8"))
    if total > MAX_TOTAL_BYTES:
        raise ValueError("preview exceeds total byte budget")
    return result


def patched_preview(preview: CodePreview, proposal: Proposal) -> CodePreview:
    """Return a preview with only the chosen file content/hash/lines changed."""
    checked = CodePreview.model_validate(preview)
    result = Proposal.model_validate(proposal)
    files: list[CodeFile] = []
    found = False
    for item in checked.files:
        if item.id != result.file_id:
            files.append(item)
            continue
        found = True
        content = result.replacement
        files.append(
            CodeFile(
                id=item.id,
                path=item.path,
                language=item.language,
                content=content,
                sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                lines=_line_count(content),
            )
        )
    if not found:
        raise ValueError("unknown file id")
    return CodePreview(
        profile=checked.profile,
        id=checked.id,
        project_id=checked.project_id,
        created_at=checked.created_at,
        files=tuple(files),
    )


def proposal_diff(preview: CodePreview, proposal: Proposal) -> str:
    """Unified diff with local relative paths for manual application."""
    checked = CodePreview.model_validate(preview)
    result = Proposal.model_validate(proposal)
    source = next((item for item in checked.files if item.id == result.file_id), None)
    if source is None:
        raise ValueError("unknown file id")
    before = source.content.splitlines(keepends=True)
    after = result.replacement.splitlines(keepends=True)
    diff = difflib.unified_diff(
        before,
        after,
        fromfile=f"a/{source.path}",
        tofile=f"b/{source.path}",
        lineterm="\n",
    )
    out = []
    for line in diff:
        if line.endswith("\n"):
            out.append(line)
        else:
            out.append(line + "\n")
            out.append("\\ No newline at end of file\n")
    return "".join(out)


class LabClaim(DTO):
    preview_id: UUID
    preview_sha256: Sha256
    review_sha256: Sha256
    finding_index: FindingIndex
    created_at: UTCDateTime


class LabResult(DTO):
    preview_id: UUID
    preview_sha256: Sha256
    review_sha256: Sha256
    completed_at: UTCDateTime
    status: Literal["completed", "failed"]
    proposal: Proposal | None = None
    input_tokens: Annotated[StrictInt, Field(ge=0, le=100000)] = 0
    output_tokens: Annotated[StrictInt, Field(ge=0, le=2800)] = 0
    failure_code: FailureCode | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.status == "completed") != (self.proposal is not None):
            raise ValueError("inconsistent result status")
        if self.status == "completed" and self.failure_code is not None:
            raise ValueError("completed result must not carry a failure code")
        return self


class VerifyClaim(DTO):
    preview_id: UUID
    proposal_sha256: Sha256
    environment: Environment
    created_at: UTCDateTime


class VerificationResult(DTO):
    preview_id: UUID
    proposal_sha256: Sha256
    completed_at: UTCDateTime
    status: Literal["reproduced", "not_reproduced", "inconclusive"]
    before: RunResult
    after: RunResult


def _review_digest(review: CodeReview) -> str:
    return _digest(CodeReview.model_validate(review).model_dump(mode="json"))


def _result_digest(result: LabResult) -> str:
    return _digest(LabResult.model_validate(result).model_dump(mode="json"))


def _environment_allowed(target: TargetLanguage, environment: Environment) -> bool:
    if target == "python":
        return environment in {"minimal", "python-data-v1"}
    if target == "javascript":
        return environment == "node-js-v1"
    return environment == "node-vitest-v1"


def _snapshot_ok(run: RunResult, expected_sha: str) -> bool:
    return (
        run.reason == "checks_completed"
        and run.execution_scope == "container"
        and run.container_removed is True
        and run.snapshot is not None
        and run.snapshot.sha256 == expected_sha
        and run.image_id is not None
        and len(run.checks) == 2
        and all(not item.output_truncated for item in run.checks)
    )


def classify_runs(
    before: RunResult,
    after: RunResult,
    expected_before_sha: str,
    expected_after_sha: str,
) -> Literal["reproduced", "not_reproduced", "inconclusive"]:
    """Classify a before/after pair; never claims semantic proof."""
    first, second = RunResult.model_validate(before), RunResult.model_validate(after)
    if not _snapshot_ok(first, expected_before_sha) or not _snapshot_ok(second, expected_after_sha):
        return "inconclusive"
    if first.image_id != second.image_id or first.image_id is None:
        return "inconclusive"
    if first.environment != second.environment or first.profile != second.profile:
        return "inconclusive"
    if tuple(item.tool for item in first.checks) != tools_for(first.profile):
        return "inconclusive"
    if tuple(item.tool for item in second.checks) != tools_for(second.profile):
        return "inconclusive"
    syntax_before, test_before = first.checks
    syntax_after, test_after = second.checks
    if syntax_before.status != "passed" or syntax_after.status != "passed":
        return "not_reproduced"
    if (
        test_before.status == "failed"
        and test_before.exit_code == 1
        and test_after.status == "passed"
        and test_after.exit_code == 0
    ):
        return "reproduced"
    return "not_reproduced"


class LabRepository:
    """Single-attempt proposal and verification history for frozen previews."""

    def __init__(self, directory: Path, code: CodeRepository) -> None:
        self.directory = directory.absolute()
        self.code = code
        self.store = CodeRepository(self.directory, code.projects)
        self.verification_store = CodeRepository(self.directory / "verification", code.projects)
        self.lock = RLock()

    def _path(self, identifier: UUID, kind: str) -> Path:
        return plain_path(self.directory / str(identifier) / f"{kind}.json")

    def _read(self, path: Path, maximum: int = 196608) -> object:
        return json.loads(read_text(plain_path(path), maximum), object_pairs_hook=unique_object)

    def _write(self, identifier: UUID, kind: str, value: str) -> None:
        path = self._path(identifier, kind)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value.encode())
            stream.flush()
            os.fsync(stream.fileno())

    def _exists(self, identifier: UUID, kind: str) -> bool:
        return self._path(identifier, kind).exists()

    def evidence(self, identifier: UUID) -> tuple[CodePreview, CodeResult]:
        preview = self.code.preview(identifier)
        result = self.code.read_result(identifier)
        if result is None or result.status != "completed" or result.response is None:
            raise ProjectError("Разбор кода ещё не завершён")
        return preview, result

    def _review(self, identifier: UUID) -> tuple[CodePreview, CodeResult, CodeReview]:
        preview, result = self.evidence(identifier)
        if result.response is None:
            raise ProjectError("Разбор кода ещё не завершён")
        return preview, result, result.response

    def claim(self, identifier: UUID, finding_index: int) -> LabClaim:
        preview, result, review = self._review(identifier)
        if type(finding_index) is not int or not 0 <= finding_index < len(review.findings):
            raise ProjectError("Неверный номер замечания")
        finding = review.findings[finding_index]
        _target_language(preview, finding.file_id)
        if self.read_claim(identifier) is None and (
            self._exists(identifier, "result") or self._exists(identifier, "started")
        ):
            raise ProjectError("История исправления повреждена")
        claim = LabClaim(
            preview_id=identifier,
            preview_sha256=preview_digest(preview),
            review_sha256=_review_digest(review),
            finding_index=finding_index,
            created_at=utc_now(),
        )
        try:
            self._write(identifier, "claim", claim.model_dump_json())
        except FileExistsError:
            raise ProjectError("Исправление уже запрошено. Повтор не отправляется") from None
        return claim

    def read_claim(self, identifier: UUID) -> LabClaim | None:
        path = self._path(identifier, "claim")
        if not path.exists():
            return None
        claim = LabClaim.model_validate(self._read(path, 4096))
        if claim.preview_id != identifier:
            raise ProjectError("Запрос исправления повреждён")
        preview, result, review = self._review(identifier)
        if claim.preview_sha256 != preview_digest(preview):
            raise ProjectError("Разбор кода изменился после запроса")
        if claim.review_sha256 != _review_digest(review):
            raise ProjectError("Ответ разбора кода изменился после запроса")
        if claim.created_at < result.completed_at:
            raise ProjectError("Запрос исправления повреждён")
        return claim

    def result(self, identifier: UUID) -> LabResult | None:
        path = self._path(identifier, "result")
        if not path.exists():
            return None
        result = LabResult.model_validate(self._read(path))
        claim = self.read_claim(identifier)
        if (
            claim is None
            or result.preview_id != identifier
            or result.preview_sha256 != claim.preview_sha256
            or result.review_sha256 != claim.review_sha256
            or result.completed_at < claim.created_at
        ):
            raise ProjectError("Ответ исправления повреждён")
        if result.proposal is not None:
            preview, _, review = self._review(identifier)
            validate_proposal(preview, result.proposal, claim.finding_index, review)
        return result

    def fail(self, identifier: UUID, failure_code: FailureCode = "unknown") -> None:
        claim = self.read_claim(identifier)
        if claim is None or self.result(identifier) is not None:
            return
        result = LabResult(
            preview_id=identifier,
            preview_sha256=claim.preview_sha256,
            review_sha256=claim.review_sha256,
            completed_at=utc_now(),
            status="failed",
            failure_code=failure_code,
        )
        try:
            self._write(identifier, "result", result.model_dump_json())
        except FileExistsError:
            return

    def state(self, identifier: UUID) -> dict[str, object]:
        preview, _, review = self._review(identifier)
        claim = self.read_claim(identifier)
        if claim is None:
            if self._exists(identifier, "result") or self._exists(identifier, "started"):
                raise ProjectError("История исправления повреждена")
            return {
                "status": "not_requested",
                "claim": None,
                "result": None,
                "diff": None,
                "verification": self.verification(identifier),
            }
        result = self.result(identifier)
        diff: str | None = None
        if result is not None and result.proposal is not None:
            diff = proposal_diff(preview, result.proposal)
        return {
            "status": result.status if result else "pending",
            "claim": claim.model_dump(mode="json"),
            "result": result.model_dump(mode="json") if result else None,
            "diff": diff,
            "verification": self.verification(identifier),
        }

    def _verification_path(self, identifier: UUID, kind: str) -> Path:
        return plain_path(self.directory / "verification" / str(identifier) / f"{kind}.json")

    def _verification_read(self, path: Path, maximum: int = 196608) -> object:
        return json.loads(read_text(plain_path(path), maximum), object_pairs_hook=unique_object)

    def _verification_write(self, identifier: UUID, kind: str, value: str) -> None:
        path = self._verification_path(identifier, kind)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value.encode())
            stream.flush()
            os.fsync(stream.fileno())

    def verify_claim(self, identifier: UUID, environment: Environment) -> VerifyClaim:
        preview, _, review = self._review(identifier)
        result = self.result(identifier)
        if result is None or result.status != "completed" or result.proposal is None:
            raise ProjectError("Исправление ещё не предложено")
        claim = self.read_claim(identifier)
        if claim is None:
            raise ProjectError("Запрос исправления не найден")
        target = _target_language(preview, result.proposal.file_id)
        if not _environment_allowed(target, environment):
            raise ProjectError("Среда не подходит для этого языка")
        if self.verification(identifier) is not None:
            raise ProjectError("Проверка уже запрошена. Повтор не отправляется")
        verify = VerifyClaim(
            preview_id=identifier,
            proposal_sha256=_result_digest(result),
            environment=environment,
            created_at=utc_now(),
        )
        try:
            self._verification_write(identifier, "claim", verify.model_dump_json())
        except FileExistsError:
            raise ProjectError("Проверка уже запрошена. Повтор не отправляется") from None
        return verify

    def read_verify_claim(self, identifier: UUID) -> VerifyClaim | None:
        path = self._verification_path(identifier, "claim")
        if not path.exists():
            return None
        claim = VerifyClaim.model_validate(self._verification_read(path, 4096))
        if claim.preview_id != identifier:
            raise ProjectError("Запрос проверки повреждён")
        result = self.result(identifier)
        if result is None or result.status != "completed" or result.proposal is None:
            raise ProjectError("Запрос проверки повреждён")
        if claim.proposal_sha256 != _result_digest(result):
            raise ProjectError("Предложение изменилось после запроса проверки")
        if claim.created_at < result.completed_at:
            raise ProjectError("Запрос проверки повреждён")
        return claim

    def verification(self, identifier: UUID) -> dict[str, object] | None:
        claim = self.read_verify_claim(identifier)
        if claim is None:
            if (
                self._verification_path(identifier, "result").exists()
                or self._verification_path(identifier, "started").exists()
            ):
                raise ProjectError("История проверки повреждена")
            return None
        path = self._verification_path(identifier, "result")
        if not path.exists():
            return {
                "status": "pending",
                "claim": claim.model_dump(mode="json"),
                "result": None,
            }
        result = VerificationResult.model_validate(self._verification_read(path))
        if (
            result.preview_id != identifier
            or result.proposal_sha256 != claim.proposal_sha256
            or result.completed_at < claim.created_at
        ):
            raise ProjectError("Ответ проверки повреждён")
        if (
            result.before.environment != claim.environment
            or result.after.environment != claim.environment
        ):
            raise ProjectError("Ответ проверки повреждён")
        return {
            "status": "completed",
            "claim": claim.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        }

    def verify_save(self, identifier: UUID, result: VerificationResult) -> None:
        claim = self.read_verify_claim(identifier)
        if claim is None:
            raise ProjectError("Запрос проверки не найден")
        checked = VerificationResult.model_validate(result)
        if (
            checked.preview_id != identifier
            or checked.proposal_sha256 != claim.proposal_sha256
            or checked.completed_at < claim.created_at
            or checked.before.environment != claim.environment
            or checked.after.environment != claim.environment
        ):
            raise ProjectError("Ответ проверки повреждён")
        from aegisops.project_code.reproduction import validate_verification

        preview, _, _ = self._review(identifier)
        lab_result = self.result(identifier)
        if lab_result is None or lab_result.proposal is None:
            raise ProjectError("Ответ проверки повреждён")
        try:
            validate_verification(preview, lab_result.proposal, claim, checked)
        except ValueError:
            raise ProjectError("Ответ проверки повреждён") from None
        try:
            self._verification_write(identifier, "result", checked.model_dump_json())
        except FileExistsError:
            raise ProjectError("Ответ проверки уже сохранён") from None


def verification_snapshots(
    preview: CodePreview,
    proposal: Proposal,
    environment: Environment,
) -> tuple[Snapshot, Snapshot]:
    """Local wrapper for worker compatibility; imports helper lazily."""
    from aegisops.project_code.reproduction import verification_snapshots as _impl

    return _impl(preview, proposal, environment)


__all__ = (
    "CODE_FIX_PROFILE",
    "LabClaim",
    "LabRepository",
    "LabResult",
    "Proposal",
    "VerificationResult",
    "VerifyClaim",
    "classify_runs",
    "patched_preview",
    "proposal_diff",
    "validate_proposal",
    "verification_snapshots",
)
