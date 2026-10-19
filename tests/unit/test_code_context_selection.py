import re
from pathlib import Path
from typing import cast

import pytest
import yaml

from aegisops.project_code.context import build_map
from aegisops.project_code.selection import choose_candidates, file_role
from aegisops.project_code.source import catalog
from aegisops.projects.repository import ProjectRepository

pytestmark = pytest.mark.unit


def test_pure_selection_contract_and_order_invariance() -> None:
    paths = [
        "src/pkg/worker.py",
        "SRC/PKG/WORKER.PY",
        "src/pkg/__init__.py",
        "src/pkg/sub/nested.py",
        "src/core/main.py",
        "tests/test_worker.py",
        "docs/index.md",
        "pyproject.toml",
    ]
    chosen = choose_candidates(paths, limit=20)
    # Sufficient limit must return all unique canonical paths
    assert len(chosen) == 7
    canonical_pair = min("src/pkg/worker.py", "SRC/PKG/WORKER.PY")
    assert canonical_pair in chosen
    assert "SRC/PKG/WORKER.PY" in chosen
    assert "src/pkg/worker.py" not in chosen

    # Order invariance
    rev_paths = list(reversed(paths))
    assert choose_candidates(rev_paths, limit=20) == chosen

    # Init deprioritized behind normal module in the same parent directory
    pkg_limited = choose_candidates(["src/pkg/__init__.py", "src/pkg/worker.py"], limit=1)
    assert pkg_limited == ["src/pkg/worker.py"]

    # Role distribution: non-doc roles cycle before doc roles
    mixed = ["docs/a.md", "docs/b.md", "src/mod.py", "tests/test_a.py"]
    top_two = choose_candidates(mixed, limit=2)
    assert "docs/a.md" not in top_two
    assert "docs/b.md" not in top_two
    assert set(top_two) == {"src/mod.py", "tests/test_a.py"}

    # Fair round-robin across parent dirs within the same role
    multi_dir = [
        "src/a/one.py",
        "src/a/two.py",
        "src/a/three.py",
        "src/b/alpha.py",
        "src/b/beta.py",
        "src/c/root.py",
    ]
    picked = choose_candidates(multi_dir, limit=3)
    assert len(picked) == 3
    picked_parents = {p.rsplit("/", 1)[0] for p in picked}
    assert picked_parents == {"src/a", "src/b", "src/c"}


def test_file_role_classification() -> None:
    assert file_role("src/main.py") == "entrypoint"
    assert file_role("src/app.py") == "entrypoint"
    assert file_role("pyproject.toml") == "config"
    assert file_role("docker-compose.yml") == "config"
    assert file_role("tests/unit/test_app.py") == "test"
    assert file_role("src/components/button.spec.ts") == "test"
    assert file_role("docs/guide.md") == "docs"
    assert file_role("src/service.py") == "module"


def test_ast_python_import_resolution_submodules_and_aliases(tmp_path: Path) -> None:
    proj_dir = tmp_path / "pkg_ast"
    proj_dir.mkdir()
    src_dir = proj_dir / "pkg"
    src_dir.mkdir()
    sub_dir = src_dir / "sub"
    sub_dir.mkdir()

    # Relative imports, aliases, star imports, and parent resolution
    (src_dir / "helper.py").write_text("def run_helper(): pass\n", encoding="utf-8")
    (sub_dir / "helper.py").write_text("def nested_helper(): pass\n", encoding="utf-8")
    (proj_dir / "ext.py").write_text("def run_ext(): pass\n", encoding="utf-8")
    (proj_dir / "config.toml").write_text("key = 1\n", encoding="utf-8")
    (proj_dir / "my_helper.py").write_text("def phantom(): pass\n", encoding="utf-8")
    (src_dir / "my_helper.py").write_text("def phantom(): pass\n", encoding="utf-8")
    (proj_dir / "direct_helper.py").write_text("def phantom(): pass\n", encoding="utf-8")
    (src_dir / "direct_helper.py").write_text("def phantom(): pass\n", encoding="utf-8")
    (proj_dir / "renamed_func.py").write_text("def phantom(): pass\n", encoding="utf-8")
    (proj_dir / "pretend.py").write_text("def phantom(): pass\n", encoding="utf-8")

    main_content = (
        "from . import helper as my_helper\n"
        "from ..pkg import helper as direct_helper\n"
        "from .sub import helper\n"
        "from ext import run_ext as renamed_func\n"
        "from ext import *\n"
        "import ext as pretend\n"
    )
    (src_dir / "consumer.py").write_text(main_content, encoding="utf-8")

    repo = ProjectRepository(tmp_path / "registry")
    project = repo.add("pkg_ast", str(proj_dir))

    m = build_map(project)
    by_path = {node["path"]: node for node in m["files"]}
    consumer_node = by_path["pkg/consumer.py"]
    resolved = consumer_node["imports"]

    # Must include helper submodules via alias.name, ignoring local asname
    assert "pkg/helper.py" in resolved
    assert "pkg/sub/helper.py" in resolved
    assert "ext.py" in resolved

    # Must not contain phantom edges named after the asname
    assert not any("my_helper" in imp for imp in resolved)
    assert not any("direct_helper" in imp for imp in resolved)
    assert not any("renamed_func" in imp for imp in resolved)
    assert not any("pretend" in imp for imp in resolved)


def test_catalog_and_map_budget_and_diversity(tmp_path: Path) -> None:
    proj_dir = tmp_path / "scaled_project"
    proj_dir.mkdir()

    # Security exclusion checks
    (proj_dir / ".env").write_text(f"SECRET={'sk-' + 'a' * 20}\n", encoding="utf-8")
    git_dir = proj_dir / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("ignore", encoding="utf-8")

    # Essential files
    (proj_dir / "pyproject.toml").write_text('[project]\nname = "scaled"\n', encoding="utf-8")
    (proj_dir / "main.py").write_text("def main(): pass\n", encoding="utf-8")

    tests_dir = proj_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_main.py").write_text("def test_it(): pass\n", encoding="utf-8")

    # Generate > 200 files across multiple folders to trigger partial catalog and > 80 map selection
    for folder_idx in range(5):
        folder = proj_dir / f"pack_{folder_idx}"
        folder.mkdir()
        for file_idx in range(45):
            (folder / f"mod_{file_idx}.py").write_text(
                f"def func_{file_idx}(): pass\n", encoding="utf-8"
            )

    # Add docs
    docs_dir = proj_dir / "docs"
    docs_dir.mkdir()
    for doc_idx in range(30):
        (docs_dir / f"doc_{doc_idx}.md").write_text("# Doc\n", encoding="utf-8")

    repo = ProjectRepository(tmp_path / "registry")
    project = repo.add("scaled", str(proj_dir))

    cat = catalog(project)
    cat_files = cast(list[str], cat["files"])
    assert len(cat_files) <= 200
    assert cat["partial"] is True

    # Excluded files must never be cataloged
    assert ".env" not in cat_files
    assert not any(".git" in p for p in cat_files)

    # Catalog retains root entrypoint, config, and tests with BFS
    assert "main.py" in cat_files
    assert "pyproject.toml" in cat_files
    assert "tests/test_main.py" in cat_files

    m = build_map(project)
    map_files = cast(list[dict[str, object]], m["files"])
    assert len(map_files) <= 80
    map_paths = [str(node["path"]) for node in map_files]

    # Representative selection must not be filled purely by alphabetical docs
    roles = {file_role(p) for p in map_paths}
    assert "entrypoint" in roles
    assert "config" in roles
    assert "module" in roles
    assert "test" in roles
    assert "main.py" in map_paths

    # Contents are untrusted preview data, build_map does not store raw body text
    for node in map_files:
        assert "content" not in node


def test_ci_workflow_node_check() -> None:
    ci_path = Path(".github/workflows/checks.yml")
    assert ci_path.is_file(), "Workflow checks.yml must exist"
    raw = yaml.safe_load(ci_path.read_text(encoding="utf-8"))
    steps = raw["jobs"]["checks"]["steps"]

    setup_node_step = None
    node_test_step = None
    for step in steps:
        uses = step.get("uses", "")
        if "actions/setup-node@" in uses:
            setup_node_step = step
        run_cmd = step.get("run", "")
        if "node --test" in run_cmd:
            node_test_step = step

    assert setup_node_step is not None, "setup-node action step missing"
    # Verify pinned SHA (40 hex chars) and Node 22
    uses_str = setup_node_step["uses"]
    sha = uses_str.split("@")[1].split()[0]
    assert re.fullmatch(r"[0-9a-f]{40}", sha), f"setup-node not pinned by full SHA: {uses_str}"
    assert str(setup_node_step.get("with", {}).get("node-version")) == "22"

    assert node_test_step is not None, "node --test UI step missing"
    cmd = node_test_step["run"]
    assert "tests/ui/" in cmd


def test_python_meta_semantics() -> None:
    from aegisops.project_code.context import _python_meta

    # Generate >20 imports with an early duplicate to test uniqueness and exact cap of 20
    import_lines = ["import mod_0", "import mod_0"] + [f"import mod_{i}" for i in range(1, 25)]
    code = "\n".join(import_lines) + "\n"

    symbols, imports, ok = _python_meta(code, "dummy.py")
    assert ok
    assert len(imports) == 20
    assert len(imports) == len(set(imports))
    for imp in imports:
        assert len(imp) <= 128

    # Separately test only very long module import (>128 chars) to ensure skipped not truncated
    long_mod_name = "a" * 140
    long_code = f"import {long_mod_name}\n"
    long_symbols, long_imports, long_ok = _python_meta(long_code, "dummy_long.py")
    assert long_ok
    assert long_imports == []


def test_recommend_review_unlinked_entrypoint_with_modules_config_test_and_yaml(
    tmp_path: Path,
) -> None:
    from aegisops.project_code.context import recommend_review
    from aegisops.projects.repository import ProjectRepository

    root = tmp_path / "repo"
    root.mkdir(parents=True)
    (root / "main.py").write_text("print('entrypoint')\n", encoding="utf-8")
    for idx in range(12):
        (root / f"mod_{idx:02d}.py").write_text(
            f"def fn_{idx:02d}():\n    return {idx}\n", encoding="utf-8"
        )
    (root / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '0.1.0'\n", encoding="utf-8"
    )
    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_other.py").write_text(
        "def test_standalone():\n    assert True\n", encoding="utf-8"
    )
    (root / "a.yaml").write_text("data: 123\n", encoding="utf-8")

    repo = ProjectRepository(tmp_path / "registry")
    project = repo.add("demo", str(root))

    rec = recommend_review(project)
    files = rec["files"]
    paths = [item["path"] for item in files]

    assert len(files) <= 5
    assert rec["total_bytes"] <= 24576
    assert "main.py" in paths
    assert "pyproject.toml" in paths
    assert "tests/test_other.py" in paths
    assert "a.yaml" not in paths

    test_entry = next(item for item in files if item["path"] == "tests/test_other.py")
    assert test_entry["reason"] == "Тестовый файл для ручной проверки."

    for item in files:
        assert item["reason"] != "Центральный модуль проекта."
