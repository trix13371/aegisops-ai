import contextlib
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from aegisops.project_ai.transport import AnalysisError
from aegisops.project_code.models import (
    CODE_MODEL,
    CODE_PROFILE,
    CodeFinding,
    CodeReview,
    preview_digest,
    validate_review,
    wire_payload,
)
from aegisops.project_code.repository import CodeRepository
from aegisops.project_code.source import (
    capture,
    catalog,
)
from aegisops.project_code.transport import explain_code
from aegisops.projects.models import ProjectError
from aegisops.projects.repository import ProjectRepository

pytestmark = [pytest.mark.unit, pytest.mark.safety]


def _create_project(tmp_path: Path, name: str = "demo") -> tuple[ProjectRepository, object]:
    storage = tmp_path / "projects_repo"
    src_dir = tmp_path / "workspaces" / name
    src_dir.mkdir(parents=True, exist_ok=True)
    repo = ProjectRepository(storage)
    proj = repo.add(name, str(src_dir))
    return repo, proj


def test_catalog_excludes_hidden_node_modules_secrets_and_reads_no_content(tmp_path: Path):
    _, proj = _create_project(tmp_path)
    root = Path(proj.path)

    (root / "valid.py").write_text("print('hello')", encoding="utf-8")
    (root / ".hidden.py").write_text("print('hidden')", encoding="utf-8")
    nm = root / "node_modules"
    nm.mkdir()
    (nm / "bad.js").write_text("console.log(1)", encoding="utf-8")
    (root / "app.secret.py").write_text("SECRET=1", encoding="utf-8")
    (root / "token_auth.py").write_text("TOKEN=1", encoding="utf-8")
    (root / "test.bin").write_bytes(b"\x00\x01\x02")

    # Unreadable file if supported, to verify catalog doesn't read file content
    unreadable = root / "unreadable.py"
    unreadable.write_text("x = 10", encoding="utf-8")
    with contextlib.suppress(OSError):
        unreadable.chmod(0o000)

    try:
        res = catalog(proj)
        assert "valid.py" in res["files"]
        assert ".hidden.py" not in res["files"]
        assert "bad.js" not in res["files"]
        assert not any("node_modules" in f for f in res["files"])
        assert "app.secret.py" not in res["files"]
        assert "token_auth.py" not in res["files"]
        assert "test.bin" not in res["files"]
        # Catalog should succeed without attempting to read unreadable.py
        assert "unreadable.py" in res["files"]
    finally:
        with contextlib.suppress(OSError):
            unreadable.chmod(0o644)


def test_catalog_skips_symlinks(tmp_path: Path):
    _, proj = _create_project(tmp_path)
    root = Path(proj.path)
    target = root / "target.py"
    target.write_text("val = 42", encoding="utf-8")
    link = root / "link.py"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks not supported on this OS/environment")

    res = catalog(proj)
    assert "target.py" in res["files"]
    assert "link.py" not in res["files"]


def test_capture_explicit_selection_utf8_metadata(tmp_path: Path):
    _, proj = _create_project(tmp_path)
    root = Path(proj.path)
    code = "def add(a, b):\n    return a + b\n"
    (root / "math.py").write_text(code, encoding="utf-8", newline="\n")

    prev = capture(proj, ["math.py"])
    assert len(prev.files) == 1
    f = prev.files[0]
    assert f.id == "f1"
    assert f.path == "math.py"
    assert f.language == "python"
    assert f.content == code
    assert f.lines == 2
    assert f.sha256 == hashlib.sha256(code.encode("utf-8")).hexdigest()


def test_capture_duplicate_traversal_control_rejected(tmp_path: Path):
    _, proj = _create_project(tmp_path)
    root = Path(proj.path)
    (root / "a.py").write_text("a = 1", encoding="utf-8")

    # Duplicate casefold
    with pytest.raises(ProjectError):
        capture(proj, ["a.py", "A.PY"])

    # Path traversal / escape
    with pytest.raises(ProjectError):
        capture(proj, ["../outside.py"])
    with pytest.raises(ProjectError):
        capture(proj, ["/etc/passwd"])
    with pytest.raises(ProjectError):
        capture(proj, ["sub\\file.py"])
    with pytest.raises(ProjectError):
        capture(proj, ["C:file.py"])


def test_capture_invalid_encoding_control_size_limits(tmp_path: Path):
    _, proj = _create_project(tmp_path)
    root = Path(proj.path)

    # Invalid UTF-8
    (root / "bad_utf.py").write_bytes(b"\xff\xfe invalid")
    with pytest.raises(ProjectError):
        capture(proj, ["bad_utf.py"])

    # Null byte / forbidden control char
    (root / "control.py").write_text("x = 1\x00", encoding="utf-8")
    with pytest.raises(ProjectError):
        capture(proj, ["control.py"])

    # Too large file (> 12288 bytes)
    (root / "large.py").write_text("x = 1\n" * 3000, encoding="utf-8")
    with pytest.raises(ProjectError):
        capture(proj, ["large.py"])

    # Empty selection / > 5 files
    for i in range(6):
        (root / f"f{i}.py").write_text(f"v = {i}", encoding="utf-8")
    with pytest.raises(ProjectError):
        capture(proj, [])
    with pytest.raises(ProjectError):
        capture(proj, [f"f{i}.py" for i in range(6)])


def test_capture_secret_markers_never_leaked_in_error(tmp_path: Path):
    _, proj = _create_project(tmp_path)
    root = Path(proj.path)

    # OpenRouter / OpenAI shaped fake token: sk- followed by 16+ chars
    fake_token = "sk-or-v1-" + ("A" * 32)
    (root / "settings.py").write_text(f"KEY = '{fake_token}'", encoding="utf-8")
    with pytest.raises(ProjectError) as exc_token:
        capture(proj, ["settings.py"])
    assert fake_token not in str(exc_token.value)

    # Quoted api_key in JSON
    secret_val = uuid4().hex
    (root / "leak.json").write_text(f'{{"api_key": "{secret_val}"}}', encoding="utf-8")
    with pytest.raises(ProjectError) as exc_json:
        capture(proj, ["leak.json"])
    assert secret_val not in str(exc_json.value)


def test_wire_payload_and_preview_digest(tmp_path: Path):
    _, proj = _create_project(tmp_path)
    root = Path(proj.path)
    (root / "mod.py").write_text("def run(): pass\n", encoding="utf-8")

    preview = capture(proj, ["mod.py"])
    wire = wire_payload(preview)

    assert wire["profile"] == CODE_PROFILE
    assert "files" in wire
    files = wire["files"]
    assert len(files) == 1
    f = files[0]
    assert set(f.keys()) == {"id", "language", "content"}
    assert "path" not in f
    assert "mod.py" not in str(wire)
    assert str(proj.id) not in str(wire)

    digest1 = preview_digest(preview)
    assert isinstance(digest1, str) and len(digest1) == 64

    # Ensure preview digest covers local path
    p2 = preview.model_copy(
        update={"files": (preview.files[0].model_copy(update={"path": "other.py"}),)}
    )
    digest2 = preview_digest(p2)
    assert digest1 != digest2


def test_code_review_validation(tmp_path: Path):
    _, proj = _create_project(tmp_path)
    root = Path(proj.path)
    code = "line1 = True\nline2 = False\nline3 = None\n"
    (root / "check.py").write_text(code, encoding="utf-8")
    preview = capture(proj, ["check.py"])

    # Valid review with exact quote
    finding = CodeFinding(
        file_id="f1",
        start_line=2,
        end_line=2,
        severity="warning",
        category="correctness",
        title="Boolean flag",
        evidence="line2 = False",
        explanation="Check usage",
        recommendation="Simplify logic",
    )
    review = CodeReview(
        summary="Review passed",
        findings=(finding,),
        limitations="Static review only",
    )
    valid = validate_review(preview, review)
    assert len(valid.findings) == 1

    # Fabricated file id
    f_bad_id = finding.model_copy(update={"file_id": "f2"})
    with pytest.raises(ValueError, match="unknown file id"):
        validate_review(preview, review.model_copy(update={"findings": (f_bad_id,)}))

    # Out of bounds lines
    f_oob = finding.model_copy(update={"start_line": 10, "end_line": 15})
    with pytest.raises(ValueError, match="line reference out of range"):
        validate_review(preview, review.model_copy(update={"findings": (f_oob,)}))

    # Incorrect quote
    f_bad_quote = finding.model_copy(update={"evidence": "does not exist"})
    with pytest.raises(ValueError, match="evidence quote not found"):
        validate_review(preview, review.model_copy(update={"findings": (f_bad_quote,)}))

    # Empty findings is valid
    empty_review = CodeReview(
        summary="Clean file",
        findings=(),
        limitations="None",
    )
    assert validate_review(preview, empty_review).findings == ()


def test_repository_lifecycle_claim_frozen_and_history(tmp_path: Path):
    proj_repo, proj = _create_project(tmp_path)
    code_repo = CodeRepository(tmp_path / "code_repo", proj_repo)
    root = Path(proj.path)
    (root / "app.py").write_text("val = 123\n", encoding="utf-8", newline="\n")

    # Create preview
    prev = code_repo.create(proj.id, ["app.py"])
    assert prev.project_id == proj.id

    # Modify original file on disk; repository preview must stay frozen
    (root / "app.py").write_text("val = 999\nprint(val)\n", encoding="utf-8")
    read_back = code_repo.preview(prev.id)
    assert read_back.files[0].content == "val = 123\n"

    # Claim preview
    state_pre = code_repo.state(prev.id)
    assert state_pre["status"] == "not_requested"
    claim = code_repo.claim(prev.id)
    assert claim.preview_id == prev.id
    assert claim.preview_sha256 == preview_digest(prev)

    # Double claim rejected
    with pytest.raises(ProjectError):
        code_repo.claim(prev.id)

    state_pending = code_repo.state(prev.id)
    assert state_pending["status"] == "pending"

    # History tracking recent 20
    history = code_repo.history(proj.id)
    assert len(history) == 1
    assert history[0]["id"] == str(prev.id)
    assert history[0]["status"] == "pending"

    # Mark failure
    code_repo.fail(prev.id)
    res = code_repo.read_result(prev.id)
    assert res is not None
    assert res.status == "failed"
    assert res.model == CODE_MODEL
    assert res.execution_performed is False

    # Orphan started or result before claim rejected
    prev2 = code_repo.create(proj.id, ["app.py"])
    code_repo.write(prev2.id, "started", "{}")
    with pytest.raises(ProjectError):
        code_repo.claim(prev2.id)


def test_transport_explain_code_mock_intercept(monkeypatch, tmp_path: Path):
    _, proj = _create_project(tmp_path)
    root = Path(proj.path)
    (root / "main.py").write_text("result = 100\n", encoding="utf-8")
    preview = capture(proj, ["main.py"])

    captured_kwargs = {}

    def mock_exchange(payload, instructions, key, **kwargs):
        captured_kwargs["payload"] = payload
        captured_kwargs["instructions"] = instructions
        captured_kwargs["kwargs"] = kwargs
        return (
            {
                "summary": "All good",
                "findings": [
                    {
                        "file_id": "f1",
                        "start_line": 1,
                        "end_line": 1,
                        "severity": "info",
                        "category": "maintainability",
                        "title": "Constant val",
                        "evidence": "result = 100",
                        "explanation": "Explicit constant",
                        "recommendation": "Keep as is",
                    }
                ],
                "limitations": "Mock limitations",
            },
            10,
            20,
        )

    monkeypatch.setattr("aegisops.project_code.transport._exchange", mock_exchange)
    review, inp, out = explain_code(preview, SecretStr("test-key"))

    assert inp == 10
    assert out == 20
    assert captured_kwargs["kwargs"].get("source_review") is True
    assert "main.py" not in captured_kwargs["payload"]
    assert "f1" in captured_kwargs["payload"]

    # Validate bad model response converts to AnalysisError without leaking secret
    secret_val = uuid4().hex

    def mock_bad_exchange(*args, **kwargs):
        return {"invalid": secret_val}, 1, 1

    monkeypatch.setattr("aegisops.project_code.transport._exchange", mock_bad_exchange)
    with pytest.raises(AnalysisError) as exc_info:
        explain_code(preview, SecretStr(secret_val))
    assert secret_val not in str(exc_info.value)


@pytest.mark.parametrize(
    "bad_files",
    [
        {"a.py": ""},
        {"a.py": "line\n" * 1201},
        {"a.py": "bad\x7fcode"},
        {
            "a.py": "x" * 9000,
            "b.py": "y" * 9000,
            "c.py": "z" * 9000,
        },
    ],
)
def test_capture_rejects_invalid_inputs(tmp_path, bad_files):
    from aegisops.project_code.repository import CodeRepository

    projects, project = _create_project(tmp_path)
    for rel_path, content in bad_files.items():
        target = Path(project.path) / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, newline="\n")
    code_repo = CodeRepository(tmp_path / "code_repo", projects)
    with pytest.raises(ProjectError):
        code_repo.create(project.id, list(bad_files.keys()))


def test_tampered_preview_path_rejected_by_state(tmp_path):
    import json

    from aegisops.project_code.repository import CodeRepository

    projects, project = _create_project(tmp_path)
    file_path = Path(project.path) / "main.py"
    file_path.write_text("print('hello')\n", newline="\n")
    code_repo = CodeRepository(tmp_path / "code_repo", projects)
    preview = code_repo.create(project.id, ["main.py"])
    code_repo.claim(preview.id)

    preview_file = code_repo.path(preview.id, "preview")
    data = json.loads(preview_file.read_text())
    data["files"][0]["path"] = "tampered.py"
    preview_file.write_text(json.dumps(data))

    with pytest.raises(ProjectError):
        code_repo.state(preview.id)


def test_history_bounded_and_requires_registered_project(tmp_path):
    from aegisops.project_code.repository import CodeRepository

    projects, project = _create_project(tmp_path)
    file_path = Path(project.path) / "app.py"
    file_path.write_text("x = 1\n", newline="\n")
    code_repo = CodeRepository(tmp_path / "code_repo", projects)

    previews = [code_repo.create(project.id, ["app.py"]) for _ in range(21)]
    hist = code_repo.history(project.id)
    assert len(hist) == 20
    assert hist[0]["id"] == str(previews[-1].id)

    projects.remove(project.id)
    with pytest.raises(ProjectError):
        code_repo.history(project.id)
