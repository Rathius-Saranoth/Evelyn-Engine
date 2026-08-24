# test_gtasks_sync.py
# date created: 2026-08-23
# tags: #test, #gtasks, #tasks, #sync, #tools

"""Unit tests for gtasks_sync.py and Google Tasks integration."""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import evelyn_config as cfg
from Evelyn.tools import evelyn_tools, gtasks_sync


class TestGTasksSync(unittest.TestCase):
    """Test suite for Google Tasks synchronizer and local cache."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_db_path = os.path.join(self.temp_dir.name, "test_chat.db")
        self.orig_chat_db = cfg.CHAT_DB_PATH
        cfg.CHAT_DB_PATH = self.test_db_path

        # Initialize schema
        con = sqlite3.connect(self.test_db_path)
        con.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id           TEXT PRIMARY KEY,
                tasklist_id  TEXT NOT NULL DEFAULT '@default',
                title        TEXT NOT NULL,
                notes        TEXT,
                due_at       TEXT,
                status       TEXT NOT NULL DEFAULT 'needsAction',
                completed_at TEXT,
                source       TEXT NOT NULL DEFAULT 'google',
                last_sync    TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id          TEXT PRIMARY KEY,
                summary     TEXT NOT NULL,
                description TEXT,
                start_at    TEXT NOT NULL,
                end_at      TEXT NOT NULL,
                location    TEXT,
                source      TEXT NOT NULL DEFAULT 'google',
                last_sync   TEXT NOT NULL
            )
        """)
        con.commit()
        con.close()

    def tearDown(self):
        cfg.CHAT_DB_PATH = self.orig_chat_db
        self.temp_dir.cleanup()

    def test_parse_due_datetime(self):
        """Test normalization of due date formats into RFC 3339."""
        # None / empty
        self.assertIsNone(gtasks_sync.parse_due_datetime(None))
        self.assertIsNone(gtasks_sync.parse_due_datetime(""))
        self.assertIsNone(gtasks_sync.parse_due_datetime("   "))

        # Date only
        res_date = gtasks_sync.parse_due_datetime("2026-08-24")
        self.assertEqual(res_date, "2026-08-24T00:00:00.000Z")

        # Datetime
        res_dt = gtasks_sync.parse_due_datetime("2026-08-24 15:30:00")
        self.assertEqual(res_dt, "2026-08-24T15:30:00.000Z")

        # ISO string
        res_iso = gtasks_sync.parse_due_datetime("2026-08-24T15:30:00Z")
        self.assertEqual(res_iso, "2026-08-24T15:30:00.000Z")

    def test_get_cached_tasks_empty(self):
        """Test retrieving tasks from empty cache."""
        tasks = gtasks_sync.get_cached_tasks()
        self.assertEqual(tasks, [])

    def test_cached_tasks_insert_and_query(self):
        """Test inserting and filtering cached tasks."""
        con = sqlite3.connect(self.test_db_path)
        con.execute(
            """
            INSERT INTO tasks (id, tasklist_id, title, notes, due_at, status, last_sync)
            VALUES ('task_1', '@default', 'Buy groceries', 'Milk and eggs', '2026-08-24T18:00:00.000Z', 'needsAction', '2026-08-23T12:00:00Z'),
                   ('task_2', '@default', 'Finished task', 'Done', '2026-08-22T12:00:00.000Z', 'completed', '2026-08-23T12:00:00Z')
            """
        )
        con.commit()
        con.close()

        # Without completed
        pending = gtasks_sync.get_cached_tasks(include_completed=False)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], "task_1")
        self.assertEqual(pending[0]["title"], "Buy groceries")

        # With completed
        all_tasks = gtasks_sync.get_cached_tasks(include_completed=True)
        self.assertEqual(len(all_tasks), 2)

    @patch("Evelyn.tools.gtasks_sync.get_gtasks_service")
    def test_create_gtask(self, mock_get_service):
        """Test creating a task via mock API and caching it."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        mock_insert = MagicMock()
        mock_insert.execute.return_value = {
            "id": "new_task_123",
            "title": "Pick up package",
            "notes": "At UPS store",
            "due": "2026-08-25T00:00:00.000Z",
            "status": "needsAction",
        }
        mock_service.tasks.return_value.insert.return_value = mock_insert

        result = gtasks_sync.create_gtask(
            title="Pick up package",
            due="2026-08-25",
            notes="At UPS store",
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["task_id"], "new_task_123")

        # Check SQLite cache
        cached = gtasks_sync.get_cached_tasks()
        self.assertEqual(len(cached), 1)
        self.assertEqual(cached[0]["id"], "new_task_123")
        self.assertEqual(cached[0]["title"], "Pick up package")

    @patch("Evelyn.tools.gtasks_sync.get_gtasks_service")
    def test_complete_gtask(self, mock_get_service):
        """Test completing a task via mock API and updating SQLite."""
        # Insert initial pending task
        con = sqlite3.connect(self.test_db_path)
        con.execute(
            """
            INSERT INTO tasks (id, tasklist_id, title, notes, due_at, status, last_sync)
            VALUES ('task_to_complete', '@default', 'Walk dog', '', '2026-08-24T18:00:00.000Z', 'needsAction', '2026-08-23T12:00:00Z')
            """
        )
        con.commit()
        con.close()

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_patch = MagicMock()
        mock_patch.execute.return_value = {"id": "task_to_complete", "status": "completed"}
        mock_service.tasks.return_value.patch.return_value = mock_patch

        res = gtasks_sync.complete_gtask("task_to_complete")
        self.assertEqual(res["status"], "success")

        # Verify status in cache
        cached = gtasks_sync.get_cached_tasks(include_completed=True)
        self.assertEqual(len(cached), 1)
        self.assertEqual(cached[0]["status"], "completed")

    @patch("Evelyn.tools.gtasks_sync.get_gtasks_service")
    def test_delete_gtask(self, mock_get_service):
        """Test deleting a task via mock API and removing from SQLite."""
        con = sqlite3.connect(self.test_db_path)
        con.execute(
            """
            INSERT INTO tasks (id, tasklist_id, title, notes, due_at, status, last_sync)
            VALUES ('task_to_delete', '@default', 'Old task', '', NULL, 'needsAction', '2026-08-23T12:00:00Z')
            """
        )
        con.commit()
        con.close()

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_delete = MagicMock()
        mock_delete.execute.return_value = {}
        mock_service.tasks.return_value.delete.return_value = mock_delete

        res = gtasks_sync.delete_gtask("task_to_delete")
        self.assertEqual(res["status"], "success")

        # Verify removal
        cached = gtasks_sync.get_cached_tasks(include_completed=True)
        self.assertEqual(len(cached), 0)

    @patch("Evelyn.tools.evelyn_tools.gtasks_sync.get_gtasks_service")
    def test_evelyn_tools_tasks_wrappers(self, mock_get_service):
        """Test evelyn_tools wrapper functions."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_insert = MagicMock()
        mock_insert.execute.return_value = {
            "id": "tool_task_1",
            "title": "Review PR",
            "notes": "Code review",
            "due": "2026-08-24T18:00:00.000Z",
            "status": "needsAction",
        }
        mock_service.tasks.return_value.insert.return_value = mock_insert

        # create_task
        create_res = evelyn_tools.create_task(title="Review PR", due_at="2026-08-24 18:00:00", notes="Code review")
        self.assertIn("Successfully created task", create_res)
        self.assertIn("tool_task_1", create_res)

        # list_tasks
        list_res = evelyn_tools.list_tasks()
        self.assertIn("Google Tasks:", list_res)
        self.assertIn("Review PR", list_res)

        # get_agenda (unified)
        agenda_res = evelyn_tools.get_agenda(days=7)
        self.assertIn("Pending Tasks:", agenda_res)
        self.assertIn("Review PR", agenda_res)


if __name__ == "__main__":
    unittest.main()
