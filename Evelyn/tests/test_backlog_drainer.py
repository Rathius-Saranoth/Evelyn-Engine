# test_backlog_drainer.py
# date created: 2026-09-05 17:36:00
# date modified: 2026-09-05 17:36:00
# tags: #test, #backlog_drainer, #queue, #unit_test

"""Unit tests for the canonical backlog_drainer module."""

import asyncio
import time
import unittest
from unittest.mock import patch

from Evelyn.tools.backlog_drainer import (
    DrainConfig,
    drain_backlog,
    drain_backlog_async,
)


class TestBacklogDrainer(unittest.TestCase):
    """Hermetic unit tests for synchronous and asynchronous backlog drainers."""

    def test_sync_drain_full_exhaustion(self):
        """Verify normal sync draining until queue is exhausted."""
        items = [1, 2, 3, 4, 5]
        processed = []

        def fetch_fn(limit):
            batch = items[:limit]
            del items[:limit]
            return batch

        def process_fn(item):
            processed.append(item)

        cfg = DrainConfig(batch_size=2, manage_task_lifecycle=False)
        result = drain_backlog("test_task", fetch_fn, process_fn, config=cfg)

        self.assertTrue(result.exhausted)
        self.assertFalse(result.yielded)
        self.assertEqual(result.items_processed, 5)
        self.assertEqual(result.errors_count, 0)
        self.assertEqual(processed, [1, 2, 3, 4, 5])
        self.assertEqual(result.batches_completed, 3)

    def test_sync_drain_error_isolation(self):
        """Verify per-item failure isolation and dead-letter callback."""
        items = ["ok1", "fail", "ok2"]
        processed = []
        handled_errors = []

        def fetch_fn(limit):
            batch = items[:limit]
            del items[:limit]
            return batch

        def process_fn(item):
            if item == "fail":
                raise ValueError("Simulated item failure")
            processed.append(item)

        def error_handler(item, err):
            handled_errors.append((item, str(err)))

        cfg = DrainConfig(batch_size=10, manage_task_lifecycle=False)
        result = drain_backlog(
            "test_task",
            fetch_fn,
            process_fn,
            config=cfg,
            error_handler=error_handler,
        )

        self.assertEqual(result.items_processed, 2)
        self.assertEqual(result.errors_count, 1)
        self.assertEqual(processed, ["ok1", "ok2"])
        self.assertEqual(len(handled_errors), 1)
        self.assertEqual(handled_errors[0][0], "fail")
        self.assertIn("Simulated item failure", handled_errors[0][1])

    @patch("Evelyn.tools.task_manager.should_yield")
    @patch("Evelyn.tools.task_manager.enqueue_idle_task")
    def test_sync_drain_cooperative_yield(self, mock_enqueue, mock_yield):
        """Verify cooperative yield stops loop and auto-re-enqueues."""
        items = [1, 2, 3, 4, 5]
        processed = []

        # Yield on the second item (call 1: before batch, call 2: item 0, call 3: item 1)
        mock_yield.side_effect = [False, False, True]

        def fetch_fn(limit):
            batch = items[:limit]
            del items[:limit]
            return batch

        def process_fn(item):
            processed.append(item)

        cfg = DrainConfig(batch_size=5, manage_task_lifecycle=False, auto_re_enqueue=True)
        result = drain_backlog("test_task", fetch_fn, process_fn, config=cfg)

        self.assertTrue(result.yielded)
        self.assertEqual(result.items_processed, 1)
        mock_enqueue.assert_called_once_with("test_task")

    def test_sync_drain_deadline_exceeded(self):
        """Verify deadline timestamp halts processing cleanly."""
        items = [1, 2, 3, 4, 5]
        processed = []

        def fetch_fn(limit):
            batch = items[:limit]
            del items[:limit]
            return batch

        def process_fn(item):
            processed.append(item)
            time.sleep(0.05)

        # Set deadline 20ms in the future
        cfg = DrainConfig(
            batch_size=2,
            deadline=time.time() + 0.02,
            manage_task_lifecycle=False,
        )
        result = drain_backlog("test_task", fetch_fn, process_fn, config=cfg)

        self.assertTrue(result.deadline_exceeded)
        self.assertLess(result.items_processed, 5)

    def test_async_drain_full(self):
        """Verify async backlog drainer with coroutines."""
        items = [10, 20, 30]
        processed = []

        async def fetch_fn(limit):
            await asyncio.sleep(0.01)
            batch = items[:limit]
            del items[:limit]
            return batch

        async def process_fn(item):
            await asyncio.sleep(0.01)
            processed.append(item)

        async def run_test():
            cfg = DrainConfig(batch_size=2, manage_task_lifecycle=False)
            return await drain_backlog_async("async_test", fetch_fn, process_fn, config=cfg)

        result = asyncio.run(run_test())
        self.assertTrue(result.exhausted)
        self.assertEqual(result.items_processed, 3)
        self.assertEqual(processed, [10, 20, 30])


if __name__ == "__main__":
    unittest.main()
