---
description: How to cleanly and safely restart Evelyn's core services, flush WAL logs, and verify engine readiness
title: restart-services.md
date created: 2026-08-27 12:22:00
date modified: 2026-08-27 12:22:00
tags: services, restart, reboot, ecosystem, guide, workflow, evelyn
---

# Service Restart Workflow

> Navigation: [[SETUP_GUIDE.md]] · [[engine_architecture.md]] · [[start-services.md]] · [[stop-services.md]] · [[README.md]]

This workflow describes how to safely, cleanly, and deterministically restart the Evelyn ecosystem without leaving truncated files, dangling transactions, or orphaned processes.

## 1. Clean Automated Restart (CLI Script)

Use the dedicated restart script located at `scripts/restart_evelyn_services.sh`. It automatically flushes SQLite WAL buffers, cycles systemd units, and polls the FastAPI lifespan probe until healthy:

```bash
# Standard clean restart (Evelyn AI Core, TTS Server, and SQLite WAL checkpoint)
bash scripts/restart_evelyn_services.sh

# Full restart including Ollama LLM backend
bash scripts/restart_evelyn_services.sh --all
```

## 2. Manual Systemd Restart Sequence

If executing commands step-by-step:

```bash
# 1. Flush SQLite WAL logs to prevent uncommitted transaction locks
for db in data/*.db data/health/*.db; do [ -f "$db" ] && sqlite3 "$db" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null; done

# 2. Restart core services
sudo systemctl restart evelyn-tts evelyn

# 3. Verify active status
systemctl is-active ollama evelyn-tts evelyn
```

## 3. Post-Restart Health Probe (MCP or CLI)

Confirm that the FastAPI server, Chroma vector DB, and background task watchdogs initialized successfully:

### Via MCP Server (Recommended):
Use `evelyn-sqlite` MCP tool: `get_server_status`

### Via CLI:
```bash
curl -sk https://127.0.0.1:7860/status
```

Expected output: `{"status": "ok", "engine_version": "...", "model": "gemma4:12b", ...}`

## 4. Troubleshooting Stalled Restarts

If the server takes longer than 15 seconds to start:

1. **Check Systemd Journal Logs**:
   ```bash
   journalctl -u evelyn -n 50 --no-pager
   ```
2. **Inspect Orphaned Processes / Deadlocks**:
   ```bash
   ps aux | grep -E '(evelyn|uvicorn|python)'
   ```
3. **Verify Port Availability**:
   ```bash
   ss -tulpn | grep -E ':(11434|5050|7860)'
   ```
