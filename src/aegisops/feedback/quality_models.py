"""Bounded comparison contracts; labels are local claims, not verified ground truth."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, model_validator

from aegisops.domain.common import DTO, Identifier
from aegisops.domain.models import CauseCategory, Origin
from aegisops.feedback.models import IncidentOutcome

Count = Annotated[StrictInt, Field(ge=0, le=100)]


class ComparisonStatus(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    ABSTAINED = "abstained"
    CONFLICT = "conflict"
    UNLABELLED = "unlabelled"
    SAMPLE_INCOMPLETE = "sample_incomplete"


class PredictionKind(StrEnum):
    FIXTURE = "fixture"
    FALLBACK = "fallback"
    MODEL = "model"
    UNCLASSIFIED = "unclassified"


class QualityCase(DTO):
    incident_id: Identifier
    run_id: UUID
    feedback_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    confirmed_cause: CauseCategory | None
    incident_outcome: IncidentOutcome | None
    predicted_categories: tuple[CauseCategory, ...] = Field(max_length=5)
    status: ComparisonStatus
    prediction_kind: PredictionKind
    provider: Identifier | None
    model_name: Identifier | None

    @model_validator(mode="after")
    def consistent(self) -> "QualityCase":
        if len(set(self.feedback_ids)) != len(self.feedback_ids):
            raise ValueError("duplicate feedback IDs")
        if len(set(self.predicted_categories)) != len(self.predicted_categories):
            raise ValueError("duplicate predictions")
        if self.confirmed_cause is CauseCategory.UNKNOWN:
            raise ValueError("unknown cannot be confirmed")
        comparable = self.status in {
            ComparisonStatus.MATCH,
            ComparisonStatus.MISMATCH,
            ComparisonStatus.ABSTAINED,
        }
        if comparable != (self.confirmed_cause is not None):
            raise ValueError("comparison needs a confirmed cause")
        concrete = (
            len(self.predicted_categories) == 1
            and self.predicted_categories[0] is not CauseCategory.UNKNOWN
        )
        if self.status in {ComparisonStatus.MATCH, ComparisonStatus.MISMATCH}:
            if not concrete or (
                (self.predicted_categories[0] == self.confirmed_cause)
                != (self.status is ComparisonStatus.MATCH)
            ):
                raise ValueError("invalid comparison verdict")
        if self.status is ComparisonStatus.ABSTAINED and concrete:
            raise ValueError("concrete prediction cannot abstain")
        if self.status in {ComparisonStatus.CONFLICT, ComparisonStatus.SAMPLE_INCOMPLETE}:
            if self.incident_outcome is not None:
                raise ValueError("excluded case cannot assert an outcome")
        return self


class QualityReport(DTO):
    mode: Literal["local_label_comparison_not_model_quality"] = (
        "local_label_comparison_not_model_quality"
    )
    origin: Origin
    limit: Annotated[StrictInt, Field(ge=1, le=100)]
    sample_size: Count
    has_more: StrictBool
    cases: tuple[QualityCase, ...] = Field(max_length=100)
    matches: Count
    mismatches: Count
    abstentions: Count
    conflicts: Count
    unlabelled: Count
    incomplete: Count
    resolved_incidents: Count
    unresolved_incidents: Count

    @model_validator(mode="after")
    def consistent(self) -> "QualityReport":
        if self.sample_size > self.limit or (self.has_more and self.sample_size != self.limit):
            raise ValueError("invalid sample size")
        ids = [identifier for case in self.cases for identifier in case.feedback_ids]
        if len(ids) != len(set(ids)) or len(ids) != self.sample_size:
            raise ValueError("feedback sample does not match cases")
        if len({case.incident_id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate incident")
        expected = (
            (self.matches, ComparisonStatus.MATCH),
            (self.mismatches, ComparisonStatus.MISMATCH),
            (self.abstentions, ComparisonStatus.ABSTAINED),
            (self.conflicts, ComparisonStatus.CONFLICT),
            (self.unlabelled, ComparisonStatus.UNLABELLED),
            (self.incomplete, ComparisonStatus.SAMPLE_INCOMPLETE),
        )
        if any(
            count != sum(case.status is status for case in self.cases) for count, status in expected
        ):
            raise ValueError("comparison counts do not match cases")
        if self.incomplete and not self.has_more:
            raise ValueError("incomplete group requires truncation")
        if self.resolved_incidents != sum(
            case.incident_outcome is IncidentOutcome.RESOLVED for case in self.cases
        ) or self.unresolved_incidents != sum(
            case.incident_outcome is IncidentOutcome.UNRESOLVED for case in self.cases
        ):
            raise ValueError("outcome counts do not match cases")
        return self
