"""Deterministic exact-quote alignment for model code reviews.

This module performs a purely textual, byte-exact alignment of a review's
evidence quotes against the frozen preview source. It is a mechanical
uniqueness check, not a semantic proof: a unique exact match only means the
quote occurs exactly once in the canonical joined source, never that the
finding is correct, reachable, or safe.

No I/O, services, credentials, code execution, hidden paths, model calls or
retries are performed here.
"""

from __future__ import annotations

from aegisops.project_code.models import (
    MAX_SPAN,
    CodeFinding,
    CodePreview,
    CodeReview,
    validate_review,
)


def _canonical_source(content: str) -> str:
    """Canonical joined source, matching the existing validator normalization."""
    return "\n".join(content.splitlines())


def _count_occurrences(haystack: str, needle: str) -> int:
    """Count all occurrences of needle in haystack, including overlapping ones."""
    if not needle:
        return 0
    count = 0
    start = 0
    while True:
        index = haystack.find(needle, start)
        if index < 0:
            return count
        count += 1
        start = index + 1


def _line_span_for_quote(source: str, index: int, length: int) -> tuple[int, int]:
    """Inclusive 1-based line span containing source[index:index+length].

    Line numbers are derived from newline counts in the canonical joined
    source. The exclusive quote end index is used so that every newline
    character inside the quote is counted, including a trailing newline.
    A quote that begins on a newline starts on the preceding line and
    includes the following content; a quote that ends on a newline is
    attributed to the line that actually contains its last character, so
    the quote is contained within the returned inclusive range.
    """
    end_index = index + length
    start_line = source.count("\n", 0, index) + 1
    end_line = source.count("\n", 0, end_index) + 1
    return start_line, end_line


def _align_finding(source_content: str, source_lines: int, finding: CodeFinding) -> CodeFinding:
    """Return a finding whose span exactly contains its unique evidence quote.

    Already-valid file/span/evidence ranges are kept unchanged, even when the
    quote also appears elsewhere. Only out-of-bounds ranges or quotes that are
    not contained in the reported range are realigned.
    """
    canonical = _canonical_source(source_content)
    lines = canonical.split("\n")

    in_bounds = finding.end_line <= source_lines and finding.end_line <= len(lines)
    if in_bounds:
        joined = "\n".join(lines[finding.start_line - 1 : finding.end_line])
        if finding.evidence in joined:
            return finding

    occurrences = _count_occurrences(canonical, finding.evidence)
    if occurrences == 0:
        raise ValueError("evidence quote not found in source")
    if occurrences > 1:
        raise ValueError("evidence quote is ambiguous in source")

    index = canonical.find(finding.evidence)
    start_line, end_line = _line_span_for_quote(canonical, index, len(finding.evidence))
    if end_line - start_line + 1 > MAX_SPAN:
        raise ValueError("evidence quote span exceeds limit")
    if end_line > source_lines or end_line > len(lines):
        raise ValueError("line reference out of range")

    return CodeFinding.model_validate(
        {
            "file_id": finding.file_id,
            "start_line": start_line,
            "end_line": end_line,
            "severity": finding.severity,
            "category": finding.category,
            "title": finding.title,
            "evidence": finding.evidence,
            "explanation": finding.explanation,
            "recommendation": finding.recommendation,
        }
    )


def align_review(preview: CodePreview, review: CodeReview) -> CodeReview:
    """Revalidate DTOs and align evidence quotes to unique exact source spans.

    DTOs are fully revalidated from model_dump dictionaries to avoid any
    model_construct bypass. Files are matched strictly by frozen file id.
    Findings that are already valid are preserved unchanged. Findings whose
    reported range is out of bounds or does not contain the evidence quote are
    realigned only when the exact quote occurs exactly once in the canonical
    joined source. Missing, ambiguous, wrong-file or over-limit quotes raise a
    fixed ValueError. Duplicate findings created by alignment are detected and
    rejected rather than silently merged or dropped.
    """
    checked_preview = CodePreview.model_validate(preview.model_dump(mode="python"))
    checked_review = CodeReview.model_validate(review.model_dump(mode="python"))

    by_id = {item.id: item for item in checked_preview.files}

    aligned: list[CodeFinding] = []
    for finding in checked_review.findings:
        source = by_id.get(finding.file_id)
        if source is None:
            raise ValueError("unknown file id")
        aligned.append(_align_finding(source.content, source.lines, finding))

    seen: set[tuple[str, int, int, str]] = set()
    for item in aligned:
        key = (item.file_id, item.start_line, item.end_line, item.title.casefold())
        if key in seen:
            raise ValueError("duplicate finding")
        seen.add(key)

    aligned_review = CodeReview.model_validate(
        {
            "summary": checked_review.summary,
            "findings": [item.model_dump(mode="python") for item in aligned],
            "limitations": checked_review.limitations,
        }
    )

    return validate_review(checked_preview, aligned_review)
