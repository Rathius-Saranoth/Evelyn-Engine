# test_fact_deduplicator.py
# date created: 2026-09-14
# date modified: 2026-10-16 00:13:20
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
    """Verify is_recategorization_suppressed is deprecated and always suppresses recategorization."""
    eid = memory_db.insert_entry(subject=cfg.USER_NAME, observation="Sleep noise boundary", status="live")
    assert fact_categorizer.is_recategorization_suppressed(eid, "Cat16-U")


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


class TestSplitProvenanceGuard:
    """A split is an explicit judgement that two facts are distinct; dedup must honour it."""

    def _f(self, a, b):
        from Evelyn.tools.fact_deduplicator import is_split_relative
        return is_split_relative(a, b)

    def test_siblings_of_one_split_are_protected(self):
        assert self._f({"id": 2, "split_from_id": 1}, {"id": 3, "split_from_id": 1}) is True

    def test_child_and_its_parent_are_protected_either_way(self):
        assert self._f({"id": 2, "split_from_id": 1}, {"id": 1, "split_from_id": None}) is True
        assert self._f({"id": 1, "split_from_id": None}, {"id": 2, "split_from_id": 1}) is True

    def test_unrelated_entries_remain_mergeable(self):
        assert self._f({"id": 5, "split_from_id": None}, {"id": 6, "split_from_id": None}) is False

    def test_children_of_different_splits_remain_mergeable(self):
        assert self._f({"id": 2, "split_from_id": 1}, {"id": 4, "split_from_id": 3}) is False

    def test_null_parents_do_not_match_each_other(self):
        """Two NULL split_from_id values must not be treated as a shared parent."""
        assert self._f({"id": 7, "split_from_id": None}, {"id": 8, "split_from_id": None}) is False

    def test_grandchild_and_grandparent_are_protected(self):
        """Entry split from a child of root is protected against root."""
        # 1 (root) -> 2 (child) -> 3 (grandchild)
        mock_db = {
            1: {"id": 1, "split_from_id": None},
            2: {"id": 2, "split_from_id": 1},
            3: {"id": 3, "split_from_id": 2},
        }
        from Evelyn.tools.fact_deduplicator import is_split_relative
        assert is_split_relative(mock_db[3], mock_db[1], db_getter=lambda eid: mock_db.get(eid)) is True
        assert is_split_relative(mock_db[1], mock_db[3], db_getter=lambda eid: mock_db.get(eid)) is True

    def test_aunt_and_niece_are_protected(self):
        """Child of one branch is protected against descendants of sibling branch."""
        # 1 (root) -> 2 (child A) and 3 (child B); 2 -> 4 (grandchild)
        mock_db = {
            1: {"id": 1, "split_from_id": None},
            2: {"id": 2, "split_from_id": 1},
            3: {"id": 3, "split_from_id": 1},
            4: {"id": 4, "split_from_id": 2},
        }
        from Evelyn.tools.fact_deduplicator import is_split_relative
        # 4 (niece) vs 3 (aunt): share root 1 in ancestor chain
        assert is_split_relative(mock_db[4], mock_db[3], db_getter=lambda eid: mock_db.get(eid)) is True
        assert is_split_relative(mock_db[3], mock_db[4], db_getter=lambda eid: mock_db.get(eid)) is True


@pytest.mark.asyncio
async def test_consolidation_auto_apply_gated_by_config(mock_memory_db, monkeypatch):
    """Verify semantic consolidation proposals remain pending unless CONSOLIDATION_AUTO_APPLY_MERGES is True."""
    monkeypatch.setattr(cfg, "CONSOLIDATION_AUTO_APPLY_MERGES", False)

    e1 = memory_db.insert_entry(category="Cat05-U", subject=cfg.USER_NAME, observation="User likes tea.", status="live")
    e2 = memory_db.insert_entry(category="Cat05-U", subject=cfg.USER_NAME, observation="User enjoys tea daily.", status="live")

    cluster = {
        "category": "Cat05-U",
        "topic": "Duplicate facts on #1",
        "records": [memory_db.get_entry(e1), memory_db.get_entry(e2)],
    }

    mock_yaml = """```yaml
verdict: merge
merged_summary: "User enjoys drinking tea on a daily basis."
merged_tags: "tea, routine"
confidence: high
reasoning: "Same preference."
```"""

    async def mock_call(*args, **kwargs):
        return mock_yaml

    pid = await fact_deduplicator.generate_consolidation_proposal(cluster, mock_call)
    assert pid is not None
    con = memory_db.get_db()
    row = con.execute("SELECT status FROM proposals WHERE id = ?", (int(pid),)).fetchone()
    con.close()
    assert row is not None
    # Must be pending for user review, NOT auto_applied!
    assert row[0] == "pending"


def test_find_similar_entries_stemming_and_connective_verbs(mock_memory_db):
    """Verify find_similar_entries matches phrased variations of established routines."""
    e1 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation=f"{cfg.USER_NAME} has a morning routine involving coffee and music.",
        status="live",
        date="2026-06-16",
    )

    # Candidate fact with different connective verb ("incorporates" vs "involving")
    query = f"{cfg.USER_NAME} incorporates coffee and music into his morning routine."
    matches = memory_db.find_similar_entries(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation_text=query,
        min_overlap=0.70,
        status="live",
    )
    assert len(matches) == 1
    assert matches[0]["id"] == e1
    assert matches[0]["overlap"] >= 0.70


def test_fast_deduplicate_exact_matches_propagates_latest_observation_and_date(mock_memory_db):
    """Verify fast_deduplicate_exact_matches updates master with newest date and last_observed."""
    t_old = 1780000000.0
    t_new = 1790000000.0

    # Primary entry (older discussion date, older last_observed)
    e1 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="Enjoys fresh hazelnut coffee.",
        status="live",
        date="2026-05-01",
        tags="coffee",
    )
    con = memory_db.get_db()
    con.execute("UPDATE context_entries SET last_observed = ?, observed_count = 1 WHERE id = ?", (t_old, e1))
    con.commit()

    # Exact duplicate entry (newer discussion date, newer last_observed)
    e2 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation="Enjoys fresh hazelnut coffee.",
        status="live",
        date="2026-10-15",
        tags="coffee, morning",
    )
    con.execute("UPDATE context_entries SET last_observed = ?, observed_count = 2 WHERE id = ?", (t_new, e2))
    con.commit()
    con.close()

    deleted = fact_deduplicator.fast_deduplicate_exact_matches()
    assert deleted == 1

    master = memory_db.get_entry(e1)
    assert master is not None
    assert master["observed_count"] == 3
    assert master["date"] == "2026-10-15"
    assert master["last_observed"] >= t_new
    assert "morning" in master["tags"]
    assert "coffee" in master["tags"]

    dup = memory_db.get_entry(e2)
    assert dup is None  # hard-deleted from live context_entries table


def test_extractor_reinforces_existing_fact_instead_of_duplicating(mock_memory_db):
    """Verify write_extracted_facts reinforces matching live facts without inserting duplicates."""
    from Evelyn.tools import fact_extractor

    e1 = memory_db.insert_entry(
        category="Cat05-U",
        subject=cfg.USER_NAME,
        observation=f"{cfg.USER_NAME} has a morning routine involving coffee and music.",
        status="live",
        date="2026-06-16",
        tags="routine",
    )
    orig_entry = memory_db.get_entry(e1)
    assert orig_entry["observed_count"] == 1
    orig_last_observed = orig_entry["last_observed"] or 0.0

    time.sleep(0.05)

    # Extract a fact that matches the existing habit
    extracted_facts = [
        {
            "category": "Cat05-U",
            "subject": cfg.USER_NAME,
            "summary": f"{cfg.USER_NAME} incorporates coffee and music into his morning routine.",
            "confidence": "high",
            "date": "2026-10-15",
            "tags": "coffee, music, routine",
        }
    ]

    inserted_count = fact_extractor.write_extracted_facts(extracted_facts)
    # Deduplicated, so not inserted as a new row
    assert inserted_count == 0

    # Verify master was reinforced
    updated = memory_db.get_entry(e1)
    assert updated is not None
    assert updated["observed_count"] == 2
    assert updated["last_observed"] > orig_last_observed
    assert updated["date"] == "2026-10-15"

    # Only 1 total live entry exists
    all_entries = memory_db.get_all_entries(statuses=["live"])
    assert len(all_entries) == 1
    assert all_entries[0]["id"] == e1


def test_unified_token_jaccard_stemming_parity():
    """Verify calculate_token_jaccard leverages canonical stemming and relational stopwords."""
    t1 = f"{cfg.USER_NAME} has a morning routine involving coffee and music."
    t2 = f"{cfg.USER_NAME} incorporates coffee and music into his morning routine."
    t3 = f"{cfg.USER_NAME} starts his morning with coffee and music."

    # Involving vs incorporates now normalize to identical keyword sets
    j12 = fact_deduplicator.calculate_token_jaccard(t1, t2)
    assert j12 == 1.0  # Identical keywords: routine, coffee, morn, music

    # Starts vs routine: subset overlap
    j13 = fact_deduplicator.calculate_token_jaccard(t1, t3)
    assert j13 >= 0.50
