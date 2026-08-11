---
description: How to check and start the core services for the Evelyn ecosystem
title: start-services.md
date created: 2026-03-14 22:28:48
date modified: 2026-08-10 19:53:30
tags: services, startup, launch, ecosystem, guide
---

# Service Startup Workflow

This workflow describes how to initialize and check the required backends for Evelyn's functionality on Sanctum (`sanctum.internal.net`).

## 1. Verify Active Services

Check systemd services and port bindings:

- `ollama.service` (Ollama LLM Server, Port 11434)
- `evelyn-tts.service` (Chatterbox TTS Server, Port 5050)
- `evelyn.service` (Evelyn FastAPI Server, Port 7860)

```bash
# Check status of systemd services
systemctl status ollama evelyn evelyn-tts

# Verify port bindings
ss -tulpn | grep -E ':(11434|5050|7860)'
```

## 2. Managing Services (via VS Code Tasks or Terminal)

Use the predefined VS Code tasks in `.vscode/tasks.json` or systemctl directly:

1. **Start Evelyn Services** (`sudo systemctl start ollama evelyn evelyn-tts`)
2. **Stop Evelyn Services** (`sudo systemctl stop evelyn evelyn-tts`)
3. **Restart Evelyn Server** (`sudo systemctl restart evelyn`)
4. **View Evelyn Logs** (`journalctl -u evelyn -f --no-pager -n 50`)
5. **View TTS Logs** (`journalctl -u evelyn-tts -f --no-pager -n 50`)
6. **GPU Status** (`nvidia-smi`)

## 3. Service Dependencies

`evelyn.service` requires `ollama.service` to be running before starting to avoid model connection timeouts.

## 4. Access

- Local: http://localhost:7860
- Tailscale Mesh: http://sanctum.internal.net:7860
- Auth: Set `EVELYN_API_KEY` environment variable in server environment.

## 5. Updating Ollama

On Linux, update Ollama via the official install script or package manager:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

After updating, restart the Ollama service and Evelyn services:

```bash
sudo systemctl restart ollama evelyn
```

## 6. Debugging

To inspect Evelyn's chat history or debug conversation issues, see `/debug-chat-db`.

