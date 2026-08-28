# test_idle_task_queue.py
# date created: 2026-08-27
# tags: #tests, #tasks, #idle_queue, #fifo, #concurrency

"""Unit tests for the Persistent FIFO Idle Task Queue and cooperative batch catch-up."""

import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from Evelyn.tools import fact_extractor, task_manager


class TestIdleTaskQueue(unittest.TestCase):
    """Test suite for FIFO idle task queue, persistence, preemption, and yielding."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.queue_file = os.path.join(self.temp_dir, "test_task_queue.json")
        self.state_file = os.path.join(self.temp_dir, "test_heavy_tasks.json")

        # Patch paths
        self.orig_queue_file = task_manager.QUEUE_STATE_FILE
        self.orig_state_file = task_manager.STATE_FILE
        task_manager.QUEUE_STATE_FILE = self.queue_file
        task_manager.STATE_FILE = self.state_file

        # Clear queue and preemption
        task_manager._idle_queue.clear()
        task_manager.set_chat_preemption(False)
        task_manager._active_handles.clear()

    def tearDown(self):
        task_manager.QUEUE_STATE_FILE = self.orig_queue_file
        task_manager.STATE_FILE = self.orig_state_file
        task_manager._idle_queue.clear()
        task_manager.set_chat_preemption(False)
        task_manager._active_handles.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_enqueue_and_fifo_order(self):
        """Verify tasks enqueue at tail and acquire pops from front in strict FIFO order."""
        self.assertTrue(task_manager.enqueue_idle_task("extractor"))
        self.assertTrue(task_manager.enqueue_idle_task("tag_librarian"))
        self.assertTrue(task_manager.enqueue_idle_task("consolidator"))

        # Duplicate enqueue attempts should return False
        self.assertFalse(task_manager.enqueue_idle_task("extractor"))
        self.assertFalse(task_manager.enqueue_idle_task("tag_librarian"))

        self.assertEqual(len(task_manager.get_idle_queue()), 3)
        self.assertTrue(task_manager.is_task_queued("extractor"))
        self.assertTrue(task_manager.is_task_queued("consolidator"))

        # Acquire 1st
        item1 = task_manager.acquire_next_idle_task()
        self.assertIsNotNone(item1)
        self.assertEqual(item1["task"], "extractor")

        # Acquire 2nd
        item2 = task_manager.acquire_next_idle_task()
        self.assertIsNotNone(item2)
        self.assertEqual(item2["task"], "tag_librarian")

        # Acquire 3rd
        item3 = task_manager.acquire_next_idle_task()
        self.assertIsNotNone(item3)
        self.assertEqual(item3["task"], "consolidator")

        # Empty
        self.assertIsNone(task_manager.acquire_next_idle_task())

    def test_should_yield_on_queue_contention(self):
        """Verify should_yield returns False when queue is empty and True when peer tasks wait."""
        task_manager._idle_queue.clear()
        task_manager.set_chat_preemption(False)

        # No tasks in queue
        self.assertFalse(task_manager.should_yield("extractor"))

        # Peer task enqueued
        task_manager.enqueue_idle_task("tag_librarian")
        self.assertTrue(task_manager.should_yield("extractor"))

    def test_chat_preemption_blocks_and_forces_yield(self):
        """Verify chat preemption flag immediately forces should_yield and halts queue dispatch."""
        task_manager._idle_queue.clear()
        task_manager.set_chat_preemption(False)
        self.assertFalse(task_manager.should_yield("extractor"))

        task_manager.set_chat_preemption(True)
        self.assertTrue(task_manager.is_chat_preempted())
        self.assertTrue(task_manager.should_yield("extractor"))

        # Queue dispatch returns None while chat is preempted
        task_manager.enqueue_idle_task("extractor")
        self.assertIsNone(task_manager.acquire_next_idle_task())

        # Clearing preemption restores normal dispatch
        task_manager.set_chat_preemption(False)
        self.assertFalse(task_manager.is_chat_preempted())
        item = task_manager.acquire_next_idle_task()
        self.assertIsNotNone(item)
        self.assertEqual(item["task"], "extractor")

    def test_persistence_and_crash_recovery(self):
        """Verify task queue persists to disk and reconciles interrupted running tasks on boot."""
        task_manager.enqueue_idle_task("tag_librarian")
        task_manager.enqueue_idle_task("consolidator")
        task_manager.save_persistent_queue()

        self.assertTrue(os.path.exists(self.queue_file))

        # Clear in-memory queue and reload
        task_manager._idle_queue.clear()
        self.assertEqual(len(task_manager.get_idle_queue()), 0)

        # Mock a task that was running when server stopped
        mock_background_tasks = {
            "extractor": {"status": "running", "started_at": time.time() - 100}
        }

        with patch("Evelyn.tools.task_manager._get_background_tasks", return_value=mock_background_tasks):
            task_manager.load_persistent_queue()

        # Interrupted task 'extractor' should be placed at the front of the queue
        q = task_manager.get_idle_queue()
        self.assertEqual(len(q), 3)
        self.assertEqual(q[0]["task"], "extractor")
        self.assertEqual(q[1]["task"], "tag_librarian")
        self.assertEqual(q[2]["task"], "consolidator")

    def test_boot_grace_period(self):
        """Verify startup boot grace period tracks initialization window."""
        with patch("Evelyn.tools.task_manager._boot_ts", time.time()):
            self.assertTrue(task_manager.is_boot_grace_period_active())

        with patch("Evelyn.tools.task_manager._boot_ts", time.time() - 120):
            self.assertFalse(task_manager.is_boot_grace_period_active())


class TestFactExtractorBatchLoop(unittest.IsolatedAsyncioTestCase):
    """Test suite for Fact Extractor batch looping and cooperative FIFO yielding."""

    async def test_fact_extractor_cooperative_yield_and_re_enqueue(self):
        """Verify fact_extractor processes 1 batch, sees peer in queue, commits, re-enqueues, and exits."""
        task_manager._idle_queue.clear()
        task_manager.set_chat_preemption(False)

        batch_1_msgs = [{"role": "user", "content": "Fact 1", "ts": 1000}] * 10
        batch_2_msgs = [{"role": "user", "content": "Fact 2", "ts": 2000}] * 10

        fetch_calls = [
            (batch_1_msgs, 10), # First batch fetched
            (batch_2_msgs, 20), # Peek / second batch
            ([], 0),
        ]

        def mock_fetch():
            if fetch_calls:
                return fetch_calls.pop(0)
            return [], 0

        # Simulate another task waiting in the queue
        task_manager.enqueue_idle_task("tag_librarian")

        with patch("Evelyn.tools.fact_extractor._fetch_new_messages", side_effect=mock_fetch), \
             patch("Evelyn.tools.fact_extractor._do_extraction", new_callable=AsyncMock) as mock_extract, \
             patch("Evelyn.tools.fact_extractor._save_extraction_state") as mock_save_state, \
             patch("Evelyn.tools.fact_extractor._heavy_tasks_running", return_value=False), \
             patch("Evelyn.tools.task_manager.get_last_run_ts", return_value=0.0), \
             patch("Evelyn.tools.fact_extractor._set_status_in_server"):

            await fact_extractor.run_extraction()

            # Batch 1 should have been extracted
            self.assertEqual(mock_extract.call_count, 1)
            mock_save_state.assert_called_with(10)

            # Because tag_librarian was waiting in queue, extractor should have yielded and re-enqueued at tail
            q = task_manager.get_idle_queue()
            self.assertTrue(any(item["task"] == "extractor" for item in q))
            self.assertEqual(q[0]["task"], "tag_librarian") # tag_librarian is first
            self.assertEqual(q[1]["task"], "extractor")     # extractor re-enqueued at tail


if __name__ == "__main__":
    unittest.main()
