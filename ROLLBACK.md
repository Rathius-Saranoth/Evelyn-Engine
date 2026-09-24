---
title: ROLLBACK.md
date created: 2026-08-22 15:00:00
date modified: 2026-09-23 21:46:27
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
> [!WARNING] This deletes **every** Chroma collection and regenerates the rebuildable ones
> (memory, Reference Library, tag taxonomy). Collections without a rebuild strategy, such as
> `evelyn_media`, are lost. It belongs only in this full revert. To fix one broken
> collection, use [Repairing a Single Chroma Collection](#repairing-a-single-chroma-collection).

The script refuses to run while `evelyn.service` is up. Step 1 has already stopped it.
```bash
PYTHONPATH=. /home/rathius/evelyn/venv/bin/python scripts/sync_full_vault_to_chroma.py
```

## 5. Restart Services
```bash
sudo systemctl start evelyn evelyn-tts
systemctl --user start evelyn-vault-watcher
```

---

## Repairing a Single Chroma Collection

Use this when one collection is unreadable, or its metadata is stale and needs regenerating.
The other collections are left alone.

**Only one process may write to Chroma, and while the engine runs that is the engine.**
Every process keeps its own in-memory copy of each HNSW index and saves it on write. Two
writers overwrite each other's files, even when they take turns. On 2026-09-23 a rebuild
run beside the engine corrupted `evelyn_reference`, and its link lists grew to 891GB until
the disk was full. The engine's custodian now holds a writer lease for its whole life, and
maintenance scripts refuse to run while `evelyn.service` is up.

```bash
cd /home/rathius/evelyn
# 1. Inspect (read-only; safe while the engine runs)
PYTHONPATH=. venv/bin/python scripts/rebuild_chroma_collection.py --list

# 2. Stop the engine. The rebuild refuses to run otherwise.
sudo systemctl stop evelyn

# 3. Rebuild. This archives the store, drops the collection, re-enqueues it and embeds it.
#    Add --force only when the collection is readable but stale.
PYTHONPATH=. venv/bin/python scripts/rebuild_chroma_collection.py --collection <name> --execute

# 4. Start the engine again
sudo systemctl start evelyn
```

If a script prints `[REFUSED]`, something else holds the writer lease, and the message names
it. Refusals are also logged to the journal: `journalctl -t rebuild_chroma_collection.py`.

If the engine finds a collection unreadable at startup, it repairs it itself. It runs the
rebuild before its custodian starts, then the custodian embeds the re-queued documents.
