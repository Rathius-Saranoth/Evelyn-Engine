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


def get_or_create_knowledge():
    print(f"Checking for Knowledge Collection '{KNOWLEDGE_NAME}'...")
    response = requests.get(f"{API_BASE}/knowledge/", headers=headers)
    if response.ok:
        collections = response.json()

        # Depending on API version, it might return a list or a dict with 'items'
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

    # Try the standard create endpoint
    resp = requests.post(f"{API_BASE}/knowledge/create", json=payload, headers=headers)

    if resp.ok:
        return resp.json()["id"]
    else:
        print(f"Failed to create collection: {resp.text}")
        return None


def upload_markdown_file(file_path):
    print(f"Uploading: {os.path.basename(file_path)}")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"Failed to read file {file_path}: {e}")
        return None

    # OpenWebUI requires multipart/form-data for file upload
    safe_name = os.path.basename(file_path)
    # The endpoint usually expects the file in the 'file' field
    files = {"file": (safe_name, content.encode("utf-8"), "text/markdown")}

    resp = requests.post(f"{API_BASE}/files/", files=files, headers=headers)
    if resp.ok:
        return resp.json()["id"]
    else:
        print(f"Failed to upload {safe_name}: {resp.text}")
        return None


def add_file_to_knowledge(collection_id, file_id):
    payload = {"file_id": file_id}
    # Some older API versions used /add, newer might use /update or standard REST.
    # The /add endpoint was used in the gist script, so we stick with it.
    resp = requests.post(
        f"{API_BASE}/knowledge/{collection_id}/file/add", json=payload, headers=headers
    )
    if not resp.ok:
        print(
            f"Failed to add file {file_id} to collection {collection_id}: {resp.text}"
        )


def get_markdown_files(directory):
    return glob(os.path.join(directory, "**", "*.md"), recursive=True)


def main():
    if not os.path.exists(EVELYN_DIR):
        print(f"Could not find Vault directory: {EVELYN_DIR}")
        return

    sync_state = {}
    if os.path.exists(SYNC_STATE_FILE):
        try:
            with open(SYNC_STATE_FILE, "r", encoding="utf-8") as f:
                sync_state = json.load(f)
        except Exception:
            print("Could not read sync state, starting fresh.")

    collection_id = get_or_create_knowledge()
    if not collection_id:
        print("Cannot proceed without a target Knowledge Collection.")
        return

    processed = 0
    errors = 0
    skipped = 0

    print("Starting sync...")

    all_files = get_markdown_files(EVELYN_DIR)
    if os.path.exists(PHYSICAL_DESC_FILE):
        all_files.append(PHYSICAL_DESC_FILE)

    for file_path in all_files:
        # Check for exclusions
        if any(excluded in file_path for excluded in EXCLUDED_SUBDIRS):
            skipped += 1
            continue

        try:
            mtime = os.path.getmtime(file_path)
        except OSError:
            continue

        # Check if already synced and unmodified
        if file_path in sync_state and sync_state[file_path] >= mtime:
            skipped += 1
            continue

        file_id = upload_markdown_file(file_path)
        if file_id:
            add_file_to_knowledge(collection_id, file_id)
            sync_state[file_path] = mtime
            processed += 1
        else:
            errors += 1

        # Small delay to avoid hammering the API
        time.sleep(0.1)

    # Save state
    with open(SYNC_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sync_state, f, indent=4)

    print(
        f"Sync complete. Processed: {processed}, Skipped: {skipped}, Errors: {errors}"
    )


if __name__ == "__main__":
    main()
