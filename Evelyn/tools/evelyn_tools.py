# evelyn_tools.py
# date created: 2026-03-23 15:38:53
# date modified: 2026-08-20 20:49:45
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
from typing import Any, Optional


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
import ingest_obsidian_knowledge # [[ingest_obsidian_knowledge.py]]
import gcal_sync
import gtasks_sync
import vault_list_manager
import terminal_agent
import gdrive_sync
import health_manager
import oura_client


def _reload():
    """Hot-reload all backing modules so live edits take effect without restarting."""
    if os.environ.get("PYTEST_CURRENT_TEST") or getattr(cfg, "DISABLE_HOT_RELOAD", False):
        return
    for mod in (
        "journal_manager",
        "context_manager",
        "ingest_obsidian_knowledge",
        "gcal_sync",
        "gtasks_sync",
        "vault_list_manager",
        "terminal_agent",
        "gdrive_sync",
        "health_manager",
        "oura_client",
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
    """Compose and queue a new journal entry for user review.

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


DEPRECATION_LOG_FILE = os.path.join(getattr(cfg, "BASE_DIR", r"/home/rathius/evelyn"), "data", "deprecation_warnings.log")

def _log_deprecation(func_name: str, args_summary: str = "") -> None:
    """Log prominent warning to console and append to deprecation_warnings.log with traceback."""
    import time
    import traceback
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    tb = "".join(traceback.format_stack()[:-1])  # Exclude current call frame
    
    warning_msg = f"[DEPRECATION WARNING] {timestamp} - Function '{func_name}' was called with args ({args_summary}). Static read tools are deprecated in favor of full-vault Chroma RAG."
    print(f"\033[93m{warning_msg}\033[0m", flush=True)
    
    try:
        os.makedirs(os.path.dirname(DEPRECATION_LOG_FILE), exist_ok=True)
        with open(DEPRECATION_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"--- {timestamp} ---\n{warning_msg}\nTraceback:\n{tb}\n\n")
    except Exception as e:
        print(f"[DEPRECATION LOG ERROR] Could not write to log: {e}", flush=True)


def read_journal(date: str = "", days: int = 0, **kwargs) -> str:
    """[DEPRECATED] Read Evelyn's personal journal entries.
    
    All journal entries are now indexed full-text in Chroma RAG context.
    """
    _log_deprecation("read_journal", f"date='{date}', days={days}")
    return "[NOTICE] 'read_journal' is deprecated. All journal entries are indexed full-text in Chroma RAG context automatically."


def read_journal_entry(date: str = "", **kwargs) -> str:
    """[DEPRECATED] Read a single journal entry by date."""
    _log_deprecation("read_journal_entry", f"date='{date}'")
    return "[NOTICE] 'read_journal_entry' is deprecated. All journal entries are indexed full-text in Chroma RAG context automatically."


def read_recent_journal_entries(days: int = 7, **kwargs) -> str:
    """[DEPRECATED] Read recent journal entries."""
    _log_deprecation("read_recent_journal_entries", f"days={days}")
    return "[NOTICE] 'read_recent_journal_entries' is deprecated. All journal entries are indexed full-text in Chroma RAG context automatically."


def search_vault(query: str = "", **kwargs) -> str:
    """[DEPRECATED] Search Obsidian Vault gist index.
    
    Full vault text across all 1,202 notes is indexed directly in Chroma RAG context.
    """
    query = query or str(kwargs.get("search_query") or kwargs.get("search_term") or kwargs.get("term") or "")
    _log_deprecation("search_vault", f"query='{query}'")
    return f"[NOTICE] 'search_vault' is deprecated. The entire vault is indexed in Chroma RAG context automatically. Relevant content for query '{query}' is already supplied in context."


def recall_specific_memory(file_path: str = "", **kwargs) -> str:
    """[DEPRECATED] Read full markdown content of a specific Obsidian vault file.
    
    All vault files are indexed in Chroma RAG. For workspace files, use read_file.
    """
    file_path = file_path or str(kwargs.get("filepath") or kwargs.get("path") or kwargs.get("file") or "")
    _log_deprecation("recall_specific_memory", f"file_path='{file_path}'")
    return f"[NOTICE] 'recall_specific_memory' is deprecated. Vault files are indexed in Chroma RAG context. For non-vault code/workspace files, use 'read_file'."


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
    import os
    import requests
    from pathlib import Path
    from evelyn_config import IMAGE_SERVER_URL, IMAGE_OUTPUT_DIR

    prompt = prompt or str(kwargs.get("description") or kwargs.get("image_prompt") or kwargs.get("p") or "")
    if not prompt.strip():
        return "Error: generate_image called with an empty prompt. Please provide a prompt description."

    try:
        payload = {
            "prompt": prompt.strip(),
            "aspect_ratio": aspect_ratio,
        }
        if short_title or kwargs.get("title"):
            payload["short_title"] = short_title or kwargs.get("title")
        if seed is not None:
            payload["seed"] = seed

        url = f"{IMAGE_SERVER_URL.rstrip('/')}/generate"
        resp = requests.post(url, json=payload, timeout=600)
        if resp.status_code != 200:
            return f"Error from Image Engine: {resp.text}"

        result = resp.json()
        filename = result["filename"]
        
        # Download and cache the image locally if server is remote
        try:
            download_url = f"{IMAGE_SERVER_URL.rstrip('/')}/images/{filename}"
            img_resp = requests.get(download_url, timeout=30)
            if img_resp.status_code == 200:
                local_dir = Path(IMAGE_OUTPUT_DIR)
                local_dir.mkdir(parents=True, exist_ok=True)
                local_path = local_dir / filename
                with open(local_path, "wb") as f:
                    f.write(img_resp.content)
        except Exception as dl_err:
            print(f"[IMAGE] Warning: Could not cache remote image locally ({dl_err}). Serving via remote host.")

        # Served statically via the main evelyn_server mount
        image_url = f"/images/{filename}"
        return f"Image generated successfully at {image_url}."
    except Exception as e:
        return f"Failed to generate image via FLUX.1 server at {IMAGE_SERVER_URL}: {e}"


def sync_context_memory(**kwargs) -> str:
    """Trigger background sync of core vault memory and context entries into Chroma.

    Args:
        **kwargs: Unused parameters.

    Returns:
        str: Status message indicating start.
    """
    import threading

    def _run():
        """Run sync_context_memory in a daemon thread."""
        _reload()
        try:
            print("Sync: Starting core memory ingest...")
            ingest_obsidian_knowledge.main()
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
                                f"'{disk_query}' (Task ID: {folder}). {cfg.USER_NAME} can read the synthesized report "
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
                f"unfinished tasks are resolved. Please inform {cfg.USER_NAME} that the topic has been successfully queued."
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
                    try:
                        import task_manager
                        task_manager.register_subprocess(proc)
                        task_manager._active_handles[task_id] = proc
                    except Exception:
                        pass
                    if server:
                        active_procs = getattr(server, "_active_research_processes", None)
                        if active_procs is not None:
                            active_procs[task_id] = proc
                            
                    returncode = proc.wait()
                    
                    try:
                        import task_manager
                        task_manager.unregister_subprocess(proc)
                        task_manager._active_handles.pop(task_id, None)
                    except Exception:
                        pass
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
                        if disk_status in ("paused", "cancelled", "needs_guidance"):
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
                    try:
                        import task_manager
                        task_manager.register_subprocess(proc)
                        task_manager._active_handles[task_id] = proc
                    except Exception:
                        pass
                    if server:
                        active_procs = getattr(server, "_active_research_processes", None)
                        if active_procs is not None:
                            active_procs[task_id] = proc
                            
                    returncode = proc.wait()
                    
                    try:
                        import task_manager
                        task_manager.unregister_subprocess(proc)
                        task_manager._active_handles.pop(task_id, None)
                    except Exception:
                        pass
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
                        if disk_status in ("paused", "cancelled", "needs_guidance"):
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


def _scan_research_tasks() -> list[dict]:
    """Scan cfg.RESEARCH_DATA_DIR and return all task states sorted by updated_at / created_at desc."""
    import os
    import json
    import evelyn_config as cfg
    tasks = []
    if not os.path.exists(cfg.RESEARCH_DATA_DIR):
        return tasks
    for folder in os.listdir(cfg.RESEARCH_DATA_DIR):
        task_dir = os.path.join(cfg.RESEARCH_DATA_DIR, folder)
        if os.path.isdir(task_dir):
            state_file = os.path.join(task_dir, "state.json")
            if os.path.exists(state_file):
                try:
                    with open(state_file, "r", encoding="utf-8") as f:
                        st = json.load(f)
                        if "task_id" not in st:
                            st["task_id"] = folder
                        tasks.append(st)
                except Exception:
                    pass
    tasks.sort(key=lambda t: t.get("updated_at") or t.get("created_at") or "", reverse=True)
    return tasks


def _resolve_research_task_id(task_id: str = "", query: str = "") -> tuple[Optional[str], Optional[dict], Optional[str]]:
    """Resolve a task_id or query string to a specific task state.

    Returns:
        (resolved_task_id, state_dict, error_or_clarification_message)
    """
    tasks = _scan_research_tasks()
    if not tasks:
        return None, None, "No research tasks found in the system."

    clean_id = (task_id or "").strip()
    clean_query = (query or "").strip().lower()

    # 1. Exact task_id match
    if clean_id:
        for t in tasks:
            if t.get("task_id") == clean_id:
                return clean_id, t, None
        # Prefix or substring match on task_id
        matching_ids = [t for t in tasks if clean_id.lower() in t.get("task_id", "").lower()]
        if len(matching_ids) == 1:
            return matching_ids[0]["task_id"], matching_ids[0], None
        elif len(matching_ids) > 1:
            opts = "\n".join(f"- `{t['task_id']}`: {t.get('query', 'Unknown')}" for t in matching_ids)
            return None, None, f"Multiple tasks match ID '{clean_id}':\n{opts}"

    # 2. Query / Topic matching
    if clean_query:
        matches = []
        for t in tasks:
            q_text = (t.get("query") or t.get("original_question") or "").lower()
            aliases = " ".join(t.get("topic_aliases") or []).lower()
            tags = " ".join(t.get("topic_tags") or []).lower()
            if clean_query in q_text or clean_query in aliases or clean_query in tags:
                matches.append(t)
            elif all(term in q_text or term in aliases or term in tags for term in clean_query.split() if len(term) > 2):
                matches.append(t)
        if len(matches) == 1:
            return matches[0]["task_id"], matches[0], None
        elif len(matches) > 1:
            # If multiple match, check if only one is stalled/struggling
            stalled_matches = [
                t for t in matches
                if t.get("status") in ("needs_guidance", "quarantined")
                or t.get("struggling")
                or any(sq.get("status") == "needs_guidance" for sq in t.get("plan", {}).get("sub_questions", []))
            ]
            if len(stalled_matches) == 1:
                return stalled_matches[0]["task_id"], stalled_matches[0], None
            opts = "\n".join(f"- `{t['task_id']}` ({t.get('status')}): {t.get('query', 'Unknown')}" for t in matches)
            return None, None, f"Multiple tasks match query '{query}':\n{opts}\nPlease specify the exact task_id."

    # 3. If neither task_id nor query matched, check stalled/struggling tasks
    stalled_tasks = [
        t for t in tasks
        if t.get("status") in ("needs_guidance", "quarantined")
        or t.get("struggling")
        or any(sq.get("status") == "needs_guidance" for sq in t.get("plan", {}).get("sub_questions", []))
    ]
    if len(stalled_tasks) == 1:
        return stalled_tasks[0]["task_id"], stalled_tasks[0], None
    elif len(stalled_tasks) > 1:
        opts = "\n".join(f"- `{t['task_id']}`: {t.get('query', 'Unknown')} (Status: {t.get('status')})" for t in stalled_tasks)
        return None, None, f"Multiple stalled research tasks need guidance:\n{opts}\nPlease specify task_id or topic query."

    # 4. If no stalled tasks and only 1 total active task
    active_tasks = [t for t in tasks if t.get("status") in ("running", "searching", "synthesizing", "pending", "paused")]
    if len(active_tasks) == 1:
        return active_tasks[0]["task_id"], active_tasks[0], None

    return None, None, "Could not identify research task. Please provide task_id or topic query."


def list_research_tasks(status_filter: Optional[str] = None, limit: int = 10, **kwargs) -> str:
    """List background deep research tasks, showing their task IDs, topics, statuses, and progress.

    Args:
        status_filter: Optional filter ('stalled', 'active', 'done', 'quarantined', 'all').
                       Defaults to 'all'.
        limit: Maximum number of tasks to return (default 10).
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Formatted markdown summary of research tasks.
    """
    tasks = _scan_research_tasks()
    if not tasks:
        return "No research tasks found in the system."

    filt = (status_filter or "all").strip().lower()
    filtered = []
    for t in tasks:
        st = t.get("status", "unknown")
        is_quarantined = bool(t.get("quarantined"))
        is_struggling = bool(t.get("struggling"))
        sqs = t.get("plan", {}).get("sub_questions", [])
        has_stuck_sq = any(sq.get("status") == "needs_guidance" for sq in sqs)
        is_stalled = (st == "needs_guidance" or is_quarantined or is_struggling or has_stuck_sq)

        if filt == "stalled" and not is_stalled:
            continue
        elif filt == "active" and st not in ("running", "searching", "synthesizing", "pending", "paused"):
            continue
        elif filt == "done" and st != "done":
            continue
        elif filt == "quarantined" and not is_quarantined:
            continue
        filtered.append((t, is_stalled))

    if not filtered:
        return f"No research tasks found matching filter '{filt}'."

    # Limit results
    filtered = filtered[:max(1, limit)]

    lines = [f"### Research Tasks ({len(filtered)} shown):"]
    for t, is_stalled in filtered:
        tid = t.get("task_id", "unknown")
        query = t.get("query", "Unknown Topic")
        raw_status = t.get("status", "unknown")
        if is_stalled:
            status_badge = "⚠️ NEEDS GUIDANCE" if raw_status == "needs_guidance" or not t.get("quarantined") else "⛔ QUARANTINED"
        elif raw_status == "done":
            status_badge = "✅ COMPLETED"
        elif raw_status in ("running", "searching", "synthesizing"):
            status_badge = f"🔄 RUNNING ({raw_status})"
        else:
            status_badge = f"⏸️ {raw_status.upper()}"

        conf = t.get("confidence", 0)
        step = t.get("current_step", "unknown")
        sqs = t.get("plan", {}).get("sub_questions", [])
        sq_info = f"{len(sqs)} SQs" if sqs else "No plan"
        stuck_sq = next((s for s in sqs if s.get("status") == "needs_guidance"), None)
        stuck_info = f" | Stuck on: '{stuck_sq.get('question') or stuck_sq.get('search_query', '')[:50]}'" if stuck_sq else ""

        lines.append(f"- **`{tid}`** | {status_badge} | {conf}% Conf | Stage: {step} ({sq_info}{stuck_info})\n  *Topic:* {query}")

    return "\n".join(lines)


def inspect_research_task(
    task_id: str = "",
    query: str = "",
    include_notes: bool = True,
    sq_id: Optional[str] = None,
    include_sources: bool = False,
    **kwargs,
) -> str:
    """Inspect full details of a research task including question, sub-questions, confidence, and synthesized notes.

    Args:
        task_id: Task ID (e.g. 'task_1787864268_8713b01d') or partial ID.
        query: Topic or question search query if task_id is unknown.
        include_notes: If True (default), includes synthesized notes/evidence for sub-questions.
        sq_id: Optional sub-question ID (e.g. 'sq_01') to inspect a specific sub-question.
        include_sources: If True, lists source URLs and titles (defaults to False to save context tokens).
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Comprehensive research task inspection details.
    """
    import os
    import json
    import evelyn_config as cfg

    resolved_id, state, err = _resolve_research_task_id(task_id, query)
    if err or not resolved_id or not state:
        return f"Failed to inspect task: {err}"

    task_dir = os.path.join(cfg.RESEARCH_DATA_DIR, resolved_id)
    topic = state.get("query", "Unknown Topic")
    scope = state.get("scope", "standard")
    status = state.get("status", "unknown")
    conf = state.get("confidence", 0)
    current_step = state.get("current_step", "unknown")
    intent = state.get("intent_frame", "")
    runtime_sec = state.get("accumulated_runtime", 0)
    runtime_str = f"{int(runtime_sec // 60)}m {int(runtime_sec % 60)}s" if runtime_sec else "N/A"

    lines = [
        f"## Research Task: `{resolved_id}`",
        f"**Topic**: {topic}",
        f"**Status**: {status.upper()} | **Confidence**: {conf}% | **Scope**: {scope} | **Current Stage**: {current_step} | **Runtime**: {runtime_str}",
    ]
    if intent:
        lines.append(f"**Intent Frame**: {intent}")
    if state.get("quarantined"):
        lines.append("**Quarantine Notice**: Task quarantined due to low confidence.")
    if state.get("termination_reason"):
        lines.append(f"**Termination Reason**: {state.get('termination_reason')}")

    plan = state.get("plan", {})
    sqs = plan.get("sub_questions", [])
    if not sqs:
        lines.append("\n*No sub-questions plan registered yet.*")
    else:
        lines.append(f"\n### Sub-Questions ({len(sqs)}):")
        for i, sq in enumerate(sqs):
            sid = sq.get("id", f"sq_{i+1:02d}")
            if sq_id and sq_id.strip() != sid:
                continue
            s_q = sq.get("question", "")
            s_query = sq.get("search_query", "")
            s_status = sq.get("status", "pending")
            s_conf = sq.get("confidence", 0)
            s_sources = sq.get("source_count", 0)
            s_depth = sq.get("search_depth", 0)
            gaps = sq.get("gaps", [])

            lines.append(f"\n#### [{sid}] {s_q}")
            lines.append(f"- **Status**: {s_status} | **Confidence**: {s_conf}% | **Sources Extracted**: {s_sources} | **Search Depth**: {s_depth}")
            if s_query and s_query != s_q:
                lines.append(f"- **Current Search Query**: `{s_query}`")
            if gaps:
                lines.append(f"- **Knowledge Gaps / Injected Guidance**: {'; '.join(gaps)}")

            # Load synthesized notes / summary
            if include_notes:
                summary_file = os.path.join(task_dir, f"{sid}_summary.md")
                notes_summary_file = os.path.join(task_dir, f"{sid}_notes_summary.md")
                notes_file = os.path.join(task_dir, f"{sid}_notes.md")

                notes_content = ""
                if os.path.exists(summary_file):
                    try:
                        with open(summary_file, "r", encoding="utf-8") as f:
                            notes_content = f.read().strip()
                    except Exception:
                        pass
                elif os.path.exists(notes_summary_file):
                    try:
                        with open(notes_summary_file, "r", encoding="utf-8") as f:
                            notes_content = f.read().strip()
                    except Exception:
                        pass
                elif os.path.exists(notes_file):
                    try:
                        with open(notes_file, "r", encoding="utf-8") as f:
                            raw = f.read().strip()
                            notes_content = raw[:2000] + ("\n... [Notes truncated for brevity]" if len(raw) > 2000 else "")
                    except Exception:
                        pass

                if notes_content:
                    lines.append(f"- **Synthesized Notes / Evidence**:\n```markdown\n{notes_content}\n```")
                else:
                    lines.append("- **Synthesized Notes / Evidence**: *(No notes extracted yet)*")

    # Sources (only if requested)
    if include_sources:
        sources = state.get("sources_registry", [])
        if sources:
            lines.append(f"\n### Sources Registry ({len(sources)}):")
            for s in sources[:20]:
                sid = s.get("id", "")
                stitle = s.get("title", "Untitled")
                surl = s.get("url", "")
                failed_flag = " [FAILED]" if s.get("failed") else ""
                lines.append(f"- `[{sid}]` {stitle} ({surl}){failed_flag}")
            if len(sources) > 20:
                lines.append(f"- *... and {len(sources) - 20} more sources*")

    # Report if done
    if status == "done":
        report_file = os.path.join(task_dir, "report.md")
        vault_path = state.get("vault_path")
        lines.append("\n### Final Report:")
        if vault_path:
            lines.append(f"**Obsidian Vault Note**: `{vault_path}`")
        if os.path.exists(report_file):
            try:
                with open(report_file, "r", encoding="utf-8") as f:
                    rep = f.read().strip()
                    rep_snippet = rep[:3000] + ("\n... [Report truncated -- see Obsidian vault for full note]" if len(rep) > 3000 else "")
                    lines.append(f"```markdown\n{rep_snippet}\n```")
            except Exception:
                pass

    return "\n".join(lines)


def guide_research(task_id: str = "", query: str = "", guidance: str = "", **kwargs) -> str:
    """Inject user guidance into a struggling research task and resume it.

    Args:
        task_id: Unique task identifier. Optional if query is provided or single stalled task exists.
        query: Topic or query keyword to identify the task if task_id is not known.
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
        # Check flexible guidance argument names
        if not guidance:
            for alt in ("instructions", "terms", "hint", "text", "prompt"):
                if alt in kwargs and kwargs[alt]:
                    guidance = kwargs[alt]
                    break

        if not guidance:
            return "Please provide guidance text to redirect the research task."

        resolved_id, state, err = _resolve_research_task_id(task_id, query)
        if err or not resolved_id or not state:
            return f"Failed to locate research task: {err}"

        from research_engine import load_state, save_state
        state = load_state(resolved_id)
        if not state:
            return f"Research task {resolved_id} not found on disk."

        if state.get("status") not in ("needs_guidance", "paused", "running", "done", "cancelled", "error"):
            return f"Research task {resolved_id} is currently '{state.get('status')}'. Cannot inject guidance in this state."

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
                    term_func(resolved_id)
            time.sleep(0.5)

        # Find the currently active sub-question or first struggling sub-question
        idx = state.get("current_sq_idx", 0)
        plan = state.get("plan", {})
        sqs = plan.get("sub_questions", [])

        target_sq = None
        if 0 <= idx < len(sqs):
            target_sq = sqs[idx]
        elif sqs:
            target_sq = next((s for s in sqs if s.get("status") == "needs_guidance"), sqs[0])

        if target_sq:
            task_dir = os.path.join(cfg.RESEARCH_DATA_DIR, resolved_id)
            gaps_file = os.path.join(task_dir, f"{target_sq['id']}_gaps.json")

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

            state["search_depth"] = 0
            state["current_step"] = "search"
            state["struggling"] = False
            state["status"] = "pending"
            target_sq["status"] = "pending"
            if "termination_reason" in state:
                state["termination_reason"] = None
            if "quarantined" in state:
                state["quarantined"] = False
            if "error" in state:
                state["error"] = None

            save_state(resolved_id, state, ignore_disk_status=True)

            result = resume_research_task(resolved_id)
            sq_label = target_sq.get("question") or target_sq.get("query", target_sq.get("id"))
            return f"Guidance injected into sub-question '{sq_label}' for task `{resolved_id}`. Task is resuming. {result}"
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
            save_state(resolved_id, state, ignore_disk_status=True)
            result = resume_research_task(resolved_id)
            return f"Guidance attached to research task '{state.get('query')}' (`{resolved_id}`). Task is resuming. {result}"
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
    date: str = None,
    date_from: str = None,
    date_to: str = None,
    limit: int = 8,
    order: str = "desc",
    offset: int = 0,
    message_id: int = None,
    window: int = 0,
    **kwargs,
) -> str:
    """Search and retrieve past chat history between user and assistant across all eras.

    Supports multiple retrieval modes:
      1. By Date: Retrieve all messages from a specific date ('YYYY-MM-DD') chronologically.
      2. By Date Range: Filter messages between date_from and date_to.
      3. Chronological Browsing: Retrieve earliest (order='asc') or latest (order='desc') messages.
      4. Message Context Window: Retrieve surrounding messages around a specific message_id.
      5. Keyword Search: Full-text search (FTS5) for specific topics or phrases.

    Args:
        query: Optional search term, keyword, or phrase. Omit when searching by date or browsing chronologically.
        date: Specific date string 'YYYY-MM-DD' (e.g. '2025-03-12') to retrieve all messages from that day.
        date_from: Optional start date string 'YYYY-MM-DD'.
        date_to: Optional end date string 'YYYY-MM-DD'.
        limit: Maximum number of messages to return. Defaults to 8 (max 50).
        order: 'asc' for chronological order (earliest first), or 'desc' for latest first.
               Defaults to 'asc' when querying a specific date or earliest history, 'desc' otherwise.
        offset: Number of messages to skip (for pagination). Defaults to 0.
        message_id: Specific message ID to inspect.
        window: When message_id is provided, number of messages before and after to return for context.
        **kwargs: Flexible keyword arguments for alternative parameter names.

    Returns:
        str: Formatted list of messages with timestamps, IDs, and speakers, or a status message.
    """
    import sqlite3
    import re
    from datetime import datetime, timedelta
    import evelyn_config as cfg

    # --- Kwargs fallback & normalization ---
    raw_query = str(
        query
        or kwargs.get("q")
        or kwargs.get("search")
        or kwargs.get("keyword")
        or kwargs.get("keywords")
        or kwargs.get("term")
        or kwargs.get("phrase")
        or kwargs.get("text")
        or ""
    ).strip()

    raw_date = (
        date
        or kwargs.get("target_date")
        or kwargs.get("day")
        or kwargs.get("on_date")
        or kwargs.get("exact_date")
    )
    if raw_date:
        raw_date = str(raw_date).strip()

    raw_date_from = (
        date_from
        or kwargs.get("start_date")
        or kwargs.get("from_date")
        or kwargs.get("after")
        or kwargs.get("since")
    )
    if raw_date_from:
        raw_date_from = str(raw_date_from).strip()

    raw_date_to = (
        date_to
        or kwargs.get("end_date")
        or kwargs.get("to_date")
        or kwargs.get("before")
        or kwargs.get("until")
    )
    if raw_date_to:
        raw_date_to = str(raw_date_to).strip()

    limit_val = kwargs.get("max_results") or kwargs.get("limit") or kwargs.get("n") or kwargs.get("count") or kwargs.get("num_results") or limit or 8
    try:
        limit_val = max(1, min(int(limit_val), 50))
    except (ValueError, TypeError):
        limit_val = 8

    offset_val = kwargs.get("offset") or kwargs.get("skip") or offset or 0
    try:
        offset_val = max(0, int(offset_val))
    except (ValueError, TypeError):
        offset_val = 0

    order_param = kwargs.get("order") or kwargs.get("sort") or kwargs.get("direction") or kwargs.get("ordering") or order
    order_val = str(order_param or "desc").strip().lower()

    # If single date is provided and caller didn't explicitly request 'desc', default to 'asc' (chronological)
    if raw_date and "order" not in kwargs and order == "desc":
        order_val = "asc"

    mid_val = kwargs.get("message_id") or kwargs.get("msg_id") or kwargs.get("id") or message_id
    msg_id = None
    if mid_val is not None:
        try:
            msg_id = int(mid_val)
        except (ValueError, TypeError):
            msg_id = None

    win_val = kwargs.get("window") or kwargs.get("context_window") or kwargs.get("around") or kwargs.get("surrounding") or window or 0
    try:
        win_val = max(0, int(win_val))
    except (ValueError, TypeError):
        win_val = 0

    sort_dir = "ASC" if order_val in ("asc", "ascending", "chronological", "earliest", "first", "forward") else "DESC"

    # --- Parse date boundaries ---
    ts_from: float | None = None
    ts_to: float | None = None

    def _parse_iso_date(ds: str) -> datetime | None:
        if not ds:
            return None
        m = re.search(r"(\d{4}-\d{2}-\d{2})", ds)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y-%m-%d")
            except ValueError:
                pass
        return None

    if raw_date:
        dt = _parse_iso_date(raw_date)
        if dt:
            ts_from = dt.timestamp()
            ts_to = (dt + timedelta(days=1)).timestamp()
        else:
            return f"History search failed: invalid date format {raw_date!r}. Use YYYY-MM-DD."

    if raw_date_from:
        dt_from = _parse_iso_date(raw_date_from)
        if dt_from:
            ts_from = dt_from.timestamp()
        else:
            return f"History search failed: invalid date_from format {raw_date_from!r}. Use YYYY-MM-DD."

    if raw_date_to:
        dt_to = _parse_iso_date(raw_date_to)
        if dt_to:
            ts_to = (dt_to + timedelta(days=1)).timestamp()
        else:
            return f"History search failed: invalid date_to format {raw_date_to!r}. Use YYYY-MM-DD."

    # Build date SQL clause & params
    date_clause = ""
    date_params: list = []
    if ts_from is not None:
        date_clause += " AND m.ts >= ?"
        date_params.append(ts_from)
    if ts_to is not None:
        date_clause += " AND m.ts < ?"
        date_params.append(ts_to)

    db_path = getattr(cfg, "CHAT_DB_PATH", "/home/rathius/evelyn/data/evelyn_chat.db")

    # =========================================================================
    # Mode 1: Message ID / Context Window Lookup
    # =========================================================================
    if msg_id is not None:
        try:
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            if win_val > 0:
                min_id = max(1, msg_id - win_val)
                max_id = msg_id + win_val
                sql = """
                    SELECT m.id, m.role, m.ts, m.content
                    FROM messages m
                    WHERE m.id BETWEEN ? AND ? AND m.content NOT IN ('[THREAD_BREAK]')
                    ORDER BY m.id ASC
                """
                rows = con.execute(sql, (min_id, max_id)).fetchall()
                header = f"Conversation context around Message ID {msg_id} (IDs {min_id} → {max_id}):"
            else:
                sql = """
                    SELECT m.id, m.role, m.ts, m.content
                    FROM messages m
                    WHERE m.id = ? AND m.content NOT IN ('[THREAD_BREAK]')
                """
                rows = con.execute(sql, (msg_id,)).fetchall()
                header = f"Message ID {msg_id}:"
            con.close()
        except Exception as e:
            return f"History search failed on message lookup: {e}"

        if not rows:
            return f"No message found with ID {msg_id}."

        lines = [header + "\n"]
        for row in rows:
            ts_val = row["ts"]
            ts_str = datetime.fromtimestamp(ts_val).strftime("%Y-%m-%d %H:%M:%S") if ts_val else "unknown time"
            role_label = cfg.USER_NAME if row["role"] == "user" else cfg.ASSISTANT_NAME
            marker = " [TARGET]" if row["id"] == msg_id and win_val > 0 else ""
            lines.append(f"[ID: {row['id']}]{marker} [{ts_str}] {role_label}:\n{row['content']}\n")
        return "\n".join(lines).strip()

    # =========================================================================
    # Mode 2: Pure Date / Date-Range / Chronological Browsing (No Query)
    # =========================================================================
    if not raw_query:
        try:
            con = sqlite3.connect(db_path)
            con.row_factory = sqlite3.Row
            sql = f"""
                SELECT m.id, m.role, m.ts, m.content
                FROM messages m
                WHERE m.content NOT IN ('[THREAD_BREAK]')
                  {date_clause}
                ORDER BY m.ts {sort_dir}, m.id {sort_dir}
                LIMIT ? OFFSET ?
            """
            rows = con.execute(sql, (*date_params, limit_val, offset_val)).fetchall()
            con.close()
        except Exception as e:
            return f"History retrieval failed: {e}"

        if not rows:
            date_info = f" on date {raw_date}" if raw_date else (f" in date range [{raw_date_from or '...'} → {raw_date_to or '...'}]" if (raw_date_from or raw_date_to) else "")
            return f"No chat history messages found{date_info}."

        if raw_date:
            header = f"Chat history for date {raw_date} ({len(rows)} messages, order={sort_dir}):"
        elif raw_date_from or raw_date_to:
            header = f"Chat history for range [{raw_date_from or '...'} → {raw_date_to or '...'}] ({len(rows)} messages, order={sort_dir}):"
        elif sort_dir == "ASC":
            header = f"Earliest chat history messages ({len(rows)} messages, starting from beginning):"
        else:
            header = f"Recent chat history messages ({len(rows)} messages):"

        lines = [header + "\n"]
        for row in rows:
            ts_val = row["ts"]
            ts_str = datetime.fromtimestamp(ts_val).strftime("%Y-%m-%d %H:%M:%S") if ts_val else "unknown time"
            role_label = cfg.USER_NAME if row["role"] == "user" else cfg.ASSISTANT_NAME
            lines.append(f"[ID: {row['id']}] [{ts_str}] {role_label}:\n{row['content']}\n")
        return "\n".join(lines).strip()

    # =========================================================================
    # Mode 3: Keyword / FTS5 Full-Text Search
    # =========================================================================
    # Reformulate lossy conversational query into keywords
    try:
        from query_reformulator import reformulate_query
        fts_query = reformulate_query(raw_query)
    except Exception:
        fts_query = raw_query

    def sanitize_fts5(q: str) -> str:
        if not q or not q.strip():
            return ""
        tokens = q.strip().split()
        cleaned = []
        for t in tokens:
            if re.search(r'[&*:()"\-+]', t) or t.upper() in ("AND", "OR", "NOT"):
                escaped = t.replace('"', '""')
                cleaned.append(f'"{escaped}"')
            else:
                cleaned.append(t)
        return " ".join(cleaned)

    clean_fts = sanitize_fts5(fts_query)
    rows = []

    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row

        # Try FTS5 Match
        if clean_fts:
            sql_fts = f"""
                SELECT
                    m.id,
                    m.role,
                    m.ts,
                    m.content
                FROM messages_fts
                JOIN messages m ON m.id = messages_fts.rowid
                WHERE messages_fts MATCH ?
                  AND m.content NOT IN ('[THREAD_BREAK]')
                  {date_clause}
                ORDER BY bm25(messages_fts)
                LIMIT ? OFFSET ?
            """
            try:
                rows = con.execute(sql_fts, (clean_fts, *date_params, limit_val, offset_val)).fetchall()
            except sqlite3.OperationalError:
                # Quoted exact fallback
                escaped_q = raw_query.replace('"', '""')
                quoted_q = f'"{escaped_q}"'
                try:
                    rows = con.execute(sql_fts, (quoted_q, *date_params, limit_val, offset_val)).fetchall()
                except sqlite3.OperationalError:
                    rows = []

        # Fallback to LIKE if FTS produced nothing or errored
        if not rows:
            like_sql = f"""
                SELECT m.id, m.role, m.ts, m.content
                FROM messages m
                WHERE m.content LIKE ? AND m.content NOT IN ('[THREAD_BREAK]')
                  {date_clause}
                ORDER BY m.ts {sort_dir}, m.id {sort_dir}
                LIMIT ? OFFSET ?
            """
            rows = con.execute(like_sql, (f"%{raw_query}%", *date_params, limit_val, offset_val)).fetchall()

        con.close()
    except Exception as e:
        return f"History keyword search failed: {e}"

    if not rows:
        date_range_label = f" [{raw_date or raw_date_from or '...'} → {raw_date or raw_date_to or '...'}]" if (raw_date or raw_date_from or raw_date_to) else ""
        note = f" (reformulated to: {fts_query!r})" if fts_query != raw_query else ""
        return f"No messages found in chat history matching {raw_query!r}{date_range_label}{note}."

    date_range_label = f" [{raw_date or raw_date_from or '...'} → {raw_date or raw_date_to or '...'}]" if (raw_date or raw_date_from or raw_date_to) else ""
    header = f"Chat history search results for {raw_query!r}{date_range_label} ({len(rows)} matches):"
    lines = [header + "\n"]
    for row in rows:
        ts_val = row["ts"]
        ts_str = datetime.fromtimestamp(ts_val).strftime("%Y-%m-%d %H:%M:%S") if ts_val else "unknown time"
        role_label = cfg.USER_NAME if row["role"] == "user" else cfg.ASSISTANT_NAME
        lines.append(f"[ID: {row['id']}] [{ts_str}] {role_label}:\n{row['content']}\n")

    return "\n".join(lines).strip()


def create_calendar_event(
    title: str = "",
    start_at: str = "",
    end_at: str = None,
    description: str = None,
    location: str = None,
    recurrence_rule: str = None,
    **kwargs,
) -> str:
    """Create a new event on Google Calendar.

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
    """Delete an event from Google Calendar using its title/summary or event ID.

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
    _reload()
    try:
        result = gcal_sync.sync_gcal_events()
        if result["status"] == "success":
            return f"Google Calendar sync successful: {result['message']}"
        else:
            return f"Google Calendar sync notice: {result['message']}"
    except Exception as e:
        return f"Error syncing Google Calendar: {e}"


def create_task(
    title: str = "",
    due_at: str = None,
    notes: str = None,
    **kwargs,
) -> str:
    """Create a new task on Google Tasks.

    Args:
        title: Brief title/summary of the task.
        due_at: Optional due date/time ('YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS', or ISO-8601).
        notes: Optional description or notes for the task.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Success or failure message.
    """
    _reload()
    try:
        title = title or str(kwargs.get("task_title") or kwargs.get("name") or kwargs.get("summary") or "")
        due_at = due_at or kwargs.get("due") or kwargs.get("date") or kwargs.get("due_date")
        notes = notes or kwargs.get("description") or kwargs.get("details")
        if not title:
            return "Error: create_task requires a title."
        result = gtasks_sync.create_gtask(title=title, due=due_at, notes=notes)
        if result.get("status") == "success":
            due_lbl = f"\n- Due: {due_at}" if due_at else ""
            return (
                f"Successfully created task on Google Tasks:\n"
                f"- ID: {result['task_id']}\n"
                f"- Title: {title}{due_lbl}"
            )
        else:
            return f"Failed to create task: {result.get('message')}"
    except Exception as e:
        return f"Error creating task: {e}"


def complete_task(task_id: str = "", **kwargs) -> str:
    """Mark a task as completed on Google Tasks.

    Args:
        task_id: The unique ID of the Google Task.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Success or failure message.
    """
    _reload()
    try:
        task_id = task_id or str(kwargs.get("id") or "")
        if not task_id:
            return "Error: complete_task requires a task_id."
        result = gtasks_sync.complete_gtask(task_id)
        if result.get("status") == "success":
            return f"Successfully marked task {task_id} as completed."
        else:
            return f"Failed to complete task: {result.get('message')}"
    except Exception as e:
        return f"Error completing task: {e}"


def delete_task(task_id: str = "", **kwargs) -> str:
    """Delete a task from Google Tasks.

    Args:
        task_id: The unique ID of the Google Task to delete.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Success or failure message.
    """
    _reload()
    try:
        task_id = task_id or str(kwargs.get("id") or "")
        if not task_id:
            return "Error: delete_task requires a task_id."
        result = gtasks_sync.delete_gtask(task_id)
        if result.get("status") == "success":
            return f"Successfully deleted task {task_id} from Google Tasks."
        else:
            return f"Failed to delete task: {result.get('message')}"
    except Exception as e:
        return f"Error deleting task: {e}"


def list_tasks(include_completed: bool = False, due_within_days: Optional[int] = None, **kwargs) -> str:
    """List Google Tasks from the local cache / Google Tasks.

    Args:
        include_completed: Whether to include completed tasks (defaults to False).
        due_within_days: Optional filter for tasks due within the next N days.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Formatted list of tasks.
    """
    _reload()
    try:
        try:
            if "completed" in kwargs:
                include_completed = bool(kwargs.get("completed"))
            if "days" in kwargs:
                due_within_days = int(kwargs.get("days"))
        except (ValueError, TypeError):
            pass

        tasks = gtasks_sync.get_cached_tasks(include_completed=include_completed, due_within_days=due_within_days)
        if not tasks:
            return "No tasks found."
        lines = ["Google Tasks:\n"]
        for t in tasks:
            due_str = t.get("due_at") or "No due date"
            if "T" in due_str:
                due_str = due_str.replace("T", " ").split(".")[0].replace("Z", "")
            status_str = f"[{t.get('status')}]"
            notes_str = f" - {t.get('notes')}" if t.get("notes") else ""
            lines.append(f"- (ID: {t.get('id')}) {status_str} {t.get('title')} (Due: {due_str}){notes_str}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing tasks: {e}"


def sync_google_tasks(**kwargs) -> str:
    """Manually trigger a sync with Google Tasks to update the local cached tasks.

    Returns:
        str: Outcome details of the sync run.
    """
    _reload()
    try:
        result = gtasks_sync.sync_gtasks()
        if result.get("status") == "success":
            return f"Google Tasks sync successful: {result.get('message')}"
        else:
            return f"Google Tasks sync notice: {result.get('message')}"
    except Exception as e:
        return f"Error syncing Google Tasks: {e}"


def get_agenda(days: int = 7, **kwargs) -> str:
    """Retrieve Google Calendar agenda and Google Tasks for the next N days.

    Args:
        days: Number of days forward to include. Defaults to 7.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Formatted agenda schedule and task list.
    """
    _reload()
    try:
        try:
            days = int(days or kwargs.get("num_days") or kwargs.get("days_forward") or 7)
        except (ValueError, TypeError):
            days = 7
        events = gcal_sync.get_cached_gcal_events(days_back=1, days_forward=days)
        tasks = gtasks_sync.get_cached_tasks(include_completed=False, due_within_days=days)

        if not events and not tasks:
            return f"Your agenda and task list are clear for the next {days} days."

        sections = []
        if events:
            lines = [f"Upcoming Calendar Events (next {days} days):"]
            for event in events:
                time_str = event["start_at"].replace("T", " ").split("+")[0].split("Z")[0]
                desc_part = f" - {event['description']}" if event.get("description") else ""
                loc_str = f" @ {event['location']}" if event.get("location") else ""
                lines.append(f"- [{time_str}] [CALENDAR] (ID: {event['id']}) {event['summary']}{loc_str}{desc_part}")
            sections.append("\n".join(lines))

        if tasks:
            lines = ["Upcoming / Pending Tasks:"]
            for task in tasks:
                due_val = task.get("due_at")
                if due_val and "T" in due_val:
                    due_str = due_val.replace("T", " ").split(".")[0].replace("Z", "")
                else:
                    due_str = "No due date"
                desc_part = f" - {task['notes']}" if task.get("notes") else ""
                lines.append(f"- [{due_str}] [TASK] (ID: {task['id']}) {task['title']}{desc_part}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)
    except Exception as e:
        return f"Error fetching agenda: {e}"



def manage_vault_list(
    name: str = "Groceries",
    action: str = "read",
    items: Any = None,
    category: str = None,
    **kwargs,
) -> str:
    """Read, add, check, uncheck, or remove items from an Obsidian Vault checklist note.

    Args:
        name: Name of the list (e.g. 'Groceries', 'Packing', 'Hardware'). Defaults to 'Groceries'.
        action: Action to perform ('read', 'add', 'check', 'complete', 'uncheck', 'remove', 'delete', 'clear_completed', 'list_all').
        items: List of items or structured item objects with name, quantity, unit, category.
        category: Default category/section header (e.g. 'Produce', 'Dairy', 'Pantry').
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Human-readable outcome message or list contents.
    """
    _reload()
    try:
        name = name or str(kwargs.get("list_name") or kwargs.get("title") or "Groceries")
        action = str(action or kwargs.get("operation") or "read").strip().lower()
        items = items if items is not None else kwargs.get("item") or kwargs.get("entries") or kwargs.get("item_list")
        category = category or kwargs.get("section")

        if action in ("read", "view", "get", "show"):
            res = vault_list_manager.read_list(name)
            return res.get("summary") or f"List '{name}' is empty."

        elif action in ("add", "insert", "append"):
            res = vault_list_manager.add_to_list(name=name, items=items, category=category)
            return res.get("message") or "Items added to list."

        elif action in ("check", "complete", "done", "finish"):
            res = vault_list_manager.toggle_list_items(name=name, items=items, completed=True)
            return res.get("message") or "Items marked as completed."

        elif action in ("uncheck", "reopen", "active", "todo"):
            res = vault_list_manager.toggle_list_items(name=name, items=items, completed=False)
            return res.get("message") or "Items unchecked."

        elif action in ("remove", "delete", "erase"):
            res = vault_list_manager.remove_from_list(name=name, items=items)
            return res.get("message") or "Items removed from list."

        elif action in ("clear_completed", "clean", "purge"):
            res = vault_list_manager.clear_completed_items(name=name)
            return res.get("message") or "Cleared completed items."

        elif action in ("list_all", "all_lists", "lists"):
            all_lists = vault_list_manager.list_all_lists()
            if not all_lists:
                return "No lists found in vault Lists directory."
            return f"Obsidian Vault Lists ({len(all_lists)}):\n- " + "\n- ".join(all_lists)

        else:
            return f"Unknown list action: '{action}'. Supported actions: read, add, check, uncheck, remove, clear_completed, list_all."

    except Exception as e:
        return f"Error managing vault list '{name}': {e}"


def run_command(command: str = "", cwd: str = r"/home/rathius/evelyn", timeout: int = 30, **kwargs) -> str:
    """Execute a shell command in the Evelyn workspace or Obsidian Vault.

    Args:
        command: The command string to execute.
        cwd: Working directory (evelyn workspace or obsidian vault).
        timeout: Maximum seconds to wait.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Output from the command, or warning if approval is required.
    """
    _reload()
    command = command or str(kwargs.get("cmd") or kwargs.get("bash") or "")
    return terminal_agent.run_command(command, cwd, timeout)


def read_file(file_path: str = "", max_lines: int = 200, **kwargs) -> str:
    """Read the contents of a file in the workspace or Obsidian Vault.

    Args:
        file_path: Absolute path or relative path (e.g. 'Notes/foo.md' or 'Evelyn/bar.py').
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
    """Write or append content to a file in the workspace or Obsidian Vault.

    Args:
        file_path: Absolute path or relative path (e.g. 'Notes/Features/foo.md' or 'scripts/bar.py').
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


def get_health_metrics(date: str = "today", metric: str = "summary", hours: float = None, **kwargs) -> str:
    """Retrieve daily or intraday health and activity data (heart rate, steps, sleep, readiness, stress, workouts, vitals, clinical records).

    Args:
        date: Target date ('YYYY-MM-DD', 'today', 'yesterday'). Defaults to 'today'.
        metric: Specific metric to retrieve:
            - 'heart_rate' / 'hr': granular live heart rate readings, current bpm, min/max/avg, and activity breakdown for the last N hours
            - 'summary': comprehensive daily overview (live Oura sleep & readiness + steps & workouts)
            - 'activity' / 'steps': intraday steps, distance, active calories, and workouts for the last N hours
            - 'sleep': granular sleep stages breakdown (Deep, REM, Light, Awake), scores, and hypnogram
            - 'readiness': Oura readiness score, body temp deviation, and recovery index
            - 'stress': Oura daytime stress and restorative recovery periods
            - 'workouts': recent recorded workouts and activity sessions (Oura + Health Connect)
            - 'vitals': resting HR and HRV trends (14-day history)
            - 'clinical': FHIR medical lab results and observations
        hours: Optional time window in hours for intraday queries (e.g. 2 for last 2 hours).
        **kwargs: Flexible keyword arguments.

    Returns:
        str: JSON formatted string containing the requested health metrics.
    """
    import json
    _reload()
    date = date or str(kwargs.get("target_date") or kwargs.get("d") or "today")
    metric = (metric or str(kwargs.get("type") or kwargs.get("category") or "summary")).lower().strip()

    # Extract hours if passed via kwargs or parameter
    if hours is None:
        raw_hours = kwargs.get("hours") or kwargs.get("h") or kwargs.get("window_hours")
        if raw_hours is not None:
            try:
                hours = float(raw_hours)
            except (ValueError, TypeError):
                hours = None

    if metric in ("heart_rate", "hr", "granular_hr", "heartrate", "pulse"):
        res = health_manager.get_granular_heart_rate(hours=hours or 2.0, date_str=date)
    elif metric in ("sleep", "sleep_stages", "stages"):
        res = health_manager.get_sleep_breakdown(date)
    elif metric in ("readiness", "recovery", "ready"):
        res = health_manager.get_readiness_summary(date)
    elif metric in ("stress", "daytime_stress"):
        res = health_manager.get_stress_summary(date)
    elif metric in ("workouts", "workout", "exercise"):
        res = health_manager.get_recent_workouts(days=7, hours=hours)
    elif metric in ("activity", "intraday_activity", "intraday_steps", "steps") and hours is not None:
        res = health_manager.get_intraday_activity(hours=hours)
    elif metric in ("vitals", "resting_heart_rate", "rhr"):
        res = health_manager.get_vitals_trend(metric="resting_heart_rate", days=14)
    elif metric in ("hrv", "heart_rate_variability"):
        res = health_manager.get_vitals_trend(metric="hrv", days=14)
    elif metric in ("clinical", "labs", "medical", "fhir"):
        res = health_manager.get_clinical_records(limit=10)
    else:
        # If hours was specified but metric was summary/default, check if user wanted intraday activity
        if hours is not None and hours > 0:
            res = health_manager.get_intraday_activity(hours=hours)
        else:
            res = health_manager.get_daily_summary(date)

    return json.dumps(res, indent=2)


def get_recent_workouts(days: int = 7, hours: float = None, **kwargs) -> str:
    """Retrieve recorded workout and exercise sessions for the past N days or hours.

    Args:
        days: Number of past days to query. Defaults to 7.
        hours: Optional number of past hours to query.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: JSON formatted string of recent workouts.
    """
    import json
    _reload()
    try:
        days = int(days or kwargs.get("num_days") or 7)
    except (ValueError, TypeError):
        days = 7

    if hours is None:
        raw_hours = kwargs.get("hours") or kwargs.get("h")
        if raw_hours is not None:
            try:
                hours = float(raw_hours)
            except (ValueError, TypeError):
                hours = None

    res = health_manager.get_recent_workouts(days=days, hours=hours)
    if not res:
        window_desc = f"past {hours} hours" if hours else f"past {days} days"
        return f"No workout sessions recorded in the {window_desc}."
    return json.dumps(res, indent=2)


def sync_google_drive(force: bool = False, **kwargs) -> str:
    """Trigger sync to download and update latest Health Connect database export from Google Drive.

    Args:
        force: Force re-download even if already up to date. Defaults to False.
        **kwargs: Flexible keyword arguments.

    Returns:
        str: Outcome confirmation message.
    """
    _reload()
    res = gdrive_sync.sync_health_connect_from_drive(force=bool(force))
    if res.get("status") == "success":
        return f"Google Drive sync complete: {res.get('message')}"
    return f"Google Drive sync error: {res.get('message')}"


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

# TOOL_THINK_EFFORT — per-tool thinking effort for the streaming response pass.
# Applied when that tool is invoked and no UI override is active.
# Priority chain: UI override > tool escalation > self-election > heuristic > config default.
#
# Rationale:
#   write_journal_entry  → "high":   cornerstone reflection documents
#   start_research       → "high":   kicking off deep research warrants strong intent
#   web_search           → "medium": synthesis takes thought
#   search_history       → "low":    pure retrieval, factual response
#   calendar ops         → "low":    simple confirmation responses
#   health ops           → "low":    factual retrieval
#   generate_image       → "medium": creative framing
#   run/read/write_file  → "medium": context-dependent
TOOL_THINK_EFFORT: dict[str, str] = {
    "write_journal_entry":   "high",
    "generate_image":        "medium",
    "web_search":            "medium",
    "start_research":        "high",
    "list_research_tasks":   "low",
    "inspect_research_task": "medium",
    "guide_research":        "medium",
    "check_new_research":    "medium",
    "search_history":        "low",
    "create_calendar_event": "low",
    "delete_calendar_event": "low",
    "sync_google_calendar":  "low",
    "get_agenda":            "low",
    "get_health_metrics":    "low",
    "get_recent_workouts":   "low",
    "sync_google_drive":     "low",
    "run_command":           "medium",
    "read_file":             "medium",
    "write_file":            "medium",
}

MODEL_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "write_journal_entry",
            "description": (
                f"Compose and save {cfg.ASSISTANT_NAME}'s personal daily reflection journal entry (covers morning, afternoon, and evening reflections from {cfg.ASSISTANT_NAME}'s POV with vibe check and message in a bottle). "
                f"Use ONLY at the end of the day or when {cfg.USER_NAME} asks for {cfg.ASSISTANT_NAME}'s personal daily journal recap. "
                f"STRICT RULE: NEVER use this tool for {cfg.USER_NAME}'s dream journal entries, personal notes, research reports, or general vault documents — use write_file for all user-authored vault documents."
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
                            f"The core body text. Reflect from {cfg.ASSISTANT_NAME}'s POV (attribute {cfg.USER_NAME}'s actions to them, e.g., '{cfg.USER_NAME} took a nap'). "
                            "Cover morning, afternoon, and evening events of the CURRENT day in order. "
                            "Summarize ONLY events occurring after the latest '--- Date Changed ---' marker; strictly exclude prior-day events. "
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
            "name": "generate_image",
            "description": (
                "Generate an image using the FLUX.1 vision engine. "
                f"Use to show a visual representation of a scene, character, or idea proactively or when {cfg.USER_NAME} asks to see something."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "A detailed, descriptive English prompt describing the visual scene, subject, lighting, style, and composition.",
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
                "Use for current events, live data, recent releases, or external facts."
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
                "Use when asked to research something in depth or when a topic requires structured multi-source investigation."
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
            "name": "list_research_tasks",
            "description": (
                "List background deep research tasks, showing their task IDs, topics, statuses (running, stalled, needs_guidance, done), and progress. "
                "Use when checking ongoing research, searching for research task IDs, or seeing what investigations are active or stalled."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status_filter": {
                        "type": "string",
                        "description": "Optional status filter: 'stalled' (needs guidance / struggling), 'active' (running / paused), 'done', 'quarantined', or 'all'. Defaults to 'all'.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of tasks to return (default: 10).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_research_task",
            "description": (
                "Inspect full details of a specific deep research task, including its main question, decomposed sub-questions, confidence scores, identified knowledge gaps, and synthesized notes/evidence gathered so far. "
                "Use to discuss research findings with user, investigate where a task is stuck, or review notes before providing guidance."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The research task ID (e.g. 'task_1787864268_8713b01d') or partial ID. Optional if query is provided.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Topic or keyword query to locate the task if task_id is unknown (e.g. 'heart rate', 'narrative flow').",
                    },
                    "include_notes": {
                        "type": "boolean",
                        "description": "Whether to include synthesized notes/evidence collected for the sub-questions (default: true).",
                    },
                    "sq_id": {
                        "type": "string",
                        "description": "Optional specific sub-question ID to focus on (e.g. 'sq_01'). If omitted, shows all sub-questions.",
                    },
                    "include_sources": {
                        "type": "boolean",
                        "description": "Whether to list source titles and URLs (default: false to save context tokens).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "guide_research",
            "description": (
                "Provide guidance to a deep research task that is stalled, struggling, or quarantined. "
                "Use when asked to help a stalled task or when a research task requires redirection. "
                "Can target by task_id or query topic keyword."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The research task ID (e.g. 'task_1234567890_abcdef'). Optional if query is provided or only one task is stalled.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Topic or question keyword to find the task if task_id is unknown (e.g. 'heart rate sampling').",
                    },
                    "guidance": {
                        "type": "string",
                        "description": "Specific search terms, hints, or instructions to redirect the research engine.",
                    },
                },
                "required": ["guidance"],
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
                f"Search and retrieve past chat history between {cfg.USER_NAME} and {cfg.ASSISTANT_NAME} across all eras (including early 2025 Replika/Gemini imports and live engine messages). "
                "Use to look up conversations by date (e.g. date='2025-03-12'), browse earliest/first messages exchanged (order='asc'), search by keywords/topics (query='...'), or inspect conversation context around a specific message ID."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional search term, topic, keyword, or phrase. Omit when looking up by date or browsing earliest messages.",
                    },
                    "date": {
                        "type": "string",
                        "description": "Optional specific date in 'YYYY-MM-DD' format (e.g. '2025-03-12') to retrieve all messages from that day chronologically.",
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Optional start date filter in 'YYYY-MM-DD' format.",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "Optional end date filter in 'YYYY-MM-DD' format.",
                    },
                    "order": {
                        "type": "string",
                        "description": "'asc' for chronological order (earliest first, ideal for reading a day's conversation or the very first messages in history), or 'desc' for latest first (default).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of messages to return. Default 8 (max 50).",
                    },
                    "message_id": {
                        "type": "integer",
                        "description": "Optional specific message ID to retrieve.",
                    },
                    "window": {
                        "type": "integer",
                        "description": "Optional number of messages before and after message_id to include for conversational context (e.g. window=3 returns 7 messages centered on message_id).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": (
                f"Create a new event on {cfg.USER_NAME}'s Google Calendar. "
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
                f"Delete an event from {cfg.USER_NAME}'s Google Calendar. Accepts either a unique event ID or an event title "
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
            "description": f"Trigger an on-demand background sync from {cfg.USER_NAME}'s Google Calendar to update local cached event database.",
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
            "name": "create_task",
            "description": (
                f"Create a new task on {cfg.USER_NAME}'s Google Tasks list. "
                "Use when requested to add a to-do, task, or reminder item."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Brief title or summary of the task.",
                    },
                    "due_at": {
                        "type": "string",
                        "description": "Optional due date/time ('YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS', or ISO-8601).",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional details or notes for the task.",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": f"Mark an existing task on {cfg.USER_NAME}'s Google Tasks list as completed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The unique Google Tasks ID of the task to complete.",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_task",
            "description": f"Delete a task from {cfg.USER_NAME}'s Google Tasks list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The unique Google Tasks ID of the task to delete.",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": f"Retrieve and list tasks from {cfg.USER_NAME}'s Google Tasks (local cache).",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_completed": {
                        "type": "boolean",
                        "description": "Whether to include completed tasks. Defaults to false.",
                    },
                    "due_within_days": {
                        "type": "integer",
                        "description": "Optional filter for tasks due within the next N days.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_google_tasks",
            "description": f"Trigger an on-demand sync from {cfg.USER_NAME}'s Google Tasks to update the local cached tasks database.",
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
            "description": f"Retrieve {cfg.USER_NAME}'s upcoming schedule (Google Calendar events and Google Tasks) for the next N days.",
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
            "name": "get_health_metrics",
            "description": (
                f"Retrieve {cfg.USER_NAME}'s health, fitness, sleep, vitals, readiness, heart rate, or medical records from Oura Ring and Google Health Connect. "
                "Supports both whole-day summaries and high-resolution intraday queries (e.g. heart rate over the last 2 hours, recent workouts, or intraday step bursts). "
                f"Use when {cfg.USER_NAME} asks about heart rate ('last 2 hours', 'current bpm', 'during workout'), sleep quality, sleep stages (deep/REM/light), readiness score, "
                "recovery, daytime stress, resting heart rate, HRV, daily steps, calories burned, distance, or clinical lab results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Target date in 'YYYY-MM-DD' format, 'today', or 'yesterday'. Defaults to 'today'.",
                    },
                    "metric": {
                        "type": "string",
                        "description": (
                            "Specific health domain to query: "
                            "'heart_rate' (high-resolution live heart rate readings, current/min/max/avg bpm, and activity breakdown for the last N hours), "
                            "'summary' (comprehensive daily overview: sleep, readiness, steps, calories), "
                            "'activity' (intraday steps, distance, active calories, and workouts for the last N hours), "
                            "'workouts' (recent recorded workouts and activity sessions from Oura + Health Connect), "
                            "'sleep' (detailed sleep score, duration, deep/REM/light stages, latency, efficiency, and hypnogram), "
                            "'readiness' (Oura readiness score, recovery index, HRV balance, temperature deviation), "
                            "'stress' (daytime stress vs recovery duration), "
                            "'vitals' (resting heart rate and HRV trends over time), "
                            "'clinical' (medical lab observations, FHIR records, blood work, or doctor records). "
                            "Defaults to 'summary'."
                        ),
                    },
                    "hours": {
                        "type": "number",
                        "description": "Optional time window in hours for intraday/granular queries (e.g. 2 for last 2 hours, 0.5 for last 30 minutes).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_workouts",
            "description": (
                f"Retrieve {cfg.USER_NAME}'s recorded exercise and workout sessions (walks, runs, strength training, yardwork, housework, gym sessions, cycling). "
                "Merges live Oura Ring activity sessions with Health Connect records. "
                "Use when asked about physical activities, recent walks, workout duration, calories burned, or exercise history."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of past days to query (default 7).",
                    },
                    "hours": {
                        "type": "number",
                        "description": "Optional number of past hours to query (e.g. 3 for workouts in the last 3 hours).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_google_drive",
            "description": (
                "Sync and update the latest Google Health Connect database export from Google Drive. "
                f"Use when {cfg.USER_NAME} asks to sync health data from Drive or update the health database."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "force": {
                        "type": "boolean",
                        "description": "Force re-download even if already up to date.",
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
                "Execute a shell command in the Evelyn workspace or Obsidian Vault. "
                "Use for service status checks, running scripts, git operations, or terminal tasks. "
                "Commands run in bash on Linux. Requires approval for destructive/modifying commands."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell/bash command to execute.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory (default: /home/rathius/evelyn or /home/rathius/obsidian_vault).",
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
                "Read the contents of a file in the workspace or Obsidian Vault. "
                "Use to inspect code, configuration, log files, or vault notes (e.g. 'Notes/...', 'Projects/...'). System directories (.obsidian, .git) are protected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path or relative path (e.g. 'Notes/Features/idea.md', 'scripts/test.py').",
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
                f"Write content to a file in the workspace or {cfg.USER_NAME}'s Obsidian Vault. "
                f"Use for creating or updating user dream journal entries (e.g. 'Dream Journal/Dream Entries/Dream Entry YYYY-MM-DD.md'), feature ideas, vault notes, scripts, or configuration files. "
                "System directories (.obsidian, .git) are protected. Requires approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path or relative path (e.g. 'Notes/Features/idea.md', 'scripts/my_script.py').",
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
    {
        "type": "function",
        "function": {
            "name": "manage_vault_list",
            "description": (
                f"Manage markdown checklists and lists in {cfg.USER_NAME}'s Obsidian Vault (e.g. Groceries, Packing, To-Dos, Hardware). "
                "Supports reading items, adding new items with quantity/unit and category sections (e.g. Produce, Dairy, Pantry), "
                "checking/completing items, unchecking, removing items, and clearing completed items."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the list note (e.g. 'Groceries', 'Packing', 'Hardware'). Defaults to 'Groceries'.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["read", "add", "check", "uncheck", "remove", "clear_completed", "list_all"],
                        "description": "Action to perform on the list.",
                    },
                    "items": {
                        "type": "array",
                        "description": (
                            "List of item objects or strings to add/check/remove. "
                            "For 'add', each item object can specify 'name', optional 'quantity', 'unit', and 'category'. "
                            "Example: [{'name': 'Whole Milk', 'quantity': 1, 'unit': 'gal', 'category': 'Dairy & Refrigerated'}, {'name': 'Spinach', 'category': 'Produce'}]. "
                            "Can also be simple strings: ['Whole Milk', 'Spinach']."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Item name"},
                                "quantity": {"type": "number", "description": "Quantity count/amount"},
                                "unit": {"type": "string", "description": "Measurement unit (e.g. 'gal', 'boxes', 'lbs', 'bunch')"},
                                "category": {"type": "string", "description": "Category or section header in the note (e.g. 'Produce', 'Dairy & Refrigerated', 'Pantry')"}
                            },
                            "required": ["name"]
                        }
                    },
                    "category": {
                        "type": "string",
                        "description": "Default category/section header for added items if not specified per item.",
                    },
                },
                "required": ["action"],
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
    "list_research_tasks": list_research_tasks,
    "inspect_research_task": inspect_research_task,
    "guide_research": guide_research,
    "check_new_research": check_new_research,
    "search_history": search_history,
    "create_calendar_event": create_calendar_event,
    "delete_calendar_event": delete_calendar_event,
    "sync_google_calendar": sync_google_calendar,
    "create_task": create_task,
    "complete_task": complete_task,
    "delete_task": delete_task,
    "list_tasks": list_tasks,
    "sync_google_tasks": sync_google_tasks,
    "get_agenda": get_agenda,
    "manage_vault_list": manage_vault_list,
    "get_health_metrics": get_health_metrics,
    "get_recent_workouts": get_recent_workouts,
    "sync_google_drive": sync_google_drive,
    "run_command": run_command,
    "read_file": read_file,
    "write_file": write_file,
}
