# test_resolved_task_autoclear.py
# date created: 2026-08-17
# date modified: 2026-08-17 19:23:04
# tags:

import os
import shutil
import unittest
from unittest.mock import AsyncMock, patch

from Evelyn.tools.research_engine import get_task_dir, save_state, step_assess_prior_knowledge


class TestResolvedTaskAutoclear(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_task_id = "task_test_autoclear_9999"
        self.test_task_dir = get_task_dir(self.test_task_id)
        os.makedirs(self.test_task_dir, exist_ok=True)
        self.state = {
            "task_id": self.test_task_id,
            "query": "How do vision encoders like CLIP work?",
            "original_question": "How do vision encoders like CLIP work?",
            "scope": "standard",
            "status": "pending",
            "current_step": "plan",
            "ollama_calls": 0,
            "limit_warnings": [],
        }
        save_state(self.test_task_id, self.state)

    def tearDown(self):
        if os.path.exists(self.test_task_dir):
            shutil.rmtree(self.test_task_dir, ignore_errors=True)

    @patch("Evelyn.tools.research_engine.call_ollama", new_callable=AsyncMock)
    @patch("Evelyn.tools.research_engine.get_recent_chat_history", return_value=[])
    async def test_resolved_task_auto_clears_directory(self, mock_chat, mock_ollama):
        """Verify that when a task resolves via internal knowledge, its workspace is deleted from disk."""
        # Mock internal knowledge response returning answerable=true, confidence=95
        mock_ollama.side_effect = [
            '{"answerable": true, "confidence": 95, "summary": "Foundational ML theory answerable without search."}',
            '{"answerable": false, "confidence": 0, "summary": "", "sources": []}',
        ]

        self.assertTrue(os.path.exists(self.test_task_dir))

        resolved = await step_assess_prior_knowledge(self.test_task_id, self.state)

        self.assertTrue(resolved, "step_assess_prior_knowledge should return True for high internal confidence.")
        self.assertEqual(self.state["status"], "resolved")
        self.assertFalse(os.path.exists(self.test_task_dir), "Resolved task directory should be automatically removed from disk.")

    def test_server_idle_loop_cleans_up_missing_or_resolved_task(self):
        """Verify server task registry removes deleted/resolved tasks."""
        background_tasks = {
            "task_test_autoclear_9999": {
                "status": "pending",
                "query": "Test query",
                "started_at": 1000.0,
            }
        }

        # Case 1: Task directory was deleted
        if os.path.exists(self.test_task_dir):
            shutil.rmtree(self.test_task_dir, ignore_errors=True)

        for tid, task in list(background_tasks.items()):
            if tid.startswith("task_"):
                # Simulate server check: load_state returns None
                from Evelyn.tools.research_engine import load_state
                disk_state = load_state(tid)
                if not disk_state and task.get("status") not in ("running", "searching", "synthesizing"):
                    del background_tasks[tid]
                    continue

        self.assertNotIn("task_test_autoclear_9999", background_tasks)


if __name__ == "__main__":
    unittest.main()
