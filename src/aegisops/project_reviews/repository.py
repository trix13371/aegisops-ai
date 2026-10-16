"""Immutable selected evidence bundles; identical inputs reuse one durable identity."""

import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from aegisops.domain.common import DTO
from aegisops.io import read_text, unique_object
from aegisops.project_reviews.models import Review
from aegisops.project_reviews.projection import build_review
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.models import ProjectError
from aegisops.projects.repository import ProjectRepository, plain_path


class ReviewIndex(DTO):
    reviews: tuple[UUID, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.reviews)) != len(self.reviews):
            raise ValueError("duplicate review identity")
        return self


def evidence_identity(review: Review) -> dict[str, object]:
    return review.model_dump(mode="json", exclude={"id", "created_at"})


def review_id(review: Review) -> UUID:
    material = {"format": "aegisops-project-review-v1", **evidence_identity(review)}
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return UUID(hex=digest[:32], version=5)


class ReviewRepository:
    def __init__(self, directory: Path, projects: ProjectRepository, runs: RunRepository):
        self.directory = directory.absolute()
        self.projects, self.runs = projects, runs
        self.lock = RLock()

    def path(self, kind: str, identifier: UUID) -> Path:
        if kind not in {"reviews", "projects", "temporary"} or type(identifier) is not UUID:
            raise ProjectError("Неверный идентификатор сводного отчёта")
        return plain_path(self.directory / kind / f"{identifier}.json")

    def read_json(self, path: Path) -> object:
        return json.loads(read_text(plain_path(path), 65536), object_pairs_hook=unique_object)

    def write(self, path: Path, data: str) -> None:
        path = plain_path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data.encode())
            stream.flush()
            os.fsync(stream.fileno())

    def selected(
        self, project_id: UUID, static_report_id: UUID | None, run_id: UUID | None
    ) -> Review:
        self.projects.get(project_id)
        static = self.projects.read_report(static_report_id) if static_report_id else None
        claim = self.runs.claim(run_id) if run_id else None
        result = self.runs.result(run_id) if run_id else None
        if claim is not None and result is None:
            raise ProjectError("Дождитесь подтверждённого результата запуска или не выбирайте его")
        return build_review(project_id, static, claim, result)

    def index(self, project_id: UUID) -> ReviewIndex:
        path = self.path("projects", project_id)
        return ReviewIndex.model_validate(self.read_json(path)) if path.exists() else ReviewIndex()

    def create(
        self, project_id: UUID, static_report_id: UUID | None, run_id: UUID | None
    ) -> Review:
        with self.lock:
            draft = self.selected(project_id, static_report_id, run_id)
            identifier = review_id(draft)
            review = Review.model_validate({**draft.model_dump(), "id": identifier})
            path = self.path("reviews", identifier)
            try:
                self.write(path, review.model_dump_json())
            except FileExistsError:
                existing = self.get(identifier)
                if evidence_identity(existing) != evidence_identity(review):
                    raise ProjectError("Идентичность сводного отчёта не подтверждена") from None
                review = existing
            if self.get(identifier) != review:
                raise ProjectError("Сводный отчёт не подтверждён после сохранения")
            previous = self.index(project_id)
            if identifier in previous.reviews:
                return review
            updated = ReviewIndex(reviews=(identifier, *previous.reviews)[:20])
            temporary = self.path("temporary", uuid4())
            self.write(temporary, updated.model_dump_json())
            destination = self.path("projects", project_id)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.replace(temporary, destination)
            return review

    def get(self, identifier: UUID) -> Review:
        review = Review.model_validate(self.read_json(self.path("reviews", identifier)))
        if review.id != identifier or review_id(review) != identifier:
            raise ProjectError("Сводный отчёт не прошёл проверку идентичности")
        current = self.selected(review.project_id, review.static_report_id, review.run_id)
        if evidence_identity(current) != evidence_identity(review):
            raise ProjectError("Выбранные сохранённые проверки изменились после создания сводки")
        return review

    def history(self, project_id: UUID) -> tuple[Review, ...]:
        self.projects.get(project_id)
        reviews = tuple(self.get(identifier) for identifier in self.index(project_id).reviews)
        if any(review.project_id != project_id for review in reviews):
            raise ProjectError("История сводных отчётов повреждена")
        return reviews
