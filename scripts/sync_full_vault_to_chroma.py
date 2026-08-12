#!/usr/bin/env python3
# scripts/sync_full_vault_to_chroma.py
"""
sync_full_vault_to_chroma.py — Reset ChromaDB and run full-vault ingestion.

Ingests all 1,221+ vault Markdown files and SQLite context entries into
the `evelyn_memory` collection using BAAI/bge-large-en-v1.5 (1024-dim).
"""

import os
import sys
import shutil
import time

# Ensure project imports resolve
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for d in (ROOT_DIR, TOOLS_DIR):
    if d not in sys.path:
        sys.path.insert(0, d)

import evelyn_config as cfg

def main():
    print("=================================================================")
    print("Full Vault Chroma Vector Index Migration (BAAI/bge-large-en-v1.5)")
    print("=================================================================")
    
    # 1. Reset chroma_dir directory to clear old 384-dim vectors
    chroma_dir = getattr(cfg, "CHROMA_DB_PATH", r"/home/rathius/evelyn/data/chroma_db")
    if os.path.exists(chroma_dir):
        print(f"Purging old vector database at: {chroma_dir}")
        shutil.rmtree(chroma_dir, ignore_errors=True)
        print("Vector database directory purged.")

    # 2. Reset sync state JSON files
    for state_file in [
        getattr(cfg, "VAULT_SYNC_STATE", r"/home/rathius/evelyn/data/vault_sync_state.json"),
        getattr(cfg, "GIST_SYNC_STATE", r"/home/rathius/evelyn/data/gist_sync_state.json")
    ]:
        if os.path.exists(state_file):
            try:
                os.remove(state_file)
                print(f"Removed stale state file: {os.path.basename(state_file)}")
            except Exception as e:
                print(f"Could not remove {state_file}: {e}")

    # 3. Import and run full vault ingestion
    print("\nStarting full-vault indexing pass into 'evelyn_memory'...")
    start_time = time.time()
    
    import ingest_obsidian_knowledge
    ingest_obsidian_knowledge.main()
    
    elapsed = time.time() - start_time
    print(f"\nMigration completed successfully in {elapsed:.2f} seconds!")

if __name__ == "__main__":
    main()
