---
title: ROLLBACK.md
date created: 2026-08-22 15:00:00
date modified: 2026-08-23 08:02:35
tags: [rollback, recovery, maintenance, snapshot, evelyn]
---

# Rollback Instructions (Pre-Sanitization Snapshot)

> Navigation: [[README.md]] · [[engine_architecture.md]] · [[backup-to-github.md]]

If anything fails or requires a complete reversion to the state prior to Template Sanitization:

## 1. Stop All Services
```bash
sudo systemctl stop evelyn evelyn-tts
systemctl --user stop evelyn-vault-watcher
```

## 2. Revert Git Working Tree
```bash
git checkout pre-sanitization
git clean -fd
```

## 3. Restore Databases
```bash
cp -av /home/rathius/evelyn/data/backups/pre-sanitization/evelyn_chat.db /home/rathius/evelyn/data/evelyn_chat.db
cp -av /home/rathius/evelyn/data/backups/pre-sanitization/evelyn_memory.db /home/rathius/evelyn/data/evelyn_memory.db
cp -av /home/rathius/evelyn/data/backups/pre-sanitization/evelyn_vault.db /home/rathius/evelyn/data/evelyn_vault.db
cp -av /home/rathius/evelyn/data/backups/pre-sanitization/evelyn_media.db /home/rathius/evelyn/data/evelyn_media.db
cp -av /home/rathius/evelyn/data/backups/pre-sanitization/health_connect.db /home/rathius/evelyn/data/health/health_connect.db
```

## 4. Re-sync ChromaDB Vector Store
```bash
PYTHONPATH=. /home/rathius/evelyn/venv/bin/python scripts/sync_full_vault_to_chroma.py
```

## 5. Restart Services
```bash
sudo systemctl start evelyn evelyn-tts
systemctl --user start evelyn-vault-watcher
```
