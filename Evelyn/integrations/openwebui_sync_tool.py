"""
title: Sync Knowledge DBs
author: Ricky / Evelyn
description: Syncs Evelyn's memory by ingesting new or modified Obsidian Vault gists and Evelyn's core knowledge directory into the Open WebUI Knowledge DBs.
version: 1.1.0
license: MIT
"""

import sys
import os

# Ensure the tools directory is in the path so we can import ingest scripts.
TOOLS_DIR = r"C:\Projects\LocalAI\Evelyn\tools"
if TOOLS_DIR not in sys.path:
    sys.path.append(TOOLS_DIR)

try:
    import ingest_gists
    import ingest_obsidian_knowledge
except ImportError as e:
    ingest_gists = None
    ingest_obsidian_knowledge = None
    print(f"Failed to import ingestion scripts: {e}")


class Tools:
    def __init__(self):
        pass

    def sync_context_memory(self) -> str:
        """
        Triggers a synchronization of the Obsidian Vault gists and Evelyn's core knowledge directory into the remote Knowledge Collection.
        Call this tool when the user says "Good morning", explicitly asks you to update your memory, sync your context, or when you think your information is stale.

        :return: A status message indicating how many new files were processed.
        """
        if not ingest_gists:
            return "Error: Could not load the underlying ingestion script from the host system."

        import threading

        def background_task():
            try:
                # 1. Sync Core Memory (Full Text)
                if ingest_obsidian_knowledge:
                    print("Starting Core Memory Sync...")
                    ingest_obsidian_knowledge.main()

                # 2. Sync Vault Gists (Summaries)
                if ingest_gists:
                    print("Starting Vault Gists Sync...")
                    # Re-run the main logic, but capture the stats
                    vault_map_file = ingest_gists.VAULT_MAP_FILE
                sync_state_file = ingest_gists.SYNC_STATE_FILE

                if not os.path.exists(vault_map_file):
                    print(f"Error: Vault map file not found ({vault_map_file}).")
                    return

                import json
                import time

                with open(vault_map_file, "r", encoding="utf-8") as f:
                    vault_data = json.load(f)

                sync_state = {}
                if os.path.exists(sync_state_file):
                    with open(sync_state_file, "r", encoding="utf-8") as f:
                        sync_state = json.load(f)

                collection_id = ingest_gists.get_or_create_knowledge()
                if not collection_id:
                    print("Error: Could not connect to Open WebUI Knowledge API.")
                    return

                processed = 0
                errors = 0
                skipped = 0

                for file_path, file_info in vault_data.items():
                    mtime = file_info.get("mtime", 0)
                    data = file_info.get("data", {})
                    gist = data.get("gist", "")

                    if not gist:
                        continue

                    if file_path in sync_state and sync_state[file_path] >= mtime:
                        skipped += 1
                        continue

                    tags = data.get("tags", [])
                    links = data.get("links", [])

                    file_id = ingest_gists.upload_gist_file(
                        file_path, gist, tags, links
                    )
                    if file_id:
                        ingest_gists.add_file_to_knowledge(collection_id, file_id)
                        sync_state[file_path] = mtime
                        processed += 1
                    else:
                        errors += 1

                    time.sleep(0.1)

                with open(sync_state_file, "w", encoding="utf-8") as f:
                    json.dump(sync_state, f, indent=4)

                print(
                    f"Sync complete. Updated {processed} entries. Skipped {skipped}, {errors} errors."
                )

            except Exception as e:
                print(f"Error during background sync process: {str(e)}")

        thread = threading.Thread(target=background_task)
        thread.start()

        return "I have initiated the synchronization of my memory with your vault in the background. Please allow a few moments for the new context to become available."
