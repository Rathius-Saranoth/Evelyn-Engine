# test_fact_deduplicator.py
# date created: 2026-09-14
# date modified: 2026-09-14 20:23:52
# tags: #test, #fact_deduplicator, #deduplication, #hysteresis, #telemetry

"""Targeted unit tests for fact_deduplicator and fact_categorizer child modules."""

import os
import tempfile
import time
from unittest.mock import patch

import pytest

import evelyn_config as cfg
from Evelyn.tools import fact_categorizer, fact_deduplicator, memory_db


@pytest.fixture
def mock_memory_db(monkeypatch):
    """Hermetic sandbox database setup using tempfile."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_db_path = os.path.join(tmpdir, "test_memory.db")
        monkeypatch.setattr(cfg, "MEMORY_DB_PATH", test_db_path)

        # Initialize full canonical schema
        memory_db.init_db()

        yield test_db_path


def test_deduplication_telemetry_metrics(mock_memory_db):
    """Verify get_fact_deduplication_metrics correctly calculates counts."""
    # 1. Insert live entries
    e1 = memory_db.insert_entry(category="Cat05-U", subject=cfg.USER_NAME, observation="E1", status="live")
    e2 = memory_db.insert_entry(category="Cat05-U", subject=cfg.USER_NAME, observation="E2", status="live")
    e3 = memory_db.insert_entry(category="Cat05-U", subject=cfg.USER_NAME, observation="E3", status="live")
    assert e3 > 0

    # 2. Merge e2 into e1
    rec1 = memory_db.get_entry(e1)
    rec2 = memory_db.get_entry(e2)
    assert rec1 is not None and rec2 is not None

    master_id = memory_db.apply_fact_merge(
        source_entries=[rec1, rec2],
        merged_text="E1 and E2 combined",
        target_category="Cat05-U",
    )
    assert master_id == e1

    # 3. Check metrics
    metrics = memory_db.get_fact_deduplication_metrics()
    assert metrics["live_facts"] == 2  # e1 (master) and e3
    assert metrics["merged_facts"] == 1  # e2 soft-deleted with merged_into_id
    assert metrics["pending_merges"] == 0


def test_recategorization_anti_hysteresis_suppresses_flip_flops(mock_memory_db):
    """Verify is_recategorization_suppressed prevents ping-pong loops."""
    eid = memory_db.insert_entry(category="Cat05-U", subject=cfg.USER_NAME, observation="Sleep noise boundary", status="live")

    # Initially, moving to Cat16-U is not suppressed
    assert not fact_categorizer.is_recategorization_suppressed(eid, "Cat16-U")

    # Simulate recategorization: proposal logged and entry recategorized_at updated
    now = time.time()
    memory_db.insert_proposal(
        type="recategorize",
        source_ids=[eid],
        suggested_category="Cat16-U",
        status="auto_applied",
    )
    memory_db.update_entry(eid, category="Cat16-U", recategorized_at=now)

    # Now, trying to recategorize back to Cat05-U or Cat16-U within cooldown must be suppressed!
    assert fact_categorizer.is_recategorization_suppressed(eid, "Cat05-U", cooldown_days=30)
    assert fact_categorizer.is_recategorization_suppressed(eid, "Cat16-U", cooldown_days=30)


def test_find_deduplication_candidates_nearest_neighbors(mock_memory_db):
    """Verify vector-driven candidate discovery finds nearest neighbors and stamps last_audited_at."""
    e1 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation=f"{cfg.USER_NAME} enjoys starting morning with coffee and music.",
        status="live",
    )
    e2 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation=f"{cfg.USER_NAME} starts morning with coffee and music.",
        status="live",
    )

    mock_chroma_chunks = [
        {"source": f"sqlite::context_entry::{e1}", "distance": 0.0},
        {"source": f"sqlite::context_entry::{e2}", "distance": 0.18},
    ]

    with patch("Evelyn.tools.chroma_rag.query_collection", return_value=mock_chroma_chunks):
        clusters = fact_deduplicator.find_deduplication_candidates(batch_limit=5, distance_threshold=0.40)
        assert len(clusters) == 1
        c = clusters[0]
        assert len(c["records"]) == 2
        record_ids = {r["id"] for r in c["records"]}
        assert e1 in record_ids
        assert e2 in record_ids

    # Verify last_audited_at was stamped on both records
    updated_e1 = memory_db.get_entry(e1)
    updated_e2 = memory_db.get_entry(e2)
    assert updated_e1 is not None and updated_e1["last_audited_at"] is not None
    assert updated_e2 is not None and updated_e2["last_audited_at"] is not None
