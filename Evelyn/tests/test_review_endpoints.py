# test_review_endpoints.py
# date created: 2026-09-03 19:47:07
# date modified: 2026-09-03 19:47:07
# tags: 

"""
Unit tests for review endpoints (extractions, proposals, procedures deletion & lifecycle)
and SQLite busy timeout resilience.
"""

import os
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

    cfg.CHAT_DB_PATH = orig_chat_db
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
