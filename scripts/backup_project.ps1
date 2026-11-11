$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python.exe -ErrorAction Stop).Source }
& $python (Join-Path $PSScriptRoot "backup.py")
if ($LASTEXITCODE -ne 0) { throw "Backup failed." }
