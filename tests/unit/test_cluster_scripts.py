"""Administrative scripts reject foreign context before any command is executed."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_admin_foreign_context_and_auth_plugin_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = importlib.util.spec_from_file_location(
        "guarded_cluster_script", ROOT / "scripts/local_kind.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    local = tmp_path / ".local/kind"
    local.mkdir(parents=True)
    admin = local / "admin.yaml"
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "LOCAL", local)
    monkeypatch.setattr(module, "ADMIN", admin)
    executable = MagicMock()
    monkeypatch.setattr(module, "run", executable)
    baseline = {
        "current-context": "foreign-production",
        "contexts": [
            {
                "name": "kind-aegisops-demo",
                "context": {"cluster": "kind-aegisops-demo", "user": "kind-aegisops-demo"},
            }
        ],
        "clusters": [
            {
                "name": "kind-aegisops-demo",
                "cluster": {
                    "server": "https://127.0.0.1:6443",
                    "certificate-authority-data": "fixture",
                },
            }
        ],
        "users": [
            {
                "name": "kind-aegisops-demo",
                "user": {"client-certificate-data": "fixture", "client-key-data": "fixture"},
            }
        ],
    }
    admin.write_text(yaml.safe_dump(baseline))
    with pytest.raises(module.LocalClusterError):
        module.cluster_down()
    executable.assert_not_called()
    baseline["current-context"] = "kind-aegisops-demo"
    baseline["users"][0]["user"] = {"exec": {"command": "forbidden"}}
    admin.write_text(yaml.safe_dump(baseline))
    with pytest.raises(module.LocalClusterError):
        module.admin_document()
    executable.assert_not_called()


def test_developer_commands_always_select_fixed_context() -> None:
    source = (ROOT / "scripts/local_kind.py").read_text(encoding="utf-8")
    assert '"--kubeconfig", str(ADMIN), "--context", CONTEXT' in source
    assert '"--name", NAME, "--kubeconfig", str(ADMIN)' in source
    assert '"--all"' not in source
