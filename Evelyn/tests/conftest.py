# conftest.py
# date created: 2026-08-31 17:47:00
# date modified: 2026-08-31 17:47:14
# tags: #pytest, #fixtures, #testing, #sandbox

"""Pytest configuration and global test harness isolation.

Provides an autouse fixture ensuring all pytest runs execute inside an ephemeral,
hermetic temporary sandbox directory. Automatically isolates ``cfg.VAULT_BASE_DIR``,
``cfg.JOURNAL_DIR``, ``cfg.LISTS_DIR``, ``cfg.PENDING_DIR``, and related write paths
to prevent any test execution from touching the user's production Obsidian vault.
"""

import os
import tempfile
from collections.abc import Generator

import pytest

import evelyn_config as cfg  # [[evelyn_config.py]]


@pytest.fixture(autouse=True, scope="function")
def isolate_test_vault_environment() -> Generator[str]:
    """Isolate vault write paths to a temporary directory for every test run."""
    orig_vault_base = getattr(cfg, "VAULT_BASE_DIR", None)
    orig_assistant_write = getattr(cfg, "ASSISTANT_WRITE_DIR", None)
    orig_journal = getattr(cfg, "JOURNAL_DIR", None)
    orig_context = getattr(cfg, "CONTEXT_DIR", None)
    orig_research = getattr(cfg, "RESEARCH_VAULT_DIR", None)
    orig_pending = getattr(cfg, "PENDING_DIR", None)
    orig_lists = getattr(cfg, "LISTS_DIR", None)

    with tempfile.TemporaryDirectory(prefix="evelyn_test_vault_") as tmp_vault:
        # Construct isolated mock vault directory hierarchy
        assistant_name = getattr(cfg, "ASSISTANT_NAME", "Evelyn")
        assistant_write_dir = os.path.join(tmp_vault, assistant_name)
        journal_dir = os.path.join(assistant_write_dir, f"{assistant_name}'s Journal")
        context_dir = os.path.join(assistant_write_dir, f"{assistant_name}'s Context")
        research_dir = os.path.join(assistant_write_dir, "Research")
        pending_dir = os.path.join(assistant_write_dir, "Pending_Approvals")
        lists_dir = os.path.join(tmp_vault, "Lists")

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

        try:
            yield tmp_vault
        finally:
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
