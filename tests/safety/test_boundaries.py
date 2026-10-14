"""Static guards complement RBAC; they are not an OS sandbox."""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.safety
SOURCE = Path(__file__).resolve().parents[2] / "src" / "aegisops"


def test_application_has_no_execution_surface() -> None:
    forbidden_imports = {"subprocess", "pty", "kubernetes"}
    forbidden_calls = {"eval", "exec", "__import__", "system", "popen", "spawn", "create_all"}
    for path in SOURCE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name.split(".")[0] not in forbidden_imports for alias in node.names
                )
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] not in forbidden_imports
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                assert name not in forbidden_calls, (path.name, name)
    assert not (SOURCE / "executor").exists()


def test_compose_binds_only_loopback() -> None:
    import yaml

    compose = yaml.safe_load((SOURCE.parents[1] / "compose.yaml").read_text(encoding="utf-8"))
    assert set(compose["services"]) == {"postgres"}
    assert all(value.startswith("127.0.0.1:") for value in compose["services"]["postgres"]["ports"])
