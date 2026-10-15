"""Pure DTOs for AegisOps F02 human feedback.

These models are data-only: no IO, no network, no logging. Reviewer is a local
alias only and is NOT a verified identity.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from aegisops.domain.common import DTO, Identifier, Sha256, UTCDateTime
from aegisops.domain.models import CauseCategory, Origin

__all__ = [
    "Assessment",
    "FeedbackError",
    "FeedbackRecord",
    "FeedbackSummary",
    "IncidentOutcome",
]


class FeedbackError(ValueError):
    """Raised for any feedback operation failure.

    The message is fixed so that no user-supplied text can leak into errors.
    """

    def __init__(self) -> None:
        super().__init__("feedback operation failed")


class Assessment(StrEnum):
    """Latest human assessment of a recommendation."""

    USEFUL = "useful"
    INCORRECT = "incorrect"
    UNSAFE = "unsafe"
    UNCERTAIN = "uncertain"


class IncidentOutcome(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    UNKNOWN = "unknown"


ReviewerAlias = Annotated[
    StrictStr,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$",
    ),
]

Comment = Annotated[StrictStr, Field(max_length=2000)]


class FeedbackRecord(DTO):
    """A single human feedback record.

    ``reviewer`` is a local alias only and is NOT a verified identity.
    """

    schema_version: Literal[1] = 1
    feedback_id: UUID
    run_id: UUID
    origin: Origin
    recommendation_id: Identifier
    recommendation_hash: Sha256
    reviewer: ReviewerAlias
    assessment: Assessment
    comment: Comment = ""
    supersedes_id: UUID | None = None
    created_at: UTCDateTime
    confirmed_cause: CauseCategory | None = None
    incident_outcome: IncidentOutcome | None = None
    reference: ReviewerAlias | None = None

    @field_validator("schema_version", mode="before")
    @classmethod
    def _reject_bool_schema_version(cls, value: object) -> object:
        # Pydantic's Literal accepts ``True`` for ``1``; reject non-int exactly.
        if type(value) is not int or value != 1:
            raise ValueError("invalid schema_version")
        return value

    @model_validator(mode="after")
    def _reject_self_supersede(self) -> FeedbackRecord:
        if self.supersedes_id is not None and self.supersedes_id == self.feedback_id:
            raise ValueError("supersedes_id must not equal feedback_id")
        if self.confirmed_cause is CauseCategory.UNKNOWN:
            raise ValueError("unknown is not a confirmed cause")
        if (
            self.confirmed_cause is not None or self.incident_outcome is not None
        ) and not self.reference:
            raise ValueError("labelled cause or outcome requires a reference")
        return self


class FeedbackSummary(DTO):
    """Bounded summary of latest current assessments per run+reviewer.

    Counts are the latest current assessment per run+reviewer, bounded and
    split by origin. They are NOT model accuracy and NOT gates.
    """

    origin: Origin
    limit: Annotated[StrictInt, Field(ge=1, le=100)]
    sample_size: Annotated[StrictInt, Field(ge=0, le=100)]
    has_more: StrictBool
    useful: Annotated[StrictInt, Field(ge=0, le=100)]
    incorrect: Annotated[StrictInt, Field(ge=0, le=100)]
    unsafe: Annotated[StrictInt, Field(ge=0, le=100)]
    uncertain: Annotated[StrictInt, Field(ge=0, le=100)]

    @model_validator(mode="after")
    def _check_counts(self) -> FeedbackSummary:
        total = self.useful + self.incorrect + self.unsafe + self.uncertain
        if total != self.sample_size:
            raise ValueError("counts must sum to sample_size")
        if self.sample_size > self.limit:
            raise ValueError("sample_size must not exceed limit")
        if self.has_more and self.sample_size != self.limit:
            raise ValueError("has_more requires sample_size == limit")
        return self
