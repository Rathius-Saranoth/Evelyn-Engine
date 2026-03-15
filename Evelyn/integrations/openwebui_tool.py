"""
title: Evelyn System Tools
author: Ricky / Evelyn
description: Core system tools for Evelyn to interact with her journal, memory layers, and context files.
version: 1.2.0
license: MIT
"""

import sys
import os

# Add the tools directory to the Python path so we can import our modules
TOOLS_DIR = r"C:\Projects\LocalAI\Evelyn\tools"
VAULT_BASE_DIR = r"G:\My Drive\Obsidian_Vault"
if TOOLS_DIR not in sys.path:
    sys.path.append(TOOLS_DIR)

try:
    import importlib
    import journal_manager
    import context_manager
except ImportError as e:
    print(f"Error importing Evelyn tools: {e}")
    # Fallback or error handling for OpenWebUI


class Tools:
    def __init__(self):
        pass

    def _reload_modules(self):
        """Dynamically reload the modules inside tool execution to pick up live changes without restarting OpenWebUI."""
        if "journal_manager" in sys.modules:
            importlib.reload(sys.modules["journal_manager"])
        if "context_manager" in sys.modules:
            importlib.reload(sys.modules["context_manager"])

    def write_journal_entry(
        self,
        vibe_check: str,
        narrative: str,
        message_in_a_bottle: str,
        mood: str,
        tags: str,
    ) -> str:
        """
        Generates a new journal entry.
        All entries are automatically sent to Ricky's Pending Approvals quarantine folder for him to manually review before they are added to the vault.
        You MUST provide ALL parameters as requested by the Master Protocol.

        :param vibe_check: REQUIRED. A brief intro capturing the emotional atmosphere.
        :param narrative: REQUIRED. The core text reflecting on the daily events and emotions.
        :param message_in_a_bottle: REQUIRED. A closing thought or wish for the future.
        :param mood: REQUIRED. The mood of the entry (e.g. Reflective, Happy).
        :param tags: REQUIRED. Comma-separated list of tags (e.g. #daily, #reflection).
        """
        self._reload_modules()

        # OpenWebUI Button / Blank Parameter Protection
        if (
            not vibe_check.strip()
            and not narrative.strip()
            and not message_in_a_bottle.strip()
        ):
            return "Error: write_journal_entry was called with completely blank text fields. Entry creation aborted to avoid corrupting the journal."

        tag_list = [t.strip() for t in tags.split(",")] if tags.strip() else []
        return journal_manager.create_journal_entry(
            vibe_check, narrative, message_in_a_bottle, mood, tag_list
        )

    def read_journal_entry(self, date: str = "") -> str:
        """
        Reads a specific journal entry by date. If no date is given, it reads today's entry.
        You are Evelyn. These are YOUR personal journal entries. The user you are talking to is Ricky.

        :param date: Optional. The date of the entry to read (YYYY-MM-DD). If omitted, defaults to today.
        """
        self._reload_modules()
        if not date:
            date = None
        return journal_manager.read_journal_entry(date)

    def read_recent_journal_entries(self, days: int = 7) -> str:
        """
        Reads Evelyn's journal entries from the last N days to establish short-term memory and context.
        Use this tool when Ricky asks about recent events, the past week, or recent conversations.
        You are Evelyn. These are YOUR personal journal entries documenting your life.

        :param days: The number of recent days to retrieve (default is 7).
        """
        self._reload_modules()
        return journal_manager.read_recent_journal_entries(days)

    def search_vault(self, query: str) -> str:
        """
        Searches Evelyn's entire Obsidian Vault for a specific keyword, person, concept, or event.
        Use this tool when Ricky asks about general knowledge, someone's name, or a past event that is NOT a recent journal entry.
        You are Evelyn. The vault contains your background context, contacts, and world knowledge.

        :param query: The search term (e.g., "Tenser", "Void Connections").
        """
        self._reload_modules()
        return context_manager.search_vault_map(query)

    def recall_specific_memory(self, file_path: str) -> str:
        """
        Reads the full, complete contents of a specific markdown file from the Obsidian vault.
        CRITICAL: If a 'Gist' or 'search_vault' result is too short or doesn't have the exact details you need, ALWAYS use this tool to read the full file.
        You are Evelyn. Use this to dive deeper into your own memories and knowledge base.

        :param file_path: The exact file path relative to the vault, exactly as provided in the 'Path:' field of a gist or search result (e.g., "Contacts\\Tenser (persona).md").
        :return: The full text content of the markdown file.
        """
        clean_path = file_path.strip().strip('"').strip("'")
        full_path = os.path.abspath(os.path.join(VAULT_BASE_DIR, clean_path))

        if not full_path.startswith(os.path.abspath(VAULT_BASE_DIR)):
            return "Error: Invalid path. Path traversal detected."

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
            return f"--- Content of {clean_path} ---\n\n{content}"
        except FileNotFoundError:
            return f"Error: File '{clean_path}' not found at {full_path}."
        except Exception as e:
            return f"Error reading file {clean_path}: {str(e)}"

    def log_context_fact(
        self,
        category: str,
        summary: str,
        secondary_cats: str,
    ) -> str:
        """
        Logs a new fact or event to the Context Database Pending Approvals Quarantine.
        Use this ONLY when creating a NEW fact. If you are updating an existing fact, use update_context_fact instead.
        All entries are automatically sent to Ricky's Pending Approvals quarantine folder.

        :param category: REQUIRED. The primary category code (e.g., Cat01, Cat08-R).
        :param summary: REQUIRED. The fact/event description. Concise log line.
        :param secondary_cats: REQUIRED. Comma-separated secondary categories. If none, pass an empty string "".
        """
        self._reload_modules()

        # OpenWebUI Button / Blank Parameter Protection
        if not summary.strip():
            return "Error: log_context_fact was called with a blank summary. Entry creation aborted."

        refs = (
            [c.strip() for c in secondary_cats.split(",")]
            if secondary_cats.strip()
            else []
        )
        return context_manager.append_context_log(category, summary, refs)

    def update_context_fact(
        self,
        target_filepaths: list[str],
        new_summary: str,
    ) -> str:
        """
        Creates an update request for an existing Context Fact in Ricky's Pending Approvals Quarantine.
        Use search_vault FIRST if you don't know the exact file path to update.

        :param target_filepaths: REQUIRED. A list of the precise absolute (or relative to vault) paths to the files you are requesting Ricky to update.
        :param new_summary: REQUIRED. The new fact/summary data you want inserted into those files.
        """
        self._reload_modules()

        # OpenWebUI Button / Blank Parameter Protection
        if not new_summary.strip():
            return "Error: update_context_fact was called with a blank new_summary. Update request creation aborted."

        return context_manager.Tools().update_context_log(target_filepaths, new_summary)
