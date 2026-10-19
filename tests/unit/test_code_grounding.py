"""Unit tests for deterministic exact-quote grounding alignment."""

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import SecretStr

import aegisops.project_code.transport as code_transport
from aegisops.project_code.grounding import align_review
from aegisops.project_code.models import (
    CodeFile,
    CodeFinding,
    CodePreview,
    CodeReview,
    preview_digest,
    validate_review,
)


def _build_code_file(
    file_id: str,
    path: str,
    content: str,
    language: str = "python",
) -> CodeFile:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    line_count = max(1, len(content.splitlines()))
    return CodeFile(
        id=file_id,
        path=path,
        language=language,
        content=content,
        sha256=digest,
        lines=line_count,
    )


def _build_preview(files: tuple[CodeFile, ...]) -> CodePreview:
    return CodePreview(
        id=uuid4(),
        project_id=uuid4(),
        created_at=datetime.now(UTC),
        files=files,
    )


def _create_v2_preview() -> CodePreview:
    f1_content = "def ratio(a, b):\n    if b == 0:\n        return None\n    return a / b\n"
    f2_content = (
        "from math import sqrt\n"
        "\n"
        "def hypot(x, y):\n"
        "    # calculate distance\n"
        "    sq = x * x + y * y\n"
        "    return sqrt(sq)\n"
    )
    f1 = _build_code_file("f1", "src/math_ops/ratio.py", f1_content)
    f2 = _build_code_file("f2", "src/math_ops/geometry.py", f2_content)
    return _build_preview((f1, f2))


def test_already_valid_ranges_unchanged_even_if_quote_appears_elsewhere():
    # 'return None' is only on f1 line 3; let's create a file where 'value = 1' appears twice
    content = "value = 1\nother = 2\nvalue = 1\nfinal = 3\n"
    f1 = _build_code_file("f1", "src/test.py", content)
    p = _build_preview((f1,))

    finding = CodeFinding(
        file_id="f1",
        start_line=1,
        end_line=1,
        severity="info",
        category="correctness",
        title="First assignment",
        evidence="value = 1",
        explanation="Initial assignment explanation",
        recommendation="Keep as is",
    )
    review = CodeReview(
        summary="Valid review",
        findings=(finding,),
        limitations="None observed",
    )
    aligned = align_review(p, review)
    assert aligned.findings[0].start_line == 1
    assert aligned.findings[0].end_line == 1


def test_unique_off_by_one_multiline_range_repair_including_blank_lines():
    preview = _create_v2_preview()
    evidence = "sq = x * x + y * y\n    return sqrt(sq)"
    # Lines in f2: 1: import, 2: blank, 3: def hypot, 4: # comment, 5: sq = ..., 6: return sqrt(sq)
    # Erroneous AI range: start_line=4, end_line=5 (off by one)
    bad_finding = CodeFinding(
        file_id="f2",
        start_line=4,
        end_line=5,
        severity="warning",
        category="maintainability",
        title="Hypot computation",
        evidence=evidence,
        explanation="Could use math.hypot directly",
        recommendation="Replace with math.hypot(x, y)",
    )
    review = CodeReview(
        summary="Multifile review",
        findings=(bad_finding,),
        limitations="None with full context",
    )

    # Strict validate_review on the unaligned review must raise (not weakened)
    with pytest.raises(ValueError, match="evidence quote not found in source"):
        validate_review(preview, review)

    digest_before = preview_digest(preview)
    aligned = align_review(preview, review)
    digest_after = preview_digest(preview)

    assert digest_before == digest_after
    assert aligned is not review
    assert aligned.findings[0].start_line == 5
    assert aligned.findings[0].end_line == 6
    assert aligned.findings[0].evidence == evidence
    assert aligned.findings[0].explanation == bad_finding.explanation
    assert aligned.summary == review.summary
    assert aligned.limitations == review.limitations


def test_unknown_file_id_and_missing_quote_rejected():
    preview = _create_v2_preview()
    # Unknown file id
    finding_bad_file = CodeFinding(
        file_id="f3",
        start_line=1,
        end_line=1,
        severity="error",
        category="correctness",
        title="Missing file",
        evidence="anything",
        explanation="File does not exist",
        recommendation="Check file",
    )
    review_bad_file = CodeReview(
        summary="Summary",
        findings=(finding_bad_file,),
        limitations="Limitations",
    )
    with pytest.raises(ValueError, match="unknown file id"):
        align_review(preview, review_bad_file)

    # Missing quote entirely in file f1
    finding_missing_quote = CodeFinding(
        file_id="f1",
        start_line=1,
        end_line=1,
        severity="error",
        category="correctness",
        title="Missing text",
        evidence="nonexistent snippet 404",
        explanation="Quote missing completely",
        recommendation="Check quote",
    )
    review_missing_quote = CodeReview(
        summary="Summary",
        findings=(finding_missing_quote,),
        limitations="Limitations",
    )
    with pytest.raises(ValueError, match="evidence quote not found in source"):
        align_review(preview, review_missing_quote)


def test_quoted_text_in_other_file_only_rejected():
    preview = _create_v2_preview()
    # Evidence belongs to f1 ('return a / b'), but reported under f2
    finding_cross_file = CodeFinding(
        file_id="f2",
        start_line=1,
        end_line=1,
        severity="error",
        category="correctness",
        title="Cross file quote",
        evidence="return a / b",
        explanation="Exists in f1, not f2",
        recommendation="Fix file reference",
    )
    review = CodeReview(
        summary="Summary",
        findings=(finding_cross_file,),
        limitations="Limitations",
    )
    with pytest.raises(ValueError, match="evidence quote not found in source"):
        align_review(preview, review)


def test_multiple_occurrences_including_overlap_rejected_when_reported_wrong():
    # Test overlapping occurrences like 'aaa' containing 'aa' at index 0 and 1
    content = "aaa\n"
    f1 = _build_code_file("f1", "src/overlap.py", content)
    preview = _build_preview((f1,))

    # 'aa' occurs twice in 'aaa' (indices 0 and 1)
    # If finding reported span doesn't match or is out of range:
    finding = CodeFinding(
        file_id="f1",
        start_line=1,
        end_line=2,  # out of bounds or wrong span
        severity="warning",
        category="correctness",
        title="Overlap ambiguous",
        evidence="aa",
        explanation="Explanation text",
        recommendation="Recommendation text",
    )
    review = CodeReview(
        summary="Summary text",
        findings=(finding,),
        limitations="Limitations text",
    )
    with pytest.raises(ValueError, match="evidence quote is ambiguous in source"):
        align_review(preview, review)


def test_over_20_line_quote_rejected():
    content = "\n".join(f"line_{i} = {i}" for i in range(1, 30)) + "\n"
    f1 = _build_code_file("f1", "src/long.py", content)
    preview = _build_preview((f1,))

    # Extract a 21-line quote
    evidence = "\n".join(f"line_{i} = {i}" for i in range(1, 22))
    finding = CodeFinding(
        file_id="f1",
        start_line=1,
        end_line=2,  # deliberately wrong span to trigger realignment
        severity="info",
        category="maintainability",
        title="Too long span",
        evidence=evidence,
        explanation="Explanation text",
        recommendation="Recommendation text",
    )
    review = CodeReview(
        summary="Summary text",
        findings=(finding,),
        limitations="Limitations text",
    )
    with pytest.raises(ValueError, match="evidence quote span exceeds limit"):
        align_review(preview, review)


def test_valid_crlf_normalized_to_canonical_lf():
    crlf_content = "first_line = 10\r\nsecond_line = 20\r\nthird_line = 30\r\n"
    f1 = _build_code_file("f1", "src/crlf.py", crlf_content)
    preview = _build_preview((f1,))

    finding = CodeFinding(
        file_id="f1",
        start_line=1,
        end_line=1,  # wrong line
        severity="info",
        category="maintainability",
        title="CRLF check",
        evidence="second_line = 20",
        explanation="Align CRLF source",
        recommendation="Check",
    )
    review = CodeReview(
        summary="Summary",
        findings=(finding,),
        limitations="Limitations",
    )
    aligned = align_review(preview, review)
    assert aligned.findings[0].start_line == 2
    assert aligned.findings[0].end_line == 2


def test_leading_trailing_newline_and_partial_line_unique_quotes():
    content = "alpha = 1\nbeta = 2\ngamma = 3\ndelta = 4\n"
    f1 = _build_code_file("f1", "src/mixed.py", content)
    preview = _build_preview((f1,))

    # Quote with leading and trailing newline: "\nbeta = 2\n"
    # canonical has lines: 1: alpha = 1, 2: beta = 2, 3: gamma = 3, 4: delta = 4
    # "\nbeta = 2\n" begins on line 1 newline and ends on line 2 newline.
    q1 = "\nbeta = 2\n"
    f_leading_trailing = CodeFinding(
        file_id="f1",
        start_line=1,
        end_line=1,  # wrong
        severity="info",
        category="reliability",
        title="Newlines quote",
        evidence=q1,
        explanation="Explanation",
        recommendation="Recommendation",
    )

    # Partial line unique quote: "mma = 3"
    q2 = "mma = 3"
    f_partial = CodeFinding(
        file_id="f1",
        start_line=1,
        end_line=1,  # wrong
        severity="warning",
        category="correctness",
        title="Partial line quote",
        evidence=q2,
        explanation="Explanation",
        recommendation="Recommendation",
    )

    review = CodeReview(
        summary="Summary",
        findings=(f_leading_trailing, f_partial),
        limitations="Limitations",
    )
    aligned = align_review(preview, review)
    # q1 span:
    assert aligned.findings[0].start_line == 1
    assert aligned.findings[0].end_line == 3
    # q2 span: gamma = 3 is line 3
    assert aligned.findings[1].start_line == 3
    assert aligned.findings[1].end_line == 3


def test_duplicate_finding_collision_after_alignment_rejected():
    content = "line_one = 1\nline_two = 2\nline_three = 3\n"
    f1 = _build_code_file("f1", "src/collision.py", content)
    preview = _build_preview((f1,))

    # Two findings with same file_id and title (case-insensitive), different old ranges
    # Both will align to line 2 for evidence 'line_two = 2'
    f_a = CodeFinding(
        file_id="f1",
        start_line=1,
        end_line=1,
        severity="info",
        category="correctness",
        title="Same Title",
        evidence="line_two = 2",
        explanation="Explanation A",
        recommendation="Recommendation A",
    )
    f_b = CodeFinding(
        file_id="f1",
        start_line=3,
        end_line=3,
        severity="warning",
        category="reliability",
        title="same title",
        evidence="line_two = 2",
        explanation="Explanation B",
        recommendation="Recommendation B",
    )
    review = CodeReview(
        summary="Summary",
        findings=(f_a, f_b),
        limitations="Limitations",
    )
    with pytest.raises(ValueError, match="duplicate finding"):
        align_review(preview, review)


def test_forged_model_construct_wrong_hash_or_type_rejected_no_raw_quote_leak():
    # Construct forged preview bypassing validation with mismatched sha256
    forged_file = CodeFile.model_construct(
        id="f1",
        path="src/forged.py",
        language="python",
        content="x = 100\n",
        sha256="wrong_hash_00000000000000000000000000000000000000000000000000000000",
        lines=1,
    )
    forged_preview = CodePreview.model_construct(
        profile="project-code-review-v1",
        id=uuid4(),
        project_id=uuid4(),
        created_at=datetime.now(UTC),
        files=(forged_file,),
    )
    sensitive_quote = "SECRET_KEY_12345"
    finding = CodeFinding(
        file_id="f1",
        start_line=1,
        end_line=1,
        severity="info",
        category="security",
        title="Security leak check",
        evidence=sensitive_quote,
        explanation="Explanation",
        recommendation="Recommendation",
    )
    review = CodeReview(
        summary="Summary",
        findings=(finding,),
        limitations="Limitations",
    )

    with pytest.raises(ValueError) as excinfo:
        align_review(forged_preview, review)
    err_msg = str(excinfo.value)
    assert sensitive_quote not in err_msg


def test_transport_integration_exchange_repairs_and_validates(monkeypatch):
    preview = _create_v2_preview()
    evidence = "sq = x * x + y * y\n    return sqrt(sq)"

    # Model returned wrong lines (e.g. 4-5 instead of 5-6)
    raw_model_review = {
        "summary": "Analysis completed with minor suggestions.",
        "findings": [
            {
                "file_id": "f2",
                "start_line": 4,
                "end_line": 5,
                "severity": "warning",
                "category": "maintainability",
                "title": "Use math.hypot",
                "evidence": evidence,
                "explanation": "Direct calculation can be simplified.",
                "recommendation": "Use math.hypot(x, y).",
            }
        ],
        "limitations": "External call sites are unseen.",
    }

    def fake_exchange(wire_content, instructions, key, source_review=True):
        return raw_model_review, 150, 80

    monkeypatch.setattr(code_transport, "_exchange", fake_exchange)

    review_out, in_tok, out_tok = code_transport.explain_code(preview, SecretStr("dummy-key"))
    assert in_tok == 150
    assert out_tok == 80
    assert len(review_out.findings) == 1
    # Must be corrected to line 5-6 and strictly valid
    assert review_out.findings[0].start_line == 5
    assert review_out.findings[0].end_line == 6
    validated = validate_review(preview, review_out)
    assert validated == review_out
