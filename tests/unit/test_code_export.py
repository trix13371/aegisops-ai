import hashlib
import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from aegisops.domain.common import utc_now
from aegisops.project_code.export import proposal_zip, review_markdown
from aegisops.project_code.lab import (
    LabRepository,
    LabResult,
    Proposal,
    VerificationResult,
    proposal_diff,
    verification_snapshots,
)
from aegisops.project_code.models import (
    CodeFile,
    CodeFinding,
    CodePreview,
    CodeResult,
    CodeReview,
    preview_digest,
)
from aegisops.project_code.repository import CodeRepository
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

VALID_IMG_A = "sha256:" + "a" * 64


def _make_run(
    run_id: UUID,
    proj_id: UUID,
    env: Environment,
    snap_sha: str,
    test_status: str,
    test_exit: int | None,
    completed_at,
) -> RunResult:
    prof: Profile = profile_for(env)
    tools = tools_for(prof)
    chk = (
        ToolCheck(tool=tools[0], status="passed", exit_code=0, output="ok", output_truncated=False),
        ToolCheck(
            tool=tools[1],
            status=test_status,
            exit_code=test_exit,
            output="test result",
            output_truncated=False,
        ),
    )
    return RunResult(
        id=run_id,
        project_id=proj_id,
        environment=env,
        profile=prof,
        completed_at=completed_at,
        status="passed" if test_status == "passed" else "issues",
        reason="checks_completed",
        snapshot=SnapshotInfo(
            sha256=snap_sha, files=2, python_files=2, javascript_files=0, bytes=50, skipped=0
        ),
        image_id=VALID_IMG_A,
        checks=chk,
        container_removed=True,
        execution_scope="container",
    )


def _setup_repos(tmp_path: Path, path_name: str = "src/исходник.py"):
    p_dir = tmp_path / "empty_proj"
    p_dir.mkdir(parents=True, exist_ok=True)
    p_repo = ProjectRepository(tmp_path / "projects")
    proj = p_repo.add("demo", str(p_dir))
    c_repo = CodeRepository(tmp_path / "code", p_repo)
    content = "def calc():\n    return 0\n"
    cf = CodeFile(
        id="f1",
        path=path_name,
        language="python",
        content=content,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        lines=2,
    )
    prev = CodePreview(id=uuid4(), project_id=proj.id, created_at=utc_now(), files=(cf,))
    c_repo.write(prev.id, "preview", prev.model_dump_json())
    c_claim = c_repo.claim(prev.id)
    return proj, c_repo, prev, c_claim, p_dir


def _save_completed_code_result(
    c_repo: CodeRepository, prev: CodePreview, summary: str = "Обзор решения"
):
    finding = CodeFinding(
        file_id="f1",
        start_line=2,
        end_line=2,
        severity="warning",
        category="reliability",
        title="Test finding",
        evidence="return 0",
        explanation="Объяснение дефекта",
        recommendation="Рекомендация по правке",
    )
    review = CodeReview(summary=summary, findings=(finding,), limitations="Ограничения анализа")
    res = CodeResult(
        preview_id=prev.id,
        preview_sha256=preview_digest(prev),
        completed_at=utc_now(),
        status="completed",
        model="gpt-5.4-mini",
        response=review,
    )
    c_repo.write(prev.id, "result", res.model_dump_json())
    return review, res


def _setup_lab(tmp_path: Path, c_repo: CodeRepository, prev: CodePreview):
    lab = LabRepository(tmp_path / "lab", c_repo)
    l_claim = lab.claim(prev.id, 0)
    prop = Proposal(
        file_id="f1",
        replacement="def calc():\n    return 1\n",
        test_language="python",
        test_code="def test_calc():\n    assert True\n",
        explanation="Исправление возврата",
    )
    l_res = LabResult(
        preview_id=prev.id,
        preview_sha256=l_claim.preview_sha256,
        review_sha256=l_claim.review_sha256,
        completed_at=utc_now(),
        status="completed",
        model="gemini-3.8-flash",
        proposal=prop,
    )
    lab._write(prev.id, "result", l_res.model_dump_json())
    return lab, l_claim, prop, l_res


@pytest.mark.unit
def test_review_markdown_content_and_fencing(tmp_path: Path):
    _, c_repo, prev, _, _ = _setup_repos(tmp_path, "src/исходник.py")
    malicious_summary = "Injected summary with ```code``` and <b>html</b> and ````more```` ticks"
    _save_completed_code_result(c_repo, prev, summary=malicious_summary)
    md_bytes = review_markdown(c_repo, prev.id)
    assert isinstance(md_bytes, bytes)
    text = md_bytes.decode("utf-8")
    assert "src/исходник.py" in text
    assert prev.files[0].sha256 in text
    assert "warning" in text and "reliability" in text
    assert "Объяснение дефекта" in text and "gpt-5.4-mini" in text
    assert "def calc():" not in text
    assert text.count(malicious_summary) == 1
    fenced_chunk = f"`````text\n{malicious_summary}\n`````"
    assert fenced_chunk in text


@pytest.mark.unit
def test_proposal_zip_deterministic_files_and_manifest(tmp_path: Path):
    proj, c_repo, prev, _, p_dir = _setup_repos(tmp_path)
    _save_completed_code_result(c_repo, prev)
    lab, _, prop, _ = _setup_lab(tmp_path, c_repo, prev)
    before_tree = sorted([p.name for p in p_dir.glob("**/*")])

    z1 = proposal_zip(lab, prev.id)
    z2 = proposal_zip(lab, prev.id)
    assert z1 == z2

    after_tree = sorted([p.name for p in p_dir.glob("**/*")])
    assert before_tree == after_tree

    with zipfile.ZipFile(io.BytesIO(z1), "r") as zf:
        names = zf.namelist()
        expected_files = [
            "README.md",
            "review.md",
            "proposal.patch",
            "manifest.json",
            "test_aegis_repro.py",
        ]
        assert sorted(names) == sorted(expected_files)
        for info in zf.infolist():
            assert info.date_time[0:3] == (1980, 1, 1)
            assert (info.external_attr >> 16) & 0o777 == 0o644
            assert "/" not in info.filename and "\\" not in info.filename
        assert zf.read("test_aegis_repro.py").decode("utf-8") == prop.test_code
        assert zf.read("proposal.patch").decode("utf-8") == proposal_diff(prev, prop)

        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["profile"] == "project-code-export-v1"
        assert manifest["review_model"] == "gpt-5.4-mini"
        assert manifest["fix_model"] == "gemini-3.8-flash"
        assert "verification" in manifest and manifest["verification"]["status"] == "not_requested"

        file_hashes = manifest["files"]
        assert "manifest.json" not in file_hashes
        assert len(file_hashes) == 4
        for fname, recorded_hash in file_hashes.items():
            assert hashlib.sha256(zf.read(fname)).hexdigest() == recorded_hash
        assert str(p_dir) not in z1.decode("latin1")


@pytest.mark.unit
def test_review_markdown_requires_completed_result(tmp_path: Path):
    _, c_repo, prev, _, _ = _setup_repos(tmp_path)
    with pytest.raises(ProjectError):
        review_markdown(c_repo, prev.id)
    failed_res = CodeResult(
        preview_id=prev.id,
        preview_sha256=preview_digest(prev),
        completed_at=utc_now(),
        status="failed",
        model="gpt-5.4-mini",
    )
    c_repo.write(prev.id, "result", failed_res.model_dump_json())
    with pytest.raises(ProjectError):
        review_markdown(c_repo, prev.id)


@pytest.mark.unit
def test_proposal_zip_requires_completed_review_and_lab(tmp_path: Path):
    _, c_repo, prev, _, _ = _setup_repos(tmp_path)
    lab = LabRepository(tmp_path / "lab", c_repo)
    with pytest.raises(ProjectError):
        proposal_zip(lab, prev.id)
    _save_completed_code_result(c_repo, prev)
    with pytest.raises(ProjectError):
        proposal_zip(lab, prev.id)


@pytest.mark.unit
def test_tampered_preview_or_result_hash_rejected(tmp_path: Path):
    _, c_repo, prev, _, _ = _setup_repos(tmp_path)
    _save_completed_code_result(c_repo, prev)
    lab, _, _, _ = _setup_lab(tmp_path, c_repo, prev)
    # Tamper saved preview file
    preview_file = tmp_path / "code" / str(prev.id) / "preview.json"
    data = json.loads(preview_file.read_text("utf-8"))
    data["files"][0]["path"] = "tampered.py"
    preview_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ProjectError):
        review_markdown(c_repo, prev.id)
    with pytest.raises(ProjectError):
        proposal_zip(lab, prev.id)


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_path", ["../evil.py", "/abs.py", "C:/evil.py", "sub\\evil.py", "bad\npath.py"]
)
def test_unsafe_target_paths_rejected_in_zip(tmp_path: Path, bad_path: str):
    _, c_repo, prev, _, _ = _setup_repos(tmp_path, path_name=bad_path)
    _save_completed_code_result(c_repo, prev)
    lab, _, _, _ = _setup_lab(tmp_path, c_repo, prev)
    with pytest.raises(ProjectError):
        proposal_zip(lab, prev.id)


@pytest.mark.unit
def test_fixed_test_filename_collision_rejected(tmp_path: Path):
    _, c_repo, prev, _, _ = _setup_repos(tmp_path, path_name="test_aegis_repro.py")
    _save_completed_code_result(c_repo, prev)
    lab, _, _, _ = _setup_lab(tmp_path, c_repo, prev)
    with pytest.raises(ProjectError):
        proposal_zip(lab, prev.id)


@pytest.mark.unit
def test_verification_pending_status_in_manifest(tmp_path: Path):
    _, c_repo, prev, _, _ = _setup_repos(tmp_path)
    _save_completed_code_result(c_repo, prev)
    lab, _, _, _ = _setup_lab(tmp_path, c_repo, prev)
    lab.verify_claim(prev.id, "minimal")
    raw_zip = proposal_zip(lab, prev.id)
    with zipfile.ZipFile(io.BytesIO(raw_zip), "r") as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["verification"]["status"] == "pending"


@pytest.mark.unit
def test_verification_completed_reproduced_and_tampered(tmp_path: Path):
    proj, c_repo, prev, _, _ = _setup_repos(tmp_path)
    _save_completed_code_result(c_repo, prev)
    lab, _, prop, _ = _setup_lab(tmp_path, c_repo, prev)
    v_claim = lab.verify_claim(prev.id, "minimal")
    snap_before, snap_after = verification_snapshots(prev, prop, "minimal")
    run_before = _make_run(
        uuid4(),
        proj.id,
        "minimal",
        snap_before.info.sha256,
        "failed",
        1,
        utc_now() + timedelta(seconds=1),
    )
    run_after = _make_run(
        uuid4(),
        proj.id,
        "minimal",
        snap_after.info.sha256,
        "passed",
        0,
        utc_now() + timedelta(seconds=2),
    )
    v_res = VerificationResult(
        preview_id=prev.id,
        proposal_sha256=v_claim.proposal_sha256,
        completed_at=utc_now() + timedelta(seconds=3),
        status="reproduced",
        before=run_before,
        after=run_after,
    )
    lab.verify_save(prev.id, v_res)

    raw_zip = proposal_zip(lab, prev.id)
    with zipfile.ZipFile(io.BytesIO(raw_zip), "r") as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["verification"]["status"] == "reproduced"
        assert manifest["verification"]["before_run_id"] == str(run_before.id)
        assert manifest["verification"]["after_run_id"] == str(run_after.id)

    # Tamper verification result on disk
    v_file = tmp_path / "lab" / "verification" / str(prev.id) / "result.json"
    v_data = json.loads(v_file.read_text("utf-8"))
    v_data["status"] = "not_reproduced"
    v_file.write_text(json.dumps(v_data), encoding="utf-8")
    with pytest.raises(ProjectError):
        proposal_zip(lab, prev.id)
