#!/usr/bin/env python3
# migrate_reference_library_vectors.py
# date created: 2026-09-11 17:20:00
# tags: #chroma, #migration, #reference-library, #vector-store

"""
Fast zero-re-embed vector migration script.

Transfers all Reference Library book and manual chunks from `evelyn_memory` to
the dedicated `evelyn_reference` ChromaDB collection, preserving precomputed
vector embeddings. Then purges the transferred chunks from `evelyn_memory` and
initializes `reference_sync_state.json`.
"""

import hashlib
import json
import os
import sys

# Anchor workspace roots for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for _p in (ROOT_DIR, TOOLS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import chromadb

import evelyn_config as cfg
from Evelyn.tools import chroma_rag
from Evelyn.tools.chroma_rag import acquire_chroma_write_lock, sanitize_chroma_metadata


def compute_content_hash(content: str) -> str:
    """Compute SHA-256 hash of document text content."""
    return hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()


def main():
    print("=" * 70)
    print("Reference Library Vector Migration: evelyn_memory -> evelyn_reference")
    print("=" * 70)

    chroma_dir = getattr(cfg, "CHROMA_DB_PATH", os.path.join(cfg.BASE_DIR, "data", "chroma_db"))
    memory_col_name = getattr(cfg, "CHROMA_MEMORY_COLLECTION", "evelyn_memory")
    reference_col_name = getattr(cfg, "CHROMA_REFERENCE_COLLECTION", "evelyn_reference")
    ref_sync_file = getattr(cfg, "REFERENCE_SYNC_STATE", os.path.join(cfg.BASE_DIR, "data", "reference_sync_state.json"))
    vault_root = getattr(cfg, "VAULT_BASE_DIR", "/home/rathius/obsidian_vault")
    ref_library_dir = os.path.join(vault_root, "Reference Library")

    print(f"Chroma DB Path:          {chroma_dir}")
    print(f"Source Collection:       {memory_col_name}")
    print(f"Destination Collection:  {reference_col_name}")
    print(f"Reference Sync State:    {ref_sync_file}")

    with acquire_chroma_write_lock(timeout=120.0):
        client = chromadb.PersistentClient(path=chroma_dir)
        try:
            mem_col = client.get_collection(memory_col_name)
        except (RuntimeError, ValueError, KeyError, OSError) as e:
            print(f"Error accessing memory collection '{memory_col_name}': {e}")
            return 1

        ref_col = chroma_rag.get_or_create_collection(reference_col_name)

        initial_mem_count = mem_col.count()
        initial_ref_count = ref_col.count()
        print(f"\nInitial chunk count in {memory_col_name}:    {initial_mem_count}")
        print(f"Initial chunk count in {reference_col_name}: {initial_ref_count}")

        print("\nScanning memory collection for Reference Library chunks...")
        # Inspect metadatas and IDs to identify target records
        all_records = mem_col.get(include=["metadatas"])
        all_ids = all_records["ids"]
        all_metas = all_records["metadatas"]

        target_ids = []
        for cid, meta in zip(all_ids, all_metas, strict=False):
            is_ref = False
            if "reference library" in cid.lower():
                is_ref = True
            elif meta and isinstance(meta, dict):
                if meta.get("type") == "reference-chapter":
                    is_ref = True
                tags = meta.get("tags")
                if tags and ("reference-library" in tags if isinstance(tags, (list, set)) else "reference-library" in str(tags)):
                    is_ref = True
            if is_ref:
                target_ids.append(cid)

        total_targets = len(target_ids)
        print(f"Identified {total_targets} chunks to transfer to {reference_col_name}.")

        if total_targets == 0:
            print("No Reference Library chunks found in memory collection. Proceeding to state sync.")
        else:
            batch_size = 500
            transferred = 0
            for i in range(0, total_targets, batch_size):
                batch_ids = target_ids[i : i + batch_size]
                chunk_data = mem_col.get(
                    ids=batch_ids,
                    include=["documents", "metadatas", "embeddings"],
                )

                b_ids = chunk_data["ids"]
                b_docs = chunk_data["documents"]
                b_metas = chunk_data["metadatas"]
                b_embeds = chunk_data["embeddings"]

                # Ensure metadata source reflects canonical file path
                normalized_metas = []
                for cid, m in zip(b_ids, b_metas, strict=False):
                    m_copy = dict(m) if m else {}
                    file_path = cid.split("::chunk-")[0] if "::chunk-" in cid else m_copy.get("source", "")
                    if "source" in m_copy and m_copy["source"] != file_path:
                        m_copy["frontmatter_source"] = m_copy["source"]
                    m_copy["source"] = file_path
                    normalized_metas.append(sanitize_chroma_metadata(m_copy))

                # Upsert into evelyn_reference
                ref_col.upsert(
                    ids=b_ids,
                    documents=b_docs,
                    metadatas=normalized_metas,
                    embeddings=b_embeds,
                )

                # Prune from evelyn_memory
                mem_col.delete(ids=b_ids)

                transferred += len(b_ids)
                print(f"Transferred & pruned: {transferred}/{total_targets} chunks...", flush=True)

        final_mem_count = mem_col.count()
        final_ref_count = ref_col.count()
        print(f"\nFinal chunk count in {memory_col_name}:    {final_mem_count}")
        print(f"Final chunk count in {reference_col_name}: {final_ref_count}")

    # Build reference_sync_state.json for fast incremental syncing
    print(f"\nInitializing sync state in {ref_sync_file}...")
    ref_state = {}
    indexed_files = 0
    if os.path.exists(ref_library_dir):
        for root, _, files in os.walk(ref_library_dir):
            for f in files:
                if f.endswith(".md"):
                    fp = os.path.join(root, f)
                    try:
                        mtime = os.path.getmtime(fp)
                        with open(fp, encoding="utf-8", errors="ignore") as fobj:
                            content = fobj.read()
                        chash = compute_content_hash(content)
                        ref_state[fp] = {"mtime": mtime, "sha256": chash}
                        indexed_files += 1
                    except OSError:
                        continue

    os.makedirs(os.path.dirname(ref_sync_file), exist_ok=True)
    with open(ref_sync_file, "w", encoding="utf-8") as f:
        json.dump(ref_state, f, indent=2)

    print(f"Recorded sync state for {indexed_files} Reference Library markdown files.")
    print("=" * 70)
    print("MIGRATION COMPLETED SUCCESSFULLY.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
