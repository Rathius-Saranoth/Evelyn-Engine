#!/usr/bin/env python3
# sync_full_vault_to_chroma.py
# date created: 2026-09-23 21:42:00
# date modified: 2026-09-23 21:46:27
# tags:

# scripts/sync_full_vault_to_chroma.py
"""
sync_full_vault_to_chroma.py — Reset ChromaDB and regenerate every rebuildable collection.

DESTRUCTIVE. Deletes the ENTIRE vector store, then regenerates each collection listed in
`chroma_rag.REBUILD_STRATEGIES` (vault memory, Reference Library, tag taxonomy) from its
source. Collections with no rebuild strategy (e.g. `evelyn_media`) are lost. Written for the
one-time 384 -> 1024-dim embedding migration; to repair ONE collection use
`scripts/rebuild_chroma_collection.py --collection <name>` instead.

Refuses to run while evelyn.service is up (see `chroma_rag.acquire_offline_writer`).
"""

import os
import shutil
import sys
import time

# Ensure project imports resolve
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for d in (ROOT_DIR, TOOLS_DIR):
    if d not in sys.path:
        sys.path.insert(0, d)

import evelyn_config as cfg
from Evelyn.tools import chroma_rag


def main() -> int:
    print("=================================================================")
    print("Full Vault Chroma Vector Index Migration (BAAI/bge-large-en-v1.5)")
    print("=================================================================")

    code, _ = chroma_rag.acquire_offline_writer("sync_full_vault_to_chroma.py (full store reset)")
    if code:
        return code

    # 1. Reset chroma_dir to clear old vectors. The lock file stays: removing it would let
    # another process create a fresh one and claim writes while this one still holds them.
    chroma_dir = getattr(cfg, "CHROMA_DB_PATH", r"/home/rathius/evelyn/data/chroma_db")
    if os.path.exists(chroma_dir):
        print(f"Purging old vector database at: {chroma_dir}")
        keep = os.path.basename(chroma_rag._writer_lock_path())
        for entry in os.listdir(chroma_dir):
            if entry == keep:
                continue
            path = os.path.join(chroma_dir, entry)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        print("Vector database directory purged.")

    # 2. Reset every sync state file, or the incremental syncs skip "unchanged" files and
    # leave their collections empty.
    state_keys = {k for s in chroma_rag.REBUILD_STRATEGIES.values() for k in s["state_files"]}
    state_keys.add("GIST_SYNC_STATE")
    for key in sorted(state_keys):
        state_file = getattr(cfg, key, None)
        if state_file and os.path.exists(state_file):
            try:
                os.remove(state_file)
                print(f"Removed stale state file: {os.path.basename(state_file)}")
            except OSError as e:
                print(f"Could not remove {state_file}: {e}")

    # 3. Re-enqueue every rebuildable collection from its canonical source.
    start_time = time.time()
    for name, strategy in chroma_rag.REBUILD_STRATEGIES.items():
        module_name, func_name = strategy["entrypoint"]
        print(f"\nRe-enqueueing '{name}' via {module_name}.{func_name}() ...", flush=True)
        __import__(module_name)
        getattr(sys.modules[module_name], func_name)()

    # 4. Ingestion only enqueues. This process holds the writer lease and the engine is
    # stopped, so it embeds the queue itself.
    print("\nDraining embedding queue (this is the slow part) ...", flush=True)
    total = 0
    while drained := chroma_rag.drain_sync_queue(50):
        total += drained
        print(f"  embedded {total} ...", flush=True)

    print(f"\nMigration completed successfully in {time.time() - start_time:.2f} seconds!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
