"""Windows credential/credit preflight: mocked key endpoint, no paid inference."""

import json
import os
import shutil
import subprocess  # noqa: TID251 -- tests launch the Windows developer wrapper, not application code
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
CANARY = "launcher-demo-credential"
pytestmark = [pytest.mark.safety, pytest.mark.skipif(not SHELL, reason="Windows launcher")]

HARNESS = """
$ErrorActionPreference = 'Stop'
function Invoke-RestMethod {
    param($Uri, $Headers, $TimeoutSec, $MaximumRedirection)
    if ($Uri -ne 'https://openrouter.ai/api/v1/key' -or $MaximumRedirection -ne 0) {
        throw 'Unexpected endpoint or redirect policy'
    }
    if ($env:LAUNCHER_TEST_FAIL -eq '1') { throw $env:LAUNCHER_TEST_CANARY }
    if ($Headers.Authorization -cne ('Bearer ' + $env:LAUNCHER_TEST_CANARY)) {
        throw 'Credential forwarding mismatch'
    }
    return ($env:LAUNCHER_TEST_STATUS | ConvertFrom-Json)
}
$priorKey = $env:AEGISOPS_LLM_API_KEY
$priorEncoding = $env:PYTHONIOENCODING
$priorLocation = (Get-Location).Path
if ($env:LAUNCHER_TEST_PROTECTED -eq '1') {
    $secret = ConvertTo-SecureString $env:LAUNCHER_TEST_CANARY -AsPlainText -Force
    try {
        [IO.File]::WriteAllText($env:LAUNCHER_TEST_FILE, (ConvertFrom-SecureString $secret))
    } finally { $secret.Dispose() }
}
$launchArgs = @{ CheckOnly = $true }
if ($env:LAUNCHER_TEST_FILE) { $launchArgs.CredentialFile = $env:LAUNCHER_TEST_FILE }
& $env:LAUNCHER_TEST_SCRIPT @launchArgs
$launchCode = $LASTEXITCODE
Write-Output ('RESTORED=' + (($priorKey -ceq $env:AEGISOPS_LLM_API_KEY) -and
    ($priorEncoding -ceq $env:PYTHONIOENCODING) -and ($priorLocation -eq (Get-Location).Path)))
exit $launchCode
"""


def launch(tmp_path, *, data=None, credential="environment", fail=False):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AEGISOPS_") and key.upper() != "PSMODULEPATH"
    }
    environment.update(
        LAUNCHER_TEST_SCRIPT=str(ROOT / "scripts/openrouter-local.ps1"),
        LAUNCHER_TEST_CANARY=CANARY,
        LAUNCHER_TEST_STATUS=json.dumps({"data": data}),
        LAUNCHER_TEST_FAIL="1" if fail else "0",
        LAUNCHER_TEST_PROTECTED="1" if credential == "protected" else "0",
        PYTHONIOENCODING="ascii",
    )
    if credential == "environment":
        environment["AEGISOPS_LLM_API_KEY"] = CANARY
    else:
        environment["LAUNCHER_TEST_FILE"] = str(tmp_path / "credential.dpapi")
        # Explicit file selection must take priority over a stale inherited key.
        environment["AEGISOPS_LLM_API_KEY"] = "stale-demo-credential"
        if credential == "corrupt":
            (tmp_path / "credential.dpapi").write_text("not-a-protected-credential")
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
    assert CANARY not in output and "stale-demo-credential" not in output
    assert "RESTORED=True" in output
    return result.returncode, output


@pytest.mark.parametrize("credential", ["environment", "protected"])
def test_ready_key_does_not_request_inference_and_restores_environment(tmp_path, credential):
    code, output = launch(
        tmp_path,
        credential=credential,
        data={"limit": 5, "limit_reset": None, "limit_remaining": 1},
    )
    assert code == 0 and "credential ready" in output
    assert "no model request was made" in output


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"limit": None},
        {"limit": 0},
        {"limit": -1},
        {"limit": 5.01},
        {"limit": True},
        {"limit": "NaN"},
        {"limit": 5, "limit_reset": "daily"},
        {"limit": 5, "limit_remaining": None},
        {"limit": 5, "limit_remaining": 0.09},
        {"limit": 5, "limit_remaining": True},
        {"limit": 5, "limit_remaining": "NaN"},
    ],
)
def test_unbounded_reset_exhausted_or_malformed_allowance_rejected(tmp_path, data):
    code, output = launch(tmp_path, data=data)
    assert code == 1 and "credential ready" not in output


def test_endpoint_error_does_not_print_provider_details_or_credential(tmp_path):
    code, output = launch(tmp_path, fail=True)
    assert code == 1 and "key verification failed" in output


@pytest.mark.parametrize("credential", ["missing", "corrupt"])
def test_bad_protected_credential_fails_before_key_endpoint(tmp_path, credential):
    code, output = launch(tmp_path, credential=credential)
    assert code == 1 and "Cannot read the protected credential" in output
