$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $projectRoot "app.py"
$dataDir = Join-Path $projectRoot "data"
$logFile = Join-Path $dataDir "editor.log"
$errorLog = Join-Path $dataDir "editor-error.log"
$port = 8765

New-Item -ItemType Directory -Path $dataDir -Force | Out-Null

$targetIds = [System.Collections.Generic.HashSet[int]]::new()
$appPattern = [regex]::Escape($appPath)

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @("python.exe", "pythonw.exe") -and $_.CommandLine -match $appPattern } |
    ForEach-Object { [void]$targetIds.Add([int]$_.ProcessId) }

Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)" -ErrorAction SilentlyContinue
        if ($process -and $process.Name -in @("python.exe", "pythonw.exe") -and $process.CommandLine -match $appPattern) {
            [void]$targetIds.Add([int]$process.ProcessId)
        }
    }

foreach ($processId in $targetIds) {
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
}

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 150
}

if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    "$(Get-Date -Format s) Port $port is still occupied by another process." | Set-Content -LiteralPath $errorLog -Encoding UTF8
    exit 1
}

$python = (Get-Command python.exe -ErrorAction Stop).Source
Start-Process -FilePath $python -ArgumentList @("""$appPath""") -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError $errorLog
