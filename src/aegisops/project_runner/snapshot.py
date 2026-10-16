"""Bounded regular-file copy; neither imports nor runs any user code."""

import hashlib
import io
import os
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

from aegisops.project_runner.models import JS_PROFILE, PROFILE, Profile, SnapshotInfo
from aegisops.projects.models import Project, ProjectError
from aegisops.projects.repository import plain_path, project_root
from aegisops.projects.scanner import EXCLUDED, excluded_file

SUFFIXES = frozenset({".py", ".json", ".yaml", ".yml", ".toml", ".ini", ".txt", ".csv"})
JS_SUFFIXES = frozenset({".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"})


@dataclass(frozen=True)
class Snapshot:
    data: bytes
    info: SnapshotInfo


def snapshot_project(project: Project, profile: Profile = PROFILE) -> Snapshot:
    if profile not in {PROFILE, JS_PROFILE}:
        raise ProjectError("Неизвестный профиль проверки")
    root = project_root(project)
    pending = [(root, 0)]
    seen = skipped = total = python_files = javascript_files = entries = 0
    suffixes = SUFFIXES if profile == PROFILE else (SUFFIXES - {".py"}) | JS_SUFFIXES
    deadline = time.monotonic() + 20
    contents: list[tuple[str, bytes]] = []
    try:
        while pending:
            directory, depth = pending.pop()
            plain_path(directory)
            with os.scandir(directory) as items:
                for item in items:
                    entries += 1
                    if entries > 4000 or time.monotonic() > deadline:
                        raise ProjectError("Превышен лимит снимка проекта")
                    path = Path(item.path)
                    relative = path.relative_to(root).as_posix()
                    if (
                        len(relative) > 1024
                        or any(ord(char) < 32 for char in relative)
                        or "\\" in relative
                    ):
                        raise ProjectError("Имя файла не поддерживается профилем")
                    if item.is_symlink() or path.is_junction():
                        skipped += 1
                        continue
                    if item.is_dir(follow_symlinks=False):
                        if item.name.lower() in EXCLUDED:
                            skipped += 1
                        elif depth >= 12:
                            raise ProjectError("Превышена глубина снимка проекта")
                        else:
                            pending.append((path, depth + 1))
                        continue
                    if not item.is_file(follow_symlinks=False):
                        skipped += 1
                        continue
                    if excluded_file(item.name) or path.suffix.lower() not in suffixes:
                        skipped += 1
                        continue
                    if (
                        profile == JS_PROFILE
                        and path.suffix.lower() in JS_SUFFIXES
                        and path.suffix not in JS_SUFFIXES
                    ):
                        raise ProjectError("JS/TS требует расширения файлов в нижнем регистре")
                    seen += 1
                    if seen > 1000:
                        raise ProjectError("В снимке больше 1000 файлов")
                    plain_path(path)
                    before = path.stat()
                    with path.open("rb") as source:
                        data = source.read(262145)
                        after = os.fstat(source.fileno())
                    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                        after.st_ino,
                        after.st_size,
                        after.st_mtime_ns,
                    ):
                        raise ProjectError("Файлы изменились во время подготовки снимка")
                    total += len(data)
                    if len(data) > 262144 or total > 8 * 1024 * 1024:
                        raise ProjectError("Превышен размер снимка проекта")
                    python_files += path.suffix.lower() == ".py"
                    javascript_files += path.suffix.lower() in JS_SUFFIXES
                    contents.append((relative, data))
        project_root(project)
        if not (javascript_files if profile == JS_PROFILE else python_files):
            raise ProjectError("В папке нет файлов выбранного языка")
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for relative, data in sorted(contents):
                member = tarfile.TarInfo(relative)
                member.size, member.mode = len(data), 0o600
                archive.addfile(member, io.BytesIO(data))
        data = output.getvalue()
        if len(data) > 10 * 1024 * 1024:
            raise ProjectError("Превышен размер архива снимка")
        return Snapshot(
            data=data,
            info=SnapshotInfo(
                sha256=hashlib.sha256(data).hexdigest(),
                files=seen,
                python_files=python_files,
                javascript_files=javascript_files,
                bytes=total,
                skipped=min(skipped, 4000),
            ),
        )
    except ProjectError:
        raise
    except Exception:
        raise ProjectError("Не удалось безопасно подготовить снимок проекта") from None
