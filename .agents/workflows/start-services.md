---
description: How to check and start the 6 core services for the Evelyn ecosystem
---

# Service Startup Workflow

This workflow describes how to initialize the required backends for Evelyn's functionality.

## 1. Verify Active Services

// turbo
Check if the following processes are running:

- `ollama.exe` (Port 11434)
- `python.exe` running `qwen_tts_server.py`
- `open-webui.exe` (Port 8080)
- `python.exe` (ComfyUI)
- `obsidian.exe`

## 2. Manual Startup (via VS Code Tasks)

If a service is missing, trigger the corresponding task from `.vscode/tasks.json`:

1. **Run Ollama**
2. **Run Tailscale**
3. **Run Qwen3 TTS**
4. **Run Open WebUI**
5. **Run ComfyUI**
6. **Run Obsidian**

## 3. All-in-One Startup

// turbo
Run the consolidated task: "Start Evelyn Services". This will launch all dependencies in parallel.
