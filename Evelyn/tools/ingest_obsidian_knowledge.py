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

# Chroma RAG wrapper
ROOT_DIR  = r"C:\Projects\LocalAI"
TOOLS_DIR = r"C:\Projects\LocalAI\Evelyn\tools"
for _d in (ROOT_DIR, TOOLS_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)
import chroma_rag  # noqa: E402

# Paths
EVELYN_DIR         = r"G:\My Drive\Obsidian_Vault\Evelyn"
PHYSICAL_DESC_FILE = r"G:\My Drive\Obsidian_Vault\Notes\Prompt Lab\Physical Descriptions\Physical Description - Evelyn.md"
EXCLUDED_SUBDIRS   = ["Archived", "Pending_Approvals"]
SYNC_STATE_FILE    = r"C:\Projects\LocalAI\Evelyn\tools\vault_sync_state.json"
COLLECTION_NAME    = "evelyn_memory"

# Ricky/ files that belong in core memory (full-text, high-fidelity operational docs).
# General Ricky facts (work history, education, etc.) flow through the gist pipeline instead.
RICKY_CORE_FILES = [
    r"G:\My Drive\Obsidian_Vault\Ricky\Ricky - Psychological Blueprint.md",
    r"G:\My Drive\Obsidian_Vault\Ricky\Ricky - Love Languages & Connection.md",
]


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state(state_file):
    """Load sync state, auto-migrating old {path: float} format."""
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


def save_state(state, state_file):
    """Persist state to disk."""
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=4)


def get_markdown_files(directory):
    """Return all *.md files under directory (recursive)."""
    return glob(os.path.join(directory, "**", "*.md"), recursive=True)


def parse_rag_frontmatter(content: str) -> dict:
    """
    Extract rag_priority, rag_pinned, and aliases from YAML frontmatter.

    Returns a dict with defaults if frontmatter is absent or fields are missing:
        {"rag_priority": "normal", "rag_pinned": False, "aliases": ""}
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

def main():
    """
    Full incremental sync of Evelyn's core memory files into Chroma.

    Pipeline:
      1. Load state from vault_sync_state.json.
      2. Collect active markdown files (excluding Archived, Pending_Approvals).
      3. GC: remove Chroma records for files no longer present/active.
      4. For each active file: skip if mtime unchanged, else upsert into Chroma.
      5. Save updated state to disk.
    """
    if not os.path.exists(EVELYN_DIR):
        print(f"Could not find Vault directory: {EVELYN_DIR}")
        return

    state = load_state(SYNC_STATE_FILE)

    # Build active file list
    all_files = get_markdown_files(EVELYN_DIR)
    all_files += [f for f in RICKY_CORE_FILES if os.path.exists(f)]
    if os.path.exists(PHYSICAL_DESC_FILE):
        all_files.append(PHYSICAL_DESC_FILE)

    active_paths = set()
    for fp in all_files:
        if any(ex in fp for ex in EXCLUDED_SUBDIRS):
            continue
        active_paths.add(fp)

    processed = skipped = cleaned = 0

    # Garbage collection
    for stale_path in list(state.keys()):
        if stale_path not in active_paths:
            print(f"GC: {os.path.basename(stale_path)}")
            chroma_rag.delete_document(stale_path, COLLECTION_NAME)
            del state[stale_path]
            cleaned += 1

    print("Starting core memory sync...")

    for file_path in active_paths:
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
