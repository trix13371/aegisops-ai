#Requires -Version 5.1
<#
.SYNOPSIS
  Connect the assistant to a provider/model and persist the selection.
.DESCRIPTION
  Interactive or parameterized provider selection. Reuses an existing stored
  key when present, otherwise prompts for a new key and stores it encrypted.
  Performs a metadata preflight before any disk changes.
#>
[CmdletBinding()]
param(
    [ValidateSet('openrouter','deepseek','gemini','openai')]
    [string]$Provider
)

$ErrorActionPreference = 'Stop'

$oldPythonIO = $env:PYTHONIOENCODING
$oldConsoleOut = $null
try { $oldConsoleOut = [Console]::OutputEncoding } catch { $oldConsoleOut = $null }
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch { }

$secureKey = $null
$plainKey = $null
$bstr = [IntPtr]::Zero
$tmpPath = $null
$storeDir = $null

try {
    $common = Join-Path $PSScriptRoot 'assistant-connection.ps1'
    . $common
    Assert-AssistantPath -Path $common | Out-Null

    $root = Get-AssistantRoot
    Assert-AssistantPath -Path $root | Out-Null
    $store = Get-AssistantStore
    Assert-AssistantPath -Path $store | Out-Null

    $catalog = Get-AssistantCatalog
    if (-not $catalog -or $catalog.Count -lt 1) { throw 'catalog-empty' }

    if (-not $Provider) {
        Write-Host 'Select a provider:'
        for ($i = 0; $i -lt $catalog.Count; $i++) {
            Write-Host ('  {0}. {1}' -f ($i + 1), $catalog[$i].label)
        }
        $choice = Read-Host 'Enter number'
        $idx = 0
        if (-not [int]::TryParse($choice, [ref]$idx) -or $idx -lt 1 -or $idx -gt $catalog.Count) {
            throw 'invalid-selection'
        }
        $Provider = [string]$catalog[$idx - 1].provider
    }

    $selection = Get-AssistantSelection -Action selected -Provider $Provider
    if (-not $selection -or -not $selection.model) { throw 'selection-failed' }
    $model = [string]$selection.model

    $keyPath = Get-AssistantKeyPath -Provider $Provider
    Assert-AssistantPath -Path $keyPath | Out-Null

    $existingKey = $null
    if (Test-Path -LiteralPath $keyPath -PathType Leaf) {
        $existingKey = Read-AssistantKey -Path $keyPath
    }

    if ($existingKey) {
        $plainKey = $existingKey
    }
    else {
        $secureKey = Read-Host -AsSecureString ('Enter API key for {0}' -f $Provider)
        if (-not $secureKey -or $secureKey.Length -lt 8 -or $secureKey.Length -gt 4096) {
            throw 'invalid-key'
        }
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        if (-not $plainKey) { throw 'invalid-key' }
        foreach ($ch in $plainKey.ToCharArray()) {
            $code = [int][char]$ch
            if ($code -lt 33 -or $code -gt 126) { throw 'invalid-key' }
        }
        if ($Provider -ne 'openrouter' -and $plainKey.StartsWith('sk-or-')) {
            throw 'invalid-key'
        }
        $devKeyPath = Join-Path (Join-Path (Join-Path $root '..') '..') 'work/agent-credentials/openrouter.dpapi'
        try {
            $devKeyPath = [System.IO.Path]::GetFullPath($devKeyPath)
            if (Test-Path -LiteralPath $devKeyPath -PathType Leaf) {
                $devKey = Read-AssistantKey -Path $devKeyPath
                if ($devKey -and $devKey -eq $plainKey) { throw 'invalid-key' }
                $devKey = $null
            }
        }
        catch {
            if ($_.Exception -and $_.Exception.Message -eq 'invalid-key') { throw }
            throw
        }
    }

    $ok = Test-AssistantConnection -Provider $Provider -Key $plainKey
    if (-not $ok) { throw 'preflight-failed' }

    $storeDir = Assert-AssistantStore -Create
    Assert-AssistantPath -Path $storeDir | Out-Null

    if (-not $existingKey) {
        $encrypted = ConvertFrom-SecureString $secureKey
        $fs = [System.IO.File]::Open($keyPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
        try {
            $writer = [System.IO.StreamWriter]::new($fs, [System.Text.UTF8Encoding]::new($false))
            try { $writer.Write($encrypted) } finally { $writer.Dispose() }
        }
        finally { $fs.Dispose() }
    }

    $configPath = $store
    Assert-AssistantPath -Path $configPath | Out-Null
    $payload = [ordered]@{ provider = $Provider; model = $model } | ConvertTo-Json -Compress
    $tmpPath = Join-Path $storeDir ('active.{0}.tmp' -f [Guid]::NewGuid().ToString('N'))
    Assert-AssistantPath -Path $tmpPath | Out-Null
    $tfs = [System.IO.File]::Open($tmpPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
    try {
        $tw = [System.IO.StreamWriter]::new($tfs, [System.Text.UTF8Encoding]::new($false))
        try { $tw.Write($payload) } finally { $tw.Dispose() }
    }
    finally { $tfs.Dispose() }
    if (Test-Path -LiteralPath $configPath -PathType Leaf) {
        [System.IO.File]::Replace($tmpPath, $configPath, $null)
    }
    else {
        [System.IO.File]::Move($tmpPath, $configPath)
    }
    $tmpPath = $null

    Write-Host ('Connected: {0} / {1}' -f $Provider, $model)
    Write-Host 'Note: provider account limits apply; budgets for direct providers are not verified here.'
}
catch {
    [Console]::Error.WriteLine('Connection setup failed. Verify provider, key, and network, then retry.')
    exit 1
}
finally {
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        $bstr = [IntPtr]::Zero
    }
    if ($secureKey) { $secureKey.Dispose(); $secureKey = $null }
    $plainKey = $null
    if ($tmpPath -and $storeDir) {
        try {
            $fullTmp = [System.IO.Path]::GetFullPath($tmpPath)
            $fullStore = [System.IO.Path]::GetFullPath($storeDir)
            if ($fullTmp.StartsWith($fullStore, [System.StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $fullTmp -PathType Leaf)) {
                Remove-Item -LiteralPath $fullTmp -Force -ErrorAction SilentlyContinue
            }
        }
        catch { }
    }
    $env:PYTHONIOENCODING = $oldPythonIO
    if ($null -ne $oldConsoleOut) { try { [Console]::OutputEncoding = $oldConsoleOut } catch { } }
}
