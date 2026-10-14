"""Administrative scripts reject foreign context before any command is executed."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, call

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


def test_reserved_or_busy_api_port_refuses_new_cluster(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "port_guarded_cluster", ROOT / "scripts/local_kind.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    local = tmp_path / ".local/kind"
    monkeypatch.setattr(module, "LOCAL", local)
    monkeypatch.setattr(module, "ADMIN", local / "admin-kubeconfig.yaml")
    monkeypatch.setattr(module, "STATE", local / "state.json")
    kind = MagicMock(return_value="")
    monkeypatch.setattr(module, "kind", kind)
    monkeypatch.setattr(module.socket, "socket", MagicMock(side_effect=PermissionError()))
    with pytest.raises(module.LocalClusterError, match="unavailable or reserved"):
        module.cluster_up()
    assert kind.call_args_list == [call(["get", "clusters"])]


def test_kind_api_port_avoids_dynamic_windows_range_and_public_bind():
    configuration = yaml.safe_load((ROOT / "deploy/local/kind.yaml").read_text())
    assert configuration["networking"] == {"apiServerAddress": "127.0.0.1", "apiServerPort": 16443}
