# check_evelyn_status.ps1
# date created: 2026-02-12 20:07:43
# date modified: 2026-07-03 18:33:00
# tags: #status, #monitor, #processes, #windows, #diagnostics

# Evelyn Startup Status Checker
# Use the "Start Evelyn Services" VS Code Task to launch these apps!

$AllClear = $true

Write-Host "=============================" -ForegroundColor Cyan
Write-Host "  Evelyn System Diagnostics  " -ForegroundColor Cyan
Write-Host "=============================" -ForegroundColor Cyan

# 1. Check Ollama
if (Get-Process -Name "ollama" -ErrorAction SilentlyContinue) {
    Write-Host "✅ [Ollama] is running." -ForegroundColor Green
} else {
    Write-Host "❌ [Ollama] is NOT running." -ForegroundColor Red
    $AllClear = $false
}

# 2. Check Tailscale
# Checking if tailscale serve is running by looking for tailscale process serving 8080 or just tailscale itself
if (Get-Process -Name "tailscaled" -ErrorAction SilentlyContinue) {
    Write-Host "✅ [Tailscale] proxy is running." -ForegroundColor Green
} else {
    Write-Host "❌ [Tailscale] proxy is NOT running." -ForegroundColor Red
    $AllClear = $false
}

# 3. Check Chatterbox TTS Server
$TTSPort = 5050
if (Get-NetTCPConnection -LocalPort $TTSPort -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "✅ [Chatterbox TTS] Server is running on port $TTSPort." -ForegroundColor Green
} else {
    Write-Host "❌ [Chatterbox TTS] Server is NOT running." -ForegroundColor Red
    $AllClear = $false
}

# 4. Check Evelyn Server
$EvelynPort = 7860
if (Get-NetTCPConnection -LocalPort $EvelynPort -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "✅ [Evelyn Server] is running on port $EvelynPort." -ForegroundColor Green
} else {
    Write-Host "❌ [Evelyn Server] is NOT running." -ForegroundColor Red
    $AllClear = $false
}

# 5. Check Image Server
$ImagePort = 5055
if (Get-NetTCPConnection -LocalPort $ImagePort -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "✅ [Image Server] is running on port $ImagePort." -ForegroundColor Green
} else {
    Write-Host "❌ [Image Server] is NOT running." -ForegroundColor Red
    $AllClear = $false
}


Write-Host "-----------------------------" -ForegroundColor Cyan
if ($AllClear) {
    Write-Host "All systems are GO! Evelyn is ready." -ForegroundColor Green
} else {
    Write-Host "Some systems are offline." -ForegroundColor Yellow
    Write-Host "Press Ctrl+Shift+P, type 'Run Task', and select 'Start Evelyn Services'." -ForegroundColor Yellow
}
Write-Host "=============================`n" -ForegroundColor Cyan
