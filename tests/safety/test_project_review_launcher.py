"""Isolated Windows review launcher: mocked key check and inert worker, no paid inference."""

import json
import os
import shutil
import subprocess  # noqa: TID251 -- isolated operator launcher, no project execution
import venv
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
CANARY = "sk-or-isolated-launcher-runtime"
pytestmark = [pytest.mark.safety, pytest.mark.skipif(not SHELL, reason="Windows launcher")]

HARNESS = """
$ErrorActionPreference = 'Stop'
$global:keyEndpointCalls = 0
function Invoke-RestMethod {
    param($Uri, $Headers, $TimeoutSec, $MaximumRedirection)
    $global:keyEndpointCalls++
    if ($Uri -cne 'https://openrouter.ai/api/v1/key' -or $MaximumRedirection -ne 0 -or
        $TimeoutSec -ne 30 -or
        $Headers.Authorization -cne ('Bearer ' + $env:REVIEW_TEST_CANARY)) {
        throw 'Unexpected endpoint or credential'
    }
    return ($env:REVIEW_TEST_ALLOWANCE | ConvertFrom-Json)
}
$priorKey = $env:AEGISOPS_LLM_API_KEY
$priorRuntime = $env:AEGISOPS_RUNTIME_LLM_API_KEY
$priorEncoding = $env:PYTHONIOENCODING
$priorLocation = (Get-Location).Path
& $env:REVIEW_TEST_SCRIPT -Mode project-review -ReviewId $env:REVIEW_TEST_ID
$launchCode = $LASTEXITCODE
Write-Output ('RESTORED=' + (($priorKey -ceq $env:AEGISOPS_LLM_API_KEY) -and
    ($priorRuntime -ceq $env:AEGISOPS_RUNTIME_LLM_API_KEY) -and
    ($priorEncoding -ceq $env:PYTHONIOENCODING) -and
    ($priorLocation -eq (Get-Location).Path)))
Write-Output ('KEY_CHECKS=' + $global:keyEndpointCalls)
exit $launchCode
"""

FAKE_WORKER = """
import json
import os
import sys
from pathlib import Path
Path(os.environ['REVIEW_TEST_MARKER']).write_text(json.dumps({
    'arguments': sys.argv[1:],
    'key_matches': os.environ.get('AEGISOPS_LLM_API_KEY') == os.environ['REVIEW_TEST_CANARY'],
}))
if os.environ.get('REVIEW_TEST_CHILD_FAILURE') == '1':
    print(os.environ['REVIEW_TEST_CANARY'])
    raise SystemExit(1)
print(json.dumps({'ok': True}))
"""


@pytest.mark.parametrize("case", ["valid", "invalid_identity", "low_allowance", "child_failure"])
def test_review_launcher_uses_fixed_worker_and_runtime_preflight(tmp_path, case):
    root = tmp_path / "outputs/aegisops-ai"
    (root / "scripts").mkdir(parents=True)
    venv.EnvBuilder(with_pip=False).create(root / ".venv")
    shutil.copyfile(ROOT / "scripts/openrouter-local.ps1", root / "scripts/openrouter-local.ps1")
    (root / "scripts/project-review-ai-worker.py").write_text(FAKE_WORKER)
    marker = tmp_path / "worker-called.json"
    identifier = str(uuid4()) if case != "invalid_identity" else "not-a-review-id"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AEGISOPS_", "OPENAI_", "OPENROUTER_"))
        and key.upper() not in {"PSMODULEPATH", "PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(
        REVIEW_TEST_CANARY=CANARY,
        REVIEW_TEST_SCRIPT=str(root / "scripts/openrouter-local.ps1"),
        REVIEW_TEST_ID=identifier,
        REVIEW_TEST_MARKER=str(marker),
        REVIEW_TEST_CHILD_FAILURE="1" if case == "child_failure" else "0",
        REVIEW_TEST_ALLOWANCE=json.dumps(
            {
                "data": {
                    "limit": 5,
                    "limit_reset": None,
                    "limit_remaining": 0.01 if case == "low_allowance" else 1,
                }
            }
        ),
        AEGISOPS_RUNTIME_LLM_API_KEY=CANARY,
        AEGISOPS_LLM_API_KEY="stale-isolated-credential",
        PYTHONIOENCODING="ascii",
    )
    result = subprocess.run(  # noqa: S603
        [SHELL, "-NoProfile", "-NonInteractive", "-Command", HARNESS],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout + result.stderr
    assert CANARY not in output and "stale-isolated-credential" not in output
    assert "RESTORED=True" in output
    assert f"KEY_CHECKS={0 if case == 'invalid_identity' else 1}" in output
    if case in {"valid", "child_failure"}:
        assert json.loads(marker.read_text()) == {"arguments": [identifier], "key_matches": True}
    else:
        assert not marker.exists()
    if case == "valid":
        assert result.returncode == 0 and '{"ok":true}' in output
    else:
        assert result.returncode == 1
        assert '{"ok":true}' not in output
