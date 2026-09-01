# test_auto_journaler.py
# date created: 2026-08-30 15:46:00
# date modified: 2026-09-01 17:30:20
# tags: #test, #auto_journaler, #map-reduce, #daemon, #journal

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import unittest
from unittest.mock import MagicMock, patch

import evelyn_config as cfg
from Evelyn.tools import auto_journaler, task_manager


class TestAutoJournaler(unittest.TestCase):
    def setUp(self):
        task_manager.set_chat_preemption(False)

    def tearDown(self):
        task_manager.set_chat_preemption(False)

    def test_resolve_target_journal_date_circadian_window(self):
        # 1. Late evening at 23:30 on Aug 30 -> Target date is Aug 30
        evening_dt = datetime(2026, 8, 30, 23, 30, 0, tzinfo=UTC)
        target_date, start_ts, end_ts = auto_journaler.resolve_target_journal_date(evening_dt)
        self.assertEqual(target_date.strftime("%Y-%m-%d"), "2026-08-30")
        self.assertLess(start_ts, end_ts)

        # 2. Midnight crossover at 02:15 on Aug 31 -> Target date is yesterday (Aug 30)
        night_dt = datetime(2026, 8, 31, 2, 15, 0, tzinfo=UTC)
        target_date, start_ts, end_ts = auto_journaler.resolve_target_journal_date(night_dt)
        self.assertEqual(target_date.strftime("%Y-%m-%d"), "2026-08-30")

        # 3. Morning at 05:00 on Aug 31 (past AUTO_JOURNAL_END_HOUR = 4) -> Target date is Aug 31
        morning_dt = datetime(2026, 8, 31, 5, 0, 0, tzinfo=UTC)
        target_date, start_ts, end_ts = auto_journaler.resolve_target_journal_date(morning_dt)
        self.assertEqual(target_date.strftime("%Y-%m-%d"), "2026-08-31")

    @patch("Evelyn.tools.journal_manager._resolve_journal_filepath")
    @patch("sqlite3.connect")
    def test_should_trigger_auto_journal_gate_checks(self, mock_sqlite, mock_resolve_file):
        # 1. Test disabled in config
        with patch.object(cfg, "AUTO_JOURNAL_ENABLED", False):
            eligible, reason = auto_journaler.should_trigger_auto_journal()
            self.assertFalse(eligible)
            self.assertIn("disabled", reason.lower())

        # 2. Test outside circadian window (e.g. 14:00 daytime)
        afternoon_dt = datetime(2026, 8, 30, 14, 0, 0, tzinfo=UTC)
        eligible, reason = auto_journaler.should_trigger_auto_journal(now_dt=afternoon_dt, idle_seconds=6000)
        self.assertFalse(eligible)
        self.assertIn("outside after-hours window", reason.lower())

        # 3. Test insufficient inactivity during valid window
        night_dt = datetime(2026, 8, 30, 23, 30, 0, tzinfo=UTC)
        eligible, reason = auto_journaler.should_trigger_auto_journal(now_dt=night_dt, idle_seconds=1200)
        self.assertFalse(eligible)
        self.assertIn("below threshold", reason.lower())

        # 4. Test vault collision (journal already exists for target date)
        mock_resolve_file.return_value = "/mock/vault/Journal Entry 2026-08-30.md"
        with patch("os.path.exists", return_value=True):
            eligible, reason = auto_journaler.should_trigger_auto_journal(now_dt=night_dt, idle_seconds=6000)
            self.assertFalse(eligible)
            self.assertIn("already exists", reason.lower())

        # 5. Test insufficient messages in DB (< 4 messages)
        mock_resolve_file.return_value = None
        mock_con = MagicMock()
        mock_cursor = MagicMock()
        mock_con.cursor.return_value = mock_cursor
        mock_cursor.execute.return_value.fetchone.return_value = (2,)  # Only 2 messages
        mock_con.execute.return_value.fetchone.return_value = (2,)
        mock_sqlite.return_value = mock_con

        with patch("os.path.exists", return_value=False):
            eligible, reason = auto_journaler.should_trigger_auto_journal(now_dt=night_dt, idle_seconds=6000)
            self.assertFalse(eligible)
            self.assertIn("insufficient conversation turns", reason.lower())

        # 6. Test all gates passing (eligible with explicit idle_seconds)
        mock_cursor.execute.return_value.fetchone.return_value = (18,)  # 18 messages
        mock_con.execute.return_value.fetchone.return_value = (18,)
        with patch("os.path.exists", return_value=False):
            eligible, reason = auto_journaler.should_trigger_auto_journal(now_dt=night_dt, idle_seconds=6000)
            self.assertTrue(eligible)
            self.assertIn("eligible", reason.lower())

        # 7. Test automatic idle_seconds calculation from chat DB when omitted/zero
        with patch("os.path.exists", return_value=False), \
             patch("Evelyn.tools.time_manager.get_user_idle_seconds", return_value=7200.0):
            eligible, reason = auto_journaler.should_trigger_auto_journal(now_dt=night_dt, idle_seconds=0.0)
            self.assertTrue(eligible)
            self.assertIn("eligible", reason.lower())

    def test_compact_history_map_reduce_small_transcript(self):
        # 10 turns -> Fits comfortably under safe budget -> Left untouched
        messages = [{"role": "user", "content": f"Turn {i} content"} for i in range(10)]
        res = asyncio.run(auto_journaler.compact_history_map_reduce(messages, chunk_size=25, safe_budget=16000))
        self.assertEqual(len(res), 10)
        self.assertEqual(res, messages)

    @patch("Evelyn.tools.ollama_client.query_ollama")
    def test_compact_history_map_reduce_high_turn_chunking(self, mock_query):
        mock_query.return_value = "- Worked on garden\n- Fixed system procedures"

        # Create 60 messages (each ~500 chars) exceeding safe_budget of 2000 tokens
        messages = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"Detailed turn {i}: " + ("data " * 100)}
            for i in range(60)
        ]

        res = asyncio.run(auto_journaler.compact_history_map_reduce(messages, chunk_size=20, safe_budget=2000))

        # Recent 20 turns are kept raw + 1 system digest message for older turns
        self.assertEqual(len(res), 21)
        self.assertEqual(res[0]["role"], "system")
        self.assertIn("<day_history_digest>", res[0]["content"])
        self.assertIn("Worked on garden", res[0]["content"])
        self.assertEqual(res[1]["content"], messages[40]["content"])

    def test_preemption_in_compaction(self):
        task_manager.set_chat_preemption(True)
        messages = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"Detailed turn {i}: " + ("data " * 100)}
            for i in range(60)
        ]

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(auto_journaler.compact_history_map_reduce(messages, chunk_size=20, safe_budget=2000))


if __name__ == "__main__":
    unittest.main()
