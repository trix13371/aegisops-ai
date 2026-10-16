"""Closed full-check summary profile, separate from static and combined review profiles."""

from typing import Final, Literal, Self

from pydantic import Field, model_validator

from aegisops.domain.common import DTO
from aegisops.project_ai.review_models import (
    ReviewCategory,
    RunnerSummary,
    StaticSummary,
)

FULL_PROFILE: Final = "project-full-check-summary-v1"

FullStatus = Literal["passed", "issues", "incomplete"]
FullSourceMatch = Literal["shared_snapshot", "not_confirmed"]
FullLimitation = Literal[
    "missing_static",
    "partial_static",
    "unconfirmed_runner",
    "truncated_output",
    "selected_profile_only",
    "sequential_capture",
    "missing_snapshot",
]


class FullSummary(DTO):
    profile: Literal["project-full-check-summary-v1"] = FULL_PROFILE
    status: FullStatus
    source_match: FullSourceMatch
    categories: tuple[ReviewCategory, ...] = Field(max_length=35)
    static: StaticSummary | None
    runner: RunnerSummary
    limitations: tuple[FullLimitation, ...] = Field(max_length=7)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if len({(item.code, item.severity) for item in self.categories}) != len(self.categories):
            raise ValueError("duplicate full category")
        if len(set(self.limitations)) != len(self.limitations):
            raise ValueError("duplicate full limitation")
        if "selected_profile_only" not in self.limitations:
            raise ValueError("missing selected profile limitation")
        if "sequential_capture" not in self.limitations:
            raise ValueError("missing sequential capture limitation")
        return self


__all__ = (
    "FULL_PROFILE",
    "FullLimitation",
    "FullSourceMatch",
    "FullStatus",
    "FullSummary",
)
