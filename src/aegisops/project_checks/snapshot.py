"""Bounded, tar-only validation of a prepared snapshot archive.

``scan_snapshot`` never extracts the archive in place and never touches the
original registered project folder. It validates the archive fully, writes
the validated bytes into a fresh private temporary directory, and then runs
the existing static scanner against that temporary root. The temporary root
is removed before returning.
"""

import hashlib
import io
import os
import tarfile
import tempfile
from pathlib import Path
from uuid import UUID

from aegisops.domain.common import utc_now
from aegisops.project_runner.snapshot import JS_SUFFIXES, Snapshot
from aegisops.projects.models import Project, ProjectError, ProjectReport
from aegisops.projects.scanner import EXCLUDED, excluded_file, scan_project

MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_ENTRIES = 1000
MAX_RELATIVE = 1024
MAX_DEPTH = 13
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024

_WINDOWS_DEVICES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "conin$",
        "conout$",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
        *(f"com{i}" for i in "\u00b9\u00b2\u00b3"),
        *(f"lpt{i}" for i in "\u00b9\u00b2\u00b3"),
    }
)


def _reject() -> ProjectError:
    return ProjectError("Снимок проекта не прошёл проверку")


def _validate_component(component: str) -> None:
    if not component or component == ".":
        raise _reject()
    if "\\" in component or "\x00" in component:
        raise _reject()
    if any(ord(char) < 32 or ord(char) == 127 for char in component):
        raise _reject()
    if component.endswith((".", " ")):
        raise _reject()
    if ":" in component:
        raise _reject()
    stem = component.split(".", 1)[0].lower()
    if stem in _WINDOWS_DEVICES:
        raise _reject()


def _validate_name(name: str) -> tuple[str, ...]:
    if not name or name.startswith("/") or name.startswith("\\"):
        raise _reject()
    if len(name) > MAX_RELATIVE:
        raise _reject()
    parts = name.split("/")
    if len(parts) > MAX_DEPTH:
        raise _reject()
    for part in parts:
        if part == "..":
            raise _reject()
        _validate_component(part)
    return tuple(parts)


def _is_excluded(parts: tuple[str, ...]) -> bool:
    for part in parts[:-1]:
        if part.lower() in EXCLUDED:
            return True
    return excluded_file(parts[-1])


def scan_snapshot(project_id: UUID, snapshot: Snapshot) -> ProjectReport:
    """Validate a snapshot archive and run the static scanner on a temp copy.

    The archive must be an uncompressed tar whose SHA-256 matches
    ``snapshot.info.sha256``. Only regular files are accepted; links,
    directories, devices and any path that could escape the temporary root
    are rejected. The original project folder is never read or modified.
    """
    if type(project_id) is not UUID:
        raise _reject()
    data = snapshot.data
    if not isinstance(data, bytes) or len(data) > MAX_ARCHIVE_BYTES:
        raise _reject()
    if hashlib.sha256(data).hexdigest() != snapshot.info.sha256:
        raise _reject()

    captured: list[tuple[str, bytes]] = []
    seen_names: set[str] = set()
    seen_folded: set[str] = set()
    total = 0
    python_files = 0
    javascript_files = 0

    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for member in archive:
                if len(captured) >= MAX_ENTRIES:
                    raise _reject()
                if member.isdir() or member.issym() or member.islnk():
                    raise _reject()
                if not member.isfile():
                    raise _reject()
                parts = _validate_name(member.name)
                if _is_excluded(parts):
                    raise _reject()
                folded = member.name.casefold()
                if member.name in seen_names or folded in seen_folded:
                    raise _reject()
                for existing in seen_folded:
                    if existing.startswith(folded + "/") or folded.startswith(existing + "/"):
                        raise _reject()
                seen_names.add(member.name)
                seen_folded.add(folded)
                if member.size < 0 or member.size > MAX_FILE_BYTES:
                    raise _reject()
                total += member.size
                if total > MAX_TOTAL_BYTES:
                    raise _reject()
                stream = archive.extractfile(member)
                if stream is None:
                    raise _reject()
                body = stream.read(MAX_FILE_BYTES + 1)
                if len(body) != member.size or len(body) > MAX_FILE_BYTES:
                    raise _reject()
                suffix = Path(member.name).suffix.lower()
                python_files += suffix == ".py"
                javascript_files += suffix in JS_SUFFIXES
                captured.append((member.name, body))
    except ProjectError:
        raise
    except Exception:
        raise _reject() from None

    if not captured:
        raise _reject()
    if len(captured) != snapshot.info.files:
        raise _reject()
    if total != snapshot.info.bytes:
        raise _reject()
    if python_files != snapshot.info.python_files:
        raise _reject()
    if javascript_files != snapshot.info.javascript_files:
        raise _reject()

    try:
        with tempfile.TemporaryDirectory(prefix="aegisops-scan-") as temporary:
            root = Path(temporary)
            for name, body in captured:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(body)
                    output.flush()
                    os.fsync(output.fileno())
            info = root.stat()
            project = Project(
                id=project_id,
                name="snapshot",
                path=str(root),
                device=info.st_dev,
                inode=info.st_ino,
                added_at=utc_now(),
            )
            return scan_project(project)
    except ProjectError:
        raise
    except Exception:
        raise _reject() from None


__all__ = ("scan_snapshot",)
