---
description: How to check and start the core services for the Evelyn ecosystem
---

# Service Startup Workflow

This workflow describes how to initialize the required backends for Evelyn's functionality.

## 1. Verify Active Services

// turbo
Check if the following processes are running:

- `ollama.exe` (Port 11434)
- `python.exe` running `qwen_tts_server.py` (Port 8019)
- `python.exe` running `evelyn_server.py` (Port 7860)
- `python.exe` (ComfyUI, Port 8188)
- `obsidian.exe`

## 2. Manual Startup (via VS Code Tasks)

If a service is missing, trigger the corresponding task from `.vscode/tasks.json`:

1. **Run Ollama**
2. **Run Tailscale**
3. **Run Qwen3 TTS**
4. **Run Evelyn Server** (`python evelyn_server.py` in `C:\Projects\LocalAI`)
5. **Run ComfyUI**
6. **Run Obsidian**

## 3. All-in-One Startup

// turbo
Run the consolidated task: "Start Evelyn Services". This will launch all dependencies in parallel.

## 4. Access

- Local: http://localhost:7860
- Tailscale: http://ricky-pc.tail0e161b.ts.net:7860
- Set `EVELYN_API_KEY` env var before starting the server for auth.
