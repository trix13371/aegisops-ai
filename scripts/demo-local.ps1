$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path -Parent $PSScriptRoot
$demoPython = Join-Path $demoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $demoPython)) { throw 'Install the project with uv sync --frozen first.' }
Push-Location -LiteralPath $demoRoot
try {
    $demoOutput = & $demoPython scripts/with-local-env.py aegisops eval
    if ($LASTEXITCODE -ne 0) { throw 'Demo failed. Check local PostgreSQL and the project installation.' }
    $demoReport = ($demoOutput -join "`n") | ConvertFrom-Json
    if (-not $demoReport.passed) { throw 'Fixture evaluation did not pass.' }
    Write-Host 'AegisOps L0: fixture mechanics demo (no paid API calls)'
    Write-Host ('Scenarios: {0}; incidents: {1}; negative cases: {2}' -f $demoReport.scenarios.Count, $demoReport.true_positives, $demoReport.true_negatives)
    Write-Host ('Policy probes rejected: {0}/{1}' -f $demoReport.adversarial_policy_probe_denials.numerator, $demoReport.adversarial_policy_probe_denials.denominator)
    Write-Host ('Canary leaks: {0}; executions: {1}' -f $demoReport.canary_leaks, $demoReport.execution_count)
    Write-Host 'Ten scenario results have been committed to local audit.'
    Write-Host ('Example audit ID: {0}' -f $demoReport.scenarios[0].audit_run_ids[0])
    Write-Host 'This verifies fixture mechanics, not model quality or production readiness.'
} finally {
    Pop-Location
}
