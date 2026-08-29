# test_dream_manager_and_procedures_cleanup.py
# date created: 2026-08-29 07:48:00
# date modified: 2026-08-29 07:48:00
# tags: #tests, #dreams, #procedures, #consolidation

"""
test_dream_manager_and_procedures_cleanup.py — Tests for write_dream_entry tool,
dream_manager vault note creation, token-scored procedure search, and domain clustering.
"""

import os
import shutil
import tempfile
import pytest

import evelyn_config as cfg
from Evelyn.tools import dream_manager, evelyn_tools, memory_db, procedure_consolidator
from Evelyn.tools.frontmatter_utils import parse_frontmatter


@pytest.fixture
def temp_vault_dir(monkeypatch):
    """Fixture providing a temporary obsidian vault directory."""
    temp_dir = tempfile.mkdtemp(prefix="test_vault_")
    monkeypatch.setattr(cfg, "VAULT_BASE_DIR", temp_dir)
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_create_dream_entry_new_note(temp_vault_dir):
    """Test creating a fresh Dream Entry markdown note in the vault."""
    result = dream_manager.create_dream_entry(
        title="Floating Cities",
        description="I was flying between floating glass towers above clouds.",
        date_str="2026-08-29",
        feelings="Peaceful and curious",
        tags=["flying", "sci-fi-dream"],
        analysis="Related to architectural design interest."
    )

    assert "Successfully created new dream entry" in result
    dream_file = os.path.join(temp_vault_dir, "Dream Journal", "Dream Entries", "Dream Entry 2026-08-29.md")
    assert os.path.exists(dream_file)

    with open(dream_file, encoding="utf-8") as f:
        content = f.read()

    meta, body = parse_frontmatter(content)
    assert meta["title"] == "Dream Entry 2026-08-29"
    assert "CY-2026/08/29" in meta["tags"]
    assert "flying" in meta["tags"]
    assert "dream" in meta["tags"]
    assert "## Dream Title: Floating Cities" in body
    assert "Dream Description: I was flying between floating glass towers above clouds." in body
    assert "Initial Feelings/Thoughts: Peaceful and curious" in body
    assert "Analytical Notes: Related to architectural design interest." in body


def test_create_dream_entry_append_note(temp_vault_dir):
    """Test appending a second dream on the same calendar date."""
    # First dream
    dream_manager.create_dream_entry(
        title="First Dream",
        description="First dream narrative.",
        date_str="2026-08-29",
        feelings="Calm",
        tags=["first-dream"]
    )

    # Second dream
    result2 = dream_manager.create_dream_entry(
        title="Second Dream",
        description="Second dream narrative with different elements.",
        date_str="2026-08-29",
        feelings="Wired",
        tags=["second-dream"]
    )

    assert "Successfully appended dream" in result2
    dream_file = os.path.join(temp_vault_dir, "Dream Journal", "Dream Entries", "Dream Entry 2026-08-29.md")

    with open(dream_file, encoding="utf-8") as f:
        content = f.read()

    meta, body = parse_frontmatter(content)
    assert "first-dream" in meta["tags"]
    assert "second-dream" in meta["tags"]
    assert "## Dream Title: First Dream" in body
    assert "## Dream Title: Second Dream" in body
    assert "Dream Description: Second dream narrative with different elements." in body


def test_evelyn_tools_write_dream_entry(temp_vault_dir):
    """Test dispatching write_dream_entry via evelyn_tools."""
    res = evelyn_tools.write_dream_entry(
        title="Labyrinth of Books",
        description="Searching through an endless library for a specific note.",
        date="2026-08-30",
        feelings="Determined",
        tags="library, books, search"
    )
    assert "Successfully created new dream entry" in res


def test_procedure_search_scoring(monkeypatch):
    """Test that search_procedures_by_trigger returns accurate matches with relevance scoring."""
    # Query with specific domain keywords
    results = memory_db.search_procedures_by_trigger("Ricky asks to log a dream entry from last night")
    assert len(results) > 0
    # Top result should be the dream entry procedure
    top_proc = results[0]
    assert "dream" in top_proc["trigger_pattern"].lower()


def test_domain_synonym_extraction():
    """Test that _extract_keywords tags domain synonym markers correctly."""
    kws = procedure_consolidator._extract_keywords("When the user is preparing for sleep at bedtime")
    assert "domain_journal" in kws

    kws_dream = procedure_consolidator._extract_keywords("When analyzing a recurring dream")
    assert "domain_dream" in kws_dream
