import json
import os
import shutil
import tempfile

import pytest

import evelyn_config as cfg
from Evelyn.tools.evelyn_tools import (
    guide_research,
    inspect_research_task,
    list_research_tasks,
)
from evelyn_server import get_research_context


@pytest.fixture
def temp_research_env(monkeypatch):
    tmp_dir = tempfile.mkdtemp()
    res_dir = os.path.join(tmp_dir, "data", "research")
    os.makedirs(res_dir, exist_ok=True)
    monkeypatch.setattr(cfg, "RESEARCH_DATA_DIR", res_dir)

    yield res_dir

    shutil.rmtree(tmp_dir, ignore_errors=True)


def _create_mock_task(res_dir, task_id, query, status="running", struggling=False, sq_status="pending", summary_text=None):
    tdir = os.path.join(res_dir, task_id)
    os.makedirs(tdir, exist_ok=True)
    state = {
        "task_id": task_id,
        "query": query,
        "original_question": query,
        "status": status,
        "scope": "standard",
        "confidence": 75 if status == "done" else 40,
        "current_step": "search",
        "struggling": struggling,
        "current_sq_idx": 0,
        "plan": {
            "sub_questions": [
                {
                    "id": "sq_01",
                    "question": f"Sub-question 1 for {query}",
                    "search_query": "Sub-question 1 query",
                    "status": sq_status,
                    "confidence": 30,
                    "source_count": 3,
                    "search_depth": 1,
                    "gaps": ["Gap 1"] if sq_status == "needs_guidance" else [],
                }
            ]
        },
        "sources_registry": [
            {
                "id": "src_001",
                "title": f"Source 1 for {query}",
                "url": "https://example.com/source1",
                "timestamp": "2026-08-28T07:00:00",
            }
        ],
    }
    with open(os.path.join(tdir, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

    if summary_text:
        with open(os.path.join(tdir, "sq_01_summary.md"), "w", encoding="utf-8") as f:
            f.write(summary_text)

    return state


def test_list_research_tasks_empty(temp_research_env):
    res = list_research_tasks()
    assert "No research tasks found" in res


def test_list_research_tasks_filtering(temp_research_env):
    _create_mock_task(temp_research_env, "task_01", "Heart rate 5-second sampling", status="running", struggling=True, sq_status="needs_guidance")
    _create_mock_task(temp_research_env, "task_02", "Storytelling narrative flow", status="done")
    _create_mock_task(temp_research_env, "task_03", "Sleep tracking analysis", status="running", struggling=False, sq_status="pending")

    all_res = list_research_tasks(status_filter="all")
    assert "task_01" in all_res
    assert "task_02" in all_res
    assert "task_03" in all_res
    assert "NEEDS GUIDANCE" in all_res
    assert "COMPLETED" in all_res

    stalled_res = list_research_tasks(status_filter="stalled")
    assert "task_01" in stalled_res
    assert "task_02" not in stalled_res
    assert "task_03" not in stalled_res

    done_res = list_research_tasks(status_filter="done")
    assert "task_02" in done_res
    assert "task_01" not in done_res


def test_inspect_research_task(temp_research_env):
    _create_mock_task(
        temp_research_env,
        "task_12345_abc",
        "High-frequency heart rate sampling",
        status="running",
        struggling=True,
        sq_status="needs_guidance",
        summary_text="### Key Physiological Findings\n- 5-second PPG intervals capture rapid RSA oscillations.",
    )

    # Inspect by task_id
    res = inspect_research_task(task_id="task_12345_abc")
    assert "task_12345_abc" in res
    assert "High-frequency heart rate sampling" in res
    assert "Sub-question 1 for High-frequency heart rate sampling" in res
    assert "Key Physiological Findings" in res
    assert "Sources Registry" not in res  # Sources omitted by default to save tokens

    # Inspect with include_sources=True
    res_sources = inspect_research_task(task_id="task_12345_abc", include_sources=True)
    assert "Sources Registry" in res_sources
    assert "https://example.com/source1" in res_sources

    # Inspect by topic query fuzzy match
    res_query = inspect_research_task(query="heart rate")
    assert "task_12345_abc" in res_query
    assert "Key Physiological Findings" in res_query


def test_guide_research_fuzzy_and_auto_resolution(temp_research_env, monkeypatch):
    _create_mock_task(
        temp_research_env,
        "task_stalled_01",
        "High-frequency heart rate sampling",
        status="running",
        struggling=True,
        sq_status="needs_guidance",
    )

    # Mock resume_research_task so it doesn't launch background subprocess in unit tests
    monkeypatch.setattr("Evelyn.tools.evelyn_tools.resume_research_task", lambda tid: "Mock resumed.")

    # 1. Guide by query topic without providing task_id
    res = guide_research(query="heart rate", guidance="Focus on polar H10 ECG sampling rate")
    assert "Guidance injected into sub-question" in res
    assert "task_stalled_01" in res

    # Verify gaps file was updated
    gaps_file = os.path.join(temp_research_env, "task_stalled_01", "sq_01_gaps.json")
    assert os.path.exists(gaps_file)
    with open(gaps_file, encoding="utf-8") as f:
        gaps_data = json.load(f)
    assert any("Focus on polar H10" in g for g in gaps_data["gaps"])


def test_get_research_context_struggling_detection(temp_research_env):
    _create_mock_task(
        temp_research_env,
        "task_hr_01",
        "Comparison of high-frequency heart rate sampling",
        status="running",
        struggling=True,
        sq_status="needs_guidance",
    )

    ctx = get_research_context()
    assert "=== STALLED / QUARANTINED RESEARCH TASKS ===" in ctx
    assert "task_hr_01" in ctx
    assert "Comparison of high-frequency heart rate sampling" in ctx
    assert "Stuck on Sub-Question" in ctx
