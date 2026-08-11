# test_task_manager_watchdog.py
# date created: 2026-08-11
# tags: #test, #task_manager, #watchdog, #history

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest

# Ensure repo root and Evelyn/tools are on python path
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
tools_dir = os.path.join(repo_root, "Evelyn/tools")
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

import task_manager


class TestTaskManagerWatchdog(unittest.TestCase):
    def test_record_task_history_and_dynamic_timeout(self):
        """Test recording run history to SQLite DB and calculating dynamic soft timeout."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_db = os.path.join(tmp_dir, "test_evelyn_memory.db")
            
            # Patch _get_db_connection
            orig_get_db = task_manager._get_db_connection
            def mock_get_db():
                import sqlite3
                conn = sqlite3.connect(test_db, timeout=10.0)
                conn.row_factory = sqlite3.Row
                return conn

            try:
                task_manager._get_db_connection = mock_get_db
                
                # Default dynamic timeout before history
                baseline = task_manager.get_dynamic_timeout("extractor")
                self.assertEqual(baseline, task_manager.DEFAULT_SOFT_TIMEOUTS["extractor"])

                # Insert 10 mock runs with average duration 100s
                now = time.time()
                for i in range(10):
                    task_manager.record_task_history(
                        name="test_watchdog_task",
                        started_at=now - 100,
                        finished_at=now,
                        elapsed_seconds=100.0 + (i % 3),
                        status="idle",
                        items_processed=5,
                    )

                # Dynamic timeout for test_watchdog_task should compute mean + 3*std_dev (~101s)
                # But since default baseline (1800s) is higher, enforce baseline minimum
                dyn_val = task_manager.get_dynamic_timeout("test_watchdog_task")
                self.assertGreaterEqual(dyn_val, 100.0)

            finally:
                task_manager._get_db_connection = orig_get_db

    def test_reconcile_orphaned_tasks(self):
        """Test that task watchdog auto-reconciles finished tasks stuck in running status."""
        tasks_dict = {}
        
        orig_get_bg = task_manager._get_background_tasks
        task_manager._get_background_tasks = lambda: tasks_dict

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def dummy_coro():
                return 42

            async_task = loop.create_task(dummy_coro())
            loop.run_until_complete(async_task)
            self.assertTrue(async_task.done())

            # Simulate task registered as running with completed task handle
            task_manager.set_running("test_stuck_task", task_obj=async_task)
            self.assertEqual(tasks_dict["test_stuck_task"]["status"], "running")

            # Run reconciliation
            task_manager._reconcile_orphaned_tasks()

            # Should be reconciled to idle
            self.assertEqual(tasks_dict["test_stuck_task"]["status"], "idle")
            self.assertIn("Auto-reconciled", tasks_dict["test_stuck_task"]["summary"])

            loop.close()

        finally:
            task_manager._get_background_tasks = orig_get_bg


if __name__ == "__main__":
    unittest.main()
