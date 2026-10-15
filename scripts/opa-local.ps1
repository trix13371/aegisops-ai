param([ValidateSet('up','status','stop')][string]$Command='status')
$ErrorActionPreference='Stop'
$opaRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$opaBinary = [IO.Path]::GetFullPath((Join-Path $opaRoot '../../work/tooling/opa/opa.exe'))
$opaLocal = Join-Path $opaRoot '.local/authorization'
$opaStateFile = Join-Path $opaLocal 'process.json'
$opaExpectedHash = '0089fa7ad8cdea7ff9e56ff86fa9202462afb63ef74506e2b547a24b4a987ccb'

function Get-OwnedOpa {
    if (-not (Test-Path -LiteralPath $opaStateFile)) { return $null }
    $opaState = Get-Content -LiteralPath $opaStateFile -Raw | ConvertFrom-Json
    $opaProcess = Get-Process -Id $opaState.pid -ErrorAction SilentlyContinue
    if (-not $opaProcess) { return $null }
    if ($opaProcess.Path -ne $opaBinary -or $opaProcess.StartTime.ToUniversalTime().Ticks.ToString() -ne $opaState.startTicks) {
        throw 'Process ownership mismatch; refusing to manage this PID.'
    }
    return $opaProcess
}

if ($Command -eq 'stop') {
    $opaProcess = Get-OwnedOpa
    if ($opaProcess) { $opaProcess | Stop-Process }
    Write-Output 'OPA stopped; opa authorization now fails closed.'
    exit 0
}
if ($Command -eq 'status') {
    $opaProcess = Get-OwnedOpa
    Write-Output $(if ($opaProcess) { 'OPA process running on 127.0.0.1:18181 (verify mTLS readiness separately)' } else { 'OPA stopped' })
    exit 0
}
if ((Get-FileHash -LiteralPath $opaBinary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $opaExpectedHash) {
    throw 'OPA runtime digest mismatch.'
}
$opaPython = Join-Path $opaRoot '.venv/Scripts/python.exe'
Push-Location -LiteralPath $opaRoot
try {
    & $opaPython scripts/prepare-opa-policy.py --check
    if ($LASTEXITCODE -ne 0) { throw 'OPA policy metadata rejected.' }
    & $opaPython scripts/sync-assets.py --check
    if ($LASTEXITCODE -ne 0) { throw 'Packaged policy rejected.' }
    & $opaPython scripts/policy-release-local.py check
    if ($LASTEXITCODE -ne 0) { throw 'Signed policy release rejected.' }
    & $opaPython scripts/opa-control.py init
    if ($LASTEXITCODE -ne 0) { throw 'Emergency control rejected.' }
    $opaTlsDirectory = & $opaPython scripts/opa-tls-local.py paths
    if ($LASTEXITCODE -ne 0) { throw 'OPA TLS identity rejected. Issue with scripts/opa-tls-local.py first.' }
    if (Get-OwnedOpa) {
        & $opaPython scripts/wait-opa.py
        if ($LASTEXITCODE -ne 0) { throw 'Owned OPA is stale or insecure. Stop and restart it explicitly.' }
        Write-Output 'OPA already running with verified mTLS.'
        exit 0
    }
    $opaListener = Get-NetTCPConnection -LocalPort 18181 -State Listen -ErrorAction SilentlyContinue
    if ($opaListener) { throw 'OPA port already occupied; refusing to reuse an unknown service.' }
    $opaPolicy = Join-Path $opaRoot 'policies/opa'
    $opaArguments = @('run','--server','--addr=https://127.0.0.1:18181','--authorization=basic','--authentication=tls','--min-tls-version=1.3','--disable-telemetry','--log-level=error',
        ('--tls-cert-file="'+(Join-Path $opaTlsDirectory 'server.pem')+'"'),
        ('--tls-private-key-file="'+(Join-Path $opaTlsDirectory 'server-key.pem')+'"'),
        ('--tls-ca-cert-file="'+(Join-Path $opaTlsDirectory 'ca.pem')+'"'),
        ('"'+(Join-Path $opaPolicy 'decision.rego')+'"'),
        ('"'+(Join-Path $opaPolicy 'api_authz.rego')+'"'),
        ('"'+(Join-Path $opaPolicy 'data.json')+'"'))
    $opaProcess = Start-Process -FilePath $opaBinary -ArgumentList $opaArguments -WorkingDirectory $opaRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $opaLocal 'stdout.log') -RedirectStandardError (Join-Path $opaLocal 'stderr.log')
    Start-Sleep -Milliseconds 500
    $opaProcess.Refresh()
    if ($opaProcess.HasExited) { throw 'OPA failed to start; inspect local stderr.log.' }
    [IO.File]::WriteAllText($opaStateFile, (@{pid=$opaProcess.Id;startTicks=$opaProcess.StartTime.ToUniversalTime().Ticks.ToString()} | ConvertTo-Json))
    & $opaPython scripts/wait-opa.py
    if ($LASTEXITCODE -ne 0) { throw 'OPA started but mTLS readiness failed; no authorization granted.' }
    Write-Output 'OPA mTLS started on 127.0.0.1:18181; controls preserved (initial state is blocked).'
} finally { Pop-Location }
