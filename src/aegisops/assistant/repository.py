"""Append-only assistant bindings; never rewrites or rebinds a claimed job."""

import json
import os
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import UUID

from aegisops.assistant.models import LEGACY, Selection
from aegisops.io import read_text, unique_object
from aegisops.projects.models import ProjectError
from aegisops.projects.repository import plain_path

Mode = Literal[
    "project_ai",
    "project_review_ai",
    "project_check_ai",
    "project_code_ai",
    "project_code_fix",
    "project_code_evaluation",
]

MODES: frozenset[str] = frozenset(
    {
        "project_ai",
        "project_review_ai",
        "project_check_ai",
        "project_code_ai",
        "project_code_fix",
        "project_code_evaluation",
    }
)

MAX_CONFIG = 8192
MAX_BINDING = 8192


def _mode(value: str) -> Mode:
    if value not in MODES:
        raise ProjectError("Неизвестный режим ассистента")
    return value  # type: ignore[return-value]


def _identifier(value: UUID | str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except Exception:
        raise ProjectError("Некорректный идентификатор задания") from None


class AssistantRepository:
    """Reads active config and stores per-attempt bindings under a private directory."""

    def __init__(self, config_path: Path, attempt_directory: Path):
        self.config_path = config_path.absolute()
        self.attempt_directory = attempt_directory.absolute()
        self.lock = RLock()

    def _read_json(self, path: Path, maximum: int) -> object:
        return json.loads(read_text(plain_path(path), maximum), object_pairs_hook=unique_object)

    def active(self) -> Selection | None:
        """Return the active selection, or None when absent (legacy).

        Malformed configuration fails closed; it never falls back to legacy.
        """
        path = plain_path(self.config_path)
        if not path.exists():
            return None
        try:
            data = self._read_json(path, MAX_CONFIG)
            if not isinstance(data, dict) or set(data) != {"provider", "model"}:
                raise ProjectError()
            return Selection.model_validate(data)
        except ProjectError:
            raise
        except Exception:
            raise ProjectError("Файл настроек ассистента повреждён") from None

    def _binding_path(self, mode: Mode, identifier: UUID) -> Path:
        return plain_path(self.attempt_directory / mode / f"{identifier}.json")

    def bind(self, mode: str, identifier: UUID | str) -> Selection | None:
        """Claim a job for the active selection.

        Returns None when no active selection exists (legacy job, no binding).
        Writes exactly once per attempt; duplicates never overwrite or rebind.
        """
        checked_mode = _mode(mode)
        checked_id = _identifier(identifier)
        with self.lock:
            active = self.active()
            if active is None:
                return None
            path = self._binding_path(checked_mode, checked_id)
            payload = json.dumps(
                {"provider": active.provider, "model": active.model},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                existing = self.read_binding(checked_mode, checked_id)
                if existing is None:
                    raise ProjectError("Привязка задания повреждена") from None
                return existing
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            return active

    def read_binding(self, mode: str, identifier: UUID | str) -> Selection | None:
        """Read an existing binding; None when absent. Malformed data fails closed."""
        checked_mode = _mode(mode)
        checked_id = _identifier(identifier)
        path = self._binding_path(checked_mode, checked_id)
        if not path.exists():
            return None
        try:
            data = self._read_json(path, MAX_BINDING)
            if not isinstance(data, dict) or set(data) != {"provider", "model"}:
                raise ProjectError()
            return Selection.model_validate(data)
        except ProjectError:
            raise
        except Exception:
            raise ProjectError("Привязка задания повреждена") from None

    def resolve(self, mode: str, identifier: UUID | str) -> Selection:
        """Resolve a job's selection: its binding if present, else legacy.

        Never consults the currently active configuration, so already-claimed
        legacy jobs keep the legacy pair even after a new provider is activated.
        """
        binding = self.read_binding(mode, identifier)
        return binding if binding is not None else LEGACY
