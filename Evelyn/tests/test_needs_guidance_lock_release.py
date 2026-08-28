# test_needs_guidance_lock_release.py
# date created: 2026-08-10

import time
import unittest
from unittest.mock import MagicMock, patch


class TestNeedsGuidanceLockRelease(unittest.TestCase):
    def test_task_manager_is_any_running_returns_false_for_needs_guidance(self):
        """Verify task_manager.is_any_running() returns False when research task is in 'needs_guidance'."""
        mock_server = MagicMock()
        mock_server._background_tasks = {
            "task_12345678_9abcdef0": {
                "status": "needs_guidance",
                "query": "Test research query",
                "started_at": time.time() - 100,
                "finished_at": time.time() - 10
            }
        }

        with patch.dict("sys.modules", {"evelyn_server": mock_server}):
            from Evelyn.tools import task_manager
            is_running = task_manager.is_any_running()
            self.assertFalse(is_running, "task_manager.is_any_running() should return False when task status is needs_guidance")

    def test_background_tasks_status_syncs_needs_guidance(self):
        """Verify background task status syncs 'needs_guidance' status from disk state."""
        background_tasks = {
            "task_12345678_9abcdef0": {
                "status": "searching",
                "query": "Test query",
                "started_at": time.time() - 100
            }
        }
        mock_disk_state = {
            "task_id": "task_12345678_9abcdef0",
            "status": "needs_guidance",
            "query": "Test query",
            "scope": "standard",
            "created_at": "2026-08-10T12:00:00"
        }

        # Simulate the sync logic in _idle_research_loop
        for tid, _task in list(background_tasks.items()):
            if tid.startswith("task_"):
                status = mock_disk_state.get("status")
                if status:
                    background_tasks[tid]["status"] = status
                    if (
                        status in ("done", "error", "cancelled", "needs_guidance", "paused")
                        and ("finished_at" not in background_tasks[tid] or not background_tasks[tid].get("finished_at"))
                    ):
                        background_tasks[tid]["finished_at"] = time.time()

        self.assertEqual(background_tasks["task_12345678_9abcdef0"]["status"], "needs_guidance")
        self.assertIn("finished_at", background_tasks["task_12345678_9abcdef0"])

if __name__ == "__main__":
    unittest.main()
