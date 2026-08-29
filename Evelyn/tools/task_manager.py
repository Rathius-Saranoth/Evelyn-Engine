# task_manager.py
# date created: 2026-08-01
# date modified: 2026-08-28 21:01:56
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
    terminate_task_subprocess(name) — Forcefully terminate subprocess and clean PID locks for a task.
"""

import contextlib
import json
import math
import os
import sqlite3
import subprocess
import sys
import time

import psutil

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
    "tag_librarian",
    # Research subprocess tasks are keyed as "task_<id>" — handled by prefix check.
})

# Statuses that indicate a task is actively consuming resources.
RUNNING_STATUSES = frozenset({"running", "searching", "synthesizing"})

# Default baseline timeouts (in seconds) used until historical data is collected.
DEFAULT_SOFT_TIMEOUTS = {
    "extractor": 1200.0,             # 20 minutes
    "consolidator": 2100.0,          # 35 minutes
    "procedure_consolidator": 900.0, # 15 minutes
    "profile_evolver": 4500.0,        # 75 minutes (up to 3 documents * 25m per doc ceiling)
    "refresh_memory": 1800.0,        # 30 minutes
    "vault_map": 600.0,              # 10 minutes
    "sync": 1800.0,                  # 30 minutes
    "tag_librarian": 600.0,          # 10 minutes
    "research_quick": 2400.0,        # 40 minutes (wall_clock is 1800s + grace buffer)
    "research_standard": 9000.0,     # 2.5 hours (wall_clock is 7200s + grace buffer)
    "research_deep": 32400.0,        # 9 hours (wall_clock is 28800s + grace buffer)
    "research": 9000.0,              # 2.5 hours default research fallback
}

# Active Python task/thread handles and subprocesses for lifecycle tracking
_active_handles: dict = {}
_spawned_subprocesses: list = []
_watchdog_task = None


def register_subprocess(proc: subprocess.Popen) -> None:
    """Track a spawned subprocess for graceful teardown upon server shutdown."""
    if proc not in _spawned_subprocesses:
        _spawned_subprocesses.append(proc)


def unregister_subprocess(proc: subprocess.Popen) -> None:
    """Remove a finished subprocess from the tracking registry."""
    if proc in _spawned_subprocesses:
        _spawned_subprocesses.remove(proc)


def terminate_task_subprocess(name: str, grace_period: float = 2.0) -> None:
    """Immediately terminate any active subprocess associated with a named task (e.g. task_* research).

    Performs a defense-in-depth teardown:
      1. Cancels/terminates in-memory process handle from _active_handles or server's _active_research_processes.
      2. Calls evelyn_server.terminate_research_process(name) if available.
      3. Scans for on-disk engine.pid, terminates the matching PID via psutil, and deletes the lock file.
      4. Synchronizes task status in state.json on disk to 'timed_out' so server loops do not revive it.

    Args:
        name: Task key (e.g., 'task_1787311024_e75fcde1').
        grace_period: Seconds to wait for SIGTERM before escalating to SIGKILL.
    """
    print(f"[TASK MANAGER] Terminating subprocess for task '{name}'...", flush=True)

    # 1. In-memory handle check from _active_handles
    handle = _active_handles.pop(name, None)
    if handle is not None and hasattr(handle, "terminate") and callable(getattr(handle, "terminate", None)):
        try:
            poll_fn = getattr(handle, "poll", None)
            is_alive = poll_fn() is None if callable(poll_fn) else True
            if is_alive:
                handle.terminate()
                wait_fn = getattr(handle, "wait", None)
                if callable(wait_fn):
                    try:
                        wait_fn(timeout=grace_period)
                    except (subprocess.SubprocessError, psutil.Error, OSError, TimeoutError):
                        kill_fn = getattr(handle, "kill", None)
                        if callable(kill_fn):
                            with contextlib.suppress(subprocess.SubprocessError, psutil.Error, OSError):
                                kill_fn()
        except (subprocess.SubprocessError, psutil.Error, OSError) as e:
            print(f"[TASK MANAGER] Error terminating handle for {name}: {e}", flush=True)
        unregister_subprocess(handle)

    # 2. Delegate to server's terminate_research_process if available
    for mod_name in ("evelyn_server", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod:
            term_fn = getattr(mod, "terminate_research_process", None)
            if callable(term_fn):
                try:
                    term_fn(name)
                except (subprocess.SubprocessError, psutil.Error, OSError, RuntimeError) as e:
                    print(f"[TASK MANAGER] Error in server.terminate_research_process for {name}: {e}", flush=True)
                break

    # 3. Direct PID check via engine.pid and disk state update for research tasks
    if name.startswith("task_"):
        try:
            try:
                import evelyn_config as cfg
                base_dir = getattr(cfg, "BASE_DIR", r"/home/rathius/evelyn")
                task_dir = os.path.join(base_dir, "data", "research", name)
            except (ImportError, AttributeError):
                task_dir = os.path.join(r"/home/rathius/evelyn/data/research", name)

            pid_path = os.path.join(task_dir, "engine.pid")
            if os.path.exists(pid_path):
                try:
                    with open(pid_path, encoding="utf-8") as f:
                        pid = int(f.read().strip())
                    if psutil.pid_exists(pid):
                        p = psutil.Process(pid)
                        cmdline = p.cmdline() or []
                        if any("research_engine.py" in arg for arg in cmdline):
                            print(f"[TASK MANAGER] Killing process PID {pid} for task {name}", flush=True)
                            p.terminate()
                            try:
                                p.wait(timeout=grace_period)
                            except (psutil.Error, TimeoutError):
                                with contextlib.suppress(psutil.Error, OSError):
                                    p.kill()
                except (psutil.Error, OSError, ValueError) as e:
                    print(f"[TASK MANAGER] Error killing PID from {pid_path}: {e}", flush=True)
                finally:
                    with contextlib.suppress(OSError):
                        os.remove(pid_path)

            # 4. Synchronize on-disk state.json to prevent resurrection by idle research loop
            state_path = os.path.join(task_dir, "state.json")
            if os.path.exists(state_path):
                try:
                    with open(state_path, encoding="utf-8") as f:
                        disk_state = json.load(f)
                    if isinstance(disk_state, dict):
                        disk_state["status"] = "timed_out"
                        disk_state["error"] = "Task terminated: Exceeded watchdog runtime threshold"
                        disk_state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                        with open(state_path, "w", encoding="utf-8") as f:
                            json.dump(disk_state, f, indent=2)
                except (OSError, json.JSONDecodeError, ValueError) as e:
                    print(f"[TASK MANAGER] Error updating disk state for {name}: {e}", flush=True)
        except (OSError, ValueError) as e:
            print(f"[TASK MANAGER] Subprocess termination error for {name}: {e}", flush=True)


def terminate_all_subprocesses(grace_period: float = 3.0) -> None:
    """Gracefully terminate all spawned child processes and cancel in-flight async tasks.

    Sends SIGTERM first, waits up to grace_period, then issues SIGKILL to any remaining processes.

    Args:
        grace_period: Seconds to wait after SIGTERM before escalating to SIGKILL.
    """
    import asyncio

    print(f"[TASK MANAGER] Terminating all {len(_spawned_subprocesses)} registered subprocesses...", flush=True)

    # 1. Cancel in-memory asyncio task handles
    for _name, handle in list(_active_handles.items()):
        if isinstance(handle, asyncio.Task) and not handle.done():
            handle.cancel()

    # 2. Send SIGTERM to all subprocesses
    alive_procs = []
    for proc in _spawned_subprocesses:
        with contextlib.suppress(subprocess.SubprocessError, psutil.Error, OSError):
            if proc.poll() is None:
                proc.terminate()
                alive_procs.append(proc)

    if not alive_procs:
        _spawned_subprocesses.clear()
        return

    # Wait for grace period
    start = time.time()
    while time.time() - start < grace_period:
        alive_procs = [p for p in alive_procs if p.poll() is None]
        if not alive_procs:
            break
        time.sleep(0.1)

    # Escalation to SIGKILL if still alive
    for proc in alive_procs:
        with contextlib.suppress(subprocess.SubprocessError, psutil.Error, OSError):
            if proc.poll() is None:
                print(f"[TASK MANAGER] Process PID {proc.pid} unresponsive; sending SIGKILL.", flush=True)
                proc.kill()

    _spawned_subprocesses.clear()


def reap_orphaned_processes() -> dict:
    """Startup sanitization: sweep orphaned background processes, stale locks, and interrupted states.

    Returns:
        dict: Summary of reaped PIDs and cleaned locks.
    """
    reaped = []
    cleaned_locks = []
    my_pid = os.getpid()

    # Known background worker script names
    target_scripts = {
        "obsidian_vault_watcher.py",
        "refresh_memory.py",
        "ingest_obsidian_knowledge.py",
        "sync_full_vault_to_chroma.py",
        "tag_librarian.py",
        "fact_extractor.py",
        "fact_consolidator.py",
    }

    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                pid = proc.info["pid"]
                if pid == my_pid:
                    continue
                cmdline = proc.info["cmdline"] or []
                cmd_str = " ".join(cmdline)
                if any(script in cmd_str for script in target_scripts):
                    print(f"[STARTUP REAPER] Found orphaned process PID {pid}: {cmd_str[:80]}", flush=True)
                    try:
                        p = psutil.Process(pid)
                        p.terminate()
                        try:
                            p.wait(timeout=1.5)
                        except psutil.TimeoutExpired:
                            p.kill()
                        reaped.append(pid)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except (psutil.Error, OSError) as e:
        print(f"[STARTUP REAPER] Warning during process scan: {e}", flush=True)

    # Clean up stale .lock files
    data_dir = r"/home/rathius/evelyn/data"
    for root, _, files in os.walk(data_dir):
        for file in files:
            if file.endswith(".lock") or file.startswith(".chroma_write.lock"):
                lock_path = os.path.join(root, file)
                with contextlib.suppress(OSError):
                    # Remove lock file unconditionally during server startup
                    os.remove(lock_path)
                    cleaned_locks.append(lock_path)
                    print(f"[STARTUP REAPER] Removed stale lock file: {lock_path}", flush=True)

    # Normalize heavy_tasks_state.json if tasks were interrupted
    tasks = _get_background_tasks()
    if tasks:
        modified = False
        for name, info in list(tasks.items()):
            if isinstance(info, dict) and info.get("status") in RUNNING_STATUSES:
                print(f"[STARTUP REAPER] Normalizing interrupted task '{name}' from '{info.get('status')}' to 'idle'", flush=True)
                info["status"] = "idle"
                info["summary"] = "Reset to idle during server startup sanitization"
                modified = True
        if modified:
            save_persistent_state()

    return {"reaped_pids": reaped, "cleaned_locks": cleaned_locks}



# ---------------------------------------------------------------------------
# SQLite Performance History Database Helpers
# ---------------------------------------------------------------------------


def _get_db_connection():
    """Return an open SQLite connection to evelyn_memory.db."""
    try:
        import evelyn_config as cfg
        db_path = getattr(cfg, "MEMORY_DB_PATH", r"/home/rathius/evelyn/data/evelyn_memory.db")
    except (ImportError, AttributeError):
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
    except (sqlite3.Error, OSError) as e:
        print(f"[TASK MANAGER] Warning: Could not initialize task history DB: {e}", flush=True)


def record_task_history(
    name: str,
    started_at: float,
    finished_at: float,
    elapsed_seconds: float,
    status: str,
    error: str | None = None,
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
    except (sqlite3.Error, OSError, ValueError) as e:
        print(f"[TASK MANAGER] Error recording task history for {name}: {e}", flush=True)


def get_dynamic_timeout(name: str) -> float:
    """Calculate the soft maximum runtime timeout for a task using historical statistics (mean + 3 * std_dev).

    Args:
        name: The task key (e.g. 'extractor', 'profile_evolver', 'task_1787429513_0876a6e7').

    Returns:
        float: Soft timeout threshold in seconds.
    """
    baseline = DEFAULT_SOFT_TIMEOUTS.get(name)
    if baseline is None:
        if name.startswith("task_"):
            scope = None
            wc_timeout = None
            tasks = _get_background_tasks()
            if tasks and name in tasks:
                scope = tasks[name].get("scope")

            try:
                import evelyn_config as cfg
                res_dir = getattr(cfg, "RESEARCH_DATA_DIR", "/home/rathius/evelyn/data/research")
            except (ImportError, AttributeError):
                res_dir = "/home/rathius/evelyn/data/research"
            state_file = os.path.join(res_dir, name, "state.json")
            if os.path.exists(state_file):
                with contextlib.suppress(OSError, json.JSONDecodeError, ValueError), open(state_file, encoding="utf-8") as f:
                    state_data = json.load(f)
                    if not scope:
                        scope = state_data.get("scope")
                    wc_timeout = state_data.get("wall_clock_timeout")

            if wc_timeout:
                baseline = max(float(wc_timeout) + 1800.0, float(wc_timeout) * 1.25)
            else:
                scope_key = f"research_{scope}" if scope else "research"
                baseline = DEFAULT_SOFT_TIMEOUTS.get(scope_key, DEFAULT_SOFT_TIMEOUTS.get("research", 9000.0))
        else:
            baseline = 1800.0

    if name == "profile_evolver":
        try:
            import evelyn_config as cfg
            doc_timeout = float(getattr(cfg, "PROFILE_EVOLUTION_DOC_TIMEOUT", 1500.0))
            baseline = max(baseline or 4500.0, doc_timeout * 3.0)
        except (ImportError, AttributeError):
            pass

    # Query SQLite database for up to 30 completed runs
    with contextlib.suppress(sqlite3.Error, OSError, ValueError):
        conn = _get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT elapsed_seconds FROM heavy_task_history
            WHERE task_name = ? AND status = 'idle'
            ORDER BY finished_at DESC LIMIT 30
            """,
            (name,),
        )
        rows = cur.fetchall()
        conn.close()

        if rows and len(rows) >= 3:
            durations = [r["elapsed_seconds"] for r in rows if r["elapsed_seconds"] and r["elapsed_seconds"] > 0]
            if len(durations) >= 3:
                mean = sum(durations) / len(durations)
                variance = sum((x - mean) ** 2 for x in durations) / len(durations)
                std_dev = math.sqrt(variance)
                dynamic_val = mean + (3.0 * std_dev)
                # Enforce baseline minimum so small averages don't cut off normal runs
                return max(baseline, dynamic_val)

    return baseline



# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_local_background_tasks: dict = {}


def _get_background_tasks() -> dict:
    """Return the server's _background_tasks dict, or local fallback if standalone.

    Walks the two module name candidates the existing codebase already uses,
    so this works whether the server is the main module or an imported one.

    Returns:
        dict: The _background_tasks dict (or local fallback).
    """
    import sys
    for mod_name in ("evelyn_server", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod:
            tasks = getattr(mod, "_background_tasks", None)
            if isinstance(tasks, dict):
                return tasks
    return _local_background_tasks



# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

STATE_FILE = r"/home/rathius/evelyn/data/heavy_tasks_state.json"
QUEUE_STATE_FILE = r"/home/rathius/evelyn/data/evelyn_task_queue.json"

# ---------------------------------------------------------------------------
# Idle Task Queue (Pure FIFO Sequential Scheduling)
# ---------------------------------------------------------------------------

_idle_queue: list[dict] = []
_chat_preempted: bool = False
_boot_ts: float = time.time()


def get_boot_ts() -> float:
    """Return the timestamp when the server or task manager was initialized."""
    return _boot_ts


def is_boot_grace_period_active() -> bool:
    """Return True if the server startup grace period is currently active."""
    try:
        import evelyn_config as cfg
        grace_period = getattr(cfg, "IDLE_STARTUP_GRACE_PERIOD", 60.0)
    except (ImportError, AttributeError):
        grace_period = 60.0
    return (time.time() - _boot_ts) < grace_period


def is_chat_preempted() -> bool:
    """Return True if user chat is actively preempting idle tasks."""
    return _chat_preempted


def set_chat_preemption(active: bool) -> None:
    """Set or clear the user chat preemption flag.

    When active=True, immediately cancels in-flight idle tasks to grant
    100% compute/GPU resources to the interactive user turn.

    Args:
        active: True to preempt idle tasks for chat, False when chat finishes.
    """
    global _chat_preempted
    _chat_preempted = active
    if _chat_preempted:
        cancel_all_idle_tasks("chat_preemption")


def is_task_queued(name: str) -> bool:
    """Return True if a task with the given name is currently waiting in the idle queue.

    Args:
        name: The task key (e.g. 'extractor', 'consolidator').

    Returns:
        bool: True if already present in the queue.
    """
    return any(item.get("task") == name for item in _idle_queue)


def get_idle_queue() -> list[dict]:
    """Return a shallow copy of the current idle queue."""
    return list(_idle_queue)


def enqueue_idle_task(name: str, metadata: dict | None = None) -> bool:
    """Enqueue a task at the tail of the FIFO idle queue.

    Idempotent: Prevents duplicate queue entries if the task is already
    waiting in _idle_queue or currently actively running.

    Args:
        name: The task key (e.g. 'extractor', 'consolidator', 'tag_librarian').
        metadata: Optional metadata dictionary associated with the task run.

    Returns:
        bool: True if newly enqueued, False if already queued or running.
    """
    # 1. Check if already queued
    if is_task_queued(name):
        return False

    # 2. Check if currently actively running
    status = get_status(name)
    if status in RUNNING_STATUSES or status == "running":
        return False

    entry = {
        "task": name,
        "enqueued_at": time.time(),
        "metadata": metadata or {},
    }
    _idle_queue.append(entry)
    save_persistent_queue()
    print(f"[TASK QUEUE] Enqueued '{name}' at tail (queue size: {len(_idle_queue)}).", flush=True)
    return True


def acquire_next_idle_task() -> dict | None:
    """Pop and return the next task from the front of the FIFO idle queue.

    Returns:
        dict | None: The popped queue item, or None if the queue is empty
        or chat preemption is active.
    """
    if _chat_preempted or not _idle_queue:
        return None

    item = _idle_queue.pop(0)
    save_persistent_queue()
    print(f"[TASK QUEUE] Dispatched '{item.get('task')}' from front (remaining in queue: {len(_idle_queue)}).", flush=True)
    return item


def peek_next_idle_task() -> dict | None:
    """Return the next task from the front of the queue without removing it."""
    if not _idle_queue:
        return None
    return _idle_queue[0]


def should_yield(name: str) -> bool:
    """Check if the currently running batch task should yield runtime.

    Returns True if:
      - Chat preemption is active (_chat_preempted is True), OR
      - Other peer tasks are waiting in the idle queue (len(_idle_queue) > 0).

    Args:
        name: The name of the currently running task.

    Returns:
        bool: True if the task should yield and commit its progress, False otherwise.
    """
    if _chat_preempted:
        return True

    # If other peer tasks are waiting in the queue, yield to let them execute
    return len(_idle_queue) > 0


def cancel_all_idle_tasks(reason: str = "chat_request") -> None:
    """Cancel all active idle/background tasks to free resources immediately.

    Used during user chat interaction or server shutdown.

    Args:
        reason: Description of why cancellation was triggered.
    """
    import contextlib
    # 1. Cancel in-memory handles for idle tasks
    for name, handle in list(_active_handles.items()):
        if name.startswith("test_"):
            continue
        try:
            if hasattr(handle, "cancel") and callable(handle.cancel) and not handle.done():
                handle.cancel()
                print(f"[TASK MANAGER] Cancelled active handle for '{name}' ({reason}).", flush=True)
        except (RuntimeError, OSError) as e:
            print(f"[TASK MANAGER] Error cancelling handle for '{name}': {e}", flush=True)

    # 2. Call tool-specific cancellation hooks if available
    with contextlib.suppress(ImportError, AttributeError):
        from Evelyn.tools import fact_extractor
        fact_extractor.cancel_pending_extraction(reason=reason)


def save_persistent_queue() -> None:
    """Persist the current idle task queue to disk."""
    import json
    import os
    queue_file = QUEUE_STATE_FILE

    try:
        os.makedirs(os.path.dirname(queue_file), exist_ok=True)
        with open(queue_file, "w", encoding="utf-8") as f:
            json.dump(_idle_queue, f, indent=2)
    except (OSError, TypeError, ValueError) as e:
        print(f"[TASK MANAGER] Error saving persistent task queue: {e}", flush=True)


def load_persistent_queue() -> None:
    """Load persistent idle task queue from disk on startup and reconcile interrupted jobs."""
    global _idle_queue
    import json
    import os
    import time
    queue_file = QUEUE_STATE_FILE

    loaded_queue = []
    if os.path.exists(queue_file):
        try:
            with open(queue_file, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                loaded_queue.extend([item for item in data if isinstance(item, dict) and "task" in item])
            print(f"[TASK MANAGER] Loaded persistent idle queue ({len(loaded_queue)} items).", flush=True)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            print(f"[TASK MANAGER] Error loading persistent task queue: {e}", flush=True)

    # Reconcile interrupted running tasks from persistent state:
    # If a task was marked 'running' when the server stopped/crashed,
    # re-enqueue it at the front of the queue so it resumes.
    tasks = _get_background_tasks() or {}
    for task_name, info in tasks.items():
        if task_name.startswith(("test_", "task_")):
            continue
        if isinstance(info, dict) and (info.get("status") in RUNNING_STATUSES or info.get("status") == "running") and not any(item.get("task") == task_name for item in loaded_queue):
            loaded_queue.insert(0, {
                "task": task_name,
                "enqueued_at": time.time(),
                "metadata": {"resumed_from_crash": True},
            })
            print(f"[TASK MANAGER] Reconciled interrupted task '{task_name}' to front of idle queue.", flush=True)

    _idle_queue = loaded_queue
    save_persistent_queue()


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
    except (OSError, TypeError, ValueError) as e:
        print(f"[TASK MANAGER] Error saving persistent state: {e}", flush=True)


def load_persistent_state() -> None:
    """Load non-research heavy task states from disk on server startup."""
    import json
    import os
    import sqlite3
    tasks = _get_background_tasks()
    if tasks is None:
        return

    # 1. Load JSON state file from disk
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for key, val in data.items():
                    if isinstance(val, dict) and key not in tasks:
                        tasks[key] = val
                print(f"[TASK MANAGER] Restored heavy tasks state from disk ({len(tasks)} tasks).", flush=True)
        except (OSError, json.JSONDecodeError, ValueError) as e:
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
    except (sqlite3.Error, OSError, ValueError) as e:
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
        with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
            with open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and name in data:
                ts = data[name].get("last_run_at")
                if isinstance(ts, (int, float)) and ts > 0:
                    return float(ts)

    with contextlib.suppress(sqlite3.Error, OSError, ValueError):
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

    return default


def save_last_run_ts(name: str, ts: float | None = None) -> float:
    """Save and update the last_run_at timestamp for a named heavy task.

    Args:
        name: Task key.
        ts: Float timestamp (defaults to current time if None).

    Returns:
        float: The timestamp float that was saved.
    """
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
            with contextlib.suppress(OSError, json.JSONDecodeError, ValueError), open(STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        if not isinstance(data, dict):
            data = {}
        if name not in data or not isinstance(data[name], dict):
            data[name] = {"status": "idle"}
        data[name]["last_run_at"] = now
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except (OSError, TypeError, ValueError) as e:
            print(f"[TASK MANAGER] Error saving persistent state: {e}", flush=True)

    return now


def is_any_running(exclude: str | None = None) -> bool:
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
    phase: str | None = None,
    sub_status: dict | None = None,
    diagnostics: dict | None = None,
    task_obj: object | None = None,
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
        with contextlib.suppress(RuntimeError):
            import asyncio
            current = asyncio.current_task()
            if current:
                _active_handles[name] = current

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
    error: str | None = None,
    summary: str | None = None,
    sub_status: dict | None = None,
    diagnostics: dict | None = None,
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


def get_status(name: str) -> str | None:
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
        elif hasattr(handle, "poll") and callable(getattr(handle, "poll", None)):
            is_done = handle.poll() is not None

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

            # Terminate subprocess if this is a research task or subprocess-backed task
            if name.startswith("task_") or (handle is not None and hasattr(handle, "terminate")):
                terminate_task_subprocess(name)

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
            except (sqlite3.Error, OSError, RuntimeError, ValueError) as e:
                print(f"[TASK WATCHDOG ERROR] {e}", flush=True)

    _watchdog_task = asyncio.create_task(_loop())

