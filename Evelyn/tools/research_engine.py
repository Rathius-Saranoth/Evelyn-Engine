# research_engine.py
# date created: 2026-05-26
# date modified: 2026-07-14 21:08:55
# tags: #research, #orchestrator, #engine, #statemachine, #cli

"""research_engine.py — Core Orchestrator for Evelyn's Deep Research.

Executes a robust, state-machine-driven research pipeline. Persists progress
to `state.json` at every step to ensure complete crash-safety. Uses
confidence-driven termination as the primary exit signal, backed by generous
emergency safety limits.
"""

import asyncio
import datetime
import importlib
import json
import os
import re
import shutil
import sys
import time
import traceback
from typing import List, Dict, Any, Tuple, Optional

import httpx

# Reconfigure stdout/stderr to force UTF-8 output to avoid Windows CP1252 character mapping crashes on international titles
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Ensure Tools and root directories are in system path
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(TOOLS_DIR, "..", ".."))
if TOOLS_DIR not in sys.path:
    sys.path.append(TOOLS_DIR)
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

import evelyn_config as cfg # [[evelyn_config.py]]
import web_reader # [[web_reader.py]]
import research_prompts # [[research_prompts.py]]
import evelyn_tools # [[evelyn_tools.py]]

# Virtual memory cache to store chunks from previous deep research collections without re-scrapes
VIRTUAL_SOURCES: Dict[str, str] = {}


def _in_research_window() -> bool:
    """Return True if the current local hour is within the configured active-hours window.

    Mirrors the same logic in evelyn_server._in_research_window() so the engine
    can enforce the circadian boundary even when running as a subprocess (where
    the server module is not importable).

    If RESEARCH_ACTIVE_HOURS_START and RESEARCH_ACTIVE_HOURS_END are both 0 the
    window check is disabled and research is permitted at any hour.

    Returns:
        bool: True if research steps are permitted to execute right now.
    """
    importlib.reload(cfg)
    start = getattr(cfg, "RESEARCH_ACTIVE_HOURS_START", 6)
    end   = getattr(cfg, "RESEARCH_ACTIVE_HOURS_END",   21)
    if start == 0 and end == 0:
        return True  # Windowing disabled
    return start <= time.localtime().tm_hour < end


def parse_json_response(raw_response: str) -> Any:
    """Parse JSON from an LLM response, stripping markdown code fences if present.

    Args:
        raw_response: The raw string response from the LLM.

    Returns:
        Any: The parsed JSON data (dict, list, etc.).
    """
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        if cleaned.endswith("```"):
            cleaned = cleaned.rsplit("\n", 1)[0]
    cleaned = cleaned.strip()
    return json.loads(cleaned)


def get_task_dir(task_id: str) -> str:
    """Return the absolute path to a task's workspace directory.

    Args:
        task_id: The unique task identifier.

    Returns:
        str: Absolute directory path.
    """
    return os.path.join(cfg.RESEARCH_DATA_DIR, task_id)


def recalculate_total_sources(task_id: str, state: Dict[str, Any]) -> int:
    """Recalculate the true source count based on actual citations in active notes files."""
    task_dir = get_task_dir(task_id)
    active_src_ids = set()
    for sq in state.get("plan", {}).get("sub_questions", []):
        if sq.get("status") not in ("removed", "split"):
            notes_file = os.path.join(task_dir, f"{sq['id']}_notes.md")
            sq_sources = set()
            if os.path.exists(notes_file):
                try:
                    with open(notes_file, "r", encoding="utf-8") as f:
                        content = f.read()
                    matches = re.findall(r"\[src_(\d+)\]", content)
                    for m in matches:
                        src_name = f"src_{m}"
                        active_src_ids.add(src_name)
                        sq_sources.add(src_name)
                except Exception:
                    pass
            sq["source_count"] = len(sq_sources)
            
    return len(active_src_ids)


def update_limit_warnings(task_id: str, state: Dict[str, Any]) -> None:
    """Check task and subquestion state against limits and populate warning flags."""
    limit_warnings = set()
    
    max_total_sources = state.get("max_total_sources", 100)
    if state.get("total_sources", 0) >= max_total_sources:
        limit_warnings.add("total_sources_cap_reached")
        
    turn_limit = state.get(
        "max_orchestrator_turns",
        getattr(cfg, "RESEARCH_MAX_ORCHESTRATOR_TURNS", 50)
    )
    if state.get("orchestrator_turns", 0) >= turn_limit:
        limit_warnings.add("orchestrator_turns_cap_reached")
        
    timeout_limit = state.get(
        "wall_clock_timeout",
        getattr(cfg, "RESEARCH_WALL_CLOCK_TIMEOUT", 7200)
    )
    if state.get("accumulated_runtime", 0.0) >= timeout_limit:
        limit_warnings.add("timeout_reached")
        
    state["limit_warnings"] = list(limit_warnings)
    
    max_sources_per_sq = state.get("max_sources_per_sq", 15)
    max_search_depth = state.get("max_search_depth", 8)
    
    for sq in state.get("plan", {}).get("sub_questions", []):
        sq_warnings = set()
        
        if sq.get("source_count", 0) >= max_sources_per_sq:
            sq_warnings.add("source_cap_reached")
            
        if sq.get("status") not in ("done", "removed", "split"):
            if sq.get("search_depth", 0) >= max_search_depth - 1:
                sq_warnings.add("depth_cap_reached")
                
        sq["limit_warnings"] = list(sq_warnings)


def load_state(task_id: str) -> Optional[Dict[str, Any]]:
    """Load the persisted state of a research task from disk.

    Args:
        task_id: The unique task identifier.

    Returns:
        Optional[Dict[str, Any]]: State dictionary, or None if not found/corrupt.
    """
    state_file = os.path.join(get_task_dir(task_id), "state.json")
    if not os.path.exists(state_file):
        return None
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
        if state:
            state["total_sources"] = recalculate_total_sources(task_id, state)
            update_limit_warnings(task_id, state)
        return state
    except Exception as e:
        print(f"[RESEARCH_ENGINE ERROR] Failed to load state for {task_id}: {e}", flush=True)
        return None


def save_state(task_id: str, state: Dict[str, Any], ignore_disk_status: bool = False) -> None:
    """Persist the current research task state to disk.

    Ensures the task directory exists before writing.

    Args:
        task_id: The unique task identifier.
        state: State dictionary to save.
        ignore_disk_status: If True, bypass merging old out-of-band statuses from disk.
    """
    task_dir = get_task_dir(task_id)
    os.makedirs(task_dir, exist_ok=True)
    state_file = os.path.join(task_dir, "state.json")
    
    if not ignore_disk_status and os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                disk_state = json.load(f)
                disk_status = disk_state.get("status")
                if disk_status in ("paused", "cancelled", "error"):
                    state["status"] = disk_status
                if "termination_reason" in disk_state and disk_state["termination_reason"]:
                    state["termination_reason"] = disk_state["termination_reason"]
                if "error" in disk_state and disk_state["error"]:
                    state["error"] = disk_state["error"]
        except Exception:
            pass
            
    state["updated_at"] = datetime.datetime.now().isoformat()
    update_limit_warnings(task_id, state)
    
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[RESEARCH_ENGINE ERROR] Failed to save state for {task_id}: {e}", flush=True)


def create_research_task(
    query: str,
    scope: str = "standard",
    triggered_by: str = "user",
    initial_status: str = "pending",
    intent_frame: Optional[str] = None,
) -> str:
    """Initialize a brand-new research task and persist its base state.

    Args:
        query: The main search query or research topic, be specific.
        scope: Scope of the research ('quick', 'standard', 'deep').
        triggered_by: Identifies the initiator ('user', 'idle', 'evelyn').
        initial_status: The initial status of the task ('pending' or 'running').
        intent_frame: Optional 2-3 sentence string describing why this topic
            matters and what kind of answer is needed. When provided, it skips
            LLM-based frame generation in step_plan(). Defaults to None.

    Returns:
        str: Generated unique task_id.
    """
    importlib.reload(cfg)
    
    # Generate unique task_id based on timestamp
    task_id = f"task_{int(time.time())}_{os.urandom(4).hex()}"
    
    # Establish scope presets.
    # Each preset is fully self-contained so tasks carry their own budgets in
    # state.json rather than relying on config at runtime. This means changing
    # evelyn_config.py does not retroactively affect tasks already in flight.
    presets = {
        "quick": {
            "sub_questions_limit": 3,
            "threshold": 70,
            "max_depth": 2,
            "max_sources": 5,
            "max_orchestrator_turns": 30,
            "wall_clock_timeout": 1800,    # 30 min
            "min_sources_per_sq": 1,
        },
        "standard": {
            "sub_questions_limit": 5,
            "threshold": 80,
            "max_depth": 3,
            "max_sources": 8,
            "max_orchestrator_turns": 80,
            "wall_clock_timeout": 7200,    # 2 hours
            "min_sources_per_sq": 2,
        },
        "deep": {
            "sub_questions_limit": 8,
            "threshold": 85,
            "max_depth": 8,
            "max_sources": 15,
            "max_orchestrator_turns": 200,
            "wall_clock_timeout": 28800,   # 8 hours
            "min_sources_per_sq": 3,
        }
    }
    
    # Default fallback to standard if scope invalid
    scope = scope.lower() if scope.lower() in presets else "standard"
    scope_cfg = presets[scope]
    
    state = {
        "task_id": task_id,
        "query": query,
        "scope": scope,
        "status": initial_status,
        "created_at": datetime.datetime.now().isoformat(),
        "updated_at": datetime.datetime.now().isoformat(),
        "triggered_by": triggered_by,
        "notified": False,
        "vault_path": None,
        "confidence": 0,
        "intent_frame": intent_frame,  # None triggers LLM generation in step_plan()
        "plan": {
            "sub_questions": []
        },
        "sources_registry": [],
        "current_step": "plan",
        "current_sq_idx": 0,
        "search_depth": 0,
        "total_sources": 0,
        "ollama_calls": 0,
        "orchestrator_turns": 0,
        "accumulated_runtime": 0.0,
        "termination_reason": None,
        "error": None,
        
        # Scoped thresholds and budgets — self-contained per task
        "confidence_threshold": scope_cfg["threshold"],
        "max_search_depth": scope_cfg["max_depth"],
        "max_sources_per_sq": scope_cfg["max_sources"],
        "sub_questions_limit": scope_cfg["sub_questions_limit"],
        "max_orchestrator_turns": scope_cfg["max_orchestrator_turns"],
        "wall_clock_timeout": scope_cfg["wall_clock_timeout"],
        "min_sources_per_sq": scope_cfg["min_sources_per_sq"],
    }
    
    save_state(task_id, state)
    print(f"[RESEARCH_ENGINE] Created task {task_id} (Scope: {scope}) for query: '{query}'", flush=True)
    return task_id




async def call_ollama(prompt_messages: List[Dict[str, str]], num_predict: int = 2048) -> str:
    """Helper to communicate with Ollama synchronously or asynchronously.

    Bypasses deep conversational states/history to maximize text context.

    Args:
        prompt_messages: Format-compliant list of prompt message dicts.
        num_predict: Maximum prediction tokens.

    Returns:
        str: Raw response text from the model.
    """
    importlib.reload(cfg)
    
    override = getattr(cfg, "RESEARCH_MODEL_OVERRIDE", "default")
    model = cfg.MODEL_NAME if override == "default" else override
    
    options = {
        "num_ctx": cfg.NUM_CTX,
        "num_predict": num_predict,
        "temperature": 0.3, # Highly objective, low randomness for research
        "min_p": cfg.MIN_P,
        "top_k": cfg.TOP_K,
        "top_p": cfg.TOP_P,
    }
    
    payload = {
        "model": model,
        "messages": prompt_messages,
        "stream": True,
        "options": options,
        "think": False # Native reasoning off to fit maximum factual context
    }
    
    content_buffer = ""
    
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            async with client.stream("POST", f"{cfg.OLLAMA_URL}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        import json
                        chunk = json.loads(line)
                        msg = chunk.get("message", {})
                        content_buffer += msg.get("content", "")
                    except json.JSONDecodeError:
                        continue
    except httpx.ConnectError as ce:
        raise RuntimeError(
            f"Ollama server connection failed (is Ollama running? URL: {cfg.OLLAMA_URL}). Error: {ce}"
        ) from ce
    except httpx.TimeoutException as te:
        raise RuntimeError(
            "Ollama request timed out after 600.0 seconds. The model may be thrashing or GPU memory is saturated."
        ) from te
    except httpx.HTTPStatusError as hse:
        raise RuntimeError(
            f"Ollama server returned HTTP error status {hse.response.status_code}. Response: {hse.response.text}"
        ) from hse
        
    content = content_buffer.strip()
    return re.sub(r"^.*?</think>", "", content, flags=re.DOTALL).strip()


def parse_web_search_results(web_results: str) -> List[Tuple[str, str]]:
    """Parse URLs and titles from DuckDuckGo search string format.

    Args:
        web_results: Output of evelyn_tools.web_search.

    Returns:
        List[Tuple[str, str]]: List of (title, URL) tuples.
    """
    results = []
    # Match the exact formatting produced by web_search tool:
    # "{i}. {title}\n   {href}\n   {body}"
    pattern = re.compile(r"^\d+\.\s*(.*?)\n\s*(https?://[^\s\n]+)", re.MULTILINE)
    
    for match in pattern.finditer(web_results):
        title = match.group(1).strip().strip('"\'*')
        url = match.group(2).strip()
        results.append((title, url))
        
    return results


def parse_vault_search_results(vault_res: str) -> List[Tuple[str, str]]:
    """Parse titles and paths from search_vault_map output.

    Args:
        vault_res: Formatted output string from search_vault_map.

    Returns:
        List[Tuple[str, str]]: List of (title, vault_relative_path) tuples.
    """
    results = []
    # Match the exact formatting produced by search_vault_map:
    # "--- {title} ---\nPath: {path}"
    pattern = re.compile(r"^---\s*(.*?)\s*---\nPath:\s*(.*?)$", re.MULTILINE)
    for match in pattern.finditer(vault_res):
        title = match.group(1).strip()
        path = match.group(2).strip()
        results.append((title, path))
    return results
async def formulate_search_query(
    question_text: str,
    task_type: str,
    state: Dict[str, Any],
    intent_frame: str = "",
) -> str:
    """Formulate a short, atomic web-search query from a sub-question or gap string.

    Always runs an LLM formulation pass — research tasks execute during idle time,
    so the extra ~1-3s call per search round is immaterial to responsiveness, and
    it catches thesis-style phrasing that a cheap heuristic alone would miss (e.g.
    a short, grammatically clean clause like "regulatory mechanisms underlying X"
    passes a word-count/conjunction check but is still not how a person searches).

    The formulated output is validated with the deterministic is_atomic_query()
    heuristic. On failure, formulation is retried once with the specific failure
    reason fed back to the model. If the retry also fails validation, falls back
    to a code-only truncation of the original text so a stalled formulation never
    blocks a search round.

    Args:
        question_text: The sub-question or gap string driving this search round.
        task_type: Classified task type, passed through to the prompt builder.
        state: Task state dict — mutated to increment ollama_calls for each
               formulation attempt made.
        intent_frame: Optional 2-3 sentence research intent block. Passed through
            to build_search_query_prompt() to keep formulated queries at the
            correct practical depth. Defaults to empty string (omitted).

    Returns:
        str: A short, search-engine-ready query string.
    """
    retry_reason = None

    for attempt in range(2):
        prompt = research_prompts.build_search_query_prompt(
            question_text,
            task_type=task_type,
            retry_reason=retry_reason,
            intent_frame=intent_frame,
        )
        messages = [
            {"role": "system", "content": research_prompts.get_system_prompt(domain_level=state.get("domain_level", "specialist"))},
            {"role": "user", "content": prompt},
        ]

        state["ollama_calls"] += 1
        raw = await call_ollama(messages, num_predict=100)
        candidate = raw.strip().strip('"\'\'').split("\n")[0].strip()

        is_ok, reason = research_prompts.is_atomic_query(candidate)
        if is_ok and candidate:
            return candidate

        print(
            f"[RESEARCH_ENGINE] Search query formulation attempt {attempt + 1} rejected "
            f"({reason}). Candidate was: '{candidate}'",
            flush=True,
        )
        retry_reason = reason

    fallback = _truncate_query_fallback(question_text)
    print(
        f"[RESEARCH_ENGINE WARNING] Search query formulation failed validation twice. "
        f"Falling back to truncated original: '{fallback}'",
        flush=True,
    )
    return fallback


def _truncate_query_fallback(question_text: str, max_words: int = 8) -> str:
    """Deterministic, code-only fallback query used when LLM formulation fails twice.

    Strips question marks/quotes and a common leading question stem, then
    truncates to max_words. This is a last resort only — it does not attempt
    to be smart about atomicity, it just guarantees the pipeline never stalls
    waiting on a formulation call that keeps failing validation.

    Args:
        question_text: The original sub-question or gap string.
        max_words: Maximum words to retain.

    Returns:
        str: A short, deterministic query string.
    """
    text = re.sub(r"[?\"']", "", question_text).strip()
    text_lower = text.lower()
    leading_stopwords = (
        "what is", "what are", "how does", "how do", "why is",
        "why does", "who is", "which",
    )
    for sw in leading_stopwords:
        if text_lower.startswith(sw):
            text = text[len(sw):].strip()
            break
    words = text.split()
    return " ".join(words[:max_words])


async def _rewrite_subquestion(
    task_id: str,
    state: Dict[str, Any],
    sq: Dict[str, Any],
    gaps: List[str],
) -> None:
    """Attempt to rewrite a sub-question's phrasing to escape a barren search space.

    Shared by step_evaluate() (low-confidence retry) and the zero-result branch
    of step_search_and_extract() (a search round that returned no sources at all).
    Performs the same semantic-divergence check both call sites previously did
    inline: the rewrite is only applied if it actually differs from the current
    phrasing, so a repeated/empty response doesn't burn the search budget for
    nothing.

    Mutates sq in place (question, original_question, gaps) and state
    (ollama_calls). Does not touch state["current_step"] or state["search_depth"]
    — callers remain responsible for advancing those, since the two call sites
    advance them slightly differently.

    Args:
        task_id: Unique task identifier.
        state: Task state dict — mutated to increment ollama_calls.
        sq: The sub-question dict to potentially rewrite, mutated in place.
        gaps: List of gap strings/reasons driving the rewrite. May be empty
              (e.g. the zero-result path, where no evaluate step has run yet).
    """
    task_dir = get_task_dir(task_id)
    notes_file = os.path.join(task_dir, f"{sq['id']}_notes.md")
    current_notes = ""
    if os.path.exists(notes_file):
        with open(notes_file, "r", encoding="utf-8") as f:
            current_notes = f.read()

    rewrite_prompt = research_prompts.build_rewrite_prompt(sq["question"], current_notes, gaps)
    rewrite_messages = [
        {"role": "system", "content": research_prompts.get_system_prompt(domain_level=state.get("domain_level", "specialist"))},
        {"role": "user", "content": rewrite_prompt},
    ]
    state["ollama_calls"] += 1
    print(f"[RESEARCH_ENGINE] Auto-rewriting SQ {sq['id']}...", flush=True)
    rewritten_q = await call_ollama(rewrite_messages, num_predict=512)
    rewritten_q = rewritten_q.strip()

    gaps_file = os.path.join(task_dir, f"{sq['id']}_gaps.json")

    # Semantic divergence check: prevent verbatim echoing
    if rewritten_q.lower() == sq["question"].lower() or not rewritten_q:
        print(
            "[RESEARCH_ENGINE WARNING] Auto-rewrite returned identical/empty question. "
            "Keeping original.",
            flush=True,
        )
        return

    print(f"[RESEARCH_ENGINE] Rewrote SQ to: '{rewritten_q}'", flush=True)
    sq["original_question"] = sq.get("original_question", sq["question"])
    sq["question"] = rewritten_q
    sq["gaps"] = []

    # Clear gaps file since the rewrite absorbs them
    if os.path.exists(gaps_file):
        os.remove(gaps_file)


def query_previous_deep_research(search_query: str, current_task_id: str, limit: int = 3) -> List[Dict[str, Any]]:
    """Query completed deep-scope research task collections for relevant chunks.

    Args:
        search_query: Semantic search query string.
        current_task_id: Current research task identifier to exclude.
        limit: Maximum matching chunks to return.

    Returns:
        List[Dict[str, Any]]: Sorted chunks from prior deep runs.
    """
    results = []
    try:
        import chroma_rag
        
        # Check if research folder exists
        if not os.path.exists(cfg.RESEARCH_DATA_DIR):
            return []
            
        for folder in os.listdir(cfg.RESEARCH_DATA_DIR):
            if folder == current_task_id:
                continue
            state_path = os.path.join(cfg.RESEARCH_DATA_DIR, folder, "state.json")
            if not os.path.exists(state_path):
                continue
                
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    task_state = json.load(f)
                if task_state.get("scope") == "deep" and task_state.get("status") == "done":
                    collection_name = f"research_{folder}"
                    # Query this task's collection
                    chunks = chroma_rag.query_collection(search_query, collection_name, n_results=limit)
                    for chunk in chunks:
                        if chunk.get("distance", 1.0) <= cfg.RAG_DISTANCE_THRESHOLD:
                            chunk["task_query"] = task_state.get("query", "")
                            chunk["task_id"] = folder
                            results.append(chunk)
            except Exception:
                continue
                
        # Sort results by distance and return top matches
        results.sort(key=lambda x: x["distance"])
    except Exception as e:
        print(f"[RESEARCH_ENGINE WARNING] Failed cross-task search: {e}", flush=True)
        
    return results[:limit]


async def try_resolve_directly(task_id: str, state: Dict[str, Any]) -> bool:
    """Check whether a research query can be resolved without launching research at all.

    Runs as the first action of step_plan(). Checks the query against a
    deterministic time-sensitivity gate, then (if it passes) against recent
    chat history and existing live memory facts via one conservative LLM
    self-assessment call. If the query is judged already resolved with high
    confidence, the entire task directory is deleted -- no state.json, no
    report, no vault write ever remains on disk. No answer text is stored
    anywhere; the person can simply ask again and get a live response.

    Args:
        task_id: The unique task identifier (about to be deleted on success).
        state: The task's in-memory state dict. Used only for query text and
               ollama_calls bookkeeping before deletion; never saved back to
               disk on the success path.

    Returns:
        bool: True if the task was resolved and its directory deleted (the
              caller must stop immediately and not touch state.json again).
              False if research should proceed normally.
    """
    importlib.reload(cfg)
    if not getattr(cfg, "RESEARCH_NECESSITY_PREFILTER_ENABLED", True):
        return False

    query = state["query"]

    if research_prompts.is_time_sensitive_query(query):
        print(
            f"[RESEARCH_ENGINE] Necessity check skipped for '{query}' -- "
            "time-sensitive query, proceeding with full research.",
            flush=True,
        )
        return False

    # Gather evidence: recent chat history + keyword-overlapping live memory facts.
    # Both are cheap, already-existing lookups -- no new infrastructure needed.
    evidence_parts = []

    history = get_recent_chat_history(20)
    if history:
        history_text = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in history)
        evidence_parts.append(f"### Recent Conversation History:\n{history_text}")

    matched_entries = []
    try:
        import memory_db
        all_entries = memory_db.get_all_entries(statuses=["live"])
        for entry in all_entries:
            overlap = evelyn_tools.get_jaccard_similarity(query, entry.get("observation", ""))
            if overlap >= 0.2:
                matched_entries.append((overlap, entry))
        matched_entries.sort(key=lambda x: x[0], reverse=True)
        matched_entries = matched_entries[:5]
    except Exception as e:
        print(f"[RESEARCH_ENGINE WARNING] Failed to query memory_db for necessity check: {e}", flush=True)

    if matched_entries:
        memory_text = "\n".join(
            f"- [{entry['category']}] {entry['observation']}" for _, entry in matched_entries
        )
        evidence_parts.append(f"### Recorded Memory Facts:\n{memory_text}")

    evidence_text = "\n\n".join(evidence_parts)

    prompt = research_prompts.build_necessity_check_prompt(query, evidence_text)
    messages = [
        {"role": "system", "content": research_prompts.get_system_prompt()},
        {"role": "user", "content": prompt},
    ]

    state["ollama_calls"] += 1
    raw_response = await call_ollama(messages, num_predict=200)

    try:
        result = parse_json_response(raw_response)
        needs_research = bool(result.get("needs_research", True))
        confidence = int(result.get("confidence", 0))
    except Exception as e:
        print(
            f"[RESEARCH_ENGINE WARNING] Failed to parse necessity check JSON: {e}. "
            "Proceeding with full research.",
            flush=True,
        )
        return False

    threshold = getattr(cfg, "RESEARCH_NECESSITY_CONFIDENCE_THRESHOLD", 90)

    if needs_research or confidence < threshold:
        print(
            f"[RESEARCH_ENGINE] Necessity check: research still needed for '{query}' "
            f"(needs_research={needs_research}, confidence={confidence}%).",
            flush=True,
        )
        return False

    # Resolved directly -- record that the evidence was used, then delete the
    # task directory entirely. No report, no vault write, no trace on disk.
    for _, entry in matched_entries:
        try:
            memory_db.touch_entry_retrieved(entry["id"])
        except Exception:
            pass

    print(
        f"[RESEARCH_ENGINE] Query '{query}' resolved directly without research "
        f"(confidence={confidence}%). Deleting task directory -- no trace retained.",
        flush=True,
    )

    task_dir = get_task_dir(task_id)
    try:
        shutil.rmtree(task_dir, ignore_errors=True)
    except Exception as e:
        print(f"[RESEARCH_ENGINE WARNING] Failed to clean up resolved task directory: {e}", flush=True)

    return True


async def _generate_intent_frame(state: Dict[str, Any]) -> str:
    """Generate a 2-3 sentence research intent frame for a user-triggered task.

    Called once in step_plan() when state['intent_frame'] is None (i.e. the
    task was user-triggered and no intent was provided at creation time).
    Uses recent conversation history as context so the frame reflects the
    person's actual situation rather than being a generic restatement of the
    query. Evelyn-triggered tasks already carry a frame from
    self_initiate_research_topics() and skip this call entirely.

    Args:
        state: Task state dict — mutated to increment ollama_calls.

    Returns:
        str: The generated 2-3 sentence intent frame, or empty string on
        failure (graceful degradation — the pipeline continues without focus
        anchoring rather than blocking).
    """
    history = get_recent_chat_history(20)
    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in history
    ) if history else ""

    prompt = research_prompts.build_intent_frame_prompt(state["query"], history_text)
    messages = [
        {"role": "system", "content": research_prompts.get_system_prompt()},
        {"role": "user", "content": prompt},
    ]
    state["ollama_calls"] += 1
    try:
        frame = await call_ollama(messages, num_predict=150)
        frame = frame.strip()
        if frame:
            print(f"[RESEARCH_ENGINE] Generated intent frame: '{frame}'", flush=True)
        return frame
    except Exception as e:
        print(
            f"[RESEARCH_ENGINE WARNING] Intent frame generation failed: {e}. "
            "Proceeding without frame.",
            flush=True,
        )
        return ""


async def step_plan(task_id: str, state: Dict[str, Any]) -> Optional[bool]:

    """Execute the PLAN step of a research task.

    First checks whether the query can be resolved without research at all
    (necessity pre-filter). If not, classifies the query's task_type and
    domain_level (zero LLM cost), then generates a single seed sub-question --
    the first, most foundational thing to investigate. Further sub-questions,
    if any, are generated lazily by step_evaluate()'s coverage check one at a
    time, based on actual gaps found, rather than planned in a batch upfront.

    Args:
        task_id: Unique task identifier.
        state: State dictionary to modify.

    Returns:
        Optional[bool]: True if the task was resolved directly and its
        directory deleted -- the caller must stop immediately without
        touching state.json again. None otherwise, with state saved normally
        and the caller expected to continue the loop.
    """
    print(f"[RESEARCH_ENGINE] Planning task {task_id}...", flush=True)

    if getattr(cfg, "RESEARCH_NECESSITY_PREFILTER_ENABLED", True):
        resolved = await try_resolve_directly(task_id, state)
        if resolved:
            return True

    # Classify query type and domain level once at plan time — zero LLM cost (Hermes Tier 2 #8b)
    task_type = research_prompts.classify_research_query(state["query"])
    domain_level = research_prompts.classify_domain_level(state["query"])
    state["task_type"] = task_type
    state["domain_level"] = domain_level
    print(f"[RESEARCH_ENGINE] Classified query: task_type='{task_type}', domain_level='{domain_level}'", flush=True)

    # Generate intent frame if not already set (user-triggered tasks with no explicit intent;
    # Evelyn-triggered tasks carry a pre-generated frame from self_initiate_research_topics()).
    if not state.get("intent_frame"):
        state["intent_frame"] = await _generate_intent_frame(state)

    prompt = research_prompts.build_seed_subquestion_prompt(
        state["query"],
        domain_level=domain_level,
        intent_frame=state.get("intent_frame", ""),
    )

    messages = [
        {"role": "system", "content": research_prompts.get_system_prompt(domain_level=domain_level)},
        {"role": "user", "content": prompt}
    ]


    state["ollama_calls"] += 1
    raw_response = await call_ollama(messages, num_predict=150)
    seed_question = raw_response.strip().strip('"\'').split("\n")[0].strip()

    if not seed_question:
        print("[RESEARCH_ENGINE WARNING] Seed sub-question generation returned empty. Defaulting to main query.", flush=True)
        seed_question = state["query"]

    state["plan"]["sub_questions"] = [{
        "id": "sq_01",
        "question": seed_question,
        "status": "pending",
        "source_count": 0,
        "confidence": 0,
        "search_depth": 0
    }]

    state["current_step"] = "search"
    state["current_sq_idx"] = 0
    state["search_depth"] = 0
    state["status"] = "searching"

    save_state(task_id, state)
    print(f"[RESEARCH_ENGINE] Seed sub-question: '{seed_question}'", flush=True)


async def step_search_and_extract(task_id: str, state: Dict[str, Any]) -> None:
    """Execute search query formulation, web fetching, and fact extraction.

    Args:
        task_id: Unique task identifier.
        state: State dictionary to modify.
    """
    sq_idx = state["current_sq_idx"]
    sq_list = state["plan"]["sub_questions"]
    
    if sq_idx >= len(sq_list):
        state["current_step"] = "synthesize"
        save_state(task_id, state)
        return
        
    sq = sq_list[sq_idx]
    print(f"[RESEARCH_ENGINE] Processing SQ ({sq['id']}): '{sq['question']}' (Search Round {state['search_depth'] + 1})", flush=True)
    
    # Load any existing gaps or notes to determine the search basis text.
    # When gaps exist, use the gap text as the basis rather than the original
    # sub-question — the gap is already a targeted phrase produced by the
    # evaluator. Either way, this raw text is NOT sent directly to the search
    # engine: it is passed through formulate_search_query() below, since both
    # sub-questions and gaps are authored for reasoning/notes and are prone to
    # compound or thesis-style phrasing that search engines rank poorly.
    gaps_file = os.path.join(get_task_dir(task_id), f"{sq['id']}_gaps.json")
    search_basis = sq["question"]
    is_user_guidance = False
    
    if os.path.exists(gaps_file):
        try:
            with open(gaps_file, "r", encoding="utf-8") as f:
                gaps_data = json.load(f)
                gaps = gaps_data.get("gaps", [])
                # Prioritize user guidance if present anywhere in the gaps list
                user_gap = next((g for g in gaps if g.startswith("USER GUIDANCE:")), None)
                if user_gap:
                    search_basis = user_gap[len("USER GUIDANCE:"):].strip()
                    is_user_guidance = True
                    print(f"[RESEARCH_ENGINE] Using user guidance as search basis: '{search_basis}'", flush=True)
                elif gaps:
                    search_basis = gaps[0]
                    print(f"[RESEARCH_ENGINE] Using gap as search basis: '{search_basis}'", flush=True)
        except Exception:
            pass

    # Formulate a short, atomic search-engine query from the basis text.
    # Always runs — research tasks execute during idle time, so the extra
    # ~1-3s LLM call is immaterial — and is validated/retried internally.
    # We bypass formulation if the basis was explicitly provided as user guidance,
    # starts with a URL, or contains explicit search operators (e.g. site:, filetype:).
    bypass_formulation = is_user_guidance
    if not bypass_formulation:
        lower_basis = search_basis.lower()
        operators = ["site:", "filetype:", "intitle:", "inurl:", "ext:", "cache:"]
        if any(op in lower_basis for op in operators) or lower_basis.startswith("http://") or lower_basis.startswith("https://"):
            bypass_formulation = True
            print(f"[RESEARCH_ENGINE] Detected search operators or URL in search basis. Bypassing formulation.", flush=True)

    if bypass_formulation:
        search_query = search_basis
        print(f"[RESEARCH_ENGINE] Using search basis directly (bypassing formulation): '{search_query}'", flush=True)
    else:
        task_type = state.get("task_type", "factual")
        search_query = await formulate_search_query(
            search_basis,
            task_type,
            state,
            intent_frame=state.get("intent_frame", ""),
        )

    # Execute search
    print(f"[RESEARCH_ENGINE] Searching DuckDuckGo: '{search_query}'", flush=True)
    search_results_str = evelyn_tools.web_search(search_query, max_results=5)
    parsed_sources = parse_web_search_results(search_results_str)
    
    # Obsidian Vault search (Phase 3)
    try:
        from context_manager import search_vault_map
        print(f"[RESEARCH_ENGINE] Searching Obsidian Vault: '{search_query}'", flush=True)
        vault_res = search_vault_map(search_query, limit=3)
        if vault_res and not vault_res.startswith("No results found"):
            vault_sources = parse_vault_search_results(vault_res)
            print(f"[RESEARCH_ENGINE] Found {len(vault_sources)} relevant documents in Obsidian Vault.", flush=True)
            parsed_sources.extend(vault_sources)
    except Exception as ve:
        print(f"[RESEARCH_ENGINE WARNING] Vault search failed: {ve}", flush=True)
        
    # Cross-task search (Phase 3)
    try:
        prev_research_chunks = query_previous_deep_research(search_query, task_id, limit=3)
        if prev_research_chunks:
            print(f"[RESEARCH_ENGINE] Found {len(prev_research_chunks)} matching chunks from completed deep research tasks.", flush=True)
            for chunk in prev_research_chunks:
                title = f"Previous Research: {chunk['task_query']}"
                url = f"sqlite::research_task::{chunk['task_id']}::{chunk['metadata'].get('source', '')}::chunk-{chunk['metadata'].get('chunk', 0)}"
                VIRTUAL_SOURCES[url] = chunk["content"]
                parsed_sources.append((title, url))
    except Exception as pe:
        print(f"[RESEARCH_ENGINE WARNING] Cross-task search failed: {pe}", flush=True)
    
    if not parsed_sources:
        depth_remaining = state["search_depth"] < state["max_search_depth"] - 1
        if depth_remaining:
            print(
                "[RESEARCH_ENGINE] No web search, vault, or cross-task results found. "
                "Budget remains — rewriting sub-question and retrying.",
                flush=True,
            )
            gaps_for_rewrite = sq.get("gaps", [])
            await _rewrite_subquestion(task_id, state, sq, gaps_for_rewrite)
            state["search_depth"] += 1
            save_state(task_id, state)
            return
        else:
            print(
                f"[RESEARCH_ENGINE] SQ {sq['id']} exhausted search depth with zero results. "
                "Pausing for guidance.",
                flush=True,
            )
            sq["status"] = "needs_guidance"
            state["status"] = "needs_guidance"
            state["struggling"] = True
            save_state(task_id, state)
            return
        
    task_dir = get_task_dir(task_id)
    notes_file = os.path.join(task_dir, f"{sq['id']}_notes.md")
    
    # Load current working notes
    current_notes = ""
    if os.path.exists(notes_file):
        with open(notes_file, "r", encoding="utf-8") as f:
            current_notes = f.read()
            
    extracted_any = False
    
    for title, url in parsed_sources:
        # Check source ceiling
        if state["total_sources"] >= 100:  # Just sanity check
            print("[RESEARCH_ENGINE] Total task source cap reached.", flush=True)
            break
            
        # Deduplication
        existing_src = next((s for s in state["sources_registry"] if s["url"] == url), None)
        if existing_src:
            print(f"[RESEARCH_ENGINE] Source already consulted: {url}. Skipping.", flush=True)
            continue
            
        # Scrape or read page first to verify success before counting it against constraints
        scrape_result = {"success": False, "content": None, "chunks": []}
        
        if url in VIRTUAL_SOURCES:
            # Virtual cache source (cross-task search)
            content = VIRTUAL_SOURCES[url]
            scrape_result = {
                "success": True,
                "title": title,
                "content": content,
                "chunks": [content]
            }
        elif not (url.startswith("http://") or url.startswith("https://")):
            # Local Obsidian file source!
            try:
                full_path = os.path.abspath(os.path.join(cfg.VAULT_BASE_DIR, url))
                if os.path.exists(full_path):
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    # Strip frontmatter for clean extraction context
                    content_clean = re.sub(r"^---\n.*?\n---\n?", "", content, count=1, flags=re.DOTALL)
                    scrape_result = {
                        "success": True,
                        "title": title,
                        "content": content,
                        "chunks": [content_clean[:12000]]
                    }
                else:
                    print(f"[RESEARCH_ENGINE WARNING] Local vault file not found: {full_path}", flush=True)
            except Exception as e:
                print(f"[RESEARCH_ENGINE] Failed to read local vault file {url}: {e}", flush=True)
        else:
            # Web URL
            scrape_result = await web_reader.read_and_extract_url(url)
            
        if not scrape_result["success"] or not scrape_result["content"]:
            print(f"[RESEARCH_ENGINE WARNING] Could not extract text from: {url}", flush=True)
            # Register as failed so we still deduplicate in future rounds
            src_id = f"failed_{len(state['sources_registry']) + 1:03d}"
            failed_entry = {
                "id": src_id,
                "title": title,
                "url": url,
                "timestamp": datetime.datetime.now().isoformat(),
                "failed": True
            }
            state["sources_registry"].append(failed_entry)
            save_state(task_id, state)
            continue
            
        # Scrape succeeded! Register as a valid source and increment limits
        src_id = f"src_{len(state['sources_registry']) + 1:03d}"
        source_entry = {
            "id": src_id,
            "title": title,
            "url": url,
            "timestamp": datetime.datetime.now().isoformat()
        }
        state["sources_registry"].append(source_entry)
        state["total_sources"] += 1
        sq["source_count"] += 1
        save_state(task_id, state)
        
        # If scope is deep, embed in custom Chroma collection (Phase 3)
        if state.get("scope") == "deep" and not url.startswith("sqlite::"):
            try:
                import chroma_rag
                chroma_rag.ingest_markdown_file(
                    file_path=url,
                    content=scrape_result["content"],
                    collection_name=f"research_{task_id}",
                    extra_metadata={
                        "task_id": task_id,
                        "url": url,
                        "title": title
                    }
                )
                print(f"[RESEARCH_ENGINE] Embedded source into per-task Chroma collection research_{task_id}", flush=True)
            except Exception as ce:
                print(f"[RESEARCH_ENGINE WARNING] Failed to ingest into custom Chroma collection: {ce}", flush=True)
        
        print(f"[RESEARCH_ENGINE] Extracting facts from: '{title}' [{src_id}]", flush=True)

        # Retrieve the skill template for this task's classified type (Hermes Tier 2 #8b)
        task_type = state.get("task_type", "factual")
        skill_template = research_prompts.get_skill_template(task_type)

        # We extract chunk by chunk if the page has multiple chunks
        chunks = scrape_result["chunks"]
        for idx, chunk in enumerate(chunks):
            prompt = research_prompts.build_extract_prompt(
                sq["question"],
                src_id,
                title,
                url,
                chunk,
                current_notes,
                skill_template=skill_template,
            )
            
            messages = [
                {"role": "system", "content": research_prompts.get_system_prompt(domain_level=state.get("domain_level", "specialist"))},
                {"role": "user", "content": prompt}
            ]
            
            state["ollama_calls"] += 1
            current_notes = await call_ollama(messages, num_predict=2048)
            extracted_any = True
            
            # Save notes incrementally
            with open(notes_file, "w", encoding="utf-8") as f:
                f.write(current_notes)
                
            # Respect step cooldown
            await asyncio.sleep(cfg.RESEARCH_STEP_COOLDOWN)
            
    # Move to the evaluate step
    state["current_step"] = "evaluate"
    save_state(task_id, state)


def _build_completed_sq_summaries(task_id: str, state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Assemble a lightweight summary of all resolved sub-questions for the coverage check.

    Reuses the same truncate-to-300-chars pattern already used by the
    post-synthesis triage prompt, keeping the coverage-check call cheap even
    on tasks with several completed sub-questions.

    Args:
        task_id: Unique task identifier.
        state: Task state dict.

    Returns:
        List[Dict[str, Any]]: List of dicts with 'question', 'confidence', and
        'notes_summary' keys, one per sub-question with status 'done'.
    """
    task_dir = get_task_dir(task_id)
    summaries = []
    for s in state["plan"]["sub_questions"]:
        if s.get("status") != "done":
            continue
        notes_file = os.path.join(task_dir, f"{s['id']}_notes.md")
        notes_text = ""
        if os.path.exists(notes_file):
            with open(notes_file, "r", encoding="utf-8") as f:
                notes_text = f.read()
        notes_summary = notes_text[:300] + "..." if len(notes_text) > 300 else notes_text
        summaries.append({
            "question": s["question"],
            "confidence": s.get("confidence", 0),
            "notes_summary": notes_summary or "(no notes)",
        })
    return summaries


async def step_evaluate(task_id: str, state: Dict[str, Any]) -> None:
    """Execute the EVALUATE step of the current sub-question.

    Args:
        task_id: Unique task identifier.
        state: State dictionary to modify.
    """
    sq_idx = state["current_sq_idx"]
    sq = state["plan"]["sub_questions"][sq_idx]
    
    task_dir = get_task_dir(task_id)
    notes_file = os.path.join(task_dir, f"{sq['id']}_notes.md")
    
    # If notes don't exist (e.g. every found source failed to scrape/extract),
    # treat this as a failed round rather than blindly marking done -- there
    # is no pre-planned "next SQ" to advance to under the lazy generation
    # model, so this must go through the same depth-budget-gated retry/pause
    # logic as every other failure path (zero-result search, min-source floor).
    if not os.path.exists(notes_file):
        depth_remaining = state["search_depth"] < state["max_search_depth"] - 1
        if depth_remaining:
            print(
                f"[RESEARCH_ENGINE] No notes file found for {sq['id']} (extraction "
                "failed for all sources). Budget remains -- rewriting and retrying.",
                flush=True,
            )
            await _rewrite_subquestion(task_id, state, sq, sq.get("gaps", []))
            state["search_depth"] += 1
            state["current_step"] = "search"
        else:
            print(
                f"[RESEARCH_ENGINE] SQ {sq['id']} exhausted search depth with no "
                "extractable notes. Pausing for guidance.",
                flush=True,
            )
            sq["status"] = "needs_guidance"
            sq["confidence"] = 0
            state["status"] = "needs_guidance"
            state["struggling"] = True
        save_state(task_id, state)
        return
        
    with open(notes_file, "r", encoding="utf-8") as f:
        current_notes = f.read()
        
    print(f"[RESEARCH_ENGINE] Evaluating collected evidence for SQ ({sq['id']})...", flush=True)
    
    prompt = research_prompts.build_evaluate_prompt(
        sq["question"],
        current_notes,
        state["confidence_threshold"]
    )
    
    messages = [
        {"role": "system", "content": research_prompts.get_system_prompt(domain_level=state.get("domain_level", "specialist"))},
        {"role": "user", "content": prompt}
    ]
    
    state["ollama_calls"] += 1
    raw_response = await call_ollama(messages, num_predict=512)
    
    # Parse evaluate output (must be valid JSON)
    try:
        evaluation = parse_json_response(raw_response)
        confidence = int(evaluation.get("confidence", 0))
        gaps = evaluation.get("gaps", [])
    except Exception as e:
        print(f"[RESEARCH_ENGINE WARNING] Failed to parse evaluate JSON: {e}. Output was: '{raw_response}'", flush=True)
        # Default fallback
        confidence = 0
        gaps = ["Insufficient evidence collected."]
        
    sq["confidence"] = confidence
    sq["gaps"] = gaps
    
    # Update struggling status based on the latest sub-question evaluation
    if confidence < 60:
        state["struggling"] = True
    else:
        # Check if any other finished sub-questions are struggling
        any_struggling = False
        for s in state["plan"]["sub_questions"]:
            if s.get("status") == "done" and s.get("confidence", 100) < 60:
                any_struggling = True
                break
        state["struggling"] = any_struggling
        
    # Save gaps on disk for the next search iteration
    gaps_file = os.path.join(task_dir, f"{sq['id']}_gaps.json")
    with open(gaps_file, "w", encoding="utf-8") as f:
        json.dump({"gaps": gaps}, f, indent=2)
        
    # Minimum-sources floor: guarantee at least N sources are consulted before
    # confidence is evaluated. Prevents trivial SQs from closing too early and
    # hard SQs from being abandoned before enough evidence is gathered.
    min_sources = state.get("min_sources_per_sq", 0)
    depth_remaining = state["search_depth"] < state["max_search_depth"] - 1
    if sq["source_count"] < min_sources and depth_remaining:
        print(
            f"[RESEARCH_ENGINE] SQ {sq['id']} below min source floor "
            f"({sq['source_count']}/{min_sources}). Forcing another search round.",
            flush=True,
        )
        state["current_step"] = "search"
        state["search_depth"] += 1
        save_state(task_id, state)
        return

    # Evaluate termination decisions
    is_sufficient = confidence >= state["confidence_threshold"]
    depth_exhausted = state["search_depth"] >= state["max_search_depth"] - 1
    if is_sufficient or depth_exhausted:
        if is_sufficient:
            # Sub-question complete!
            sq["status"] = "done"
            print(f"[RESEARCH_ENGINE] SQ {sq['id']} fully resolved (Threshold met).", flush=True)
            if os.path.exists(gaps_file):
                os.remove(gaps_file)

            # Reactive seam: decide whether the ORIGINAL query is now
            # adequately covered, or whether one more targeted sub-question
            # is needed. Sub-questions are generated lazily, one at a time,
            # rather than planned in a batch upfront. sub_questions_limit is
            # enforced here in code -- never exposed to the model -- so it
            # never has a quota to anchor toward.
            active_sq_count = len([
                s for s in state["plan"]["sub_questions"]
                if s.get("status") not in ("removed", "split")
            ])
            sq_limit = state.get("sub_questions_limit", 5)

            if active_sq_count >= sq_limit:
                print(
                    f"[RESEARCH_ENGINE] Sub-question ceiling reached ({active_sq_count}/{sq_limit}). "
                    "Proceeding to synthesis.",
                    flush=True,
                )
                state["current_step"] = "synthesize"
            else:
                completed_summaries = _build_completed_sq_summaries(task_id, state)
                coverage_prompt = research_prompts.build_coverage_check_prompt(
                    state["query"],
                    completed_summaries,
                    domain_level=state.get("domain_level", "specialist"),
                    intent_frame=state.get("intent_frame", ""),
                )
                coverage_messages = [
                    {"role": "system", "content": research_prompts.get_system_prompt(domain_level=state.get("domain_level", "specialist"))},
                    {"role": "user", "content": coverage_prompt},
                ]
                state["ollama_calls"] += 1
                raw_coverage = await call_ollama(coverage_messages, num_predict=300)

                try:
                    coverage_result = parse_json_response(raw_coverage)
                    sufficient = bool(coverage_result.get("sufficient", True))
                    next_question = coverage_result.get("next_question")
                except Exception as e:
                    print(
                        f"[RESEARCH_ENGINE WARNING] Failed to parse coverage check JSON: {e}. "
                        "Defaulting to synthesis.",
                        flush=True,
                    )
                    sufficient = True
                    next_question = None

                if sufficient or not next_question or not str(next_question).strip():
                    print(
                        "[RESEARCH_ENGINE] Coverage check: original query adequately "
                        "covered. Proceeding to synthesis.",
                        flush=True,
                    )
                    state["current_step"] = "synthesize"
                else:
                    next_question = str(next_question).strip()
                    new_idx = len(state["plan"]["sub_questions"])
                    new_sq = {
                        "id": f"sq_{new_idx + 1:02d}",
                        "question": next_question,
                        "status": "pending",
                        "source_count": 0,
                        "confidence": 0,
                        "search_depth": 0,
                    }
                    state["plan"]["sub_questions"].append(new_sq)
                    state["current_sq_idx"] = new_idx
                    state["current_step"] = "search"
                    state["search_depth"] = 0
                    print(
                        f"[RESEARCH_ENGINE] Coverage check: gap found. Generated next "
                        f"sub-question: '{next_question}'",
                        flush=True,
                    )
        else:
            print(f"[RESEARCH_ENGINE] SQ {sq['id']} exhausted search depth with low confidence. Pausing for guidance.", flush=True)
            sq["status"] = "needs_guidance"
            state["status"] = "needs_guidance"
            state["struggling"] = True
            
        save_state(task_id, state)
    else:
        # Loop again!
        print(f"[RESEARCH_ENGINE] SQ {sq['id']} requires further search. Running iteration {state['search_depth'] + 2}.", flush=True)
        
        # Auto-Rewrite Logic (shared helper — also used by step_search_and_extract's
        # zero-result branch)
        print(f"[RESEARCH_ENGINE] Low confidence ({confidence}%) for SQ {sq['id']}. Attempting rewrite.", flush=True)
        await _rewrite_subquestion(task_id, state, sq, gaps)

        state["current_step"] = "search"
        state["search_depth"] += 1
        
    # Calculate live progress average confidence
    total_conf = 0
    completed_sqs = 0
    for s in state["plan"]["sub_questions"]:
        if s["status"] == "done":
            total_conf += s["confidence"]
            completed_sqs += 1
    if completed_sqs > 0:
        state["confidence"] = int(total_conf / len(state["plan"]["sub_questions"]))
        
    save_state(task_id, state)


async def _summarize_sq_notes(
    sq_question: str,
    notes: str,
    sq_id: str,
    task_type: str,
    task_id: str,
    task_dir: str,
    domain_level: str = "specialist",
) -> str:
    """Compress SQ notes that exceed the token-budget threshold before synthesis.

    Reads RESEARCH_NOTES_SUMMARY_THRESHOLD from config (default 12000 chars,
    roughly 3000 tokens). Notes under the threshold are returned unchanged.
    On any error the original notes are returned so synthesis is never blocked.

    The compressed text is saved alongside the raw notes as
    `{sq_id}_notes_summary.md` for audit purposes — the raw notes file is
    never modified.

    Args:
        sq_question: The sub-question text (used in the compression prompt).
        notes: Raw notes text for this SQ.
        sq_id: SQ identifier (e.g. 'sq_01'), used for the summary filename.
        task_type: Classified task type passed to the prompt builder for
                   type-aware preservation rules.
        task_id: Parent task identifier, used for log messages only.
        task_dir: Absolute path to the task workspace directory.
        domain_level: One of 'everyday' or 'specialist'. Keeps the compression
                      call's persona consistent with the rest of the task
                      instead of silently reverting to the specialist default.

    Returns:
        str: Compressed notes if over threshold, original notes otherwise.
    """
    threshold = getattr(cfg, "RESEARCH_NOTES_SUMMARY_THRESHOLD", 12000)
    if len(notes) <= threshold:
        return notes

    original_chars = len(notes)
    print(
        f"[RESEARCH_ENGINE] SQ {sq_id} notes exceed threshold "
        f"({original_chars} chars > {threshold}). Compressing before synthesis...",
        flush=True,
    )

    prompt = research_prompts.build_notes_summary_prompt(sq_question, notes, task_type)
    messages = [
        {"role": "system", "content": research_prompts.get_system_prompt(domain_level=domain_level)},
        {"role": "user", "content": prompt},
    ]

    try:
        # num_predict capped at 2048 — a compressed SQ summary should never
        # need more than ~1500 tokens; this prevents runaway generation.
        compressed = await call_ollama(messages, num_predict=2048)
        compressed = compressed.strip()

        if not compressed:
            print(
                f"[RESEARCH_ENGINE WARNING] Notes compression for {sq_id} returned empty. "
                "Using original notes.",
                flush=True,
            )
            return notes

        compressed_chars = len(compressed)
        ratio = compressed_chars / original_chars
        print(
            f"[RESEARCH_ENGINE] Notes compressed: {original_chars} → {compressed_chars} chars "
            f"({ratio:.0%} of original) for {sq_id}.",
            flush=True,
        )

        # Persist summary alongside raw notes for audit trail
        summary_file = os.path.join(task_dir, f"{sq_id}_notes_summary.md")
        with open(summary_file, "w", encoding="utf-8") as f:
            f.write(compressed)

        return compressed

    except Exception as e:
        print(
            f"[RESEARCH_ENGINE WARNING] Notes compression failed for {sq_id}: {e}. "
            "Falling back to original notes.",
            flush=True,
        )
        return notes


async def step_synthesize(task_id: str, state: Dict[str, Any]) -> None:
    """Execute the final SYNTHESIZE step of a research task.

    Compiles final reports and propagates output to the Obsidian Vault.

    Args:
        task_id: Unique task identifier.
        state: State dictionary to modify.
    """
    print(f"[RESEARCH_ENGINE] Synthesizing final report for task {task_id}...", flush=True)
    task_dir = get_task_dir(task_id)
    task_type = state.get("task_type", "factual")

    # Load all sub-question notes, compressing any that exceed the token budget
    # threshold before handing them to the synthesizer. Raw notes files are
    # preserved on disk; only the in-memory dict carries compressed text.
    all_notes = {}
    for sq in state["plan"]["sub_questions"]:
        notes_file = os.path.join(task_dir, f"{sq['id']}_notes.md")
        if os.path.exists(notes_file):
            with open(notes_file, "r", encoding="utf-8") as f:
                raw_notes = f.read()
            compressed = await _summarize_sq_notes(
                sq_question=sq["question"],
                notes=raw_notes,
                sq_id=sq["id"],
                task_type=task_type,
                task_id=task_id,
                task_dir=task_dir,
                domain_level=state.get("domain_level", "specialist"),
            )
            state["ollama_calls"] += 1  # Count compression call if it ran
            all_notes[sq["question"]] = compressed
        else:
            all_notes[sq["question"]] = "*(No evidence collected)*"
            
    prompt = research_prompts.build_synthesize_prompt(
        state["query"],
        all_notes,
        state["sources_registry"],
        domain_level=state.get("domain_level", "specialist"),
        scope=state.get("scope", "standard"),
        intent_frame=state.get("intent_frame", ""),
    )

    messages = [
        {"role": "system", "content": research_prompts.get_system_prompt(domain_level=state.get("domain_level", "specialist"))},
        {"role": "user", "content": prompt}
    ]

    state["ollama_calls"] += 1
    # 6144 tokens gives ~4500 words — enough for dense 8-SQ deep reports.
    # Raised from 4096 as part of the deep-scope budget review (2026-06-21).
    final_report = await call_ollama(messages, num_predict=6144)
    
    # Parse actual overall confidence score, short_title, and topic_tags out of report YAML frontmatter if present
    parsed_confidence = state["confidence"]
    short_title = None
    topic_tags = []
    
    try:
        # Match either standard YAML frontmatter '---' or markdown code block ```yaml/```
        fm_match = re.match(r"^(?:---|```yaml|```)\s*\n(.*?)\n(?:---|```)(?:\s*\n|\Z)", final_report, re.DOTALL)
        if fm_match:
            frontmatter_text = fm_match.group(1)
            import yaml
            try:
                fm_data = yaml.safe_load(frontmatter_text)
                if isinstance(fm_data, dict):
                    # extract confidence
                    if "confidence" in fm_data:
                        try:
                            conf_val = str(fm_data["confidence"]).replace("%", "").strip()
                            parsed_confidence = int(conf_val)
                        except Exception:
                            pass
                    
                    # extract short_title
                    if "short_title" in fm_data:
                        short_title = str(fm_data["short_title"]).strip()
                    elif "title" in fm_data and not short_title:
                        title_val = str(fm_data["title"]).strip()
                        if len(title_val.split()) > 5:
                            short_title = " ".join(title_val.split()[:5])
                        else:
                            short_title = title_val
                            
                    # extract topic_tags
                    if "topic_tags" in fm_data:
                        raw_tags = fm_data["topic_tags"]
                        if isinstance(raw_tags, list):
                            topic_tags = [str(t).strip() for t in raw_tags if t]
                        elif isinstance(raw_tags, str):
                            topic_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
            except Exception as e:
                # Fallback to regex if PyYAML fails
                print(f"[RESEARCH_ENGINE WARNING] PyYAML failed to parse frontmatter: {e}. Falling back to regex.", flush=True)
                conf_match = re.search(r"confidence:\s*(\d+)", frontmatter_text, re.IGNORECASE)
                if conf_match:
                    parsed_confidence = int(conf_match.group(1))
                
                short_title_match = re.search(r"short_title:\s*[\"']?(.*?)[\"']?$", frontmatter_text, re.MULTILINE)
                if short_title_match:
                    short_title = short_title_match.group(1).strip()
                    
                tags_match = re.search(r"topic_tags:\s*\[(.*?)\]", frontmatter_text, re.MULTILINE)
                if tags_match:
                    topic_tags = [t.strip().strip("\"'") for t in tags_match.group(1).split(",") if t.strip()]
    except Exception as e:
        print(f"[RESEARCH_ENGINE WARNING] Failed to extract frontmatter: {e}", flush=True)

    if not short_title:
        # Fallback to query
        query_words = state["query"].split()
        if len(query_words) > 5:
            short_title = " ".join(query_words[:5]) + "..."
        else:
            short_title = state["query"]

    state["confidence"] = parsed_confidence
    state["short_title"] = short_title
    state["topic_tags"] = topic_tags
    state["current_step"] = "done"
    state["status"] = "done"
    
    # Strip LLM's own frontmatter to write a unified, structured one
    clean_report_body = final_report
    if final_report.startswith("---"):
        clean_report_body = re.sub(r"^---.*?---\s*\n", "", final_report, count=1, flags=re.DOTALL)
    elif final_report.startswith("```yaml"):
        clean_report_body = re.sub(r"^```yaml.*?```\s*\n", "", final_report, count=1, flags=re.DOTALL)
        
    tags_list = ["research/done"]
    if state["confidence"] >= 80:
        tags_list.append("research/high-quality")
    else:
        tags_list.append("research/partial")
        
    # Clean and append topic tags from state
    for tag in state.get("topic_tags", []):
        cleaned_tag = re.sub(r"[^\w\s-]", "", tag.lower())
        cleaned_tag = re.sub(r"[-\s]+", "-", cleaned_tag).strip("-_")
        if cleaned_tag and cleaned_tag not in tags_list:
            tags_list.append(cleaned_tag)
            
    tags_str = ", ".join(tags_list)
    clean_short_title = state["short_title"].replace('"', '\\"')
    clean_query = state['query'].replace('"', '\\"')
    
    frontmatter = (
        "---\n"
        f"title: \"{clean_short_title}\"\n"
        f"research_query: \"{clean_query}\"\n"
        f"date created: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"research_task_id: {task_id}\n"
        f"scope: {state['scope']}\n"
        f"source_count: {state['total_sources']}\n"
        f"confidence: {state['confidence']}%\n"
        f"triggered_by: {state['triggered_by']}\n"
        f"tags: [{tags_str}]\n"
        "---\n\n"
    )
    
    full_report_content = frontmatter + clean_report_body
    
    # Save report locally
    report_file = os.path.join(task_dir, "report.md")
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(full_report_content)
    
    # --- Post-Synthesis Triage Logic ---
    low_conf_sqs = []
    if state.get("termination_reason") in ("timeout", "turn_cap"):
        print(
            f"[RESEARCH_ENGINE] Safety cap was triggered ('{state['termination_reason']}'). "
            "Skipping post-synthesis triage to prevent infinite loop.",
            flush=True,
        )
    else:
        state["synthesis_iterations"] = state.get("synthesis_iterations", 0) + 1
        max_synthesis_iters = getattr(cfg, "MAX_SYNTHESIS_ITERATIONS", 3)
        
        # Identify low-confidence sub-questions that haven't been removed/split
        for sq in state["plan"]["sub_questions"]:
            if sq.get("confidence", 100) < state["confidence_threshold"] and sq.get("status") not in ("removed", "split"):
                # Provide a short notes summary
                notes_text = all_notes.get(sq["question"], "")
                notes_summary = notes_text[:300] + "..." if len(notes_text) > 300 else notes_text
                sq_copy = dict(sq)
                sq_copy["notes_summary"] = notes_summary
                low_conf_sqs.append(sq_copy)
            
    if low_conf_sqs and state["synthesis_iterations"] <= max_synthesis_iters:
        print(f"[RESEARCH_ENGINE] Post-synthesis triage: found {len(low_conf_sqs)} low-confidence SQs. Triage iteration {state['synthesis_iterations']}/{max_synthesis_iters}.", flush=True)
        
        # Extract limitations/gaps section from report
        gap_analysis_text = "No gap analysis found."
        match = re.search(r"(?i)(###\s*(?:Limitations|Gaps|Remaining Questions|Areas of Uncertainty).*?)(?=^#|\Z)", final_report, re.MULTILINE | re.DOTALL)
        if match:
            gap_analysis_text = match.group(1).strip()
            
        triage_prompt = research_prompts.build_post_synthesis_triage_prompt(gap_analysis_text, low_conf_sqs)
        triage_messages = [
            {"role": "system", "content": research_prompts.get_system_prompt(domain_level=state.get("domain_level", "specialist"))},
            {"role": "user", "content": triage_prompt}
        ]
        
        state["ollama_calls"] += 1
        raw_triage_response = await call_ollama(triage_messages, num_predict=1024)
        
        try:
            triage_decisions = parse_json_response(raw_triage_response)
            if not isinstance(triage_decisions, list):
                raise ValueError("Triage response is not a list")
                
            new_children_added = False
            for decision in triage_decisions:
                sq_id = decision.get("sq_id")
                action = decision.get("action", "").lower()
                reason = decision.get("reason", "")
                
                target_sq = next((s for s in state["plan"]["sub_questions"] if s["id"] == sq_id), None)
                if not target_sq:
                    continue
                    
                if action == "remove":
                    print(f"[RESEARCH_ENGINE] Triage REMOVE: {sq_id}. Reason: {reason}", flush=True)
                    target_sq["status"] = "removed"
                    target_sq["removed_reason"] = reason
                elif action == "split":
                    children = decision.get("children", [])
                    print(f"[RESEARCH_ENGINE] Triage SPLIT: {sq_id} into {len(children)} children. Reason: {reason}", flush=True)
                    target_sq["status"] = "split"
                    target_sq["split_reason"] = reason
                    
                    for i, child_q in enumerate(children):
                        new_sq = {
                            "id": f"{sq_id}_c{i+1}",
                            "parent_sq_id": sq_id,
                            "question": child_q,
                            "status": "pending",
                            "source_count": 0,
                            "confidence": 0,
                            "search_depth": 0
                        }
                        state["plan"]["sub_questions"].append(new_sq)
                        new_children_added = True
                        
            if new_children_added:
                for idx, s in enumerate(state["plan"]["sub_questions"]):
                    if s["status"] == "pending":
                        state["current_sq_idx"] = idx
                        break
                state["current_step"] = "search"
                state["status"] = "searching"
                print("[RESEARCH_ENGINE] Triage added new child questions. Resuming search loop.", flush=True)
                save_state(task_id, state)
                return  # Exit step_synthesize, task continues!
                
        except Exception as e:
            print(f"[RESEARCH_ENGINE WARNING] Failed to parse triage JSON: {e}. Output was: '{raw_triage_response}'", flush=True)
            print("[RESEARCH_ENGINE] Falling through to quarantine/done logic.", flush=True)

    elif low_conf_sqs:
        print(f"[RESEARCH_ENGINE] Max synthesis iterations reached ({state['synthesis_iterations']}). Falling through.", flush=True)
    # --- End Triage Logic ---
    
    # Copy file to Obsidian Vault (quarantine if confidence < 60%)
    if state["confidence"] >= 60:
        try:
            # Create safe Obsidian title slug
            slug = re.sub(r"[^\w\s-]", "", state["short_title"].lower())
            slug = re.sub(r"[-\s]+", "-", slug).strip("-_")
            vault_filename = f"{slug}.md"
            
            vault_dir = getattr(cfg, "RESEARCH_VAULT_DIR", r"G:\My Drive\Obsidian_Vault\Evelyn\Research")
            os.makedirs(vault_dir, exist_ok=True)
            vault_file_path = os.path.join(vault_dir, vault_filename)
            
            with open(vault_file_path, "w", encoding="utf-8") as f:
                f.write(full_report_content)
                
            state["vault_path"] = vault_file_path
            state["quarantined"] = False
            print(f"[RESEARCH_ENGINE] Saved report to Obsidian Vault: {vault_file_path}", flush=True)
        except Exception as e:
            print(f"[RESEARCH_ENGINE ERROR] Failed to copy report to Vault: {e}", flush=True)
    else:
        state["quarantined"] = True
        state["vault_path"] = None
        print(f"[RESEARCH_ENGINE] Research completed with low confidence ({state['confidence']}%). Quarantined task, did not copy to Obsidian Vault.", flush=True)
        
    save_state(task_id, state)
    print(f"[RESEARCH_ENGINE] Task {task_id} completed successfully!", flush=True)


async def execute_task_step(task_id: str) -> bool:
    """Execute a single step of the research state machine.

    This design supports crash-safety and incremental run polling.

    Args:
        task_id: The unique task identifier.

    Returns:
        bool: True if the task has completed or hit an error, False if more steps remain.
    """
    state = load_state(task_id)
    if not state:
        print(f"[RESEARCH_ENGINE ERROR] Task {task_id} state file not found.", flush=True)
        return True
        
    if state["status"] in ("done", "error", "cancelled", "needs_guidance"):
        return True
        
    if state["status"] == "paused":
        print(f"[RESEARCH_ENGINE] Task {task_id} paused by server. Exiting background runner.", flush=True)
        return True
        
    # Increment high-level orchestrator turns (steps)
    if "orchestrator_turns" not in state:
        state["orchestrator_turns"] = 0
    state["orchestrator_turns"] += 1
    
    # Verify safety net limits (Emergency Brakes on State Loop).
    # Read from state first (set at task creation from scope preset) so each
    # task uses its own budget. Fall back to config for tasks created before
    # this change was deployed.
    turn_limit = state.get(
        "max_orchestrator_turns",
        getattr(cfg, "RESEARCH_MAX_ORCHESTRATOR_TURNS", 50)
    )
    if state["orchestrator_turns"] >= turn_limit:
        print(
            f"[RESEARCH_ENGINE WARNING] Safety cap reached ({turn_limit} orchestrator turns "
            f"for scope='{state.get('scope', 'unknown')}'). Forcing Synthesis.",
            flush=True,
        )
        state["termination_reason"] = "turn_cap"
        state["current_step"] = "synthesize"
        save_state(task_id, state)

    # Check wall-clock timeout safety limit using accumulated active run time.
    timeout_limit = state.get(
        "wall_clock_timeout",
        getattr(cfg, "RESEARCH_WALL_CLOCK_TIMEOUT", 7200)
    )
    accumulated_runtime = state.get("accumulated_runtime", 0.0)
    if accumulated_runtime >= timeout_limit:
        print(
            f"[RESEARCH_ENGINE WARNING] Task hit wall-clock timeout "
            f"({timeout_limit}s / {timeout_limit // 3600}h active runtime for scope='{state.get('scope', 'unknown')}'). "
            f"Forcing Synthesis.",
            flush=True,
        )
        state["termination_reason"] = "timeout"
        state["current_step"] = "synthesize"
        save_state(task_id, state)

    # Circadian window check — enforce between steps so overnight tasks pause
    # cleanly instead of running until their wall-clock timeout expires.
    # Synthesis is always allowed to complete regardless of the window: a task
    # that already collected all its evidence should not be left half-finished.
    step = state["current_step"]
    if step != "synthesize" and not _in_research_window():
        start_h = getattr(cfg, "RESEARCH_ACTIVE_HOURS_START", 6)
        end_h   = getattr(cfg, "RESEARCH_ACTIVE_HOURS_END",   21)
        print(
            f"[RESEARCH_ENGINE] Task {task_id} outside active hours "
            f"({start_h:02d}:00–{end_h:02d}:00). Pausing until window reopens.",
            flush=True,
        )
        state["status"] = "paused"
        save_state(task_id, state, ignore_disk_status=True)
        return True
    start_time = time.time()
    try:
        if step == "plan":
            plan_resolved = await step_plan(task_id, state)
            if plan_resolved:
                # Task was resolved directly by the necessity pre-filter and
                # its directory was already deleted -- stop immediately.
                # Do NOT call load_state() again below; state.json no longer
                # exists, and re-checking it here would misleadingly log a
                # "state file not found" error for what is actually a
                # successful, deliberate cleanup.
                return True
        elif step == "search":
            await step_search_and_extract(task_id, state)
        elif step == "evaluate":
            await step_evaluate(task_id, state)
        elif step == "synthesize":
            await step_synthesize(task_id, state)
        elif step == "done":
            return True
        else:
            print(f"[RESEARCH_ENGINE ERROR] Unknown task step '{step}'.", flush=True)
            state["status"] = "error"
            state["error"] = f"Unknown task step '{step}'"
            save_state(task_id, state)
            return True
            
        # Check if completed
        updated_state = load_state(task_id)
        if updated_state and updated_state["status"] == "done":
            return True
            
        return False
        
    except Exception as e:
        traceback.print_exc()
        state["status"] = "error"
        
        # Enrich exception output with human-actionable debug information
        err_type = type(e).__name__
        err_msg = str(e)
        
        if "ConnectError" in err_msg or "ConnectError" in err_type:
            enriched = f"Ollama Connection Error: Could not reach Ollama server (URL: {getattr(cfg, 'OLLAMA_URL', 'default')}). Ensure the Ollama service is running."
        elif "TimeoutException" in err_msg or "Timeout" in err_type:
            enriched = "Ollama Timeout: The model took too long to respond. This is typically due to GPU VRAM saturation or high resource contention."
        elif "HTTPStatusError" in err_msg or "HTTPError" in err_type:
            enriched = f"Ollama API Error: Server returned an unsuccessful status code. details: {err_msg}"
        elif "UnicodeDecodeError" in err_type or "UnicodeEncodeError" in err_type:
            enriched = f"Character Encoding Failure: Encountered an illegal characters format during text extraction or output serialization. details: {err_msg}"
        elif "FileNotFoundError" in err_type:
            enriched = f"Filesystem Error: Required asset, directory, or state cache is missing. details: {err_msg}"
        else:
            enriched = f"Execution Error [{err_type}]: {err_msg}"
            
        state["error"] = enriched
        save_state(task_id, state)
        return True
    finally:
        # Accumulate run time for this turn
        step_duration = time.time() - start_time
        current_state = load_state(task_id)
        if current_state:
            current_state["accumulated_runtime"] = current_state.get("accumulated_runtime", 0.0) + step_duration
            save_state(task_id, current_state)


async def run_full_research(task_id: str) -> None:
    """Run a complete research task from start to finish asynchronously.

    Useful for CLI runner and background thread execution.

    Args:
        task_id: The unique task identifier.
    """
    print(f"[RESEARCH_ENGINE] Starting research loop for task: {task_id}", flush=True)
    is_done = False
    
    while not is_done:
        is_done = await execute_task_step(task_id)
        if not is_done:
            await asyncio.sleep(1.0) # Yield control
            
    print(f"[RESEARCH_ENGINE] Finished processing task: {task_id}", flush=True)



def get_recent_chat_history(limit: int = 20) -> List[Dict[str, str]]:
    """Fetch the most recent messages from the SQLite database.

    Args:
        limit: The maximum number of messages to fetch. Default is 20.

    Returns:
        List[Dict[str, str]]: A list of message dictionaries.
    """
    import sqlite3
    try:
        con = sqlite3.connect(cfg.CHAT_DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT role, content FROM messages WHERE content != '' ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        con.close()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    except Exception as e:
        print(f"[RESEARCH_ENGINE ERROR] Failed to fetch chat history for self-initiate: {e}", flush=True)
        return []


async def self_initiate_research_topics() -> None:
    """Analyze recent conversations to self-initiate research topics and queue them.

    Returns:
        None
    """
    import yaml
    importlib.reload(cfg)
    if not cfg.RESEARCH_SELF_INITIATE:
        return
        
    queue_file = os.path.join(cfg.RESEARCH_DATA_DIR, "queue.json")
    
    # Load current queue
    queue = []
    if os.path.exists(queue_file):
        try:
            with open(queue_file, "r", encoding="utf-8") as f:
                queue = json.load(f)
        except Exception:
            queue = []
            
    if len(queue) >= cfg.RESEARCH_MAX_QUEUE_SIZE:
        return
        
    history = get_recent_chat_history(20)
    if not history:
        return
        
    # Construct history dump
    history_text = ""
    for msg in history:
        history_text += f"{msg['role'].upper()}: {msg['content']}\n"
        
    prompt = f"""You are Evelyn, an advanced AI research companion. Review the following recent conversation history between you and Ricky:

{history_text}

Identify 1 to 3 interesting, factual, or technical topics or open questions mentioned or implied in this chat that would be highly beneficial to research in-depth (e.g. detailed benchmarks, technology explanations, historical events, scientific developments, or project concepts).
Do NOT include extremely broad topics, personal plans, or vague ideas. Do NOT include anything that was already directly and fully answered earlier in this same conversation -- if Ricky asked a question and got a complete answer, that topic is resolved and does not need a research task. Do NOT include simple, casual, or everyday questions that a short conversational answer already covers well (e.g. basic food storage/safety facts, common definitions, simple how-tos) -- these do not warrant a multi-source cited report. Focus on concrete, searchable questions that genuinely benefit from deeper investigation. Keep each query to one topic.

For each topic, also write a 2-3 sentence intent_frame: why this topic matters right now given the conversation context, and what kind of practical answer would help (not academic depth, but the actual use case and what will be done with the information).

Output ONLY a YAML block in this exact format:

```yaml
topics:
  - query: "research question 1"
    scope: "standard"
    intent_frame: "2-3 sentence intent frame for question 1"
  - query: "research question 2"
    scope: "deep"
    intent_frame: "2-3 sentence intent frame for question 2"
```
If no topics are worth researching, output an empty list. Output nothing else but the YAML block."""

    messages = [
        {"role": "system", "content": "You are Evelyn's analytical sub-agent. Output only YAML."},
        {"role": "user", "content": prompt}
    ]
    
    try:
        raw = await call_ollama(messages, num_predict=1000)
        match = re.search(r"```(?:yaml)?\s*\n(.*?)```", raw, re.DOTALL | re.IGNORECASE)
        block = match.group(1) if match else raw
        data = yaml.safe_load(block)
        
        if isinstance(data, dict) and "topics" in data:
            new_topics = data["topics"] or []
            added = 0
            for t in new_topics:
                query = t.get("query", "").strip()
                scope = t.get("scope", "standard").strip()
                intent_frame = t.get("intent_frame", "").strip() or None
                if query and len(queue) < cfg.RESEARCH_MAX_QUEUE_SIZE:
                    # Check if already researched or queued
                    already_exists = any(q["query"].lower() == query.lower() for q in queue)
                    if not already_exists:
                        queue.append({
                            "query": query,
                            "scope": scope,
                            "priority": 1,
                            "source": "evelyn",
                            "intent_frame": intent_frame,
                            "created_at": datetime.datetime.now().isoformat()
                        })
                        added += 1
            if added > 0:
                os.makedirs(cfg.RESEARCH_DATA_DIR, exist_ok=True)
                with open(queue_file, "w", encoding="utf-8") as f:
                    json.dump(queue, f, indent=2)
                print(f"[RESEARCH_ENGINE] Successfully queued {added} self-initiated research topics.", flush=True)
    except Exception as e:
        print(f"[RESEARCH_ENGINE ERROR] Failed self-initiated topic generation: {e}", flush=True)


if __name__ == "__main__":
    # CLI Testing capability (Phase 1 Checklist)
    import argparse
    
    parser = argparse.ArgumentParser(description="Evelyn Deep Research CLI Runner")
    parser.add_argument("query", type=str, help="Research topic or search query")
    parser.add_argument("--scope", type=str, default="standard", choices=["quick", "standard", "deep"], help="Research depth scope")
    
    args = parser.parse_args()
    
    async def main():
        """Run the research engine from the command line.

        Resolves the query argument to either resume an existing task or create a new
        task before starting the asynchronous execution loop.
        """
        if args.query.startswith("task_") and os.path.exists(os.path.join(cfg.RESEARCH_DATA_DIR, args.query)):
            task_id = args.query
            print(f"[RESEARCH_ENGINE] Resuming existing task: {task_id}", flush=True)
            state = load_state(task_id)
            if state and state["status"] == "error":
                state["status"] = "searching" if state["current_step"] in ("search", "evaluate") else "pending"
                state["error"] = None
                save_state(task_id, state, ignore_disk_status=True)
        else:
            task_id = create_research_task(args.query, scope=args.scope, triggered_by="user")
        await run_full_research(task_id)
        
    asyncio.run(main())
