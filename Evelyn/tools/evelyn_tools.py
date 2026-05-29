# evelyn_tools.py
# date created: 2026-03-23 15:38:53
# date modified: 2026-05-25 19:54:06
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
    """Calculate Jaccard similarity between two strings using word tokens, excluding common stop words."""
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


def _reload():
    """Hot-reload all backing modules so live edits take effect without restarting."""
    for mod in (
        "journal_manager",
        "context_manager",
        "ingest_gists",
        "ingest_obsidian_knowledge",
    ):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])


# ===========================================================================
# Tool functions
# ===========================================================================


def write_journal_entry(
    mood: str, vibe_check: str, narrative: str, message_in_a_bottle: str, tags: str
) -> str:
    """Compose and queue a new journal entry for review."""
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
    """Read a single journal entry by date (YYYY-MM-DD). Defaults to today."""
    _reload()
    return journal_manager.read_journal_entry(date if date else None)


def read_recent_journal_entries(days: int = 7) -> str:
    """Read Evelyn's journal entries from the last N days."""
    _reload()
    return journal_manager.read_recent_journal_entries(days)


def search_vault(query: str) -> str:
    """Search the pre-summarised Obsidian Vault gist index.
    Returns a concise summary (gist) of matching documents and their vault-relative file paths.
    If the gist result lacks enough detail, follow up with recall_specific_memory using the returned path.
    """
    _reload()
    return context_manager.search_vault_map(query)


def recall_specific_memory(file_path: str) -> str:
    """Read the full markdown content of a specific Obsidian vault file.
    Use when search_vault returned a path but the gist lacked sufficient detail.
    Always use the exact file path returned by search_vault — never construct or guess one."""
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
    """Write a context fact file to the in-vault Pending folder (system use)."""
    _reload()
    if not summary.strip():
        return "Error: log_context_fact called with blank summary. Aborted."
    refs = (
        [c.strip() for c in secondary_cats.split(",")] if secondary_cats.strip() else []
    )
    return context_manager.append_context_log(category, summary, refs)


def update_context_fact(target_filepaths: list, new_summary: str) -> str:
    """Write an update-request file to Pending for an existing vault context file (system use)."""
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
    """Generate a high-quality image from a natural language prompt via FLUX.1 [schnell].
    
    Accepts preset aspect ratios: 1:1, 16:9, 9:16, 4:3, 3:4.
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
    """Trigger background sync of vault gists and core memory into the RAG database (system use)."""
    import threading

    def _run():
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
    Use only when the question requires up-to-date information, real-time data, or
    facts that are unlikely to be in training data or the vault (e.g. current events,
    live prices, recent releases). For personal/shared history, always prefer search_vault.
    Keep queries concise and specific.
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
    """Launch a deep research task on a topic in the background."""
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
                        if status == "running":
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
                        from research_engine import load_state
                        disk_state = load_state(task_id)
                        disk_status = disk_state.get("status") if disk_state else None
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
    """Re-spawn the background subprocess for a paused, cancelled, or failed research task to resume it."""
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
        if server:
            bg_tasks = getattr(server, "_background_tasks", None)
            if bg_tasks:
                for tid, tinfo in bg_tasks.items():
                    if tid.startswith("task_") and tinfo.get("status") == "running":
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
                        from research_engine import load_state
                        disk_state = load_state(task_id)
                        disk_status = disk_state.get("status") if disk_state else None
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
    """Inject user guidance into a struggling research task and resume it."""
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
            
        if state.get("status") == "running":
            import sys
            import time
            server = sys.modules.get("evelyn_server")
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
}
