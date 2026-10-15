"""Windows credential/credit preflight: mocked key endpoint, no paid inference."""

import json
import os
import shutil
import subprocess  # noqa: TID251 -- tests launch the Windows developer wrapper, not application code
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
CANARY = "sk-or-demo-runtime"
pytestmark = [pytest.mark.safety, pytest.mark.skipif(not SHELL, reason="Windows launcher")]

HARNESS = """
$ErrorActionPreference = 'Stop'
$global:keyEndpointCalls = 0
function Invoke-RestMethod {
    param($Uri, $Headers, $TimeoutSec, $MaximumRedirection)
    $global:keyEndpointCalls++
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
$priorRuntime = $env:AEGISOPS_RUNTIME_LLM_API_KEY
$priorEncoding = $env:PYTHONIOENCODING
$priorLocation = (Get-Location).Path
if ($env:LAUNCHER_TEST_PROTECTED -eq '1') {
    $secret = ConvertTo-SecureString $env:LAUNCHER_TEST_CANARY -AsPlainText -Force
    try {
        [IO.File]::WriteAllText($env:LAUNCHER_TEST_FILE, (ConvertFrom-SecureString $secret))
    } finally { $secret.Dispose() }
}
function Read-Host {
    param($Prompt, [switch]$AsSecureString)
    return (ConvertTo-SecureString $env:LAUNCHER_TEST_CANARY -AsPlainText -Force)
}
$initialProtected = $null
if ($env:LAUNCHER_TEST_FILE -and (Test-Path -LiteralPath $env:LAUNCHER_TEST_FILE)) {
    $initialProtected = [IO.File]::ReadAllText($env:LAUNCHER_TEST_FILE)
}
if ($env:LAUNCHER_TEST_CONNECT -eq '1') {
    & $env:LAUNCHER_TEST_SCRIPT
} else {
    $launchArgs = @{ CheckOnly = $true }
    if ($env:LAUNCHER_TEST_EXPLICIT -eq '1') {
        $launchArgs.CredentialFile = $env:LAUNCHER_TEST_FILE
    }
    & $env:LAUNCHER_TEST_SCRIPT @launchArgs
}
$launchCode = $LASTEXITCODE
Write-Output ('RESTORED=' + (($priorKey -ceq $env:AEGISOPS_LLM_API_KEY) -and
    ($priorRuntime -ceq $env:AEGISOPS_RUNTIME_LLM_API_KEY) -and
    ($priorEncoding -ceq $env:PYTHONIOENCODING) -and ($priorLocation -eq (Get-Location).Path)))
Write-Output ('KEY_CHECKS=' + $global:keyEndpointCalls)
if ($initialProtected) {
    $currentProtected = [IO.File]::ReadAllText($env:LAUNCHER_TEST_FILE)
    Write-Output ('ORIGINAL_FILE_UNCHANGED=' + ($initialProtected -ceq $currentProtected))
}
Write-Output ('PROJECT_FILE_EXISTS=' + (Test-Path -LiteralPath $env:LAUNCHER_TEST_RUNTIME_FILE))
if (Test-Path -LiteralPath $env:LAUNCHER_TEST_RUNTIME_FILE) {
    $stored = ConvertTo-SecureString ([IO.File]::ReadAllText($env:LAUNCHER_TEST_RUNTIME_FILE))
    try {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($stored)
        try {
            $matches = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) -ceq
                $env:LAUNCHER_TEST_CANARY
            Write-Output ('PROJECT_DECRYPTS=' + $matches)
        } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    } finally { $stored.Dispose() }
}
exit $launchCode
"""


def launch(tmp_path, *, data=None, credential="environment", fail=False, connect=False):
    # Isolated project layout: never read or write the real user's credential stores.
    root = tmp_path / "outputs/aegisops-ai"
    (root / "scripts").mkdir(parents=True)
    (root / ".venv/Scripts").mkdir(parents=True)
    (root / ".venv/Scripts/python.exe").touch()
    for name in ("openrouter-local.ps1", "connect-openrouter-local.ps1"):
        shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
    runtime_file = tmp_path / "work/aegisops-runtime-credentials/openrouter.dpapi"
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AEGISOPS_") and key.upper() != "PSMODULEPATH"
    }
    environment.update(
        LAUNCHER_TEST_SCRIPT=str(
            root
            / "scripts"
            / ("connect-openrouter-local.ps1" if connect else "openrouter-local.ps1")
        ),
        LAUNCHER_TEST_CANARY=CANARY,
        LAUNCHER_TEST_STATUS=json.dumps({"data": data}),
        LAUNCHER_TEST_FAIL="1" if fail else "0",
        LAUNCHER_TEST_PROTECTED=(
            "1" if credential in {"protected", "development-only", "default-runtime"} else "0"
        ),
        LAUNCHER_TEST_CONNECT="1" if connect else "0",
        LAUNCHER_TEST_EXPLICIT=("1" if credential in {"protected", "missing", "corrupt"} else "0"),
        LAUNCHER_TEST_RUNTIME_FILE=str(runtime_file),
        PYTHONIOENCODING="ascii",
        AEGISOPS_LLM_API_KEY="stale-demo-credential",
    )
    if credential == "environment":
        environment["AEGISOPS_RUNTIME_LLM_API_KEY"] = CANARY
    else:
        credential_path = tmp_path / "credential.dpapi"
        if credential == "development-only":
            credential_path = tmp_path / "work/agent-credentials/openrouter.dpapi"
        elif credential == "default-runtime":
            credential_path = runtime_file
        credential_path.parent.mkdir(parents=True, exist_ok=True)
        environment["LAUNCHER_TEST_FILE"] = str(credential_path)
        # Explicit file selection must take priority over a stale inherited key.
        environment["AEGISOPS_RUNTIME_LLM_API_KEY"] = "stale-runtime-credential"
        if credential in {"development-only", "default-runtime"}:
            environment.pop("AEGISOPS_RUNTIME_LLM_API_KEY")
        if credential == "corrupt":
            credential_path.write_text("not-a-protected-credential")
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


@pytest.mark.parametrize("credential", ["environment", "protected", "default-runtime"])
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
    assert code == 1 and "Cannot read the project credential" in output
    assert "KEY_CHECKS=0" in output


def test_development_store_and_generic_cli_key_are_not_used_as_runtime_fallback(tmp_path):
    code, output = launch(tmp_path, credential="development-only")
    assert code == 1 and "Connect-AegisOps-OpenRouter.cmd" in output
    assert "KEY_CHECKS=0" in output and "ORIGINAL_FILE_UNCHANGED=True" in output


def test_project_connector_saves_separate_dpapi_key_without_paid_inference(tmp_path):
    code, output = launch(
        tmp_path,
        connect=True,
        data={"limit": 1, "limit_remaining": 1},
    )
    assert code == 0 and "separate PROJECT runtime credential saved" in output
    assert "PROJECT_FILE_EXISTS=True" in output and "KEY_CHECKS=1" in output
    assert "PROJECT_DECRYPTS=True" in output


def test_project_connector_rejects_using_the_development_key(tmp_path):
    code, output = launch(tmp_path, connect=True, credential="development-only")
    assert code == 1 and "DIFFERENT key" in output
    assert "PROJECT_FILE_EXISTS=False" in output and "KEY_CHECKS=0" in output
    assert "ORIGINAL_FILE_UNCHANGED=True" in output


def test_project_connector_does_not_store_key_after_failed_preflight(tmp_path):
    code, output = launch(tmp_path, connect=True, data={"limit": 100, "limit_remaining": 100})
    assert code == 1 and "PROJECT_FILE_EXISTS=False" in output


def test_project_connector_does_not_overwrite_existing_runtime_credential(tmp_path):
    code, output = launch(tmp_path, connect=True, credential="default-runtime")
    assert code == 1 and "already exists" in output
    assert "ORIGINAL_FILE_UNCHANGED=True" in output and "KEY_CHECKS=0" in output
