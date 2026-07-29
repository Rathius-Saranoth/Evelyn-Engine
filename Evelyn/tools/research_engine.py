# research_engine.py
# date created: 2026-05-26
# date modified: 2026-07-16 19:36:00
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

# Local ISO timestamped print wrapper for subprocess log output
_original_print = print

def _timestamped_print(*args, **kwargs):
    """Print with a local ISO timestamp prefix [YYYY-MM-DD HH:MM:SS]."""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if args and isinstance(args[0], str):
        if not (args[0].startswith("[20") and len(args[0]) > 20 and args[0][20] == "]"):
            args = (f"[{ts}] {args[0]}",) + args[1:]
    elif not args:
        args = (f"[{ts}]",)
    else:
        args = (f"[{ts}]",) + args
    _original_print(*args, **kwargs)

print = _timestamped_print


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
    """Recalculate the true source count from the contributing_sources.json registry.

    Reads the per-task contributing_sources.json file, which records only sources
    that actually contributed new facts to the evidence summary (as determined by
    the incremental digest step). This is the authoritative source for source
    counting — it is never overwritten by the engine loop, so quality-gate
    decisions persist across state reloads.

    Falls back gracefully for old tasks that predate this file: leaves
    sq["source_count"] at whatever state.json already records and returns 0
    as the task-level total. Old tasks resume without breakage.

    Args:
        task_id: Unique task identifier.
        state: Task state dict — mutated to update sq["source_count"] per SQ.

    Returns:
        int: Total number of contributing sources across all active SQs.
    """
    contrib_file = os.path.join(get_task_dir(task_id), "contributing_sources.json")
    if not os.path.exists(contrib_file):
        return 0  # Old task — leave counts intact, do not stomp
    try:
        with open(contrib_file, "r", encoding="utf-8") as f:
            contrib = json.load(f)
    except Exception:
        return 0
    all_sources: set = set()
    for sq in state.get("plan", {}).get("sub_questions", []):
        if sq.get("status") not in ("removed", "split"):
            sq_sources = set(contrib.get(sq["id"], []))
            sq["source_count"] = len(sq_sources)
            all_sources |= sq_sources
    return len(all_sources)


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
        "original_question": query,
        "scope": scope,
        "status": initial_status,
        "created_at": datetime.datetime.now().isoformat(),
        "updated_at": datetime.datetime.now().isoformat(),
        "triggered_by": triggered_by,
        "notified": False,
        "vault_path": None,
        "confidence": 0,
        "intent_frame": intent_frame,  # None triggers LLM generation in step_plan()
        "topic_aliases": [],
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




async def call_ollama(
    prompt_messages: List[Dict[str, str]], 
    num_predict: int = 2048,
    think: bool = True
) -> str:
    """Helper to communicate with Ollama synchronously or asynchronously.

    Bypasses deep conversational states/history to maximize text context.
    Supports dynamic thinking/reasoning flags per research phase.

    Args:
        prompt_messages: Format-compliant list of prompt message dicts.
        num_predict: Maximum prediction tokens.
        think: Whether to enable Ollama native thinking/reasoning.

    Returns:
        str: Raw response text from the model (with <think> tags stripped).
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
        "think": think
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
        raw = await call_ollama(messages, num_predict=1024, think=True)
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


def _truncate_query_fallback(question_text: str, max_words: int = 5) -> str:
    """Deterministic fallback that strips academic prefixes and extracts raw keywords."""
    text = re.sub(r"(?i)^(an?|the)\s+(analysis|overview|study|investigation|evaluation)\s+(of|on)\s+", "", question_text)
    text = re.sub(r"(?i)^(comparative\s+)?(analysis|comparison)\s+between\s+", "", text)
    text = re.sub(r"[?\"'!,]", "", text)

    ignore_words = {
        "underlying", "mechanisms", "linking", "impact", "effects", "role",
        "towards", "using", "via", "what", "how", "why", "does", "is", "are"
    }
    words = [w for w in text.split() if w.lower() not in ignore_words]
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

    rewrite_prompt = research_prompts.build_rewrite_prompt(
        sq["question"],
        current_notes,
        gaps,
        topic_aliases=state.get("topic_aliases", []),
    )
    rewrite_messages = [
        {"role": "system", "content": research_prompts.get_system_prompt(domain_level=state.get("domain_level", "specialist"))},
        {"role": "user", "content": rewrite_prompt},
    ]
    state["ollama_calls"] += 1
    print(f"[RESEARCH_ENGINE] Auto-rewriting SQ {sq['id']}...", flush=True)
    rewritten_q = await call_ollama(rewrite_messages, num_predict=1024)
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

    # Regenerate search_query from the rewritten question
    try:
        task_type = state.get("task_type", "factual")
        new_search_query = await formulate_search_query(
            rewritten_q,
            task_type,
            state,
            intent_frame=state.get("intent_frame", ""),
        )
        sq["search_query"] = new_search_query
        print(f"[RESEARCH_ENGINE] Regenerated search_query after rewrite: '{new_search_query}'", flush=True)
    except Exception as rse:
        print(f"[RESEARCH_ENGINE WARNING] Failed to regenerate search_query after rewrite: {rse}", flush=True)

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


async def step_assess_prior_knowledge(task_id: str, state: Dict[str, Any]) -> bool:
    """Assess what Evelyn already knows before launching web research.

    Replaces the old try_resolve_directly() / "no trace" pattern. Instead of
    silently deleting the task directory on a resolved path, this function
    populates two transparent, persisted fields on the task state:

    - state["internal_knowledge"]: Can the LLM answer this from training data?
    - state["saved_knowledge"]:    Can this be answered from chat history,
      memory facts, vault documents, or prior completed research tasks?

    Decision logic:
      - saved_knowledge.confidence  >= threshold → status = "resolved"
      - internal_knowledge.confidence >= threshold AND NOT time_sensitive → status = "resolved"
      - Otherwise → proceed to full research; both summaries are available
        as prior context for extract/evaluate prompts.

    The task directory is NEVER deleted. Resolved tasks remain on disk with
    status "resolved" for transparency.

    Args:
        task_id: Unique task identifier.
        state: Task state dict — mutated with knowledge gate fields and saved.

    Returns:
        bool: True if the task was resolved from existing knowledge (caller
              should stop the pipeline). False if research should proceed.
    """
    importlib.reload(cfg)
    if not getattr(cfg, "RESEARCH_NECESSITY_PREFILTER_ENABLED", True):
        return False

    query = state["query"]

    # --- Internal knowledge check ---
    internal_prompt = research_prompts.build_prior_knowledge_prompt(
        query, variant="internal", evidence_text=""
    )
    messages = [
        {"role": "system", "content": research_prompts.get_system_prompt()},
        {"role": "user", "content": internal_prompt},
    ]
    state["ollama_calls"] += 1
    try:
        raw_internal = await call_ollama(messages, num_predict=512)
        internal_result = parse_json_response(raw_internal)
    except Exception as e:
        print(f"[RESEARCH_ENGINE WARNING] Internal knowledge check failed: {e}. Proceeding with research.", flush=True)
        internal_result = {"answerable": False, "confidence": 0, "summary": ""}
    state["internal_knowledge"] = {
        "answerable": bool(internal_result.get("answerable", False)),
        "confidence": int(internal_result.get("confidence", 0)),
        "summary": str(internal_result.get("summary", "")),
    }
    print(
        f"[RESEARCH_ENGINE] Internal knowledge check: "
        f"answerable={state['internal_knowledge']['answerable']}, "
        f"confidence={state['internal_knowledge']['confidence']}%",
        flush=True,
    )

    # --- Saved knowledge check (cheap: chat + memory always; vault/prior tasks if inconclusive) ---
    evidence_parts: list = []

    history = get_recent_chat_history(20)
    if history:
        history_text = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in history)
        evidence_parts.append(f"### Recent Conversation History:\n{history_text}")

    try:
        import memory_db
        all_entries = memory_db.get_all_entries(statuses=["live"])
        matched_entries = []
        for entry in all_entries:
            overlap = evelyn_tools.get_jaccard_similarity(query, entry.get("observation", ""))
            if overlap >= 0.2:
                matched_entries.append((overlap, entry))
        matched_entries.sort(key=lambda x: x[0], reverse=True)
        matched_entries = matched_entries[:5]
        if matched_entries:
            memory_text = "\n".join(
                f"- [{e['category']}] {e['observation']}" for _, e in matched_entries
            )
            evidence_parts.append(f"### Recorded Memory Facts:\n{memory_text}")
    except Exception as e:
        print(f"[RESEARCH_ENGINE WARNING] Memory query failed for knowledge gate: {e}", flush=True)
        matched_entries = []

    # Gate expensive lookups (vault + prior research) behind an inconclusive cheap pass
    cheap_confident = state["internal_knowledge"]["confidence"] >= 70
    if not cheap_confident:
        try:
            from context_manager import search_vault_map
            vault_res = search_vault_map(query, limit=3)
            if vault_res and not vault_res.startswith("No results found"):
                evidence_parts.append(f"### Obsidian Vault Excerpts:\n{vault_res}")
        except Exception:
            pass
        try:
            prev_chunks = query_previous_deep_research(query, task_id, limit=3)
            if prev_chunks:
                prior_text = "\n".join(
                    f"[{c['task_id']}] {c['content'][:400]}" for c in prev_chunks
                )
                evidence_parts.append(f"### Prior Research Summaries:\n{prior_text}")
        except Exception:
            pass

    evidence_text = "\n\n".join(evidence_parts)
    saved_prompt = research_prompts.build_prior_knowledge_prompt(
        query, variant="saved", evidence_text=evidence_text
    )
    messages = [
        {"role": "system", "content": research_prompts.get_system_prompt()},
        {"role": "user", "content": saved_prompt},
    ]
    state["ollama_calls"] += 1
    try:
        raw_saved = await call_ollama(messages, num_predict=512)
        saved_result = parse_json_response(raw_saved)
    except Exception as e:
        print(f"[RESEARCH_ENGINE WARNING] Saved knowledge check failed: {e}. Proceeding with research.", flush=True)
        saved_result = {"answerable": False, "confidence": 0, "summary": "", "sources": []}
    state["saved_knowledge"] = {
        "answerable": bool(saved_result.get("answerable", False)),
        "confidence": int(saved_result.get("confidence", 0)),
        "summary": str(saved_result.get("summary", "")),
        "sources": list(saved_result.get("sources", [])),
    }
    print(
        f"[RESEARCH_ENGINE] Saved knowledge check: "
        f"answerable={state['saved_knowledge']['answerable']}, "
        f"confidence={state['saved_knowledge']['confidence']}%",
        flush=True,
    )

    # --- Decision ---
    threshold = getattr(cfg, "RESEARCH_NECESSITY_CONFIDENCE_THRESHOLD", 90)
    is_time_sensitive = research_prompts.is_time_sensitive_query(query)

    resolved = False
    if state["saved_knowledge"]["confidence"] >= threshold:
        resolved = True
        print(
            f"[RESEARCH_ENGINE] Query '{query}' resolved from saved knowledge "
            f"(confidence={state['saved_knowledge']['confidence']}%).",
            flush=True,
        )
    elif (
        not is_time_sensitive
        and state["internal_knowledge"]["confidence"] >= threshold
    ):
        resolved = True
        print(
            f"[RESEARCH_ENGINE] Query '{query}' resolved from internal knowledge "
            f"(confidence={state['internal_knowledge']['confidence']}%).",
            flush=True,
        )

    if resolved:
        # Touch memory entries so they register as retrieved
        for _, entry in matched_entries:
            try:
                memory_db.touch_entry_retrieved(entry["id"])
            except Exception:
                pass
        state["status"] = "resolved"
        state["current_step"] = "done"
        save_state(task_id, state)
        return True

    return False


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
        frame = await call_ollama(messages, num_predict=2048, think=True)
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
        resolved = await step_assess_prior_knowledge(task_id, state)
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
    raw_response = await call_ollama(messages, num_predict=1024, think=True)
    seed_question = raw_response.strip().strip('"\'').split("\n")[0].strip()

    if not seed_question:
        print("[RESEARCH_ENGINE WARNING] Seed sub-question generation returned empty. Defaulting to main query.", flush=True)
        seed_question = state["query"]

    # Formulate the first search_query from the seed question
    task_type = state.get("task_type", "factual")
    seed_search_query = await formulate_search_query(
        seed_question,
        task_type,
        state,
        intent_frame=state.get("intent_frame", ""),
    )

    state["plan"]["sub_questions"] = [{
        "id": "sq_01",
        "question": seed_question,
        "search_query": seed_search_query,
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
    print(f"[RESEARCH_ENGINE] Seed sub-question: '{seed_question}' | Initial search query: '{seed_search_query}'", flush=True)



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

    # Use stored search_query if available; fall back to sq.question for old tasks
    search_query = sq.get("search_query") or ""

    if not search_query:
        # First round or old task — derive it now
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
        sq["search_query"] = search_query
    else:
        print(f"[RESEARCH_ENGINE] Using stored search query: '{search_query}'", flush=True)

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
    summary_file = os.path.join(task_dir, f"{sq['id']}_summary.md")
    contrib_file = os.path.join(task_dir, "contributing_sources.json")

    # Load current contributing sources registry
    try:
        if os.path.exists(contrib_file):
            with open(contrib_file, "r", encoding="utf-8") as f:
                contrib_registry = json.load(f)
        else:
            contrib_registry = {}
    except Exception:
        contrib_registry = {}

    # Load current evidence summary
    current_summary = ""
    if os.path.exists(summary_file):
        with open(summary_file, "r", encoding="utf-8") as f:
            current_summary = f.read()
            
    extracted_any = False
    
    max_sources_per_sq = state.get("max_sources_per_sq", 15)
    for title, url in parsed_sources:
        # Check per-SQ source ceiling and overall task source ceiling
        if sq.get("source_count", 0) >= max_sources_per_sq:
            print(f"[RESEARCH_ENGINE] SQ {sq['id']} per-SQ source limit ({max_sources_per_sq}) reached. Stopping extraction for this sub-question.", flush=True)
            break
            
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
            
        # Scrape succeeded — register in sources_registry (dedup/audit) but do NOT
        # increment source_count yet. That only happens if the digest confirms contribution.
        src_id = f"src_{len(state['sources_registry']) + 1:03d}"
        source_entry = {
            "id": src_id,
            "title": title,
            "url": url,
            "timestamp": datetime.datetime.now().isoformat()
        }
        state["sources_registry"].append(source_entry)
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
        all_extracted_notes = ""
        for idx, chunk in enumerate(chunks):
            prompt = research_prompts.build_extract_prompt(
                sq["question"],
                src_id,
                title,
                url,
                chunk,
                skill_template=skill_template,
            )
            
            messages = [
                {"role": "system", "content": research_prompts.get_system_prompt(domain_level=state.get("domain_level", "specialist"))},
                {"role": "user", "content": prompt}
            ]
            
            state["ollama_calls"] += 1
            extracted_chunk_notes = await call_ollama(messages, num_predict=2048, think=True)
            extracted_chunk_notes = extracted_chunk_notes.strip()
            
            if extracted_chunk_notes:
                all_extracted_notes += extracted_chunk_notes + "\n\n"
                section_header = f"### Source [{src_id}]: {title}\n"
                formatted_entry = f"{section_header}{extracted_chunk_notes}\n\n"
                
                # Append raw notes to disk — permanent audit log, never truncated
                with open(notes_file, "a", encoding="utf-8") as f:
                    f.write(formatted_entry)

                # Parse discovered technical aliases / synonyms for Phase 11 Alias Expansion
                if "Discovered Aliases" in extracted_chunk_notes:
                    alias_part = extracted_chunk_notes.split("Discovered Aliases", 1)[1]
                    matches = re.findall(r"[-*]\s*['\"]?(.*?)['\"]?$", alias_part, re.MULTILINE)
                    current_aliases = set(state.get("topic_aliases", []))
                    new_found = False
                    for m in matches:
                        clean_a = m.strip().strip("'\"`")
                        if clean_a and len(clean_a) > 1 and clean_a not in current_aliases:
                            current_aliases.add(clean_a)
                            new_found = True
                            print(f"[RESEARCH_ENGINE] Discovered topic alias: '{clean_a}'", flush=True)
                    if new_found:
                        state["topic_aliases"] = list(current_aliases)
                        save_state(task_id, state)
            
            # Respect step cooldown
            await asyncio.sleep(cfg.RESEARCH_STEP_COOLDOWN)

        # --- Incremental evidence digest ---
        # Merge the extracted notes into the bounded evidence summary.
        # The digest prompt returns an explicit 'contributed' boolean so we
        # never need to diff strings to decide whether to count the source.
        if all_extracted_notes.strip():
            extracted_any = True
            digest_prompt = research_prompts.build_evidence_digest_prompt(
                sub_question=sq["question"],
                current_summary=current_summary,
                new_extraction=all_extracted_notes.strip(),
                source_id=src_id,
                source_title=title,
            )
            digest_messages = [
                {"role": "system", "content": research_prompts.get_system_prompt(domain_level=state.get("domain_level", "specialist"))},
                {"role": "user", "content": digest_prompt},
            ]
            state["ollama_calls"] += 1
            try:
                raw_digest = await call_ollama(digest_messages, num_predict=4096)
                digest_result = parse_json_response(raw_digest)
                updated_summary = str(digest_result.get("summary", current_summary))
                contributed = bool(digest_result.get("contributed", True))  # default True on parse error
            except Exception as de:
                print(f"[RESEARCH_ENGINE WARNING] Evidence digest parse failed: {de}. Treating source as contributing.", flush=True)
                updated_summary = current_summary  # Don't corrupt existing summary
                contributed = True

            if contributed:
                # Source added new facts — count it against budget and persist
                current_summary = updated_summary
                sq["evidence_summary"] = current_summary
                with open(summary_file, "w", encoding="utf-8") as f:
                    f.write(current_summary)

                # Register in contributing_sources.json (the authoritative count)
                sq_contrib_list = contrib_registry.get(sq["id"], [])
                if src_id not in sq_contrib_list:
                    sq_contrib_list.append(src_id)
                contrib_registry[sq["id"]] = sq_contrib_list
                with open(contrib_file, "w", encoding="utf-8") as f:
                    json.dump(contrib_registry, f, indent=2)

                # sq["source_count"] is now derived from contributing_sources.json
                # but update it in-memory for limit_warnings to work this cycle
                sq["source_count"] = len(sq_contrib_list)
                state["total_sources"] += 1
                print(f"[RESEARCH_ENGINE] Source [{src_id}] contributed new facts. source_count={sq['source_count']}", flush=True)
            else:
                print(
                    f"[RESEARCH_ENGINE] Source [{src_id}] added no new facts to evidence summary. "
                    "Not counted against source budget.",
                    flush=True,
                )

            save_state(task_id, state)

    # After all sources processed, update search_query from gaps for next round
    current_gaps = sq.get("gaps", [])
    if current_gaps and not is_user_guidance:
        gap_basis = current_gaps[0]
        task_type = state.get("task_type", "factual")
        new_search_query = await formulate_search_query(
            gap_basis,
            task_type,
            state,
            intent_frame=state.get("intent_frame", ""),
        )
        sq["search_query"] = new_search_query
        print(f"[RESEARCH_ENGINE] Updated search_query for next round: '{new_search_query}'", flush=True)

    # Move to the evaluate step
    state["current_step"] = "evaluate"
    save_state(task_id, state)



def _build_completed_sq_summaries(task_id: str, state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Assemble a lightweight summary of all resolved sub-questions for the coverage check.

    Prefers sq_XX_summary.md (the bounded evidence summary) over the raw notes
    file. Falls back to a 300-char truncation of raw notes for old tasks that
    predate the incremental digest.

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
        # Prefer evidence_summary file; fall back to raw notes for old tasks
        summary_file = os.path.join(task_dir, f"{s['id']}_summary.md")
        notes_file = os.path.join(task_dir, f"{s['id']}_notes.md")
        notes_text = ""
        if os.path.exists(summary_file):
            with open(summary_file, "r", encoding="utf-8") as f:
                notes_text = f.read()
        elif os.path.exists(notes_file):
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

    task_dir = get_task_dir(task_id)
    notes_file = os.path.join(task_dir, f"{sq['id']}_notes.md")
    summary_file = os.path.join(task_dir, f"{sq['id']}_summary.md")

    # Check source cap before attempting evaluate — nothing new can be added
    # and the evaluate loop would just repeat the same failing call forever.
    if "source_cap_reached" in sq.get("limit_warnings", []) and sq["source_count"] == 0:
        print(
            f"[RESEARCH_ENGINE] SQ {sq['id']} source cap reached with zero contributing sources. "
            "Advancing to synthesize.",
            flush=True,
        )
        sq["status"] = "done"
        state["current_step"] = "synthesize"
        save_state(task_id, state)
        return

    # Use evidence_summary if it exists (bounded digest); fall back to raw notes for old tasks.
    # The raw notes file can be 60-97KB — passing it to the evaluator would saturate
    # the model's context and produce garbled non-JSON, triggering the 0% fallback.
    evidence_for_eval = ""
    if os.path.exists(summary_file):
        with open(summary_file, "r", encoding="utf-8") as f:
            evidence_for_eval = f.read()
        print(
            f"[RESEARCH_ENGINE] Evaluating from evidence_summary ({len(evidence_for_eval)} chars).",
            flush=True,
        )
    elif os.path.exists(notes_file):
        # Old task without summary — compress before sending to evaluator
        with open(notes_file, "r", encoding="utf-8") as f:
            raw_notes = f.read()
        evidence_for_eval = await _summarize_sq_notes(
            sq["question"],
            raw_notes,
            sq["id"],
            state.get("task_type", "factual"),
            task_id,
            task_dir,
            domain_level=state.get("domain_level", "specialist"),
        )
        print(
            f"[RESEARCH_ENGINE] Evaluating from compressed notes "
            f"({len(raw_notes)} → {len(evidence_for_eval)} chars).",
            flush=True,
        )

    if not evidence_for_eval.strip():
        print(f"[RESEARCH_ENGINE WARNING] No notes or summary for SQ {sq['id']}. "
              "Cannot evaluate — marking as needs_guidance.", flush=True)
        sq["status"] = "needs_guidance"
        state["status"] = "needs_guidance"
        state["struggling"] = True
        save_state(task_id, state)
        return
        
    print(f"[RESEARCH_ENGINE] Evaluating collected evidence for SQ ({sq['id']})...", flush=True)
    
    prompt = research_prompts.build_evaluate_prompt(
        sq["question"],
        evidence_for_eval,
        state["confidence_threshold"]
    )

    messages = [
        {"role": "system", "content": research_prompts.get_system_prompt(domain_level=state.get("domain_level", "specialist"))},
        {"role": "user", "content": prompt}
    ]
    
    state["ollama_calls"] += 1
    raw_response = await call_ollama(messages, num_predict=1024, think=True)
    
    # Parse evaluate output (must be valid JSON)
    try:
        evaluation = parse_json_response(raw_response)
        confidence = int(evaluation.get("confidence", 0))
        gaps = evaluation.get("gaps", [])
    except Exception as e:
        print(f"[RESEARCH_ENGINE WARNING] Failed to parse evaluate JSON: {e}. Output was: '{raw_response}'", flush=True)
        # Source-cap escape hatch: if no more sources can be added AND the eval
        # failed, we have no recovery path — advance to synthesize immediately
        # rather than looping forever on a permanently-failing evaluate call.
        if "source_cap_reached" in sq.get("limit_warnings", []):
            print(
                f"[RESEARCH_ENGINE] Source cap reached and evaluate failed — "
                "advancing SQ to done and proceeding to synthesize.",
                flush=True,
            )
            sq["status"] = "done"
            state["current_step"] = "synthesize"
            save_state(task_id, state)
            return
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
    max_sources_per_sq = state.get("max_sources_per_sq", 15)
    source_cap_hit = sq.get("source_count", 0) >= max_sources_per_sq or "source_cap_reached" in sq.get("limit_warnings", [])

    if is_sufficient or depth_exhausted or source_cap_hit:
        if is_sufficient or source_cap_hit:
            # Sub-question complete!
            sq["status"] = "done"
            if is_sufficient:
                print(f"[RESEARCH_ENGINE] SQ {sq['id']} fully resolved (Threshold met).", flush=True)
            else:
                print(f"[RESEARCH_ENGINE] SQ {sq['id']} source limit reached ({sq.get('source_count', 0)}/{max_sources_per_sq}). Marking SQ done and running coverage check.", flush=True)
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
                raw_coverage = await call_ollama(coverage_messages, num_predict=512, think=True)

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

                    # --- Phase 2 knowledge gate ---
                    # Before spawning a new SQ, check if saved knowledge already
                    # answers this specific proposed question. Avoids researching
                    # what Evelyn already knows from prior work.
                    sq_gate_evidence = ""
                    try:
                        # Cheap: chat + memory only (no vault/prior-task lookup here)
                        gate_history = get_recent_chat_history(20)
                        if gate_history:
                            sq_gate_evidence = "### Recent Conversation:\n" + "\n".join(
                                f"{m['role'].upper()}: {m['content']}" for m in gate_history
                            )
                        import memory_db as _mdb
                        _entries = _mdb.get_all_entries(statuses=["live"])
                        _matched = [
                            e for e in _entries
                            if evelyn_tools.get_jaccard_similarity(next_question, e.get("observation", "")) >= 0.2
                        ]
                        if _matched:
                            sq_gate_evidence += "\n\n### Memory Facts:\n" + "\n".join(
                                f"- {e['observation']}" for e in _matched[:5]
                            )
                    except Exception:
                        pass

                    sq_gate_prompt = research_prompts.build_prior_knowledge_prompt(
                        state["query"],
                        variant="saved",
                        evidence_text=sq_gate_evidence,
                        sub_question=next_question,
                    )
                    state["ollama_calls"] += 1
                    try:
                        raw_sq_gate = await call_ollama(
                            [{"role": "system", "content": research_prompts.get_system_prompt()},
                             {"role": "user", "content": sq_gate_prompt}],
                            num_predict=512,
                        )
                        sq_gate_result = parse_json_response(raw_sq_gate)
                        sq_gate_conf = int(sq_gate_result.get("confidence", 0))
                    except Exception:
                        sq_gate_conf = 0

                    sq_gate_threshold = getattr(cfg, "RESEARCH_NECESSITY_CONFIDENCE_THRESHOLD", 90)
                    if sq_gate_conf >= sq_gate_threshold:
                        print(
                            f"[RESEARCH_ENGINE] Phase 2 gate: proposed SQ '{next_question}' "
                            f"already answered by saved knowledge (confidence={sq_gate_conf}%). "
                            "Skipping — proceeding to synthesis.",
                            flush=True,
                        )
                        state["current_step"] = "synthesize"
                    else:
                        new_idx = len(state["plan"]["sub_questions"])
                        # Formulate search_query for the new SQ immediately
                        new_sq_search_query = await formulate_search_query(
                            next_question,
                            state.get("task_type", "factual"),
                            state,
                            intent_frame=state.get("intent_frame", ""),
                        )
                        new_sq = {
                            "id": f"sq_{new_idx + 1:02d}",
                            "question": next_question,
                            "search_query": new_sq_search_query,
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
        summary_file = os.path.join(task_dir, f"{sq['id']}_summary.md")
        notes_file = os.path.join(task_dir, f"{sq['id']}_notes.md")
        if os.path.exists(summary_file):
            with open(summary_file, "r", encoding="utf-8") as f:
                all_notes[sq["question"]] = f.read()
        elif os.path.exists(notes_file):
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

                    # extract aliases
                    if "aliases" in fm_data:
                        raw_aliases = fm_data["aliases"]
                        current_aliases = set(state.get("topic_aliases", []))
                        if isinstance(raw_aliases, list):
                            for a in raw_aliases:
                                clean_a = str(a).strip().strip("'\"`")
                                if clean_a:
                                    current_aliases.add(clean_a)
                        elif isinstance(raw_aliases, str):
                            for a in raw_aliases.split(","):
                                clean_a = a.strip().strip("'\"`")
                                if clean_a:
                                    current_aliases.add(clean_a)
                        state["topic_aliases"] = list(current_aliases)
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
        
    tags_list = []
        
    # Clean and append topic tags from state
    for tag in state.get("topic_tags", []):
        cleaned_tag = re.sub(r"[^\w\s-]", "", tag.lower())
        cleaned_tag = re.sub(r"[-\s]+", "-", cleaned_tag).strip("-_")
        if cleaned_tag and cleaned_tag not in tags_list:
            tags_list.append(cleaned_tag)
            
    tags_str = ", ".join(tags_list)
    clean_short_title = state["short_title"].replace('"', '\\"')
    clean_query = state['query'].replace('"', '\\"')
    
    triggered_by_val = state.get("triggered_by", "user")
    if isinstance(triggered_by_val, str) and triggered_by_val.lower() == "evelyn":
        triggered_by_val = "Evelyn"
        
    frontmatter = (
        "---\n"
        f"title: \"{clean_short_title}\"\n"
        f"research_query: \"{clean_query}\"\n"
        f"date created: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"research_task_id: {task_id}\n"
        f"scope: {state['scope']}\n"
        f"source_count: {state['total_sources']}\n"
        f"confidence: {state['confidence']}%\n"
        f"triggered_by: {triggered_by_val}\n"
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

            # Trigger memory refresh so vault indexer & Chroma ingest the new research report immediately
            try:
                server = sys.modules.get("evelyn_server") or sys.modules.get("__main__")
                if server and hasattr(server, "start_refresh_memory_internal"):
                    import asyncio
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(server.start_refresh_memory_internal())
                    except RuntimeError:
                        asyncio.run(server.start_refresh_memory_internal())
                else:
                    refresh_script = os.path.join(r"C:\Projects\LocalAI", "Evelyn", "tools", "refresh_memory.py")
                    if os.path.exists(refresh_script):
                        import subprocess
                        print(f"[RESEARCH_ENGINE] Triggering standalone memory refresh process...", flush=True)
                        subprocess.Popen([sys.executable, "-u", refresh_script], cwd=r"C:\Projects\LocalAI")
            except Exception as r_err:
                print(f"[RESEARCH_ENGINE WARNING] Could not trigger memory refresh after vault save: {r_err}", flush=True)
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

    # Build a snapshot of all known queries: queued + any task on disk
    # (running, pending, done, error — everything). This is the authoritative
    # dedup corpus and is checked with Jaccard similarity to catch rephrased
    # duplicates, not just exact matches.
    known_queries: list[str] = [q.get("query", "") for q in queue if q.get("query")]
    if os.path.exists(cfg.RESEARCH_DATA_DIR):
        for folder in os.listdir(cfg.RESEARCH_DATA_DIR):
            if folder.startswith("task_"):
                disk_state = load_state(folder)
                if disk_state:
                    dq = disk_state.get("query", "")
                    if dq:
                        known_queries.append(dq)

    def _is_duplicate(candidate: str) -> tuple[bool, str]:
        """Return (True, matched_query) if candidate is too similar to any known query."""
        from evelyn_tools import get_jaccard_similarity
        for kq in known_queries:
            if get_jaccard_similarity(candidate, kq) >= 0.45:
                return True, kq
        return False, ""

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
        raw = await call_ollama(messages, num_predict=4096)
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
                if not query:
                    continue
                if len(queue) >= cfg.RESEARCH_MAX_QUEUE_SIZE:
                    break

                # Jaccard dedup: reject if too similar to anything already queued
                # or already present as a task on disk (running, pending, or done).
                is_dup, matched = _is_duplicate(query)
                if is_dup:
                    print(
                        f"[RESEARCH_ENGINE] Skipping duplicate self-initiated topic "
                        f"'{query}' — too similar to existing: '{matched}'",
                        flush=True,
                    )
                    continue

                queue.append({
                    "query": query,
                    "scope": scope,
                    "priority": 1,
                    "source": "evelyn",
                    "intent_frame": intent_frame,
                    "created_at": datetime.datetime.now().isoformat()
                })
                known_queries.append(query)  # prevent intra-batch duplicates
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
