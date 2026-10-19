"""Read-only export of a frozen project-code review and fix proposal.

Builds two byte payloads from already-persisted, already-validated history:

* ``review_markdown`` renders the saved review as Markdown.
* ``proposal_zip`` bundles a README, the review, the unified diff, a manifest
  and the reproduction test into a deterministic in-memory ZIP archive.

No filesystem writes, no network, no execution, no live project access. All
untrusted text (paths, model output, evidence, explanations) is emitted only
inside fenced code blocks so it can never escape into Markdown structure.
"""

import hashlib
import io
import json
import zipfile
from typing import Final
from uuid import UUID

from aegisops.project_code.lab import (
    LabRepository,
    Proposal,
    VerificationResult,
    VerifyClaim,
    patched_preview,
    proposal_diff,
    validate_proposal,
)
from aegisops.project_code.models import (
    CodePreview,
    CodeResult,
    CodeReview,
    preview_digest,
    validate_review,
)
from aegisops.project_code.repository import CodeRepository
from aegisops.project_code.reproduction import validate_verification
from aegisops.projects.models import ProjectError

MAX_REPORT_BYTES: Final[int] = 256 * 1024
MAX_MEMBER_BYTES: Final[int] = 256 * 1024
MAX_ARCHIVE_BYTES: Final[int] = 1024 * 1024

MANIFEST_PROFILE: Final[str] = "project-code-export-v1"

_TEST_FILENAMES: Final[dict[str, str]] = {
    "python": "test_aegis_repro.py",
    "javascript": "aegis-repro.test.mjs",
}

_FIXED_NAMES: Final[tuple[str, ...]] = (
    "README.md",
    "review.md",
    "proposal.patch",
)

_README_INTRO: Final[str] = (
    "# Экспорт разбора кода AegisOps\n"
    "\n"
    "Этот архив содержит сохранённый снимок разбора кода и предложенное\n"
    "моделью исправление. Текст модели — непроверенный совет, а не факт.\n"
    "\n"
    "Важно:\n"
    "\n"
    "- Это сохранённый снимок, а не текущее рабочее дерево проекта.\n"
    "- Включены только патч и тест; полный снимок проекта НЕ включён.\n"
    "- Автоматического применения, запуска или нового вызова модели нет.\n"
    "- Прохождение теста модели не является доказательством корректности.\n"
)


def _fence(text: str) -> str:
    """Wrap untrusted text in a fence longer than any backtick run inside it."""
    longest = 0
    current = 0
    for char in text:
        if char == "`":
            current += 1
            if current > longest:
                longest = current
        else:
            current = 0
    length = max(3, longest + 1)
    marker = "`" * length
    body = text if text.endswith("\n") else text + "\n"
    return f"{marker}text\n{body}{marker}\n"


def _safe_error() -> ProjectError:
    return ProjectError("Экспорт разбора кода недоступен")


def _completed_result(
    code: CodeRepository, identifier: UUID
) -> tuple[CodePreview, CodeResult, CodeReview]:
    try:
        preview = code.preview(identifier)
        result = code.read_result(identifier)
    except ProjectError:
        raise _safe_error() from None
    except Exception:
        raise _safe_error() from None
    if result is None or result.status != "completed" or result.response is None:
        raise _safe_error()
    if result.preview_id != identifier or preview.id != identifier:
        raise _safe_error()
    if result.preview_sha256 != preview_digest(preview):
        raise _safe_error()
    try:
        review = validate_review(preview, result.response)
    except Exception:
        raise _safe_error() from None
    return preview, result, review


def _render_review(preview: CodePreview, result: CodeResult, review: CodeReview) -> str:
    parts: list[str] = []
    parts.append("# Разбор кода AegisOps\n\n")
    parts.append("Текст модели — непроверенный совет. Пустой список замечаний не доказывает\n")
    parts.append("отсутствие проблем: проверялись только выбранные файлы.\n\n")
    parts.append(f"- Идентификатор разбора: `{preview.id}`\n")
    parts.append(f"- Создан: `{preview.created_at.isoformat()}`\n")
    parts.append(f"- Завершён: `{result.completed_at.isoformat()}`\n")
    parts.append(f"- Модель: `{result.model}`\n")
    parts.append(f"- Отпечаток снимка: `{result.preview_sha256}`\n\n")
    parts.append("## Файлы\n\n")
    for item in preview.files:
        parts.append(f"### Файл `{item.id}`\n\n")
        parts.append(f"- Язык: `{item.language}`\n")
        parts.append(f"- Отпечаток: `{item.sha256}`\n")
        parts.append("- Путь:\n\n")
        parts.append(_fence(item.path))
        parts.append("\n")
    parts.append("## Итог\n\n")
    parts.append(_fence(review.summary))
    parts.append("\n## Замечания\n\n")
    if not review.findings:
        parts.append("Замечаний нет.\n\n")
    for index, finding in enumerate(review.findings):
        parts.append(f"### Замечание {index + 1}\n\n")
        parts.append(f"- Файл: `{finding.file_id}`\n")
        parts.append(f"- Строки: `{finding.start_line}`–`{finding.end_line}`\n")
        parts.append(f"- Важность: `{finding.severity}`\n")
        parts.append(f"- Категория: `{finding.category}`\n\n")
        parts.append("Заголовок:\n\n")
        parts.append(_fence(finding.title))
        parts.append("\nДоказательство:\n\n")
        parts.append(_fence(finding.evidence))
        parts.append("\nОбъяснение:\n\n")
        parts.append(_fence(finding.explanation))
        parts.append("\nРекомендация:\n\n")
        parts.append(_fence(finding.recommendation))
        parts.append("\n")
    parts.append("## Ограничения\n\n")
    parts.append(_fence(review.limitations))
    return "".join(parts)


def review_markdown(code: CodeRepository, identifier: UUID) -> bytes:
    """Render the saved review for a completed preview as Markdown bytes."""
    preview, result, review = _completed_result(code, identifier)
    try:
        text = _render_review(preview, result, review)
        encoded = text.encode("utf-8")
    except Exception:
        raise _safe_error() from None
    if len(encoded) > MAX_REPORT_BYTES:
        raise _safe_error()
    return encoded


def _check_path(path: str) -> None:
    if not path or path.startswith("/"):
        raise _safe_error()
    if "\\" in path or ":" in path:
        raise _safe_error()
    for char in path:
        code = ord(char)
        if code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
            raise _safe_error()
    segments = path.split("/")
    for segment in segments:
        if segment in {"", ".", ".."}:
            raise _safe_error()


def _test_filename(preview: CodePreview, proposal: Proposal) -> str:
    target = None
    for item in preview.files:
        if item.id == proposal.file_id:
            target = item
            break
    if target is None:
        raise _safe_error()
    if target.language == "python":
        name = _TEST_FILENAMES["python"]
    elif target.language in {"javascript", "typescript"}:
        name = _TEST_FILENAMES["javascript"]
    else:
        raise _safe_error()
    folded = name.casefold()
    for item in preview.files:
        if item.path.casefold() == folded:
            raise _safe_error()
    return name


def _verification_block(
    lab: LabRepository,
    identifier: UUID,
    preview: CodePreview,
    proposal: Proposal,
) -> dict[str, object]:
    try:
        state = lab.verification(identifier)
    except ProjectError:
        raise _safe_error() from None
    except Exception:
        raise _safe_error() from None
    if state is None:
        return {"status": "not_requested"}
    status = state.get("status")
    if status == "pending":
        return {"status": "pending"}
    if status != "completed":
        raise _safe_error()
    try:
        claim = VerifyClaim.model_validate(state.get("claim"))
        result = VerificationResult.model_validate(state.get("result"))
        validate_verification(preview, proposal, claim, result)
    except Exception:
        raise _safe_error() from None
    return {
        "status": result.status,
        "environment": claim.environment,
        "before_run_id": str(result.before.id),
        "after_run_id": str(result.after.id),
    }


def _readme(
    preview: CodePreview,
    result: CodeResult,
    proposal: Proposal,
    fix_model: str,
    target_path: str,
    verification: dict[str, object],
) -> str:
    parts: list[str] = [_README_INTRO]
    parts.append("\n## Сведения\n\n")
    parts.append(f"- Идентификатор разбора: `{preview.id}`\n")
    parts.append(f"- Отпечаток снимка: `{result.preview_sha256}`\n")
    parts.append(f"- Модель разбора: `{result.model}`\n")
    parts.append(f"- Модель исправления: `{fix_model}`\n")
    parts.append(f"- Статус проверки: `{verification.get('status')}`\n\n")
    parts.append("## Целевой путь\n\n")
    parts.append(_fence(target_path))
    parts.append("\n## Объяснение предложения\n\n")
    parts.append(_fence(proposal.explanation))
    return "".join(parts)


def _zip_member(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    if len(payload) > MAX_MEMBER_BYTES:
        raise _safe_error()
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, payload)


def proposal_zip(lab: LabRepository, identifier: UUID) -> bytes:
    """Bundle the saved review, diff, manifest and test into a ZIP archive."""
    try:
        preview, code_result = lab.evidence(identifier)
        claim = lab.read_claim(identifier)
        lab_result = lab.result(identifier)
    except ProjectError:
        raise _safe_error() from None
    except Exception:
        raise _safe_error() from None
    if claim is None or lab_result is None:
        raise _safe_error()
    if lab_result.status != "completed" or lab_result.proposal is None:
        raise _safe_error()
    if code_result.status != "completed" or code_result.response is None:
        raise _safe_error()
    if code_result.preview_id != preview.id or preview.id != identifier:
        raise _safe_error()
    if code_result.preview_sha256 != preview_digest(preview):
        raise _safe_error()
    if claim.preview_id != preview.id or claim.preview_sha256 != preview_digest(preview):
        raise _safe_error()
    if lab_result.preview_id != preview.id or lab_result.preview_sha256 != preview_digest(preview):
        raise _safe_error()
    if lab_result.review_sha256 != claim.review_sha256:
        raise _safe_error()
    proposal = lab_result.proposal
    try:
        validate_proposal(preview, proposal, claim.finding_index, code_result.response)
    except Exception:
        raise _safe_error() from None
    for item in preview.files:
        _check_path(item.path)
    test_name = _test_filename(preview, proposal)
    try:
        diff = proposal_diff(preview, proposal)
        patched = patched_preview(preview, proposal)
    except Exception:
        raise _safe_error() from None
    target_path = None
    before_sha = None
    after_sha = None
    for item in preview.files:
        if item.id == proposal.file_id:
            target_path = item.path
            before_sha = item.sha256
    for item in patched.files:
        if item.id == proposal.file_id:
            after_sha = item.sha256
    if target_path is None or before_sha is None or after_sha is None:
        raise _safe_error()
    verification = _verification_block(lab, identifier, preview, proposal)
    try:
        review_bytes = review_markdown(lab.code, identifier)
    except ProjectError:
        raise _safe_error() from None
    readme_bytes = _readme(
        preview, code_result, proposal, lab_result.model, target_path, verification
    ).encode("utf-8")
    diff_bytes = diff.encode("utf-8")
    test_bytes = proposal.test_code.encode("utf-8")
    members: dict[str, bytes] = {
        "README.md": readme_bytes,
        "review.md": review_bytes,
        "proposal.patch": diff_bytes,
        test_name: test_bytes,
    }
    manifest = {
        "profile": MANIFEST_PROFILE,
        "preview_id": str(preview.id),
        "preview_sha256": code_result.preview_sha256,
        "review_model": str(code_result.model),
        "fix_model": str(lab_result.model),
        "target_path": target_path,
        "before_sha256": before_sha,
        "after_sha256": after_sha,
        "verification": verification,
        "files": {name: hashlib.sha256(payload).hexdigest() for name, payload in members.items()},
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(manifest_bytes) > MAX_MEMBER_BYTES:
        raise _safe_error()
    buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in _FIXED_NAMES:
                _zip_member(archive, name, members[name])
            _zip_member(archive, test_name, members[test_name])
            _zip_member(archive, "manifest.json", manifest_bytes)
    except ProjectError:
        raise
    except Exception:
        raise _safe_error() from None
    payload = buffer.getvalue()
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise _safe_error()
    return payload


__all__ = ("proposal_zip", "review_markdown")
