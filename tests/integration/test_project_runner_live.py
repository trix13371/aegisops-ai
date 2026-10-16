"""Opt-in real Docker checks on disposable projects only; never user source."""

import importlib.util
import os
from pathlib import Path

import pytest

from aegisops.project_runner.repository import RunRepository
from aegisops.projects.repository import ProjectRepository

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.integration,
    pytest.mark.runner_live,
    pytest.mark.skipif(
        os.environ.get("AEGISOPS_RUNNER_LIVE") != "1", reason="opt-in Docker runner"
    ),
]


@pytest.fixture
def real_run(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "live_project_runner", ROOT / "scripts/project-runner-local.py"
    )
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    import project_runner_host as host

    # Preserve real image pin lookup; worker registries are isolated temporary directories.
    image = host.read_image()
    monkeypatch.setattr(worker, "read_image", lambda: image)
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    root = tmp_path / "source"
    root.mkdir()
    projects = ProjectRepository(tmp_path / ".local/projects")
    project = projects.add("Disposable test project", str(root))
    runs = RunRepository(tmp_path / ".local/project-runs")

    def launch():
        claim = runs.create(project.id)
        monkeypatch.setattr(worker.sys, "argv", ["worker", str(claim.id)])
        worker.main()
        result = runs.result(claim.id)
        assert result is not None
        assert not host.docker(
            ["ps", "--all", "--quiet", "--filter", f"label={host.LABEL}={claim.id}"]
        ).strip()
        return result

    return root, launch


def test_actual_runner_enforces_limits_no_network_keys_mounts_and_preserves_source(real_run):
    root, launch = real_run
    (root / "main.py").write_text("def add(a, b):\n    return a + b\n")
    (root / ".env").write_text("OPENROUTER_API_KEY=PRIVATE_HOST_CANARY")
    (root / "test_main.py").write_text(
        """import os
import socket
from pathlib import Path
import pytest
from main import add

def test_profile():
    assert add(2, 3) == 5
    assert os.getuid() == 65532
    assert "OPENROUTER_API_KEY" not in os.environ
    assert "AEGISOPS_LLM_API_KEY" not in os.environ
    assert not Path(".env").exists()
    assert not Path("/var/run/docker.sock").exists()
    assert Path("/sys/fs/cgroup/memory.max").read_text().strip() == "536870912"
    assert Path("/sys/fs/cgroup/pids.max").read_text().strip() == "64"
    status = Path("/proc/self/status").read_text()
    assert "CapEff:\\t0000000000000000" in status
    assert "NoNewPrivs:\\t1" in status
    assert "Seccomp:\\t2" in status
    with pytest.raises(OSError):
        socket.create_connection(("1.1.1.1", 53), timeout=0.2)
    with pytest.raises(OSError):
        Path("/opt/aegisops/entry.py").write_text("changed")
    Path("main.py").write_text("changed only inside container")
""",
        encoding="utf-8",
    )
    before = (root / "main.py").read_bytes()
    result = launch()
    assert result.status == "passed" and result.container_removed
    assert (root / "main.py").read_bytes() == before
    assert "PRIVATE_HOST_CANARY" not in result.model_dump_json()


def test_actual_runner_reports_lint_and_test_failures_with_file_locations(real_run):
    root, launch = real_run
    (root / "main.py").write_text("undefined_function()\n")
    (root / "test_bad.py").write_text("def test_wrong():\n    assert 2 + 2 == 5\n")
    result = launch()
    assert result.status == "issues" and result.container_removed
    assert [item.status for item in result.checks] == ["failed", "failed"]
    assert "main.py:1" in result.checks[0].output
    assert "test_bad.py" in result.checks[1].output


def test_actual_runner_no_tests_is_explicit_and_not_success(real_run):
    root, launch = real_run
    (root / "main.py").write_text("answer = 42\n")
    result = launch()
    assert result.status == "issues" and result.checks[1].status == "no_tests"
    assert result.container_removed


def test_actual_runner_stops_hanging_tests_and_removes_container(real_run):
    root, launch = real_run
    (root / "test_wait.py").write_text("import time\ndef test_wait():\n    time.sleep(70)\n")
    result = launch()
    assert result.status == "timeout" and result.container_removed
    assert result.checks[1].status == "timeout"


def test_actual_runner_missing_dependency_is_explicit_tool_error(real_run):
    root, launch = real_run
    (root / "test_dependency.py").write_text("import noninstalled_aegisops_test_dependency\n")
    result = launch()
    assert result.status == "error" and result.container_removed
    assert result.checks[1].status == "error"
    assert "ModuleNotFoundError" in result.checks[1].output


def test_actual_runner_bounds_output_and_stops_excessive_logs(real_run):
    root, launch = real_run
    (root / "test_output.py").write_text(
        "def test_output():\n    print('X' * 300000)\n    assert False\n"
    )
    result = launch()
    assert result.status == "error" and result.container_removed
    assert result.checks[1].output_truncated
    assert len(result.checks[1].output) <= 24000
