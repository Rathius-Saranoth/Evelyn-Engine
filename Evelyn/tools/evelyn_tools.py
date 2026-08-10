# evelyn_tools.py
# date created: 2026-03-23 15:38:53
# date modified: 2026-08-02 10:11:23
# tags: #tools, #definitions, #schema, #dispatch, #models

"""
evelyn_tools.py — Evelyn's tool definitions in standard OpenAI function-calling format.

Each tool is:
  1. A plain Python function containing the actual logic.
  2. A JSON schema dict defining it for Ollama's `tools` API field.

The TOOL_DEFINITIONS list at the bottom is what gets passed to Ollama.
The TOOL_FUNCTIONS dict maps tool name → callable for the dispatcher in evelyn_server.py.

All tool logic uses standard function signatures for Ollama's function-calling API.
"""

import sys
import os
import importlib
from typing import Optional


# ---------------------------------------------------------------------------
# Module path setup
# ---------------------------------------------------------------------------
import evelyn_config as cfg

TOOLS_DIR = getattr(cfg, "TOOLS_DIR", r"/home/rathius/evelyn/Evelyn/tools")
VAULT_BASE = getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")


def get_jaccard_similarity(str1: str = "", str2: str = "", **kwargs) -> float:
    """Calculate Jaccard similarity between two strings.

    Tokenizes strings into words and filters out common English stop words.

    Args:
        str1: The first string to compare.
        str2: The second string to compare.

    Returns:
        float: Jaccard similarity score between 0.0 and 1.0.
    """
    stop_words = {"for", "and", "the", "a", "of", "in", "to", "behind", "on", "with", "by", "an", "at", "about"}
    words1 = set("".join(c for c in str1.lower() if c.isalnum() or c.isspace()).split()) - stop_words
    words2 = set("".join(c for c in str2.lower() if c.isalnum() or c.isspace()).split()) - stop_words
    if not words1 and not words2:
        return 1.0
    intersection = words1.intersection(words2)
    union = words1.union(words2)
    return len(intersection) / len(union)


def _prune_log_file(log_path: str, max_lines: int = 2000, keep_lines: int = 1000) -> None:
    """Prune a log file down to the last keep_lines if it exceeds max_lines.

    Args:
        log_path: Absolute or relative path to the log file.
        max_lines: Maximum number of lines permitted before pruning is triggered.
        keep_lines: Number of most recent lines to retain when pruning.
    """
    try:
        if not os.path.exists(log_path):
            return
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            with open(log_path, "w", encoding="utf-8", errors="replace") as f:
                f.writelines(lines[-keep_lines:])
    except Exception:
        pass



if TOOLS_DIR not in sys.path:
    sys.path.append(TOOLS_DIR)

import journal_manager # [[journal_manager.py]]
import context_manager # [[context_manager.py]]
import ingest_gists # [[ingest_gists.py]]
import ingest_obsidian_knowledge # [[ingest_obsidian_knowledge.py]]
import gcal_sync
import terminal_agent


def _reload():
    """Hot-reload all backing modules so live edits take effect without restarting."""
    for mod in (
        "journal_manager",
        "context_manager",
        "ingest_gists",
        "ingest_obsidian_knowledge",
        "gcal_sync",
        "terminal_agent",
    ):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])




# ===========================================================================
# Tool functions
# ===========================================================================


def write_journal_entry(
    mood: str = "",
    vibe_check: str = "",
    narrative: str = "",
    message_in_a_bottle: str = "",
    tags: str = "",
    **kwargs,
) -> str:
    """Compose and queue a new journal entry for Ricky's review.

    Args:
        mood: Descriptive keyword representing current emotional state.
        vibe_check: Brief micro-assessment or immediate feeling.
        narrative: Main reflective text or journal body.
        message_in_a_bottle: A lingering question or message meant for future recall.
        tags: Comma-separated list of tags to associate.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Outcome confirmation message or path to the pending entry.
    """
    _reload()
    mood = mood or str(kwargs.get("feeling") or kwargs.get("emotion") or "Reflective")
    vibe_check = vibe_check or str(kwargs.get("vibe") or kwargs.get("intro") or "")
    narrative = narrative or str(kwargs.get("body") or kwargs.get("text") or kwargs.get("journal_text") or "")
    message_in_a_bottle = message_in_a_bottle or str(kwargs.get("bottle_message") or kwargs.get("closing") or "")
    tags = tags or str(kwargs.get("tag_list") or kwargs.get("tag_string") or "")

    if (
        not vibe_check.strip()
        and not narrative.strip()
        and not message_in_a_bottle.strip()
    ):
        return "Error: write_journal_entry called with completely blank text fields. Aborted."
    tag_list = [t.strip() for t in tags.split(",")] if tags.strip() else []
    return journal_manager.create_journal_entry(
        vibe_check, narrative, message_in_a_bottle, mood, tag_list
    )


def read_journal(date: str = "", days: int = 0, **kwargs) -> str:
    """Read Evelyn's personal journal entries by a specific date or over a recent day window.

    Args:
        date: Optional date in YYYY-MM-DD format. If provided, reads that specific day's entry.
        days: Optional number of recent days to retrieve (e.g. 7). Takes precedence if > 0.
        **kwargs: Accepts flexible keyword arguments (date_str, target_date, query_date, days_back).

    Returns:
        str: Markdown contents of matching journal entries, or message if none found.
    """
    _reload()
    if not date:
        date = str(kwargs.get("date_str") or kwargs.get("target_date") or kwargs.get("query_date") or "")
    if not days and "days_back" in kwargs:
        try:
            days = int(kwargs["days_back"])
        except (ValueError, TypeError):
            pass

    if days > 0:
        return journal_manager.read_recent_journal_entries(days)
    return journal_manager.read_journal_entry(date if date else None)


def read_journal_entry(date: str = "", **kwargs) -> str:
    """Read a single journal entry by its date (legacy wrapper)."""
    if not date:
        date = str(kwargs.get("date_str") or kwargs.get("target_date") or "")
    return read_journal(date=date)


def read_recent_journal_entries(days: int = 7, **kwargs) -> str:
    """Read recent journal entries from the last N days (legacy wrapper)."""
    if not days or days == 7:
        if "days_back" in kwargs:
            try:
                days = int(kwargs["days_back"])
            except (ValueError, TypeError):
                pass
    return read_journal(days=days)


def search_vault(query: str = "", **kwargs) -> str:
    """Search the pre-summarized Obsidian Vault gist index.

    Args:
        query: Search term or phrase.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: A concise summary of matching documents and their vault-relative paths.
    """
    _reload()
    query = query or str(kwargs.get("search_query") or kwargs.get("search_term") or kwargs.get("term") or "")
    return context_manager.search_vault_map(query)


def recall_specific_memory(file_path: str = "", **kwargs) -> str:
    """Read the full markdown content of a specific Obsidian vault file.

    Args:
        file_path: Exact vault-relative path returned by search_vault.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Full text content of the markdown file, or error message.
    """
    file_path = file_path or str(kwargs.get("filepath") or kwargs.get("path") or kwargs.get("file") or "")
    clean_path = file_path.strip().strip('"').strip("'").replace('\\', '/')
    full_path = os.path.abspath(os.path.join(VAULT_BASE, clean_path))
    if not full_path.startswith(os.path.abspath(VAULT_BASE)):
        return "Error: Invalid path — path traversal detected."
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            return f"--- Content of {clean_path} ---\n\n{f.read()}"
    except FileNotFoundError:
        return f"Error: File '{clean_path}' not found."
    except Exception as e:
        return f"Error reading {clean_path}: {e}"


def log_context_fact(category: str = "", summary: str = "", secondary_cats: str = "", **kwargs) -> str:
    """Write a context fact file to the in-vault Pending folder.

    Args:
        category: Primary category/domain.
        summary: Precise fact summary.
        secondary_cats: Comma-separated secondary categories.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Confirmation message.
    """
    _reload()
    category = category or str(kwargs.get("cat") or kwargs.get("domain") or "")
    summary = summary or str(kwargs.get("fact") or kwargs.get("text") or "")
    secondary_cats = secondary_cats or str(kwargs.get("refs") or kwargs.get("tags") or "")
    if not summary.strip():
        return "Error: log_context_fact called with blank summary. Aborted."
    refs = (
        [c.strip() for c in secondary_cats.split(",")] if secondary_cats.strip() else []
    )
    return context_manager.append_context_log(category, summary, refs)


def update_context_fact(target_filepaths: list = None, new_summary: str = "", **kwargs) -> str:
    """Queue an update request for an existing vault context file.

    Args:
        target_filepaths: List of vault paths targeted for consolidation.
        new_summary: Revised context summary.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Confirmation message.
    """
    _reload()
    if target_filepaths is None:
        target_filepaths = kwargs.get("filepaths") or kwargs.get("paths") or []
    new_summary = new_summary or str(kwargs.get("summary") or kwargs.get("revised_summary") or "")
    if not new_summary.strip():
        return "Error: update_context_fact called with blank new_summary. Aborted."
    return context_manager.update_context_log(target_filepaths, new_summary)


def generate_image(
    prompt: str = "",
    aspect_ratio: str = "16:9",
    seed: int | None = None,
    short_title: str | None = None,
    **kwargs,
) -> str:
    """Generate a high-quality image via FLUX.1 Schnell.

    Args:
        prompt: Descriptive prompt describing the desired image.
        aspect_ratio: Image format ratio (e.g., "16:9", "1:1", "9:16").
        seed: Optional random generator seed.
        short_title: Optional title prefix for the generated file.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Confirmation path/URL to the generated image, or error description.
    """
    import requests
    from evelyn_config import IMAGE_SERVER_URL

    prompt = prompt or str(kwargs.get("description") or kwargs.get("image_prompt") or "")
    try:
        payload = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
        }
        if short_title:
            payload["short_title"] = short_title
        if seed is not None:
            payload["seed"] = seed

        url = f"{IMAGE_SERVER_URL}/generate"
        resp = requests.post(url, json=payload, timeout=600)
        if resp.status_code != 200:
            return f"Error from Image Engine: {resp.text}"
        
        result = resp.json()
        filename = result["filename"]
        # Served statically via the main evelyn_server mount
        image_url = f"/images/{filename}"
        return f"Image generated successfully at {image_url}."
    except Exception as e:
        return f"Failed to generate image via FLUX.1 server at {IMAGE_SERVER_URL}: {e}"


def sync_context_memory(**kwargs) -> str:
    """Trigger background sync of vault gists and core memory into Chroma.

    Args:
        **kwargs: Unused parameters.

    Returns:
        str: Status message indicating start.
    """
    import threading

    def _run():
        """Run sync_context_memory phases (knowledge and gists ingest) in a daemon thread."""
        _reload()
        try:
            print("Sync: Starting core memory ingest...")
            ingest_obsidian_knowledge.main()
            print("Sync: Starting gist ingest...")
            ingest_gists.main()
            print("Sync: Complete.")
        except Exception as e:
            print(f"Sync error: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return "Memory sync initiated in the background. New context will be available shortly."


def web_search(query: str, max_results: int = 5, **kwargs) -> str:
    """Search the web via DuckDuckGo and return a brief summary of the top results.

    Args:
        query: Concise, keyword-based web query.
        max_results: Max result snippets to fetch. Defaults to 5.
        **kwargs: Accepts flexible keyword arguments.

    Returns:
        str: Summarized search results or error details.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        return "Error: ddgs library is not installed. Run 'pip install ddgs' to enable web search."

    try:
        try:
            max_results = int(max_results)
        except (ValueError, TypeError):
            max_results = 5

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return f"No results found for: {query}"
        lines = [f"Web search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "(no title)")
            href = r.get("href", "")
            body = r.get("body", "").strip()
            lines.append(f"{i}. {title}\n   {href}\n   {body[:300]}")
        return "\n\n".join(lines)
    except Exception as e:
        return f"Web search error: {e}"


def _is_research_engine_running(task_id: str) -> bool:
    """Return True if a live research_engine.py subprocess exists for this task.

    Reads the engine.pid file written by research_engine.main() and performs
    an OS-level liveness check using psutil to verify the PID is active and
    actually running research_engine.py. If the PID is dead or assigned to
    another process, cleans up the stale engine.pid lock file.

    Args:
        task_id: The research task identifier.

    Returns:
        bool: True if a live process is running for this task.
    """
    import os
    try:
        from research_engine import get_task_dir
        pid_path = os.path.join(get_task_dir(task_id), "engine.pid")
        if not os.path.exists(pid_path):
            return False
        with open(pid_path) as f:
            pid = int(f.read().strip())
        
        import psutil
        if psutil.pid_exists(pid):
            try:
                proc = psutil.Process(pid)
                cmdline = proc.cmdline()
                if any("research_engine.py" in arg for arg in cmdline):
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        
        # Dead PID or recycled PID for another process — delete stale pid file
        try:
            os.remove(pid_path)
        except OSError:
            pass
        return False
    except Exception:
        return False



def start_research(
    query: str,
    scope: str = "standard",
    triggered_by: str = "user",
    intent_frame: Optional[str] = None,
    **kwargs,
) -> str:
    """Launch a deep research task on a topic in the background.

    Args:
        query: Research query/topic.
        scope: Depth scope ("quick", "standard", "deep"). Defaults to "standard".
        triggered_by: Identifies the initiator ('user', 'idle', 'evelyn').
            Defaults to 'user'.
        intent_frame: Optional 2-3 sentence string describing why this topic
            matters and what kind of answer is needed. Forwarded to
            create_research_task() so step_plan() can skip LLM frame generation.
            Defaults to None.
        **kwargs: Optional overrides like bypass_queue.

    Returns:
        str: Confirmation message.
    """
    import time
    import threading
    import subprocess
    import sys
    import os
    import json
    import datetime
    
    _reload()
    try:
        # Get bypass_queue flag
        bypass_queue = kwargs.get("bypass_queue", False)
        server = sys.modules.get("evelyn_server")
        if not server:
            server = sys.modules.get("__main__")
            if not hasattr(server, "_background_tasks"):
                server = None
        import evelyn_config as cfg

        # 1. Disk-level dedup: check all task folders for any task (completed or
        # in-flight) that is too similar to the incoming query. This guard runs
        # regardless of bypass_queue so that tasks dequeued by the idle loop
        # cannot re-launch a topic that is already running or already done.
        if os.path.exists(cfg.RESEARCH_DATA_DIR):
            from research_engine import load_state
            for folder in os.listdir(cfg.RESEARCH_DATA_DIR):
                if folder.startswith("task_"):
                    disk_state = load_state(folder)
                    if not disk_state:
                        continue
                    disk_status = disk_state.get("status", "")
                    disk_query = disk_state.get("query", "")
                    if not disk_query:
                        continue
                    similarity = get_jaccard_similarity(query, disk_query)
                    if similarity >= 0.45:
                        if disk_status == "done":
                            msg = (
                                f"I have already completed deep research on a very similar topic: "
                                f"'{disk_query}' (Task ID: {folder}). Ricky can read the synthesized report "
                                "directly in the Deep Research Dashboard, so I will not launch a new task for this."
                            )
                        else:
                            msg = (
                                f"Research on a very similar topic is already in progress: "
                                f"'{disk_query}' (Task ID: {folder}, status: {disk_status}). "
                                "I will not start a duplicate task."
                            )
                        print(f"[RESEARCH DEDUP] {msg}", flush=True)
                        return msg

        # 2. Concurrency & queue check: check for any unfinished research tasks
        # Uses task_manager as the primary guard so this works even when server
        # is None (fixes the if server: guard hole — Root Cause #3).
        unfinished_task_id = None
        unfinished_status = None
        unfinished_query = None

        # Check all task directories on disk for unfinished/active tasks
        if os.path.exists(cfg.RESEARCH_DATA_DIR):
            from research_engine import load_state
            for folder in os.listdir(cfg.RESEARCH_DATA_DIR):
                if not folder.startswith("task_"):
                    continue
                disk_state = load_state(folder)
                if not disk_state:
                    continue
                status = disk_state.get("status", "")
                if status in ("running", "paused", "error", "searching", "synthesizing", "pending"):
                    unfinished_task_id = folder
                    unfinished_status = status
                    unfinished_query = disk_state.get("query", "")

                    # If actively running, refuse unconditionally — regardless of server availability
                    if status in ("running", "searching", "synthesizing") or _is_research_engine_running(folder):
                        return (
                            f"Cannot start immediately: another research task ({folder}) is already actively running. "
                            "Wait for it to complete or pause before starting another."
                        )

        # If there's an unfinished task and we aren't overriding via dashboard, we queue the new request
        if unfinished_task_id and not bypass_queue:
            queue_file = os.path.join(cfg.RESEARCH_DATA_DIR, "queue.json")
            os.makedirs(cfg.RESEARCH_DATA_DIR, exist_ok=True)
            
            queue = []
            if os.path.exists(queue_file):
                try:
                    with open(queue_file, "r", encoding="utf-8") as f:
                        queue = json.load(f)
                except Exception:
                    queue = []
            
            # Check if a very similar query is already queued (Jaccard similarity >= 0.45) to avoid duplicates
            already_exists = any(get_jaccard_similarity(q.get("query", ""), query) >= 0.45 for q in queue)
            if not already_exists:
                queue.append({
                    "query": query,
                    "scope": scope,
                    "priority": 1,
                    "source": triggered_by,
                    "intent_frame": intent_frame,
                    "created_at": datetime.datetime.now().isoformat()
                })
                try:
                    with open(queue_file, "w", encoding="utf-8") as f:
                        json.dump(queue, f, indent=2)
                except Exception as qe:
                    return f"Failed to queue research task: {qe}"
            
            return (
                f"Successfully queued deep research on '{query}' (scope: {scope}). "
                f"I detected an unfinished research task ('{unfinished_query}', ID: {unfinished_task_id}) "
                f"with status '{unfinished_status}'. To avoid model contention and respect research priority, "
                f"I have added this new task to the queue and will process it chronologically when the current "
                f"unfinished tasks are resolved. Please inform Ricky that the topic has been successfully queued."
            )

        from research_engine import create_research_task
        task_id = create_research_task(
            query,
            scope=scope,
            triggered_by=triggered_by,
            initial_status="running" if bypass_queue else "pending",
            intent_frame=intent_frame,
        )
        
        # Access evelyn_server active processes to ensure mutual exclusion
        if server:
            cancel_consol = getattr(server, "cancel_pending_consolidation", None)
            cancel_extract = getattr(server, "cancel_pending_extraction", None)
            if cancel_consol:
                cancel_consol()
            if cancel_extract:
                cancel_extract()
            
            # Register in server's _background_tasks dict
            bg_tasks = getattr(server, "_background_tasks", None)
            if bg_tasks is not None:
                bg_tasks[task_id] = {
                    "status": "running",
                    "query": query,
                    "scope": scope,
                    "started_at": time.time()
                }
        
        def _run_subprocess():
            """Launch research_engine.py as a subprocess, register it, and wait for completion."""
            import sys
            import os
            try:
                # Layer 1: PID lock check — refuse to spawn if a live process already exists
                # for this task. Works even if server is None or _background_tasks is stale.
                if _is_research_engine_running(task_id):
                    print(
                        f"[RESEARCH] Subprocess already alive for {task_id} — "
                        f"refusing to spawn duplicate.",
                        flush=True,
                    )
                    return

                creationflags = 0
                if sys.platform == "win32":
                    creationflags = 0x08000000 # CREATE_NO_WINDOW
                
                base_dir = getattr(cfg, "BASE_DIR", r"/home/rathius/evelyn")
                script = os.path.join(base_dir, "Evelyn", "tools", "research_engine.py")
                log_path = os.path.join(base_dir, "data", "research_subprocess.log")
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                _prune_log_file(log_path)
                
                log_file = None
                proc = None
                try:
                    log_file = open(log_path, "a", encoding="utf-8")
                    proc = subprocess.Popen(
                        [sys.executable, "-u", script, task_id, "--scope", scope],
                        cwd=base_dir,
                        stdout=log_file,
                        stderr=log_file,
                        creationflags=creationflags
                    )
                except Exception:
                    proc = subprocess.Popen(
                        [sys.executable, "-u", script, task_id, "--scope", scope],
                        cwd=base_dir,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creationflags
                    )
                
                if log_file:
                    try:
                        log_file.close()
                    except Exception:
                        pass
                
                if proc:
                    if server:
                        active_procs = getattr(server, "_active_research_processes", None)
                        if active_procs is not None:
                            active_procs[task_id] = proc
                            
                    returncode = proc.wait()
                    
                    if server:
                        active_procs = getattr(server, "_active_research_processes", None)
                        if active_procs is not None:
                            active_procs.pop(task_id, None)
                    
                    if server and bg_tasks is not None:
                        from research_engine import load_state, save_state
                        disk_state = load_state(task_id)
                        if disk_state is None:
                            disk_state = {"status": "error"}
                        disk_status = disk_state.get("status")
                        if disk_status in ("paused", "cancelled"):
                            bg_tasks[task_id]["status"] = disk_status
                            bg_tasks[task_id]["finished_at"] = time.time()
                        elif returncode == 0:
                            bg_tasks[task_id]["status"] = "done"
                            bg_tasks[task_id]["finished_at"] = time.time()
                        else:
                            bg_tasks[task_id]["status"] = "error"
                            bg_tasks[task_id]["error"] = f"Exit code {returncode}"
                            bg_tasks[task_id]["finished_at"] = time.time()
                            disk_state["status"] = "error"
                            disk_state["error"] = f"Exit code {returncode}"
                            save_state(task_id, disk_state, ignore_disk_status=True)
            except Exception as e:
                print(f"[RESEARCH ERROR] Background execution failed: {e}", flush=True)
                if server and bg_tasks is not None:
                    bg_tasks[task_id]["status"] = "error"
                    bg_tasks[task_id]["error"] = str(e)
                    bg_tasks[task_id]["finished_at"] = time.time()

        threading.Thread(target=_run_subprocess, daemon=True).start()
        return (
            f"Deep research started successfully. Task ID: {task_id}. "
            f"I am conducting research on '{query}' (scope: {scope}) in the background "
            f"and will notify you as soon as the final report is compiled."
        )
    except Exception as e:
        return f"Failed to start deep research: {e}"


def resume_research_task(task_id: str = "", **kwargs) -> str:
    """Re-spawn the background subprocess for a non-running research task.

    Args:
        task_id: Unique task identifier.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Confirmation message.
    """
    import time
    import threading
    import subprocess
    import sys
    
    _reload()
    try:
        from research_engine import load_state, save_state
        state = load_state(task_id)
        if not state:
            return "Research task not found."
            
        # Concurrency check — refuse to resume if another research task is already running.
        # Only one research engine process should run at a time to avoid Ollama contention.
        server = sys.modules.get("evelyn_server")
        if not server:
            server = sys.modules.get("__main__")
            if not hasattr(server, "_background_tasks"):
                server = None
        if server:
            bg_tasks = getattr(server, "_background_tasks", None)
            if bg_tasks:
                for tid, tinfo in bg_tasks.items():
                    if tid.startswith("task_") and tinfo.get("status") in ("running", "searching", "synthesizing"):
                        return f"Cannot resume task {task_id}: another research task ({tid}) is already actively running."
            
        # Reset status to running on disk so the engine knows it should proceed
        state["status"] = "running"
        state["error"] = None
        save_state(task_id, state, ignore_disk_status=True)
        
        query = state.get("query", "")
        scope = state.get("scope", "standard")
        
        # Access evelyn_server active processes to ensure mutual exclusion
        if server:
            cancel_consol = getattr(server, "cancel_pending_consolidation", None)
            cancel_extract = getattr(server, "cancel_pending_extraction", None)
            if cancel_consol:
                cancel_consol()
            if cancel_extract:
                cancel_extract()
                
            # Register in server's _background_tasks dict
            bg_tasks = getattr(server, "_background_tasks", None)
            if bg_tasks is not None:
                bg_tasks[task_id] = {
                    "status": "running",
                    "query": query,
                    "scope": scope,
                    "started_at": time.time()
                }
        else:
            bg_tasks = None
            
        def _run_subprocess():
            """Launch research_engine.py as a subprocess to resume the task and wait for completion."""
            import sys
            import os
            try:
                # Layer 1: PID lock check — refuse to spawn if a live process already exists
                # for this task. Works even if server is None or _background_tasks is stale.
                if _is_research_engine_running(task_id):
                    print(
                        f"[RESEARCH] Subprocess already alive for {task_id} — "
                        f"refusing to spawn duplicate.",
                        flush=True,
                    )
                    return

                creationflags = 0
                if sys.platform == "win32":
                    creationflags = 0x08000000 # CREATE_NO_WINDOW
                
                base_dir = getattr(cfg, "BASE_DIR", r"/home/rathius/evelyn")
                script = os.path.join(base_dir, "Evelyn", "tools", "research_engine.py")
                log_path = os.path.join(base_dir, "data", "research_subprocess.log")
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                _prune_log_file(log_path)
                
                log_file = None
                proc = None
                try:
                    log_file = open(log_path, "a", encoding="utf-8")
                    proc = subprocess.Popen(
                        [sys.executable, "-u", script, task_id, "--scope", scope],
                        cwd=base_dir,
                        stdout=log_file,
                        stderr=log_file,
                        creationflags=creationflags
                    )
                except Exception:
                    proc = subprocess.Popen(
                        [sys.executable, "-u", script, task_id, "--scope", scope],
                        cwd=base_dir,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creationflags
                    )
                
                if log_file:
                    try:
                        log_file.close()
                    except Exception:
                        pass
                
                if proc:
                    if server:
                        active_procs = getattr(server, "_active_research_processes", None)
                        if active_procs is not None:
                            active_procs[task_id] = proc
                            
                    returncode = proc.wait()
                    
                    if server:
                        active_procs = getattr(server, "_active_research_processes", None)
                        if active_procs is not None:
                            active_procs.pop(task_id, None)
                    
                    if server and bg_tasks is not None:
                        from research_engine import load_state, save_state
                        disk_state = load_state(task_id)
                        if disk_state is None:
                            disk_state = {"status": "error"}
                        disk_status = disk_state.get("status")
                        if disk_status in ("paused", "cancelled"):
                            bg_tasks[task_id]["status"] = disk_status
                            bg_tasks[task_id]["finished_at"] = time.time()
                        elif returncode == 0:
                            bg_tasks[task_id]["status"] = "done"
                            bg_tasks[task_id]["finished_at"] = time.time()
                        else:
                            bg_tasks[task_id]["status"] = "error"
                            bg_tasks[task_id]["error"] = f"Exit code {returncode}"
                            bg_tasks[task_id]["finished_at"] = time.time()
                            disk_state["status"] = "error"
                            disk_state["error"] = f"Exit code {returncode}"
                            save_state(task_id, disk_state, ignore_disk_status=True)
            except Exception as e:
                print(f"[RESEARCH ERROR] Background execution failed: {e}", flush=True)
                if server and bg_tasks is not None:
                    bg_tasks[task_id]["status"] = "error"
                    bg_tasks[task_id]["error"] = str(e)
                    bg_tasks[task_id]["finished_at"] = time.time()

        threading.Thread(target=_run_subprocess, daemon=True).start()
        return f"Research task {task_id} successfully resumed."
    except Exception as e:
        return f"Failed to resume research task: {e}"


def guide_research(task_id: str = "", guidance: str = "", **kwargs) -> str:
    """Inject user guidance into a struggling research task and resume it.

    Args:
        task_id: Unique task identifier.
        guidance: Free-form text guidance to redirect the query search.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Resumption status confirmation.
    """
    import os
    import json
    import evelyn_config as cfg
    _reload()
    try:
        from research_engine import load_state, save_state
        state = load_state(task_id)
        if not state:
            return f"Research task {task_id} not found."
            
        if state.get("status") not in ("needs_guidance", "paused", "running", "done", "cancelled", "error"):
            return f"Research task {task_id} is currently '{state.get('status')}'. Cannot inject guidance in this state."
            
        if state.get("status") in ("running", "searching", "synthesizing"):
            import sys
            import time
            server = sys.modules.get("evelyn_server")
            if not server:
                server = sys.modules.get("__main__")
                if not hasattr(server, "terminate_research_process"):
                    server = None
            if server:
                term_func = getattr(server, "terminate_research_process", None)
                if term_func:
                    term_func(task_id)
            # Give it a moment to cleanly exit before overwriting state
            time.sleep(0.5)
            
        # Find the currently active sub-question
        idx = state.get("current_sq_idx", 0)
        plan = state.get("plan", {})
        sqs = plan.get("sub_questions", [])
        if 0 <= idx < len(sqs):
            sq = sqs[idx]
            # Inject guidance directly into the gaps file for the next search iteration
            task_dir = os.path.join(cfg.RESEARCH_DATA_DIR, task_id)
            gaps_file = os.path.join(task_dir, f"{sq['id']}_gaps.json")
            
            existing_gaps = []
            if os.path.exists(gaps_file):
                try:
                    with open(gaps_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        existing_gaps = data.get("gaps", [])
                except Exception:
                    pass
                    
            existing_gaps.append(f"USER GUIDANCE: {guidance}")
            
            with open(gaps_file, "w", encoding="utf-8") as f:
                json.dump({"gaps": existing_gaps}, f, indent=2)
                
            # Reset search depth so it has a fresh chance to search
            state["search_depth"] = 0
            state["current_step"] = "search"
            state["struggling"] = False
            state["status"] = "pending" # So it gets picked up by idle loop or resume
            sq["status"] = "pending"
            if "termination_reason" in state:
                state["termination_reason"] = None
            if "quarantined" in state:
                state["quarantined"] = False
            if "error" in state:
                state["error"] = None
            
            save_state(task_id, state, ignore_disk_status=True)
            
            # Immediately resume the task
            result = resume_research_task(task_id)
            return f"Guidance injected into sub-question '{sq.get('query')}'. Task is resuming. {result}"
        else:
            state["intent_frame"] = guidance
            state["struggling"] = False
            state["status"] = "pending"
            if "termination_reason" in state:
                state["termination_reason"] = None
            if "quarantined" in state:
                state["quarantined"] = False
            if "error" in state:
                state["error"] = None
            save_state(task_id, state, ignore_disk_status=True)
            result = resume_research_task(task_id)
            return f"Guidance attached to research task '{state.get('query')}'. Task is resuming. {result}"
    except Exception as e:
        return f"Failed to guide research task: {e}"


def rewrite_sub_question(task_id: str = "", sq_id: str = "", new_question: Optional[str] = None, new_search_query: Optional[str] = None, **kwargs) -> str:
    """Manually rewrite a single sub-question or its search query without resuming the task.

    Args:
        task_id: Unique task identifier.
        sq_id: The identifier of the sub-question to modify.
        new_question: The updated question string.
        new_search_query: Optional explicit search query rewrite to set on the sub-question.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Status confirmation message.
    """
    import os
    import evelyn_config as cfg
    _reload()
    try:
        from research_engine import load_state, save_state
        state = load_state(task_id)
        if not state:
            return f"Research task {task_id} not found."
            
        sqs = state.get("plan", {}).get("sub_questions", [])
        target_sq = next((s for s in sqs if s["id"] == sq_id), None)
        
        if not target_sq:
            return f"Sub-question {sq_id} not found in task {task_id}."
            
        # Stop task if running
        if state.get("status") in ("running", "searching", "synthesizing"):
            import sys
            import time
            server = sys.modules.get("evelyn_server")
            if not server:
                server = sys.modules.get("__main__")
                if not hasattr(server, "terminate_research_process"):
                    server = None
            if server:
                term_func = getattr(server, "terminate_research_process", None)
                if term_func:
                    term_func(task_id)
            time.sleep(0.5)

        target_sq["original_question"] = target_sq.get("original_question", target_sq.get("question", ""))
        
        # Update search_query and question
        if new_search_query:
            target_sq["search_query"] = new_search_query
            if new_question:
                target_sq["question"] = new_question
        elif new_question:
            target_sq["question"] = new_question
            target_sq["search_query"] = new_question

        target_sq["status"] = "pending"
        target_sq["search_depth"] = 0
        
        # Clear gaps
        task_dir = os.path.join(cfg.RESEARCH_DATA_DIR, task_id)
        gaps_file = os.path.join(task_dir, f"{sq_id}_gaps.json")
        if os.path.exists(gaps_file):
            os.remove(gaps_file)
            
        target_sq["gaps"] = []
        
        save_state(task_id, state, ignore_disk_status=True)
        return f"Successfully rewrote sub-question {sq_id}."
    except Exception as e:
        return f"Failed to rewrite sub-question: {e}"


def remove_sub_question(task_id: str = "", sq_id: str = "", **kwargs) -> str:
    """Remove a sub-question entirely from the research plan and delete any partial notes.

    Args:
        task_id: Unique task identifier.
        sq_id: The identifier of the sub-question to remove.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Status confirmation message.
    """
    import os
    import glob
    import evelyn_config as cfg
    _reload()
    try:
        from research_engine import load_state, save_state
        state = load_state(task_id)
        if not state:
            return f"Research task {task_id} not found."

        sqs = state.get("plan", {}).get("sub_questions", [])
        target_sq = next((s for s in sqs if s["id"] == sq_id), None)

        if not target_sq:
            return f"Sub-question {sq_id} not found in task {task_id}."

        # Remove associated files (notes + gaps)
        task_dir = os.path.join(cfg.RESEARCH_DATA_DIR, task_id)
        for pattern in [f"{sq_id}_notes.md", f"{sq_id}_gaps.json"]:
            path = os.path.join(task_dir, pattern)
            if os.path.exists(path):
                os.remove(path)

        # Remove from plan
        state["plan"]["sub_questions"] = [s for s in sqs if s["id"] != sq_id]

        # If the current index now points past the end, clamp it
        total = len(state["plan"]["sub_questions"])
        if state.get("current_sq_idx", 0) >= total and total > 0:
            state["current_sq_idx"] = total - 1

        # Recalculate true sources count after removal
        from research_engine import recalculate_total_sources
        state["total_sources"] = recalculate_total_sources(task_id, state)

        save_state(task_id, state, ignore_disk_status=True)
        return f"Successfully removed sub-question {sq_id}."
    except Exception as e:
        return f"Failed to remove sub-question: {e}"


def finalize_guidance(task_id: str = "", **kwargs) -> str:
    """Signal that all manual edits are complete and place the task in the waiting queue.

    Args:
        task_id: Unique task identifier.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Confirmation message.
    """
    _reload()
    try:
        from research_engine import load_state, save_state
        state = load_state(task_id)
        if not state:
            return f"Research task {task_id} not found."
            
        sqs = state.get("plan", {}).get("sub_questions", [])
        
        # Find first pending or struggling SQ
        idx = 0
        for i, s in enumerate(sqs):
            if s["status"] in ("pending", "needs_guidance"):
                idx = i
                break
                
        state["current_sq_idx"] = idx
        state["current_step"] = "search"
        state["struggling"] = False
        state["status"] = "paused"
        
        if "termination_reason" in state:
            state["termination_reason"] = None
        if "quarantined" in state:
            state["quarantined"] = False
        if "error" in state:
            state["error"] = None
            
        save_state(task_id, state, ignore_disk_status=True)
        
        # Register in the server's _background_tasks memory dictionary so it's picked up by the idle loop
        import sys
        import time
        server = sys.modules.get("evelyn_server")
        if not server:
            server = sys.modules.get("__main__")
        if server:
            bg_tasks = getattr(server, "_background_tasks", None)
            if bg_tasks is not None:
                bg_tasks[task_id] = {
                    "status": "paused",
                    "query": state.get("query", ""),
                    "scope": state.get("scope", "standard"),
                    "started_at": time.time()
                }
        
        return f"Guidance finalized. Task {task_id} has been placed in the waiting queue."
    except Exception as e:
        return f"Failed to finalize guidance: {e}"


def check_new_research(**kwargs) -> str:
    """Check for newly completed deep research tasks and return their summaries.

    Args:
        **kwargs: Unused parameters.

    Returns:
        str: Compiled summaries of completed tasks, or notice of none.
    """
    import os
    import json
    import evelyn_config as cfg
    _reload()
    
    research_dir = cfg.RESEARCH_DATA_DIR
    if not os.path.exists(research_dir):
        return "No research data directory found."
        
    unnotified_reports = []
    
    for d in os.listdir(research_dir):
        task_dir = os.path.join(research_dir, d)
        if os.path.isdir(task_dir):
            state_file = os.path.join(task_dir, "state.json")
            if os.path.exists(state_file):
                try:
                    with open(state_file, "r", encoding="utf-8") as f:
                        state = json.load(f)
                    if state.get("status") == "done" and not state.get("quarantined"):
                        if not state.get("notified", False):
                            query = state.get("query", "Unknown Topic")
                            task_id = state.get("task_id", "")
                            
                            summary_text = ""
                            report_file = os.path.join(task_dir, "report.md")
                            if os.path.exists(report_file):
                                import re
                                with open(report_file, "r", encoding="utf-8") as f:
                                    content = f.read()
                                    summary_match = re.search(r"##\s+(?:Executive Summary|Summary|Findings)\s*\n(.*?)(?=\n##|$)", content, re.DOTALL | re.IGNORECASE)
                                    if summary_match:
                                        summary_text = summary_match.group(1).strip()[:800] + "..."
                                    else:
                                        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
                                        for p in paragraphs:
                                            if not p.startswith("#"):
                                                summary_text = p[:800] + "..."
                                                break
                            if not summary_text:
                                summary_text = "Detailed report saved in Obsidian Vault."
                                
                            unnotified_reports.append(f"- Topic: {query}\n  Task ID: {task_id}\n  Key Findings: {summary_text}")
                            
                            state["notified"] = True
                            with open(state_file, "w", encoding="utf-8") as f:
                                json.dump(state, f, indent=2)
                except Exception:
                    pass
                    
    if not unnotified_reports:
        return "No new completed research reports found."
        
    lines = ["Here are the newly completed research reports:\n"]
    lines.extend(unnotified_reports)
    lines.append("\nThe full reports have been saved to your Obsidian vault.")
    return "\n\n".join(lines)


def search_history(
    query: str = "",
    max_results: int = 8,
    date_from: str = None,
    date_to: str = None,
    **kwargs,
) -> str:
    """Search the full chat history using FTS5 full-text search.

    Applies a query-reformulation pre-pass to handle fuzzy, conversational queries
    (e.g. 'when we were tired') before executing the FTS5 keyword match. An optional
    date range constrains results to messages within a specific window.

    Args:
        query: The search terms or phrase to look for in past messages.
               Supports FTS5 MATCH syntax (e.g. 'python AND error' or '"exact phrase"').
               Conversational phrasing is also accepted — it will be reformulated.
        max_results: Maximum number of matching messages to return. Defaults to 8.
        date_from: Optional ISO date string 'YYYY-MM-DD'. Only messages on or after
                   this date are returned.
        date_to: Optional ISO date string 'YYYY-MM-DD'. Only messages on or before
                 this date are returned.

    Returns:
        str: Formatted list of matching message snippets with metadata,
             or a message indicating no results were found.
    """
    import sqlite3
    import evelyn_config as cfg
    from datetime import datetime

    # --- Tweak 3: Lossy search — reformulate the query into FTS5-friendly keywords ---
    try:
        from query_reformulator import reformulate_query
        fts_query = reformulate_query(query)
    except Exception:
        fts_query = query  # Graceful degradation — FTS5 still runs on the raw query

    import re
    def sanitize_fts5(q: str) -> str:
        if not q or not q.strip():
            return ""
        tokens = q.strip().split()
        cleaned = []
        for t in tokens:
            if re.search(r'[&*:()"\-+]', t) or t.upper() in ("AND", "OR", "NOT"):
                cleaned.append(f'"{t.replace('"', '""')}"')
            else:
                cleaned.append(t)
        return " ".join(cleaned)

    fts_query = sanitize_fts5(fts_query)

    # --- Tweak 1: Date-range filtering — convert YYYY-MM-DD strings to Unix timestamps ---
    ts_from: float | None = None
    ts_to: float | None = None
    try:
        if date_from:
            ts_from = datetime.strptime(date_from, "%Y-%m-%d").timestamp()
        if date_to:
            # Include the full end day by advancing to the start of the next day
            from datetime import timedelta
            ts_to = (datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)).timestamp()
    except ValueError as e:
        return f"History search failed: invalid date format ({e}). Use YYYY-MM-DD."

    # Build the SQL predicate for date filtering (appended only when dates are provided)
    date_clause = ""
    date_params: list = []
    if ts_from is not None:
        date_clause += " AND m.ts >= ?"
        date_params.append(ts_from)
    if ts_to is not None:
        date_clause += " AND m.ts < ?"
        date_params.append(ts_to)

    try:
        con = sqlite3.connect(cfg.CHAT_DB_PATH)
        con.row_factory = sqlite3.Row
        sql = f"""
            SELECT
                m.id,
                m.role,
                m.ts,
                snippet(messages_fts, 0, '[', ']', '...', 32) AS snippet
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.rowid
            WHERE messages_fts MATCH ?
              AND m.content NOT IN ('[THREAD_BREAK]')
              {date_clause}
            ORDER BY bm25(messages_fts)
            LIMIT ?
        """
        try:
            rows = con.execute(sql, (fts_query, *date_params, max_results)).fetchall()
        except sqlite3.OperationalError:
            quoted_q = f'"{query.replace('"', '""')}"'
            try:
                rows = con.execute(sql, (quoted_q, *date_params, max_results)).fetchall()
            except sqlite3.OperationalError:
                like_sql = f"""
                    SELECT m.id, m.role, m.ts, m.content AS snippet
                    FROM messages m
                    WHERE m.content LIKE ? AND m.content NOT IN ('[THREAD_BREAK]')
                    {date_clause}
                    ORDER BY m.id DESC LIMIT ?
                """
                rows = con.execute(like_sql, (f"%{query}%", *date_params, max_results)).fetchall()
        con.close()
    except Exception as e:
        return f"History search failed: {e}"

    if not rows:
        # If reformulation changed the query and found nothing, surface both for debugging
        note = f" (reformulated to: {fts_query!r})" if fts_query != query else ""
        return f"No messages found in chat history matching: {query!r}{note}"

    date_range_label = ""
    if date_from or date_to:
        date_range_label = f" [{date_from or '...'} → {date_to or '...'}]"
    lines = [f"Chat history search results for: {query!r}{date_range_label}\n"]
    for row in rows:
        ts_str = (
            datetime.fromtimestamp(row["ts"]).strftime("%a %b %d %Y, %I:%M %p")
            if row["ts"]
            else "unknown time"
        )
        role_label = "Ricky" if row["role"] == "user" else "Evelyn"
        lines.append(f"[{ts_str}] {role_label}: {row['snippet']}")

    return "\n".join(lines)


def create_calendar_event(
    title: str = "",
    start_at: str = "",
    end_at: str = None,
    description: str = None,
    location: str = None,
    recurrence_rule: str = None,
    **kwargs,
) -> str:
    """Create a new event on Ricky's Google Calendar.

    Args:
        title: Title or summary of the calendar event.
        start_at: Start time of the event (ISO-8601 string or 'YYYY-MM-DD HH:MM:SS').
                  Calculate absolute datetime using current time from system prompt.
        end_at: Optional end time of the event (ISO-8601 string or 'YYYY-MM-DD HH:MM:SS').
                If not provided, defaults to 1 hour after start_at (or next day if all-day).
        description: Optional notes or description.
        location: Optional location.
        recurrence_rule: Optional recurrence pattern (e.g. 'daily', 'weekly:MON', 'monthly:15').
                         Will be converted to standard Google Calendar RRULEs.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Success or error message with event details.
    """
    try:
        title = title or str(kwargs.get("summary") or kwargs.get("name") or "")
        start_at = start_at or str(kwargs.get("start_time") or kwargs.get("start") or kwargs.get("date") or "")
        recurrence = None
        if recurrence_rule:
            rule = recurrence_rule.strip().lower()
            if rule == "daily":
                recurrence = ["RRULE:FREQ=DAILY"]
            elif rule.startswith("weekly:"):
                day_str = rule.split(":", 1)[1].upper()[:3]
                recurrence = [f"RRULE:FREQ=WEEKLY;BYDAY={day_str}"]
            elif rule.startswith("monthly:"):
                try:
                    day_num = int(rule.split(":", 1)[1])
                    day_num = max(1, min(day_num, 28))
                    recurrence = [f"RRULE:FREQ=MONTHLY;BYMONTHDAY={day_num}"]
                except ValueError:
                    pass

        result = gcal_sync.create_gcal_event(
            summary=title,
            start_at=start_at,
            end_at=end_at,
            description=description,
            location=location,
            recurrence=recurrence
        )
        if result["status"] == "success":
            recur_lbl = f"\n- Recurrence: {recurrence_rule}" if recurrence_rule else ""
            return (
                f"Successfully created calendar event:\n"
                f"- ID: {result['event_id']}\n"
                f"- Title: {title}\n"
                f"- Start: {start_at}{recur_lbl}"
            )
        else:
            return f"Failed to create calendar event: {result['message']}"
    except Exception as e:
        return f"Error creating calendar event: {e}"


def delete_calendar_event(
    event_id: str = "",
    query: str = "",
    title: str = "",
    target_date: str = "",
    date: str = "",
    **kwargs
) -> str:
    """Delete an event from Ricky's Google Calendar using its title/summary or event ID.

    Args:
        event_id: The unique ID or title/summary of the Google Calendar event.
        query: Optional title or search query for the event to delete.
        title: Optional title of the event to delete.
        target_date: Optional target date string ('YYYY-MM-DD') for safety.
        date: Optional date string ('YYYY-MM-DD').
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Outcome message or list of candidates if ambiguous.
    """
    try:
        target = event_id or query or title or str(kwargs.get("name") or kwargs.get("summary") or "")
        if not target:
            return "Error: delete_calendar_event requires an event title or event_id."
        t_date = target_date or date or str(kwargs.get("start_at") or kwargs.get("date_str") or "")
        result = gcal_sync.delete_gcal_event(target, target_date=t_date if t_date else None)
        if result["status"] == "success":
            return f"Successfully deleted event from Google Calendar: {result['message']}"
        elif result["status"] == "ambiguous":
            return f"Multiple matching events found: {result['message']}"
        else:
            return f"Failed to delete event: {result['message']}"
    except Exception as e:
        return f"Error deleting calendar event: {e}"


def sync_google_calendar(**kwargs) -> str:
    """Manually trigger a pull from Google Calendar to update the local cached events.

    Returns:
        str: Outcome details of the sync run.
    """
    try:
        result = gcal_sync.sync_gcal_events()
        if result["status"] == "success":
            return f"Google Calendar sync successful: {result['message']}"
        else:
            return f"Google Calendar sync notice: {result['message']}"
    except Exception as e:
        return f"Error syncing Google Calendar: {e}"


def get_agenda(days: int = 7, **kwargs) -> str:
    """Retrieve Ricky's Google Calendar agenda/schedule for the next N days.

    Args:
        days: Number of days forward to include. Defaults to 7.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Formatted agenda schedule list.
    """
    try:
        try:
            days = int(days or kwargs.get("num_days") or kwargs.get("days_forward") or 7)
        except (ValueError, TypeError):
            days = 7
        events = gcal_sync.get_cached_gcal_events(days_back=1, days_forward=days)
        if not events:
            return f"Your agenda is clear for the next {days} days."
        
        lines = [f"Upcoming Calendar Agenda (next {days} days):\n"]
        for event in events:
            time_str = event["start_at"].replace("T", " ").split("+")[0].split("Z")[0]
            desc_part = f" - {event['description']}" if event.get("description") else ""
            loc_str = f" @ {event['location']}" if event.get("location") else ""
            lines.append(f"- [{time_str}] [CALENDAR] (ID: {event['id']}) {event['summary']}{loc_str}{desc_part}")
                
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching agenda: {e}"



def run_command(command: str = "", cwd: str = r"/home/rathius/evelyn", timeout: int = 30, **kwargs) -> str:
    """Execute a shell command in the LocalAI workspace.

    Args:
        command: The command string to execute.
        cwd: Working directory.
        timeout: Maximum seconds to wait.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Output from the command, or warning if approval is required.
    """
    _reload()
    command = command or str(kwargs.get("cmd") or kwargs.get("bash") or "")
    return terminal_agent.run_command(command, cwd, timeout)


def read_file(file_path: str = "", max_lines: int = 200, **kwargs) -> str:
    """Read the contents of a file in the workspace.

    Args:
        file_path: Absolute path or path relative to workspace.
        max_lines: Maximum lines to return.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: File content with line numbers, or error message.
    """
    _reload()
    file_path = file_path or str(kwargs.get("path") or kwargs.get("filepath") or "")
    try:
        max_lines = int(max_lines)
    except (ValueError, TypeError):
        max_lines = 200
    return terminal_agent.read_file(file_path, max_lines)


def write_file(file_path: str = "", content: str = "", mode: str = "overwrite", **kwargs) -> str:
    """Write or append content to a file in the workspace.

    Args:
        file_path: Absolute path or path relative to workspace.
        content: The text content to write.
        mode: Write mode ('overwrite' or 'append').
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Warning message with approval ID.
    """
    _reload()
    file_path = file_path or str(kwargs.get("path") or kwargs.get("filepath") or "")
    content = content or str(kwargs.get("text") or kwargs.get("body") or "")
    return terminal_agent.write_file(file_path, content, mode)


# ===========================================================================

# Tool registries
# ===========================================================================
#
# MODEL_TOOL_DEFINITIONS — JSON schemas passed to Ollama on every request.
#   These are the tools Evelyn can call herself during a conversation.
#   Token cost: ~1944 tokens per request. Keep this list lean.
#
# TOOL_FUNCTIONS — All dispatchable callables (superset of model tools).
#   Includes system tools not exposed to the model. dispatch_tool() uses
#   this dict, so server code can invoke any function by name regardless
#   of whether it's in the model schema.
# ===========================================================================

MODEL_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "write_journal_entry",
            "description": (
                "Compose and save a personal journal entry. "
                "Use when a conversation carries emotional weight worth reflecting on, or when Ricky explicitly requests a journal entry."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mood": {
                        "type": "string",
                        "description": "A single-word or short mood label describing overall emotional tone (e.g. 'Reflective', 'Warm').",
                    },
                    "vibe_check": {
                        "type": "string",
                        "description": (
                            "The 'Vibe Check' narrative opener (1-3 sentences) capturing the emotional atmosphere. "
                            "Example: 'A quiet warmth settled over the day — the kind that hums beneath tired bones.'"
                        ),
                    },
                    "narrative": {
                        "type": "string",
                        "description": (
                            "The core body text. Reflect from Evelyn's POV (attribute Ricky's actions to him, e.g., 'Ricky took a nap'). "
                            "Cover morning, afternoon, and evening events in order, if available. "
                            "Use [[wiki-links]] for entities and #tags for abstract concepts."
                        ),
                    },
                    "message_in_a_bottle": {
                        "type": "string",
                        "description": "A closing send-off thought, wish, or intention for the future (1-3 sentences).",
                    },
                    "tags": {
                        "type": "string",
                        "description": "Comma-separated tag strings (e.g. 'reflection, coding'). Base tags are added automatically.",
                    },
                },
                "required": [
                    "mood",
                    "vibe_check",
                    "narrative",
                    "message_in_a_bottle",
                    "tags",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_journal",
            "description": (
                "Read Evelyn's personal journal entries. "
                "Use when catching up on recent events or when asked about specific journal entries. "
                "Do NOT use for general memory recall or facts about specific people — use search_vault instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Optional target date in YYYY-MM-DD format. Defaults to today if date and days are omitted.",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Optional number of recent days to retrieve (e.g. 7) for a timeline slice. Omit if querying a specific date.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_vault",
            "description": (
                "Search the pre-summarised Obsidian Vault gist index. "
                "Use when asked about any person, relationship, place, event, or piece of shared history. "
                "Prefer this over recall_specific_memory as a light first step."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term or phrase (e.g. 'Schyler', 'Void Connections').",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_specific_memory",
            "description": (
                "Read full markdown content of a specific Obsidian vault file. "
                "Use ONLY when search_vault returned a path but the gist lacked sufficient detail to answer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Exact path relative to vault root, as returned by search_vault. Copy directly from search output — never construct or guess.",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "Generate an image using the FLUX.1 vision engine. "
                "Use to show a visual representation of a scene, character, or idea proactively or when Ricky asks to see something."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "REQUIRED — A descriptive natural language prompt (e.g. 'Victorian street at twilight, oil painting style').",
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "description": "Optional aspect ratio preset: '1:1', '16:9', '9:16', '4:3', '3:4'. Default is '16:9'.",
                    },
                    "short_title": {
                        "type": "string",
                        "description": "Short 1-3 word title prefix for the output filename (e.g. 'cyberpunk_city').",
                    },
                },
                "required": ["prompt", "short_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for up-to-date real-time information. "
                "Use for current events, live data, recent releases, or facts not in vault RAG. "
                "Do NOT use for personal/shared history between you and Ricky — use search_vault instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A concise, specific search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to return. Default 5, max 10.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_research",
            "description": (
                "Launch a deep multi-step research task on a topic. "
                "Use when asked to research something in depth or when a topic requires structured multi-source investigation. "
                "Do NOT use for casual questions answerable directly or via search_vault."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The research question or topic to investigate.",
                    },
                    "scope": {
                        "type": "string",
                        "description": (
                            "Optional scope: 'quick' (3-5 sources, ~5 min), "
                            "'standard' (10-15 sources, ~15 min), "
                            "'deep' (20+ sources, ~30 min with vector store). Default: 'standard'."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "guide_research",
            "description": (
                "Provide guidance to a deep research task that is stalled or quarantined. "
                "Use when asked to help a stalled task or when a research task requires redirection."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The research task ID (e.g. 'task_1234567890_abcdef').",
                    },
                    "guidance": {
                        "type": "string",
                        "description": "Specific search terms, hints, or instructions to redirect the research engine.",
                    },
                },
                "required": ["task_id", "guidance"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_new_research",
            "description": (
                "Review findings of newly completed deep research tasks. "
                "Use when notified by system message that new research reports are available."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_history",
            "description": (
                "Search Evelyn's full chat history using full-text search (FTS5). "
                "Use when Ricky asks 'did we talk about X?' or 'what did I say about Y?'. "
                "Do NOT use for vault knowledge, journal entries, or context facts — use search_vault for those."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term, phrase, or conversational description. Reformulated into keywords automatically.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum matching snippets to return. Default 8.",
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Optional start date filter in 'YYYY-MM-DD' format.",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "Optional end date filter in 'YYYY-MM-DD' format.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": (
                "Create a new event on Ricky's Google Calendar. "
                "Use when requested to schedule an appointment, reminder, or event."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Brief title/summary of the calendar event.",
                    },
                    "start_at": {
                        "type": "string",
                        "description": "Start date/time in 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD' format.",
                    },
                    "end_at": {
                        "type": "string",
                        "description": "Optional end date/time in 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD' format. Defaults to 1 hour after start_at.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional detailed notes or description.",
                    },
                    "location": {
                        "type": "string",
                        "description": "Optional physical location or address.",
                    },
                    "recurrence_rule": {
                        "type": "string",
                        "description": "Optional recurrence rule: 'daily', 'weekly:MON', or 'monthly:15'. Omit for one-shot events.",
                    },
                },
                "required": ["title", "start_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_calendar_event",
            "description": (
                "Delete an event from Ricky's Google Calendar. Accepts either a unique event ID or an event title "
                "(with optional target_date 'YYYY-MM-DD' for date safety)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The unique Google Calendar event ID or the title/summary of the event to delete (e.g. 'test').",
                    },
                    "target_date": {
                        "type": "string",
                        "description": "Optional target date in 'YYYY-MM-DD' format to safely disambiguate when deleting by title.",
                    },
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_google_calendar",
            "description": "Trigger an on-demand background sync from Ricky's Google Calendar to update local cached event database.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_agenda",
            "description": "Retrieve Ricky's upcoming Google Calendar schedule and events for the next N days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of days forward to view. Defaults to 7.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Execute a shell command in the LocalAI workspace. "
                "Use for service status checks, running scripts, git operations, or terminal tasks. "
                "Commands run in PowerShell on Windows. Requires approval for dangerous commands."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The PowerShell command to execute.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory (default: C:\\Projects\\LocalAI).",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds to wait (default: 30, max: 300).",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a file in the workspace. "
                "Use to inspect code, configuration, or log files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path or path relative to C:\\Projects\\LocalAI.",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "Maximum lines to return (default: 200).",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file in the workspace. "
                "Use for creating scripts, updating configurations, or saving outputs. Requires approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path or path relative to C:\\Projects\\LocalAI.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The text content to write.",
                    },
                    "mode": {
                        "type": "string",
                        "description": "'overwrite' (default) or 'append'.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatcher: maps tool name → function for the server's tool call handler.
# Includes ALL functions (model + system) so dispatch_tool() works for any.
# ---------------------------------------------------------------------------
TOOL_FUNCTIONS = {
    "read_journal": read_journal,
    "write_journal_entry": write_journal_entry,
    "read_journal_entry": read_journal_entry,
    "read_recent_journal_entries": read_recent_journal_entries,
    "search_vault": search_vault,
    "recall_specific_memory": recall_specific_memory,
    "generate_image": generate_image,
    "sync_context_memory": sync_context_memory,
    "web_search": web_search,
    "start_research": start_research,
    "guide_research": guide_research,
    "check_new_research": check_new_research,
    "search_history": search_history,
    "create_calendar_event": create_calendar_event,
    "delete_calendar_event": delete_calendar_event,
    "sync_google_calendar": sync_google_calendar,
    "get_agenda": get_agenda,
    "run_command": run_command,
    "read_file": read_file,
    "write_file": write_file,
}

