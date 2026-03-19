"""
ingest_obsidian_knowledge.py — Syncs Evelyn's core memory files into Open WebUI.

Uploads full markdown files from Evelyn's Obsidian Vault subdirectory (journals,
context entries, physical description) into the "Evelyn's Memory" knowledge
collection in Open WebUI. This gives the RAG system access to the raw,
unabridged source of truth for Evelyn's lived experience and context facts.

Key behaviour:
  - State file (``vault_sync_state.json``) stores mtime + Open WebUI file ID per
    source file. This allows updates to reliably delete the old record before
    re-uploading — no API file-listing lookup required.
  - State format: ``{source_path: {"mtime": float, "file_id": str|None}}``
    Old format (``{source_path: float}``) is automatically migrated on load.
  - Garbage collection: state entries for deleted/excluded files are cleaned up
    and their Open WebUI records removed.
  - Uses a path-derived ``safe_name`` (spaces and separators replaced) for
    uniqueness within Open WebUI's file storage.

Run directly (``python ingest_obsidian_knowledge.py``) or imported and called
via ``main()`` from ``openwebui_sync_tool.py``.
"""
import os
import json
import requests
import time
from glob import glob

API_BASE = "http://localhost:8080/api/v1"
API_KEY = os.environ.get("OpenWebUI_API", "sk-768aef85b01e44349aae5a536cc47c10")

# Obsidian Vault Paths
EVELYN_DIR = r"G:\My Drive\Obsidian_Vault\Evelyn"
PHYSICAL_DESC_FILE = r"G:\My Drive\Obsidian_Vault\Notes\Prompt Lab\Physical Descriptions\Physical Description - Evelyn.md"
EXCLUDED_SUBDIRS = ["Archived", "Pending_Approvals"]
SYNC_STATE_FILE = r"C:\Projects\LocalAI\Evelyn\tools\vault_sync_state.json"

KNOWLEDGE_NAME = "Evelyn's Memory"

headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state(state_file):
    """
    Loads the sync state, migrating old format automatically.

    Old format: ``{source_path: mtime_float}``
    New format: ``{source_path: {"mtime": float, "file_id": str|None}}``

    Returns:
        dict: State dictionary in new format.
    """
    if not os.path.exists(state_file):
        return {}
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        state = {}
        for k, v in raw.items():
            if isinstance(v, (int, float)):
                # Migrate old format
                state[k] = {"mtime": float(v), "file_id": None}
            elif isinstance(v, dict):
                state[k] = v
        return state
    except Exception:
        print("Could not read state file, starting fresh.")
        return {}


def save_state(state, state_file):
    """Persists state to disk."""
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=4)


def make_safe_name(file_path):
    """
    Derives a unique, filesystem-safe filename from a source file path.

    Replaces drive colons, backslashes, forward slashes, and spaces with
    underscores so the resulting name is safe to use as an Open WebUI
    file upload filename while remaining uniquely tied to the source path.
    """
    return (
        file_path.replace(":", "")
                 .replace("\\", "_")
                 .replace("/", "_")
                 .replace(" ", "_")
    )


# ---------------------------------------------------------------------------
# Open WebUI API helpers
# ---------------------------------------------------------------------------

def get_or_create_knowledge():
    """
    Retrieves the ID of the "Evelyn's Memory" knowledge collection, creating
    it if it does not already exist.

    Returns:
        str: The UUID of the knowledge collection, or ``None`` on failure.
    """
    print(f"Checking for Knowledge Collection '{KNOWLEDGE_NAME}'...")
    response = requests.get(f"{API_BASE}/knowledge/", headers=headers)
    if response.ok:
        collections = response.json()
        if isinstance(collections, dict):
            collections = collections.get("items", [])
        for col in collections:
            if col.get("name") == KNOWLEDGE_NAME:
                print("Found existing collection.")
                return col["id"]

    print("Creating new Knowledge Collection.")
    payload = {
        "name": KNOWLEDGE_NAME,
        "description": "Auto-synced journal and context entries from Obsidian vault for Evelyn.",
    }
    resp = requests.post(f"{API_BASE}/knowledge/create", json=payload, headers=headers)
    if resp.ok:
        return resp.json()["id"]
    else:
        print(f"Failed to create collection: {resp.text}")
        return None


def remove_file_from_knowledge(collection_id, file_id):
    """Removes a specific file from the knowledge collection."""
    payload = {"file_id": file_id}
    requests.post(
        f"{API_BASE}/knowledge/{collection_id}/file/remove", json=payload, headers=headers
    )


def delete_file(file_id):
    """Deletes a file record from Open WebUI storage."""
    resp = requests.delete(f"{API_BASE}/files/{file_id}", headers=headers)
    if not resp.ok:
        print(f"Failed to delete file record {file_id}: {resp.text}")


def upload_markdown_file(file_path, safe_name=None):
    """
    Uploads a markdown file to Open WebUI file storage.

    Args:
        file_path: Absolute path to the source markdown file.
        safe_name: Unique filename to use in Open WebUI. Defaults to basename.

    Returns:
        str: The Open WebUI file UUID on success, or ``None`` on failure.
    """
    if not safe_name:
        safe_name = os.path.basename(file_path)

    print(f"Uploading: {safe_name}")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"Failed to read file {file_path}: {e}")
        return None

    files = {"file": (safe_name, content.encode("utf-8"), "text/markdown")}
    resp = requests.post(f"{API_BASE}/files/", files=files, headers=headers)
    if resp.ok:
        return resp.json()["id"]
    else:
        print(f"Failed to upload {safe_name}: {resp.text}")
        return None


def add_file_to_knowledge(collection_id, file_id):
    """
    Registers an already-uploaded file with a knowledge collection.

    Args:
        collection_id: UUID of the target knowledge collection.
        file_id: UUID of the file record in Open WebUI's file storage.
    """
    payload = {"file_id": file_id}
    resp = requests.post(
        f"{API_BASE}/knowledge/{collection_id}/file/add", json=payload, headers=headers
    )
    if not resp.ok:
        print(f"Failed to add file {file_id} to collection {collection_id}: {resp.text}")


def get_markdown_files(directory):
    """
    Returns a list of all ``*.md`` files under ``directory`` (recursive).

    Args:
        directory: Root directory to search.

    Returns:
        list[str]: Absolute paths to every markdown file found.
    """
    return glob(os.path.join(directory, "**", "*.md"), recursive=True)


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def main():
    """
    Entry point: runs the full incremental sync of Evelyn's core memory files.

    Pipeline:
      1. Load state from ``vault_sync_state.json`` (auto-migrates old format).
      2. Get or create the "Evelyn's Memory" knowledge collection.
      3. Collect all active markdown files (excluding Archived, Pending_Approvals).
      4. Garbage-collect: remove state entries (and their Open WebUI records)
         for files that no longer exist on disk or have been moved to excluded dirs.
      5. For each active file:
           - Skip if mtime unchanged (trust state).
           - If mtime changed and state has a file_id: delete old record first.
           - Upload new version, register with collection, save new file_id to state.
      6. Save updated state to disk.
    """
    if not os.path.exists(EVELYN_DIR):
        print(f"Could not find Vault directory: {EVELYN_DIR}")
        return

    state = load_state(SYNC_STATE_FILE)

    collection_id = get_or_create_knowledge()
    if not collection_id:
        print("Cannot proceed without a target Knowledge Collection.")
        return

    # Build active file list
    all_files = get_markdown_files(EVELYN_DIR)
    if os.path.exists(PHYSICAL_DESC_FILE):
        all_files.append(PHYSICAL_DESC_FILE)

    active_paths = set()
    for file_path in all_files:
        if any(excluded in file_path for excluded in EXCLUDED_SUBDIRS):
            continue
        active_paths.add(file_path)

    processed = 0
    skipped = 0
    cleaned = 0

    # Garbage collection: remove state entries for files no longer active
    for stale_path in list(state.keys()):
        if stale_path not in active_paths:
            entry = state[stale_path]
            file_id = entry.get("file_id") if isinstance(entry, dict) else None
            if file_id:
                print(f"GC: Removing obsolete file from collection: {os.path.basename(stale_path)}")
                remove_file_from_knowledge(collection_id, file_id)
                delete_file(file_id)
            del state[stale_path]
            cleaned += 1

    print("Starting sync...")

    for file_path in active_paths:
        try:
            mtime = os.path.getmtime(file_path)
        except OSError:
            continue

        entry = state.get(file_path, {})
        stored_mtime = entry.get("mtime", 0) if isinstance(entry, dict) else float(entry)

        # Skip if unchanged
        if stored_mtime >= mtime:
            skipped += 1
            continue

        safe_name = make_safe_name(file_path)

        # Delete old record if we have its ID
        old_file_id = entry.get("file_id") if isinstance(entry, dict) else None
        if old_file_id:
            print(f"Update detected: {os.path.basename(file_path)} — removing old record.")
            remove_file_from_knowledge(collection_id, old_file_id)
            delete_file(old_file_id)

        # Upload new version
        file_id = upload_markdown_file(file_path, safe_name=safe_name)
        if file_id:
            add_file_to_knowledge(collection_id, file_id)
            state[file_path] = {"mtime": mtime, "file_id": file_id}
            processed += 1
        else:
            print(f"Upload failed or duplicate for {os.path.basename(file_path)} — marking as synced.")
            state[file_path] = {"mtime": mtime, "file_id": old_file_id}

        time.sleep(0.1)

    save_state(state, SYNC_STATE_FILE)
    print(f"Sync complete. Processed: {processed}, Skipped: {skipped}, GC'd: {cleaned}")


if __name__ == "__main__":
    main()
