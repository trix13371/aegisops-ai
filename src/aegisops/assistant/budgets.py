"""Fixed, centralized time budgets for assistant wire requests.

Pure module: no I/O, no environment access, no UI overrides. Callers may only
select between the two supported request profiles (source-free vs. selected-code
source review) and the supported evaluation case counts. Arbitrary time budgets
cannot be requested.
"""

from __future__ import annotations

# Request budgets (seconds) for a single provider round-trip.
SOURCE_FREE_REQUEST_SECONDS = 30
SOURCE_REVIEW_REQUEST_SECONDS = 90

# Metadata + request + margin that compose the source-review single child budget.
SOURCE_REVIEW_METADATA_SECONDS = 30
SOURCE_REVIEW_MARGIN_SECONDS = 15

# Child-process budgets (seconds).
SOURCE_FREE_CHILD_SECONDS = 80
SOURCE_REVIEW_SINGLE_CHILD_SECONDS = (
    SOURCE_REVIEW_METADATA_SECONDS + SOURCE_REVIEW_REQUEST_SECONDS + SOURCE_REVIEW_MARGIN_SECONDS
)

# Source-review evaluation child budgets (seconds) per supported case count.
SOURCE_REVIEW_EVALUATION_CHILD_SECONDS = {
    count: SOURCE_REVIEW_METADATA_SECONDS
    + count * SOURCE_REVIEW_REQUEST_SECONDS
    + SOURCE_REVIEW_MARGIN_SECONDS
    for count in (3, 9)
}

SUPPORTED_EVALUATION_CASE_COUNTS = frozenset(SOURCE_REVIEW_EVALUATION_CHILD_SECONDS)


def request_timeout(source_review: bool) -> int:
    """Return the fixed per-request timeout for the selected profile."""
    if type(source_review) is not bool:
        raise TypeError("source_review must be a bool")
    return SOURCE_REVIEW_REQUEST_SECONDS if source_review else SOURCE_FREE_REQUEST_SECONDS


def child_timeout(*, source_review: bool = False, evaluation_cases: int | None = None) -> int:
    """Return the fixed child-process timeout for the selected profile.

    ``evaluation_cases`` is ``None`` for a single child, or one of the supported
    evaluation case counts (3 or 9). Booleans and unknown counts are rejected.
    """
    if type(source_review) is not bool:
        raise TypeError("source_review must be a bool")
    if evaluation_cases is None:
        return SOURCE_REVIEW_SINGLE_CHILD_SECONDS if source_review else SOURCE_FREE_CHILD_SECONDS
    if type(evaluation_cases) is not int or isinstance(evaluation_cases, bool):
        raise TypeError("evaluation_cases must be an int or None")
    if evaluation_cases not in SUPPORTED_EVALUATION_CASE_COUNTS:
        raise ValueError("unsupported evaluation case count")
    return SOURCE_REVIEW_EVALUATION_CHILD_SECONDS[evaluation_cases]
