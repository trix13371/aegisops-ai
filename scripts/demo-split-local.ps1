$ErrorActionPreference = 'Stop'
$splitRoot = Split-Path -Parent $PSScriptRoot
$splitPython = Join-Path $splitRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $splitPython)) { throw 'Install the project with uv sync --frozen first.' }
Push-Location -LiteralPath $splitRoot
try {
    & $splitPython scripts/collector-identity-local.py check
    if ($LASTEXITCODE -ne 0) { throw 'Enroll the local collector with scripts/collector-identity-local.py init first.' }
    & $splitPython scripts/cluster-local.py refresh-reader
    if ($LASTEXITCODE -ne 0) { throw 'Local read-only kind identity could not be refreshed.' }
    $splitCollected = & $splitPython -m aegisops.collector.cli collect
    if ($LASTEXITCODE -ne 0) { throw 'Collector rejected the local environment or packet.' }
    $splitBatch = ($splitCollected -join "`n") | ConvertFrom-Json
    $splitOutput = & $splitPython scripts/with-local-env.py aegisops-control consume $splitBatch.batch_id
    $splitCode = $LASTEXITCODE
    try { $splitDelivery = ($splitOutput -join "`n") | ConvertFrom-Json } catch { throw 'Control Plane did not return a committed receipt. Inspect the claim; do not retry automatically.' }
    if (-not $splitDelivery.receipt -or $splitDelivery.receipt.execution_performed -ne $false) { throw 'Unexpected Control Plane receipt.' }
    if ($splitCode -ne 0 -and $splitDelivery.receipt.status -eq 'completed') { throw 'Control Plane exit status disagrees with receipt.' }
    Write-Host 'AegisOps L0: two separate processes, signed local handoff, no paid API calls'
    Write-Host ('Batch: {0}; snapshots: {1}; incidents: {2}' -f $splitBatch.batch_id, $splitDelivery.receipt.snapshot_count, $splitDelivery.receipt.incident_count)
    Write-Host ('Status: {0}; replayed: {1}; executions: 0' -f $splitDelivery.receipt.status, $splitDelivery.replayed)
    Write-Host ('Audit ID: {0}' -f $splitDelivery.receipt.audit_run_ids[0])
    if ($splitDelivery.receipt.status -ne 'completed') {
        Write-Host 'Processing was blocked or failed. Inspect the saved audit and OPA/policy controls; this demo never resumes them.'
    }
} finally {
    Pop-Location
}
