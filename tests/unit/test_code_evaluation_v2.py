import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aegisops.domain.common import utc_now
from aegisops.project_code import (
    evaluation as v1,
)
from aegisops.project_code import (
    evaluation_cases_v2 as cases_v2,
)
from aegisops.project_code import (
    evaluation_v2 as v2,
)
from aegisops.project_code.evaluation_cases_v2 import (
    Case,
    CaseFile,
    ExpectedFinding,
)
from aegisops.project_code.evaluation_repository import EvalRepository
from aegisops.project_code.models import CodeFinding, CodeReview, wire_payload
from aegisops.projects.repository import ProjectRepository


def _make_review_for_case(case: Case) -> CodeReview:
    if case.kind != "bug":
        return CodeReview(
            summary="No defects found in clean/injection case",
            limitations="Synthetic static check limitations",
            findings=(),
        )
    findings = []
    files_by_id = {f.id: f for f in case.files}
    for idx, exp in enumerate(case.expected):
        src = files_by_id[exp.file_id]
        lines = src.content.splitlines()
        quote = "\n".join(lines[exp.start_line - 1 : exp.end_line])
        findings.append(
            CodeFinding(
                file_id=exp.file_id,
                start_line=exp.start_line,
                end_line=exp.end_line,
                severity="error",
                category=exp.categories[0],
                title=f"Defect observed in {case.id} finding {idx}",
                evidence=quote,
                explanation="Explanation for synthetic defect",
                recommendation="Fix synthetic defect carefully",
            )
        )
    return CodeReview(
        summary=f"Review for {case.id}",
        limitations="Evaluation review limitations",
        findings=tuple(findings),
    )


def test_suite_composition_and_preview_wire_payload() -> None:
    all_cases = cases_v2.cases()
    assert len(all_cases) == 9

    lang_counts = {}
    for c in all_cases:
        lang_counts[c.language] = lang_counts.get(c.language, 0) + 1
    assert lang_counts == {"python": 3, "javascript": 3, "typescript": 3}

    multifile = [c for c in all_cases if len(c.files) > 1]
    assert len(multifile) == 2
    for c in multifile:
        assert len(c.files) == 2

    clean_cases = [c for c in all_cases if c.kind == "clean"]
    assert len(clean_cases) == 2
    injection_cases = [c for c in all_cases if c.kind == "injection"]
    assert len(injection_cases) == 2

    initial_digest = cases_v2.fixture_digest()
    assert initial_digest == v2.fixture_digest()
    assert len(initial_digest) == 64

    suite_id1 = uuid4()
    suite_id2 = uuid4()
    for c in all_cases:
        p1 = v2.case_preview(c.id, suite_id1)
        p2 = v2.case_preview(c.id, suite_id2)
        assert p1.id != p2.id

        for f in p1.files:
            expected_sha = hashlib.sha256(f.content.encode("utf-8")).hexdigest()
            assert f.sha256 == expected_sha
            assert f.lines == max(1, len(f.content.splitlines()))

        wire = wire_payload(p1)
        assert set(wire.keys()) == {"profile", "files"}
        assert wire["profile"] == "project-code-review-v1"
        assert isinstance(wire["files"], list)
        for item in wire["files"]:
            assert set(item.keys()) == {"id", "language", "content"}
            assert "path" not in item
            assert "title" not in item
            assert "labels" not in item
            assert "expected" not in item

    assert cases_v2.fixture_digest() == initial_digest
    assert v2.fixture_digest() == initial_digest


def test_scoring_perfect_synthetic_answers() -> None:
    perfect_answers = {c.id: _make_review_for_case(c) for c in cases_v2.cases()}
    score = v2.score_answers(perfect_answers)
    assert score["scope"] == v2.SCOPE
    assert score["complete"] is True
    assert score["answered"] == 9
    assert score["total"] == 9
    assert score["tp"] == 5
    assert score["fp"] == 0
    assert score["fn"] == 0
    assert score["overall_precision"] == 1.0
    assert score["overall_recall"] == 1.0

    clean_info = score["clean_cases"]
    assert clean_info["answered"] == 2
    assert clean_info["total"] == 2
    assert clean_info["passed"] == 2
    assert clean_info["false_positive_cases"] == 0

    inj_info = score["injection_cases"]
    assert inj_info["answered"] == 2
    assert inj_info["total"] == 2
    assert inj_info["passed"] == 2
    assert inj_info["false_positive_cases"] == 0


def test_scoring_empty_nine_reviews() -> None:
    empty_review = CodeReview(
        summary="No findings reported",
        limitations="Limitations text",
        findings=(),
    )
    empty_answers = {c.id: empty_review for c in cases_v2.cases()}
    score = v2.score_answers(empty_answers)
    assert score["complete"] is True
    assert score["answered"] == 9
    assert score["tp"] == 0
    assert score["fp"] == 0
    assert score["fn"] == 5
    assert score["overall_recall"] == 0.0
    assert score["overall_precision"] is None

    assert score["clean_cases"]["passed"] == 2
    assert score["clean_cases"]["false_positive_cases"] == 0
    assert score["injection_cases"]["passed"] == 2
    assert score["injection_cases"]["false_positive_cases"] == 0


def test_scoring_partial_answers() -> None:
    some_case = cases_v2.cases()[0]
    partial_answers = {some_case.id: _make_review_for_case(some_case)}
    score = v2.score_answers(partial_answers)
    assert score["complete"] is False
    assert score["answered"] == 1
    assert score["overall_precision"] is None
    assert score["overall_recall"] is None

    by_lang = score["by_language"]
    assert set(by_lang.keys()) == {"python", "javascript", "typescript"}
    for lang, metrics in by_lang.items():
        assert metrics["precision"] is None
        assert metrics["recall"] is None
        assert metrics["total"] == 3


def test_invalid_case_or_quote_raises() -> None:
    with pytest.raises(ValueError, match="unknown case id"):
        v2.score_answers(
            {
                "nonexistent-case-id": CodeReview(
                    summary="Summary", limitations="Limitations", findings=()
                )
            }
        )

    case = cases_v2.cases()[0]
    bad_quote_review = CodeReview(
        summary="Summary text",
        limitations="Limitations text",
        findings=(
            CodeFinding(
                file_id="f1",
                start_line=1,
                end_line=2,
                severity="warning",
                category="correctness",
                title="Forged quote finding",
                evidence="nonexistent string not present in code",
                explanation="Explanation text",
                recommendation="Recommendation text",
            ),
        ),
    )
    with pytest.raises(ValueError, match="evidence quote not found in source"):
        v2.score_answers({case.id: bad_quote_review})

    unknown_file_review = CodeReview(
        summary="Summary text",
        limitations="Limitations text",
        findings=(
            CodeFinding(
                file_id="f3",
                start_line=1,
                end_line=1,
                severity="info",
                category="correctness",
                title="Unknown file finding",
                evidence="def total(values):",
                explanation="Explanation text",
                recommendation="Recommendation text",
            ),
        ),
    )
    with pytest.raises(ValueError, match="unknown file id"):
        v2.score_answers({case.id: unknown_file_review})


def test_duplicate_predictions_yield_one_tp_and_extra_fp() -> None:
    case = cases_v2.cases()[0]
    assert len(case.expected) == 1
    exp = case.expected[0]
    src = case.files[0]
    lines = src.content.splitlines()
    quote = "\n".join(lines[exp.start_line - 1 : exp.end_line])

    f1 = CodeFinding(
        file_id=exp.file_id,
        start_line=exp.start_line,
        end_line=exp.end_line,
        severity="error",
        category=exp.categories[0],
        title="Defect title 1",
        evidence=quote,
        explanation="Exp 1",
        recommendation="Rec 1",
    )
    f2 = CodeFinding(
        file_id=exp.file_id,
        start_line=exp.start_line,
        end_line=exp.end_line,
        severity="warning",
        category=exp.categories[0],
        title="Defect title 2",
        evidence=quote,
        explanation="Exp 2",
        recommendation="Rec 2",
    )
    review = CodeReview(
        summary="Review with duplicates",
        limitations="Limitations text",
        findings=(f1, f2),
    )
    tp, fp, fn = v2._match_case(case, review)
    assert (tp, fp, fn) == (1, 1, 0)


def test_wrong_file_same_line_never_tp() -> None:
    case = [c for c in cases_v2.cases() if c.id == "typescript-multifile-getuser-null"][0]
    exp = case.expected[0]
    assert exp.file_id == "f2"

    wrong_file = "f1"
    f1_src = [f for f in case.files if f.id == wrong_file][0]
    line_no = exp.start_line
    line_content = f1_src.content.splitlines()[line_no - 1]

    finding = CodeFinding(
        file_id=wrong_file,
        start_line=line_no,
        end_line=line_no,
        severity="error",
        category=exp.categories[0],
        title="Finding on wrong file",
        evidence=line_content,
        explanation="Explanation",
        recommendation="Recommendation",
    )
    review = CodeReview(
        summary="Review on wrong file",
        limitations="Limitations",
        findings=(finding,),
    )
    tp, fp, fn = v2._match_case(case, review)
    assert tp == 0
    assert fp == 1
    assert fn == 1


def test_maximum_bipartite_matching_two_instead_of_greedy_one() -> None:
    mock_case = Case(
        id="mock-case-match",
        title="Mock Case for Bipartite Match",
        language="python",
        kind="bug",
        files=(
            CaseFile(
                id="f1",
                path="code.py",
                language="python",
                content="line1\nline2\nline3\nline4\n",
            ),
        ),
        expected=(
            ExpectedFinding(file_id="f1", start_line=1, end_line=3, categories=("correctness",)),
            ExpectedFinding(file_id="f1", start_line=2, end_line=4, categories=("correctness",)),
        ),
        description="Mock description for bipartite matching test.",
    )

    finding_both = CodeFinding(
        file_id="f1",
        start_line=2,
        end_line=3,
        severity="error",
        category="correctness",
        title="Overlaps both expected",
        evidence="line2\nline3",
        explanation="Explanation 1",
        recommendation="Recommendation 1",
    )
    finding_second = CodeFinding(
        file_id="f1",
        start_line=4,
        end_line=4,
        severity="error",
        category="correctness",
        title="Overlaps only second expected",
        evidence="line4",
        explanation="Explanation 2",
        recommendation="Recommendation 2",
    )

    review_order1 = CodeReview(
        summary="Order 1",
        limitations="Limitation",
        findings=(finding_both, finding_second),
    )
    tp1, fp1, fn1 = v2._match_case(mock_case, review_order1)
    assert (tp1, fp1, fn1) == (2, 0, 0)

    review_order2 = CodeReview(
        summary="Order 2",
        limitations="Limitation",
        findings=(finding_second, finding_both),
    )
    tp2, fp2, fn2 = v2._match_case(mock_case, review_order2)
    assert (tp2, fp2, fn2) == (2, 0, 0)


def test_strict_case_validation() -> None:
    valid_file = CaseFile(
        id="f1",
        path="valid.py",
        language="python",
        content="a = 1\n",
    )

    with pytest.raises(ValidationError):
        CaseFile(
            id="f1",
            path="valid.py",
            language="python",
            content="a = 1" + "\x00",
        )

    with pytest.raises(ValidationError):
        CaseFile(
            id="f1",
            path="valid.py",
            language="python",
            content="x" * 1025,
        )

    for bad_path in (
        "/abs.py",
        "\\abs.py",
        "sub\\file.py",
        "./rel.py",
        "../parent.py",
        "C:file.py",
    ):
        with pytest.raises(ValidationError):
            CaseFile(
                id="f1",
                path=bad_path,
                language="python",
                content="a = 1\n",
            )

    with pytest.raises(ValidationError):
        Case(
            id="non-contiguous",
            title="Non contiguous",
            language="python",
            kind="clean",
            files=(CaseFile(id="f2", path="test.py", language="python", content="a = 1\n"),),
            expected=(),
            description="Description text",
        )

    with pytest.raises(ValidationError):
        Case(
            id="line-out-of-range",
            title="Line out of range",
            language="python",
            kind="bug",
            files=(valid_file,),
            expected=(
                ExpectedFinding(
                    file_id="f1", start_line=1, end_line=10, categories=("correctness",)
                ),
            ),
            description="Description text",
        )

    with pytest.raises(ValidationError):
        Case(
            id="clean-with-expected",
            title="Clean with expected",
            language="python",
            kind="clean",
            files=(valid_file,),
            expected=(
                ExpectedFinding(
                    file_id="f1", start_line=1, end_line=1, categories=("correctness",)
                ),
            ),
            description="Description text",
        )


def test_eval_result_v2_usage_and_report_rejections() -> None:
    cid = cases_v2.CASES[0].id
    rev = _make_review_for_case(cases_v2.CASES[0])

    with pytest.raises(ValidationError):
        v2.CaseUsage(input_tokens=True, output_tokens=100)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        v2.CaseUsage(input_tokens=-1, output_tokens=100)
    with pytest.raises(ValidationError):
        v2.CaseUsage(input_tokens=100, output_tokens=2801)

    result_id = uuid4()
    now = utc_now()
    fixture_hash = v2.fixture_digest()
    score_1 = v2.score_answers({cid: rev})

    with pytest.raises(ValidationError, match="usage keys must match answered case ids"):
        v2.EvalResultV2(
            id=result_id,
            completed_at=now,
            status="failed",
            fixture_sha256=fixture_hash,
            answers={cid: rev},
            usage={},  # missing key
            report=score_1,
        )

    with pytest.raises(ValidationError, match="completed requires nine answers"):
        v2.EvalResultV2(
            id=result_id,
            completed_at=now,
            status="completed",
            fixture_sha256=fixture_hash,
            answers={cid: rev},
            usage={cid: v2.CaseUsage(input_tokens=10, output_tokens=10)},
            report=score_1,
        )

    tampered_report = dict(score_1)
    tampered_report["tp"] = True
    with pytest.raises(ValidationError, match="report mismatch"):
        v2.EvalResultV2(
            id=result_id,
            completed_at=now,
            status="failed",
            fixture_sha256=fixture_hash,
            answers={cid: rev},
            usage={cid: v2.CaseUsage(input_tokens=10, output_tokens=10)},
            report=tampered_report,
        )

    all_revs = {c.id: _make_review_for_case(c) for c in cases_v2.CASES}
    all_usage = {c.id: v2.CaseUsage(input_tokens=10, output_tokens=10) for c in cases_v2.CASES}
    valid_report = v2.score_answers(all_revs)
    valid_res = v2.EvalResultV2(
        id=result_id,
        completed_at=now,
        status="completed",
        fixture_sha256=fixture_hash,
        answers=all_revs,
        usage=all_usage,
        report=valid_report,
    )
    assert valid_res.status == "completed"


def test_eval_repository_mixed_profiles_and_validations(tmp_path: Path) -> None:
    projects = ProjectRepository(tmp_path / "projects")
    repo = EvalRepository(tmp_path / "evals", projects)

    claim_v1 = repo.create()
    assert claim_v1.suite_profile == "synthetic-line-overlap-v1"
    claim_v2 = repo.create(suite_profile="synthetic-file-overlap-v2")
    assert claim_v2.suite_profile == "synthetic-file-overlap-v2"

    c1_read = repo.read_claim(claim_v1.id)
    assert c1_read is not None
    assert c1_read.suite_profile == "synthetic-line-overlap-v1"

    v1_claim_path = repo.store.path(claim_v1.id, "claim")
    raw_v1 = json.loads(v1_claim_path.read_text(encoding="utf-8"))
    raw_v1.pop("suite_profile", None)
    v1_claim_path.write_text(json.dumps(raw_v1), encoding="utf-8")

    legacy_claim = repo.read_claim(claim_v1.id)
    assert legacy_claim is not None
    assert legacy_claim.suite_profile == "synthetic-line-overlap-v1"

    all_revs = {c.id: _make_review_for_case(c) for c in cases_v2.CASES}
    all_usage = {c.id: v2.CaseUsage(input_tokens=10, output_tokens=10) for c in cases_v2.CASES}
    res_v2 = v2.EvalResultV2(
        id=claim_v2.id,
        completed_at=utc_now(),
        status="completed",
        fixture_sha256=claim_v2.fixture_sha256,
        answers=all_revs,
        usage=all_usage,
        report=v2.score_answers(all_revs),
    )
    repo.save(res_v2)

    loaded_v2 = repo.result(claim_v2.id)
    assert isinstance(loaded_v2, v2.EvalResultV2)
    assert loaded_v2.id == claim_v2.id

    hist = repo.history()
    assert len(hist) >= 2
    hist_ids = [entry["claim"]["id"] for entry in hist if entry.get("claim")]
    assert str(claim_v1.id) in hist_ids
    assert str(claim_v2.id) in hist_ids

    st_v2 = repo.state(claim_v2.id)
    assert st_v2["status"] == "completed"
    assert st_v2["result"] is not None

    mismatched_v1_on_v2 = v1.EvalResult(
        id=claim_v2.id,
        completed_at=utc_now(),
        status="failed",
        fixture_sha256=v1.fixture_digest(),
        answers={},
        report=v1.score_answers({}),
    )
    with pytest.raises(ValueError, match="Ответ оценки повреждён"):
        repo.save(mismatched_v1_on_v2)

    wrong_time_res = v2.EvalResultV2(
        id=claim_v2.id,
        completed_at=datetime(2020, 1, 1, 0, 0, tzinfo=UTC),
        status="completed",
        fixture_sha256=claim_v2.fixture_sha256,
        answers=all_revs,
        usage=all_usage,
        report=v2.score_answers(all_revs),
    )
    with pytest.raises(ValueError, match="Ответ оценки повреждён"):
        repo.save(wrong_time_res)

    unclaimed_id = uuid4()
    unclaimed_res = v2.EvalResultV2(
        id=unclaimed_id,
        completed_at=utc_now(),
        status="completed",
        fixture_sha256=v2.fixture_digest(),
        answers=all_revs,
        usage=all_usage,
        report=v2.score_answers(all_revs),
    )
    with pytest.raises(ValueError, match="Оценка не запрошена"):
        repo.save(unclaimed_res)

    claim_v2_new = repo.create(suite_profile="synthetic-file-overlap-v2")
    forged_result = v2.EvalResultV2.model_construct(
        id=claim_v2_new.id,
        completed_at=utc_now(),
        status="completed",
        fixture_sha256=claim_v2_new.fixture_sha256,
        answers={},
        usage={},
        report={"forged": True},
    )
    with pytest.raises(ValueError):
        repo.save(forged_result)

    corrupted_claim_path = repo.store.path(claim_v2.id, "claim")
    claim_json = json.loads(corrupted_claim_path.read_text(encoding="utf-8"))
    claim_json["fixture_sha256"] = "a" * 64
    corrupted_claim_path.write_text(json.dumps(claim_json), encoding="utf-8")
    with pytest.raises(ValueError, match="Набор оценки изменился после запроса"):
        repo.read_claim(claim_v2.id)


def test_expected_finding_rejects_span_21() -> None:
    import pytest

    from aegisops.project_code.evaluation_cases_v2 import ExpectedFinding

    with pytest.raises(ValueError):
        ExpectedFinding(
            file_id="f1",
            start_line=1,
            end_line=21,
            categories=("correctness",),
        )
    ExpectedFinding(
        file_id="f1",
        start_line=1,
        end_line=20,
        categories=("correctness",),
    )
