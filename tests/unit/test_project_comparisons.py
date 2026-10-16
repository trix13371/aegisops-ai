"""Compare observations without inferring code fixes from partial results or raw logs."""

from uuid import uuid4

import pytest

from aegisops.domain.common import utc_now
from aegisops.project_runner.comparison import compare_runs
from aegisops.project_runner.models import RunClaim, RunResult, SnapshotInfo, ToolCheck
from aegisops.projects.comparison import compare_reports
from aegisops.projects.models import Finding, ProjectError, ProjectReport

pytestmark = [pytest.mark.unit, pytest.mark.safety]


def report(project_id, findings=(), *, rules="local-static-v2", partial=False):
    now = utc_now()
    return ProjectReport(
        id=uuid4(),
        project_id=project_id,
        started_at=now,
        completed_at=now,
        status="partial" if partial else "issues" if findings else "passed",
        files_seen=1,
        files_checked=1,
        skipped=0,
        stacks=(),
        findings=findings,
        partial=partial,
        rules_version=rules,
    )


def finding(code="privileged", *, file="compose.yaml", location=None, line=None):
    return Finding(
        severity="warning",
        code=code,
        file=file,
        location=location,
        line=line,
        message="Локальное замечание",
    )


def test_static_multiset_reports_added_removed_and_unchanged_without_mutation():
    project = uuid4()
    common = finding()
    removed = finding("mutable_image")
    added = finding("docker_socket")
    base, target = report(project, (common, common, removed)), report(project, (common, added))
    before = base.model_dump_json(), target.model_dump_json()
    result = compare_reports(base, target)
    assert result.comparable and result.unchanged == 1
    assert result.added == (added,) and result.removed == (common, removed)
    assert result.base_total == 3 and result.target_total == 2
    assert (base.model_dump_json(), target.model_dump_json()) == before


@pytest.mark.parametrize("change", ["file", "location", "line", "code", "severity"])
def test_coordinates_and_code_define_observation_identity(change):
    project = uuid4()
    before = finding()
    changes = {
        "file": "renamed.yaml",
        "location": "document[2]",
        "line": 4,
        "code": "docker_socket",
        "severity": "error",
    }
    after = Finding.model_validate({**before.model_dump(), change: changes[change]})
    result = compare_reports(report(project, (before,)), report(project, (after,)))
    assert result.unchanged == 0 and result.added == (after,) and result.removed == (before,)


def test_message_or_recommendation_change_is_not_a_new_observation():
    project = uuid4()
    before = finding()
    after = Finding.model_validate(
        {**before.model_dump(), "message": "Другая формулировка", "recommendation": "Совет"}
    )
    result = compare_reports(report(project, (before,)), report(project, (after,)))
    assert result.unchanged == 1 and not result.added and not result.removed


@pytest.mark.parametrize("changed_rules,partial", [(True, False), (False, True), (True, True)])
def test_changed_rules_or_partial_scope_do_not_claim_findings_were_fixed(changed_rules, partial):
    project = uuid4()
    base = report(
        project, (finding(),), rules="local-static-v1" if changed_rules else "local-static-v2"
    )
    target = report(project, (), partial=partial)
    result = compare_reports(base, target)
    assert not result.comparable and result.unchanged is None
    assert not result.added and not result.removed
    assert result.reason == ("rules_changed" if changed_rules else "partial")


def test_static_empty_reports_and_wrong_project_or_same_identity():
    project = uuid4()
    base = report(project)
    result = compare_reports(base, report(project))
    assert result.comparable and result.unchanged == 0
    with pytest.raises(ProjectError):
        compare_reports(base, report(uuid4()))
    with pytest.raises(ProjectError):
        compare_reports(base, base)


def run(project, states=("passed", "passed"), *, image="1", snapshot="1", truncated=False):
    claim = RunClaim(id=uuid4(), project_id=project, created_at=utc_now())
    codes = {"passed": 0, "failed": 1, "error": 2, "timeout": None, "no_tests": 5}
    checks = tuple(
        ToolCheck(
            tool=tool,
            status=status,
            exit_code=codes[status],
            output="PRIVATE_LOG_CANARY",
            output_truncated=truncated,
        )
        for tool, status in zip(("ruff", "pytest"), states, strict=True)
    )
    status = (
        "timeout"
        if "timeout" in states
        else "error"
        if "error" in states
        else "issues"
        if any(s in {"failed", "no_tests"} for s in states)
        else "passed"
    )
    result = RunResult(
        id=claim.id,
        project_id=project,
        completed_at=utc_now(),
        status=status,
        reason="checks_completed",
        image_id="sha256:" + image * 64,
        snapshot=SnapshotInfo(sha256=snapshot * 64, files=1, python_files=1, bytes=10, skipped=0),
        checks=checks,
        container_removed=True,
        execution_scope="container",
    )
    return claim, result


@pytest.mark.parametrize(
    "before,after,change",
    [
        (("failed", "passed"), ("passed", "passed"), "now_passed"),
        (("passed", "passed"), ("failed", "passed"), "no_longer_passed"),
        (("error", "passed"), ("failed", "passed"), "changed"),
        (("passed", "passed"), ("passed", "passed"), "unchanged"),
    ],
)
def test_runner_status_transitions_never_include_or_parse_raw_logs(before, after, change):
    project = uuid4()
    base, left = run(project, before)
    target, right = run(project, after, snapshot="2")
    comparison = compare_runs(base, target, left, right)
    assert comparison.comparable and comparison.same_snapshot is False
    assert comparison.transitions[0].change == change
    assert comparison.transitions[1].change == "unchanged"
    assert "PRIVATE_LOG_CANARY" not in comparison.model_dump_json()


def test_runner_changed_image_or_missing_result_is_not_comparable():
    project = uuid4()
    base, left = run(project)
    target, right = run(project, image="2")
    assert compare_runs(base, target, left, right).reason == "image_changed"
    comparison = compare_runs(base, target, None, right)
    assert comparison.reason == "unfinished" and not comparison.transitions
    with pytest.raises(ProjectError):
        compare_runs(base, base, left, left)
    other, result = run(uuid4())
    with pytest.raises(ProjectError):
        compare_runs(base, other, left, result)
    with pytest.raises(ProjectError):
        compare_runs(base, target, right, left)


def test_dependency_environment_change_is_not_a_code_fix_even_with_same_image():
    project = uuid4()
    base, left = run(project)
    target, right = run(project)
    target = RunClaim.model_validate({**target.model_dump(), "environment": "python-data-v1"})
    right = RunResult.model_validate({**right.model_dump(), "environment": "python-data-v1"})
    comparison = compare_runs(base, target, left, right)
    assert comparison.reason == "environment_changed" and not comparison.transitions
    with pytest.raises(ProjectError):
        compare_runs(
            base,
            target,
            left,
            RunResult.model_validate({**right.model_dump(), "environment": "minimal"}),
        )


def test_runner_unconfirmed_scope_or_truncated_output_blocks_comparison():
    project = uuid4()
    base, left = run(project)
    target, right = run(project, ("error", "error"), truncated=True)
    assert compare_runs(base, target, left, right).reason == "unconfirmed"
    unavailable = RunResult(
        id=target.id,
        project_id=project,
        completed_at=utc_now(),
        status="error",
        reason="runner_unavailable",
    )
    assert compare_runs(base, target, left, unavailable).reason == "unconfirmed"
    target, right = run(project)
    assert compare_runs(base, target, left, right).same_snapshot is True
