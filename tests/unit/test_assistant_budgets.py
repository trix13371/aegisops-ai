import pytest

from aegisops.assistant.budgets import (
    SOURCE_FREE_CHILD_SECONDS,
    SOURCE_FREE_REQUEST_SECONDS,
    SOURCE_REVIEW_EVALUATION_CHILD_SECONDS,
    SOURCE_REVIEW_MARGIN_SECONDS,
    SOURCE_REVIEW_METADATA_SECONDS,
    SOURCE_REVIEW_REQUEST_SECONDS,
    SOURCE_REVIEW_SINGLE_CHILD_SECONDS,
    SUPPORTED_EVALUATION_CASE_COUNTS,
    child_timeout,
    request_timeout,
)


def test_pure_budgets_request_constants() -> None:
    assert SOURCE_FREE_REQUEST_SECONDS == 30
    assert SOURCE_REVIEW_REQUEST_SECONDS == 90
    assert request_timeout(False) == 30
    assert request_timeout(True) == 90


def test_pure_budgets_child_derived_constants() -> None:
    assert SOURCE_REVIEW_METADATA_SECONDS == 30
    assert SOURCE_REVIEW_MARGIN_SECONDS == 15
    assert SOURCE_FREE_CHILD_SECONDS == 80
    assert SOURCE_REVIEW_SINGLE_CHILD_SECONDS == 135
    assert SOURCE_REVIEW_SINGLE_CHILD_SECONDS == (
        SOURCE_REVIEW_METADATA_SECONDS
        + SOURCE_REVIEW_REQUEST_SECONDS
        + SOURCE_REVIEW_MARGIN_SECONDS
    )

    assert SUPPORTED_EVALUATION_CASE_COUNTS == frozenset({3, 9})
    assert SOURCE_REVIEW_EVALUATION_CHILD_SECONDS[3] == 30 + 3 * 90 + 15  # 315
    assert SOURCE_REVIEW_EVALUATION_CHILD_SECONDS[9] == 30 + 9 * 90 + 15  # 855
    assert child_timeout(source_review=False) == 80
    assert child_timeout(source_review=True) == 135
    assert child_timeout(source_review=True, evaluation_cases=3) == 315
    assert child_timeout(source_review=True, evaluation_cases=9) == 855


def test_request_timeout_rejects_non_bool() -> None:
    for invalid in (1, 0, None, "true", "false", [], {}):
        with pytest.raises(TypeError, match="source_review must be a bool"):
            request_timeout(invalid)  # type: ignore[arg-type]


def test_child_timeout_rejects_invalid_inputs() -> None:
    for invalid in (1, 0, None, "true", "false", [], {}):
        with pytest.raises(TypeError, match="source_review must be a bool"):
            child_timeout(source_review=invalid)  # type: ignore[arg-type]

    for invalid_case in (True, False, 1.5, "3", [3], {9}):
        with pytest.raises(TypeError, match="evaluation_cases must be an int or None"):
            child_timeout(source_review=True, evaluation_cases=invalid_case)  # type: ignore[arg-type]

    for unknown_case in (0, 1, 2, 4, 8, 10, -3):
        with pytest.raises(ValueError, match="unsupported evaluation case count"):
            child_timeout(source_review=True, evaluation_cases=unknown_case)
