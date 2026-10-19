"""Bounded, stable, read-only capture of explicitly selected local files.

Selected contents are untrusted data. Nothing is imported, executed or fetched.
"""

import hashlib
import os
import re
import stat
import time
from collections import deque
from pathlib import Path
from uuid import uuid4

from aegisops.domain.common import utc_now
from aegisops.project_code.models import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_LINES,
    MAX_TOTAL_BYTES,
    CodeFile,
    CodePreview,
    Language,
)
from aegisops.project_code.selection import choose_candidates
from aegisops.projects.models import Project, ProjectError
from aegisops.projects.repository import plain_path, project_root
from aegisops.projects.scanner import EXCLUDED, excluded_file

SUFFIX_LANGUAGE: dict[str, Language] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
}

SECRET_MARKERS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(
        r"(?i)[\"']?(?:password|passwd|api_key|apikey|secret|token|access_token|"
        r"openai_api_key|client_secret)[\"']?\s*[:=]\s*[\"'][A-Za-z0-9._\-\/+=]{8,}[\"']"
    ),
)

CONTROL = frozenset(chr(code) for code in range(0x20) if code not in (0x09, 0x0A, 0x0D))
CONTROL_ALL = frozenset(chr(code) for code in range(0x20)) | frozenset(
    chr(code) for code in range(0x7F, 0xA0)
)

CATALOG_DEADLINE_SECONDS = 5.0


def _safe_error(message: str = "Не удалось подготовить выбранные файлы") -> ProjectError:
    return ProjectError(message)


def _hidden(name: str) -> bool:
    return name.startswith(".")


def _directory_rank(name: str) -> int:
    lower = name.lower()
    if lower in ("src", "lib", "app", "test", "tests"):
        return 0
    if lower in ("docs", "assets"):
        return 2
    return 1


def _excluded_name(name: str) -> bool:
    if _hidden(name):
        return True
    if excluded_file(name):
        return True
    lower = name.lower()
    for word in ("secret", "token", "credential", "password"):
        if word in lower:
            return True
    return False


def _relative(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        raise _safe_error("Выбранный файл вне папки проекта") from None
    if not relative or len(relative) > 1024:
        raise _safe_error("Недопустимый путь к файлу")
    return relative


def _valid_relative(text: str) -> bool:
    if not 1 <= len(text) <= 1024:
        return False
    if any(char in CONTROL_ALL for char in text):
        return False
    if text.startswith(("/", "~")) or "\\" in text or ":" in text:
        return False
    if re.match(r"^[A-Za-z]:", text):
        return False
    if "//" in text:
        return False
    parts = text.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            return False
        if part.startswith("."):
            return False
        if part.endswith((".", " ")):
            return False
    return True


def _resolve(root: Path, raw: object) -> Path:
    if type(raw) is not str:
        raise _safe_error("Недопустимый путь к файлу")
    if not _valid_relative(raw):
        raise _safe_error("Недопустимый путь к файлу")
    candidate = root / raw
    plain_path(candidate)
    return candidate


def _check_chain(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise _safe_error("Выбранный файл вне папки проекта") from None
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise _safe_error("Ссылки и перенаправления не поддерживаются")
        if part.lower() in EXCLUDED or _hidden(part):
            raise _safe_error("Папка исключена из анализа")
    if path.is_symlink() or path.is_junction():
        raise _safe_error("Ссылки и перенаправления не поддерживаются")


def _read_stable(path: Path) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        pre = path.stat()
    except OSError:
        raise _safe_error("Файл недоступен для чтения") from None
    if not stat.S_ISREG(pre.st_mode):
        raise _safe_error("Поддерживаются только обычные файлы")
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise _safe_error("Файл недоступен для чтения") from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise _safe_error("Поддерживаются только обычные файлы")
        if (pre.st_dev, pre.st_ino, pre.st_size, pre.st_mtime_ns) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ):
            raise _safe_error("Файл изменился во время чтения")
        if before.st_size > MAX_FILE_BYTES:
            raise _safe_error("Файл превышает допустимый размер")
        chunks: list[bytes] = []
        remaining = MAX_FILE_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(data) > MAX_FILE_BYTES:
        raise _safe_error("Файл превышает допустимый размер")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise _safe_error("Файл изменился во время чтения")
    try:
        current = path.stat()
    except OSError:
        raise _safe_error("Файл недоступен для чтения") from None
    if not stat.S_ISREG(current.st_mode):
        raise _safe_error("Поддерживаются только обычные файлы")
    if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise _safe_error("Файл изменился во время чтения")
    return data


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeError:
        raise _safe_error("Файл не является корректным UTF-8") from None
    if "\x00" in text or any(char in CONTROL for char in text):
        raise _safe_error("Файл содержит недопустимые управляющие символы")
    return text


def _secret_marker(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_MARKERS)


def catalog(project: Project) -> dict[str, object]:
    """Bounded listing of candidate files; never reads file contents."""
    try:
        root = project_root(project)
    except ProjectError:
        raise
    except Exception:
        raise _safe_error() from None
    deadline = time.monotonic() + CATALOG_DEADLINE_SECONDS
    collected: list[str] = []
    partial = False
    entries = 0
    pending: deque[tuple[Path, int]] = deque([(root, 0)])
    while pending:
        if entries >= 4000 or time.monotonic() > deadline:
            partial = True
            break
        directory, depth = pending.popleft()
        try:
            plain_path(directory)
            children: list[tuple[str, Path, bool]] = []
            with os.scandir(directory) as iterator:
                for item in iterator:
                    entries += 1
                    if entries > 4000 or time.monotonic() > deadline:
                        partial = True
                        break
                    path = Path(item.path)
                    if item.is_symlink() or path.is_junction():
                        continue
                    if item.is_dir(follow_symlinks=False):
                        if item.name.lower() in EXCLUDED or _hidden(item.name):
                            continue
                        if depth >= 12:
                            partial = True
                            continue
                        children.append((item.name, path, True))
                        continue
                    if not item.is_file(follow_symlinks=False):
                        continue
                    if _excluded_name(item.name):
                        continue
                    if path.suffix.lower() not in SUFFIX_LANGUAGE:
                        continue
                    try:
                        relative = _relative(root, path)
                        if not _valid_relative(relative):
                            partial = True
                            continue
                        collected.append(relative)
                    except ProjectError:
                        partial = True
            children.sort(
                key=lambda entry: (_directory_rank(entry[0]), entry[0].casefold(), entry[0])
            )
            for _name, path, _is_dir in children:
                pending.append((path, depth + 1))
        except ProjectError:
            partial = True
        except OSError:
            partial = True
    try:
        if project_root(project) != root:
            raise _safe_error()
    except ProjectError:
        raise
    except Exception:
        raise _safe_error() from None
    chosen = choose_candidates(collected, 200)
    if len(chosen) < len(collected):
        partial = True
    files = sorted(chosen)
    return {"files": files, "partial": partial}


def capture(project: Project, paths: object) -> CodePreview:
    """Freeze exactly the selected files into an immutable preview."""
    try:
        root = project_root(project)
    except ProjectError:
        raise
    except Exception:
        raise _safe_error() from None
    if not isinstance(paths, (list, tuple)) or not 1 <= len(paths) <= MAX_FILES:
        raise _safe_error("Выберите от одного до пяти файлов")
    selected: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for raw in paths:
        path = _resolve(root, raw)
        relative = _relative(root, path)
        key = relative.casefold()
        if key in seen:
            raise _safe_error("Дублирующиеся файлы не поддерживаются")
        seen.add(key)
        selected.append((relative, path))
    files: list[CodeFile] = []
    total = 0
    for index, (relative, path) in enumerate(selected, start=1):
        try:
            _check_chain(root, path)
            if _excluded_name(path.name):
                raise _safe_error("Файл исключён из анализа")
            language = SUFFIX_LANGUAGE.get(path.suffix.lower())
            if language is None:
                raise _safe_error("Неподдерживаемый тип файла")
            try:
                info = path.stat()
            except OSError:
                raise _safe_error("Файл недоступен для чтения") from None
            if not stat.S_ISREG(info.st_mode):
                raise _safe_error("Поддерживаются только обычные файлы")
            data = _read_stable(path)
            text = _decode(data)
            if _secret_marker(text):
                raise _safe_error("В выбранных файлах обнаружены признаки секретов")
            lines = max(1, len(text.splitlines()))
            if lines > MAX_LINES:
                raise _safe_error("Файл содержит слишком много строк")
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise _safe_error("Выбранные файлы превышают общий размер")
            _check_chain(root, path)
            plain_path(path)
            if project_root(project) != root:
                raise _safe_error()
            files.append(
                CodeFile(
                    id=f"f{index}",
                    path=relative,
                    language=language,
                    content=text,
                    sha256=hashlib.sha256(data).hexdigest(),
                    lines=lines,
                )
            )
        except ProjectError:
            raise
        except Exception:
            raise _safe_error() from None
    return CodePreview(
        id=uuid4(),
        project_id=project.id,
        created_at=utc_now(),
        files=tuple(files),
    )
