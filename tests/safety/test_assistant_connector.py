# ruff: noqa: E501
# Keep embedded PowerShell test harness scripts readable without artificial line wrapping.
import json
import os
import subprocess  # noqa: TID251 - offline Windows connector safety harness execution
import sys
import uuid

import pytest

pytestmark = [
    pytest.mark.safety,
    pytest.mark.skipif(
        sys.platform != "win32", reason="Windows-only offline safety connector tests"
    ),
]

FIXTURE_PROVIDERS = {
    "openrouter": {"label": "OpenRouter", "model": "deepseek/deepseek-v4.1-flash"},
    "deepseek": {"label": "DeepSeek", "model": "deepseek-flash"},
    "openai": {"label": "OpenAI", "model": "gpt-5.4-mini"},
    "gemini": {"label": "Google Gemini", "model": "gemini-3.8-flash"},
}

CANARY_KEY = "AegisTestKey_12345678_Canary"
CANARY_KEY_OPENROUTER = "sk-or-demo-runtime"

COMMON_OVERRIDE_PS = r"""
function Get-AssistantCatalog {
    [CmdletBinding()]
    param()
    return @(
        [pscustomobject]@{ provider = 'openrouter'; label = 'OpenRouter'; model = 'deepseek/deepseek-v4.1-flash' },
        [pscustomobject]@{ provider = 'deepseek'; label = 'DeepSeek'; model = 'deepseek-flash' },
        [pscustomobject]@{ provider = 'openai'; label = 'OpenAI'; model = 'gpt-5.4-mini' },
        [pscustomobject]@{ provider = 'gemini'; label = 'Google Gemini'; model = 'gemini-3.8-flash' }
    )
}

function Get-AssistantSelection {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('selected', 'active', 'resolve')]
        [string]$Action,
        [string]$Provider,
        [string]$Mode,
        [string]$Id
    )
    $p = $env:TEST_SELECTED_PROVIDER
    if (-not $p) { $p = 'openai' }
    $m = $env:TEST_SELECTED_MODEL
    if (-not $m) {
        switch ($p) {
            'openrouter' { $m = 'deepseek/deepseek-v4.1-flash' }
            'deepseek'   { $m = 'deepseek-flash' }
            'openai'     { $m = 'gpt-5.4-mini' }
            'gemini'     { $m = 'gemini-3.8-flash' }
        }
    }
    return [pscustomobject]@{ provider = $p; model = $m }
}
"""

MOCK_HARNESS_HEADER = r"""
$global:ReadHostCount = 0
function Read-Host {
    [CmdletBinding()]
    param(
        [Parameter(Position=0)]
        [string]$Prompt,
        [switch]$AsSecureString
    )
    $global:ReadHostCount++
    $canary = $env:TEST_INPUT_CANARY
    if (-not $canary) { $canary = 'AegisTestKey_12345678_Canary' }
    if ($AsSecureString) {
        $sec = New-Object System.Security.SecureString
        foreach ($c in $canary.ToCharArray()) { $sec.AppendChar($c) }
        $sec.MakeReadOnly()
        return $sec
    }
    return $canary
}

function Invoke-RestMethod {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][ValidateSet('Get')][string]$Method,
        [Parameter(Mandatory=$true)][string]$Uri,
        [Parameter(Mandatory=$true)][hashtable]$Headers,
        [Parameter(Mandatory=$true)][ValidateRange(30,30)][int]$TimeoutSec,
        [Parameter(Mandatory=$true)][ValidateRange(0,0)][int]$MaximumRedirection
    )
    $expectedKey = $env:TEST_EXPECTED_KEY
    if (-not $expectedKey) { $expectedKey = 'AegisTestKey_12345678_Canary' }

    $matched = $false
    if ($Uri -eq 'https://openrouter.ai/api/v1/key') {
        if ($Headers['Authorization'] -ne ('Bearer ' + $expectedKey)) { throw 'bad-auth-header' }
        $matched = $true
        $mode = $env:TEST_OPENROUTER_METADATA_MODE
        if ($mode -eq 'bad_model') { return [pscustomobject]@{ data = [pscustomobject]@{ limit = 1.0; limit_remaining = 0.5 } } }
        if ($mode -eq 'reset_null') { return [pscustomobject]@{ data = [pscustomobject]@{ limit = 2.0; limit_remaining = 1.0; limit_reset = $null } } }
        if ($mode -eq 'has_reset') { return [pscustomobject]@{ data = [pscustomobject]@{ limit = 2.0; limit_remaining = 1.0; limit_reset = '2026-01-01' } } }
        if ($mode -eq 'bool_limit') { return [pscustomobject]@{ data = [pscustomobject]@{ limit = $true; limit_remaining = 1.0 } } }
        if ($mode -eq 'over_limit') { return [pscustomobject]@{ data = [pscustomobject]@{ limit = 10.0; limit_remaining = 1.0 } } }
        if ($mode -eq 'exhausted') { return [pscustomobject]@{ data = [pscustomobject]@{ limit = 1.0; limit_remaining = 0.05 } } }
        return [pscustomobject]@{ data = [pscustomobject]@{ limit = 2.0; limit_remaining = 1.5 } }
    }
    elseif ($Uri -eq 'https://api.deepseek.com/models') {
        if ($Headers['Authorization'] -ne ('Bearer ' + $expectedKey)) { throw 'bad-auth-header' }
        $matched = $true
        if ($env:TEST_METADATA_FAIL -eq '1') { return [pscustomobject]@{ data = @([pscustomobject]@{ id = 'unmatched-model' }) } }
        return [pscustomobject]@{ data = @([pscustomobject]@{ id = 'deepseek-flash' }, [pscustomobject]@{ id = 'deepseek-flash' }) }
    }
    elseif ($Uri -eq 'https://api.openai.com/v1/models/gpt-5.4-mini') {
        if ($Headers['Authorization'] -ne ('Bearer ' + $expectedKey)) { throw 'bad-auth-header' }
        $matched = $true
        if ($env:TEST_METADATA_FAIL -eq '1') { return [pscustomobject]@{ id = 'wrong-model' } }
        return [pscustomobject]@{ id = 'gpt-5.4-mini' }
    }
    elseif ($Uri -eq 'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash') {
        if ($Headers['x-goog-api-key'] -ne $expectedKey) { throw 'bad-auth-header' }
        $matched = $true
        if ($env:TEST_METADATA_FAIL -eq '1') { return [pscustomobject]@{ name = 'models/gemini-3.8-flash-wrong' } }
        return [pscustomobject]@{ name = 'models/gemini-3.8-flash' }
    }

    if (-not $matched) { throw ("unexpected-uri: " + $Uri) }
}
"""


@pytest.fixture
def assistant_env(tmp_path):
    repo_root = tmp_path / "outputs" / "aegisops-ai"
    scripts_dir = repo_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    store_dir = tmp_path / "work" / "aegisops-runtime-credentials"
    store_dir.mkdir(parents=True, exist_ok=True)

    here = os.path.dirname(os.path.abspath(__file__))
    project_scripts = os.path.normpath(os.path.join(here, "..", "..", "scripts"))

    for fname in ["connect-assistant.ps1", "assistant-local.ps1", "assistant-connection.ps1"]:
        src = os.path.join(project_scripts, fname)
        dst = scripts_dir / fname
        if os.path.exists(src):
            content = open(src, encoding="utf-8").read()
        else:
            pytest.skip(f"Source {fname} not found")
        if fname == "assistant-connection.ps1":
            content = content + "\r\n" + COMMON_OVERRIDE_PS
        with open(dst, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(content)

    # also provide a dummy assistant-common.ps1 referencing connection if needed
    common_alias = scripts_dir / "assistant-common.ps1"
    with open(common_alias, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(". (Join-Path $PSScriptRoot 'assistant-connection.ps1')\r\n")

    return {
        "repo_root": str(repo_root),
        "scripts_dir": str(scripts_dir),
        "store_dir": str(store_dir),
    }


def run_ps(env_info, script_type, extra_ps="", custom_env=None):
    scripts_dir = env_info["scripts_dir"]
    script_path = os.path.join(scripts_dir, script_type)
    runner_ps = os.path.join(scripts_dir, f"run_{uuid.uuid4().hex}.ps1")

    content = MOCK_HARNESS_HEADER + "\r\n" + extra_ps + "\r\n& '" + script_path + "' "
    with open(runner_ps, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(content)

    clean_env = {}
    for k, v in os.environ.items():
        if (
            k.upper().startswith("AEGISOPS")
            or "OPENROUTER" in k.upper()
            or k.upper() == "PSMODULEPATH"
        ):
            continue
        clean_env[k] = v
    if custom_env:
        clean_env.update(custom_env)

    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", runner_ps]
    res = subprocess.run(  # noqa: S603 - fixed powershell executable and temporary trusted paths with canary
        cmd, capture_output=True, text=True, cwd=env_info["repo_root"], env=clean_env, timeout=15
    )
    return res


def seed_dpapi_key(key_path, raw_key):
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        "$ErrorActionPreference = 'Stop'; "
        f"$s = ConvertTo-SecureString '{raw_key}' -AsPlainText -Force; "
        f"$enc = ConvertFrom-SecureString $s; "
        f"[System.IO.File]::WriteAllText('{key_path}', $enc, [System.Text.UTF8Encoding]::new($false))",
    ]
    subprocess.run(cmd, check=True, capture_output=True, env=env)  # noqa: S603 - fixed powershell executable and temporary trusted paths with canary


def decrypt_dpapi_key(key_path):
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        "$ErrorActionPreference = 'Stop'; "
        f"$enc = [System.IO.File]::ReadAllText('{key_path}', [System.Text.UTF8Encoding]::new($false)); "
        "$sec = ConvertTo-SecureString $enc; "
        "$bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec); "
        "$plain = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr); "
        "[System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr); "
        "Write-Output $plain",
    ]
    res = subprocess.run(cmd, check=True, capture_output=True, text=True, env=env)  # noqa: S603 - fixed powershell executable and temporary trusted paths with canary
    return res.stdout.strip()


@pytest.mark.parametrize("provider", ["openrouter", "deepseek", "openai", "gemini"])
def test_successful_new_connections_persists_config_and_dpapi(assistant_env, provider):
    canary = CANARY_KEY_OPENROUTER if provider == "openrouter" else CANARY_KEY
    env = {
        "TEST_SELECTED_PROVIDER": provider,
        "TEST_INPUT_CANARY": canary,
        "TEST_EXPECTED_KEY": canary,
    }
    extra_ps = f"& (Join-Path $PSScriptRoot 'connect-assistant.ps1') -Provider '{provider}'; exit $LASTEXITCODE"
    res = run_ps(assistant_env, "connect-assistant.ps1", extra_ps, env)

    assert res.returncode == 0, f"Failed: {res.stdout}\n{res.stderr}"
    assert canary not in res.stdout
    assert canary not in res.stderr

    active_json = os.path.join(assistant_env["store_dir"], "assistant.json")
    assert os.path.isfile(active_json)
    with open(active_json, encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("provider") == provider
    assert data.get("model") == FIXTURE_PROVIDERS[provider]["model"]

    key_file = os.path.join(assistant_env["store_dir"], f"{provider}.dpapi")
    assert os.path.isfile(key_file)
    with open(key_file, encoding="utf-8") as f:
        raw_disk = f.read()
    assert canary not in raw_disk
    assert decrypt_dpapi_key(key_file) == canary


@pytest.mark.parametrize("provider", ["openrouter", "deepseek", "openai", "gemini"])
def test_successful_runtime_check_only(assistant_env, provider):
    canary = CANARY_KEY_OPENROUTER if provider == "openrouter" else CANARY_KEY
    key_file = os.path.join(assistant_env["store_dir"], f"{provider}.dpapi")
    seed_dpapi_key(key_file, canary)

    fixed_uuid = str(uuid.uuid4())
    env = {
        "TEST_SELECTED_PROVIDER": provider,
        "TEST_EXPECTED_KEY": canary,
        "AEGISOPS_ASSISTANT_PROVIDER": "sentinel-provider",
        "AEGISOPS_ASSISTANT_MODEL": "sentinel-model",
        "AEGISOPS_LLM_API_KEY": "sentinel-key",
    }
    extra_ps = f"& (Join-Path $PSScriptRoot 'assistant-local.ps1') -Mode 'project_code_ai' -Id '{fixed_uuid}' -CheckOnly; exit $LASTEXITCODE"
    res = run_ps(assistant_env, "assistant-local.ps1", extra_ps, env)

    assert res.returncode == 0
    assert '{"ok":true}' in res.stdout
    assert canary not in res.stdout
    assert canary not in res.stderr


def test_existing_provider_key_reused_without_prompt(assistant_env):
    provider = "openai"
    canary = "CanaryExistingReusedKey_12345"
    key_file = os.path.join(assistant_env["store_dir"], f"{provider}.dpapi")
    seed_dpapi_key(key_file, canary)
    with open(key_file, "rb") as f:
        original_bytes = f.read()

    env = {
        "TEST_SELECTED_PROVIDER": provider,
        "TEST_EXPECTED_KEY": canary,
        "TEST_INPUT_CANARY": "PromptShouldNotHappenKey_99999",
    }
    extra_ps = f"& (Join-Path $PSScriptRoot 'connect-assistant.ps1') -Provider '{provider}'; if ($global:ReadHostCount -ne 0) {{ throw 'prompted' }}; exit $LASTEXITCODE"
    res = run_ps(assistant_env, "connect-assistant.ps1", extra_ps, env)

    assert res.returncode == 0
    with open(key_file, "rb") as f:
        after_bytes = f.read()
    assert original_bytes == after_bytes
    assert canary not in res.stdout


def test_failure_preflight_preserves_existing_config(assistant_env):
    active_json = os.path.join(assistant_env["store_dir"], "assistant.json")
    initial_config = {"provider": "openai", "model": "gpt-5.4-mini"}
    with open(active_json, "w", encoding="utf-8") as f:
        json.dump(initial_config, f)

    env = {
        "TEST_SELECTED_PROVIDER": "openai",
        "TEST_INPUT_CANARY": CANARY_KEY,
        "TEST_METADATA_FAIL": "1",
    }
    extra_ps = (
        "& (Join-Path $PSScriptRoot 'connect-assistant.ps1') -Provider 'openai'; exit $LASTEXITCODE"
    )
    res = run_ps(assistant_env, "connect-assistant.ps1", extra_ps, env)

    assert res.returncode != 0
    with open(active_json, encoding="utf-8") as f:
        data = json.load(f)
    assert data == initial_config


@pytest.mark.parametrize(
    "mode,should_succeed",
    [
        ("reset_null", True),  # null means no periodic reset
        ("has_reset", False),  # property limit_reset exists
        ("bool_limit", False),  # limit is bool
        ("over_limit", False),  # limit > 5
        ("exhausted", False),  # remaining < 0.10
    ],
)
def test_openrouter_reset_and_budget_guards(assistant_env, mode, should_succeed):
    env = {
        "TEST_SELECTED_PROVIDER": "openrouter",
        "TEST_INPUT_CANARY": CANARY_KEY_OPENROUTER,
        "TEST_EXPECTED_KEY": CANARY_KEY_OPENROUTER,
        "TEST_OPENROUTER_METADATA_MODE": mode,
    }
    extra_ps = "& (Join-Path $PSScriptRoot 'connect-assistant.ps1') -Provider 'openrouter'; exit $LASTEXITCODE"
    res = run_ps(assistant_env, "connect-assistant.ps1", extra_ps, env)
    if should_succeed:
        assert res.returncode == 0
    else:
        assert res.returncode != 0


def test_wrong_model_metadata_fails_preflight(assistant_env):
    env = {
        "TEST_SELECTED_PROVIDER": "gemini",
        "TEST_INPUT_CANARY": CANARY_KEY,
        "TEST_EXPECTED_KEY": CANARY_KEY,
        "TEST_METADATA_FAIL": "1",
    }
    extra_ps = (
        "& (Join-Path $PSScriptRoot 'connect-assistant.ps1') -Provider 'gemini'; exit $LASTEXITCODE"
    )
    res = run_ps(assistant_env, "connect-assistant.ps1", extra_ps, env)
    assert res.returncode != 0


def test_no_key_canary_leaked_in_stdout_or_stderr(assistant_env):
    env = {
        "TEST_SELECTED_PROVIDER": "openai",
        "TEST_INPUT_CANARY": CANARY_KEY,
        "TEST_EXPECTED_KEY": CANARY_KEY,
    }
    extra_ps = (
        "& (Join-Path $PSScriptRoot 'connect-assistant.ps1') -Provider 'openai'; exit $LASTEXITCODE"
    )
    res = run_ps(assistant_env, "connect-assistant.ps1", extra_ps, env)
    assert CANARY_KEY not in res.stdout
    assert CANARY_KEY not in res.stderr
