$GeneratorProcess = Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%generate_vault_map.py%'"
if ($GeneratorProcess) {
    Write-Host "Monitoring Vault Map Generator (PID: $($GeneratorProcess.ProcessId))..."
    Wait-Process -Id $GeneratorProcess.ProcessId
    Write-Host "Vault Map Generation finished." -ForegroundColor Green
}
else {
    Write-Host "Vault Map Generation is not running or already finished." -ForegroundColor Yellow
}

Write-Host "Waiting 10 seconds before starting OpenWebUI..."
Start-Sleep -Seconds 10

# Start OpenWebUI in the background
$OpenWebUIUrl = "http://localhost:8080/health"
$PythonPath = "C:\Users\ricky\AppData\Local\Programs\Python\Python311\Scripts\open-webui.exe"
$env:WEBUI_SECRET_KEY = Get-Content -Path "C:\Projects\LocalAI\.webui_secret_key" -Raw
$env:CORS_ALLOW_ORIGIN = "https://localhost;https://192.168.1.125;https://ricky-pc.tail0e161b.ts.net;https://rickys-lenovo-tab-k-11.tail0e161b.ts.net;https://rickys-pixel-9-pro.tail0e161b.ts.net"
$env:USER_AGENT = "Evelyn/1.0"

Write-Host "Starting OpenWebUI in the background..."
Start-Process -FilePath $PythonPath -ArgumentList "serve" -WindowStyle Hidden

Write-Host "Waiting for OpenWebUI to become responsive..."
$isReady = $false
for ($i = 0; $i -lt 30; $i++) {
    try {
        $response = Invoke-WebRequest -Uri $OpenWebUIUrl -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            $isReady = $true
            break
        }
    }
    catch {
        # Ignore and wait
    }
    Start-Sleep -Seconds 5
}

if (-not $isReady) {
    Write-Host "Error: OpenWebUI did not start in time. Aborting." -ForegroundColor Red
    exit 1
}

Write-Host "OpenWebUI is running. Starting Ingest Script..." -ForegroundColor Green
python "c:\Projects\LocalAI\Evelyn\tools\ingest_gists.py"

Write-Host "Ingest complete! You can manually shut down OpenWebUI using your task manager when ready." -ForegroundColor Cyan
