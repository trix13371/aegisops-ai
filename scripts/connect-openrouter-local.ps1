# Store a NEW project runtime key, independently of the development helper key.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$connectionRoot = Split-Path -Parent $PSScriptRoot
$connectionDirectory = [IO.Path]::GetFullPath((Join-Path $connectionRoot '../../work/aegisops-runtime-credentials'))
$connectionFile = Join-Path $connectionDirectory 'openrouter.dpapi'
$developmentFile = Join-Path $connectionRoot '../../work/agent-credentials/openrouter.dpapi'
$connectionOldRuntime = $env:AEGISOPS_RUNTIME_LLM_API_KEY
$connectionInput = $null
$connectionPlain = $null
$developmentPlain = $null
$connectionExit = 1
try {
    if (Test-Path -LiteralPath $connectionFile) {
        Write-Output 'A project credential already exists; it was not overwritten.'
    } else {
        Write-Host 'AegisOps PROJECT runtime key (separate from the coding assistants).'
        Write-Host 'Paste a NEW OpenRouter key. Input is hidden. No model request will be made.'
        $connectionInput = Read-Host 'Project OpenRouter API key' -AsSecureString
        $connectionPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($connectionInput)
        try { $connectionPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($connectionPointer) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($connectionPointer) }
        if (-not $connectionPlain.StartsWith('sk-or-') -or $connectionPlain.Length -gt 4096 -or
            $connectionPlain -match '[^\x21-\x7e]') { throw 'Invalid credential' }
        if (Test-Path -LiteralPath $developmentFile) {
            $developmentSecure = ConvertTo-SecureString ([IO.File]::ReadAllText($developmentFile).Trim())
            try {
                $developmentPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($developmentSecure)
                try { $developmentPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($developmentPointer) }
                finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($developmentPointer) }
            } finally { $developmentSecure.Dispose() }
        }
        if ($connectionPlain -ceq $developmentPlain -or $connectionPlain -ceq $env:AEGISOPS_LLM_API_KEY) {
            Write-Output 'This is the development key. Create a DIFFERENT key for the project.'
        } else {
            $env:AEGISOPS_RUNTIME_LLM_API_KEY = $connectionPlain
            & (Join-Path $PSScriptRoot 'openrouter-local.ps1') -CheckOnly
            if ($LASTEXITCODE -ne 0) { throw 'Preflight failed' }
            New-Item -ItemType Directory -Path $connectionDirectory -Force | Out-Null
            if ((Get-Item -LiteralPath $connectionDirectory).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Unexpected credential directory'
            }
            $connectionUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
            $connectionAcl = & "$env:SystemRoot/System32/icacls.exe" $connectionDirectory /inheritance:r /grant:r "${connectionUser}:(OI)(CI)F" 2>&1
            if ($LASTEXITCODE -ne 0) { throw 'Credential directory access rejected' }
            $connectionEncrypted = ConvertFrom-SecureString $connectionInput
            $connectionStream = [IO.File]::Open($connectionFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try {
                $connectionBytes = [Text.Encoding]::UTF8.GetBytes($connectionEncrypted)
                $connectionStream.Write($connectionBytes, 0, $connectionBytes.Length)
            } finally { $connectionStream.Dispose() }
            Write-Output 'Connected: separate PROJECT runtime credential saved for this Windows user.'
            $connectionExit = 0
        }
    }
} catch {
    Write-Output 'Project connection rejected; no existing credential was replaced. Check the key, total limit and Windows access.'
} finally {
    if ($null -eq $connectionOldRuntime) { Remove-Item Env:AEGISOPS_RUNTIME_LLM_API_KEY -ErrorAction SilentlyContinue }
    else { $env:AEGISOPS_RUNTIME_LLM_API_KEY = $connectionOldRuntime }
    if ($connectionInput) { $connectionInput.Dispose() }
    $connectionPlain = $null
    $developmentPlain = $null
    $connectionOldRuntime = $null
}
exit $connectionExit
