"""
verify_fix.py — Diagnostic script for auditing Open WebUI knowledge collections.

Connects to the Open WebUI API and reports on the contents of specified
knowledge collections, including total file count, duplicate detection
(by filename), and a sample of the first 10 entries.

This is a one-off diagnostic/debugging tool run directly after sync operations
to verify that knowledge collections are in the expected state.

Run directly: ``python verify_fix.py``
  Checks both "Evelyn Vault Gists" and "Evelyn's Memory" collections.
"""
import os
import requests
import json

API_BASE = "http://localhost:8080/api/v1"
API_KEY = "sk-768aef85b01e44349aae5a536cc47c10"
headers = {"Authorization": f"Bearer {API_KEY}"}

def check_collection(name):
    """
    Fetches and audits a knowledge collection by name.

    Prints to stdout:
      - Collection UUID.
      - Raw response type (for debugging API schema changes).
      - Total number of file entries found.
      - Any filenames that appear more than once (duplicates).
      - A sample of the first 10 file names and their IDs.

    Args:
        name: The exact display name of the knowledge collection to audit
            (e.g. ``"Evelyn Vault Gists"`` or ``"Evelyn's Memory"``).
    """
    print(f"\n--- Checking Collection: {name} ---")
    response = requests.get(f"{API_BASE}/knowledge/", headers=headers)
    if not response.ok:
        print(f"Failed to list collections: {response.text}")
        return

    collections = response.json()
    if isinstance(collections, dict):
        collections = collections.get("items", [])

    target = None
    for col in collections:
        if col.get("name") == name:
            target = col
            break

    if not target:
        print(f"Collection '{name}' not found.")
        return

    col_id = target["id"]
    print(f"ID: {col_id}")

    files_resp = requests.get(f"{API_BASE}/knowledge/{col_id}/files", headers=headers)
    if files_resp.ok:
        data = files_resp.json()
        print(f"Debug - Raw files response type: {type(data)}")
        
        # Determine the list of files
        if isinstance(data, list):
            files = data
        elif isinstance(data, dict):
            files = data.get("items") or data.get("files") or []
        else:
            files = []

        print(f"Total entries found: {len(files)}")
        
        if len(files) > 0:
            print(f"Debug - First entry type: {type(files[0])}")
            print(f"Debug - First entry content: {files[0]}")

        # Count duplicates
        names = {}
        for f in files:
            if isinstance(f, dict):
                # Try common metadata fields
                meta = f.get("meta", {})
                fname = meta.get("name") or f.get("name") or f.get("filename") or "UNNAMED"
            else:
                fname = str(f)
            names[fname] = names.get(fname, 0) + 1
        
        duplicates = {n: c for n, c in names.items() if c > 1}
        if duplicates:
            print("Found duplicates by name:")
            for n, c in duplicates.items():
                print(f"  - {n}: {c} instances")
        else:
            print("No duplicates found by name.")
            
        # List first 10 files
        print("\nSample files:")
        for f in files[:10]:
            print(f"  - {f.get('meta', {}).get('name')} (ID: {f['id']})")
    else:
        print(f"Failed to list files: {files_resp.text}")

if __name__ == "__main__":
    check_collection("Evelyn Vault Gists")
    check_collection("Evelyn's Memory")
