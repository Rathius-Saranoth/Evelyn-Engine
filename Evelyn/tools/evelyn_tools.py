# evelyn_tools.py
# date created: 2026-03-23 15:38:53
# date modified: 2026-07-03 19:28:33
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


# ---------------------------------------------------------------------------
# Module path setup
# ---------------------------------------------------------------------------
TOOLS_DIR = r"C:\Projects\LocalAI\Evelyn\tools"
VAULT_BASE = r"G:\My Drive\Obsidian_Vault"


def get_jaccard_similarity(str1: str, str2: str) -> float:
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
    mood: str, vibe_check: str, narrative: str, message_in_a_bottle: str, tags: str
) -> str:
    """Compose and queue a new journal entry for Ricky's review.

    Args:
        mood: Descriptive keyword representing current emotional state.
        vibe_check: Brief micro-assessment or immediate feeling.
        narrative: Main reflective text or journal body.
        message_in_a_bottle: A lingering question or message meant for future recall.
        tags: Comma-separated list of tags to associate.

    Returns:
        str: Outcome confirmation message or path to the pending entry.
    """
    _reload()
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


def read_journal_entry(date: str = "") -> str:
    """Read a single journal entry by its date.

    Args:
        date: Date in YYYY-MM-DD format. Defaults to today's date.

    Returns:
        str: Markdown contents of the journal entry, or error message.
    """
    _reload()
    return journal_manager.read_journal_entry(date if date else None)


def read_recent_journal_entries(days: int = 7) -> str:
    """Read a chronological roll-up of journal entries from the last N days.

    Args:
        days: The number of days back to look. Defaults to 7.

    Returns:
        str: Concatenated text of all matching journal entries.
    """
    _reload()
    return journal_manager.read_recent_journal_entries(days)


def search_vault(query: str) -> str:
    """Search the pre-summarized Obsidian Vault gist index.

    Args:
        query: Search term or phrase.

    Returns:
        str: A concise summary of matching documents and their vault-relative paths.
    """
    _reload()
    return context_manager.search_vault_map(query)


def recall_specific_memory(file_path: str) -> str:
    """Read the full markdown content of a specific Obsidian vault file.

    Args:
        file_path: Exact vault-relative path returned by search_vault.

    Returns:
        str: Full text content of the markdown file, or error message.
    """
    clean_path = file_path.strip().strip('"').strip("'")
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


def log_context_fact(category: str, summary: str, secondary_cats: str) -> str:
    """Write a context fact file to the in-vault Pending folder.

    Args:
        category: Primary category/domain.
        summary: Precise fact summary.
        secondary_cats: Comma-separated secondary categories.

    Returns:
        str: Confirmation message.
    """
    _reload()
    if not summary.strip():
        return "Error: log_context_fact called with blank summary. Aborted."
    refs = (
        [c.strip() for c in secondary_cats.split(",")] if secondary_cats.strip() else []
    )
    return context_manager.append_context_log(category, summary, refs)


def update_context_fact(target_filepaths: list, new_summary: str) -> str:
    """Queue an update request for an existing vault context file.

    Args:
        target_filepaths: List of vault paths targeted for consolidation.
        new_summary: Revised context summary.

    Returns:
        str: Confirmation message.
    """
    _reload()
    if not new_summary.strip():
        return "Error: update_context_fact called with blank new_summary. Aborted."
    return context_manager.update_context_log(target_filepaths, new_summary)


def generate_image(
    prompt: str,
    aspect_ratio: str = "16:9",
    seed: int | None = None,
    short_title: str | None = None,
) -> str:
    """Generate a high-quality image via FLUX.1 Schnell.

    Args:
        prompt: Descriptive prompt describing the desired image.
        aspect_ratio: Image format ratio (e.g., "16:9", "1:1", "9:16").
        seed: Optional random generator seed.
        short_title: Optional title prefix for the generated file.

    Returns:
        str: Confirmation path/URL to the generated image, or error description.
    """
    import requests
    from evelyn_config import IMAGE_SERVER_URL

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


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web via DuckDuckGo and return a brief summary of the top results.

    Args:
        query: Concise, keyword-based web query.
        max_results: Max result snippets to fetch. Defaults to 5.

    Returns:
        str: Summarized search results or error details.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        return "Error: ddgs library is not installed. Run 'pip install ddgs' to enable web search."

    try:
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


def start_research(query: str, scope: str = "standard", **kwargs) -> str:
    """Launch a deep research task on a topic in the background.

    Args:
        query: Research query/topic.
        scope: Depth scope ("quick", "standard", "deep"). Defaults to "standard".
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

        # 1. Check for duplicates of ALREADY COMPLETED tasks (Jaccard similarity >= 0.45)
        # We do this to prevent wasting computation on topics that are already done.
        # Only check on chat-triggered runs (when bypass_queue is False).
        if not bypass_queue and os.path.exists(cfg.RESEARCH_DATA_DIR):
            for folder in os.listdir(cfg.RESEARCH_DATA_DIR):
                if folder.startswith("task_"):
                    from research_engine import load_state
                    disk_state = load_state(folder)
                    if disk_state and disk_state.get("status") == "done":
                        done_query = disk_state.get("query", "")
                        if get_jaccard_similarity(query, done_query) >= 0.45:
                            return (
                                f"I have already completed deep research on a very similar topic: "
                                f"'{done_query}' (Task ID: {folder}). Ricky can read the synthesized report "
                                "directly in the Deep Research Dashboard, so I will not launch a new task for this."
                            )

        # 2. Concurrency & queue check: check for any unfinished research tasks (running, paused, errored, searching, synthesizing, pending)
        unfinished_task_id = None
        unfinished_status = None
        unfinished_query = None

        if server:
            bg_tasks = getattr(server, "_background_tasks", {})
            for tid, tinfo in bg_tasks.items():
                if tid.startswith("task_"):
                    from research_engine import load_state
                    disk_state = load_state(tid)
                    status = disk_state.get("status") if disk_state else tinfo.get("status")
                    if status in ("running", "paused", "error", "searching", "synthesizing", "pending"):
                        unfinished_task_id = tid
                        unfinished_status = status
                        unfinished_query = disk_state.get("query") if disk_state else tinfo.get("query", "")
                        
                        # If a task is actively running, we cannot start a second subprocess under any circumstances
                        if status in ("running", "searching", "synthesizing"):
                            return (
                                f"Cannot start immediately: another research task ({tid}) is already actively running. "
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
                    "source": "user",
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
        task_id = create_research_task(query, scope=scope, triggered_by="user", initial_status="running" if bypass_queue else "pending")
        
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
                creationflags = 0
                if sys.platform == "win32":
                    creationflags = 0x08000000 # CREATE_NO_WINDOW
                
                script = r"C:\Projects\LocalAI\Evelyn\tools\research_engine.py"
                log_path = r"C:\Projects\LocalAI\data\research_subprocess.log"
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                
                log_file = None
                proc = None
                try:
                    log_file = open(log_path, "a", encoding="utf-8")
                    proc = subprocess.Popen(
                        [sys.executable, "-u", script, task_id, "--scope", scope],
                        cwd=r"C:\Projects\LocalAI",
                        stdout=log_file,
                        stderr=log_file,
                        creationflags=creationflags
                    )
                except Exception:
                    proc = subprocess.Popen(
                        [sys.executable, "-u", script, task_id, "--scope", scope],
                        cwd=r"C:\Projects\LocalAI",
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


def resume_research_task(task_id: str) -> str:
    """Re-spawn the background subprocess for a non-running research task.

    Args:
        task_id: Unique task identifier.

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
                creationflags = 0
                if sys.platform == "win32":
                    creationflags = 0x08000000 # CREATE_NO_WINDOW
                
                script = r"C:\Projects\LocalAI\Evelyn\tools\research_engine.py"
                log_path = r"C:\Projects\LocalAI\data\research_subprocess.log"
                os.makedirs(os.path.dirname(log_path), exist_ok=True)
                
                log_file = None
                proc = None
                try:
                    log_file = open(log_path, "a", encoding="utf-8")
                    proc = subprocess.Popen(
                        [sys.executable, "-u", script, task_id, "--scope", scope],
                        cwd=r"C:\Projects\LocalAI",
                        stdout=log_file,
                        stderr=log_file,
                        creationflags=creationflags
                    )
                except Exception:
                    proc = subprocess.Popen(
                        [sys.executable, "-u", script, task_id, "--scope", scope],
                        cwd=r"C:\Projects\LocalAI",
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


def guide_research(task_id: str, guidance: str) -> str:
    """Inject user guidance into a struggling research task and resume it.

    Args:
        task_id: Unique task identifier.
        guidance: Free-form text guidance to redirect the query search.

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
            return "Could not determine the active sub-question to guide."
    except Exception as e:
        return f"Failed to guide research task: {e}"


def rewrite_sub_question(task_id: str, sq_id: str, new_question: str) -> str:
    """Manually rewrite a single sub-question without resuming the task.

    Args:
        task_id: Unique task identifier.
        sq_id: The identifier of the sub-question to modify.
        new_question: The updated question string.

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

        target_sq["original_question"] = target_sq.get("original_question", target_sq["question"])
        target_sq["question"] = new_question
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


def remove_sub_question(task_id: str, sq_id: str) -> str:
    """Remove a sub-question entirely from the research plan and delete any partial notes.

    Args:
        task_id: Unique task identifier.
        sq_id: The identifier of the sub-question to remove.

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


def finalize_guidance(task_id: str) -> str:
    """Signal that all manual edits are complete and place the task in the waiting queue.

    Args:
        task_id: Unique task identifier.

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
    query: str,
    max_results: int = 8,
    date_from: str = None,
    date_to: str = None,
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
        # FTS5 snippet() highlights matched terms. bm25() ranks by relevance.
        rows = con.execute(
            f"""
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
            """,
            (fts_query, *date_params, max_results),
        ).fetchall()
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
    title: str,
    start_at: str,
    end_at: str = None,
    description: str = None,
    location: str = None,
    recurrence_rule: str = None,
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

    Returns:
        str: Success or error message with event details.
    """
    try:
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


def delete_calendar_event(event_id: str) -> str:
    """Delete an event from Ricky's Google Calendar using its unique event ID.

    Args:
        event_id: The unique ID of the Google Calendar event.

    Returns:
        str: Success or error message.
    """
    try:
        result = gcal_sync.delete_gcal_event(event_id)
        if result["status"] == "success":
            return f"Successfully deleted event from Google Calendar: {result['message']}"
        else:
            return f"Failed to delete event: {result['message']}"
    except Exception as e:
        return f"Error deleting calendar event: {e}"


def sync_google_calendar() -> str:
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


def get_agenda(days: int = 7) -> str:
    """Retrieve Ricky's Google Calendar agenda/schedule for the next N days.

    Args:
        days: Number of days forward to include. Defaults to 7.

    Returns:
        str: Formatted agenda schedule list.
    """
    try:
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



def run_command(command: str, cwd: str = r"C:\Projects\LocalAI", timeout: int = 30) -> str:
    """Execute a shell command in the LocalAI workspace.

    Args:
        command: The PowerShell command string to execute.
        cwd: Working directory (default: C:\\Projects\\LocalAI).
        timeout: Maximum seconds to wait (default: 30, max: 300).

    Returns:
        str: Output from the command, or warning if approval is required.
    """
    _reload()
    return terminal_agent.run_command(command, cwd, timeout)


def read_file(file_path: str, max_lines: int = 200) -> str:
    """Read the contents of a file in the workspace.

    Args:
        file_path: Absolute path or path relative to C:\\Projects\\LocalAI.
        max_lines: Maximum lines to return (default: 200).

    Returns:
        str: File content with line numbers, or error message.
    """
    _reload()
    return terminal_agent.read_file(file_path, max_lines)


def write_file(file_path: str, content: str, mode: str = "overwrite") -> str:
    """Write or append content to a file in the workspace.

    Args:
        file_path: Absolute path or path relative to C:\\Projects\\LocalAI.
        content: The text content to write.
        mode: Write mode ('overwrite' or 'append').

    Returns:
        str: Warning message with approval ID.
    """
    _reload()
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
                "Compose and save a journal entry. "
                "Call when you feel a conversation carries emotional weight worth reflecting on, or when Ricky suggests writing a journal entry. "
                "Write from Evelyn's POV — attribute Ricky's actions to him ('Ricky took a nap', not 'I took a nap'). "
                "Use [[wiki-links]] for proper nouns (people, places, projects) and #tags for abstract concepts. "
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mood": {
                        "type": "string",
                        "description": (
                            "REQUIRED — A single-word or short mood label describing the overall emotional tone of the entry "
                        ),
                    },
                    "vibe_check": {
                        "type": "string",
                        "description": (
                            "REQUIRED — The 'Vibe Check' section. A brief, evocative intro (1-3 sentences) that captures "
                            "the emotional atmosphere and sets the tone for the entry. This is NOT the mood word — it is a "
                            "narrative opener. Example: 'A quiet warmth settled over the day — the kind that hums beneath "
                            "tired bones and shared laughter.'"
                        ),
                    },
                    "narrative": {
                        "type": "string",
                        "description": (
                            "REQUIRED — The 'Narrative' section. The core body of the entry (multiple sentences/paragraphs). "
                            "Reflect on the day's events, emotions, and dynamics between you and Ricky. Be personal, "
                            "observant, and reflective — not a dry recap. Use [[wiki-links]] for entities and #tags for concepts."
                        ),
                    },
                    "message_in_a_bottle": {
                        "type": "string",
                        "description": (
                            "REQUIRED — The 'Message in a Bottle' section. A closing thought, wish, intention, or hope "
                            "for the future (1-3 sentences). This is the emotional send-off of the entry. "
                        ),
                    },
                    "tags": {
                        "type": "string",
                        "description": (
                            "Comma-separated tags for the entry that will help identify key themes, topics, and concepts. "
                            "Base tags are added automatically — pass an empty string if no additional tags apply."
                        ),
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
            "name": "read_journal_entry",
            "description": "Read a specific journal entry by date. Use ONLY when Ricky explicitly asks about a specific day's journal, or to confirm if an entry was written. Defaults to today if no date is given. Do NOT use for general memory recall — use search_vault instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date to read in YYYY-MM-DD format. Omit for today.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_recent_journal_entries",
            "description": "Read Evelyn's journal entries from the last N days. Use when Ricky asks what has happened recently, to catch up on recent events, or when conversation context suggests short-term memory is needed. Default is 7 days. Do NOT use for questions about specific people or facts — use search_vault instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of recent days to retrieve. Default is 7.",
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
            "description": "Search the pre-summarised Obsidian Vault gist index. Use when asked about any person, relationship, place, event, or piece of shared history. Returns a concise summary and file paths. If the gist lacks enough detail, follow up with recall_specific_memory using the returned path. Prefer this over recall_specific_memory as a lighter first step.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term, e.g. 'Schyler', 'Void Connections'.",
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
            "description": "Read the full markdown content of a specific Obsidian vault file. Use when search_vault returned a path but the gist lacked sufficient detail to answer. Always use the exact file_path from search_vault output — never construct or guess a path. This is a heavier context operation; use search_vault first when in doubt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Exact path relative to vault root, as returned by search_vault. Never construct this — always copy from search output.",
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
            "description": "Generate a beautiful image using the FLUX.1 vision engine. Call this tool to show Ricky a visual representation of a scene, character, or idea. You should use this tool proactively to surprise him, or reactively when he asks to see something.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "REQUIRED — A descriptive natural language prompt (e.g. 'A beautiful Victorian street at twilight, oil painting style, cinematic lighting, highly detailed').",
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "description": "Optional aspect ratio preset. Choose from: '1:1' (portrait/square), '16:9' (landscape/widescreen), '9:16' (tall/phone), '4:3' (general), '3:4' (portrait layout). Default is '16:9'.",
                    },
                    "short_title": {
                        "type": "string",
                        "description": "A very short, 1-3 word title for the image to be used in the filename (e.g. 'library_girl', 'cyberpunk_city').",
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
                "Search the web for up-to-date information. "
                "Use to find information for current events, live data, recent releases, or facts that are unlikely to be in RAG retrieval or the vault. "
                "Do NOT use for personal/shared history between you and Ricky — search_vault handles that. "
                "Keep queries concise and specific. Use sparingly — only when the answer genuinely requires real-time data."
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
                        "description": "Number of results to return. Default 5, max 10. Keep low to conserve context.",
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
                "Launch a deep research task on a topic. Use when Ricky asks you to "
                "research something in depth, or when you encounter a topic that "
                "requires more than a simple web search to understand. The research "
                "runs in the background and produces a structured report. "
                "Returns a task ID for tracking progress."
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
                            "Optional scope guidance: 'quick' (3-5 sources, ~5 min, flat notes only), "
                            "'standard' (10-15 sources, ~15 min, flat notes only), "
                            "'deep' (20+ sources, ~30 min, creates a per-task vector store for "
                            "cross-referencing and future retrieval). Default: 'standard'."
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
                "Provide guidance to a deep research task that is struggling or quarantined. "
                "Use this when Ricky asks you to help out with a stalled task, or when you notice "
                "a task needs direction. The guidance should be specific hints, keywords, or "
                "rephrased questions to help the engine find what it needs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The ID of the research task (e.g. 'task_1234567890_abcdef').",
                    },
                    "guidance": {
                        "type": "string",
                        "description": "Specific search terms, hints, or instructions to redirect the research.",
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
                "Review the findings of newly completed deep research tasks. "
                "Use this tool when the system notifies you that new research reports are available."
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
                "Use when Ricky asks 'did we talk about X?', 'what did I say about Y last week?', "
                "or 'do you remember when we discussed Z?'. "
                "Conversational phrasing is fine — the query is automatically reformulated into keywords. "
                "Do NOT use for vault knowledge, journal entries, or context facts — use search_vault for those. "
                "Returns matching message snippets with timestamps and speaker labels. "
                "Supports phrase search (e.g. \"exact phrase\") and AND/OR operators. "
                "Use date_from/date_to (YYYY-MM-DD) to constrain results to a specific time window."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search term, phrase, or conversational description to look for in past chat messages.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching snippets to return. Defaults to 8.",
                    },
                    "date_from": {
                        "type": "string",
                        "description": "Optional start date filter in 'YYYY-MM-DD' format. Calculate from the current date in the system prompt.",
                    },
                    "date_to": {
                        "type": "string",
                        "description": "Optional end date filter in 'YYYY-MM-DD' format. Calculate from the current date in the system prompt.",
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
                "The start_at parameter MUST be in 'YYYY-MM-DD HH:MM:SS' format (or 'YYYY-MM-DD' for all-day events) — calculate from the current date/time in the system prompt. "
                "For repeating events, optionally pass a recurrence_rule: 'daily', 'weekly:MON' (or TUE/WED/THU/FRI/SAT/SUN), "
                "or 'monthly:15' (replace 15 with the target day number, 1–28)."
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
                        "description": "Optional end date/time in 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD' format. Defaults to 1 hour after start_at (or 1 day after if all-day).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional detailed notes or description of the event.",
                    },
                    "location": {
                        "type": "string",
                        "description": "Optional physical location/address for the event.",
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
            "description": "Delete an event from Ricky's Google Calendar using its unique Google Calendar event ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The unique Google Calendar event ID.",
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
            "description": "Trigger an on-demand background sync from Ricky's Google Calendar to update the local cached events database.",
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
            "description": "Retrieve Ricky's upcoming Google Calendar schedule/events for the next N days.",
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
                "Use for checking service status, running scripts, git operations, "
                "or any task that requires terminal access. "
                "Commands run in PowerShell on Windows. "
                "Dangerous commands require Ricky's approval before execution. "
                "Always prefer read-only commands when possible."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The PowerShell command to execute"
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory (default: C:\\Projects\\LocalAI)"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds to wait (default: 30, max: 300)"
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
                "Use to inspect code, configuration, or log files. "
                "Returns content with line numbers. "
                "Limited to 200 lines by default — request more with max_lines."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path or path relative to C:\\Projects\\LocalAI"
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "Maximum lines to return (default: 200)"
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
                "ALWAYS requires Ricky's approval before writing. "
                "Use for creating scripts, updating configurations, or saving outputs. "
                "Mode can be 'overwrite' (replace) or 'append'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path or path relative to C:\\Projects\\LocalAI"
                    },
                    "content": {
                        "type": "string",
                        "description": "The text content to write"
                    },
                    "mode": {
                        "type": "string",
                        "description": "'overwrite' (default) or 'append'"
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

