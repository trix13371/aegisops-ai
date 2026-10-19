"""Single-attempt project-code review history for frozen previews.

No source, network, credentials or execution access. Repository reads only
frozen previews persisted on disk; it never reopens the live project source.
"""

import json
import os
from pathlib import Path
from threading import RLock
from typing import Self
from uuid import UUID, uuid4

from pydantic import model_validator

from aegisops.assistant.models import LEGACY_MODEL, ModelName
from aegisops.domain.common import DTO, utc_now
from aegisops.io import read_text, unique_object
from aegisops.project_code.models import (
    CodeClaim,
    CodePreview,
    CodeResult,
    preview_digest,
    validate_review,
)
from aegisops.project_code.source import capture
from aegisops.projects.models import ProjectError
from aegisops.projects.repository import ProjectRepository, plain_path


class CodeIndex(DTO):
    """Bounded per-project index of recent preview identifiers."""

    previews: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len(self.previews) > 20:
            raise ValueError("too many previews")
        if len(set(self.previews)) != len(self.previews):
            raise ValueError("duplicate previews")
        return self


class CodeRepository:
    def __init__(self, directory: Path, projects: ProjectRepository) -> None:
        self.directory = directory.absolute()
        self.projects = projects
        self.lock = RLock()

    def path(self, identifier: UUID, kind: str) -> Path:
        if type(identifier) is not UUID or kind not in {
            "preview",
            "claim",
            "started",
            "result",
            "index",
        }:
            raise ProjectError("Неверный идентификатор разбора кода")
        return plain_path(self.directory / str(identifier) / f"{kind}.json")

    def read(self, path: Path, maximum: int = 196608) -> object:
        return json.loads(read_text(plain_path(path), maximum), object_pairs_hook=unique_object)

    def write(self, identifier: UUID, kind: str, value: str) -> None:
        path = self.path(identifier, kind)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value.encode())
            stream.flush()
            os.fsync(stream.fileno())

    def index(self, project_id: UUID) -> CodeIndex:
        path = self.path(project_id, "index")
        if not path.exists():
            return CodeIndex()
        return CodeIndex.model_validate(self.read(path, 4096))

    def create(self, project_id: UUID, paths: object) -> CodePreview:
        try:
            with self.lock:
                preview = capture(self.projects.get(project_id), paths)
                self.write(preview.id, "preview", preview.model_dump_json())
                previous = self.index(project_id)
                index = CodeIndex(previews=(preview.id, *previous.previews)[:20])
                temporary_id = uuid4()
                self.write(temporary_id, "index", index.model_dump_json())
                destination = self.path(project_id, "index")
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.replace(self.path(temporary_id, "index"), destination)
                return preview
        except ProjectError:
            raise
        except Exception:
            raise ProjectError(
                "Не удалось сохранить разбор кода; автоматического повтора нет"
            ) from None

    def preview(self, identifier: UUID) -> CodePreview:
        preview = CodePreview.model_validate(self.read(self.path(identifier, "preview")))
        if preview.id != identifier:
            raise ProjectError("Разбор кода не прошёл проверку")
        self.projects.get(preview.project_id)
        return preview

    def read_claim(self, identifier: UUID) -> CodeClaim | None:
        path = self.path(identifier, "claim")
        if not path.exists():
            return None
        claim = CodeClaim.model_validate(self.read(path, 4096))
        if claim.preview_id != identifier:
            raise ProjectError("Запрос разбора кода повреждён")
        preview = self.preview(identifier)
        if claim.preview_sha256 != preview_digest(preview):
            raise ProjectError("Разбор кода изменился после запроса")
        return claim

    def read_result(self, identifier: UUID) -> CodeResult | None:
        path = self.path(identifier, "result")
        if not path.exists():
            return None
        result = CodeResult.model_validate(self.read(path))
        claim = self.read_claim(identifier)
        if (
            claim is None
            or result.preview_id != identifier
            or result.preview_sha256 != claim.preview_sha256
            or result.completed_at < claim.created_at
        ):
            raise ProjectError("Ответ разбора кода повреждён")
        if result.response is not None:
            preview = self.preview(identifier)
            validate_review(preview, result.response)
        return result

    def claim(self, identifier: UUID) -> CodeClaim:
        preview = self.preview(identifier)
        if self.read_claim(identifier) is None and (
            self.path(identifier, "result").exists() or self.path(identifier, "started").exists()
        ):
            raise ProjectError("История разбора кода повреждена")
        claim = CodeClaim(
            preview_id=identifier,
            preview_sha256=preview_digest(preview),
            created_at=utc_now(),
        )
        try:
            self.write(identifier, "claim", claim.model_dump_json())
        except FileExistsError:
            raise ProjectError("Разбор этого кода уже запрошен. Повтор не отправляется") from None
        return claim

    def state(self, identifier: UUID) -> dict[str, object]:
        preview = self.preview(identifier)
        claim = self.read_claim(identifier)
        if claim is None:
            if (
                self.path(identifier, "result").exists()
                or self.path(identifier, "started").exists()
            ):
                raise ProjectError("История разбора кода повреждена")
            return {
                "preview": preview.model_dump(mode="json"),
                "status": "not_requested",
                "result": None,
            }
        if claim.preview_sha256 != preview_digest(preview):
            raise ProjectError("Разбор кода изменился после запроса")
        result = self.read_result(identifier)
        return {
            "preview": preview.model_dump(mode="json"),
            "status": result.status if result else "pending",
            "result": result.model_dump(mode="json") if result else None,
        }

    def history(self, project_id: UUID) -> list[dict[str, object]]:
        self.projects.get(project_id)
        entries: list[dict[str, object]] = []
        for identifier in self.index(project_id).previews:
            preview = self.preview(identifier)
            if preview.project_id != project_id:
                raise ProjectError("История разбора кода повреждена")
            claim = self.read_claim(identifier)
            if claim is None:
                if (
                    self.path(identifier, "result").exists()
                    or self.path(identifier, "started").exists()
                ):
                    raise ProjectError("История разбора кода повреждена")
                status = "not_requested"
            else:
                result = self.read_result(identifier)
                status = result.status if result else "pending"
            entries.append(
                {
                    "id": str(preview.id),
                    "created_at": preview.created_at.isoformat(),
                    "status": status,
                    "file_count": len(preview.files),
                }
            )
        return entries

    def fail(self, identifier: UUID, model: ModelName = LEGACY_MODEL) -> None:
        claim = self.read_claim(identifier)
        if claim is None or self.read_result(identifier) is not None:
            return
        result = CodeResult(
            preview_id=identifier,
            preview_sha256=claim.preview_sha256,
            completed_at=utc_now(),
            status="failed",
            model=model,
        )
        try:
            self.write(identifier, "result", result.model_dump_json())
        except FileExistsError:
            return


__all__ = ("CodeIndex", "CodeRepository")
