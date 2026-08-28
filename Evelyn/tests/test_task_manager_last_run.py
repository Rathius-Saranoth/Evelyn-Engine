# test_task_manager_last_run.py
# date created: 2026-08-10

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


class TestTaskManagerLastRun(unittest.TestCase):
    def test_get_last_run_ts_fallback(self):
        """Test get_last_run_ts returns default when task has no timestamp recorded."""
        val = task_manager.get_last_run_ts("non_existent_task_xyz", default=123.45)
        self.assertEqual(val, 123.45)

    def test_save_and_get_last_run_ts(self):
        """Test saving and retrieving last_run_ts in task_manager."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_state_file = os.path.join(tmp_dir, "test_heavy_tasks_state.json")
            original_state_file = task_manager.STATE_FILE
            try:
                task_manager.STATE_FILE = test_state_file
                now = time.time()

                # Save timestamp for a task
                saved_ts = task_manager.save_last_run_ts("custom_consolidator", ts=now)
                self.assertEqual(saved_ts, now)

                # Read back timestamp
                fetched_ts = task_manager.get_last_run_ts("custom_consolidator")
                self.assertEqual(fetched_ts, now)

                # Verify disk contents
                self.assertTrue(os.path.exists(test_state_file))
                with open(test_state_file, encoding="utf-8") as f:
                    data = json.load(f)
                self.assertEqual(data["custom_consolidator"]["last_run_at"], now)

            finally:
                task_manager.STATE_FILE = original_state_file

    def test_clear_running_updates_last_run_ts(self):
        """Test that clear_running automatically updates last_run_at timestamp."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_state_file = os.path.join(tmp_dir, "test_heavy_tasks_state2.json")
            original_state_file = task_manager.STATE_FILE
            try:
                task_manager.STATE_FILE = test_state_file

                task_manager.set_running("custom_task_abc")
                time.sleep(0.05)
                task_manager.clear_running("custom_task_abc", status="idle")

                fetched_ts = task_manager.get_last_run_ts("custom_task_abc")
                self.assertGreater(fetched_ts, 0)
            finally:
                task_manager.STATE_FILE = original_state_file


if __name__ == "__main__":
    unittest.main()
