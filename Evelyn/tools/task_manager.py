# task_manager.py
# date created: 2026-08-01
# date modified: 2026-08-15 11:54:52
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

# Default baseline timeouts (in seconds) used until historical data is collected.
DEFAULT_SOFT_TIMEOUTS = {
    "extractor": 1200.0,             # 20 minutes
    "consolidator": 2100.0,          # 35 minutes
    "procedure_consolidator": 900.0, # 15 minutes
    "profile_evolver": 900.0,        # 15 minutes
    "refresh_memory": 600.0,         # 10 minutes
    "vault_map": 600.0,              # 10 minutes
    "sync": 300.0,                   # 5 minutes
    "tag_librarian": 600.0,          # 10 minutes
}

# Active Python task/thread handles for handle-reconciliation
_active_handles: dict = {}
_watchdog_task = None


# ---------------------------------------------------------------------------
# SQLite Performance History Database Helpers
# ---------------------------------------------------------------------------


def _get_db_connection():
    """Return an open SQLite connection to evelyn_memory.db."""
    import sqlite3
    import os
    try:
        import evelyn_config as cfg
        db_path = getattr(cfg, "MEMORY_DB_PATH", r"/home/rathius/evelyn/data/evelyn_memory.db")
    except Exception:
        db_path = r"/home/rathius/evelyn/data/evelyn_memory.db"
    
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _init_history_db() -> None:
    """Ensure the heavy_task_history table exists in evelyn_memory.db."""
    try:
        conn = _get_db_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS heavy_task_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name TEXT NOT NULL,
                started_at REAL NOT NULL,
                finished_at REAL NOT NULL,
                elapsed_seconds REAL NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                items_processed INTEGER DEFAULT 0,
                timestamp REAL NOT NULL
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_heavy_task_name ON heavy_task_history(task_name);")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[TASK MANAGER] Warning: Could not initialize task history DB: {e}", flush=True)


def record_task_history(
    name: str,
    started_at: float,
    finished_at: float,
    elapsed_seconds: float,
    status: str,
    error: Optional[str] = None,
    items_processed: int = 0,
) -> None:
    """Record a completed task run in heavy_task_history SQLite table."""
    if not name or not started_at or not finished_at or elapsed_seconds is None:
        return
    try:
        _init_history_db()
        conn = _get_db_connection()
        conn.execute(
            """
            INSERT INTO heavy_task_history 
            (task_name, started_at, finished_at, elapsed_seconds, status, error, items_processed, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, started_at, finished_at, elapsed_seconds, status, error, items_processed, time.time()),
        )
        # Prune old records to keep table under 500 rows per task
        conn.execute(
            """
            DELETE FROM heavy_task_history 
            WHERE id IN (
                SELECT id FROM heavy_task_history 
                WHERE task_name = ? 
                ORDER BY id DESC LIMIT -1 OFFSET 500
            )
            """,
            (name,),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[TASK MANAGER] Error recording task history for {name}: {e}", flush=True)


def get_dynamic_timeout(name: str) -> float:
    """Calculate the soft maximum runtime timeout for a task using historical statistics (mean + 3 * std_dev).

    Args:
        name: The task key (e.g. 'extractor', 'profile_evolver').

    Returns:
        float: Soft timeout threshold in seconds.
    """
    baseline = DEFAULT_SOFT_TIMEOUTS.get(name, 1800.0)
    try:
        _init_history_db()
        conn = _get_db_connection()
        rows = conn.execute(
            """
            SELECT elapsed_seconds FROM heavy_task_history
            WHERE task_name = ? AND status IN ('idle', 'done', 'success')
            ORDER BY id DESC LIMIT 50
            """,
            (name,),
        ).fetchall()
        conn.close()

        if len(rows) >= 5:
            times = [r["elapsed_seconds"] for r in rows if r["elapsed_seconds"] > 0]
            if len(times) >= 5:
                import math
                mean = sum(times) / len(times)
                variance = sum((x - mean) ** 2 for x in times) / len(times)
                std_dev = math.sqrt(variance)
                dynamic_val = mean + (3.0 * std_dev)
                # Enforce baseline minimum so small averages don't cut off normal runs
                return max(baseline, dynamic_val)
    except Exception:
        pass

    return baseline



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


STATE_FILE = r"/home/rathius/evelyn/data/heavy_tasks_state.json"


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
            k: v for k, v in tasks.items()
            if not k.startswith("task_") and not k.startswith("test_")
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(persist_data, f, indent=2)
    except Exception as e:
        print(f"[TASK MANAGER] Error saving persistent state: {e}", flush=True)


def load_persistent_state() -> None:
    """Load persistent heavy task state from disk and SQLite history into server memory on startup."""
    import json
    import os
    tasks = _get_background_tasks()
    if tasks is None:
        return

    # 1. Load state file from disk if present
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key, val in data.items():
                    if isinstance(val, dict) and not key.startswith("test_"):
                        if val.get("status") in RUNNING_STATUSES or val.get("status") == "running":
                            val["status"] = "idle"
                        tasks[key] = val
                print(f"[TASK MANAGER] Restored heavy tasks state from disk ({len(tasks)} tasks).", flush=True)
        except Exception as e:
            print(f"[TASK MANAGER] Error loading persistent state: {e}", flush=True)

    # 2. Reconcile missing / uninitialized task records from SQLite heavy_task_history
    try:
        conn = _get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT task_name, MAX(finished_at) as last_fin, elapsed_seconds, status, error, items_processed
                FROM heavy_task_history
                WHERE task_name NOT LIKE 'test_%'
                GROUP BY task_name
            """)
            rows = cur.fetchall()
            for r in rows:
                t_name = r["task_name"]
                t_fin = r["last_fin"]
                if t_name not in tasks:
                    tasks[t_name] = {
                        "status": "idle" if r["status"] in RUNNING_STATUSES or r["status"] == "running" else r["status"],
                        "started_at": t_fin,
                        "finished_at": t_fin,
                        "last_run_at": t_fin,
                        "elapsed_seconds": r["elapsed_seconds"],
                        "error": r["error"],
                    }
                else:
                    if not tasks[t_name].get("last_run_at") and t_fin:
                        tasks[t_name]["last_run_at"] = t_fin
                    if not tasks[t_name].get("finished_at") and t_fin:
                        tasks[t_name]["finished_at"] = t_fin
                    if tasks[t_name].get("elapsed_seconds") is None and r["elapsed_seconds"] is not None:
                        tasks[t_name]["elapsed_seconds"] = r["elapsed_seconds"]
            save_persistent_state()
        finally:
            conn.close()
    except Exception as e:
        print(f"[TASK MANAGER] Error reconciling history from DB: {e}", flush=True)


def get_last_run_ts(name: str, default: float = 0.0) -> float:
    """Return the last_run_at timestamp for a named heavy task from memory, disk, or SQLite history.

    Args:
        name: Task key (e.g. "consolidator", "extractor", "procedure_consolidator", "profile_evolver").
        default: Default float timestamp to return if no record exists.

    Returns:
        float: Timestamp of the last run, or `default`.
    """
    import json
    import os
    tasks = _get_background_tasks()
    if tasks and name in tasks:
        ts = tasks[name].get("last_run_at")
        if isinstance(ts, (int, float)) and ts > 0:
            return float(ts)

    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and name in data:
                ts = data[name].get("last_run_at")
                if isinstance(ts, (int, float)) and ts > 0:
                    return float(ts)
        except Exception:
            pass

    try:
        conn = _get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT MAX(finished_at) FROM heavy_task_history WHERE task_name = ?",
                (name,)
            )
            row = cur.fetchone()
            if row and row[0] and row[0] > 0:
                return float(row[0])
        finally:
            conn.close()
    except Exception:
        pass

    return default


def save_last_run_ts(name: str, ts: Optional[float] = None) -> float:
    """Save and update the last_run_at timestamp for a named heavy task.

    Args:
        name: Task key.
        ts: Float timestamp (defaults to current time if None).

    Returns:
        float: The timestamp float that was saved.
    """
    import json
    import os
    now = ts if ts is not None else time.time()
    tasks = _get_background_tasks()
    if tasks is not None:
        if name not in tasks or not isinstance(tasks.get(name), dict):
            tasks[name] = {"status": "idle"}
        tasks[name]["last_run_at"] = now
        save_persistent_state()
    else:
        # Standalone / test mode without server module imported
        data = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        if name not in data or not isinstance(data[name], dict):
            data[name] = {"status": "idle"}
        data[name]["last_run_at"] = now
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[TASK MANAGER] Error saving persistent state: {e}", flush=True)

    return now


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


def set_running(
    name: str,
    phase: Optional[str] = None,
    sub_status: Optional[dict] = None,
    diagnostics: Optional[dict] = None,
    task_obj: Optional[object] = None,
) -> None:
    """Register a named task as running in the server's background task registry.

    Idempotent — calling when already registered updates phase and preserves
    `started_at` to avoid resetting a running timer.

    Args:
        name: The task key (e.g. "extractor", "consolidator").
        phase: Optional phase description string.
        sub_status: Optional dictionary with task-specific sub-status metrics.
        diagnostics: Optional diagnostic details dictionary.
        task_obj: Optional asyncio.Task or threading.Thread reference for auto-reconciliation.
    """
    global _active_handles
    if task_obj is not None:
        _active_handles[name] = task_obj
    else:
        # Fallback to current asyncio task if available
        try:
            import asyncio
            current = asyncio.current_task()
            if current:
                _active_handles[name] = current
        except Exception:
            pass

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
        "sub_status": sub_status if sub_status is not None else existing.get("sub_status"),
        "diagnostics": diagnostics if diagnostics is not None else existing.get("diagnostics"),
        "summary": existing.get("summary"),
        "error": None,
    }
    save_persistent_state()


def clear_running(
    name: str,
    status: str = "idle",
    error: Optional[str] = None,
    summary: Optional[str] = None,
    sub_status: Optional[dict] = None,
    diagnostics: Optional[dict] = None,
    items_processed: int = 0,
) -> None:
    """Mark a named task as completed/cancelled/idle in the background task registry.

    Preserves started_at, finished_at, last_run_at, elapsed_seconds, error info,
    and diagnostic payloads so UI monitors can render completion status and last run summaries.
    Also logs runtime metrics to heavy_task_history SQLite database table.

    Args:
        name: The task key to update.
        status: The completion status ("idle", "done", "cancelled", "error").
        error: Optional error message string.
        summary: Optional completion summary text.
        sub_status: Optional task-specific sub-status metrics dictionary.
        diagnostics: Optional diagnostic details dictionary.
        items_processed: Optional count of items processed in this run.
    """
    global _active_handles
    _active_handles.pop(name, None)

    tasks = _get_background_tasks()
    if tasks is None:
        save_last_run_ts(name)
        return
    now = time.time()
    existing = tasks.get(name, {})
    started_at = existing.get("started_at")
    elapsed = round(now - started_at, 1) if started_at else None

    # Sanitize and normalize error string
    clean_error = None
    raw_error = error if error is not None else (existing.get("error") if status == "error" else None)
    if raw_error:
        s = str(raw_error).rstrip(": ").strip()
        if s:
            clean_error = s

    # Record history to SQLite DB if valid duration
    if started_at and elapsed is not None:
        record_task_history(
            name=name,
            started_at=started_at,
            finished_at=now,
            elapsed_seconds=elapsed,
            status=status,
            error=clean_error,
            items_processed=items_processed,
        )

    tasks[name] = {
        "status": status,
        "started_at": started_at,
        "finished_at": now,
        "last_run_at": now,
        "elapsed_seconds": elapsed,
        "error": clean_error,
        "phase": None,
        "summary": summary or existing.get("summary"),
        "sub_status": sub_status if sub_status is not None else existing.get("sub_status"),
        "diagnostics": diagnostics if diagnostics is not None else existing.get("diagnostics"),
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


# ---------------------------------------------------------------------------
# Task Watchdog & Handle Auto-Reconciliation
# ---------------------------------------------------------------------------


def _reconcile_orphaned_tasks() -> None:
    """Check active task handles; if the backing task/thread is finished, clear status."""
    import asyncio
    import threading

    tasks = _get_background_tasks()
    if not tasks:
        return

    to_clear = []
    for name, handle in list(_active_handles.items()):
        current_status = tasks.get(name, {}).get("status")
        if current_status not in RUNNING_STATUSES and current_status != "running":
            _active_handles.pop(name, None)
            continue

        is_done = False
        if isinstance(handle, asyncio.Task):
            is_done = handle.done()
        elif isinstance(handle, threading.Thread):
            is_done = not handle.is_alive()

        if is_done:
            print(
                f"[TASK WATCHDOG] Auto-reconciling completed handle for task '{name}' "
                f"(status was stuck on '{current_status}'). Clearing to idle.",
                flush=True,
            )
            to_clear.append(name)

    for name in to_clear:
        clear_running(name, status="idle", summary="Auto-reconciled by task watchdog")


def _check_soft_timeouts() -> None:
    """Check running tasks against dynamic soft-timeout thresholds."""
    import asyncio

    tasks = _get_background_tasks()
    if not tasks:
        return

    now = time.time()
    for name, task_info in list(tasks.items()):
        if task_info.get("status") not in RUNNING_STATUSES and task_info.get("status") != "running":
            continue

        started_at = task_info.get("started_at")
        if not started_at:
            continue

        elapsed = now - started_at
        threshold = get_dynamic_timeout(name)

        if elapsed > threshold:
            print(
                f"[TASK WATCHDOG WARNING] Task '{name}' running for {elapsed:.1f}s "
                f"exceeds dynamic threshold ({threshold:.1f}s). Triggering soft cancellation.",
                flush=True,
            )

            # Issue gentle cancellation to the backing handle if available
            handle = _active_handles.get(name)
            if isinstance(handle, asyncio.Task) and not handle.done():
                handle.cancel()

            # Clear status in registry
            clear_running(
                name,
                status="timed_out",
                error=f"Soft timeout exceeded ({round(elapsed)}s > threshold {round(threshold)}s)",
            )


async def start_watchdog(interval: float = 30.0) -> None:
    """Start the periodic background task watchdog loop if not already running.

    Args:
        interval: Polling interval in seconds (defaults to 30.0).
    """
    global _watchdog_task
    import asyncio

    if _watchdog_task and not _watchdog_task.done():
        return

    async def _loop():
        print(f"[TASK WATCHDOG] Started background monitoring loop (interval={interval}s).", flush=True)
        while True:
            await asyncio.sleep(interval)
            try:
                _reconcile_orphaned_tasks()
                _check_soft_timeouts()
            except Exception as e:
                print(f"[TASK WATCHDOG ERROR] {e}", flush=True)

    _watchdog_task = asyncio.create_task(_loop())

