"""
title: Sync Knowledge DBs
author: Ricky / Evelyn
description: Syncs Evelyn's memory by ingesting new or modified Obsidian Vault gists
             and Evelyn's core knowledge directory into the Open WebUI Knowledge DBs.
version: 1.3.0
license: MIT
"""

# --- Module Overview ---
# This file is an Open WebUI Tool (uploaded via the Tools UI).
# It exposes a single callable: `Tools.sync_context_memory()`.
#
# When Evelyn calls that tool, it spawns a background thread that runs two
# sequential sync operations:
#   1. Core Memory  — calls `ingest_obsidian_knowledge.main()` to push full
#      journal + context markdown files into the "Evelyn's Memory" collection.
#   2. Vault Gists  — calls `ingest_gists.main()` to upload LLM-generated
#      gist summaries for every new/changed file into the "Evelyn Vault Gists"
#      collection.
#
# Both ingest modules are hot-reloaded at call time from TOOLS_DIR so that live
# edits to those files take effect without restarting Open WebUI.

import sys
import importlib

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


def _reload_modules():
    """
    Reloads both ingest modules from disk so that live edits take effect
    without requiring an Open WebUI restart.
    """
    global ingest_gists, ingest_obsidian_knowledge
    try:
        if ingest_gists:
            ingest_gists = importlib.reload(ingest_gists)
        if ingest_obsidian_knowledge:
            ingest_obsidian_knowledge = importlib.reload(ingest_obsidian_knowledge)
    except Exception as e:
        print(f"Warning: module reload failed: {e}")


class Tools:
    def __init__(self):
        pass

    def sync_context_memory(self) -> str:
        """
        Triggers a synchronization of the Obsidian Vault gists and Evelyn's core
        knowledge directory into the remote Knowledge Collections.

        Call this tool ONCE at the start of a conversation when Ricky says "Good
        morning", or if he explicitly asks you to sync, update your memory, or
        refresh your context. Do NOT call this tool more than once per session.

        :return: A status message indicating that sync has been initiated.
        """
        if not ingest_gists or not ingest_obsidian_knowledge:
            return "Error: Could not load the underlying ingestion scripts from the host system."

        import threading

        def background_task():
            """
            Runs the two-phase knowledge sync sequentially in a background thread
            so the tool can return immediately to the chat UI without blocking.

            Phase 1 — Core Memory:
                Delegates to ``ingest_obsidian_knowledge.main()``, which handles
                state tracking, GC, and upload of full journal/context files.

            Phase 2 — Vault Gists:
                Delegates to ``ingest_gists.main()``, which handles state tracking,
                GC, and upload of LLM-generated gist summaries.

            Both modules are reloaded before use so that any edits made to the
            scripts since Open WebUI started are picked up automatically.
            """
            try:
                _reload_modules()

                # Phase 1: Core Memory (full text files)
                print("Starting Core Memory Sync...")
                ingest_obsidian_knowledge.main()

                # Phase 2: Vault Gists (LLM summaries)
                print("Starting Vault Gists Sync...")
                ingest_gists.main()

                print("Both sync operations complete.")

            except Exception as e:
                print(f"Error during background sync: {str(e)}")

        thread = threading.Thread(target=background_task)
        thread.start()

        return "I have initiated the synchronization of my memory with your vault in the background. Please allow a few moments for the new context to become available."
