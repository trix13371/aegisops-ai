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
    monkeypatch.setattr(worker, "read_image", host.read_image)
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    root = tmp_path / "source"
    root.mkdir()
    projects = ProjectRepository(tmp_path / ".local/projects")
    project = projects.add("Disposable test project", str(root))
    runs = RunRepository(tmp_path / ".local/project-runs")

    def launch(environment="minimal"):
        claim = runs.create(project.id, environment)
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
    from aegisops.project_runner.diagnostics import missing_modules

    assert missing_modules(result.checks[1]) == ("noninstalled_aegisops_test_dependency",)


def test_dependency_preset_fixes_missing_environment_without_changing_source(real_run):
    from aegisops.project_runner.diagnostics import missing_modules

    root, launch = real_run
    (root / "test_data.py").write_text(
        "import yaml\nfrom pydantic import BaseModel\n"
        "class Data(BaseModel):\n    value: int\n"
        "def test_data():\n    assert Data(**yaml.safe_load('value: 42')).value == 42\n"
    )
    before = launch()
    assert before.status == "error" and missing_modules(before.checks[1]) == ("yaml",)
    after = launch("python-data-v1")
    assert after.status == "passed" and after.container_removed
    assert after.environment == "python-data-v1"
    assert before.snapshot.sha256 == after.snapshot.sha256


def test_real_aegisops_static_project_unit_tests_with_prepared_dependencies(real_run):
    # A read-only selection of existing source/tests; no root conftest or service fixtures.
    root, launch = real_run
    files = [
        "src/aegisops/__init__.py",
        "src/aegisops/domain/__init__.py",
        "src/aegisops/domain/common.py",
        "src/aegisops/io.py",
        "tests/unit/test_local_projects.py",
    ]
    files.extend(
        str(path.relative_to(ROOT)) for path in (ROOT / "src/aegisops/projects").glob("*.py")
    )
    for name in files:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    result = launch("python-data-v1")
    assert result.status == "passed" and result.container_removed
    assert "passed" in result.checks[1].output


def test_actual_runner_bounds_output_and_stops_excessive_logs(real_run):
    root, launch = real_run
    (root / "test_output.py").write_text(
        "def test_output():\n    print('X' * 300000)\n    assert False\n"
    )
    result = launch()
    assert result.status == "error" and result.container_removed
    assert result.checks[1].output_truncated
    assert len(result.checks[1].output) <= 24000


def test_node_typescript_and_native_tests_pass_with_same_container_boundaries(real_run):
    root, launch = real_run
    (root / "main.ts").write_text(
        "export function add(a: number, b: number): number {return a+b;}\n"
    )
    (root / ".env").write_text("PRIVATE_NODE_CANARY")
    (root / "package.json").write_text(
        '{"type":"module","scripts":{"test":"exit 123","postinstall":"exit 123"}}'
    )
    (root / "tsconfig.json").write_text('{"extends":"./MUST_NOT_LOAD.json"}')
    (root / "main.test.ts").write_text(
        """import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import net from 'node:net';
import {add} from './main.ts';
test('profile', async () => {
  assert.equal(add(2, 3), 5);
  assert.equal(process.getuid!(), 65532);
  assert.equal(process.env.OPENROUTER_API_KEY, undefined);
  assert.equal(process.env.AEGISOPS_LLM_API_KEY, undefined);
  assert.equal(fs.existsSync('.env'), false);
  assert.equal(fs.existsSync('/var/run/docker.sock'), false);
  assert.equal(fs.readFileSync('/sys/fs/cgroup/memory.max','utf8').trim(), '536870912');
  assert.equal(fs.readFileSync('/sys/fs/cgroup/pids.max','utf8').trim(), '64');
  const status = fs.readFileSync('/proc/self/status','utf8');
  assert.match(status, /CapEff:\\s+0000000000000000/);
  assert.match(status, /NoNewPrivs:\\s+1/);
  assert.match(status, /Seccomp:\\s+2/);
  assert.throws(() => fs.writeFileSync('/opt/aegisops/check.mjs','bad'));
  await new Promise<void>((resolve, reject) => {
    const socket = net.connect({host:'1.1.1.1',port:53});
    socket.on('error',() => {socket.destroy(); resolve();});
    socket.on('connect',() => {socket.destroy(); reject(new Error('network available'));});
  });
  fs.writeFileSync('main.ts','container copy only');
});
""",
        encoding="utf-8",
    )
    original = (root / "main.ts").read_bytes()
    result = launch("node-js-v1")
    assert result.status == "passed", [item.output for item in result.checks]
    assert result.profile == "javascript-checks-v1" and result.container_removed
    assert (root / "main.ts").read_bytes() == original
    assert "PRIVATE_NODE_CANARY" not in result.model_dump_json()


@pytest.mark.parametrize("mode", ["syntax", "types", "test_failure", "missing_package"])
def test_node_reports_syntax_types_assertions_and_missing_dependencies(real_run, mode):
    root, launch = real_run
    source = {
        "syntax": "export const broken = ;\n",
        "types": "export const value: number = 'wrong';\n",
        "test_failure": "export const value = 2;\n",
        "missing_package": "import 'noninstalled-aegisops-package';\n",
    }[mode]
    (root / "main.mts").write_text(source)
    (root / "main.test.mts").write_text(
        "import {test} from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "import './main.mts';\ntest('expected', () => assert.equal(2+2,5));\n"
    )
    result = launch("node-js-v1")
    assert result.status == "issues" and result.container_removed
    assert result.checks[1].status == "failed"
    if mode in {"syntax", "types"}:
        assert result.checks[0].status == "failed"
        assert "main.mts" in result.checks[0].output
    elif mode == "missing_package":
        assert "ERR_MODULE_NOT_FOUND" in result.checks[1].output


def test_node_no_tests_never_looks_like_full_success(real_run):
    root, launch = real_run
    (root / "main.cjs").write_text("module.exports = {answer: 42};\n")
    result = launch("node-js-v1")
    assert result.status == "issues" and result.checks[0].status == "passed"
    assert result.checks[1].status == "no_tests" and result.container_removed


def test_node_hanging_tests_are_stopped_and_container_removed(real_run):
    root, launch = real_run
    (root / "wait.test.mjs").write_text(
        "import {test} from 'node:test';\n"
        "test('hang', async () => {await new Promise(r => setTimeout(r, 70000));});\n"
    )
    result = launch("node-js-v1")
    assert result.status == "timeout" and result.checks[1].status == "timeout"
    assert result.container_removed


def test_node_excessive_output_is_bounded_and_not_success(real_run):
    root, launch = real_run
    (root / "logs.test.mjs").write_text(
        "import {test} from 'node:test';\ntest('logs', () => {console.log('X'.repeat(300000));});\n"
    )
    result = launch("node-js-v1")
    assert result.status == "error" and result.checks[1].output_truncated
    assert result.container_removed and len(result.checks[1].output) <= 24000


def vitest_project(root):
    (root / "package.json").write_text(
        '{"type":"module","devDependencies":{"vitest":"^4.0.0",'
        '"typescript":"^5.0.0"},"scripts":{"test":"exit 123","postinstall":"exit 123"}}'
    )
    (root / "add.ts").write_text(
        "export function add(a: number, b: number): number { return a+b; }\n"
    )


def test_vitest_runs_real_typescript_tests_with_prepared_dependencies_and_ignores_config(real_run):
    root, launch = real_run
    vitest_project(root)
    (root / "vitest.config.ts").write_text("throw new Error('PROJECT_CONFIG_MUST_NOT_EXECUTE');\n")
    (root / "vite.config.js").write_text("throw new Error('VITE_CONFIG_MUST_NOT_EXECUTE');\n")
    (root / "tsconfig.json").write_text('{"extends":"./MISSING_CONFIG.json"}')
    (root / ".env").write_text("PRIVATE_VITEST_CANARY")
    (root / "add.test.ts").write_text(
        """import {test,expect} from 'vitest';
import fs from 'node:fs';
import net from 'node:net';
import {add} from './add';
test('addition and isolation', async () => {
  expect(add(2,3)).toBe(5);
  expect(process.getuid!()).toBe(65532);
  expect(process.env.OPENROUTER_API_KEY).toBeUndefined();
  expect(process.env.AEGISOPS_LLM_API_KEY).toBeUndefined();
  expect(fs.existsSync('.env')).toBe(false);
  expect(fs.existsSync('/var/run/docker.sock')).toBe(false);
  expect(fs.readFileSync('/sys/fs/cgroup/memory.max','utf8').trim()).toBe('536870912');
  expect(fs.readFileSync('/sys/fs/cgroup/pids.max','utf8').trim()).toBe('64');
  expect(() => fs.writeFileSync('/opt/aegisops/run.mjs','bad')).toThrow();
  await new Promise<void>((resolve,reject) => {
    const socket=net.connect({host:'1.1.1.1',port:53});
    socket.on('error',()=>{socket.destroy();resolve();});
    socket.on('connect',()=>{socket.destroy();reject(new Error('network available'));});
  });
  fs.writeFileSync('add.ts','changed only inside container');
});
""",
        encoding="utf-8",
    )
    before = (root / "add.ts").read_bytes()
    result = launch("node-vitest-v1")
    assert result.status == "passed", "\n".join(item.output for item in result.checks)
    assert result.container_removed and result.profile == "javascript-vitest-v1"
    assert result.compatibility.framework == "vitest" and not result.compatibility_issues
    assert (root / "add.ts").read_bytes() == before
    assert "PRIVATE_VITEST_CANARY" not in result.model_dump_json()


@pytest.mark.parametrize("mode", ["assertion", "types", "only"])
def test_vitest_reports_failures_instead_of_no_tests(real_run, mode):
    root, launch = real_run
    vitest_project(root)
    (root / "add.test.ts").write_text(
        "import {test,expect} from 'vitest';\nimport {add} from './add';\n"
        + ("test.only" if mode == "only" else "test")
        + "('addition',()=>expect(add(2,3)).toBe("
        + ("6" if mode == "assertion" else "5")
        + "));\n"
    )
    if mode == "types":
        (root / "add.ts").write_text(
            "export function add(a: number,b: number): number { return 'wrong'; }\n"
        )
    result = launch("node-vitest-v1")
    assert result.status == "issues" and result.container_removed
    assert result.checks[1].status == "failed"
    if mode == "types":
        assert result.checks[0].status == "failed"


def test_vitest_no_test_files_is_not_passed(real_run):
    root, launch = real_run
    vitest_project(root)
    result = launch("node-vitest-v1")
    assert result.status == "issues" and result.container_removed
    assert result.checks[0].status == "passed" and result.checks[1].status == "no_tests"


def test_vitest_sync_hang_is_stopped_by_outer_deadline(real_run):
    root, launch = real_run
    vitest_project(root)
    (root / "hang.test.js").write_text(
        "import {test} from 'vitest';\ntest('hang',()=>{while(true) {}});\n"
    )
    result = launch("node-vitest-v1")
    assert result.status == "timeout" and result.checks[1].status == "timeout"
    assert result.container_removed


def test_vitest_output_is_bounded(real_run):
    root, launch = real_run
    vitest_project(root)
    (root / "logs.test.js").write_text(
        "import {test} from 'vitest';\ntest('logs',()=>console.log('X'.repeat(300000)));\n"
    )
    result = launch("node-vitest-v1")
    assert result.status == "error" and result.checks[1].output_truncated
    assert result.container_removed and len(result.checks[1].output) <= 24000


def test_vitest_unavailable_declared_dependency_is_rejected_before_execution(real_run):
    root, launch = real_run
    vitest_project(root)
    (root / "package.json").write_text(
        '{"devDependencies":{"vitest":"4.0.18"},"dependencies":{"react":"19"}}'
    )
    result = launch("node-vitest-v1")
    assert result.status == "not_supported" and result.reason == "project_incompatible"
    assert result.execution_scope == "not_started" and not result.checks
    assert "dependencies_unavailable" in result.compatibility_issues
