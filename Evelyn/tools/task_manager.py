# task_manager.py
# date created: 2026-08-01
# date modified: 2026-08-01 09:20:18
# tags: #tasks, #concurrency, #mutual_exclusion, #background

"""task_manager.py — Centralized registry and mutual-exclusion layer for all heavy background tasks.

Replaces the 4 separate, drift-prone copies of `_heavy_tasks_running()` that existed
across `fact_extractor`, `fact_consolidator`, `profile_evolver`, and `evelyn_server`.

All heavy tasks (extractor, consolidator, procedure_consolidator, profile_evolver,
refresh_memory, sync, vault_map, and research subprocesses) report their running
state through this module. `is_any_running()` is the single canonical source of
truth for mutual exclusion.

Design notes:
- No classes, no dataclasses, no abstractions beyond plain dicts. Cheap to import.
- Thread-safe reads via `sys.modules` reference to the server's `_background_tasks`
  dict, which is already accessed this way by all existing modules.
- The module-level boolean flags (_extracting, _consolidating, etc.) in each
  worker module are preserved as a second layer of protection — this module is
  an additional layer above them, not a replacement.

Exports:
    is_any_running(exclude)   — True if any heavy task is currently running.
    set_running(name)         — Mark a task as running in the server registry.
    clear_running(name)       — Remove a task from the running set.
    get_status(name)          — Return current status string for a named task.
"""

import sys
import time
from typing import Optional


# ---------------------------------------------------------------------------
# Heavy task key definitions
# ---------------------------------------------------------------------------

# All known heavy-task keys. Used for documentation and validation only —
# no runtime enforcement, so adding a new task requires no change here.
HEAVY_TASK_KEYS = frozenset({
    "extractor",
    "consolidator",
    "procedure_consolidator",
    "profile_evolver",
    "refresh_memory",
    "sync",
    "vault_map",
    # Research subprocess tasks are keyed as "task_<id>" — handled by prefix check.
})

# Statuses that indicate a task is actively consuming resources.
RUNNING_STATUSES = frozenset({"running", "searching", "synthesizing"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_background_tasks() -> Optional[dict]:
    """Return the server's _background_tasks dict, or None if not importable.

    Walks the two module name candidates the existing codebase already uses,
    so this works whether the server is the main module or an imported one.

    Returns:
        Optional[dict]: The _background_tasks dict, or None.
    """
    for mod_name in ("evelyn_server", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod:
            tasks = getattr(mod, "_background_tasks", None)
            if isinstance(tasks, dict):
                return tasks
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


STATE_FILE = r"C:\Projects\LocalAI\data\heavy_tasks_state.json"


def save_persistent_state() -> None:
    """Save non-research heavy background task states to disk."""
    import json
    import os
    tasks = _get_background_tasks()
    if not tasks:
        return
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        persist_data = {
            k: v for k, v in tasks.items() if not k.startswith("task_")
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(persist_data, f, indent=2)
    except Exception as e:
        print(f"[TASK MANAGER] Error saving persistent state: {e}", flush=True)


def load_persistent_state() -> None:
    """Load persistent heavy task state from disk into server memory on startup."""
    import json
    import os
    tasks = _get_background_tasks()
    if tasks is None:
        return
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key, val in data.items():
                if isinstance(val, dict):
                    if val.get("status") in RUNNING_STATUSES or val.get("status") == "running":
                        val["status"] = "idle"
                    tasks[key] = val
            print(f"[TASK MANAGER] Restored heavy tasks state from disk ({len(data)} tasks).", flush=True)
    except Exception as e:
        print(f"[TASK MANAGER] Error loading persistent state: {e}", flush=True)


def is_any_running(exclude: str = None) -> bool:
    """Return True if any heavy background task is currently running.

    Drop-in canonical replacement for all copies of `_heavy_tasks_running()`
    and `_other_heavy_tasks_running()` in the codebase. Each caller passes
    its own task name as `exclude` so a task doesn't block itself.

    Args:
        exclude: Optional task key to exclude from the check. Research tasks
            use their full "task_<id>" key; named tasks use their fixed key.

    Returns:
        bool: True if any other heavy task is actively running.
    """
    tasks = _get_background_tasks()
    if not tasks:
        return False

    for key, task in tasks.items():
        if exclude and key == exclude:
            continue
        status = task.get("status")
        if key.startswith("task_"):
            # Research subprocess — uses richer status vocabulary
            if status in RUNNING_STATUSES:
                return True
        else:
            if status == "running":
                return True

    return False


def set_running(name: str, phase: Optional[str] = None) -> None:
    """Register a named task as running in the server's background task registry.

    Idempotent — calling when already registered updates phase and preserves
    `started_at` to avoid resetting a running timer.

    Args:
        name: The task key (e.g. "extractor", "consolidator").
        phase: Optional phase description string.
    """
    tasks = _get_background_tasks()
    if tasks is None:
        return
    existing = tasks.get(name, {})
    now = time.time()
    started_at = existing.get("started_at") if existing.get("status") == "running" else now
    last_run_at = existing.get("last_run_at")
    
    tasks[name] = {
        "status": "running",
        "started_at": started_at,
        "last_run_at": last_run_at,
        "phase": phase or existing.get("phase"),
        "error": None,
    }
    save_persistent_state()


def clear_running(name: str, status: str = "idle", error: Optional[str] = None) -> None:
    """Mark a named task as completed/cancelled/idle in the background task registry.

    Preserves started_at, finished_at, last_run_at, elapsed_seconds, and error info
    so UI monitors can render completion status and last run timestamps.
    `is_any_running()` evaluates only active RUNNING_STATUSES, so preserving
    completed task metadata does not block other heavy tasks.

    Args:
        name: The task key to update.
        status: The completion status ("idle", "done", "cancelled", "error").
        error: Optional error message string.
    """
    tasks = _get_background_tasks()
    if tasks is None:
        return
    now = time.time()
    existing = tasks.get(name, {})
    started_at = existing.get("started_at")
    elapsed = round(now - started_at, 1) if started_at else None
    
    tasks[name] = {
        "status": status,
        "started_at": started_at,
        "finished_at": now,
        "last_run_at": now,
        "elapsed_seconds": elapsed,
        "error": error or existing.get("error"),
        "phase": existing.get("phase"),
    }
    save_persistent_state()


def get_status(name: str) -> Optional[str]:
    """Return the current status string for a named task, or None if not registered.

    Args:
        name: The task key.

    Returns:
        Optional[str]: Status string, or None if not found.
    """
    tasks = _get_background_tasks()
    if tasks is None:
        return None
    return tasks.get(name, {}).get("status")
