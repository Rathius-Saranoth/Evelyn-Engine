# start_image_server.ps1 — Standalone Image Server Startup Script for ricky-pc / Windows Hosts
# date created: 2026-08-11

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseDir = Split-Path -Parent $ScriptDir
$ImageDir = Join-Path $BaseDir "services\image"

Set-Location $ImageDir

# Activate virtual environment if present
$VenvPath = Join-Path $ImageDir "venv\Scripts\Activate.ps1"
$GlobalVenvPath = Join-Path $BaseDir "venv\Scripts\Activate.ps1"

if (Test-Path $VenvPath) {
    Write-Host "[IMAGE] Activating virtualenv at $VenvPath..." -ForegroundColor Green
    & $VenvPath
} elseif (Test-Path $GlobalVenvPath) {
    Write-Host "[IMAGE] Activating virtualenv at $GlobalVenvPath..." -ForegroundColor Green
    & $GlobalVenvPath
}

if (-not $env:IMAGE_SERVER_HOST) { $env:IMAGE_SERVER_HOST = "0.0.0.0" }
if (-not $env:IMAGE_SERVER_PORT) { $env:IMAGE_SERVER_PORT = "5055" }
if (-not $env:IMAGE_SERVER_UNLOAD_TIMEOUT) { $env:IMAGE_SERVER_UNLOAD_TIMEOUT = "120" }

Write-Host "[IMAGE] Starting FLUX.1 [schnell] Image Server on $env:IMAGE_SERVER_HOST:$env:IMAGE_SERVER_PORT..." -ForegroundColor Cyan
python image_server.py
