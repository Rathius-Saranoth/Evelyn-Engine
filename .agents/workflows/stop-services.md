---
description: How to safely stop Evelyn's core services and background daemons
title: stop-services.md
date created: 2026-08-23 17:42:00
date modified: 2026-08-23 17:42:00
tags: services, shutdown, stop, teardown, guide, workflow, evelyn
---

# Service Shutdown Workflow

> Navigation: [[SETUP_GUIDE.md]] · [[engine_architecture.md]] · [[start-services.md]] · [[README.md]]

This workflow describes how to safely and cleanly stop all Evelyn services, workers, and background daemons.

## 1. Fast Graceful Stop (CLI Script)

Use the dedicated shutdown script located at `scripts/stop_evelyn_services.sh`:

```bash
# Standard stop (Evelyn Core, TTS, and Vault Watcher)
bash scripts/stop_evelyn_services.sh

# Complete stop including Ollama and SQLite WAL checkpoint
bash scripts/stop_evelyn_services.sh --all --checkpoint-wal
```

## 2. Managing Services via Systemd

You can also control the systemd units directly:

```bash
# 1. Stop Evelyn server, TTS server, and Ollama LLM backend
sudo systemctl stop evelyn evelyn-tts ollama

# 2. Stop User Vault Watcher service
systemctl --user stop evelyn-vault-watcher
```

## 3. Verify Full Resource Release

Confirm all services, port bindings, and GPU VRAM are completely free:

```bash
# 1. Check systemd unit statuses (should be inactive)
systemctl is-active ollama evelyn evelyn-tts && systemctl --user is-active evelyn-vault-watcher

# 2. Check port bindings (should return empty)
ss -tulpn | grep -E ':(11434|5050|7860)'

# 3. Check GPU status (Tesla T4 VRAM should be 0 MiB)
nvidia-smi
```

## 4. Hardware Inspection & Maintenance

Once all services are stopped and verified, the host hardware, GPU, or UPS systems can be safely inspected or rebooted without risk of data corruption or lockfile residue.
