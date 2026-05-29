# ollama_serve_debug.ps1
# date created: 2026-05-28 19:35:26
# date modified: 2026-05-28 19:35:46
# tags: 

# Launches Ollama with debugging enabled
$env:OLLAMA_DEBUG = "1"; ollama serve
