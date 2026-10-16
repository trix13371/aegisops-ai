from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from aegisops.domain.common import utc_now
from aegisops.project_checks.models import build_result
from aegisops.project_checks.repository import FullCheckRepository
from aegisops.project_runner.models import RunClaim, RunResult, SnapshotInfo, ToolCheck
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.models import Finding, ProjectError, ProjectReport
from aegisops.projects.repository import ProjectRepository


def _make_claim(pid, env="minimal", created=None):
    return RunClaim(id=uuid4(), project_id=pid, created_at=created or utc_now(), environment=env)


def _make_report(pid, t0, t1, findings=(), partial=False):
    status = "partial" if partial else ("issues" if findings else "passed")
    return ProjectReport(
        id=uuid4(),
        project_id=pid,
        started_at=t0,
        completed_at=t1,
        status=status,
        files_seen=1,
        files_checked=1,
        skipped=0,
        stacks=("py",),
        findings=findings,
        partial=partial,
    )


def _make_run(claim, t_run, status="passed", truncated=False, no_tests=False, rm=True, img=True):
    tool_st = "no_tests" if no_tests else ("failed" if status == "issues" else "passed")
    ec = 5 if no_tests else (1 if status == "issues" else 0)
    c1 = ToolCheck(tool="ruff", status="passed", exit_code=0, output="ok", output_truncated=False)
    c2 = ToolCheck(
        tool="pytest", status=tool_st, exit_code=ec, output="out", output_truncated=truncated
    )
    return RunResult(
        id=claim.id,
        project_id=claim.project_id,
        environment=claim.environment,
        profile=claim.profile,
        completed_at=t_run,
        status=status,
        reason="checks_completed",
        image_id="sha256:" + "a" * 64 if img else None,
        checks=(c1, c2),
        container_removed=rm,
        execution_scope="container",
        snapshot=SnapshotInfo(sha256="b" * 64, files=1, python_files=1, bytes=10, skipped=0),
    )


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize(
    ("rep_findings", "rep_part", "run_st", "trunc", "no_tests", "expected"),
    [
        (False, False, "passed", False, False, "passed"),
        (True, False, "passed", False, False, "issues"),
        (False, False, "issues", False, False, "issues"),
        (False, True, "passed", False, False, "incomplete"),
        (False, False, "issues", True, False, "incomplete"),
        (False, False, "issues", False, True, "issues"),
    ],
)
def test_build_result_matrix(rep_findings, rep_part, run_st, trunc, no_tests, expected):
    pid = uuid4()
    t0 = utc_now()
    claim = _make_claim(pid, created=t0)
    f = (
        (Finding(severity="error", code="syntax", file="a.py", message="err"),)
        if rep_findings
        else ()
    )
    report = _make_report(pid, t0 + timedelta(seconds=1), t0 + timedelta(seconds=2), f, rep_part)
    run = _make_run(claim, t0 + timedelta(seconds=3), run_st, trunc, no_tests)
    res = build_result(claim, report, run)
    assert res.status == expected
    assert res.id == claim.id and res.static_report_id == report.id


@pytest.mark.unit
@pytest.mark.safety
@pytest.mark.parametrize("bad_type", ["time_reversal", "cross_project", "none_report", "wrong_env"])
def test_build_result_validation(bad_type):
    pid = uuid4()
    t0 = utc_now()
    claim = _make_claim(pid, created=t0)
    rep = _make_report(pid, t0 + timedelta(seconds=1), t0 + timedelta(seconds=2))
    run = _make_run(claim, t0 + timedelta(seconds=3))
    if bad_type == "time_reversal":
        rep = _make_report(pid, t0 + timedelta(seconds=4), t0 + timedelta(seconds=5))
        with pytest.raises(ProjectError):
            build_result(claim, rep, run)
    elif bad_type == "cross_project":
        bad_rep = _make_report(uuid4(), t0 + timedelta(seconds=1), t0 + timedelta(seconds=2))
        with pytest.raises(ProjectError):
            build_result(claim, bad_rep, run)
    elif bad_type == "none_report":
        assert build_result(claim, None, run).status == "incomplete"
    elif bad_type == "wrong_env":
        bad_run = run.model_copy(
            update={"environment": "node-js-v1", "profile": "javascript-checks-v1"}
        )
        with pytest.raises(ProjectError):
            build_result(claim, rep, bad_run)


@pytest.mark.unit
@pytest.mark.safety
def test_build_result_incomplete_conditions():
    pid = uuid4()
    t0 = utc_now()
    claim = _make_claim(pid, created=t0)
    rep = _make_report(pid, t0 + timedelta(seconds=1), t0 + timedelta(seconds=2))

    # unavailable RunResult with captured snapshot and report -> incomplete
    run_unavail = RunResult(
        id=claim.id,
        project_id=claim.project_id,
        environment=claim.environment,
        profile=claim.profile,
        completed_at=t0 + timedelta(seconds=3),
        status="error",
        reason="runner_unavailable",
        snapshot=SnapshotInfo(sha256="b" * 64, files=1, python_files=1, bytes=10, skipped=0),
        container_removed=True,
        execution_scope="not_started",
    )
    res_unavail = build_result(claim, rep, run_unavail)
    assert res_unavail.status == "incomplete"

    # cleanup unknown run status error container_removed=False -> incomplete
    run_cleanup_fail = RunResult(
        id=claim.id,
        project_id=claim.project_id,
        environment=claim.environment,
        profile=claim.profile,
        completed_at=t0 + timedelta(seconds=3),
        status="error",
        reason="uncertain",
        image_id="sha256:" + "a" * 64,
        checks=(
            ToolCheck(
                tool="ruff", status="passed", exit_code=0, output="ok", output_truncated=False
            ),
            ToolCheck(
                tool="pytest", status="error", exit_code=None, output="err", output_truncated=False
            ),
        ),
        container_removed=False,
        execution_scope="container",
        snapshot=SnapshotInfo(sha256="b" * 64, files=1, python_files=1, bytes=10, skipped=0),
    )
    res_cleanup = build_result(claim, rep, run_cleanup_fail)
    assert res_cleanup.status == "incomplete"


@pytest.mark.unit
@pytest.mark.safety
def test_repository_lifecycle_restart_and_tampering(tmp_path: Path):
    p_dir, r_dir, c_dir = tmp_path / "proj", tmp_path / "run", tmp_path / "chk"
    proj_repo = ProjectRepository(p_dir)
    run_repo = RunRepository(r_dir)
    chk_repo = FullCheckRepository(c_dir, proj_repo, run_repo)
    proj_path = tmp_path / "code"
    proj_path.mkdir()
    (proj_path / "test.py").write_text("x = 1")
    project = proj_repo.add("Sample", str(proj_path))
    claim = chk_repo.create(project.id, "minimal")
    t_rep0, t_rep1 = (
        claim.created_at + timedelta(seconds=1),
        claim.created_at + timedelta(seconds=2),
    )
    rep = _make_report(project.id, t_rep0, t_rep1)
    proj_repo.save_report(rep)
    run = _make_run(claim, claim.created_at + timedelta(seconds=3))
    run_repo.save(run)
    res = build_result(claim, rep, run)
    chk_repo.save(res)
    assert chk_repo.result(claim.id).status == "passed"
    with pytest.raises(ProjectError):
        chk_repo.save(res)
    chk_repo_restarted = FullCheckRepository(c_dir, proj_repo, run_repo)
    assert chk_repo_restarted.result(claim.id).id == claim.id
    run_file = r_dir / str(claim.id) / "result.json"
    original_run_text = run.model_dump_json()
    run_file.write_text(run_file.read_text().replace('"output":"out"', '"output":"tampered"'))
    with pytest.raises(ProjectError):
        chk_repo.result(claim.id)

    # Restore run_file and verify result valid again
    run_file.write_text(original_run_text)
    assert chk_repo.result(claim.id).status == "passed"

    # Static report valid JSON tamper rejected
    rep_file = p_dir / "reports" / f"{rep.id}.json"
    tampered_rep_text = rep_file.read_text().replace('"stacks":["py"]', '"stacks":["js"]')
    ProjectReport.model_validate_json(tampered_rep_text)
    rep_file.write_text(tampered_rep_text)
    with pytest.raises(ProjectError):
        chk_repo_restarted.result(claim.id)


@pytest.mark.unit
@pytest.mark.safety
def test_repository_cross_project_and_deleted_and_history(tmp_path: Path):
    p_dir, r_dir, c_dir = tmp_path / "p", tmp_path / "r", tmp_path / "c"
    proj_repo, run_repo = ProjectRepository(p_dir), RunRepository(r_dir)
    chk_repo = FullCheckRepository(c_dir, proj_repo, run_repo)
    p1_dir, p2_dir = tmp_path / "p1", tmp_path / "p2"
    p1_dir.mkdir()
    p2_dir.mkdir()
    p1 = proj_repo.add("P1", str(p1_dir))
    p2 = proj_repo.add("P2", str(p2_dir))
    c1 = chk_repo.create(p1.id)
    rep_p2 = _make_report(
        p2.id, c1.created_at + timedelta(seconds=1), c1.created_at + timedelta(seconds=2)
    )
    proj_repo.save_report(rep_p2)
    run1 = _make_run(c1, c1.created_at + timedelta(seconds=3))
    run_repo.save(run1)
    res_spoofed = build_result(c1, None, run1).model_copy(
        update={"static_report_id": rep_p2.id, "static_report_sha256": "c" * 64}
    )
    with pytest.raises(ProjectError):
        chk_repo.save(res_spoofed)
    claims = [chk_repo.create(p1.id) for _ in range(21)]
    hist = chk_repo.history(p1.id)
    assert len(hist) == 20
    assert hist[0]["claim"]["id"] == str(claims[-1].id)
    proj_repo.remove(p1.id)
    with pytest.raises(ProjectError):
        chk_repo.claim(c1.id)
