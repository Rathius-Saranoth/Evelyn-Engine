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


def set_running(name: str) -> None:
    """Register a named task as running in the server's background task registry.

    Idempotent — calling when already registered updates `started_at` only if
    the task was not previously marked running, to avoid resetting a running timer.

    Args:
        name: The task key (e.g. "extractor", "consolidator").
    """
    tasks = _get_background_tasks()
    if tasks is None:
        return
    existing = tasks.get(name, {})
    if existing.get("status") != "running":
        tasks[name] = {
            "status": "running",
            "started_at": time.time(),
        }


def clear_running(name: str) -> None:
    """Remove a named task from the server's background task registry.

    Called when a task finishes (success, error, or cancellation). Removes
    the entry entirely rather than setting status="done" so that
    `is_any_running()` correctly returns False for the next caller.

    Args:
        name: The task key to remove.
    """
    tasks = _get_background_tasks()
    if tasks is None:
        return
    tasks.pop(name, None)


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
