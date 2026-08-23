# test_vault_move_optimization.py
# date created: 2026-08-22 19:10:00
# tags: #test, #vault, #move, #chroma, #optimization, #sqlite

import os
import time
import pytest

import Evelyn.tools.vault_db as vault_db
import Evelyn.tools.chroma_rag as chroma_rag
import Evelyn.tools.memory_db as memory_db
from Evelyn.tools.ingest_obsidian_knowledge import compute_content_hash


def test_vault_db_move_document(monkeypatch, tmp_path):
    """Verify vault_db.move_document updates relative paths atomically while preserving metadata."""
    test_db = str(tmp_path / "test_vault.db")
    monkeypatch.setattr(vault_db, "DB_PATH", test_db)
    vault_db.init_db()

    # Insert initial document
    old_path = "Reference Library/AI/old_note.md"
    new_path = "Reference Library/AI/Agents/new_note.md"
    now = time.time()

    vault_db.upsert_document(
        path=old_path,
        title="Agent Frameworks",
        mtime=now,
        gist="Detailed analysis of agent architectures.",
        rag_priority="high",
        rag_pinned=True,
        tags="Tech/AI,Tech/Architecture",
        aliases="Agents Note,Frameworks",
    )

    doc_before = vault_db.get_document(old_path)
    assert doc_before is not None
    assert doc_before["title"] == "Agent Frameworks"
    assert doc_before["rag_priority"] == "high"

    # Execute move
    success = vault_db.move_document(old_path, new_path)
    assert success is True

    # Old path must be gone, new path must have preserved fields
    assert vault_db.get_document(old_path) is None
    doc_after = vault_db.get_document(new_path)
    assert doc_after is not None
    assert doc_after["title"] == "Agent Frameworks"
    assert doc_after["gist"] == "Detailed analysis of agent architectures."
    assert doc_after["rag_priority"] == "high"
    assert doc_after["rag_pinned"] == 1
    assert "Tech/AI" in doc_after["tags"]


def test_vault_db_get_all_entities(monkeypatch, tmp_path):
    """Verify vault_db.get_all_entities returns known note titles and parsed aliases."""
    test_db = str(tmp_path / "test_vault_entities.db")
    monkeypatch.setattr(vault_db, "DB_PATH", test_db)
    vault_db.init_db()

    vault_db.upsert_document(
        path="Projects/Terminal.md",
        title="FastAPI Terminal Agent",
        mtime=time.time(),
        aliases="Terminal, CLI Agent",
    )
    vault_db.upsert_document(
        path="Contacts/Ricky.md",
        title="Ricky Sekulich",
        mtime=time.time(),
        aliases="Operator, Ricky",
    )

    entities = vault_db.get_all_entities()
    assert len(entities) == 2
    titles = [e["title"] for e in entities]
    assert "FastAPI Terminal Agent" in titles
    assert "Ricky Sekulich" in titles

    for e in entities:
        if e["title"] == "FastAPI Terminal Agent":
            assert "Terminal" in e["aliases"]
            assert "CLI Agent" in e["aliases"]


def test_compute_content_hash():
    """Verify SHA-256 content hashing computes stable hex digests."""
    text1 = "# Sample Note\n\nContent here."
    text2 = "# Sample Note\n\nContent here."
    text3 = "# Sample Note\n\nModified content."

    assert compute_content_hash(text1) == compute_content_hash(text2)
    assert compute_content_hash(text1) != compute_content_hash(text3)
    assert len(compute_content_hash(text1)) == 64


def test_chroma_direct_remap_and_queue(monkeypatch, tmp_path):
    """Verify direct_remap and enqueue_remap update document chunks in Chroma and staging queue."""
    import evelyn_config as cfg
    test_queue_db = str(tmp_path / "test_chroma_queue.db")
    monkeypatch.setattr(cfg, "MEMORY_DB_PATH", test_queue_db)
    monkeypatch.setattr(chroma_rag, "_MEMORY_DB_PATH", test_queue_db)
    memory_db.init_db()

    old_src = "test::note_old.md"
    new_src = "test::note_new.md"

    # 1. Enqueue remap
    enqueued = chroma_rag.enqueue_remap(old_src, new_src, collection_name="evelyn_memory")
    assert enqueued is True

    # 2. Check row in queue
    con = chroma_rag._get_queue_db()
    row = con.execute("SELECT action, source_path, content FROM chroma_sync_queue WHERE source_path = ?", (old_src,)).fetchone()
    con.close()

    assert row is not None
    assert row["action"] == "remap"
    assert row["source_path"] == old_src
    assert row["content"] == new_src

