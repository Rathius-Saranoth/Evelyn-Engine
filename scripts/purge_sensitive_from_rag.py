#!/usr/bin/env python3
# purge_sensitive_from_rag.py
# date created: 2026-09-23 17:40:00
# date modified: 2026-09-23 17:30:16
# tags: #privacy, #rag, #chroma, #maintenance

"""Remove documents the user marked sensitive from the RAG vector index.

The incremental sync skips any file whose mtime and hash are unchanged, and it does so
*before* reading the frontmatter — so making `sensitivity:` exclude a document does not
retroactively remove one that was indexed before the rule existed. Those documents never
change on disk, so they would stay retrievable indefinitely.

This walks the vault, finds every note whose `sensitivity` level the config withholds from
retrieval, deletes its chunks, and records the exclusion in the sync state so later runs agree.

Usage:
    python scripts/purge_sensitive_from_rag.py            # report only
    python scripts/purge_sensitive_from_rag.py --execute  # delete
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evelyn_config as cfg
from Evelyn.tools import chroma_rag
from Evelyn.tools.ingest_obsidian_knowledge import (
    COLLECTION_NAME,
    SYNC_STATE_FILE,
    load_state,
    parse_rag_frontmatter,
    save_state,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="Delete; otherwise report only.")
    args = ap.parse_args()

    vault = getattr(cfg, "VAULT_BASE_DIR", os.path.expanduser("~/obsidian_vault"))
    withheld = {s.lower() for s in getattr(cfg, "SENSITIVITY_RAG_EXCLUDED", set())}
    if not withheld:
        print("No sensitivity levels are configured as withheld; nothing to do.")
        return 0
    print(f"Withheld levels: {', '.join(sorted(withheld))}")

    state = load_state(SYNC_STATE_FILE)
    col = chroma_rag.get_or_create_collection(COLLECTION_NAME)
    indexed = {m.get("source", "") for m in col.get(include=["metadatas"])["metadatas"]}

    found, purged = [], []
    for root, _dirs, files in os.walk(vault):
        for name in files:
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    meta = parse_rag_frontmatter(fh.read())
            except OSError as exc:
                print(f"  [WARN] unreadable, treating as sensitive: {path} ({exc})")
                meta = {"sensitivity": "secret"}
            if str(meta.get("sensitivity", "")).lower() not in withheld:
                continue
            found.append(path)
            if path in indexed:
                purged.append(path)

    def rel(path: str) -> str:
        """Vault-relative form, for readable output."""
        return path.replace(vault.rstrip("/") + "/", "")

    print(f"\nNotes marked withheld : {len(found)}")
    print(f"Currently indexed     : {len(purged)}")
    for p in purged:
        print(f"  {rel(p)}")

    if not args.execute:
        print("\nReport only. Re-run with --execute to delete these from the index.")
        return 0

    removed = 0
    for path in purged:
        if chroma_rag.delete_document(path, COLLECTION_NAME):
            removed += 1
        else:
            print(f"  [WARN] delete reported failure: {rel(path)}")
    # Record the exclusion for every withheld note, indexed or not, so the incremental sync
    # does not re-add one the next time its mtime changes.
    for path in found:
        entry = state.get(path) or {}
        entry["excluded"] = True
        state[path] = entry
    save_state(state, SYNC_STATE_FILE)

    print(f"\nDeleted {removed} of {len(purged)} indexed notes; {len(found)} marked excluded in sync state.")

    # Verify through a fresh client. The module caches a PersistentClient singleton, and
    # re-reading the same collection handle returned a pre-delete snapshot — which reported
    # eight false "STILL PRESENT" failures on a purge that had in fact succeeded. A privacy
    # tool that cries wolf gets ignored, so this reopens the store rather than trusting it.
    import chromadb

    fresh = chromadb.PersistentClient(path=cfg.CHROMA_DB_PATH).get_or_create_collection(
        COLLECTION_NAME
    )
    still = {m.get("source", "") for m in fresh.get(include=["metadatas"])["metadatas"]}
    leftover = [p for p in found if p in still]
    print(f"Verification (fresh client): {len(leftover)} withheld notes still present.")
    for p in leftover:
        print(f"  STILL PRESENT: {rel(p)}")
    return 1 if leftover else 0


if __name__ == "__main__":
    raise SystemExit(main())
