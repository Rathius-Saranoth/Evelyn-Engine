# ingest_obsidian_knowledge.py
# date created: 2026-05-03 18:05:36
# date modified: 2026-06-07 10:28:38
# tags: #obsidian, #ingest, #knowledge, #sync, #pipeline

"""
ingest_obsidian_knowledge.py — Syncs Evelyn's core memory files into Chroma.

Uploads full markdown files from Evelyn's Obsidian Vault subdirectory (journals,
context entries, physical description) into the Chroma 'evelyn_memory' collection.

Key behaviour:
  - State file (vault_sync_state.json) stores mtime per source file.
  - Only changed files are re-ingested on subsequent runs.
  - Garbage collection removes Chroma records for deleted/excluded files.

Run directly or imported via sync_context_memory() in evelyn_tools.py.
"""

import os
import sys
import re
import json
import time
from glob import glob

# Anchoring paths before importing evelyn_config
ROOT_DIR  = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
for _d in (ROOT_DIR, TOOLS_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import evelyn_config as cfg
import chroma_rag  # noqa: E402
import memory_db   # noqa: E402

# Paths
VAULT_DIR          = getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
EVELYN_DIR         = getattr(cfg, "ASSISTANT_WRITE_DIR", os.path.join(VAULT_DIR, getattr(cfg, "ASSISTANT_NAME", "Evelyn")))
EXCLUDED_SUBDIRS   = getattr(cfg, "VAULT_READ_IGNORE", ["Archived", "Pending_Approvals", "Extracted", "Pending"])
SYNC_STATE_FILE    = getattr(cfg, "VAULT_SYNC_STATE", r"/home/rathius/evelyn/data/vault_sync_state.json")
COLLECTION_NAME    = "evelyn_memory"


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state(state_file: str) -> dict:
    """Load sync state, auto-migrating old {path: float} format.

    Args:
        state_file: The path to the JSON state file.

    Returns:
        dict: The loaded and migrated state dictionary.
    """
    if not os.path.exists(state_file):
        return {}
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        state = {}
        for k, v in raw.items():
            if isinstance(v, (int, float)):
                state[k] = {"mtime": float(v)}
            elif isinstance(v, dict):
                state[k] = v
        return state
    except Exception:
        print("Could not read state file, starting fresh.")
        return {}


def save_state(state: dict, state_file: str) -> None:
    """Persist sync state to disk.

    Args:
        state: The state dictionary to save.
        state_file: The destination file path.
    """
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=4)


def get_markdown_files(directory: str) -> list[str]:
    """Return all *.md files under a directory (recursive).

    Args:
        directory: The source directory path.

    Returns:
        list[str]: A list of absolute file paths matching *.md.
    """
    return glob(os.path.join(directory, "**", "*.md"), recursive=True)


def parse_rag_frontmatter(content: str) -> dict:
    """Extract RAG settings from a YAML frontmatter block.

    Args:
        content: The raw markdown content string.

    Returns:
        dict: A dictionary containing 'rag_priority', 'rag_pinned', and 'aliases'.
    """
    defaults = {"rag_priority": "normal", "rag_pinned": False, "aliases": ""}
    fm_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not fm_match:
        return defaults
    fm_text = fm_match.group(1)

    # rag_priority
    m = re.search(r"^rag_priority:\s*(\S+)", fm_text, re.MULTILINE)
    if m:
        defaults["rag_priority"] = m.group(1).strip().lower()

    # rag_pinned
    m = re.search(r"^rag_pinned:\s*(\S+)", fm_text, re.MULTILINE)
    if m:
        defaults["rag_pinned"] = m.group(1).strip().lower() == "true"

    # aliases
    m = re.search(r"^aliases:\s*(\[.*?\]|.*)$", fm_text, re.MULTILINE)
    if m:
        raw = m.group(1).replace("[", "").replace("]", "").replace('"', "").replace("'", "")
        aliases = [a.strip() for a in raw.split(",") if a.strip()]
        defaults["aliases"] = ", ".join(aliases)

    return defaults


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def main() -> None:
    """Perform a full incremental sync of all vault markdown files into Chroma.

    Returns:
        None
    """
    if not os.path.exists(VAULT_DIR):
        print(f"Could not find Vault directory: {VAULT_DIR}")
        return

    state = load_state(SYNC_STATE_FILE)

    # Scan the full vault directory
    all_files = get_markdown_files(VAULT_DIR)

    active_paths = set()
    for fp in all_files:
        if any(ex in fp for ex in EXCLUDED_SUBDIRS):
            continue
        active_paths.add(fp)

    # Fetch live SQLite context entries
    try:
        live_entries = memory_db.get_all_entries(statuses=["live"])
        sqlite_entries_map = {}
        for row in live_entries:
            db_id = f"sqlite::context_entry::{row['id']}"
            active_paths.add(db_id)
            sqlite_entries_map[db_id] = row
    except Exception as e:
        print(f"Failed to query memory_db: {e}")
        sqlite_entries_map = {}

    processed = skipped = cleaned = 0

    # Garbage collection
    for stale_path in list(state.keys()):
        if stale_path not in active_paths:
            print(f"GC: {os.path.basename(stale_path)}")
            chroma_rag.delete_document(stale_path, COLLECTION_NAME)
            del state[stale_path]
            cleaned += 1

    print("Starting full vault memory sync...")

    for file_path in active_paths:
        if file_path.startswith("sqlite::context_entry::"):
            # It's a SQLite DB entry — high priority
            row = sqlite_entries_map.get(file_path)
            if not row:
                continue
            mtime = float(row.get("updated_at") or row.get("created_at") or 0)
            
            entry = state.get(file_path, {})
            stored_mtime = entry.get("mtime", 0) if isinstance(entry, dict) else float(entry)

            if stored_mtime >= mtime:
                skipped += 1
                continue

            content = f"Date: {row['date'] or 'Unknown'}\nTags: {row.get('tags', '')}\nObservation: {row['observation']}"
            rag_meta = {"rag_priority": "high", "rag_pinned": False, "aliases": ""}
            print(f"Ingesting DB Entry: {file_path}")
            
        else:
            # It's a flat file
            try:
                mtime = os.path.getmtime(file_path)
            except OSError:
                continue

            entry = state.get(file_path, {})
            stored_mtime = entry.get("mtime", 0) if isinstance(entry, dict) else float(entry)

            if stored_mtime >= mtime:
                skipped += 1
                continue

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                print(f"Failed to read {file_path}: {e}")
                continue

            print(f"Ingesting: {os.path.basename(file_path)}")
            rag_meta = parse_rag_frontmatter(content)
            
            # Boost extracted/context entries to rag_priority=high if not explicitly specified in frontmatter
            basename = os.path.basename(file_path)
            if rag_meta.get("rag_priority") == "normal":
                if basename.startswith("EX_") or basename.startswith("CE_"):
                    rag_meta["rag_priority"] = "high"
            
        if chroma_rag.ingest_markdown_file(file_path, content, COLLECTION_NAME,
                                           extra_metadata=rag_meta):
            state[file_path] = {"mtime": mtime}
            processed += 1
        else:
            print(f"Ingest failed for {os.path.basename(file_path)}")

        # Checkpoint every 25 files so progress survives a kill/crash
        if (processed + skipped) % 25 == 0:
            save_state(state, SYNC_STATE_FILE)

        time.sleep(0.05)

    save_state(state, SYNC_STATE_FILE)
    print(f"Core memory sync complete. Processed: {processed}, Skipped: {skipped}, GC'd: {cleaned}")


if __name__ == "__main__":
    main()
