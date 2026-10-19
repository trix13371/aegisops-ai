#requires -Version 5.1
<#
.SYNOPSIS
  Local runtime launcher: resolves a binding for a mode and runs a fixed worker.
.DESCRIPTION
  Never reads the active config. Resolves the binding via the common API, loads
  only the chosen provider key, performs a metadata preflight, then launches the
  single fixed Python worker for the mode with a scrubbed environment.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('project_ai','project_review_ai','project_check_ai','project_code_ai','project_code_fix','project_code_evaluation')]
    [string]$Mode,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')]
    [string]$Id,
    [switch]$CheckOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Emit-Ok    { Write-Output '{"ok":true}' }
function Emit-Fail  { Write-Output '{"ok":false}' }

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$root      = [System.IO.Path]::GetFullPath((Join-Path $scriptDir '..'))
$commonApi = Join-Path $scriptDir 'assistant-connection.ps1'

$oldProvider = $env:AEGISOPS_ASSISTANT_PROVIDER
$oldModel    = $env:AEGISOPS_ASSISTANT_MODEL
$oldApiKey   = $env:AEGISOPS_LLM_API_KEY
$oldPythonIO = $env:PYTHONIOENCODING
$oldOutputEncoding = $null
try { $oldOutputEncoding = [Console]::OutputEncoding } catch { $oldOutputEncoding = $null }
$oldCwd      = (Get-Location).Path
$plainKey    = $null

try {
    if (-not (Test-Path -LiteralPath $commonApi -PathType Leaf)) {
        Emit-Fail; exit 1
    }
    . $commonApi

    $binding = Get-AssistantSelection -Action resolve -Mode $Mode -Id $Id
    if (-not $binding -or -not $binding.provider -or -not $binding.model) {
        Emit-Fail; exit 1
    }
    $provider = [string]$binding.provider
    $model    = [string]$binding.model

    $storePath = Get-AssistantStore
    Assert-AssistantStore | Out-Null
    Assert-AssistantPath -Path (Split-Path -Parent $storePath) | Out-Null

    $keyFilePath = Get-AssistantKeyPath -Provider $provider
    Assert-AssistantPath -Path (Split-Path -Parent $keyFilePath) | Out-Null
    if (-not (Test-Path -LiteralPath $keyFilePath -PathType Leaf)) {
        Emit-Fail; exit 1
    }
    $plainKey = Read-AssistantKey -Path $keyFilePath
    if (-not $plainKey -or $plainKey.Length -lt 8 -or $plainKey.Length -gt 4096) {
        Emit-Fail; exit 1
    }
    if ($provider -ne 'openrouter' -and $plainKey -match '^sk-or-') {
        Emit-Fail; exit 1
    }

    $ok = Test-AssistantConnection -Provider $provider -Key $plainKey
    if (-not $ok) { Emit-Fail; exit 1 }

    if ($CheckOnly) {
        Emit-Ok
        exit 0
    }

    $workerMap = @{
        project_ai               = 'scripts/project-ai-worker.py'
        project_review_ai        = 'scripts/project-review-ai-worker.py'
        project_check_ai         = 'scripts/project-check-ai-worker.py'
        project_code_ai          = 'scripts/project-code-ai-worker.py'
        project_code_fix         = 'scripts/project-code-fix-worker.py'
        project_code_evaluation  = 'scripts/project-code-evaluation-worker.py'
    }
    $workerRel = $workerMap[$Mode]
    $worker    = [System.IO.Path]::GetFullPath((Join-Path $root $workerRel))
    if (-not (Test-Path -LiteralPath $worker -PathType Leaf)) {
        Emit-Fail; exit 1
    }

    # Re-read selection just before the paid child; abort if the pair changed.
    $recheck = Get-AssistantSelection -Action resolve -Mode $Mode -Id $Id
    if (-not $recheck -or $recheck.provider -ne $provider -or $recheck.model -ne $model) {
        Emit-Fail; exit 1
    }

    $env:AEGISOPS_LLM_API_KEY          = $plainKey
    $env:AEGISOPS_ASSISTANT_PROVIDER   = $provider
    $env:AEGISOPS_ASSISTANT_MODEL      = $model
    $env:PYTHONIOENCODING              = 'utf-8'
    try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }

    Push-Location -LiteralPath $root
    try {
        $pythonExe = Join-Path $root '.venv/Scripts/python.exe'
        $raw = & $pythonExe $worker $Id 2>$null
        $childExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }

    if ($childExit -ne 0) { Emit-Fail; exit 1 }

    $parsed = $null
    try { $parsed = $raw | ConvertFrom-Json } catch { $parsed = $null }
    if (-not $parsed -or -not ($parsed.PSObject.Properties.Name -contains 'ok')) {
        Emit-Fail; exit 1
    }
    if ($parsed.ok -isnot [bool]) { Emit-Fail; exit 1 }
    if (-not $parsed.ok) { Emit-Fail; exit 1 }

    Emit-Ok
    exit 0
}
catch {
    Emit-Fail
    exit 1
}
finally {
    $env:AEGISOPS_ASSISTANT_PROVIDER = $oldProvider
    $env:AEGISOPS_ASSISTANT_MODEL    = $oldModel
    $env:AEGISOPS_LLM_API_KEY        = $oldApiKey
    $env:PYTHONIOENCODING            = $oldPythonIO
    if ($oldOutputEncoding) {
        try { [Console]::OutputEncoding = $oldOutputEncoding } catch { }
    }
    try { Set-Location -LiteralPath $oldCwd } catch { }
    if ($plainKey) { $plainKey = $null }
}
