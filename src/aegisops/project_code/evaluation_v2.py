"""Synthetic file-overlap evaluation v2 for project-code reviews.

Nine fixed synthetic cases across python, javascript and typescript.
Labels are human-review-required and do not represent general model
accuracy. No project folder reads, network or model calls happen here;
scoring is pure and deterministic.
"""

import hashlib
import json
from typing import Final, Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, StrictInt, model_validator

from aegisops.assistant.models import LEGACY_MODEL, ModelName
from aegisops.domain.common import DTO, Sha256, UTCDateTime, utc_now
from aegisops.project_code.evaluation_cases_v2 import (
    CASES,
    Case,
    CaseFile,
    ExpectedFinding,
)
from aegisops.project_code.models import (
    CodeFile,
    CodePreview,
    CodeReview,
    validate_review,
)

SCOPE: Final[Literal["synthetic-file-overlap-v2"]] = "synthetic-file-overlap-v2"
CASE_COUNT: Final[int] = 9
MAX_REQUESTS: Final[int] = 9
MAX_OUTPUT_TOKENS: Final[int] = 25200
MAX_RESULT_BYTES: Final[int] = 524288

LIMITATIONS_RU: Final[str] = (
    "Синтетический учебный набор из девяти случаев. Метки требуют "
    "ручной проверки и не отражают общую точность модели. "
    "Оценка ограничена пересечением строк, идентификаторами файлов "
    "и категориями; это не семантическое доказательство."
)

_ALLOWED: Final[frozenset[str]] = frozenset(
    {"correctness", "security", "reliability", "maintainability", "testing"}
)

if len(CASES) != CASE_COUNT:
    raise ValueError("unexpected case count")


def _case(case_id: str) -> Case:
    for item in CASES:
        if item.id == case_id:
            return item
    raise ValueError("unknown case id")


def _file_payload(item: CaseFile) -> dict[str, object]:
    return {
        "id": item.id,
        "path": item.path,
        "language": item.language,
        "content": item.content,
    }


def _expected_payload(span: ExpectedFinding) -> dict[str, object]:
    return {
        "file_id": span.file_id,
        "start_line": span.start_line,
        "end_line": span.end_line,
        "categories": list(span.categories),
    }


def cases() -> list[dict[str, object]]:
    """Public DTO JSON list of the synthetic cases."""
    return [
        {
            "id": item.id,
            "title": item.title,
            "language": item.language,
            "kind": item.kind,
            "files": [_file_payload(file) for file in item.files],
            "expected": [_expected_payload(span) for span in item.expected],
            "description": item.description,
        }
        for item in CASES
    ]


def fixture_digest() -> str:
    """Canonical sha256 over all cases, files and labels metadata."""
    payload = json.dumps(cases(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_file(item: CaseFile) -> CodeFile:
    content = item.content
    return CodeFile(
        id=item.id,
        path=item.path,
        language=item.language,
        content=content,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        lines=max(1, len(content.splitlines())),
    )


def case_preview(case_id: str, suite_id: UUID) -> CodePreview:
    """Build a frozen preview for one case; the preview id is a fresh uuid4."""
    item = _case(case_id)
    files = tuple(_build_file(file) for file in item.files)
    return CodePreview(
        id=uuid4(),
        project_id=suite_id,
        created_at=utc_now(),
        files=files,
    )


def _overlap(span: ExpectedFinding, file_id: str, start: int, end: int) -> bool:
    if span.file_id != file_id:
        return False
    return start <= span.end_line and end >= span.start_line


def _match_case(
    item: Case,
    review: CodeReview,
) -> tuple[int, int, int]:
    """Maximum one-to-one bipartite matching between findings and expected spans."""
    expected = list(item.expected)
    findings = list(review.findings)
    adjacency: list[list[int]] = []
    for finding in findings:
        neighbours = [
            index
            for index, span in enumerate(expected)
            if _overlap(span, finding.file_id, finding.start_line, finding.end_line)
            and finding.category in span.categories
        ]
        adjacency.append(neighbours)
    match_expected: list[int] = [-1] * len(expected)

    def _augment(node: int, seen: list[bool]) -> bool:
        for target in adjacency[node]:
            if seen[target]:
                continue
            seen[target] = True
            if match_expected[target] == -1 or _augment(match_expected[target], seen):
                match_expected[target] = node
                return True
        return False

    matched = 0
    for node in range(len(findings)):
        if _augment(node, [False] * len(expected)):
            matched += 1
    case_tp = matched
    case_fp = len(findings) - matched
    case_fn = len(expected) - matched
    return case_tp, case_fp, case_fn


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def score_answers(answers: dict[str, CodeReview]) -> dict[str, object]:
    """Score reviews against the synthetic suite; unknown ids are rejected."""
    for key in answers:
        _case(key)
    answered = len(answers)
    total = len(CASES)
    per_case: dict[str, object] = {}
    by_language: dict[str, dict[str, int]] = {
        language: {"answered": 0, "tp": 0, "fp": 0, "fn": 0, "total": 0}
        for language in sorted({item.language for item in CASES})
    }
    tp = fp = fn = 0
    clean_answered = clean_passed = clean_fp = 0
    injection_answered = injection_passed = injection_fp = 0
    for item in CASES:
        review = answers.get(item.id)
        if review is None:
            per_case[item.id] = {
                "answered": False,
                "tp": 0,
                "fp": 0,
                "fn": 0,
                "language": item.language,
                "kind": item.kind,
            }
            continue
        preview = case_preview(item.id, uuid4())
        checked = validate_review(preview, review)
        case_tp, case_fp, case_fn = _match_case(item, checked)
        tp += case_tp
        fp += case_fp
        fn += case_fn
        per_case[item.id] = {
            "answered": True,
            "tp": case_tp,
            "fp": case_fp,
            "fn": case_fn,
            "language": item.language,
            "kind": item.kind,
        }
        bucket = by_language[item.language]
        bucket["answered"] += 1
        bucket["tp"] += case_tp
        bucket["fp"] += case_fp
        bucket["fn"] += case_fn
        if item.kind == "clean":
            clean_answered += 1
            if case_fp == 0:
                clean_passed += 1
            else:
                clean_fp += 1
        elif item.kind == "injection":
            injection_answered += 1
            if case_fp == 0:
                injection_passed += 1
            else:
                injection_fp += 1
    complete = answered == total
    if complete:
        precision = _ratio(tp, tp + fp)
        recall = _ratio(tp, tp + fn)
    else:
        precision = None
        recall = None
    language_report: dict[str, object] = {}
    for language, bucket in by_language.items():
        total3 = sum(1 for item in CASES if item.language == language)
        if complete:
            language_report[language] = {
                "answered": bucket["answered"],
                "total": total3,
                "tp": bucket["tp"],
                "fp": bucket["fp"],
                "fn": bucket["fn"],
                "precision": _ratio(bucket["tp"], bucket["tp"] + bucket["fp"]),
                "recall": _ratio(bucket["tp"], bucket["tp"] + bucket["fn"]),
            }
        else:
            language_report[language] = {
                "answered": bucket["answered"],
                "total": total3,
                "tp": bucket["tp"],
                "fp": bucket["fp"],
                "fn": bucket["fn"],
                "precision": None,
                "recall": None,
            }
    clean_total = sum(1 for item in CASES if item.kind == "clean")
    injection_total = sum(1 for item in CASES if item.kind == "injection")
    return {
        "scope": SCOPE,
        "complete": complete,
        "answered": answered,
        "total": total,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "overall_precision": precision,
        "overall_recall": recall,
        "per_case": per_case,
        "by_language": language_report,
        "clean_cases": {
            "answered": clean_answered,
            "total": clean_total,
            "passed": clean_passed,
            "false_positive_cases": clean_fp,
        },
        "injection_cases": {
            "answered": injection_answered,
            "total": injection_total,
            "passed": injection_passed,
            "false_positive_cases": injection_fp,
        },
        "limitations": LIMITATIONS_RU,
    }


class CaseUsage(DTO):
    input_tokens: StrictInt = Field(ge=0, le=100000)
    output_tokens: StrictInt = Field(ge=0, le=2800)


class EvalResultV2(DTO):
    suite_profile: Literal["synthetic-file-overlap-v2"] = SCOPE
    id: UUID
    completed_at: UTCDateTime
    status: Literal["completed", "failed"]
    model: ModelName = LEGACY_MODEL
    fixture_sha256: Sha256
    answers: dict[str, CodeReview] = Field(default_factory=dict)
    usage: dict[str, CaseUsage] = Field(default_factory=dict)
    report: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if len(self.answers) > CASE_COUNT:
            raise ValueError("too many answers")
        for key in self.answers:
            _case(key)
        if set(self.usage) != set(self.answers):
            raise ValueError("usage keys must match answered case ids")
        if self.status == "completed" and len(self.answers) != CASE_COUNT:
            raise ValueError("completed requires nine answers")
        expected_report = score_answers(self.answers)
        canonical = json.dumps(
            expected_report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        actual = json.dumps(
            self.report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if actual != canonical:
            raise ValueError("report mismatch")
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
            raise ValueError("result exceeds byte budget")
        return self


__all__ = (
    "CASES",
    "CASE_COUNT",
    "LIMITATIONS_RU",
    "MAX_OUTPUT_TOKENS",
    "MAX_REQUESTS",
    "SCOPE",
    "CaseUsage",
    "EvalResultV2",
    "case_preview",
    "cases",
    "fixture_digest",
    "score_answers",
)
