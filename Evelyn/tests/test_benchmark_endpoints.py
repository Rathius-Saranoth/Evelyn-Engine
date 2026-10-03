# test_benchmark_endpoints.py
# date created: 2026-10-02 20:05:00
# date modified: 2026-10-02 19:59:59
# tags: #test, #benchmark, #api, #endpoints

"""Targeted integration tests for Evelyn continuous evaluation benchmark API endpoints."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

from fastapi.testclient import TestClient

import evelyn_config as cfg
from Evelyn.tools import benchmark_store
from evelyn_server import app


def _get_auth_headers():
    return {"X-Evelyn-Key": cfg.API_KEY} if cfg.API_KEY else {}


def test_benchmark_matrix_endpoint():
    client = TestClient(app)
    response = client.get("/api/benchmark/matrix", headers=_get_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert "summaries" in data
    assert "details" in data
    assert len(data["summaries"]) >= 1


def test_benchmark_history_and_diff_endpoints():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_history_file = os.path.join(tmp_dir, "benchmark_history.json")
        with patch.object(benchmark_store, "HISTORY_FILE", tmp_history_file):
            client = TestClient(app)

            # Initially empty
            res = client.get("/api/benchmark/history", headers=_get_auth_headers())
            assert res.status_code == 200
            assert res.json() == []

            # Save two test runs into history store
            run1 = benchmark_store.save_run_snapshot(
                model="gemma4:12b",
                prompt_mode="template",
                prompt_text="Line 1\nLine 2",
                tools=[],
                summary={"passed": 24, "total": 25, "avg_tps": 50.0, "cold_load": 7.0},
                results=[{"id": "case1", "category": "cat1", "passed": True}],
            )
            run2 = benchmark_store.save_run_snapshot(
                model="gemma4:12b",
                prompt_mode="live",
                prompt_text="Line 1 modified\nLine 2",
                tools=[{"type": "function", "function": {"name": "test_tool", "description": "desc"}}],
                summary={"passed": 25, "total": 25, "avg_tps": 55.0, "cold_load": 5.0},
                results=[{"id": "case1", "category": "cat1", "passed": True}],
            )

            # Query history list
            res = client.get("/api/benchmark/history", headers=_get_auth_headers())
            assert res.status_code == 200
            history = res.json()
            assert len(history) == 2

            # Query single run
            res_run = client.get(f"/api/benchmark/run/{run1['run_id']}", headers=_get_auth_headers())
            assert res_run.status_code == 200
            run_data = res_run.json()
            assert run_data["run_id"] == run1["run_id"]
            assert "prompt_text" in run_data

            # Query diff between run1 and run2
            res_diff = client.get(
                f"/api/benchmark/diff?run_a={run1['run_id']}&run_b={run2['run_id']}",
                headers=_get_auth_headers(),
            )
            assert res_diff.status_code == 200
            diff_data = res_diff.json()
            assert diff_data["prompt_identical"] is False
            assert diff_data["metrics_delta"]["score_delta"] == 1
            assert len(diff_data["tool_diffs"]) == 1


def test_benchmark_status_endpoint():
    client = TestClient(app)
    response = client.get("/api/benchmark/status", headers=_get_auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert "running" in data
    assert "queued" in data
    assert "state" in data


def test_benchmark_run_enqueues_when_heavy_task_running():
    from Evelyn.tools import task_manager

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_queue_file = os.path.join(tmp_dir, "test_task_queue.json")
        orig_queue = list(task_manager._idle_queue)
        try:
            task_manager._idle_queue = []
            with (
                patch.object(task_manager, "QUEUE_STATE_FILE", tmp_queue_file),
                patch("evelyn_server.is_any_heavy_task_running", return_value=True),
            ):
                client = TestClient(app)
                res = client.post(
                    "/api/benchmark/run",
                    json={"model": "gemma4:12b", "prompt_mode": "live", "routed": True, "category": "pushback"},
                    headers=_get_auth_headers(),
                )
                assert res.status_code == 200
                data = res.json()
                assert data["status"] == "enqueued"
                assert data["category"] == "pushback"
                assert "waiting_for" in data

                # Verify task is queued in task_manager
                assert task_manager.is_task_queued("benchmark")
        finally:
            task_manager._idle_queue = orig_queue
