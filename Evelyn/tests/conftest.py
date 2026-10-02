# conftest.py
# date created: 2026-08-31 17:47:00
# date modified: 2026-10-02 16:50:29
# tags: #pytest, #fixtures, #testing, #sandbox

"""Pytest configuration and global test harness isolation.

Provides an autouse fixture ensuring all pytest runs execute inside an ephemeral,
hermetic temporary sandbox directory. Automatically isolates ``cfg.VAULT_BASE_DIR``,
``cfg.JOURNAL_DIR``, ``cfg.LISTS_DIR``, ``cfg.PENDING_DIR``, and related write paths
to prevent any test execution from touching the user's production Obsidian vault.

Also isolates ``cfg.MEMORY_DB_PATH``, ``cfg.CHAT_DB_PATH``, and ``cfg.CHROMA_DB_PATH``.
The vault database was sandboxed here from the start but the memory database and ChromaDB
were not, leaving tests that touched memory or vectors vulnerable to writing to the live
store or colliding with the engine's single-writer lease (AGENTS.md §2). A test that needs
these may still point them wherever it likes; the default is completely isolated in the
ephemeral test sandbox.
"""

import os
import tempfile
from collections.abc import Generator

import pytest

import evelyn_config as cfg  # [[evelyn_config.py]]
from Evelyn.tools import chroma_rag, memory_db, terminal_agent, vault_db


@pytest.fixture(autouse=True, scope="function")
def isolate_test_vault_environment() -> Generator[str]:
    """Isolate vault write paths, SQLite databases, and ChromaDB store to a temporary directory."""
    orig_vault_base = getattr(cfg, "VAULT_BASE_DIR", None)
    orig_assistant_write = getattr(cfg, "ASSISTANT_WRITE_DIR", None)
    orig_journal = getattr(cfg, "JOURNAL_DIR", None)
    orig_context = getattr(cfg, "CONTEXT_DIR", None)
    orig_research = getattr(cfg, "RESEARCH_VAULT_DIR", None)
    orig_pending = getattr(cfg, "PENDING_DIR", None)
    orig_lists = getattr(cfg, "LISTS_DIR", None)
    orig_vault_db_cfg = getattr(cfg, "VAULT_DB_PATH", None)
    orig_vault_db_mod = getattr(vault_db, "DB_PATH", None)
    orig_memory_db = getattr(cfg, "MEMORY_DB_PATH", None)
    orig_chat_db = getattr(cfg, "CHAT_DB_PATH", None)
    orig_chroma_db_cfg = getattr(cfg, "CHROMA_DB_PATH", None)
    orig_chroma_dir_mod = getattr(chroma_rag, "_CHROMA_DIR", None)
    orig_approvals_cfg = getattr(cfg, "TERMINAL_APPROVALS_PATH", None)
    orig_approvals_mod = getattr(terminal_agent, "APPROVALS_FILE", None)

    with tempfile.TemporaryDirectory(prefix="evelyn_test_vault_") as tmp_vault:
        # Construct isolated mock vault directory hierarchy
        assistant_name = getattr(cfg, "ASSISTANT_NAME", "Evelyn")
        assistant_write_dir = os.path.join(tmp_vault, assistant_name)
        journal_dir = os.path.join(assistant_write_dir, f"{assistant_name}'s Journal")
        context_dir = os.path.join(assistant_write_dir, f"{assistant_name}'s Context")
        research_dir = os.path.join(assistant_write_dir, "Research")
        pending_dir = os.path.join(assistant_write_dir, "Pending_Approvals")
        lists_dir = os.path.join(tmp_vault, "Lists")
        test_vault_db = os.path.join(tmp_vault, "test_evelyn_vault.db")

        os.makedirs(journal_dir, exist_ok=True)
        os.makedirs(context_dir, exist_ok=True)
        os.makedirs(research_dir, exist_ok=True)
        os.makedirs(pending_dir, exist_ok=True)
        os.makedirs(lists_dir, exist_ok=True)

        cfg.VAULT_BASE_DIR = tmp_vault
        cfg.ASSISTANT_WRITE_DIR = assistant_write_dir
        cfg.JOURNAL_DIR = journal_dir
        cfg.CONTEXT_DIR = context_dir
        cfg.RESEARCH_VAULT_DIR = research_dir
        cfg.PENDING_DIR = pending_dir
        cfg.LISTS_DIR = lists_dir
        cfg.VAULT_DB_PATH = test_vault_db
        vault_db.DB_PATH = test_vault_db
        vault_db.init_db()

        cfg.MEMORY_DB_PATH = os.path.join(tmp_vault, "test_evelyn_memory.db")
        cfg.CHAT_DB_PATH = os.path.join(tmp_vault, "test_evelyn_chat.db")
        # Created empty, so a test that reaches memory incidentally gets a valid empty store
        # rather than "no such table" — the failure mode that previously pushed such tests
        # onto the production database.
        memory_db.init_db()

        # Staging a write appends to the terminal approvals store. That file was the
        # production one, so a test exercising write_file left a real pending approval in
        # the user's queue.
        approvals = os.path.join(tmp_vault, "test_terminal_approvals.json")
        cfg.TERMINAL_APPROVALS_PATH = approvals
        terminal_agent.APPROVALS_FILE = approvals

        # Isolate ChromaDB vector store into ephemeral sandbox and reset client singleton
        test_chroma_dir = os.path.join(tmp_vault, "test_chroma_db")
        os.makedirs(test_chroma_dir, exist_ok=True)
        cfg.CHROMA_DB_PATH = test_chroma_dir
        chroma_rag._CHROMA_DIR = test_chroma_dir
        chroma_rag._client = None

        try:
            yield tmp_vault
        finally:
            chroma_rag.release_chroma_writer()
            chroma_rag._client = None
            if orig_vault_base is not None:
                cfg.VAULT_BASE_DIR = orig_vault_base
            if orig_assistant_write is not None:
                cfg.ASSISTANT_WRITE_DIR = orig_assistant_write
            if orig_journal is not None:
                cfg.JOURNAL_DIR = orig_journal
            if orig_context is not None:
                cfg.CONTEXT_DIR = orig_context
            if orig_research is not None:
                cfg.RESEARCH_VAULT_DIR = orig_research
            if orig_pending is not None:
                cfg.PENDING_DIR = orig_pending
            if orig_lists is not None:
                cfg.LISTS_DIR = orig_lists
            if orig_vault_db_cfg is not None:
                cfg.VAULT_DB_PATH = orig_vault_db_cfg
            if orig_vault_db_mod is not None:
                vault_db.DB_PATH = orig_vault_db_mod
            if orig_memory_db is not None:
                cfg.MEMORY_DB_PATH = orig_memory_db
            if orig_chat_db is not None:
                cfg.CHAT_DB_PATH = orig_chat_db
            if orig_chroma_db_cfg is not None:
                cfg.CHROMA_DB_PATH = orig_chroma_db_cfg
            if orig_chroma_dir_mod is not None:
                chroma_rag._CHROMA_DIR = orig_chroma_dir_mod
            if orig_approvals_cfg is not None:
                cfg.TERMINAL_APPROVALS_PATH = orig_approvals_cfg
            if orig_approvals_mod is not None:
                terminal_agent.APPROVALS_FILE = orig_approvals_mod
