from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from aegisops.domain.common import utc_now
from aegisops.project_checks.comparison import (
    CheckEvidence,
    compare_checks,
    compare_saved_checks,
    read_evidence,
)
from aegisops.project_checks.models import build_result
from aegisops.project_checks.repository import FullCheckRepository
from aegisops.project_runner.models import (
    Environment,
    RunClaim,
    RunResult,
    SnapshotInfo,
    ToolCheck,
    profile_for,
    tools_for,
)
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.models import Finding, ProjectError, ProjectReport
from aegisops.projects.repository import ProjectRepository

pytestmark = [pytest.mark.unit, pytest.mark.safety]

HEX64_A = "a" * 64
HEX64_B = "b" * 64
IMG_A = f"sha256:{HEX64_A}"
IMG_B = f"sha256:{HEX64_B}"


def _make_finding(
    file: str = "app.py", line: int = 10, code: str = "syntax", msg: str = "err"
) -> Finding:
    return Finding(severity="error", code=code, file=file, line=line, message=msg)


def make_evidence(
    project_id: UUID | None = None,
    env: Environment = "minimal",
    rules_ver: str = "local-static-v1",
    findings: tuple[Finding, ...] = (),
    partial: bool = False,
    include_static: bool = True,
    pending: bool = False,
    image_id: str = IMG_A,
    snap_sha: str = HEX64_A,
    tool_statuses: tuple[str, str] = ("passed", "passed"),
    tool_exits: tuple[int, int] = (0, 0),
    tool_truncated: bool = False,
    claim_id: UUID | None = None,
    time_shift_sec: int = 0,
    run_reason: str = "checks_completed",
    container_removed: bool = True,
) -> CheckEvidence:
    pid = project_id or uuid4()
    cid = claim_id or uuid4()
    prof = profile_for(env)
    t0 = utc_now() + timedelta(seconds=time_shift_sec)
    claim = RunClaim(id=cid, project_id=pid, created_at=t0, profile=prof, environment=env)
    if pending:
        return CheckEvidence(claim=claim, result=None, report=None, run=None)

    t1 = t0 + timedelta(seconds=1)
    t2 = t0 + timedelta(seconds=2)
    t3 = t0 + timedelta(seconds=3)

    report = None
    if include_static:
        status = "partial" if partial else "issues" if findings else "passed"
        report = ProjectReport(
            rules_version=rules_ver,
            id=uuid4(),
            project_id=pid,
            started_at=t1,
            completed_at=t2,
            status=status,
            files_seen=10,
            files_checked=0 if partial else 5,
            skipped=0,
            stacks=("python",),
            findings=findings,
            partial=partial,
        )

    t_names = tools_for(prof)
    actual_statuses = ("failed", tool_statuses[1]) if tool_truncated else tool_statuses
    actual_exits = (1, tool_exits[1]) if tool_truncated else tool_exits
    checks = (
        ToolCheck(
            tool=t_names[0],
            status=actual_statuses[0],
            exit_code=actual_exits[0],
            output="out0",
            output_truncated=tool_truncated,
        ),
        ToolCheck(
            tool=t_names[1],
            status=actual_statuses[1],
            exit_code=actual_exits[1],
            output="out1",
            output_truncated=False,
        ),
    )
    run_status = "issues" if any(s in {"failed", "no_tests"} for s in actual_statuses) else "passed"
    if run_reason != "checks_completed" or not container_removed:
        run_status = "error"
        checks = ()
    snap = SnapshotInfo(
        sha256=snap_sha,
        files=2,
        python_files=1 if prof == "python-checks-v1" else 0,
        javascript_files=1 if prof != "python-checks-v1" else 0,
        bytes=100,
        skipped=0,
    )
    run = RunResult(
        id=cid,
        project_id=pid,
        environment=env,
        profile=prof,
        completed_at=t3,
        status=run_status,
        reason=run_reason,
        snapshot=snap,
        image_id=image_id,
        checks=checks,
        container_removed=container_removed,
        execution_scope="container" if container_removed else "not_started",
    )
    result = build_result(claim, report, run)
    return CheckEvidence(claim=claim, result=result, report=report, run=run)


@pytest.mark.parametrize(
    "scenario,setup_fn,expected_comparable,expected_reasons,check_fn",
    [
        (
            "same_snapshot_false_diff_hash",
            lambda p: (make_evidence(p, snap_sha=HEX64_A), make_evidence(p, snap_sha=HEX64_B)),
            True,
            (),
            lambda c: c.same_snapshot is False,
        ),
        (
            "same_snapshot_true_same_hash",
            lambda p: (make_evidence(p, snap_sha=HEX64_A), make_evidence(p, snap_sha=HEX64_A)),
            True,
            (),
            lambda c: c.same_snapshot is True,
        ),
        (
            "findings_multiset_added_removed",
            lambda p: (
                make_evidence(p, findings=(_make_finding(line=1), _make_finding(line=2))),
                make_evidence(p, findings=(_make_finding(line=2), _make_finding(line=3))),
            ),
            True,
            (),
            lambda c: len(c.static.added) == 1 and len(c.static.removed) == 1,
        ),
        (
            "findings_line_shift_is_add_and_remove",
            lambda p: (
                make_evidence(p, findings=(_make_finding(line=10),)),
                make_evidence(p, findings=(_make_finding(line=20),)),
            ),
            True,
            (),
            lambda c: len(c.static.added) == 1 and len(c.static.removed) == 1,
        ),
        (
            "tool_transition_now_passed",
            lambda p: (
                make_evidence(p, tool_statuses=("failed", "passed"), tool_exits=(1, 0)),
                make_evidence(p, tool_statuses=("passed", "passed"), tool_exits=(0, 0)),
            ),
            True,
            (),
            lambda c: c.runner.transitions[0].change == "now_passed",
        ),
        (
            "tool_transition_regressed",
            lambda p: (
                make_evidence(p, tool_statuses=("passed", "passed"), tool_exits=(0, 0)),
                make_evidence(p, tool_statuses=("failed", "passed"), tool_exits=(1, 0)),
            ),
            True,
            (),
            lambda c: c.runner.transitions[0].change == "no_longer_passed",
        ),
        (
            "pending_result_none",
            lambda p: (make_evidence(p, pending=True), make_evidence(p)),
            False,
            ("unfinished",),
            lambda c: c.static is None and c.runner is None,
        ),
        (
            "missing_static_report",
            lambda p: (make_evidence(p, include_static=False), make_evidence(p)),
            False,
            ("missing_static", "incomplete"),
            lambda c: c.static is None,
        ),
        (
            "partial_static_yields_incomplete",
            lambda p: (make_evidence(p, partial=True), make_evidence(p)),
            False,
            ("incomplete",),
            lambda c: c.reasons == ("incomplete",),
        ),
        (
            "truncated_output_yields_incomplete",
            lambda p: (make_evidence(p, tool_truncated=True), make_evidence(p)),
            False,
            ("incomplete",),
            lambda c: c.reasons == ("incomplete",),
        ),
        (
            "unconfirmed_cleanup_yields_incomplete",
            lambda p: (
                make_evidence(p, run_reason="uncertain", container_removed=False),
                make_evidence(p),
            ),
            False,
            ("incomplete",),
            lambda c: c.reasons == ("incomplete",),
        ),
        (
            "rules_version_changed",
            lambda p: (
                make_evidence(p, rules_ver="local-static-v1"),
                make_evidence(p, rules_ver="local-static-v2"),
            ),
            False,
            ("rules_changed",),
            lambda c: c.reasons == ("rules_changed",),
        ),
        (
            "environment_and_profile_changed",
            lambda p: (make_evidence(p, env="minimal"), make_evidence(p, env="node-js-v1")),
            False,
            ("profile_changed", "environment_changed"),
            lambda c: c.reasons == ("profile_changed", "environment_changed"),
        ),
        (
            "image_changed",
            lambda p: (make_evidence(p, image_id=IMG_A), make_evidence(p, image_id=IMG_B)),
            False,
            ("image_changed",),
            lambda c: c.reasons == ("image_changed",),
        ),
        (
            "multiple_changes_fixed_reason_order",
            lambda p: (
                make_evidence(p, env="minimal", image_id=IMG_A, rules_ver="local-static-v1"),
                make_evidence(p, env="node-js-v1", image_id=IMG_B, rules_ver="local-static-v2"),
            ),
            False,
            ("profile_changed", "environment_changed", "image_changed", "rules_changed"),
            lambda c: (
                c.reasons
                == ("profile_changed", "environment_changed", "image_changed", "rules_changed")
            ),
        ),
        (
            "reverse_chronology_allowed",
            lambda p: (make_evidence(p, time_shift_sec=100), make_evidence(p, time_shift_sec=0)),
            True,
            (),
            lambda c: c.comparable is True and c.base.created_at > c.target.created_at,
        ),
    ],
)
def test_compare_checks_scenarios(
    scenario, setup_fn, expected_comparable, expected_reasons, check_fn
):
    p = uuid4()
    b, t = setup_fn(p)
    comp = compare_checks(b, t)
    assert comp.comparable is expected_comparable
    assert comp.reasons == expected_reasons
    assert check_fn(comp)


def test_compare_checks_rejections():
    p1, p2 = uuid4(), uuid4()
    ev1 = make_evidence(p1)
    ev1_dup = make_evidence(p1, claim_id=ev1.claim.id)
    with pytest.raises(ProjectError, match="Выберите две разные проверки"):
        compare_checks(ev1, ev1_dup)
    ev2 = make_evidence(p2)
    with pytest.raises(ProjectError, match="Выберите две проверки одного проекта"):
        compare_checks(ev1, ev2)


def test_check_evidence_digest_tamper_rejected():
    ev = make_evidence()
    tampered_run = ev.run.model_copy(update={"completed_at": utc_now() + timedelta(days=1)})
    with pytest.raises(ValueError, match="saved check does not match its sources"):
        CheckEvidence(claim=ev.claim, result=ev.result, report=ev.report, run=tampered_run)


def _setup_repos(
    tmp_path: Path,
) -> tuple[ProjectRepository, RunRepository, FullCheckRepository, UUID]:
    src = tmp_path / "proj_src"
    src.mkdir(parents=True, exist_ok=True)
    p_repo = ProjectRepository(tmp_path / "projects")
    r_repo = RunRepository(tmp_path / "runs")
    c_repo = FullCheckRepository(tmp_path / "checks", p_repo, r_repo)
    proj = p_repo.add("test-proj", str(src))
    return p_repo, r_repo, c_repo, proj.id


def _commit_check(
    p_repo: ProjectRepository, r_repo: RunRepository, c_repo: FullCheckRepository, pid: UUID
):
    claim = c_repo.create(pid, "minimal")
    prof = profile_for("minimal")
    t_names = tools_for(prof)
    checks = (
        ToolCheck(
            tool=t_names[0], status="passed", exit_code=0, output="ok", output_truncated=False
        ),
        ToolCheck(
            tool=t_names[1], status="passed", exit_code=0, output="ok", output_truncated=False
        ),
    )
    run = RunResult(
        id=claim.id,
        project_id=pid,
        environment="minimal",
        profile=prof,
        completed_at=claim.created_at + timedelta(seconds=2),
        status="passed",
        reason="checks_completed",
        snapshot=SnapshotInfo(sha256=HEX64_A, files=1, python_files=1, bytes=10, skipped=0),
        image_id=IMG_A,
        checks=checks,
        container_removed=True,
        execution_scope="container",
    )
    report = ProjectReport(
        rules_version="local-static-v1",
        id=uuid4(),
        project_id=pid,
        started_at=claim.created_at + timedelta(seconds=1),
        completed_at=claim.created_at + timedelta(seconds=2),
        status="passed",
        files_seen=1,
        files_checked=1,
        skipped=0,
        stacks=("python",),
        findings=(),
        partial=False,
    )
    p_repo.save_report(report)
    r_repo.save(run)
    res = build_result(claim, report, run)
    c_repo.save(res)
    return claim.id


def test_repository_lifecycle_and_compare_saved(tmp_path: Path):
    p_repo, r_repo, c_repo, pid = _setup_repos(tmp_path)
    c1 = _commit_check(p_repo, r_repo, c_repo, pid)
    c2 = _commit_check(p_repo, r_repo, c_repo, pid)

    # Source directory removal proves compare_saved_checks does not read project_root
    src = tmp_path / "proj_src"
    src.rmdir()

    comp = compare_saved_checks(c_repo, c1, c2)
    assert comp.comparable is True
    assert comp.base.id == c1
    assert comp.target.id == c2


def test_repository_tamper_and_invariants(tmp_path: Path):
    p_repo, r_repo, c_repo, pid = _setup_repos(tmp_path)
    cid = _commit_check(p_repo, r_repo, c_repo, pid)

    # Tamper report
    res = c_repo.result(cid)
    rep_file = p_repo.directory / "reports" / f"{res.static_report_id}.json"
    rep_data = p_repo.read_report(res.static_report_id).model_copy(update={"files_seen": 99})
    rep_file.write_text(rep_data.model_dump_json())
    with pytest.raises(ProjectError):
        read_evidence(c_repo, cid)

    # Tamper run
    p_repo2, r_repo2, c_repo2, pid2 = _setup_repos(tmp_path / "t2")
    cid2 = _commit_check(p_repo2, r_repo2, c_repo2, pid2)
    run_file = r_repo2.path(cid2, "result")
    run_data = r_repo2.result(cid2).model_copy(update={"image_id": IMG_B})
    run_file.write_text(run_data.model_dump_json())
    with pytest.raises(ProjectError):
        read_evidence(c_repo2, cid2)

    # Non-existent & deleted project
    with pytest.raises(ProjectError):
        read_evidence(c_repo, uuid4())
    p_repo.remove(pid)
    with pytest.raises(ProjectError):
        read_evidence(c_repo, cid)
