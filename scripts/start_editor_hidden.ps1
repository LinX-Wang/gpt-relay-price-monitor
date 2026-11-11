﻿$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$appPath = Join-Path $projectRoot "app.py"
$dataDir = Join-Path $projectRoot "data"
$logFile = Join-Path $dataDir "editor.log"
$errorLog = Join-Path $dataDir "editor-error.log"
$port = 8765

New-Item -ItemType Directory -Path $dataDir -Force | Out-Null

function Show-Failure([string]$message) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [void][System.Windows.Forms.MessageBox]::Show($message, "价格监控编辑器", [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Error)
    } catch {
    }
}

function Resolve-Python {
    $candidates = @(
        (Join-Path $projectRoot ".venv/Scripts/python.exe"),
        (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            if ($candidate -like "*\WindowsApps\*") { continue }
            return $candidate
        }
    }
    return $null
}

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
    $message = "端口 $port 被其它程序占用，编辑器无法启动。请关闭占用该端口的程序后重试。"
    "$(Get-Date -Format s) Port $port is still occupied by another process." | Set-Content -LiteralPath $errorLog -Encoding UTF8
    Show-Failure $message
    exit 1
}

$python = Resolve-Python
if (-not $python) {
    $message = "没有找到 Python。请先安装 Python 3.10 或更高版本，然后重试。"
    "$(Get-Date -Format s) Python not found." | Set-Content -LiteralPath $errorLog -Encoding UTF8
    Show-Failure $message
    exit 1
}

Start-Process -FilePath $python -ArgumentList @("""$appPath""") -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError $errorLog

$editorUrl = "http://127.0.0.1:$port/"
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { break }
    Start-Sleep -Milliseconds 200
}

if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
    $detail = ""
    if (Test-Path -LiteralPath $errorLog) {
        $detail = (Get-Content -LiteralPath $errorLog -Raw -ErrorAction SilentlyContinue)
        if ($detail) { $detail = [Environment]::NewLine + [Environment]::NewLine + "错误详情：" + [Environment]::NewLine + $detail.Trim() }
    }
    $message = "编辑器启动失败（端口 $port 未监听）。" + $detail
    "$(Get-Date -Format s) Editor did not start on port $port." | Set-Content -LiteralPath $errorLog -Encoding UTF8
    Show-Failure $message
    exit 1
}

$edgeCandidates = @(
    (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
    (Join-Path $env:LocalAppData "Microsoft\Edge\Application\msedge.exe")
)
$edge = $edgeCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if ($edge) {
    Start-Process -FilePath $edge -ArgumentList @($editorUrl)
} else {
    Start-Process -FilePath $editorUrl
}
