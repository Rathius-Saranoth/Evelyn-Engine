# test_ambient_reflector.py
# date created: 2026-08-30 16:40:00
# date modified: 2026-08-30 16:35:57
# tags: #tests, #ambient, #thought-bubbles, #feed, #journal-synthesis

"""
test_ambient_reflector.py — Test Suite for Ambient Impressions & Diurnal Reflector.

Covers:
  - Database schema, CRUD, and active feed ordering (ts DESC)
  - Diurnal gate condition evaluation (circadian window, silence duration, daily cap, new turns)
  - Multi-modal helpers (record_media_share, record_system_alert)
  - Cross-layer journal synthesis & failure-isolated consumption
  - API endpoint contracts (/ambient/feed, /ambient/dismiss, /thought_bubble)
"""

from __future__ import annotations

from datetime import UTC, datetime, time as dtime
import os
import sqlite3
import tempfile
import time
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

import evelyn_config as cfg
from Evelyn.tools import ambient_reflector, memory_db


@pytest.fixture
def temp_ambient_dbs(monkeypatch):
    """Create isolated temporary memory and chat databases for ambient testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        mem_db_path = os.path.join(tmpdir, "test_memory.db")
        chat_db_path = os.path.join(tmpdir, "test_chat.db")

        # Initialize memory DB schema
        con_mem = sqlite3.connect(mem_db_path)
        con_mem.execute("""
            CREATE TABLE IF NOT EXISTS daily_ambient_impressions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL NOT NULL,
                date        TEXT NOT NULL,
                type        TEXT NOT NULL,
                content     TEXT NOT NULL,
                source_ref  TEXT,
                media_id    TEXT,
                metadata    TEXT,
                consumed    INTEGER DEFAULT 0,
                dismissed   INTEGER DEFAULT 0
            );
        """)
        con_mem.execute("CREATE INDEX IF NOT EXISTS idx_ambient_date ON daily_ambient_impressions(date, consumed);")
        con_mem.execute("CREATE INDEX IF NOT EXISTS idx_ambient_type ON daily_ambient_impressions(type, dismissed);")
        con_mem.execute("CREATE INDEX IF NOT EXISTS idx_ambient_feed ON daily_ambient_impressions(dismissed, ts DESC);")
        con_mem.execute("CREATE INDEX IF NOT EXISTS idx_ambient_type_feed ON daily_ambient_impressions(type, dismissed, ts DESC);")
        con_mem.commit()
        con_mem.close()

        # Initialize chat DB schema
        con_chat = sqlite3.connect(chat_db_path)
        con_chat.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tools_used TEXT,
                ts REAL NOT NULL
            );
        """)
        con_chat.commit()
        con_chat.close()

        monkeypatch.setattr(cfg, "MEMORY_DB_PATH", mem_db_path)
        monkeypatch.setattr(cfg, "CHAT_DB_PATH", chat_db_path)
        monkeypatch.setattr(memory_db.cfg, "MEMORY_DB_PATH", mem_db_path)

        yield {"mem_path": mem_db_path, "chat_path": chat_db_path}


def test_ambient_impressions_crud_and_feed_ordering(temp_ambient_dbs):
    """Test inserting, querying active feed, dismissing, and type filtering."""
    now_ts = time.time()
    today_str = "2026-08-30"

    # 1. Insert multiple impressions
    id1 = memory_db.record_ambient_impression(
        type="thought",
        content="Reflecting on the scalemail crafting techniques.",
        source_ref="chat:100",
        metadata={"mood": "Reflective"},
        target_date=today_str,
        ts=now_ts - 300,
    )
    id2 = memory_db.record_ambient_impression(
        type="media_share",
        content="Linen tunic concept rendering.",
        source_ref="media:guid-123",
        media_id="guid-123",
        metadata={"category": "wardrobe"},
        target_date=today_str,
        ts=now_ts - 100,
    )
    id3 = memory_db.record_ambient_impression(
        type="system_alert",
        content="Research task completed for acoustic damping.",
        source_ref="task:acoustic",
        metadata={"priority": "info"},
        target_date=today_str,
        ts=now_ts,
    )

    assert id1 > 0
    assert id2 > 0
    assert id3 > 0

    # 2. Query active feed — newest first (ts DESC)
    feed = memory_db.get_active_ambient_feed(limit=10)
    assert len(feed) == 3
    assert feed[0]["id"] == id3
    assert feed[1]["id"] == id2
    assert feed[2]["id"] == id1

    # 3. Query filtered by type
    thoughts = memory_db.get_active_ambient_feed(limit=10, type_filter="thought")
    assert len(thoughts) == 1
    assert thoughts[0]["id"] == id1
    assert thoughts[0]["metadata"]["mood"] == "Reflective"

    latest_thought = memory_db.get_latest_ambient_impression(type_filter="thought")
    assert latest_thought is not None
    assert latest_thought["id"] == id1

    # 4. Dismiss an item
    dismissed = memory_db.mark_ambient_impression_dismissed(id1)
    assert dismissed is True

    # Active feed should now only show 2 items
    active_after = memory_db.get_active_ambient_feed(limit=10)
    assert len(active_after) == 2
    assert all(i["id"] != id1 for i in active_after)


def test_cross_layer_journal_synthesis_and_consumption(temp_ambient_dbs):
    """Verify that unconsumed impressions are queried by date and marked consumed on disk confirmation."""
    today_str = "2026-08-30"

    id1 = memory_db.record_ambient_impression(
        type="thought",
        content="Wandering thought 1",
        target_date=today_str,
    )
    id2 = memory_db.record_ambient_impression(
        type="thought",
        content="Wandering thought 2",
        target_date=today_str,
    )
    id_other_day = memory_db.record_ambient_impression(
        type="thought",
        content="Yesterday thought",
        target_date="2026-08-29",
    )

    # Dismissing in UI does NOT prevent journal consumption (orthogonality invariant)
    memory_db.mark_ambient_impression_dismissed(id1)

    unconsumed = memory_db.get_unconsumed_ambient_impressions(today_str)
    assert len(unconsumed) == 2
    assert {u["id"] for u in unconsumed} == {id1, id2}

    # Mark consumed
    memory_db.mark_ambient_impressions_consumed([id1, id2])

    unconsumed_after = memory_db.get_unconsumed_ambient_impressions(today_str)
    assert len(unconsumed_after) == 0

    # Other day should remain unconsumed
    assert len(memory_db.get_unconsumed_ambient_impressions("2026-08-29")) == 1


def test_ambient_reflector_gate_conditions(temp_ambient_dbs):
    """Test gate evaluations: circadian window, inactivity threshold, daily cap, and new turns."""
    # Local time at 14:00 (inside diurnal window 09:00–21:00)
    now_dt = datetime(2026, 8, 30, 14, 0, 0, tzinfo=UTC).astimezone()

    # 1. Inactivity below threshold (< 7200s)
    eligible, reason = ambient_reflector.should_generate_idle_thought(now_dt=now_dt, idle_seconds=1800)
    assert eligible is False
    assert "below required threshold" in reason

    # 2. Outside diurnal window (03:00 AM)
    night_dt = datetime(2026, 8, 30, 3, 0, 0, tzinfo=UTC).astimezone()
    eligible, reason = ambient_reflector.should_generate_idle_thought(now_dt=night_dt, idle_seconds=9000)
    assert eligible is False
    assert "Outside diurnal window" in reason

    # 3. Sufficient silence but no messages in chat DB
    eligible, reason = ambient_reflector.should_generate_idle_thought(now_dt=now_dt, idle_seconds=7500)
    assert eligible is False
    assert "Insufficient new conversation turns" in reason

    # 4. Add conversation messages to chat DB
    con_chat = sqlite3.connect(temp_ambient_dbs["chat_path"])
    msg_ts = now_dt.timestamp() - 7500
    con_chat.execute("INSERT INTO messages (role, content, ts) VALUES ('user', 'Let us discuss scalemail.', ?)", (msg_ts,))
    con_chat.execute("INSERT INTO messages (role, content, ts) VALUES ('assistant', 'I love the tactile process of scalemail.', ?)", (msg_ts + 10,))
    con_chat.commit()
    con_chat.close()

    eligible, reason = ambient_reflector.should_generate_idle_thought(now_dt=now_dt, idle_seconds=7500)
    assert eligible is True
    assert "Eligible for daytime thought reflection" in reason

    # 5. Max daily thoughts reached (3 used)
    today_str = now_dt.strftime("%Y-%m-%d")
    for i in range(3):
        memory_db.record_ambient_impression(type="thought", content=f"Thought {i}", target_date=today_str)

    eligible, reason = ambient_reflector.should_generate_idle_thought(now_dt=now_dt, idle_seconds=7500)
    assert eligible is False
    assert "Daily thought limit reached" in reason


def test_multi_modal_helpers(temp_ambient_dbs):
    """Test record_media_share and record_system_alert helper functions."""
    id_media = ambient_reflector.record_media_share(
        content="Check out this linen tunic draft!",
        media_id="wardrobe-uuid-99",
        metadata={"category": "wardrobe"},
    )
    assert id_media > 0

    id_alert = ambient_reflector.record_system_alert(
        content="Anomaly detected in sleep recovery score.",
        source_ref="health:oura",
        metadata={"metric": "recovery"},
    )
    assert id_alert > 0

    items = memory_db.get_active_ambient_feed(limit=5)
    assert len(items) == 2
    types = {i["type"] for i in items}
    assert types == {"media_share", "system_alert"}


@pytest.mark.asyncio
async def test_ambient_api_endpoints(temp_ambient_dbs):
    """Test FastAPI /ambient/feed, /ambient/dismiss, and /thought_bubble endpoints."""
    from evelyn_server import app

    today_str = datetime.now(UTC).astimezone().strftime("%Y-%m-%d")
    id_thought = memory_db.record_ambient_impression(
        type="thought",
        content="A wandering thought on design elegance.",
        metadata={"mood": "Inspired"},
        target_date=today_str,
    )

    client = TestClient(app)

    # 1. GET /ambient/feed
    res = client.get("/ambient/feed")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert len(data["items"]) >= 1
    assert data["items"][0]["content"] == "A wandering thought on design elegance."

    # 2. GET /thought_bubble
    res_bubble = client.get("/thought_bubble")
    assert res_bubble.status_code == 200
    bubble_data = res_bubble.json()
    assert bubble_data["status"] == "ok"
    assert bubble_data["latest_thought"]["id"] == id_thought

    # 3. POST /ambient/dismiss
    res_dismiss = client.post("/ambient/dismiss", json={"id": id_thought})
    assert res_dismiss.status_code == 200
    assert res_dismiss.json()["updated"] is True

    # After dismissal, feed is empty
    res_after = client.get("/ambient/feed")
    assert len(res_after.json()["items"]) == 0
