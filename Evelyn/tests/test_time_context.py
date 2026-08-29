# test_time_context.py
# date created: 2026-08-29
# date modified: 2026-08-29 13:20:42
# tags: #test, #temporal, #time-manager, #agenda, #heartbeat

"""Unit tests for Evelyn Temporal Management Subsystem (TimeManager) and evelyn_server time integration."""

import sqlite3
import unittest
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import evelyn_config as cfg
from Evelyn.tools.time_manager import TimeManager


class TestTimeContext(unittest.TestCase):
    """Test suite for TimeManager, telemetry directives, and time context formatting."""

    def setUp(self):
        """Set up an in-memory SQLite database and test TimeManager instance."""
        self.tz = ZoneInfo(getattr(cfg, "USER_TIMEZONE", "America/Chicago"))
        self.tm = TimeManager(
            idle_threshold_minutes=45,
            calendar_lookahead_hours=4,
            task_lookahead_hours=2,
            timezone_name=getattr(cfg, "USER_TIMEZONE", "America/Chicago"),
        )
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row

        # Create schema matching live database
        self.con.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                thinking TEXT,
                tools_used TEXT,
                ts REAL NOT NULL
            )
            """
        )
        self.con.execute(
            """
            CREATE TABLE calendar_events (
                id TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                description TEXT,
                start_at TEXT NOT NULL,
                end_at TEXT NOT NULL,
                location TEXT,
                source TEXT NOT NULL DEFAULT 'google',
                last_sync TEXT NOT NULL
            )
            """
        )
        self.con.execute(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                tasklist_id TEXT NOT NULL DEFAULT '@default',
                title TEXT NOT NULL,
                notes TEXT,
                due_at TEXT,
                status TEXT NOT NULL DEFAULT 'needsAction',
                completed_at TEXT,
                source TEXT NOT NULL DEFAULT 'google',
                last_sync TEXT NOT NULL
            )
            """
        )

    def tearDown(self):
        """Close in-memory database."""
        self.con.close()

    def test_load_system_prompt_contains_date_time_and_telemetry_directives(self):
        """Verify system prompt includes localized clock and environmental telemetry directives."""
        from evelyn_server import load_system_prompt

        sys_prompt = load_system_prompt()
        self.assertIn("The current date and time is", sys_prompt)
        self.assertIn("<system_telemetry_directives>", sys_prompt)
        self.assertIn("<temporal_context>", sys_prompt)
        self.assertIn("`<current_time>` is the sole authoritative clock", sys_prompt)
        self.assertIn("Treat `<session_gap>` as passive atmospheric awareness", sys_prompt)
        self.assertIn(f"Never attribute telemetry blocks to {cfg.USER_NAME}", sys_prompt)

    def test_parse_dt_normalization(self):
        """Verify parse_dt normalizes epoch floats, all-day dates, ISO strings, and standard timestamps."""
        # 1. UNIX epoch float (UTC -> Central)
        base_utc = datetime(2026, 8, 29, 15, 0, 0, tzinfo=UTC)
        epoch = base_utc.timestamp()
        dt_epoch = self.tm.parse_dt(epoch)
        self.assertIsNotNone(dt_epoch)
        self.assertEqual(dt_epoch.hour, 10)  # 15:00 UTC == 10:00 CDT (-5)

        # 2. All-day date-only string (YYYY-MM-DD)
        dt_allday = self.tm.parse_dt("2026-08-29")
        self.assertIsNotNone(dt_allday)
        self.assertEqual(dt_allday.year, 2026)
        self.assertEqual(dt_allday.month, 8)
        self.assertEqual(dt_allday.day, 29)
        self.assertEqual(dt_allday.hour, 0)
        self.assertEqual(dt_allday.tzinfo, self.tm.local_tz)

        # 3. ISO 8601 string with offset
        dt_iso = self.tm.parse_dt("2026-08-29T14:30:00-05:00")
        self.assertIsNotNone(dt_iso)
        self.assertEqual(dt_iso.hour, 14)
        self.assertEqual(dt_iso.minute, 30)

        # 4. Standard SQLite timestamp
        dt_sql = self.tm.parse_dt("2026-08-29 18:45:00")
        self.assertIsNotNone(dt_sql)
        self.assertEqual(dt_sql.hour, 18)
        self.assertEqual(dt_sql.minute, 45)

    def test_role_agnostic_interaction_gap(self):
        """Verify silence is measured against the latest message regardless of role (Flaw B fix)."""
        # User message at 10:00 AM Central
        t_user = datetime(2026, 8, 29, 10, 0, 0, tzinfo=self.tz)
        self.con.execute(
            "INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)",
            ("user", "Hello Evelyn", t_user.timestamp()),
        )

        # Assistant replied at 10:14 AM Central
        t_assistant = datetime(2026, 8, 29, 10, 14, 0, tzinfo=self.tz)
        self.con.execute(
            "INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)",
            ("assistant", "Hello Ricky! How are you?", t_assistant.timestamp()),
        )

        # User sends new message at 10:15 AM Central (1 minute after assistant)
        t_now = datetime(2026, 8, 29, 10, 15, 0, tzinfo=self.tz)
        gap = self.tm.evaluate_session_gap(self.con, t_now)

        # Should be None (active flow), NOT a 15-minute gap from user's 10:00 AM turn
        self.assertIsNone(gap)

    def test_evaluate_session_gap_thresholds(self):
        """Verify gaps under 45m return None while gaps >= 45m return duration strings."""
        t_start = datetime(2026, 8, 29, 8, 0, 0, tzinfo=self.tz)
        self.con.execute(
            "INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)",
            ("assistant", "See you later!", t_start.timestamp()),
        )

        # 44 minutes later -> None (under 45m threshold)
        t_44m = t_start + timedelta(minutes=44)
        self.assertIsNone(self.tm.evaluate_session_gap(self.con, t_44m))

        # 45 minutes later -> resumed
        t_45m = t_start + timedelta(minutes=45)
        gap_45m = self.tm.evaluate_session_gap(self.con, t_45m)
        self.assertIsNotNone(gap_45m)
        self.assertEqual(gap_45m["elapsed_minutes"], 45)
        self.assertEqual(gap_45m["duration_str"], "45m")

        # 4 hours 30 minutes later -> "4h 30m"
        t_4h30m = t_start + timedelta(hours=4, minutes=30)
        gap_4h = self.tm.evaluate_session_gap(self.con, t_4h30m)
        self.assertIsNotNone(gap_4h)
        self.assertEqual(gap_4h["duration_str"], "4h 30m")

    def test_calendar_agenda_lookahead_and_all_day(self):
        """Verify calendar events within lookahead window and all-day events are correctly extracted."""
        now = datetime(2026, 8, 29, 11, 0, 0, tzinfo=self.tz)

        # 1. Event starting in 30 minutes
        ev1_start = now + timedelta(minutes=30)
        ev1_end = ev1_start + timedelta(hours=1)
        self.con.execute(
            "INSERT INTO calendar_events (id, summary, start_at, end_at, last_sync) VALUES (?, ?, ?, ?, ?)",
            ("ev1", "Design Review", ev1_start.isoformat(), ev1_end.isoformat(), now.isoformat()),
        )

        # 2. All-day event today
        self.con.execute(
            "INSERT INTO calendar_events (id, summary, start_at, end_at, last_sync) VALUES (?, ?, ?, ?, ?)",
            ("ev2", "Saturday Workshop", "2026-08-29", "2026-08-30", now.isoformat()),
        )

        events = self.tm.get_calendar_agenda(self.con, now)
        self.assertEqual(len(events), 2)
        summaries = [e["title"] for e in events]
        self.assertIn("Design Review", summaries)
        self.assertIn("Saturday Workshop", summaries)

        ev_timed = next(e for e in events if e["id"] == "ev1")
        self.assertEqual(ev_timed["status"], "In 30 minutes")

        ev_allday = next(e for e in events if e["id"] == "ev2")
        self.assertEqual(ev_allday["status"], "All day today")

    def test_imminent_and_overdue_tasks(self):
        """Verify overdue and imminent tasks are identified with accurate status strings."""
        now = datetime(2026, 8, 29, 11, 0, 0, tzinfo=self.tz)

        # 1. Task overdue by 20 minutes
        t_overdue = now - timedelta(minutes=20)
        self.con.execute(
            "INSERT INTO tasks (id, title, due_at, status, last_sync) VALUES (?, ?, ?, ?, ?)",
            ("tk1", "Submit Report", t_overdue.isoformat(), "needsAction", now.isoformat()),
        )

        # 2. Task due in 45 minutes
        t_imminent = now + timedelta(minutes=45)
        self.con.execute(
            "INSERT INTO tasks (id, title, due_at, status, last_sync) VALUES (?, ?, ?, ?, ?)",
            ("tk2", "Hydrate and stretch", t_imminent.isoformat(), "needsAction", now.isoformat()),
        )

        # 3. Completed task (should be excluded)
        self.con.execute(
            "INSERT INTO tasks (id, title, due_at, status, last_sync) VALUES (?, ?, ?, ?, ?)",
            ("tk3", "Old Finished Task", t_overdue.isoformat(), "completed", now.isoformat()),
        )

        tasks = self.tm.get_imminent_tasks(self.con, now)
        self.assertEqual(len(tasks), 2)

        overdue_tk = next(t for t in tasks if t["id"] == "tk1")
        self.assertEqual(overdue_tk["status"], "Overdue by 20m")

        imminent_tk = next(t for t in tasks if t["id"] == "tk2")
        self.assertEqual(imminent_tk["status"], "Due in 45 minutes")

    def test_build_temporal_envelope_xml_structure(self):
        """Verify build_temporal_envelope outputs complete, well-formed XML metadata."""
        now = datetime(2026, 8, 29, 11, 0, 0, tzinfo=self.tz)

        # Insert a message 2 hours ago
        t_msg = now - timedelta(hours=2)
        self.con.execute(
            "INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)",
            ("assistant", "See you later", t_msg.timestamp()),
        )

        # Insert upcoming event
        t_ev = now + timedelta(minutes=15)
        self.con.execute(
            "INSERT INTO calendar_events (id, summary, start_at, end_at, last_sync) VALUES (?, ?, ?, ?, ?)",
            ("ev1", "Team Sync", t_ev.isoformat(), (t_ev + timedelta(hours=1)).isoformat(), now.isoformat()),
        )

        # Insert upcoming task
        t_tk = now + timedelta(minutes=10)
        self.con.execute(
            "INSERT INTO tasks (id, title, due_at, status, last_sync) VALUES (?, ?, ?, ?, ?)",
            ("tk1", "Review PR", t_tk.isoformat(), "needsAction", now.isoformat()),
        )

        envelope = self.tm.build_temporal_envelope(self.con, now)

        self.assertTrue(envelope.startswith("<temporal_context>"))
        self.assertTrue(envelope.endswith("</temporal_context>"))
        self.assertIn('status="resumed"', envelope)
        self.assertIn('break_duration="2h"', envelope)
        self.assertIn('last_interaction="2026-08-29 09:00 AM"', envelope)
        self.assertIn('<calendar_agenda>', envelope)
        self.assertIn('event title="Team Sync" time="11:15 AM" status="In 15 minutes"', envelope)
        self.assertIn('<task_agenda>', envelope)
        self.assertIn('task title="Review PR" time="11:10 AM" status="Due in 10 minutes"', envelope)

    def test_heartbeat_alert_evaluation_and_deduplication(self):
        """Verify evaluate_heartbeat triggers alerts, deduplicates them, and prunes stale keys."""
        now = datetime(2026, 8, 29, 11, 0, 0, tzinfo=self.tz)

        # Task due in 5 minutes
        t_tk = now + timedelta(minutes=5)
        self.con.execute(
            "INSERT INTO tasks (id, title, due_at, status, last_sync) VALUES (?, ?, ?, ?, ?)",
            ("tk_alert", "Take Medication", t_tk.isoformat(), "needsAction", now.isoformat()),
        )

        # Tick 1: Alert should fire
        alerts_1 = self.tm.evaluate_heartbeat(self.con, now)
        self.assertEqual(len(alerts_1), 1)
        self.assertEqual(alerts_1[0]["type"], "task_imminent")
        self.assertEqual(alerts_1[0]["entity_id"], "tk_alert")

        # Tick 2: Consecutive call at the same minute should be deduplicated (0 alerts)
        alerts_2 = self.tm.evaluate_heartbeat(self.con, now)
        self.assertEqual(len(alerts_2), 0)

        # Reset cache
        self.tm.reset_alert_cache()
        alerts_3 = self.tm.evaluate_heartbeat(self.con, now)
        self.assertEqual(len(alerts_3), 1)


if __name__ == "__main__":
    unittest.main()
