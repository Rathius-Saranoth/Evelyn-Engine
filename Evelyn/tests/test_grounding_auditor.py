# test_grounding_auditor.py
# date created: 2026-09-14
# date modified: 2026-09-14 20:23:52
# tags: #test, #grounding_auditor, #pronouns, #review_queue, #chat_context

"""
Targeted unit tests for grounding_auditor, surrounding chat context retrieval,
and associated server endpoints.
"""

import os
import sqlite3
import tempfile
import time

import pytest
from fastapi.testclient import TestClient

import evelyn_config as cfg
from Evelyn.tools import grounding_auditor, memory_db
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


def test_detect_floating_pronouns():
    """Verify detect_grounding_issues flags floating pronoun starts and grounds with subject."""
    eid1 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="He enjoys playing rhythm games with high BPM.",
        status="live",
    )
    eid2 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="His mechanical keyboard has tactile switches.",
        status="live",
    )

    issues = grounding_auditor.detect_grounding_issues(limit=10)
    assert len(issues) == 2

    i1 = next(i for i in issues if i["entry_id"] == eid1)
    assert i1["issue_type"] == "floating_pronoun"
    assert i1["grounded_observation"] == f"{cfg.USER_NAME} enjoys playing rhythm games with high BPM."

    i2 = next(i for i in issues if i["entry_id"] == eid2)
    assert i2["issue_type"] == "floating_pronoun"
    assert i2["grounded_observation"] == f"{cfg.USER_NAME}'s mechanical keyboard has tactile switches."


def test_detect_pronoun_subject_contradictions():
    """Verify detect_grounding_issues catches mismatches between subject tag and pronoun."""
    # Contradiction 1: Cat-U entry tagged as Assistant, but refers to 'He'
    eid1 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.ASSISTANT_NAME,
        observation="He values structured deep work sessions without interruption.",
        status="live",
    )
    # Contradiction 2: Cat-A entry tagged as User, but refers to 'She'
    eid2 = memory_db.insert_entry(
        category="Cat06-A",
        subject=cfg.USER_NAME,
        observation="She feels a strong sense of purpose when collaborating.",
        status="live",
    )

    issues = grounding_auditor.detect_grounding_issues(limit=10)
    assert len(issues) == 2

    i1 = next(i for i in issues if i["entry_id"] == eid1)
    assert i1["issue_type"] == "contradiction_pronoun_subject"
    assert i1["suggested_subject"] == cfg.USER_NAME
    assert i1["grounded_observation"] == f"{cfg.USER_NAME} values structured deep work sessions without interruption."

    i2 = next(i for i in issues if i["entry_id"] == eid2)
    assert i2["issue_type"] == "contradiction_pronoun_subject"
    assert i2["suggested_subject"] == cfg.ASSISTANT_NAME
    assert i2["grounded_observation"] == f"{cfg.ASSISTANT_NAME} feels a strong sense of purpose when collaborating."


def test_detect_bare_verbs():
    """Verify detect_grounding_issues catches subject-less bare verbs."""
    eid1 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="Enjoys drinking green tea in the morning.",
        status="live",
    )
    eid2 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="Prefers neovim keybindings in the IDE.",
        status="live",
    )

    issues = grounding_auditor.detect_grounding_issues(limit=10)
    assert len(issues) == 2

    i1 = next(i for i in issues if i["entry_id"] == eid1)
    assert i1["issue_type"] == "bare_verb"
    assert i1["grounded_observation"] == f"{cfg.USER_NAME} enjoys drinking green tea in the morning."

    i2 = next(i for i in issues if i["entry_id"] == eid2)
    assert i2["issue_type"] == "bare_verb"
    assert i2["grounded_observation"] == f"{cfg.USER_NAME} prefers neovim keybindings in the IDE."


def test_clean_entries_not_flagged():
    """Verify already well-grounded entries are ignored."""
    memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation=f"{cfg.USER_NAME} uses Arch Linux as their primary workstation.",
        status="live",
    )
    issues = grounding_auditor.detect_grounding_issues(limit=10)
    assert len(issues) == 0


def test_stage_grounding_proposals():
    """Verify stage_grounding_proposals writes non-destructive rephrase proposals."""
    eid = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="Enjoys listening to synthwave while writing code.",
        tags="music, coding",
        status="live",
    )

    issues = grounding_auditor.detect_grounding_issues(limit=10)
    assert len(issues) == 1

    pids = grounding_auditor.stage_grounding_proposals(issues)
    assert len(pids) == 1
    pid = pids[0]

    # Verify proposal content
    props = memory_db.get_pending_proposals()
    prop = next(p for p in props if p["id"] == pid)
    assert prop["type"] == "rephrase"
    assert prop["source_ids"] == [eid]
    assert prop["merged_observation"] == f"{cfg.USER_NAME} enjoys listening to synthwave while writing code."
    assert prop["suggested_category"] == "Cat05-U"

    # Subsequent scan should ignore this entry due to pending proposal
    issues_after = grounding_auditor.detect_grounding_issues(limit=10)
    assert len(issues_after) == 0


def test_find_surrounding_chat_context():
    """Verify on-demand FTS5 search finds the source message and surrounding conversational turns."""
    now = time.time()
    # Populate isolated chat database
    with sqlite3.connect(cfg.CHAT_DB_PATH) as conn:
        for i in range(1, 8):
            if i == 4:
                content = "I really love designing custom mechanical keyboard PCBs using KiCad and soldering components."
                role = "user"
            else:
                content = f"Conversational filler turn {i} about unrelated daily topics."
                role = "assistant" if i % 2 == 0 else "user"
            conn.execute(
                "INSERT INTO messages (id, role, content, ts) VALUES (?, ?, ?, ?)",
                (i, role, content, now + i),
            )

    # Insert context entry that was derived from message 4
    eid = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="Enjoys designing custom mechanical keyboard PCBs using KiCad and soldering components.",
        status="live",
    )

    result = grounding_auditor.find_surrounding_chat_context(eid, window=2)
    assert result is not None
    assert result["matched_message_id"] == 4

    messages = result["messages"]
    # Window of 2 around 4 should include ids 2, 3, 4, 5, 6
    message_ids = [m["id"] for m in messages]
    assert 4 in message_ids
    assert len(messages) >= 3

    # Check match flag
    matched_msg = next(m for m in messages if m["id"] == 4)
    assert matched_msg["is_match"] is True
    assert "KiCad" in matched_msg["content"]


def test_server_grounding_endpoints():
    """Verify FastAPI review endpoints for surrounding chat context and grounding audit."""
    now = time.time()
    with sqlite3.connect(cfg.CHAT_DB_PATH) as conn:
        conn.execute(
            "INSERT INTO messages (id, role, content, ts) VALUES (?, ?, ?, ?)",
            (1, "user", "I prefer obsidian over notion because it keeps all my data local in markdown.", now),
        )

    eid = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="Prefers obsidian over notion because it keeps all data local in markdown.",
        status="live",
    )

    client = TestClient(app)

    # 1. Test GET /api/review/context_entry/{entry_id}/surrounding_chat
    res = client.get(f"/api/review/context_entry/{eid}/surrounding_chat?window=2")
    assert res.status_code == 200
    data = res.json()
    assert data["matched_message_id"] == 1
    assert len(data["messages"]) == 1
    assert data["messages"][0]["is_match"] is True

    # 2. Test POST /api/audit/grounding (dry_run=True)
    res_dry = client.post("/api/audit/grounding", json={"limit": 5, "dry_run": True})
    assert res_dry.status_code == 200
    dry_data = res_dry.json()
    assert dry_data["dry_run"] is True
    assert dry_data["staged_proposals_count"] == 0
    assert len(dry_data["issues"]) == 1
    assert dry_data["issues"][0]["entry_id"] == eid

    # 3. Test POST /api/audit/grounding (dry_run=False, stage proposals)
    res_stage = client.post("/api/audit/grounding", json={"limit": 5, "dry_run": False})
    assert res_stage.status_code == 200
    stage_data = res_stage.json()
    assert stage_data["dry_run"] is False
    assert stage_data["staged_proposals_count"] == 1
    assert len(stage_data["staged_proposal_ids"]) == 1
    pid = stage_data["staged_proposal_ids"][0]

    # 4. Test approving the staged rephrase proposal via /api/review/proposals/{id}/approve
    res_action = client.post(
        f"/api/review/proposals/{pid}/approve",
        json={"modified_text": f"{cfg.USER_NAME} strongly prefers obsidian over notion."},
    )
    assert res_action.status_code == 200
    assert res_action.json()["status"] == "ok"

    # Verify context_entries was updated
    updated_entry = memory_db.get_entry(eid)
    assert updated_entry is not None
    assert updated_entry["observation"] == f"{cfg.USER_NAME} strongly prefers obsidian over notion."
