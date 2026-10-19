from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, Field, StrictStr, model_validator

from aegisops.assistant.models import LEGACY_MODEL, ModelName
from aegisops.domain.common import DTO, Sha256, UTCDateTime, nonblank
from aegisops.projects.models import FindingCode

PROFILE = "project-summary-v1"
MODEL = "deepseek/deepseek-v4.1-flash"
Count = Annotated[int, Field(strict=True, ge=0, le=4000)]
Note = Annotated[StrictStr, Field(min_length=1, max_length=1600), AfterValidator(nonblank)]


class Category(DTO):
    code: FindingCode
    severity: Literal["error", "warning"]
    count: Count


class Summary(DTO):
    profile: Literal["project-summary-v1"] = "project-summary-v1"
    rules_version: Literal["local-static-v1", "local-static-v2"]
    status: Literal["passed", "issues", "partial"]
    files_checked: Count
    skipped: Count
    categories: tuple[Category, ...] = Field(max_length=28)


class Advice(DTO):
    code: FindingCode
    priority: Literal["high", "medium", "low"]
    explanation: Note
    next_step: Note


class Explanation(DTO):
    summary: Note
    actions: tuple[Advice, ...] = Field(max_length=5)
    limitations: Note

    @model_validator(mode="after")
    def unique_codes(self) -> Self:
        if len({item.code for item in self.actions}) != len(self.actions):
            raise ValueError("duplicate advice")
        return self


class Claim(DTO):
    report_id: UUID
    report_sha256: Sha256
    payload: Summary
    created_at: UTCDateTime


class Result(DTO):
    report_id: UUID
    report_sha256: Sha256
    completed_at: UTCDateTime
    status: Literal["completed", "failed"]
    model: ModelName = LEGACY_MODEL
    response: Explanation | None = None
    input_tokens: Annotated[int, Field(strict=True, ge=0, le=100000)] = 0
    output_tokens: Annotated[int, Field(strict=True, ge=0, le=1600)] = 0
    execution_performed: Literal[False] = False

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.status == "completed") != (self.response is not None):
            raise ValueError("inconsistent explanation")
        return self
