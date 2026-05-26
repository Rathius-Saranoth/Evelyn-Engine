# wait_for_ollama.ps1
# date created: 2026-03-25 19:55:23
# date modified: 2026-05-25 19:50:52
# tags: #helper, #ollama, #startup, #network, #port

# Waits until Ollama's TCP port (11434) is accepting connections.
# Used as a startup gate so other services only launch after Ollama is ready.

Write-Host "Waiting for Ollama to be ready on port 11434..."
while (-not (Test-NetConnection -ComputerName localhost -Port 11434 -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    Start-Sleep -Seconds 1
}
Write-Host "Ollama is ready."
