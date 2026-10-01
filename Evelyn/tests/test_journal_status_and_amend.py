# test_journal_status_and_amend.py
# date created: 2026-10-01 17:54:00
# date modified: 2026-10-01 17:55:24
# tags: #[test, #journaling, #journal-status, #amend, #procedure]

import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

import evelyn_config as cfg
from Evelyn.tools import db_migrator, evelyn_tools, journal_manager
from Evelyn.tools.frontmatter_utils import parse_frontmatter
from Evelyn.tools.string_utils import wrap_xml_envelope


@pytest.fixture
def temp_vault_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        journal_dir = os.path.join(tmpdir, "Evelyn", "Evelyn's Journal")
        os.makedirs(journal_dir, exist_ok=True)
        with patch.object(cfg, "JOURNAL_DIR", journal_dir), \
             patch.object(cfg, "VAULT_BASE_DIR", tmpdir), \
             patch.object(journal_manager, "JOURNAL_DIR", journal_dir):
            yield tmpdir, journal_dir


def test_create_new_journal_entry(temp_vault_env):
    """Test standard initial journal creation for a date."""
    _tmpdir, _journal_dir = temp_vault_env
    res = journal_manager.create_journal_entry(
        vibe_check="Quiet autumn afternoon.",
        narrative="Worked on taxonomy classification and engine telemetry.",
        message_in_a_bottle="Keep refining the architecture.",
        mood="Reflective",
        tags=["taxonomy", "engineering"],
        date_str="2026-10-01",
    )
    assert "successfully written to" in res

    entry_path = journal_manager._resolve_journal_filepath("2026-10-01")
    assert entry_path is not None
    assert os.path.exists(entry_path)

    with open(entry_path, encoding="utf-8") as f:
        content = f.read()

    fm, body = parse_frontmatter(content)
    assert fm.get("mood") == "Reflective"
    assert "taxonomy" in fm.get("tags", [])
    assert "engineering" in fm.get("tags", [])
    assert str(fm.get("occurred")) == "2026-10-01"
    assert "# Journal Entry 2026-10-01" in body
    assert "## Supplemental Entry" not in body


def test_amend_journal_entry_merges_tags_and_updates_body(temp_vault_env):
    """Test amend mode updates body in-place and merges frontmatter tags without creating supplemental sections."""
    _tmpdir, _journal_dir = temp_vault_env
    # 1. Create initial entry
    journal_manager.create_journal_entry(
        vibe_check="Initial thoughts.",
        narrative="First reflection of the evening.",
        message_in_a_bottle="Night is early.",
        mood="Pensive",
        tags=["evening", "reflection"],
        date_str="2026-10-01",
    )

    # 2. Amend existing entry
    res = journal_manager.create_journal_entry(
        vibe_check="Updated wind-down vibe.",
        narrative="Second reflection: wrapping up engineering tasks and heading to rest.",
        message_in_a_bottle="Rest well.",
        mood="Peaceful",
        tags=["sleep", "routine"],
        date_str="2026-10-01",
        mode="amend",
    )
    assert "successfully updated and amended" in res

    entry_path = journal_manager._resolve_journal_filepath("2026-10-01")
    assert entry_path is not None

    with open(entry_path, encoding="utf-8") as f:
        content = f.read()

    fm, body = parse_frontmatter(content)
    assert fm.get("mood") == "Peaceful"
    # Tags should be merged
    assert "evening" in fm.get("tags", [])
    assert "reflection" in fm.get("tags", [])
    assert "sleep" in fm.get("tags", [])
    assert "routine" in fm.get("tags", [])
    # Body should be updated in place without supplemental append
    assert "Second reflection: wrapping up engineering tasks" in body
    assert "## Supplemental Entry" not in body
    assert "First reflection of the evening" not in body


def test_overwrite_journal_entry(temp_vault_env):
    """Test overwrite mode completely replaces tags and body."""
    _tmpdir, _journal_dir = temp_vault_env
    journal_manager.create_journal_entry(
        vibe_check="First draft.",
        narrative="Draft text.",
        message_in_a_bottle="Draft bottle.",
        mood="Tired",
        tags=["draft-tag"],
        date_str="2026-10-01",
    )

    res = journal_manager.create_journal_entry(
        vibe_check="Final draft.",
        narrative="Completely rewritten body.",
        message_in_a_bottle="Final bottle.",
        mood="Accomplished",
        tags=["final-tag"],
        date_str="2026-10-01",
        mode="overwrite",
    )
    assert "successfully overwritten" in res

    entry_path = journal_manager._resolve_journal_filepath("2026-10-01")
    assert entry_path is not None

    with open(entry_path, encoding="utf-8") as f:
        content = f.read()

    fm, body = parse_frontmatter(content)
    assert fm.get("mood") == "Accomplished"
    assert fm.get("tags") == ["final-tag"]
    assert "draft-tag" not in fm.get("tags", [])
    assert "Completely rewritten body" in body


def test_write_journal_entry_tool_binding(temp_vault_env):
    """Test evelyn_tools.write_journal_entry respects mode argument."""
    _tmpdir, _journal_dir = temp_vault_env
    # First write
    res1 = evelyn_tools.write_journal_entry(
        mood="Calm",
        vibe_check="Vibe 1",
        narrative="Narrative 1",
        message_in_a_bottle="Bottle 1",
        tags="tag1",
    )
    assert "successfully written to" in res1

    # Second write with amend
    res2 = evelyn_tools.write_journal_entry(
        mood="Warm",
        vibe_check="Vibe 2",
        narrative="Narrative 2",
        message_in_a_bottle="Bottle 2",
        tags="tag2",
        mode="amend",
    )
    assert "successfully updated and amended" in res2


def test_model_tool_definition_schema():
    """Verify write_journal_entry schema in MODEL_TOOL_DEFINITIONS."""
    tool_def = next((t for t in evelyn_tools.MODEL_TOOL_DEFINITIONS if t["function"]["name"] == "write_journal_entry"), None)
    assert tool_def is not None
    props = tool_def["function"]["parameters"]["properties"]
    assert "mode" in props
    assert props["mode"]["enum"] == ["amend", "overwrite"]
    desc = tool_def["function"]["description"]
    assert "<journal_status>" in desc
    assert "status='recorded'" in desc


def test_journal_status_xml_envelopes():
    """Verify journal_status XML envelope rendering."""
    # When recorded:
    xml_recorded = wrap_xml_envelope(
        "journal_status",
        self_closing_if_empty=True,
        status="recorded",
        date="2026-10-01",
        path="Evelyn/Evelyn's Journal/Journal Entries/2026/10-Oct/Journal Entry 2026-10-01.md",
    )
    assert 'status="recorded"' in xml_recorded
    assert 'date="2026-10-01"' in xml_recorded
    assert 'path="Evelyn/Evelyn&apos;s Journal/Journal Entries/2026/10-Oct/Journal Entry 2026-10-01.md"' in xml_recorded

    # When none:
    xml_none = wrap_xml_envelope(
        "journal_status",
        self_closing_if_empty=True,
        status="none",
        date="2026-10-01",
    )
    assert 'status="none"' in xml_none
    assert 'date="2026-10-01"' in xml_none
    assert 'path=' not in xml_none


def test_procedure_1034_schema_and_steps():
    """Verify Procedure #1034 has updated evaluation gate directives."""
    memory_db_path = db_migrator.DB_MAP.get("memory") or cfg.MEMORY_DB_PATH
    assert os.path.exists(memory_db_path)

    conn = sqlite3.connect(memory_db_path)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM procedures WHERE id = 1034").fetchone()
        assert row is not None
        steps = row["steps"]
        assert "<journal_status>" in steps
        assert "status='recorded'" in steps
        assert "mode='amend'" in steps
        assert "suggested_tools" in dict(row)
        tools = row["suggested_tools"]
        assert "write_journal_entry" in tools
        assert "read_file" in tools
    finally:
        conn.close()
