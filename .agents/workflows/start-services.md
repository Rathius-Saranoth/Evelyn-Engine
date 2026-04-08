---
description: How to check and start the core services for the Evelyn ecosystem
---

# Service Startup Workflow

This workflow describes how to initialize the required backends for Evelyn's functionality.

## 1. Verify Active Services

// turbo
Check if the following processes are running:

- `ollama.exe` (Port 11434)
- `python.exe` running `qwen_tts_server.py` (Port 5050)
- `python.exe` running `evelyn_server.py` (Port 7860)
- `python.exe` (ComfyUI, Port 8188)
- `obsidian.exe`

## 2. Manual Startup (via VS Code Tasks)

If a service is missing, trigger the corresponding task from `.vscode/tasks.json`:

1. **Run Ollama**
2. **Run Tailscale**
3. **Run Qwen3 TTS**
4. **Run Evelyn Server** (`python evelyn_server.py` in `C:\Projects\LocalAI`)
5. **Run ComfyUI** (starts with `--lowvram` to release GPU memory when idle)
6. **Run Obsidian**

## 3. All-in-One Startup

// turbo
Run the consolidated task: **"Start Evelyn Services"**.

**Startup sequence:**
1. **Run Ollama** — starts first, alone.
2. **Wait for Ollama** — polls `http://localhost:11434` until Ollama responds (uses `wait_for_ollama.ps1`). All other services are blocked until this completes.
3. **Start Remaining Services** — all others launch in parallel once Ollama is confirmed ready.

This ensures Ollama claims its GPU layers before ComfyUI starts, which maximises the number of model layers that can be offloaded to VRAM.

## 4. Access

- Local: http://localhost:7860
- Tailscale: http://image-host.internal.net:7860
- Set `EVELYN_API_KEY` env var before starting the server for auth.

## 5. Updating Ollama

Run the **"Update Ollama"** VS Code task to download and silently install the latest Ollama release.

> [!IMPORTANT]
> This task is **intentionally NOT part of the startup sequence**. Auto-updating on every boot is risky — a new Ollama release could change API behavior and break Evelyn. Run this task manually when you want to update (e.g., to support a new model family). After it completes, run **"Start Evelyn Services"** as usual.

The task will:
1. Download the latest `OllamaSetup.exe` from `ollama.com`
2. Install it silently (`/S` flag) — UAC may prompt once
3. Print the new version number to confirm success

## 6. Debugging

To inspect Evelyn's chat history or debug conversation issues, see `/debug-chat-db`.

