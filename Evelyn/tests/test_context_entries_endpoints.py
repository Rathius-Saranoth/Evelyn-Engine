# test_context_entries_endpoints.py
# date created: 2026-10-15 11:55:00
# date modified: 2026-10-15 12:26:34
# tags: #test, #context_entries, #api, #endpoints

"""Unit tests for the context_entries search, edit, queueing, and proposal endpoints."""

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

    if orig_chat_db is not None:
        cfg.CHAT_DB_PATH = orig_chat_db
    if orig_memory_db is not None:
        cfg.MEMORY_DB_PATH = orig_memory_db
    temp_dir.cleanup()


def test_search_and_metadata_endpoints():
    """Verify search, pagination, and filter metadata for context entries."""
    client = TestClient(app)

    # Insert test entries
    id1 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="User drinks dark roast coffee every morning.",
        confidence="high",
        status="live",
        date="2026-10-01",
        tags="coffee, morning, routine",
    )
    id2 = memory_db.insert_entry(
        category="Cat01-A",
        subject=cfg.ASSISTANT_NAME,
        observation="Assistant prefers concise replies with code examples.",
        confidence="medium",
        status="live",
        date="2026-10-02",
        tags="conciseness, code",
    )
    id3 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="User enjoys weekend mountain biking.",
        confidence="low",
        status="deleted",
        date="2026-10-03",
        tags="cycling, outdoors",
    )

    # 1. Metadata check
    res_meta = client.get("/api/context/entries/metadata")
    assert res_meta.status_code == 200
    meta = res_meta.json()
    assert meta["total_live"] == 2
    assert meta["total_all"] == 3
    assert meta["latest_date"] == "2026-10-02"
    cats = [c["category"] for c in meta["categories"]]
    assert "Cat05-U" in cats
    assert "Cat01-A" in cats

    # 2. Query search
    res_q = client.get("/api/context/entries?q=coffee")
    assert res_q.status_code == 200
    data_q = res_q.json()
    assert data_q["total"] == 1
    assert data_q["entries"][0]["id"] == id1

    # 3. Search by ID
    res_id = client.get(f"/api/context/entries?q={id2}")
    assert res_id.status_code == 200
    assert res_id.json()["entries"][0]["id"] == id2

    # 4. Filter by category
    res_cat = client.get("/api/context/entries?category=Cat01-A")
    assert res_cat.status_code == 200
    assert res_cat.json()["total"] == 1
    assert res_cat.json()["entries"][0]["id"] == id2

    # 5. Filter by status
    res_del = client.get("/api/context/entries?status=deleted")
    assert res_del.status_code == 200
    assert res_del.json()["total"] == 1
    assert res_del.json()["entries"][0]["id"] == id3

    # 6. Sort by last_observed
    res_sort = client.get("/api/context/entries?sort_by=last_observed&sort_order=desc&status=live")
    assert res_sort.status_code == 200
    assert len(res_sort.json()["entries"]) == 2


def test_direct_safe_save_with_chroma_queue():
    """Verify that editing an entry via PATCH updates SQLite and enqueues Chroma vector re-indexing."""
    client = TestClient(app)

    eid = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="Initial observation statement.",
        status="live",
        tags="initial, tag",
    )

    patch_payload = {
        "observation": "Updated observation statement with refined phrasing.",
        "category": "Cat08-U",
        "tags": "refined, update",
        "confidence": "high",
    }
    res = client.patch(f"/api/context/entries/{eid}", json=patch_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["mode"] == "direct_save"
    assert data["entry"]["observation"] == "Updated observation statement with refined phrasing."
    assert data["entry"]["category"] == "Cat08-U"

    # Verify SQLite row directly
    updated = memory_db.get_entry(eid)
    assert updated is not None
    assert updated["observation"] == "Updated observation statement with refined phrasing."
    assert updated["category"] == "Cat08-U"
    assert updated["confidence"] == "high"

    # Verify Chroma sync queue has an enqueued item (single-writer rule compliance)
    con = memory_db.get_db()
    csq_rows = con.execute(
        "SELECT * FROM chroma_sync_queue WHERE source_path = ?",
        (f"sqlite::context_entry::{eid}",),
    ).fetchall()
    con.close()
    assert len(csq_rows) == 1
    assert csq_rows[0]["action"] == "upsert"
    assert "Updated observation" in csq_rows[0]["content"]


def test_stage_as_proposal_mode():
    """Verify that staging an edit as a proposal inserts a pending rephrase proposal into the proposals table."""
    client = TestClient(app)

    eid = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="User lives in Portland.",
        status="live",
    )

    prop_payload = {
        "observation": "User recently relocated to Seattle.",
        "category": "Cat05-U",
        "reason": "Address change update",
    }
    res = client.post(f"/api/context/entries/{eid}/propose", json=prop_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["mode"] == "proposal_staged"
    pid = data["proposal_id"]

    # Verify original entry was NOT altered yet
    orig = memory_db.get_entry(eid)
    assert orig["observation"] == "User lives in Portland."

    # Verify proposal exists in proposals table
    con = memory_db.get_db()
    prop_row = con.execute("SELECT * FROM proposals WHERE id = ?", (pid,)).fetchone()
    con.close()
    assert prop_row is not None
    assert prop_row["type"] == "rephrase"
    assert prop_row["status"] == "pending"
    assert prop_row["merged_observation"] == "User recently relocated to Seattle."
    assert "Address change" in prop_row["reason"]


def test_queue_split_and_queue_merge():
    """Verify split and merge queueing and status annotations on entries."""
    client = TestClient(app)

    eid1 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="Compound fact containing multiple activities.",
        status="live",
    )
    eid2 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="Second fact for merge.",
        status="live",
    )

    # Enqueue split on eid1
    res_split = client.post(f"/api/context/{eid1}/queue_split")
    assert res_split.status_code == 200

    # Enqueue merge on eid1 + eid2
    res_merge = client.post("/api/context/queue_merge", json={"entry_ids": [eid1, eid2]})
    assert res_merge.status_code == 200

    # Fetch entries and verify queue annotations
    res_list = client.get(f"/api/context/entries?q={eid1}")
    assert res_list.status_code == 200
    item = res_list.json()["entries"][0]
    assert item["is_queued_split"] is True
    assert item["is_queued_merge"] is True


def test_create_and_delete_endpoints():
    """Verify creating a new context entry and soft/hard deleting."""
    client = TestClient(app)

    # 1. Create live entry
    create_payload = {
        "category": "Cat08-U",
        "observation": "User drinks matcha tea on Tuesdays.",
        "tags": "tea, routine",
        "confidence": "high",
        "stage_as_pending": False,
    }
    res_create = client.post("/api/context/entries", json=create_payload)
    assert res_create.status_code == 200
    new_id = res_create.json()["entry"]["id"]

    # Verify created
    entry = memory_db.get_entry(new_id)
    assert entry is not None
    assert entry["status"] == "live"
    assert "matcha" in entry["observation"]

    # 2. Soft delete
    res_del = client.delete(f"/api/context/entries/{new_id}")
    assert res_del.status_code == 200
    entry_after_soft = memory_db.get_entry(new_id)
    assert entry_after_soft["status"] == "deleted"

    # 3. Hard delete
    res_hard = client.delete(f"/api/context/entries/{new_id}?hard=true")
    assert res_hard.status_code == 200
    assert memory_db.get_entry(new_id) is None
