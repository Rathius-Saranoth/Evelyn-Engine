import os
import tempfile
import pytest
import evelyn_config as cfg
from Evelyn.tools import memory_db
from Evelyn.tools.fact_consolidator import fast_deduplicate_exact_matches, remediate_database_categories


@pytest.fixture
def mock_memory_db(monkeypatch):
    """Hermetic sandbox database setup using tempfile."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db_path = os.path.join(tmpdir, "test_memory.db")
        monkeypatch.setattr(cfg, "MEMORY_DB_PATH", test_db_path)

        # Initialize full canonical schema
        memory_db.init_db()

        yield test_db_path


def test_fast_deduplicate_exact_matches(mock_memory_db):
    """Verify fast deduplication merges exact and whitespace-duplicate entries."""
    con = memory_db.get_db()
    cur = con.cursor()
    # Insert 3 entries: 1 and 2 are duplicates of each other, 3 is distinct
    cur.execute(
        "INSERT INTO context_entries (category, subject, observation, tags, observed_count, status, created_at) VALUES (?, ?, ?, ?, ?, 'live', ?)",
        ("Cat05-U", "Ricky", "Drinks oat milk latte every morning.", "coffee, morning", 2, 1000.0),
    )
    cur.execute(
        "INSERT INTO context_entries (category, subject, observation, tags, observed_count, status, created_at) VALUES (?, ?, ?, ?, ?, 'live', ?)",
        ("Cat05-U", "Ricky", "drinks oat milk   latte every morning", "beverage", 1, 1100.0),
    )
    cur.execute(
        "INSERT INTO context_entries (category, subject, observation, tags, observed_count, status, created_at) VALUES (?, ?, ?, ?, ?, 'live', ?)",
        ("Cat05-U", "Ricky", "Drinks green tea in the afternoon.", "tea", 1, 1200.0),
    )
    con.commit()
    con.close()

    removed = fast_deduplicate_exact_matches()
    assert removed == 1

    con = memory_db.get_db()
    remaining = con.execute("SELECT id, observation, tags, observed_count FROM context_entries ORDER BY id ASC").fetchall()
    assert len(remaining) == 2

    # Primary entry #1 should have aggregated counts and merged tags
    primary = dict(remaining[0])
    assert primary["id"] == 1
    assert primary["observed_count"] == 3
    assert "coffee" in primary["tags"]
    assert "beverage" in primary["tags"]
    assert "morning" in primary["tags"]
    con.close()


def test_apply_fact_merge_preserves_master(mock_memory_db):
    """Verify apply_fact_merge updates the oldest entry in place and soft-deletes secondary entries."""
    con = memory_db.get_db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO context_entries (id, category, subject, observation, tags, observed_count, retrieval_count, first_observed, last_observed, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', 1000.0)",
        (10, "Cat05-U", "Ricky", "Enjoys dark roast coffee.", "coffee", 3, 5, 1000.0, 2000.0),
    )
    cur.execute(
        "INSERT INTO context_entries (id, category, subject, observation, tags, observed_count, retrieval_count, first_observed, last_observed, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', 1500.0)",
        (20, "Cat05-U", "Ricky", "Drinks Ethiopian dark roast daily.", "coffee, ethiopian", 2, 3, 1500.0, 2500.0),
    )
    con.commit()

    source_entries = [
        dict(cur.execute("SELECT * FROM context_entries WHERE id = 10").fetchone()),
        dict(cur.execute("SELECT * FROM context_entries WHERE id = 20").fetchone()),
    ]
    con.close()

    master_id = memory_db.apply_fact_merge(
        source_entries=source_entries,
        merged_text="Enjoys single-origin Ethiopian dark roast coffee every morning.",
        target_category="Cat05-U",
        merged_tags="coffee, ethiopian, morning",
    )

    assert master_id == 10

    con = memory_db.get_db()
    # Master entry checks
    master_row = dict(con.execute("SELECT * FROM context_entries WHERE id = 10").fetchone())
    assert master_row["observation"] == "Enjoys single-origin Ethiopian dark roast coffee every morning."
    assert master_row["status"] == "live"
    assert master_row["observed_count"] == 5  # 3 + 2
    assert master_row["retrieval_count"] == 8  # 5 + 3
    assert master_row["first_observed"] == 1000.0  # min
    assert master_row["last_observed"] == 2500.0  # max
    assert "coffee" in master_row["tags"]
    assert "ethiopian" in master_row["tags"]

    # Secondary entry checks
    secondary_row = dict(con.execute("SELECT * FROM context_entries WHERE id = 20").fetchone())
    assert secondary_row["status"] == "deleted"
    con.close()


def test_remediate_database_categories_ignores_procedure_proposals(mock_memory_db):
    """Verify remediate_database_categories does NOT alter procedure proposal suggested_category."""
    con = memory_db.get_db()
    cur = con.cursor()
    # Insert procedure proposal whose suggested_category is a master procedure numeric string
    cur.execute(
        "INSERT INTO proposals (id, type, source_ids, suggested_category, merged_observation, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (101, "procedure", "[1034]", "1034", "trigger: bedtime", "pending", 1000.0),
    )
    cur.execute(
        "INSERT INTO proposals (id, type, source_ids, suggested_category, merged_observation, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (102, "procedure_merge", "[1034, 1205]", "1034", "trigger: bedtime merge", "pending", 1000.0),
    )
    # Insert context proposal with old category code
    cur.execute(
        "INSERT INTO proposals (id, type, source_ids, suggested_category, merged_observation, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (103, "merge", "[1, 2]", "Cat05-R", "observation text", "pending", 1000.0),
    )
    con.commit()

    remediate_database_categories()

    # Procedure proposals must retain numeric master IDs
    p101 = dict(con.execute("SELECT suggested_category FROM proposals WHERE id = 101").fetchone())
    assert p101["suggested_category"] == "1034"

    p102 = dict(con.execute("SELECT suggested_category FROM proposals WHERE id = 102").fetchone())
    assert p102["suggested_category"] == "1034"

    # Context proposal should be remediated to Cat05-U
    p103 = dict(con.execute("SELECT suggested_category FROM proposals WHERE id = 103").fetchone())
    assert p103["suggested_category"] == "Cat05-U"
    con.close()


def test_fact_merge_queue_operations(mock_memory_db):
    """Verify enqueuing, listing, and dequeuing fact merge queue items."""
    qid = memory_db.enqueue_fact_merge([10, 20, 30])
    assert qid > 0

    pending = memory_db.get_fact_merge_queue(status="pending")
    assert len(pending) == 1
    assert pending[0]["id"] == qid
    assert pending[0]["entry_ids"] == [10, 20, 30]

    all_ids = memory_db.get_all_queued_fact_merge_ids()
    assert all_ids == {10, 20, 30}

    # Dequeue
    memory_db.dequeue_fact_merge(qid)
    pending_after = memory_db.get_fact_merge_queue(status="pending")
    assert len(pending_after) == 0


@pytest.mark.asyncio
async def test_queue_merge_server_endpoint(monkeypatch):
    """Verify POST /api/context/queue_merge endpoint properly queues IDs."""
    import evelyn_server
    from fastapi.testclient import TestClient

    queued_payload = []
    monkeypatch.setattr(
        memory_db,
        "enqueue_fact_merge",
        lambda ids: (queued_payload.extend(ids) or 42),
    )
    monkeypatch.setattr(
        memory_db,
        "get_all_queued_fact_merge_ids",
        lambda: set(),
    )

    client = TestClient(evelyn_server.app)
    res = client.post("/api/context/queue_merge", json={"entry_ids": [101, 102]})
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
    assert res.json()["queue_id"] == 42
    assert queued_payload == [101, 102]
