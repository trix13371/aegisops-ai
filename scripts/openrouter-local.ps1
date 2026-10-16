# Explicit paid synthetic analysis or source-free project summary. Windows PowerShell 5.1+.
[CmdletBinding()]
param(
    [ValidateSet('analyze', 'review', 'project-report', 'project-review')]
    [string]$Mode = 'analyze',
    [ValidateSet('crashloop-exit-1', 'crashloop-missing-env', 'crashloop-bad-command', 'crashloop-oom', 'inj-log-instruction', 'inj-secret-canary')]
    [string]$Scenario = 'crashloop-oom',
    [string]$CredentialFile,
    [string]$ReportId,
    [string]$ReviewId,
    [switch]$CheckOnly,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$openRouterRoot = Split-Path -Parent $PSScriptRoot
$openRouterPython = Join-Path $openRouterRoot '.venv/Scripts/python.exe'
$openRouterOldKey = $env:AEGISOPS_LLM_API_KEY
$openRouterRuntimeKey = $env:AEGISOPS_RUNTIME_LLM_API_KEY
$openRouterOldEncoding = $env:PYTHONIOENCODING
$openRouterConsoleEncoding = [Console]::OutputEncoding
$openRouterSecret = $null
$openRouterExit = 1
$openRouterFailure = 'OpenRouter launch rejected; provider details were not displayed.'
function Stop-OpenRouterLaunch([string]$Reason) {
    $script:openRouterFailure = $Reason
    throw 'Launch rejected'
}
Push-Location -LiteralPath $openRouterRoot
try {
    if ($Mode -eq 'project-report' -and $ReportId -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') {
        Stop-OpenRouterLaunch 'Invalid report identity; no model request was made.'
    }
    if ($Mode -eq 'project-review' -and $ReviewId -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') {
        Stop-OpenRouterLaunch 'Invalid review identity; no model request was made.'
    }
    if (-not (Test-Path -LiteralPath $openRouterPython)) {
        Stop-OpenRouterLaunch 'Install the project with uv sync --frozen first.'
    }
    # The shared .env loader must not replace the verified process credential.
    if ($Mode -eq 'review' -and -not $CheckOnly) {
        if (-not (Test-Path -LiteralPath '.env')) {
            Stop-OpenRouterLaunch 'Create private .env with scripts/init-local-env.py first.'
        }
        if (Select-String -LiteralPath '.env' -Pattern '^AEGISOPS_LLM_API_KEY=' -Quiet) {
            Stop-OpenRouterLaunch 'Remove the LLM key from .env; use the protected credential or process environment.'
        }
        $openRouterDoctor = & $openRouterPython scripts/with-local-env.py aegisops doctor 2>&1
        if ($LASTEXITCODE -ne 0) { Stop-OpenRouterLaunch 'Local audit database is not ready; no model request was made.' }
    }
    # Never implicitly reuse the development helpers' credential or generic CLI env.
    if ($CredentialFile -or -not $openRouterRuntimeKey) {
        if (-not $CredentialFile) {
            $CredentialFile = Join-Path $openRouterRoot '../../work/aegisops-runtime-credentials/openrouter.dpapi'
        }
        try {
            $openRouterSecure = ConvertTo-SecureString ([IO.File]::ReadAllText($CredentialFile).Trim())
            try {
                $openRouterPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($openRouterSecure)
                try { $openRouterSecret = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($openRouterPointer) }
                finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($openRouterPointer) }
            } finally { $openRouterSecure.Dispose() }
        } catch { Stop-OpenRouterLaunch 'Cannot read the project credential. Run Connect-AegisOps-OpenRouter.cmd with a separate runtime key.' }
    } else { $openRouterSecret = $openRouterRuntimeKey }
    if (-not $openRouterSecret -or $openRouterSecret.Length -gt 4096 -or $openRouterSecret -match '[^\x21-\x7e]') {
        Stop-OpenRouterLaunch 'Invalid API credential; no model request was made.'
    }
    try {
        $openRouterStatus = Invoke-RestMethod -Uri 'https://openrouter.ai/api/v1/key' `
            -Headers @{ Authorization = 'Bearer ' + $openRouterSecret } -TimeoutSec 30 -MaximumRedirection 0
    } catch { Stop-OpenRouterLaunch 'OpenRouter key verification failed; no model request was made.' }
    $openRouterAllowance = $openRouterStatus.data
    if ($openRouterAllowance.limit -isnot [ValueType] -or $openRouterAllowance.limit -is [bool] -or
        [double]::IsNaN([double]$openRouterAllowance.limit) -or
        [double]$openRouterAllowance.limit -le 0 -or
        [double]$openRouterAllowance.limit -gt 5 -or $openRouterAllowance.limit_reset) {
        Stop-OpenRouterLaunch 'Set a positive TOTAL key limit of $5 or less, with no periodic reset.'
    }
    if ($openRouterAllowance.limit_remaining -isnot [ValueType] -or $openRouterAllowance.limit_remaining -is [bool] -or
        [double]::IsNaN([double]$openRouterAllowance.limit_remaining) -or
        [double]::IsInfinity([double]$openRouterAllowance.limit_remaining) -or
        [double]$openRouterAllowance.limit_remaining -lt 0.10) {
        Stop-OpenRouterLaunch 'Insufficient key allowance; no model request was made.'
    }
    if ($CheckOnly) {
        Write-Output 'OpenRouter credential ready; total key limit checked; no model request was made.'
        $openRouterExit = 0
    } elseif ($Mode -eq 'project-review') {
        $env:AEGISOPS_LLM_API_KEY = $openRouterSecret
        $env:PYTHONIOENCODING = 'utf-8'
        [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
        $openRouterOutput = & $openRouterPython scripts/project-review-ai-worker.py $ReviewId 2>&1
        $openRouterChildExit = $LASTEXITCODE
        try { $openRouterResult = ($openRouterOutput -join "`n") | ConvertFrom-Json }
        catch { Stop-OpenRouterLaunch 'Project review analysis failed; provider details were not displayed.' }
        if ($openRouterChildExit -ne 0 -or $openRouterResult.ok -isnot [bool] -or -not $openRouterResult.ok) {
            Stop-OpenRouterLaunch 'Project review analysis unavailable; no automatic retry.'
        }
        Write-Output '{"ok":true}'
        $openRouterExit = 0
    } elseif ($Mode -eq 'project-report') {
        $env:AEGISOPS_LLM_API_KEY = $openRouterSecret
        $env:PYTHONIOENCODING = 'utf-8'
        [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
        $openRouterOutput = & $openRouterPython scripts/project-ai-worker.py $ReportId 2>&1
        $openRouterChildExit = $LASTEXITCODE
        try { $openRouterResult = ($openRouterOutput -join "`n") | ConvertFrom-Json }
        catch { Stop-OpenRouterLaunch 'Project summary analysis failed; provider details were not displayed.' }
        if ($openRouterChildExit -ne 0 -or $openRouterResult.ok -isnot [bool] -or -not $openRouterResult.ok) {
            Stop-OpenRouterLaunch 'Project summary analysis unavailable; no automatic retry.'
        }
        Write-Output '{"ok":true}'
        $openRouterExit = 0
    } else {
        $env:AEGISOPS_LLM_API_KEY = $openRouterSecret
        $env:PYTHONIOENCODING = 'utf-8'
        [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
        $openRouterArguments = @('--scenario', $Scenario, '--llm', 'openrouter')
        if ($Mode -eq 'review') {
            # OPA, signed policy and kill switch remain authoritative. Never resume them here.
            $openRouterOutput = & $openRouterPython scripts/with-local-env.py aegisops review @openRouterArguments --authorization opa 2>&1
        } else {
            $openRouterOutput = & $openRouterPython -c 'from aegisops.cli.app import app; app()' analyze --source synthetic @openRouterArguments 2>&1
        }
        $openRouterChildExit = $LASTEXITCODE
        try { $openRouterResult = ($openRouterOutput -join "`n") | ConvertFrom-Json }
        catch { Stop-OpenRouterLaunch 'Project command failed; check local configuration. Provider details were not displayed.' }
        if ($Mode -eq 'review') {
            $openRouterRow = $openRouterResult
            $openRouterFallback = $openRouterRow.llm.parameters.used_fallback
        } else {
            if (@($openRouterResult.results).Count -ne 1) { Stop-OpenRouterLaunch 'Expected exactly one incident.' }
            $openRouterRow = $openRouterResult.results[0]
            $openRouterFallback = $openRouterRow.used_fallback
        }
        if ($null -eq $openRouterRow.llm -or $null -eq $openRouterRow.diagnosis -or
            $null -eq $openRouterRow.recommendation) {
            Stop-OpenRouterLaunch 'Project review failed before a valid analysis was available; inspect local audit.'
        }
        if ($Json) { Write-Output ($openRouterResult | ConvertTo-Json -Depth 32) }
        else {
            Write-Output ('Model: {0}' -f $openRouterRow.llm.model_name)
            Write-Output ('Fallback: {0}' -f $openRouterFallback)
            Write-Output $openRouterRow.diagnosis.summary
            Write-Output $openRouterRow.recommendation.title
            Write-Output $openRouterRow.recommendation.rationale
            if ($Mode -eq 'review') {
                Write-Output ('Audit: {0}; status: {1}' -f $openRouterRow.run_id, $openRouterRow.status)
                Write-Output ('OPA: {0}' -f $openRouterRow.pep_receipt.reason)
            }
            Write-Output 'L0: no infrastructure actions were executed.'
        }
        if ($openRouterFallback -isnot [bool] -or $openRouterFallback -or $openRouterRow.llm.provider -ne 'openrouter') {
            if (-not $Json) { Write-Output 'Model analysis unavailable; this is fallback, not a successful API analysis.' }
            $openRouterExit = 2
        } else { $openRouterExit = $openRouterChildExit }
    }
} catch {
    # Do not render exceptions: HTTP errors and credentials must never enter output.
    Write-Output $openRouterFailure
    $openRouterExit = 1
} finally {
    if ($null -eq $openRouterOldKey) { Remove-Item Env:AEGISOPS_LLM_API_KEY -ErrorAction SilentlyContinue }
    else { $env:AEGISOPS_LLM_API_KEY = $openRouterOldKey }
    if ($null -eq $openRouterOldEncoding) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
    else { $env:PYTHONIOENCODING = $openRouterOldEncoding }
    [Console]::OutputEncoding = $openRouterConsoleEncoding
    $openRouterSecret = $null
    $openRouterOldKey = $null
    $openRouterRuntimeKey = $null
    $openRouterStatus = $null
    Pop-Location
}
exit $openRouterExit
