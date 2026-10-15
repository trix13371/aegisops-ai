$ErrorActionPreference = 'Stop'
$uiRoot = Split-Path -Parent $PSScriptRoot
$uiPython = Join-Path $uiRoot '.venv/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $uiPython)) { throw 'Install the project with uv sync --frozen first.' }
Start-Process -FilePath $uiPython -ArgumentList ('"' + (Join-Path $PSScriptRoot 'dashboard-local.py') + '"') -WorkingDirectory $uiRoot -WindowStyle Hidden
