# test_temporal_rag_scoring.py
# date created: 2026-10-12
# date modified: 2026-10-13 00:43:42
# tags: #test, #rag, #temporal, #recency, #half_life

"""Unit tests for RAG Temporal Recency Scoring and Invalidation in chroma_rag.py."""

import sqlite3
import unittest
from unittest.mock import patch

import evelyn_config as cfg
from Evelyn.tools.chroma_rag import _apply_temporal_recency_boost


class TestTemporalRAGScoring(unittest.TestCase):
    def setUp(self):
        # Create an in-memory SQLite database simulating context_entries
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.execute(
            """CREATE TABLE context_entries (
                id INTEGER PRIMARY KEY,
                category TEXT NOT NULL,
                subject TEXT NOT NULL,
                observation TEXT NOT NULL,
                date TEXT,
                created_at REAL,
                last_observed REAL,
                status TEXT NOT NULL DEFAULT 'live'
            )"""
        )

    def tearDown(self):
        self.con.close()

    def test_temporal_recency_boost_recent_vs_old(self):
        """Verify that recent observations in adaptive categories (Cat05) outrank older ones."""
        # Reference epoch: 2026-10-12 12:00:00 UTC
        ref_ts = 1791806400.0

        # Entry 1: 1 day old (Cat05, half-life 75 days)
        # Entry 2: 250 days old (Cat05, half-life 75 days)
        self.con.execute(
            "INSERT INTO context_entries (id, category, subject, observation, date, status) VALUES (?, ?, ?, ?, ?, ?)",
            (101, "Cat05-U", "Alex", "Recent preference", "2026-10-11", "live"),
        )
        self.con.execute(
            "INSERT INTO context_entries (id, category, subject, observation, date, status) VALUES (?, ?, ?, ?, ?, ?)",
            (102, "Cat05-U", "Alex", "Outdated preference", "2026-02-04", "live"),
        )
        self.con.commit()

        chunks = [
            {"source": "sqlite::context_entry::102", "distance": 0.30, "content": "Outdated preference"},
            {"source": "sqlite::context_entry::101", "distance": 0.30, "content": "Recent preference"},
        ]

        with patch("Evelyn.tools.memory_db.get_db", return_value=self.con):
            re_ranked = _apply_temporal_recency_boost(chunks, now_ts=ref_ts)

        # Recent entry should have lower distance (promoted)
        # Old entry should have higher distance (penalized)
        self.assertEqual(re_ranked[0]["source"], "sqlite::context_entry::101")
        self.assertLess(re_ranked[0]["distance"], 0.30)
        self.assertEqual(re_ranked[1]["source"], "sqlite::context_entry::102")
        self.assertGreater(re_ranked[1]["distance"], 0.30)

    def test_temporal_recency_infinite_half_life_neutral(self):
        """Verify that historical episodic categories (Cat10) do not suffer recency decay penalties."""
        ref_ts = 1791806400.0

        # Entry in Cat10 (Major Life Events) from 20 years ago
        self.con.execute(
            "INSERT INTO context_entries (id, category, subject, observation, date, status) VALUES (?, ?, ?, ?, ?, ?)",
            (201, "Cat10-U", "Alex", "Historical life event", "2006-08-15", "live"),
        )
        self.con.commit()

        chunks = [
            {"source": "sqlite::context_entry::201", "distance": 0.28, "content": "Historical life event"},
        ]

        with patch("Evelyn.tools.memory_db.get_db", return_value=self.con):
            re_ranked = _apply_temporal_recency_boost(chunks, now_ts=ref_ts)

        # Distance should remain exactly 0.28 (neutral, no penalty)
        self.assertAlmostEqual(re_ranked[0]["distance"], 0.28, places=4)

    def test_temporal_recency_superseded_invalidation(self):
        """Verify that superseded or deleted facts are marked with infinite distance to be dropped."""
        ref_ts = 1791806400.0

        self.con.execute(
            "INSERT INTO context_entries (id, category, subject, observation, date, status) VALUES (?, ?, ?, ?, ?, ?)",
            (301, "Cat05-U", "Alex", "Superseded fact", "2026-10-10", "superseded"),
        )
        self.con.commit()

        chunks = [
            {"source": "sqlite::context_entry::301", "distance": 0.25, "content": "Superseded fact"},
        ]

        with patch("Evelyn.tools.memory_db.get_db", return_value=self.con):
            re_ranked = _apply_temporal_recency_boost(chunks, now_ts=ref_ts)

        self.assertEqual(re_ranked[0]["distance"], 999.0)

    def test_temporal_decay_disabled(self):
        """Verify that chunks remain unmodified when RAG_TEMPORAL_DECAY_ENABLED is False."""
        chunks = [
            {"source": "sqlite::context_entry::101", "distance": 0.35, "content": "Some fact"},
        ]
        with patch.object(cfg, "RAG_TEMPORAL_DECAY_ENABLED", False):
            res = _apply_temporal_recency_boost(chunks)
            self.assertEqual(res[0]["distance"], 0.35)

    def test_temporal_recency_robust_date_formats(self):
        """Verify date parsing tolerates ISO timestamp strings and falls back from year 0001."""
        ref_ts = 1791806400.0  # ~2026-10-12
        # Entry 401 has an ISO timestamp with time component; entry 402 has year 0001 with created_at fallback
        self.con.execute(
            "INSERT INTO context_entries (id, category, subject, observation, date, created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (401, "Cat05-U", "Alex", "Fact with time component", "2026-10-10 00:00:00+00:00", None, "live"),
        )
        self.con.execute(
            "INSERT INTO context_entries (id, category, subject, observation, date, created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (402, "Cat05-U", "Alex", "Fact with 0001 year", "0001-01-01 00:00:00", ref_ts - 86400, "live"),
        )
        self.con.commit()

        chunks = [
            {"source": "sqlite::context_entry::401", "distance": 0.30, "content": "Fact 401"},
            {"source": "sqlite::context_entry::402", "distance": 0.30, "content": "Fact 402"},
        ]

        with patch("Evelyn.tools.memory_db.get_db", return_value=self.con):
            re_ranked = _apply_temporal_recency_boost(chunks, now_ts=ref_ts)

        # Both should parse and receive recency boosts (distances closer than 0.30)
        self.assertLess(re_ranked[0]["distance"], 0.30)
        self.assertLess(re_ranked[1]["distance"], 0.30)


if __name__ == "__main__":
    unittest.main()
