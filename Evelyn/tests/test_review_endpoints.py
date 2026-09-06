# test_review_endpoints.py
# date created: 2026-09-03 19:47:07
# date modified: 2026-09-06 08:32:06
# tags: 

"""
Unit tests for review endpoints (extractions, proposals, procedures deletion & lifecycle)
and SQLite busy timeout resilience.
"""

import os
from pathlib import Path
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient

import evelyn_config as cfg
from Evelyn.tools import memory_db
from Evelyn.tools.db_migrator import BASELINE_CHAT_SQL, BASELINE_MEMORY_SQL
from evelyn_server import app


@pytest.fixture(autouse=True)
def isolated_dbs():
    """Create isolated temporary chat and memory SQLite databases for hermetic testing."""
    temp_dir = tempfile.TemporaryDirectory()
    temp_chat_db = os.path.join(temp_dir.name, "test_chat.db")
    temp_memory_db = os.path.join(temp_dir.name, "test_memory.db")

    with sqlite3.connect(temp_chat_db) as conn:
        conn.executescript(BASELINE_CHAT_SQL)

    with sqlite3.connect(temp_memory_db) as conn:
        conn.executescript(BASELINE_MEMORY_SQL)

    orig_chat_db = getattr(cfg, "CHAT_DB_PATH", None)
    orig_memory_db = getattr(cfg, "MEMORY_DB_PATH", None)

    cfg.CHAT_DB_PATH = temp_chat_db
    cfg.MEMORY_DB_PATH = temp_memory_db

    memory_db.init_db()

    yield

    if orig_chat_db is not None:
        cfg.CHAT_DB_PATH = orig_chat_db
    if orig_memory_db is not None:
        cfg.MEMORY_DB_PATH = orig_memory_db
    temp_dir.cleanup()


def test_delete_extraction_endpoint():
    """Verify that deleting an extraction via review endpoint removes it cleanly without blocking."""
    # 1. Insert an extracted entry
    eid = memory_db.insert_entry(
        category="Cat12-U",
        subject="Ricky",
        observation="Test observation for deletion.",
        status="extracted",
    )
    assert eid > 0
    assert memory_db.get_entry(eid) is not None

    client = TestClient(app)
    headers = {"X-Evelyn-Key": cfg.API_KEY} if cfg.API_KEY else {}

    # 2. Call DELETE endpoint
    res = client.post(f"/api/review/extractions/{eid}/delete", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

    # 3. Verify entry was permanently deleted
    assert memory_db.get_entry(eid) is None


def test_delete_proposal_endpoint():
    """Verify that deleting a proposal via review endpoint removes it cleanly without blocking."""
    prop_id = memory_db.insert_proposal(
        type="recategorize",
        source_ids=[],
        suggested_category="Cat02-U",
        reason="Test proposal delete",
    )
    assert prop_id > 0

    client = TestClient(app)
    headers = {"X-Evelyn-Key": cfg.API_KEY} if cfg.API_KEY else {}

    res = client.post(f"/api/review/proposals/{prop_id}/delete", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

    proposals = memory_db.get_pending_proposals()
    assert not any(p["id"] == prop_id for p in proposals)


def test_delete_procedure_endpoint():
    """Verify that deleting a procedure via review endpoint removes it cleanly without blocking."""
    proc_id = memory_db.insert_procedure(
        trigger_pattern="test procedure pattern",
        steps="1. Test steps.",
        status="extracted",
    )
    assert proc_id > 0

    client = TestClient(app)
    headers = {"X-Evelyn-Key": cfg.API_KEY} if cfg.API_KEY else {}

    res = client.post(f"/api/review/procedures/{proc_id}/delete", headers=headers)
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

    assert memory_db.get_procedure(proc_id) is None


def test_get_unified_review_endpoint():
    """Verify that get_unified_review returns extractions, proposals, and procedures."""
    eid = memory_db.insert_entry(
        category="Cat01-U",
        subject="User",
        observation="Pending extraction fact.",
        status="extracted",
    )
    proc_id = memory_db.insert_procedure(
        trigger_pattern="pending trigger",
        steps="Pending steps.",
        status="extracted",
    )

    client = TestClient(app)
    headers = {"X-Evelyn-Key": cfg.API_KEY} if cfg.API_KEY else {}

    res = client.get("/api/review/unified", headers=headers)
    assert res.status_code == 200
    items = res.json()
    assert any(i.get("item_type") == "extraction" and i.get("id") == eid for i in items)
    assert any(i.get("item_type") == "procedure" and i.get("id") == proc_id for i in items)


def test_ghost_link_stub_proposal_lifecycle():
    """Verify that ghost_link_stub proposals are exposed in unified/proposals endpoints,

    and approving writes the stub cleanly to the vault and updates vault_db and proposal status.
    """
    from Evelyn.tools import link_librarian, vault_db

    temp_vault_dir = tempfile.TemporaryDirectory()
    temp_vdb_file = os.path.join(temp_vault_dir.name, "test_vault.db")

    orig_vault_dir = getattr(cfg, "VAULT_BASE_DIR", None)
    orig_vault_db_cfg = getattr(cfg, "VAULT_DB_PATH", None)
    orig_vault_db_path = vault_db.DB_PATH

    cfg.VAULT_BASE_DIR = temp_vault_dir.name
    cfg.VAULT_DB_PATH = temp_vdb_file
    vault_db.DB_PATH = temp_vdb_file
    vault_db.init_db()

    try:
        payload = link_librarian.StubPayload(
            target_name="Test Ghost Tool",
            source_path="Hardware/Toolhead.md",
            context_excerpt="Mounting the [[Test Ghost Tool]] securely on carriage.",
            domain="hardware",
            tags=["stub", "hardware"],
            min_refs=2,
            ref_count=1,
        )
        xml_payload = link_librarian.render_stub_xml(payload)

        prop_id = memory_db.insert_proposal(
            type="ghost_link_stub",
            source_ids=[],
            topic="Test Ghost Tool",
            suggested_category="Hardware/Toolhead.md",
            reason="Ghost link [[Test Ghost Tool]] cited in 'Hardware/Toolhead.md' (1/2 references).",
            merged_observation=xml_payload,
            confidence="medium",
        )
        assert prop_id > 0

        client = TestClient(app)
        headers = {"X-Evelyn-Key": cfg.API_KEY} if cfg.API_KEY else {}

        # 1. Test GET /api/review/unified
        res_unified = client.get("/api/review/unified", headers=headers)
        assert res_unified.status_code == 200
        unified_items = res_unified.json()
        matching_u = next(
            (i for i in unified_items if i.get("id") == prop_id and i.get("type") == "ghost_link_stub"),
            None,
        )
        assert matching_u is not None
        assert matching_u["item_type"] == "proposal"
        assert matching_u["target"] == "Test Ghost Tool"
        assert matching_u["source_path"] == "Hardware/Toolhead.md"
        assert matching_u["parsed_payload"] is not None
        assert matching_u["parsed_payload"]["domain"] == "hardware"
        assert "stub" in matching_u["parsed_payload"]["tags"]

        # 2. Test GET /api/review/proposals
        res_props = client.get("/api/review/proposals", headers=headers)
        assert res_props.status_code == 200
        props_list = res_props.json()
        matching_p = next((p for p in props_list if p.get("id") == prop_id), None)
        assert matching_p is not None
        assert matching_p["parsed_payload"]["target_name"] == "Test Ghost Tool"

        # 3. Approve proposal
        res_approve = client.post(f"/api/review/proposals/{prop_id}/approve", headers=headers)
        assert res_approve.status_code == 200
        assert res_approve.json() == {"status": "ok"}

        # 4. Verify proposal status updated to applied
        pending = memory_db.get_pending_proposals()
        assert not any(p["id"] == prop_id for p in pending)

        con = memory_db.get_db()
        row = con.execute("SELECT status FROM proposals WHERE id = ?", (prop_id,)).fetchone()
        con.close()
        assert row is not None and row["status"] == "applied"

        # 5. Verify stub note was created in hermetic vault sandbox
        created_file = os.path.join(temp_vault_dir.name, "Test Ghost Tool.md")
        assert os.path.exists(created_file)
        note_text = Path(created_file).read_text(encoding="utf-8")

        assert "---" in note_text
        assert "title: Test Ghost Tool" in note_text
        assert "> [!ABSTRACT]" in note_text
        assert "> Conceptual entity stub for [[Test Ghost Tool]]" in note_text
        assert "# 🏛️ Test Ghost Tool" in note_text
        assert "## 🔗 References" in note_text

        # 6. Verify vault_db document record
        doc = vault_db.get_document("Test Ghost Tool.md")
        assert doc is not None
        assert doc["title"] == "Test Ghost Tool"
        assert "stub" in (doc["tags"] or "")

    finally:
        if orig_vault_dir is not None:
            cfg.VAULT_BASE_DIR = orig_vault_dir
        if orig_vault_db_cfg is not None:
            cfg.VAULT_DB_PATH = orig_vault_db_cfg
        vault_db.DB_PATH = orig_vault_db_path
        temp_vault_dir.cleanup()


def test_ghost_link_stub_proposal_deny():
    """Verify that denying a ghost_link_stub proposal rejects it without creating any files."""
    from Evelyn.tools import link_librarian, vault_db

    temp_vault_dir = tempfile.TemporaryDirectory()
    temp_vdb_file = os.path.join(temp_vault_dir.name, "test_vault.db")

    orig_vault_dir = getattr(cfg, "VAULT_BASE_DIR", None)
    orig_vault_db_cfg = getattr(cfg, "VAULT_DB_PATH", None)
    orig_vault_db_path = vault_db.DB_PATH

    cfg.VAULT_BASE_DIR = temp_vault_dir.name
    cfg.VAULT_DB_PATH = temp_vdb_file
    vault_db.DB_PATH = temp_vdb_file
    vault_db.init_db()

    try:
        payload = link_librarian.StubPayload(
            target_name="Spurious Target",
            source_path="Notes/SomeNote.md",
            context_excerpt="Spurious reference [[Spurious Target]].",
        )
        xml_payload = link_librarian.render_stub_xml(payload)

        prop_id = memory_db.insert_proposal(
            type="ghost_link_stub",
            source_ids=[],
            topic="Spurious Target",
            suggested_category="Notes/SomeNote.md",
            reason="Ghost link cited.",
            merged_observation=xml_payload,
        )

        client = TestClient(app)
        headers = {"X-Evelyn-Key": cfg.API_KEY} if cfg.API_KEY else {}

        res_deny = client.post(f"/api/review/proposals/{prop_id}/deny", headers=headers)
        assert res_deny.status_code == 200
        assert res_deny.json() == {"status": "ok"}

        # Verify status is rejected
        con = memory_db.get_db()
        row = con.execute("SELECT status FROM proposals WHERE id = ?", (prop_id,)).fetchone()
        con.close()
        assert row is not None and row["status"] == "rejected"

        # Verify no file created
        assert not os.path.exists(os.path.join(temp_vault_dir.name, "Spurious Target.md"))

    finally:
        if orig_vault_dir is not None:
            cfg.VAULT_BASE_DIR = orig_vault_dir
        if orig_vault_db_cfg is not None:
            cfg.VAULT_DB_PATH = orig_vault_db_cfg
        vault_db.DB_PATH = orig_vault_db_path
        temp_vault_dir.cleanup()


def test_ghost_link_stub_multi_reference_approval():
    """Verify approval of a multi-reference ghost link proposal generates a rich note with mentions and references."""
    from Evelyn.tools import link_librarian, vault_db

    temp_vault_dir = tempfile.TemporaryDirectory()
    temp_vdb_file = os.path.join(temp_vault_dir.name, "test_vault.db")

    orig_vault_dir = getattr(cfg, "VAULT_BASE_DIR", None)
    orig_vault_db_cfg = getattr(cfg, "VAULT_DB_PATH", None)
    orig_vault_db_path = vault_db.DB_PATH

    cfg.VAULT_BASE_DIR = temp_vault_dir.name
    cfg.VAULT_DB_PATH = temp_vdb_file
    vault_db.DB_PATH = temp_vdb_file
    vault_db.init_db()

    try:
        payload = link_librarian.StubPayload(
            target_name="Caladorn",
            source_path="Lore/History/Chronicles.md",
            context_excerpt="Prince Caladorn led the defense of the western frontier during the summer campaign.",
            domain="lore",
            tags=["stub", "character", "royalty"],
            min_refs=2,
            ref_count=2,
            sources=["Lore/History/Chronicles.md", "People/Queen_Elora.md"],
            references=[
                {
                    "source": "Lore/History/Chronicles.md",
                    "context": "Prince Caladorn led the defense of the western frontier during the summer campaign.",
                },
                {
                    "source": "People/Queen_Elora.md",
                    "context": "She consulted with Prince Caladorn regarding the diplomatic treaty and western borders.",
                },
            ],
            synthesized_abstract="Prince Caladorn is a renowned military commander and diplomatic advisor to the Queen.",
            total_context_chars=220,
        )
        xml_payload = link_librarian.render_stub_xml(payload)

        prop_id = memory_db.insert_proposal(
            type="ghost_link_stub",
            source_ids=[],
            topic="Caladorn",
            suggested_category="Lore/History/Chronicles.md",
            reason="Ghost link [[Caladorn]] cited across 2 notes with 220 characters of context.",
            merged_observation=xml_payload,
            confidence="high",
        )
        assert prop_id > 0

        client = TestClient(app)
        headers = {"X-Evelyn-Key": cfg.API_KEY} if cfg.API_KEY else {}

        # 1. Test GET /api/review/unified has multi-ref fields in parsed_payload
        res_unified = client.get("/api/review/unified", headers=headers)
        assert res_unified.status_code == 200
        matching_u = next((i for i in res_unified.json() if i.get("id") == prop_id), None)
        assert matching_u is not None
        assert matching_u["parsed_payload"]["synthesized_abstract"] == "Prince Caladorn is a renowned military commander and diplomatic advisor to the Queen."
        assert len(matching_u["parsed_payload"]["sources"]) == 2
        assert len(matching_u["parsed_payload"]["references"]) == 2
        assert matching_u["parsed_payload"]["total_context_chars"] == 220

        # 2. Test GET /api/review/proposals has multi-ref fields in parsed_payload
        res_props = client.get("/api/review/proposals", headers=headers)
        assert res_props.status_code == 200
        matching_p = next((p for p in res_props.json() if p.get("id") == prop_id), None)
        assert matching_p is not None
        assert matching_p["parsed_payload"]["ref_count"] == 2
        assert matching_p["parsed_payload"]["sources"] == ["Lore/History/Chronicles.md", "People/Queen_Elora.md"]

        # 3. Approve proposal
        res_approve = client.post(f"/api/review/proposals/{prop_id}/approve", headers=headers)
        assert res_approve.status_code == 200
        assert res_approve.json() == {"status": "ok"}

        # 4. Verify note creation and markdown structure
        created_file = os.path.join(temp_vault_dir.name, "Caladorn.md")
        assert os.path.exists(created_file)
        note_text = Path(created_file).read_text(encoding="utf-8")

        assert "title: Caladorn" in note_text
        assert "> [!ABSTRACT]" in note_text
        assert "> Prince Caladorn is a renowned military commander and diplomatic advisor to the Queen." in note_text
        assert "## 🧭 Context & Mentions" in note_text
        assert "- **[[Chronicles]]**: \"Prince Caladorn led the defense" in note_text
        assert "- **[[Queen_Elora]]**: \"She consulted with Prince Caladorn" in note_text
        assert "## 🔗 References" in note_text
        assert "- [[Chronicles]]" in note_text
        assert "- [[Queen_Elora]]" in note_text

        # 5. Verify vault_db document record
        doc = vault_db.get_document("Caladorn.md")
        assert doc is not None
        assert doc["title"] == "Caladorn"
        assert "stub" in (doc["tags"] or "")

    finally:
        if orig_vault_dir is not None:
            cfg.VAULT_BASE_DIR = orig_vault_dir
        if orig_vault_db_cfg is not None:
            cfg.VAULT_DB_PATH = orig_vault_db_cfg
        vault_db.DB_PATH = orig_vault_db_path
        temp_vault_dir.cleanup()

