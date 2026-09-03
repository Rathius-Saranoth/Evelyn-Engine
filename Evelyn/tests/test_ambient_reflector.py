# test_ambient_reflector.py
# date created: 2026-08-30 16:40:00
# date modified: 2026-09-02 21:29:54
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

from datetime import UTC, datetime
import os
import sqlite3
import tempfile
import time

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
    memory_db.record_ambient_impression(
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
    """Test gate evaluations: circadian window, inactivity threshold, thought cooldown spacing, and daily cap."""
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

    # 3. Sufficient silence -> Eligible
    eligible, reason = ambient_reflector.should_generate_idle_thought(now_dt=now_dt, idle_seconds=7500)
    assert eligible is True
    assert "Eligible for daytime thought reflection" in reason

    # 4. Thought cooldown spacing (< 7200s since last thought)
    today_str = now_dt.strftime("%Y-%m-%d")
    memory_db.record_ambient_impression(
        type="thought",
        content="First thought",
        target_date=today_str,
        ts=now_dt.timestamp() - 1800,  # 30m ago (< 2h cooldown)
    )

    eligible, reason = ambient_reflector.should_generate_idle_thought(now_dt=now_dt, idle_seconds=7500)
    assert eligible is False
    assert "Thought cooldown active" in reason

    # 5. Max daily thoughts reached (3 used)
    for i in range(2):
        memory_db.record_ambient_impression(type="thought", content=f"Thought {i}", target_date=today_str, ts=now_dt.timestamp() - 8000)

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

    # 4. POST /ambient/dismiss_all
    id_t1 = memory_db.record_ambient_impression(type="thought", content="Thought A", target_date=today_str)
    id_t2 = memory_db.record_ambient_impression(type="thought", content="Thought B", target_date=today_str)
    assert id_t1 > 0 and id_t2 > 0
    res_feed = client.get("/ambient/feed")
    assert len(res_feed.json()["items"]) == 2

    res_dismiss_all = client.post("/ambient/dismiss_all", json={"type": "thought"})
    assert res_dismiss_all.status_code == 200
    assert res_dismiss_all.json()["dismissed_count"] == 2

    res_empty = client.get("/ambient/feed")
    assert len(res_empty.json()["items"]) == 0


def test_ambient_providers_execution(temp_ambient_dbs):
    """Test ambient provider implementations and fallback behaviors."""
    from Evelyn.tools import ambient_providers

    now_dt = datetime(2026, 9, 2, 14, 30, 0, tzinfo=UTC).astimezone()

    # 1. RecentChatProvider
    chat_prov = ambient_providers.get_provider("recent_chat")
    xml, ref, mood = chat_prov.fetch_seed_context({"id": "chat_recent"}, now_dt)
    assert "<conversation_context_sample>" in xml
    assert "chat_turns:" in ref
    assert mood == "Reflective"

    # 2. VaultDocumentProvider (fallback with empty vault)
    vault_prov = ambient_providers.get_provider("vault_document")
    xml, ref, mood = vault_prov.fetch_seed_context({"id": "vault_notes"}, now_dt)
    assert "<vault_reminiscence" in xml
    assert "vault:" in ref

    # 3. LoreSnippetProvider (graceful fallback with non-existent file)
    lore_prov = ambient_providers.get_provider("lore_file")
    xml, ref, mood = lore_prov.fetch_seed_context({"id": "lore", "file_path": "NonExistent/Aura.md"}, now_dt)
    assert "<companion_lore" in xml
    assert "lore:" in ref
    assert mood == "Serene"

    # 4. TopicCuriosityProvider
    topic_prov = ambient_providers.get_provider("topic_curiosity")
    xml, ref, mood = topic_prov.fetch_seed_context({"id": "research", "topic_pool": ["quantum mechanics"]}, now_dt)
    assert "<intellectual_curiosity" in xml
    assert "quantum mechanics" in xml
    assert mood == "Curious"

    # 5. SensoryWanderProvider
    sensory_prov = ambient_providers.get_provider("sensory_wander")
    xml, ref, mood = sensory_prov.fetch_seed_context({"id": "sensory"}, now_dt)
    assert "<sensory_wander" in xml
    assert "sensory:" in ref
    assert mood == "Serene"


def test_diurnal_bucket_and_activity_selection():
    """Test circadian phase calculation and weighted activity selection with recency decay."""
    assert ambient_reflector.get_diurnal_bucket(6) == "morning"
    assert ambient_reflector.get_diurnal_bucket(13) == "afternoon"
    assert ambient_reflector.get_diurnal_bucket(19) == "evening"
    assert ambient_reflector.get_diurnal_bucket(23) == "night"

    activities = [
        {"id": "act_a", "type": "recent_chat", "enabled": True, "weights": {"morning": 1.0, "afternoon": 0.0}},
        {"id": "act_b", "type": "sensory_wander", "enabled": True, "weights": {"morning": 0.0, "afternoon": 1.0}},
        {"id": "act_disabled", "type": "topic_curiosity", "enabled": False, "weights": {"morning": 10.0}},
    ]

    # In morning, act_a should be chosen
    selected_morning = ambient_reflector.select_ambient_activity(activities, "morning")
    assert selected_morning["id"] == "act_a"

    # In afternoon, act_b should be chosen
    selected_afternoon = ambient_reflector.select_ambient_activity(activities, "afternoon")
    assert selected_afternoon["id"] == "act_b"

    # Test recency cooldown dampening
    activities_balanced = [
        {"id": "act_a", "type": "recent_chat", "enabled": True, "weights": {"morning": 0.5}},
        {"id": "act_b", "type": "sensory_wander", "enabled": True, "weights": {"morning": 0.5}},
    ]
    # If act_a was run last, its weight is dampened by 0.2 (0.1 vs 0.5)
    # Over 100 trials, act_b should win the vast majority
    b_wins = sum(
        1 for _ in range(100)
        if ambient_reflector.select_ambient_activity(activities_balanced, "morning", last_activity_id="act_a", cooldown_decay=0.01)["id"] == "act_b"
    )
    assert b_wins > 80


@pytest.mark.asyncio
async def test_run_ambient_reflection_with_prior_continuity(temp_ambient_dbs, monkeypatch):
    """Test full run_ambient_reflection cycle with prior reflections injected into prompt."""
    today_str = datetime.now(UTC).astimezone().strftime("%Y-%m-%d")

    # Record an earlier thought today
    memory_db.record_ambient_impression(
        type="thought",
        content="First morning contemplation on cedar trees.",
        metadata={"mood": "Serene", "activity_id": "sensory_wander"},
        target_date=today_str,
    )

    captured_prompts = []

    def mock_query_ollama(prompt, system="", options=None, timeout=120, strip_thinking=True):
        captured_prompts.append({"prompt": prompt, "system": system})
        return "A curious wandering realization about database indexing and memory structures."

    monkeypatch.setattr("Evelyn.tools.ollama_client.query_ollama", mock_query_ollama)

    res = await ambient_reflector.run_ambient_reflection(dry_run=True, force=True)
    assert res["status"] == "success"
    assert res["dry_run"] is True
    assert "curious" in res["thought"].lower()
    assert res["mood"] == "Curious"
    assert res["activity_id"] in [a["id"] for a in cfg.AMBIENT_ACTIVITIES]

    # Verify that <daily_journal_so_far> was included in the user prompt
    assert len(captured_prompts) == 1
    prompt_text = captured_prompts[0]["prompt"]
    assert "<daily_journal_so_far>" in prompt_text
    assert "First morning contemplation on cedar trees." in prompt_text
    assert "<ambient_seed_context" in prompt_text
