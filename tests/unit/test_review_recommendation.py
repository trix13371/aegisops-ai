import hashlib
from pathlib import Path

import pytest

from aegisops.project_code import context
from aegisops.project_code.context import build_map, recommend_review
from aegisops.projects.repository import ProjectRepository

pytestmark = pytest.mark.unit


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_real_tree_recommendation_and_build_map(tmp_path: Path) -> None:
    proj_dir = tmp_path / "demo_tree"
    proj_dir.mkdir()
    tests_dir = proj_dir / "tests"
    tests_dir.mkdir()
    (proj_dir / "main.py").write_text("import util\ndef run(): pass\n", encoding="utf-8")
    (proj_dir / "util.py").write_text("def helper(): return 1\n", encoding="utf-8")
    (tests_dir / "test_main.py").write_text("import main\ndef test_it(): pass\n", encoding="utf-8")
    (proj_dir / "pyproject.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")
    (proj_dir / "README.md").write_text("# Demo doc\n", encoding="utf-8")
    (proj_dir / ".env").write_text(f"SECRET={'sk-' + 'a' * 32}\n", encoding="utf-8")

    repo = ProjectRepository(tmp_path / "registry")
    project = repo.add("demo", str(proj_dir))

    m = build_map(project)
    files_map = {f["path"]: f for f in m["files"]}
    assert ".env" not in files_map
    assert files_map["main.py"]["bytes"] == len((proj_dir / "main.py").read_bytes())
    assert files_map["main.py"]["sha256"] == _sha((proj_dir / "main.py").read_bytes())

    rec = recommend_review(project)
    assert rec["profile"] == "project-code-selection-v1"
    paths = [f["path"] for f in rec["files"]]
    assert "main.py" in paths and "util.py" in paths
    assert "pyproject.toml" in paths or "tests/test_main.py" in paths
    assert len(rec["files"]) <= 5
    assert rec["total_bytes"] <= 24576
    for f in rec["files"]:
        assert "content" not in f
        assert not str(f["path"]).startswith("/")
        assert len(f["sha256"]) == 64
        assert f["bytes"] > 0 and isinstance(f["reason"], str) and len(f["reason"]) > 0


def test_build_map_invalid_python_meta(tmp_path: Path) -> None:
    proj_dir = tmp_path / "bad_tree"
    proj_dir.mkdir()
    bad_bytes = b"def bad_syntax(: pass"
    (proj_dir / "broken.py").write_bytes(bad_bytes)
    repo = ProjectRepository(tmp_path / "registry")
    project = repo.add("demo", str(proj_dir))
    m = build_map(project)
    node = m["files"][0]
    assert node["path"] == "broken.py"
    assert node["analyzed"] is False
    assert node["bytes"] == len(bad_bytes)
    assert node["sha256"] == _sha(bad_bytes)
    assert m["partial"] is True


def test_build_map_capture_fail_oversized(tmp_path: Path) -> None:
    proj_dir = tmp_path / "huge_tree"
    proj_dir.mkdir()
    (proj_dir / "huge.py").write_bytes(b"x" * 300000)
    repo = ProjectRepository(tmp_path / "registry")
    project = repo.add("demo", str(proj_dir))
    m = build_map(project)
    huge_node = next(f for f in m["files"] if f["path"] == "huge.py")
    assert huge_node["bytes"] == 0
    assert huge_node["sha256"] is None
    assert m["partial"] is True


def _make_fake_map(files, partial=False, limitations=None):
    return {
        "files": files,
        "partial": partial,
        "limitations": limitations or [],
        "analyzed_files": len(files),
        "candidate_files": len(files),
    }


def test_planner_io_monkeypatch_and_deterministic_order(monkeypatch, tmp_path: Path) -> None:
    proj_dir = tmp_path / "dummy"
    proj_dir.mkdir()
    repo = ProjectRepository(tmp_path / "registry")
    project = repo.add("demo", str(proj_dir))

    calls = 0

    def fake_build_map(_):
        nonlocal calls
        calls += 1
        return _make_fake_map(
            [
                {
                    "path": "b_mod.py",
                    "language": "python",
                    "role": "module",
                    "symbols": [],
                    "imports": [],
                    "related": [],
                    "analyzed": True,
                    "bytes": 100,
                    "sha256": "b" * 64,
                },
                {
                    "path": "a_mod.py",
                    "language": "python",
                    "role": "module",
                    "symbols": [],
                    "imports": [],
                    "related": [],
                    "analyzed": True,
                    "bytes": 100,
                    "sha256": "a" * 64,
                },
            ]
        )

    monkeypatch.setattr(context, "build_map", fake_build_map)
    monkeypatch.setattr(context, "capture", lambda *args, **kwargs: pytest.fail("Planner did IO"))
    monkeypatch.setattr(context, "catalog", lambda *args, **kwargs: pytest.fail("Planner did IO"))

    rec1 = recommend_review(project)
    assert calls == 1
    assert [f["path"] for f in rec1["files"]] == ["a_mod.py", "b_mod.py"]

    def fake_build_map_rev(_):
        return _make_fake_map(
            [
                {
                    "path": "a_mod.py",
                    "language": "python",
                    "role": "module",
                    "symbols": [],
                    "imports": [],
                    "related": [],
                    "analyzed": True,
                    "bytes": 100,
                    "sha256": "a" * 64,
                },
                {
                    "path": "b_mod.py",
                    "language": "python",
                    "role": "module",
                    "symbols": [],
                    "imports": [],
                    "related": [],
                    "analyzed": True,
                    "bytes": 100,
                    "sha256": "b" * 64,
                },
            ]
        )

    monkeypatch.setattr(context, "build_map", fake_build_map_rev)
    rec2 = recommend_review(project)
    assert [f["path"] for f in rec2["files"]] == ["a_mod.py", "b_mod.py"]


def test_budget_limits_and_invalid_metadata_filtered(monkeypatch, tmp_path: Path) -> None:
    proj_dir = tmp_path / "dummy"
    proj_dir.mkdir()
    repo = ProjectRepository(tmp_path / "registry")
    project = repo.add("demo", str(proj_dir))

    fake_files = [
        {
            "path": "app.py",
            "language": "python",
            "role": "entrypoint",
            "symbols": [],
            "imports": ["mod.py"],
            "related": ["mod.py"],
            "analyzed": True,
            "bytes": 10000,
            "sha256": "1" * 64,
        },
        {
            "path": "mod.py",
            "language": "python",
            "role": "module",
            "symbols": [],
            "imports": [],
            "related": [],
            "analyzed": True,
            "bytes": 10000,
            "sha256": "2" * 64,
        },
        {
            "path": "toobig.py",
            "language": "python",
            "role": "module",
            "symbols": [],
            "imports": [],
            "related": [],
            "analyzed": True,
            "bytes": 12289,
            "sha256": "3" * 64,
        },
        {
            "path": "zero.py",
            "language": "python",
            "role": "module",
            "symbols": [],
            "imports": [],
            "related": [],
            "analyzed": True,
            "bytes": 0,
            "sha256": "4" * 64,
        },
        {
            "path": "badsha.py",
            "language": "python",
            "role": "module",
            "symbols": [],
            "imports": [],
            "related": [],
            "analyzed": True,
            "bytes": 100,
            "sha256": "invalid",
        },
        {
            "path": "extra.py",
            "language": "python",
            "role": "module",
            "symbols": [],
            "imports": [],
            "related": [],
            "analyzed": True,
            "bytes": 5000,
            "sha256": "5" * 64,
        },
        {
            "path": "overflow.py",
            "language": "python",
            "role": "module",
            "symbols": [],
            "imports": [],
            "related": [],
            "analyzed": True,
            "bytes": 1000,
            "sha256": "6" * 64,
        },
    ]
    monkeypatch.setattr(context, "build_map", lambda _: _make_fake_map(fake_files))

    rec = recommend_review(project)
    paths = [f["path"] for f in rec["files"]]
    assert "app.py" in paths and "mod.py" in paths
    assert "toobig.py" not in paths and "zero.py" not in paths and "badsha.py" not in paths
    assert rec["total_bytes"] <= 24576
    assert len(rec["files"]) <= 5


def test_inbound_anchor_selection_no_entrypoint(monkeypatch, tmp_path: Path) -> None:
    proj_dir = tmp_path / "dummy"
    proj_dir.mkdir()
    repo = ProjectRepository(tmp_path / "registry")
    project = repo.add("demo", str(proj_dir))

    fake_files = [
        {
            "path": "leaf.py",
            "language": "python",
            "role": "module",
            "symbols": [],
            "imports": ["core.py"],
            "related": ["core.py"],
            "analyzed": True,
            "bytes": 50,
            "sha256": "1" * 64,
        },
        {
            "path": "core.py",
            "language": "python",
            "role": "module",
            "symbols": [],
            "imports": [],
            "related": [],
            "analyzed": True,
            "bytes": 50,
            "sha256": "2" * 64,
        },
        {
            "path": "other.py",
            "language": "python",
            "role": "module",
            "symbols": [],
            "imports": ["core.py"],
            "related": ["core.py"],
            "analyzed": True,
            "bytes": 50,
            "sha256": "3" * 64,
        },
    ]
    monkeypatch.setattr(context, "build_map", lambda _: _make_fake_map(fake_files))

    rec = recommend_review(project)
    assert rec["files"][0]["path"] == "core.py"
    assert "Центральный модуль" in rec["files"][0]["reason"]


def test_test_inbound_inverse_link_and_configs(monkeypatch, tmp_path: Path) -> None:
    proj_dir = tmp_path / "dummy"
    proj_dir.mkdir()
    repo = ProjectRepository(tmp_path / "registry")
    project = repo.add("demo", str(proj_dir))

    fake_files = [
        {
            "path": "main.py",
            "language": "python",
            "role": "entrypoint",
            "symbols": [],
            "imports": [],
            "related": [],
            "analyzed": True,
            "bytes": 60,
            "sha256": "a" * 64,
        },
        {
            "path": "t_spec.py",
            "language": "python",
            "role": "test",
            "symbols": [],
            "imports": ["main.py"],
            "related": ["main.py"],
            "analyzed": True,
            "bytes": 60,
            "sha256": "b" * 64,
        },
        {
            "path": "config.yaml",
            "language": "yaml",
            "role": "config",
            "symbols": [],
            "imports": [],
            "related": [],
            "analyzed": True,
            "bytes": 60,
            "sha256": "c" * 64,
        },
    ]
    monkeypatch.setattr(context, "build_map", lambda _: _make_fake_map(fake_files))

    rec = recommend_review(project)
    test_item = next(f for f in rec["files"] if f["path"] == "t_spec.py")
    assert "Связанный тест" in test_item["reason"]
    cfg_item = next(f for f in rec["files"] if f["path"] == "config.yaml")
    assert "Конфигурация" in cfg_item["reason"]


def test_partial_limitations_and_empty_plan(monkeypatch, tmp_path: Path) -> None:
    proj_dir = tmp_path / "dummy"
    proj_dir.mkdir()
    repo = ProjectRepository(tmp_path / "registry")
    project = repo.add("demo", str(proj_dir))

    monkeypatch.setattr(
        context,
        "build_map",
        lambda _: _make_fake_map([], partial=True, limitations=["Кастомный лимит"]),
    )

    rec = recommend_review(project)
    assert rec["files"] == []
    assert rec["total_bytes"] == 0
    assert rec["partial"] is True
    assert any("Кастомный лимит" in lim for lim in rec["limitations"])
    assert any("а не полный аудит" in lim for lim in rec["limitations"])
