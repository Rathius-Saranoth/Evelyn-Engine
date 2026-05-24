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

# 3. Check Qwen3 TTS Server
$QwenPort = 5050
if (Get-NetTCPConnection -LocalPort $QwenPort -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "✅ [Qwen3 TTS] Server is running on port $QwenPort." -ForegroundColor Green
} else {
    Write-Host "❌ [Qwen3 TTS] Server is NOT running." -ForegroundColor Red
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

# 5. Check ComfyUI
$ComfyUIPort = 8188
if (Get-NetTCPConnection -LocalPort $ComfyUIPort -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "✅ [ComfyUI] is running on port $ComfyUIPort." -ForegroundColor Green
} else {
    Write-Host "❌ [ComfyUI] is NOT running." -ForegroundColor Red
    $AllClear = $false
}

# 6. Check Obsidian
if (Get-Process -Name "Obsidian" -ErrorAction SilentlyContinue) {
    Write-Host "✅ [Obsidian] is running." -ForegroundColor Green
} else {
    Write-Host "❌ [Obsidian] is NOT running." -ForegroundColor Red
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
