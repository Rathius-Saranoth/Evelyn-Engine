# stop_evelyn_services.ps1
# Graceful shutdown script for Evelyn services

# 1. Send graceful shutdown request to Evelyn Server to pause active research and background tasks
try {
    $apiKey = $env:EVELYN_API_KEY
    $headers = @{}
    if ($apiKey) { $headers["X-API-Key"] = $apiKey }
    Write-Host "Sending graceful shutdown request to Evelyn Server..."
    Invoke-RestMethod -Uri "http://localhost:8080/shutdown" -Method Post -Headers $headers -TimeoutSec 3 -ErrorAction SilentlyContinue | Out-Null
    Start-Sleep -Seconds 2
} catch {
    # Server may already be stopped or unreachable
}

# 2. Terminate remaining processes cleanly
Write-Host "Stopping service processes..."
Get-CimInstance Win32_Process | Where-Object { 
    $_.CommandLine -match 'evelyn_server\.py|tts_server\.py|image_server\.py|ollama\.exe|llama-server\.exe|research_engine\.py' -and $_.ProcessName -notmatch 'Code\.exe' 
} | Invoke-CimMethod -MethodName Terminate | Out-Null

Write-Host "Evelyn Core Services stopped cleanly."
