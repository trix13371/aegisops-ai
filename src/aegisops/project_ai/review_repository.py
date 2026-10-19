"""Separate single-attempt AI history for immutable selected-evidence reviews."""

import json
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from aegisops.assistant.models import LEGACY_MODEL, ModelName
from aegisops.domain.common import DTO, Sha256, UTCDateTime, utc_now
from aegisops.io import read_text, unique_object
from aegisops.project_ai.repository import AnalysisRepository
from aegisops.project_ai.review_models import ReviewExplanation, ReviewSummary
from aegisops.project_ai.review_projection import review_summary
from aegisops.project_reviews.models import Review
from aegisops.project_reviews.projection import source_digest
from aegisops.projects.models import ProjectError


class ReviewClaim(DTO):
    review_id: UUID
    review_sha256: Sha256
    payload: ReviewSummary
    created_at: UTCDateTime


class ReviewResult(DTO):
    review_id: UUID
    review_sha256: Sha256
    completed_at: UTCDateTime
    status: Literal["completed", "failed"]
    model: ModelName = LEGACY_MODEL
    response: ReviewExplanation | None = None
    input_tokens: Annotated[int, Field(strict=True, ge=0, le=100000)] = 0
    output_tokens: Annotated[int, Field(strict=True, ge=0, le=1600)] = 0
    execution_performed: Literal[False] = False

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.status == "completed") != (self.response is not None):
            raise ValueError("inconsistent explanation")
        return self


class ReviewAnalysisRepository:
    def __init__(self, directory: Path):
        self.files = AnalysisRepository(directory)

    def write(self, identifier: UUID, name: str, data: str) -> None:
        self.files.write(identifier, name, data)

    def read_claim(self, identifier: UUID) -> ReviewClaim | None:
        path = self.files.path(identifier, "claim")
        if not path.exists():
            return None
        claim = ReviewClaim.model_validate(
            json.loads(read_text(path, 16384), object_pairs_hook=unique_object)
        )
        if claim.review_id != identifier:
            raise ProjectError("Запрос разбора сводки повреждён")
        return claim

    def read_result(self, identifier: UUID) -> ReviewResult | None:
        path = self.files.path(identifier, "result")
        if not path.exists():
            return None
        result = ReviewResult.model_validate(
            json.loads(read_text(path, 131072), object_pairs_hook=unique_object)
        )
        claim = self.read_claim(identifier)
        if (
            claim is None
            or result.review_id != identifier
            or result.review_sha256 != claim.review_sha256
            or result.completed_at < claim.created_at
        ):
            raise ProjectError("Ответ разбора сводки повреждён")
        if result.response is not None:
            allowed = {item.code for item in claim.payload.categories}
            if any(item.code not in allowed for item in result.response.actions):
                raise ProjectError("Ответ ссылается на отсутствующие проблемы")
        return result

    def claim(self, review: Review) -> ReviewClaim:
        if self.read_claim(review.id) is None and (
            self.files.path(review.id, "result").exists()
            or self.files.path(review.id, "started").exists()
        ):
            raise ProjectError("История разбора сводки повреждена")
        claim = ReviewClaim(
            review_id=review.id,
            review_sha256=source_digest(review),
            payload=review_summary(review),
            created_at=utc_now(),
        )
        try:
            self.write(review.id, "claim", claim.model_dump_json())
        except FileExistsError:
            raise ProjectError("Разбор этой сводки уже запрошен. Повтор не отправляется") from None
        return claim

    def state(self, review: Review) -> dict[str, object]:
        claim = self.read_claim(review.id)
        if claim is None:
            if (
                self.files.path(review.id, "result").exists()
                or self.files.path(review.id, "started").exists()
            ):
                raise ProjectError("История разбора сводки повреждена")
            return {"status": "not_requested"}
        if claim.review_sha256 != source_digest(review) or claim.payload != review_summary(review):
            raise ProjectError("Сводка изменилась после запроса разбора")
        result = self.read_result(review.id)
        return (
            result.model_dump(mode="json")
            if result
            else {"status": "pending", "created_at": claim.created_at.isoformat()}
        )

    def fail(self, identifier: UUID, model: ModelName = LEGACY_MODEL) -> None:
        claim = self.read_claim(identifier)
        if claim is None or self.read_result(identifier) is not None:
            return
        result = ReviewResult(
            review_id=identifier,
            review_sha256=claim.review_sha256,
            completed_at=utc_now(),
            status="failed",
            model=model,
        )
        try:
            self.write(identifier, "result", result.model_dump_json())
        except FileExistsError:
            return
