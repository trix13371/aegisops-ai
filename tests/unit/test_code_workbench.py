"""Unit tests for UI17 workbench, lab, context, reproduction, and eval."""

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from aegisops.domain.common import utc_now
from aegisops.project_code.context import build_map, suggest_context
from aegisops.project_code.evaluation import case_preview, score_answers
from aegisops.project_code.evaluation_repository import EvalRepository
from aegisops.project_code.lab import (
    LabClaim,
    LabRepository,
    LabResult,
    Proposal,
    VerificationResult,
    VerifyClaim,
    _result_digest,
    classify_runs,
    patched_preview,
    proposal_diff,
    proposal_payload,
    validate_proposal,
    verification_snapshots,
)
from aegisops.project_code.models import (
    CodeClaim,
    CodeFile,
    CodeFinding,
    CodePreview,
    CodeResult,
    CodeReview,
    preview_digest,
)
from aegisops.project_code.repository import CodeRepository
from aegisops.project_code.reproduction import validate_verification
from aegisops.project_runner.models import (
    Environment,
    Profile,
    RunResult,
    SnapshotInfo,
    ToolCheck,
    profile_for,
    tools_for,
)
from aegisops.projects.models import ProjectError
from aegisops.projects.repository import ProjectRepository

VALID_SHA_A = "a" * 64
VALID_SHA_B = "b" * 64
VALID_IMG = f"sha256:{VALID_SHA_A}"
VALID_IMG_ALT = f"sha256:{VALID_SHA_B}"


def _make_preview(pid, fid, path, lang, content):
    cf = CodeFile(
        id=fid,
        path=path,
        language=lang,
        content=content,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        lines=max(1, len(content.splitlines())),
    )
    return CodePreview(id=uuid4(), project_id=pid, created_at=utc_now(), files=(cf,))


def _make_review(fid, ev, start=1, end=1):
    f = CodeFinding(
        file_id=fid,
        start_line=start,
        end_line=end,
        severity="info",
        category="reliability",
        title="Test finding",
        evidence=ev,
        explanation="Detail note",
        recommendation="Action step",
    )
    return CodeReview(summary="Review summary", findings=(f,), limitations="Scope note")


def _make_run(
    run_id,
    proj_id,
    env: Environment,
    snap_sha: str,
    test_status: str,
    test_exit: int | None,
    completed_at,
    output_truncated: bool = False,
    img: str = VALID_IMG,
) -> RunResult:
    prof: Profile = profile_for(env)
    tools = tools_for(prof)
    chk = (
        ToolCheck(
            tool=tools[0],
            status="passed",
            exit_code=0,
            output="ok",
            output_truncated=False,
        ),
        ToolCheck(
            tool=tools[1],
            status=test_status,
            exit_code=test_exit,
            output="test result",
            output_truncated=output_truncated,
        ),
    )
    overall_status = "passed" if test_status == "passed" else "issues"
    return RunResult(
        id=run_id,
        project_id=proj_id,
        environment=env,
        profile=prof,
        completed_at=completed_at,
        status=overall_status,
        reason="checks_completed",
        snapshot=SnapshotInfo(
            sha256=snap_sha,
            files=2,
            python_files=2 if prof == "python-checks-v1" else 0,
            javascript_files=2 if prof != "python-checks-v1" else 0,
            bytes=50,
            skipped=0,
        ),
        image_id=img,
        checks=chk,
        container_removed=True,
        execution_scope="container",
    )


def test_context_map_and_suggestions(tmp_path: Path) -> None:
    p_dir = tmp_path / "proj"
    p_dir.mkdir()
    (p_dir / "a.py").write_text("import b\nx = 1\n")
    (p_dir / "b.py").write_text("y = 2\n")
    (p_dir / "app.js").write_text("import { sub } from './sub.js';\n")
    (p_dir / "sub.js").write_text("export const sub = 1;\n")
    (p_dir / "bad.py").write_text("def broken(:\n")

    repo = ProjectRepository(tmp_path / "projects")
    proj = repo.add("sample", str(p_dir))

    m = build_map(proj)
    assert m["analyzed_files"] >= 4
    nodes = {n["path"]: n for n in m["files"]}
    assert "b.py" in nodes["a.py"]["related"]
    assert "sub.js" in nodes["app.js"]["related"]
    assert not nodes["bad.py"]["analyzed"]

    res = suggest_context(proj, ["b.py"])
    sug_paths = [s["path"] for s in res["suggestions"]]
    assert "a.py" in sug_paths
    assert "b.py" not in sug_paths

    with pytest.raises(ProjectError):
        suggest_context(proj, ["b.py"] * 6)


def test_proposal_validation_payload_and_diff() -> None:
    orig = "def run():\n    return 0\n"
    prev = _make_preview(uuid4(), "f1", "src/run.py", "python", orig)
    rev = _make_review("f1", "return 0", start=2, end=2)

    payload = proposal_payload(prev, rev, 0)
    assert payload is not None
    assert payload["profile"] == "project-code-fix-v1"
    assert payload["files"][0]["path"] == "src/run.py"

    valid_p = Proposal(
        file_id="f1",
        replacement="def run():\n    return 1\n",
        test_language="python",
        test_code="def test_run():\n    pass\n",
        explanation="Update return",
    )
    checked = validate_proposal(prev, valid_p, 0, rev)
    assert checked.replacement != orig

    patched = patched_preview(prev, valid_p)
    assert patched.files[0].content == valid_p.replacement
    assert patched.files[0].sha256 != prev.files[0].sha256
    assert prev.files[0].content == orig

    diff = proposal_diff(prev, valid_p)
    assert "--- a/src/run.py" in diff
    assert "+++ b/src/run.py" in diff
    assert "-def run():" not in diff
    assert "-    return 0" in diff
    assert "+    return 1" in diff

    bad_syntax = Proposal(
        file_id="f1",
        replacement="def syntax broken",
        test_language="python",
        test_code="def test():\n    pass\n",
        explanation="Note",
    )
    with pytest.raises(ValueError, match="syntax"):
        validate_proposal(prev, bad_syntax, 0, rev)


def test_proposal_validation_rejects_wrong_target_or_secrets() -> None:
    prev = _make_preview(uuid4(), "f1", "src/app.py", "python", "x = 1\n")
    rev = _make_review("f1", "x = 1")

    wrong_file = Proposal(
        file_id="f2",
        replacement="x = 2\n",
        test_language="python",
        test_code="def test():\n    pass\n",
        explanation="Wrong file",
    )
    with pytest.raises(ValueError, match="proposal file does not match finding"):
        validate_proposal(prev, wrong_file, 0, rev)

    with pytest.raises(ValueError):
        Proposal(
            file_id="f1",
            replacement=f"x = 'sk-{'A' * 32}'\n",
            test_language="python",
            test_code="def test():\n    pass\n",
            explanation="Secret leak",
        )


def test_lab_repository_lifecycle_and_single_attempt(tmp_path: Path) -> None:
    p_dir = tmp_path / "proj"
    p_dir.mkdir()
    (p_dir / "run.py").write_text("def run():\n    return 0\n")
    p_repo = ProjectRepository(tmp_path / "projects")
    proj = p_repo.add("lab_proj", str(p_dir))

    c_repo = CodeRepository(tmp_path / "code", p_repo)
    prev = c_repo.create(proj.id, ["run.py"])

    c_now = utc_now()
    claim = CodeClaim(preview_id=prev.id, preview_sha256=preview_digest(prev), created_at=c_now)
    c_repo.write(prev.id, "claim", claim.model_dump_json())

    rev = _make_review("f1", "return 0", start=2, end=2)
    c_res = CodeResult(
        preview_id=prev.id,
        preview_sha256=preview_digest(prev),
        completed_at=utc_now(),
        status="completed",
        response=rev,
    )
    c_repo.write(prev.id, "result", c_res.model_dump_json())

    lab = LabRepository(tmp_path / "lab", c_repo)
    l_claim = lab.claim(prev.id, 0)
    assert isinstance(l_claim, LabClaim)
    assert l_claim.finding_index == 0
    assert lab.read_claim(prev.id) is not None

    with pytest.raises(ProjectError, match="уже запрошено"):
        lab.claim(prev.id, 0)

    prop = Proposal(
        file_id="f1",
        replacement="def run():\n    return 1\n",
        test_language="python",
        test_code="def test_r():\n    pass\n",
        explanation="Fix issue",
    )
    lab_res = LabResult(
        preview_id=prev.id,
        preview_sha256=preview_digest(prev),
        review_sha256=l_claim.review_sha256,
        completed_at=utc_now(),
        status="completed",
        proposal=prop,
        input_tokens=10,
        output_tokens=20,
    )
    lab._write(prev.id, "result", lab_res.model_dump_json())
    loaded = lab.result(prev.id)
    assert loaded is not None
    assert loaded.status == "completed"

    v_claim = lab.verify_claim(prev.id, "minimal")
    assert isinstance(v_claim, VerifyClaim)
    assert v_claim.environment == "minimal"

    with pytest.raises(ProjectError, match="Среда не подходит"):
        lab.verify_claim(prev.id, "node-js-v1")
    with pytest.raises(ProjectError, match="уже запрошена"):
        lab.verify_claim(prev.id, "minimal")


def test_reproduction_snapshots_and_classify_runs() -> None:
    pid = uuid4()
    prev = _make_preview(pid, "f1", "app.py", "python", "def x():\n    return 42\n")
    prop = Proposal(
        file_id="f1",
        replacement="def x():\n    return 99\n",
        test_language="python",
        test_code="def test_x():\n    pass\n",
        explanation="Patch val",
    )
    b_snap, a_snap = verification_snapshots(prev, prop, "minimal")
    assert b_snap.info.files == 2
    assert a_snap.info.files == 2
    assert b_snap.info.sha256 != a_snap.info.sha256

    c_time = utc_now()
    run_b = _make_run(uuid4(), pid, "minimal", b_snap.info.sha256, "failed", 1, c_time)
    run_a = _make_run(uuid4(), pid, "minimal", a_snap.info.sha256, "passed", 0, c_time)
    assert classify_runs(run_b, run_a, b_snap.info.sha256, a_snap.info.sha256) == "reproduced"

    run_a_fail = _make_run(uuid4(), pid, "minimal", a_snap.info.sha256, "failed", 1, c_time)
    assert (
        classify_runs(run_b, run_a_fail, b_snap.info.sha256, a_snap.info.sha256) == "not_reproduced"
    )

    run_b_diff_img = _make_run(
        uuid4(), pid, "minimal", b_snap.info.sha256, "failed", 1, c_time, img=VALID_IMG_ALT
    )
    assert (
        classify_runs(run_b_diff_img, run_a, b_snap.info.sha256, a_snap.info.sha256)
        == "inconclusive"
    )

    run_b_trunc = _make_run(
        uuid4(), pid, "minimal", b_snap.info.sha256, "failed", 1, c_time, output_truncated=True
    )
    assert (
        classify_runs(run_b_trunc, run_a, b_snap.info.sha256, a_snap.info.sha256) == "inconclusive"
    )


def test_validate_verification_rejects_forgery() -> None:
    pid = uuid4()
    prev = _make_preview(pid, "f1", "calc.py", "python", "def c():\n    return 1\n")
    prop = Proposal(
        file_id="f1",
        replacement="def c():\n    return 2\n",
        test_language="python",
        test_code="def test_c():\n    pass\n",
        explanation="Incr",
    )
    b_snap, a_snap = verification_snapshots(prev, prop, "minimal")
    now = utc_now()
    lab_res = LabResult(
        preview_id=prev.id,
        preview_sha256=preview_digest(prev),
        review_sha256="c" * 64,
        completed_at=now,
        status="completed",
        proposal=prop,
    )
    prop_sha = _result_digest(lab_res)
    v_claim = VerifyClaim(
        preview_id=prev.id,
        proposal_sha256=prop_sha,
        environment="minimal",
        created_at=now + timedelta(seconds=1),
    )
    run_b = _make_run(
        uuid4(), pid, "minimal", b_snap.info.sha256, "failed", 1, now + timedelta(seconds=2)
    )
    run_a = _make_run(
        uuid4(), pid, "minimal", a_snap.info.sha256, "passed", 0, now + timedelta(seconds=3)
    )
    res = VerificationResult(
        preview_id=prev.id,
        proposal_sha256=prop_sha,
        completed_at=now + timedelta(seconds=5),
        status="reproduced",
        before=run_b,
        after=run_a,
    )
    validate_verification(prev, prop, v_claim, res)

    forged_res = res.model_copy(update={"status": "not_reproduced"})
    with pytest.raises(ValueError, match="verification status mismatch"):
        validate_verification(prev, prop, v_claim, forged_res)


def test_evaluation_scoring_and_repository(tmp_path: Path) -> None:
    c_prev = case_preview("off_by_one", uuid4())
    finding_real = CodeFinding(
        file_id=c_prev.files[0].id,
        start_line=3,
        end_line=4,
        severity="error",
        category="correctness",
        title="Loop bound",
        evidence="total += values[index]",
        explanation="Past end",
        recommendation="Use standard range",
    )
    hit_rev = CodeReview(summary="Found bug", findings=(finding_real,), limitations="None")
    clean_rev = CodeReview(summary="Looks clean", findings=(), limitations="None")

    full_answers = {
        "off_by_one": hit_rev,
        "clean_add": clean_rev,
        "clean_injection": clean_rev,
    }
    score = score_answers(full_answers)
    assert score["complete"] is True
    assert score["overall_precision"] == 1.0
    assert score["overall_recall"] == 1.0

    partial = score_answers({"off_by_one": hit_rev})
    assert partial["complete"] is False
    assert partial["overall_precision"] is None

    with pytest.raises(ValueError, match="unknown case id"):
        score_answers({"ghost_case": clean_rev})

    p_repo = ProjectRepository(tmp_path / "projects")
    eval_repo = EvalRepository(tmp_path / "eval", p_repo)
    claim = eval_repo.create()
    assert claim.id is not None
    assert eval_repo.read_claim(claim.id) is not None
    assert eval_repo.state(claim.id)["status"] == "pending"


def test_lab_repository_corrupted_state_rejected(tmp_path: Path) -> None:
    p_dir = tmp_path / "proj"
    p_dir.mkdir()
    (p_dir / "run.py").write_text("x = 10\n")
    p_repo = ProjectRepository(tmp_path / "projects")
    proj = p_repo.add("proj_corrupt", str(p_dir))

    c_repo = CodeRepository(tmp_path / "code", p_repo)
    prev = c_repo.create(proj.id, ["run.py"])
    c_now = utc_now()
    claim = CodeClaim(preview_id=prev.id, preview_sha256=preview_digest(prev), created_at=c_now)
    c_repo.write(prev.id, "claim", claim.model_dump_json())
    rev = _make_review("f1", "x = 10")
    c_res = CodeResult(
        preview_id=prev.id,
        preview_sha256=preview_digest(prev),
        completed_at=c_now + timedelta(seconds=1),
        status="completed",
        response=rev,
    )
    c_repo.write(prev.id, "result", c_res.model_dump_json())

    lab = LabRepository(tmp_path / "lab", c_repo)
    lab._write(prev.id, "started", "{}")
    with pytest.raises(ProjectError, match="История исправления повреждена"):
        lab.claim(prev.id, 0)

    with pytest.raises(ProjectError, match="История исправления повреждена"):
        lab.state(prev.id)


def test_eval_repository_read_and_save(tmp_path: Path) -> None:
    from aegisops.project_code.evaluation import EvalResult, fixture_digest

    p_repo = ProjectRepository(tmp_path / "projects")
    eval_repo = EvalRepository(tmp_path / "eval", p_repo)
    claim = eval_repo.create()

    c_prev = case_preview("off_by_one", uuid4())
    finding = CodeFinding(
        file_id=c_prev.files[0].id,
        start_line=3,
        end_line=4,
        severity="error",
        category="correctness",
        title="Loop bound",
        evidence="total += values[index]",
        explanation="Past end",
        recommendation="Use standard range",
    )
    hit_rev = CodeReview(summary="Found bug", findings=(finding,), limitations="None")
    clean_rev = CodeReview(summary="Looks clean", findings=(), limitations="None")
    answers = {
        "off_by_one": hit_rev,
        "clean_add": clean_rev,
        "clean_injection": clean_rev,
    }
    rep = score_answers(answers)

    res = EvalResult(
        id=claim.id,
        completed_at=claim.created_at + timedelta(seconds=1),
        status="completed",
        fixture_sha256=fixture_digest(),
        answers=answers,
        report=rep,
    )
    eval_repo.save(res)
    saved = eval_repo.result(claim.id)
    assert saved is not None
    assert saved.status == "completed"
    assert saved.report["complete"] is True
    st = eval_repo.state(claim.id)
    assert st["status"] == "completed"


def test_eval_repository_rejects_corrupted_artifact(tmp_path: Path) -> None:
    p_repo = ProjectRepository(tmp_path / "projects")
    eval_repo = EvalRepository(tmp_path / "eval", p_repo)
    claim = eval_repo.create()

    bad_id = uuid4()
    payload = {
        "id": str(bad_id),
        "completed_at": (claim.created_at + timedelta(seconds=1)).isoformat(),
        "status": "failed",
        "fixture_sha256": "f" * 64,
        "answers": {},
        "report": score_answers({}),
    }
    eval_repo._write(claim.id, "result", json.dumps(payload))
    with pytest.raises(ValueError, match="Ответ оценки повреждён"):
        eval_repo.result(claim.id)


def test_proposal_diff_no_newline_variants() -> None:
    orig_no_nl = "def run():\n    return 0"
    prev = _make_preview(uuid4(), "f1", "src/run.py", "python", orig_no_nl)
    orig_content = prev.files[0].content

    p_no_nl = Proposal(
        file_id="f1",
        replacement="def run():\n    return 1",
        test_language="python",
        test_code="def test():\n    pass\n",
        explanation="noNL",
    )
    diff_no_nl = proposal_diff(prev, p_no_nl)
    assert "-    return 0\n\\ No newline at end of file\n" in diff_no_nl
    assert "+    return 1\n\\ No newline at end of file\n" in diff_no_nl

    p_added_nl = Proposal(
        file_id="f1",
        replacement="def run():\n    return 1\n",
        test_language="python",
        test_code="def test():\n    pass\n",
        explanation="addedNL",
    )
    diff_added_nl = proposal_diff(prev, p_added_nl)
    assert "-    return 0\n\\ No newline at end of file\n" in diff_added_nl
    assert "+    return 1\n" in diff_added_nl
    assert diff_added_nl.endswith("+    return 1\n")

    prev_nl = _make_preview(uuid4(), "f1", "src/run.py", "python", "def run():\n    return 0\n")
    diff_removed_nl = proposal_diff(prev_nl, p_no_nl)
    assert "-    return 0\n" in diff_removed_nl
    assert "+    return 1\n\\ No newline at end of file\n" in diff_removed_nl
    assert prev.files[0].content == orig_content
