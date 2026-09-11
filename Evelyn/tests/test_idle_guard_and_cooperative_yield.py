# test_idle_guard_and_cooperative_yield.py
# Unit tests for Stale Idle Clock Guard, Subprocess isolation, and Cooperative Yielding.

import asyncio
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import evelyn_server
from Evelyn.tools import backlog_drainer, task_manager


class TestIdleGuardAndYield(unittest.TestCase):
    def setUp(self):
        # Reset server idle guard state
        evelyn_server._server_boot_ts = time.time()
        evelyn_server._has_received_interactive_ping = False
        evelyn_server._last_activity_ts = time.time()

    @patch("Evelyn.tools.time_manager.get_user_idle_seconds")
    def test_stale_idle_clock_guard_on_boot(self, mock_get_idle):
        """Verify _get_current_idle_seconds() is capped by uptime on boot until interactive ping."""
        # Simulate 24 hours of silence in database
        mock_get_idle.return_value = 86400.0

        # Boot was just 0.1s ago; idle time should be capped by uptime (~0.1s), NOT 86400s
        idle_boot = evelyn_server._get_current_idle_seconds()
        self.assertLess(idle_boot, 2.0)
        self.assertGreaterEqual(idle_boot, 0.0)

        # After interactive ping, idle seconds reflects db idle time
        evelyn_server.record_interactive_ping()
        self.assertTrue(evelyn_server._has_received_interactive_ping)
        idle_after_ping = evelyn_server._get_current_idle_seconds()
        self.assertEqual(idle_after_ping, 86400.0)

    @patch("Evelyn.tools.time_manager.get_user_idle_seconds")
    def test_max_idle_seconds_ceiling(self, mock_get_idle):
        """Verify MAX_IDLE_SECONDS_CEILING prevents sentinel values like 999999.0."""
        mock_get_idle.return_value = 999999.0
        evelyn_server.record_interactive_ping()

        with patch.object(evelyn_server.cfg, "MAX_IDLE_SECONDS_CEILING", 3600.0, create=True):
            idle = evelyn_server._get_current_idle_seconds()
            self.assertEqual(idle, 3600.0)

    def test_backlog_drainer_inter_item_delay_async(self):
        """Verify drain_backlog_async inserts delay_between_items between items."""
        items = ["doc1", "doc2"]
        processed = []

        async def fetch_fn(limit):
            batch = items[:limit]
            del items[:limit]
            return batch

        async def process_fn(item):
            processed.append(item)

        async def run():
            cfg = backlog_drainer.DrainConfig(
                batch_size=2,
                delay_between_items=0.05,
                manage_task_lifecycle=False,
            )
            t0 = time.perf_counter()
            res = await backlog_drainer.drain_backlog_async("test_async_delay", fetch_fn, process_fn, config=cfg)
            elapsed = time.perf_counter() - t0
            return res, elapsed

        result, elapsed = asyncio.run(run())
        self.assertEqual(result.items_processed, 2)
        self.assertEqual(processed, ["doc1", "doc2"])
        # With 2 items and 0.05s delay after each, elapsed time should be >= 0.08s
        self.assertGreaterEqual(elapsed, 0.08)

    def test_asyncio_process_termination_in_task_manager(self):
        """Verify terminate_task_subprocess handles asyncio process-like objects cleanly."""
        mock_proc = MagicMock()
        mock_proc.pid = os.getpid()  # Real PID for exists check
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.wait = MagicMock()

        task_manager.register_subprocess(mock_proc)
        task_manager._active_handles["test_subp_task"] = mock_proc

        # Call terminate_task_subprocess with a mock where PID won't actually be killed
        with patch("psutil.Process") as mock_psutil_proc:
            mock_inst = MagicMock()
            mock_inst.is_running.return_value = True
            mock_psutil_proc.return_value = mock_inst

            task_manager.terminate_task_subprocess("test_subp_task", grace_period=0.1)
            mock_inst.terminate.assert_called_once()
            self.assertNotIn("test_subp_task", task_manager._active_handles)
            self.assertNotIn(mock_proc, task_manager._spawned_subprocesses)


if __name__ == "__main__":
    unittest.main()
