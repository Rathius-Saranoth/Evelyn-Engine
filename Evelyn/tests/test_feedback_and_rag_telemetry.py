"""
Test suite for Conversational Feedback and RAG Context Telemetry Logging.
Verifies database operations, telemetry recording, retrieval logging,
and server endpoints within an isolated temporary sandbox.
"""

import os
import sqlite3
import tempfile
import pytest
from fastapi.testclient import TestClient

import evelyn_config as cfg
import Evelyn.tools.chroma_rag as chroma_rag
from Evelyn.tools.chroma_rag import log_rag_retrieval, get_recent_rag_telemetry, link_rag_telemetry_to_message
from Evelyn.tools.db_migrator import (
    BASELINE_CHAT_SQL,
    BASELINE_MEMORY_SQL,
    CREATE_MESSAGE_FEEDBACK_TABLE_SQL,
    CREATE_RAG_RETRIEVAL_LOG_TABLE_SQL,
)
from evelyn_server import app, save_or_update_feedback, get_feedback_for_messages, save_message_get_id


@pytest.fixture(autouse=True)
def isolated_dbs():
    """Create isolated temporary chat and memory SQLite databases for each test."""
    temp_dir = tempfile.TemporaryDirectory()
    temp_chat_db = os.path.join(temp_dir.name, "test_chat.db")
    temp_memory_db = os.path.join(temp_dir.name, "test_memory.db")

    # Initialize schema in temporary chat DB
    with sqlite3.connect(temp_chat_db) as conn:
        conn.executescript(BASELINE_CHAT_SQL)
        conn.executescript(CREATE_MESSAGE_FEEDBACK_TABLE_SQL)

    # Initialize schema in temporary memory DB
    with sqlite3.connect(temp_memory_db) as conn:
        conn.executescript(BASELINE_MEMORY_SQL)
        conn.executescript(CREATE_RAG_RETRIEVAL_LOG_TABLE_SQL)

    # Patch config and chroma_rag module paths
    orig_chat_db = getattr(cfg, "CHAT_DB_PATH", None)
    orig_memory_db = getattr(cfg, "MEMORY_DB_PATH", None)
    orig_chroma_mem_db = getattr(chroma_rag, "_MEMORY_DB_PATH", None)

    cfg.CHAT_DB_PATH = temp_chat_db
    cfg.MEMORY_DB_PATH = temp_memory_db
    chroma_rag._MEMORY_DB_PATH = temp_memory_db

    try:
        yield
    finally:
        if orig_chat_db:
            cfg.CHAT_DB_PATH = orig_chat_db
        if orig_memory_db:
            cfg.MEMORY_DB_PATH = orig_memory_db
        if orig_chroma_mem_db:
            chroma_rag._MEMORY_DB_PATH = orig_chroma_mem_db
        temp_dir.cleanup()


@pytest.fixture
def client():
    return TestClient(app)


def test_feedback_crud_operations():
    """Verify create, update, retrieve, and delete on message feedback."""
    # 1. Create a dummy assistant message in isolated DB
    msg_id = save_message_get_id("assistant", "Test assistant message for feedback test", thinking="Thinking trace")
    assert isinstance(msg_id, int)
    assert msg_id > 0

    # 2. Add Upvote Feedback
    res = save_or_update_feedback(msg_id, rating=1, feedback="Accurate response")
    assert res["message_id"] == msg_id
    assert res["rating"] == 1
    assert res["feedback"] == "Accurate response"

    # 3. Retrieve Feedback
    fb_map = get_feedback_for_messages([msg_id])
    assert msg_id in fb_map
    assert fb_map[msg_id]["rating"] == 1
    assert fb_map[msg_id]["feedback"] == "Accurate response"

    # 4. Update Feedback to Downvote
    res2 = save_or_update_feedback(msg_id, rating=-1, feedback="Needs more detail")
    assert res2["rating"] == -1
    assert res2["feedback"] == "Needs more detail"

    fb_map2 = get_feedback_for_messages([msg_id])
    assert fb_map2[msg_id]["rating"] == -1
    assert fb_map2[msg_id]["feedback"] == "Needs more detail"

    # 5. Clear Feedback (rating=0)
    res3 = save_or_update_feedback(msg_id, rating=0)
    assert res3["rating"] == 0

    fb_map3 = get_feedback_for_messages([msg_id])
    assert msg_id not in fb_map3


def test_rag_telemetry_logging():
    """Verify logging of vector search events into rag_retrieval_log."""
    query = "test query for telemetry logging"
    search_query = "reformulated test query"
    pinned_chunks = [
        {
            "source": "/vault/path/to/pinned_note.md",
            "content": "Pinned note full content snippet",
            "metadata": {"chunk": 0, "total_chunks": 1, "rag_priority": "high", "tags": "pin, test"}
        }
    ]
    all_chunks = [
        {
            "source": "/vault/path/to/retrieved_note.md",
            "content": "Retrieved note content chunk",
            "distance": 0.25,
            "metadata": {"chunk": 1, "total_chunks": 3, "rag_priority": "normal", "tags": "ai, rag"}
        },
        {
            "source": "/vault/path/to/dropped_note.md",
            "content": "Dropped note distant content",
            "distance": 0.85,
            "metadata": {"chunk": 0, "total_chunks": 1, "rag_priority": "low", "tags": "misc"}
        }
    ]
    relevant = [all_chunks[0]]
    procedures = [
        {
            "id": 42,
            "trigger_pattern": "deploy service",
            "tags": "devops, deploy"
        }
    ]

    # Log retrieval event into isolated DB
    log_id = log_rag_retrieval(
        query=query,
        search_query=search_query,
        pinned_chunks=pinned_chunks,
        all_chunks=all_chunks,
        relevant=relevant,
        matching_procedures=procedures,
        message_id=None
    )
    assert isinstance(log_id, int)
    assert log_id > 0

    # Retrieve telemetry
    recent = get_recent_rag_telemetry(limit=10)
    assert len(recent) > 0
    logged_event = next((e for e in recent if e["id"] == log_id), None)
    assert logged_event is not None
    assert logged_event["query"] == query
    assert logged_event["search_query"] == search_query
    assert logged_event["total_retrieved"] == 2
    assert logged_event["total_kept"] == 1
    assert logged_event["total_pinned"] == 1
    assert len(logged_event["chunks"]) == 4  # 1 pinned + 2 queried + 1 procedure

    # Test link to message ID
    link_rag_telemetry_to_message(log_id, message_id=999)
    recent_after = get_recent_rag_telemetry(limit=10)
    updated_event = next(e for e in recent_after if e["id"] == log_id)
    assert updated_event["message_id"] == 999


def test_server_feedback_and_telemetry_endpoints(client):
    """Verify FastAPI endpoints for feedback and telemetry."""
    # 1. Create a dummy message in isolated DB
    msg_id = save_message_get_id("assistant", "Endpoint testing assistant message")

    # 2. Post feedback via API
    resp = client.post(
        "/chat/feedback",
        json={"message_id": msg_id, "rating": 1, "feedback": "Great insight"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["feedback"]["rating"] == 1

    # 3. Get feedback via API
    get_resp = client.get(f"/chat/feedback/{msg_id}")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["feedback"]["rating"] == 1

    # 4. Get RAG telemetry via API
    rag_resp = client.get("/telemetry/rag?limit=5")
    assert rag_resp.status_code == 200
    rag_data = rag_resp.json()
    assert rag_data["status"] == "ok"
    assert isinstance(rag_data["events"], list)

    # 5. Get Feedback telemetry summary via API
    fb_resp = client.get("/telemetry/feedback")
    assert fb_resp.status_code == 200
    fb_data = fb_resp.json()
    assert fb_data["status"] == "ok"
    assert fb_data["total_rated"] >= 1
    assert fb_data["upvotes"] >= 1

    # 6. Save message metrics with thinking effort and verify GET /telemetry/thinking
    from evelyn_server import save_message_metrics
    save_message_metrics(msg_id, {
        "think_effort": "high",
        "think_source": "tool_escalation",
        "prompt_eval_count": 1200,
        "eval_count": 450,
        "total_duration": 12000000000
    })

    think_resp = client.get("/telemetry/thinking")
    assert think_resp.status_code == 200
    think_data = think_resp.json()
    assert think_data["status"] == "ok"
    assert think_data["total_tracked"] >= 1
    assert think_data["effort_breakdown"].get("high") >= 1
    assert think_data["source_breakdown"].get("tool_escalation") >= 1
    assert len(think_data["recent_records"]) >= 1
    assert think_data["recent_records"][0]["think_effort"] == "high"


def test_vault_note_endpoints(client):
    """Verify reading, editing, and vector sync enqueueing for vault notes."""
    with tempfile.TemporaryDirectory() as temp_vault:
        orig_vault = cfg.VAULT_BASE_DIR
        cfg.VAULT_BASE_DIR = temp_vault

        try:
            # Create a test note in temp vault
            note_rel_path = "Concepts/TestConcept.md"
            full_note_path = os.path.join(temp_vault, note_rel_path)
            os.makedirs(os.path.dirname(full_note_path), exist_ok=True)
            with open(full_note_path, "w", encoding="utf-8") as f:
                f.write("# Test Concept\nInitial content.")

            # 1. Read note via GET /api/vault/note
            get_resp = client.get(f"/api/vault/note?path={note_rel_path}")
            assert get_resp.status_code == 200
            assert get_resp.json()["content"] == "# Test Concept\nInitial content."

            # 2. Update note via POST /api/vault/note
            post_resp = client.post(
                "/api/vault/note",
                json={"path": note_rel_path, "content": "# Test Concept\nUpdated note content."}
            )
            assert post_resp.status_code == 200
            assert post_resp.json()["status"] == "ok"

            # 3. Verify disk file updated
            with open(full_note_path, "r", encoding="utf-8") as f:
                assert f.read() == "# Test Concept\nUpdated note content."

            # 4. Traversal attack protection (403)
            bad_resp = client.get("/api/vault/note?path=../../etc/passwd")
            assert bad_resp.status_code == 403

            bad_post = client.post(
                "/api/vault/note",
                json={"path": "../../etc/evil.md", "content": "malicious"}
            )
            assert bad_post.status_code == 403

            # 5. Non-existent note returns 404
            not_found = client.get("/api/vault/note?path=NonExistent.md")
            assert not_found.status_code == 404

        finally:
            cfg.VAULT_BASE_DIR = orig_vault
