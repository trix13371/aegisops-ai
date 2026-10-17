"""Frozen DTOs for opt-in selected source code review.

Captured text is immutable data. Model output is unverified advice.
"""

import hashlib
import json
from typing import Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import Field, StrictInt, StrictStr, model_validator

from aegisops.domain.common import DTO, Sha256, UTCDateTime

CODE_PROFILE: Final[Literal["project-code-review-v1"]] = "project-code-review-v1"
CODE_MODEL: Final[Literal["deepseek/deepseek-v4.1-flash"]] = "deepseek/deepseek-v4.1-flash"

MAX_FILES = 5
MAX_FILE_BYTES = 12288
MAX_TOTAL_BYTES = 24576
MAX_LINES = 1200
MAX_FINDINGS = 5
MAX_SPAN = 20

Language = Literal[
    "python",
    "javascript",
    "typescript",
    "json",
    "yaml",
    "toml",
    "markdown",
]
Severity = Literal["error", "warning", "info"]
Category = Literal["correctness", "security", "reliability", "maintainability", "testing"]

FileId = Annotated[StrictStr, Field(pattern=r"^f[1-5]$")]
PathText = Annotated[StrictStr, Field(min_length=1, max_length=1024)]
Content = Annotated[StrictStr, Field(min_length=1, max_length=MAX_FILE_BYTES)]
Title = Annotated[StrictStr, Field(min_length=1, max_length=160)]
Evidence = Annotated[StrictStr, Field(min_length=1, max_length=500)]
Explanation = Annotated[StrictStr, Field(min_length=1, max_length=700)]
Recommendation = Annotated[StrictStr, Field(min_length=1, max_length=700)]
Summary = Annotated[StrictStr, Field(min_length=1, max_length=1200)]
Limitations = Annotated[StrictStr, Field(min_length=1, max_length=1200)]
LineNumber = Annotated[StrictInt, Field(ge=1, le=MAX_LINES)]

_FORBIDDEN = frozenset(
    chr(code) for code in range(0x20) if code not in (0x09, 0x0A, 0x0D)
) | frozenset(chr(code) for code in range(0x7F, 0xA0))


def _check_text(value: str) -> str:
    if not value.strip():
        raise ValueError("nonblank text required")
    if any(char in _FORBIDDEN for char in value):
        raise ValueError("control characters are not allowed")
    return value


def _check_content(value: str) -> str:
    if not value:
        raise ValueError("content required")
    if any(char in _FORBIDDEN for char in value):
        raise ValueError("control characters are not allowed")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError:
        raise ValueError("content must be valid UTF-8") from None
    if len(encoded) > MAX_FILE_BYTES:
        raise ValueError("file exceeds byte budget")
    return value


def _line_count(content: str) -> int:
    return max(1, len(content.splitlines()))


class CodeFile(DTO):
    id: FileId
    path: PathText
    language: Language
    content: Content
    sha256: Sha256
    lines: LineNumber

    @model_validator(mode="after")
    def consistent(self) -> Self:
        _check_content(self.content)
        _check_text(self.path)
        digest = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if digest != self.sha256:
            raise ValueError("content hash mismatch")
        if _line_count(self.content) != self.lines:
            raise ValueError("line count mismatch")
        return self


class CodePreview(DTO):
    profile: Literal["project-code-review-v1"] = CODE_PROFILE
    id: UUID
    project_id: UUID
    created_at: UTCDateTime
    files: tuple[CodeFile, ...] = Field(min_length=1, max_length=MAX_FILES)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        expected = [f"f{index}" for index in range(1, len(self.files) + 1)]
        if [item.id for item in self.files] != expected:
            raise ValueError("file ids must be contiguous")
        seen: set[str] = set()
        total = 0
        for item in self.files:
            key = item.path.casefold()
            if key in seen:
                raise ValueError("duplicate file path")
            seen.add(key)
            total += len(item.content.encode("utf-8"))
        if total > MAX_TOTAL_BYTES:
            raise ValueError("preview exceeds total byte budget")
        return self


class CodeClaim(DTO):
    preview_id: UUID
    preview_sha256: Sha256
    created_at: UTCDateTime


class CodeFinding(DTO):
    file_id: FileId
    start_line: LineNumber
    end_line: LineNumber
    severity: Severity
    category: Category
    title: Title
    evidence: Evidence
    explanation: Explanation
    recommendation: Recommendation

    @model_validator(mode="after")
    def consistent(self) -> Self:
        for value in (self.title, self.evidence, self.explanation, self.recommendation):
            _check_text(value)
        if self.end_line < self.start_line or self.end_line - self.start_line + 1 > MAX_SPAN:
            raise ValueError("invalid line span")
        return self


class CodeReview(DTO):
    summary: Summary
    findings: tuple[CodeFinding, ...] = Field(default=(), max_length=MAX_FINDINGS)
    limitations: Limitations

    @model_validator(mode="after")
    def consistent(self) -> Self:
        _check_text(self.summary)
        _check_text(self.limitations)
        seen: set[tuple[str, int, int, str]] = set()
        for item in self.findings:
            key = (item.file_id, item.start_line, item.end_line, item.title.casefold())
            if key in seen:
                raise ValueError("duplicate finding")
            seen.add(key)
        return self


class CodeResult(DTO):
    preview_id: UUID
    preview_sha256: Sha256
    completed_at: UTCDateTime
    status: Literal["completed", "failed"]
    model: Literal["deepseek/deepseek-v4.1-flash"] = CODE_MODEL
    response: CodeReview | None = None
    input_tokens: Annotated[StrictInt, Field(ge=0, le=100000)] = 0
    output_tokens: Annotated[StrictInt, Field(ge=0, le=2800)] = 0
    execution_performed: Literal[False] = False

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.status == "completed") != (self.response is not None):
            raise ValueError("inconsistent result status")
        return self


def preview_digest(preview: CodePreview) -> str:
    """Canonical digest over the entire frozen preview, including local paths."""
    checked = CodePreview.model_validate(preview)
    payload = checked.model_dump(mode="json")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def wire_payload(preview: CodePreview) -> dict[str, object]:
    """Outbound payload: synthetic ids, language and raw exact content only."""
    checked = CodePreview.model_validate(preview)
    files: list[dict[str, object]] = [
        {"id": item.id, "language": item.language, "content": item.content}
        for item in checked.files
    ]
    return {"profile": checked.profile, "files": files}


def validate_review(preview: CodePreview, review: CodeReview) -> CodeReview:
    """Revalidate a model review against the frozen preview; evidence must be exact."""
    checked = CodePreview.model_validate(preview)
    result = CodeReview.model_validate(review)
    by_id = {item.id: item for item in checked.files}
    for finding in result.findings:
        source = by_id.get(finding.file_id)
        if source is None:
            raise ValueError("unknown file id")
        if finding.end_line > source.lines:
            raise ValueError("line reference out of range")
        lines = source.content.splitlines()
        if finding.end_line > len(lines):
            raise ValueError("line reference out of range")
        joined = "\n".join(lines[finding.start_line - 1 : finding.end_line])
        if finding.evidence not in joined:
            raise ValueError("evidence quote not found in source")
    return result
