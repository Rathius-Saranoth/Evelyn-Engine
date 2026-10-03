# test_benchmark_store.py
# date created: 2026-10-02 19:55:00
# date modified: 2026-10-02 19:59:59
# tags: #test, #benchmark, #diff, #storage

"""Targeted unit tests for Evelyn/tools/benchmark_store.py rolling history and diff engine."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from Evelyn.tools import benchmark_store


class TestBenchmarkStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.history_file = os.path.join(self.temp_dir.name, "benchmark_history.json")
        self.patcher = patch.object(benchmark_store, "HISTORY_FILE", self.history_file)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def test_save_and_retrieve_snapshots(self):
        tools = [{"type": "function", "function": {"name": "test_tool", "description": "desc 1"}}]
        summary = {"passed": 24, "total": 25, "avg_tps": 50.0, "cold_load": 7.0}
        results = [{"id": "case1", "category": "cat1", "passed": True}]

        snap = benchmark_store.save_run_snapshot(
            model="gemma4:12b",
            prompt_mode="template",
            prompt_text="Template system prompt v1",
            tools=tools,
            summary=summary,
            results=results,
            max_capacity=5,
        )

        self.assertTrue(snap["run_id"].startswith("template_"))
        self.assertEqual(snap["model"], "gemma4:12b")

        history = benchmark_store.list_history(partition="template")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["run_id"], snap["run_id"])
        # In compact mode, raw prompt_text is omitted
        self.assertNotIn("prompt_text", history[0])

        full_run = benchmark_store.get_run(snap["run_id"])
        self.assertIsNotNone(full_run)
        assert full_run is not None
        self.assertEqual(full_run["prompt_text"], "Template system prompt v1")

    def test_ring_buffer_capacity_enforcement(self):
        tools = []
        summary = {"passed": 20, "total": 25}
        results = []

        # Save 6 snapshots with max_capacity=3
        for i in range(6):
            benchmark_store.save_run_snapshot(
                model="gemma4:12b",
                prompt_mode="live",
                prompt_text=f"Live prompt version {i}",
                tools=tools,
                summary=summary,
                results=results,
                max_capacity=3,
            )

        live_history = benchmark_store.list_history(partition="live")
        self.assertEqual(len(live_history), 3)
        # Newest should be version 5, then 4, then 3
        run_5 = benchmark_store.get_run(live_history[0]["run_id"])
        assert run_5 is not None
        self.assertEqual(run_5["prompt_text"], "Live prompt version 5")

    def test_compute_run_diff_identifies_deltas(self):
        tools_a = [{"type": "function", "function": {"name": "test_tool", "description": "old description"}}]
        tools_b = [
            {"type": "function", "function": {"name": "test_tool", "description": "new description"}},
            {"type": "function", "function": {"name": "added_tool", "description": "new tool"}},
        ]

        results_a = [
            {"id": "case1", "category": "cat1", "passed": True, "called": ["test_tool"], "reply": "ok"},
            {"id": "case2", "category": "cat2", "passed": False, "called": [], "reply": "err"},
        ]
        results_b = [
            {"id": "case1", "category": "cat1", "passed": False, "called": [], "reply": "flipped"},
            {"id": "case2", "category": "cat2", "passed": True, "called": ["test_tool"], "reply": "fixed"},
        ]

        run_a = benchmark_store.save_run_snapshot(
            model="gemma4:12b",
            prompt_mode="template",
            prompt_text="Line 1: baseline\nLine 2: common",
            tools=tools_a,
            summary={"passed": 20, "total": 25, "avg_tps": 50.0, "cold_load": 5.0},
            results=results_a,
        )

        run_b = benchmark_store.save_run_snapshot(
            model="gemma4:12b",
            prompt_mode="live",
            prompt_text="Line 1: modified\nLine 2: common",
            tools=tools_b,
            summary={"passed": 22, "total": 25, "avg_tps": 55.0, "cold_load": 3.0},
            results=results_b,
        )

        diff = benchmark_store.compute_run_diff(run_a["run_id"], run_b["run_id"])

        self.assertFalse(diff["prompt_identical"])
        self.assertEqual(diff["metrics_delta"]["score_delta"], 2)
        self.assertEqual(diff["metrics_delta"]["tps_delta"], 5.0)

        # Tool diffs
        self.assertEqual(len(diff["tool_diffs"]), 2)
        tool_names = {t["tool"] for t in diff["tool_diffs"]}
        self.assertIn("added_tool", tool_names)
        self.assertIn("test_tool", tool_names)

        # Case divergences: both case1 and case2 flipped
        self.assertEqual(len(diff["divergences"]), 2)
        div_ids = {d["id"] for d in diff["divergences"]}
        self.assertEqual(div_ids, {"case1", "case2"})

    def test_probe_runs_isolated_from_full_suite(self):
        # Save a full run
        benchmark_store.save_run_snapshot(
            model="gemma4:12b",
            prompt_mode="live",
            prompt_text="Full live prompt",
            tools=[],
            summary={"passed": 24, "total": 25, "avg_tps": 50.0},
            results=[{"id": "case1", "category": "cat1", "passed": True}],
            run_type="full",
        )

        # Save a single-category probe run
        probe = benchmark_store.save_run_snapshot(
            model="gemma4:12b",
            prompt_mode="live",
            prompt_text="Full live prompt",
            tools=[],
            summary={"passed": 1, "total": 1, "avg_tps": 52.0},
            results=[{"id": "proactivity_1", "category": "proactivity", "passed": True}],
            run_type="probe",
            category="proactivity",
        )

        self.assertTrue(probe["run_id"].startswith("probe_"))
        self.assertEqual(probe["run_type"], "probe")
        self.assertEqual(probe["category"], "proactivity")

        # Standard list_history excludes probes by default
        default_history = benchmark_store.list_history()
        self.assertEqual(len(default_history), 1)
        self.assertFalse(default_history[0]["run_id"].startswith("probe_"))

        # Explicit probe query returns probes
        probe_history = benchmark_store.list_history(partition="probes")
        self.assertEqual(len(probe_history), 1)
        self.assertEqual(probe_history[0]["run_id"], probe["run_id"])


if __name__ == "__main__":
    unittest.main()
