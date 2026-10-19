# Assistant connection helpers for AegisOps on Windows.
# Dot-source only. Does not mutate caller environment or global keys on load.
# ASCII-only messages for Windows PowerShell 5.1 (no BOM).

Set-StrictMode -Version Latest

function Get-AssistantRoot {
    [CmdletBinding()]
    param()
    $repo = Split-Path -Parent $PSScriptRoot
    $candidate = Join-Path $repo '..\..\work\aegisops-runtime-credentials'
    return [System.IO.Path]::GetFullPath($candidate)
}

function Get-AssistantStore {
    [CmdletBinding()]
    param()
    $root = Get-AssistantRoot
    return (Join-Path $root 'assistant.json')
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

    $repo = Split-Path -Parent $PSScriptRoot
    $script = Join-Path $repo 'scripts\assistant-selection.py'
    $python = Join-Path $repo '.venv\Scripts\python.exe'

    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'Assistant settings unavailable'
    }
    if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
        throw 'Assistant settings unavailable'
    }

    $arguments = @($script, $Action)
    switch ($Action) {
        'selected' {
            if ([string]::IsNullOrEmpty($Provider)) {
                throw 'Assistant settings unavailable'
            }
            $arguments += $Provider
        }
        'resolve' {
            if ([string]::IsNullOrEmpty($Mode) -or [string]::IsNullOrEmpty($Id)) {
                throw 'Assistant settings unavailable'
            }
            $arguments += $Mode
            $arguments += $Id
        }
    }

    $raw = & $python @arguments 2>$null
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw 'Assistant settings unavailable'
    }

    try {
        $parsed = $raw | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw 'Assistant settings unavailable'
    }

    if ($null -eq $parsed) {
        throw 'Assistant settings unavailable'
    }
    if ($parsed.PSObject.Properties.Name -contains 'error') {
        throw 'Assistant settings unavailable'
    }

    $catalog = Get-AssistantCatalog
    $allowed = @{}
    foreach ($entry in $catalog) {
        $allowed[$entry.provider] = $entry.model
    }

    $provider = $parsed.provider
    $model = $parsed.model
    if ([string]::IsNullOrEmpty($provider) -or [string]::IsNullOrEmpty($model)) {
        throw 'Assistant settings unavailable'
    }
    if (-not $allowed.ContainsKey($provider)) {
        throw 'Assistant settings unavailable'
    }
    if ($allowed[$provider] -ne $model) {
        throw 'Assistant settings unavailable'
    }

    return [pscustomobject]@{ provider = $provider; model = $model }
}

function Get-AssistantCatalog {
    [CmdletBinding()]
    param()

    $repo = Split-Path -Parent $PSScriptRoot
    $script = Join-Path $repo 'scripts\assistant-selection.py'
    $python = Join-Path $repo '.venv\Scripts\python.exe'

    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'Assistant settings unavailable'
    }
    if (-not (Test-Path -LiteralPath $script -PathType Leaf)) {
        throw 'Assistant settings unavailable'
    }

    $raw = & $python $script 'catalog' 2>$null
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw 'Assistant settings unavailable'
    }

    try {
        $parsed = $raw | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw 'Assistant settings unavailable'
    }

    if ($null -eq $parsed -or $null -eq $parsed.providers) {
        throw 'Assistant settings unavailable'
    }

    $result = @()
    foreach ($entry in $parsed.providers) {
        $provider = $entry.provider
        $label = $entry.label
        $model = $entry.model
        if ([string]::IsNullOrEmpty($provider) -or [string]::IsNullOrEmpty($model)) {
            throw 'Assistant settings unavailable'
        }
        $result += [pscustomobject]@{ provider = $provider; label = $label; model = $model }
    }

    return $result
}

function Get-AssistantKeyPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Provider
    )

    $catalog = Get-AssistantCatalog
    $known = $false
    foreach ($entry in $catalog) {
        if ($entry.provider -eq $Provider) {
            $known = $true
            break
        }
    }
    if (-not $known) {
        throw 'Assistant settings unavailable'
    }

    $root = Get-AssistantRoot
    return (Join-Path $root ($Provider + '.dpapi'))
}

function Read-AssistantKey {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $full = Assert-AssistantPath -Path $Path

    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
        throw 'Assistant settings unavailable'
    }

    $item = Get-Item -LiteralPath $full -Force
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw 'Assistant settings unavailable'
    }

    $item = Get-Item -LiteralPath $full -Force
    if ($item.Length -gt 32768) {
        throw 'Assistant settings unavailable'
    }
    $encrypted = Get-Content -LiteralPath $full -Raw -Encoding UTF8
    if ([string]::IsNullOrEmpty($encrypted)) {
        throw 'Assistant settings unavailable'
    }
    if ($encrypted.Length -lt 1 -or $encrypted.Length -gt 32768) {
        throw 'Assistant settings unavailable'
    }

    $secure = $null
    $bstr = [IntPtr]::Zero
    $plain = $null
    try {
        $secure = ConvertTo-SecureString -String $encrypted -ErrorAction Stop
        $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $plain = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    catch {
        throw 'Assistant settings unavailable'
    }
    finally {
        if ($bstr -ne [IntPtr]::Zero) {
            [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        }
        if ($null -ne $secure) {
            $secure.Dispose()
        }
    }

    if ([string]::IsNullOrEmpty($plain)) {
        throw 'Assistant settings unavailable'
    }
    if ($plain.Length -lt 8 -or $plain.Length -gt 4096) {
        throw 'Assistant settings unavailable'
    }
    foreach ($ch in $plain.ToCharArray()) {
        $code = [int][char]$ch
        if ($code -lt 33 -or $code -gt 126) {
            throw 'Assistant settings unavailable'
        }
    }

    return $plain
}

function Test-AssistantConnection {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Provider,

        [Parameter(Mandatory = $true)]
        [string]$Key
    )

    if ([string]::IsNullOrEmpty($Key)) {
        throw 'Assistant settings unavailable'
    }

    $headers = @{}
    $uri = $null

    switch ($Provider) {
        'openrouter' {
            $uri = 'https://openrouter.ai/api/v1/key'
            $headers['Authorization'] = 'Bearer ' + $Key
        }
        'deepseek' {
            $uri = 'https://api.deepseek.com/models'
            $headers['Authorization'] = 'Bearer ' + $Key
        }
        'openai' {
            $uri = 'https://api.openai.com/v1/models/gpt-5.4-mini'
            $headers['Authorization'] = 'Bearer ' + $Key
        }
        'gemini' {
            $uri = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash'
            $headers['x-goog-api-key'] = $Key
        }
        default {
            throw 'Assistant settings unavailable'
        }
    }

    try {
        $response = Invoke-RestMethod -Method Get -Uri $uri -Headers $headers -TimeoutSec 30 -MaximumRedirection 0 -ErrorAction Stop
    }
    catch {
        throw 'Assistant settings unavailable'
    }

    switch ($Provider) {
        'openrouter' {
            if ($null -eq $response.data) {
                throw 'Assistant settings unavailable'
            }
            if (($response.data.PSObject.Properties.Name -contains 'limit_reset') -and $response.data.limit_reset) {
                throw 'Assistant settings unavailable'
            }

            $limit = $response.data.limit
            $remaining = $response.data.limit_remaining

            if ($null -eq $limit -or $null -eq $remaining) {
                throw 'Assistant settings unavailable'
            }
            if ($limit -is [bool] -or $remaining -is [bool]) {
                throw 'Assistant settings unavailable'
            }
            if (-not (($limit -is [int]) -or ($limit -is [long]) -or ($limit -is [double]) -or ($limit -is [decimal]) -or ($limit -is [float]))) {
                throw 'Assistant settings unavailable'
            }
            if (-not (($remaining -is [int]) -or ($remaining -is [long]) -or ($remaining -is [double]) -or ($remaining -is [decimal]) -or ($remaining -is [float]))) {
                throw 'Assistant settings unavailable'
            }

            $limitValue = [double]$limit
            $remainingValue = [double]$remaining

            if ([double]::IsNaN($limitValue) -or [double]::IsInfinity($limitValue)) {
                throw 'Assistant settings unavailable'
            }
            if ([double]::IsNaN($remainingValue) -or [double]::IsInfinity($remainingValue)) {
                throw 'Assistant settings unavailable'
            }
            if ($limitValue -le 0) {
                throw 'Assistant settings unavailable'
            }
            if ($limitValue -gt 5) {
                throw 'Assistant settings unavailable'
            }
            if ($remainingValue -lt 0.10) {
                throw 'Assistant settings unavailable'
            }
        }
        'deepseek' {
            $catalog = Get-AssistantCatalog
            $expectedModel = $null
            foreach ($entry in $catalog) {
                if ($entry.provider -eq 'deepseek') {
                    $expectedModel = $entry.model
                    break
                }
            }
            if ([string]::IsNullOrEmpty($expectedModel)) {
                throw 'Assistant settings unavailable'
            }

            $ids = @()
            foreach ($item in $response.data) {
                $ids += $item.id
            }
            if ($ids -notcontains $expectedModel) {
                throw 'Assistant settings unavailable'
            }
            if ($ids -notcontains 'deepseek-flash') {
                throw 'Assistant settings unavailable'
            }
        }
        'openai' {
            $catalog = Get-AssistantCatalog
            $expectedModel = $null
            foreach ($entry in $catalog) {
                if ($entry.provider -eq 'openai') {
                    $expectedModel = $entry.model
                    break
                }
            }
            if ([string]::IsNullOrEmpty($expectedModel)) {
                throw 'Assistant settings unavailable'
            }
            if ($response.id -ne $expectedModel) {
                throw 'Assistant settings unavailable'
            }
        }
        'gemini' {
            $catalog = Get-AssistantCatalog
            $expectedModel = $null
            foreach ($entry in $catalog) {
                if ($entry.provider -eq 'gemini') {
                    $expectedModel = $entry.model
                    break
                }
            }
            if ([string]::IsNullOrEmpty($expectedModel)) {
                throw 'Assistant settings unavailable'
            }
            if ($response.name -ne ('models/' + $expectedModel)) {
                throw 'Assistant settings unavailable'
            }
        }
    }

    return $true
}

function Assert-AssistantStore {
    [CmdletBinding()]
    param(
        [switch]$Create
    )

    $root = Get-AssistantRoot
    $full = Assert-AssistantPath -Path $root

    if (Test-Path -LiteralPath $full) {
        $item = Get-Item -LiteralPath $full -Force
        if (-not $item.PSIsContainer) {
            throw 'Assistant settings unavailable'
        }
        if ($Create) {
            $current = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
            $icacls = & (Join-Path $env:SystemRoot 'System32/icacls.exe') $full /inheritance:r /grant:r ($current + ':(OI)(CI)F') 2>$null
            if ($LASTEXITCODE -ne 0) {
                throw 'Assistant settings unavailable'
            }
        }
        return $full
    }

    if (-not $Create) {
        throw 'Assistant settings unavailable'
    }

    $parent = Split-Path -Parent $full
    if ([string]::IsNullOrEmpty($parent)) {
        throw 'Assistant settings unavailable'
    }
    Assert-AssistantPath -Path $parent | Out-Null

    New-Item -ItemType Directory -Path $full -Force | Out-Null

    $current = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $icacls = & (Join-Path $env:SystemRoot 'System32/icacls.exe') $full /inheritance:r /grant:r ($current + ':(OI)(CI)F') 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'Assistant settings unavailable'
    }

    return $full
}

function Assert-AssistantPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ([string]::IsNullOrEmpty($Path)) {
        throw 'Assistant settings unavailable'
    }

    $full = $null
    try {
        $full = [System.IO.Path]::GetFullPath($Path)
    }
    catch {
        throw 'Assistant settings unavailable'
    }

    if ([string]::IsNullOrEmpty($full)) {
        throw 'Assistant settings unavailable'
    }
    if (-not [System.IO.Path]::IsPathRooted($full)) {
        throw 'Assistant settings unavailable'
    }
    if ($full.StartsWith('\\')) {
        throw 'Assistant settings unavailable'
    }

    $root = [System.IO.Path]::GetPathRoot($full)
    if ($root.StartsWith('\\')) {
        throw 'Assistant settings unavailable'
    }

    $parts = $full.Substring($root.Length).Split(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )

    $current = $root
    $candidates = @()
    foreach ($part in $parts) {
        if ([string]::IsNullOrEmpty($part)) {
            continue
        }
        $current = Join-Path $current $part
        $candidates += $current
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            $item = Get-Item -LiteralPath $candidate -Force
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw 'Assistant settings unavailable'
            }
        }
    }

    return $full
}
