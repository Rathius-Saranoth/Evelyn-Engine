# research_engine.py
# date created: 2026-05-26
# date modified: 2026-06-21 07:49:05
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
import sys
import time
import traceback
from typing import List, Dict, Any, Tuple, Optional

import httpx

# Reconfigure stdout/stderr to avoid Windows CP1252 character mapping crashes on international titles
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(errors='replace')
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
            return json.load(f)
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
    
    # Merge status from disk if updated out-of-band (e.g. paused/cancelled by server chat interrupt)
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
            
    # Update timestamps
    state["updated_at"] = datetime.datetime.now().isoformat()
    
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[RESEARCH_ENGINE ERROR] Failed to save state for {task_id}: {e}", flush=True)


def create_research_task(query: str, scope: str = "standard", triggered_by: str = "user", initial_status: str = "pending") -> str:
    """Initialize a brand-new research task and persist its base state.

    Args:
        query: The main search query or research topic, be specific.
        scope: Scope of the research ('quick', 'standard', 'deep').
        triggered_by: Identifies the initiator ('user', 'idle', 'evelyn').
        initial_status: The initial status of the task ('pending' or 'running').

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


def parse_plan_markdown(markdown_content: str) -> List[str]:
    """Parse planning sub-questions out of LLM markdown output.

    Looks for standard numbered list matches.

    Args:
        markdown_content: Raw LLM response string.

    Returns:
        List[str]: Parsed sub-question strings.
    """
    questions = []
    # Match lines like "1. What is X?" or " - 2. How does Y work?"
    pattern = re.compile(r"^\s*\d+\.\s*(.+)$", re.MULTILINE)
    
    for match in pattern.finditer(markdown_content):
        # Strip trailing punctuation, clean whitespace, strip quotes
        q = match.group(1).strip().strip('"\'*')
        if q:
            questions.append(q)
            
    return questions


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


async def step_plan(task_id: str, state: Dict[str, Any]) -> None:
    """Execute the PLAN step of a research task.

    Classifies the query into a task type (factual/comparison/troubleshooting/opinion)
    using zero-cost keyword heuristics, then generates sub-questions via the LLM.
    The task_type is persisted in state so subsequent steps can inject the matching
    skill template without re-classifying.

    Args:
        task_id: Unique task identifier.
        state: State dictionary to modify.
    """
    print(f"[RESEARCH_ENGINE] Planning sub-questions for task {task_id}...", flush=True)

    # Classify query type and domain level once at plan time — zero LLM cost (Hermes Tier 2 #8b)
    task_type = research_prompts.classify_research_query(state["query"])
    domain_level = research_prompts.classify_domain_level(state["query"])
    state["task_type"] = task_type
    state["domain_level"] = domain_level
    print(f"[RESEARCH_ENGINE] Classified query: task_type='{task_type}', domain_level='{domain_level}'", flush=True)

    prompt = research_prompts.build_plan_prompt(
        state["query"],
        state["scope"],
        state["sub_questions_limit"],
        domain_level=domain_level,
    )

    messages = [
        {"role": "system", "content": research_prompts.get_system_prompt(domain_level=domain_level)},
        {"role": "user", "content": prompt}
    ]

    state["ollama_calls"] += 1
    raw_response = await call_ollama(messages, num_predict=1024)

    # Save raw plan file for audit trail
    task_dir = get_task_dir(task_id)
    with open(os.path.join(task_dir, "plan.md"), "w", encoding="utf-8") as f:
        f.write(raw_response)

    sub_questions = parse_plan_markdown(raw_response)

    if not sub_questions:
        print("[RESEARCH_ENGINE WARNING] Failed to parse sub-questions. Defaulting to main query.", flush=True)
        sub_questions = [state["query"]]

    state["plan"]["sub_questions"] = [
        {
            "id": f"sq_{i:02d}",
            "question": q,
            "status": "pending",
            "source_count": 0,
            "confidence": 0,
            "search_depth": 0
        }
        for i, q in enumerate(sub_questions, 1)
    ]

    state["current_step"] = "search"
    state["current_sq_idx"] = 0
    state["search_depth"] = 0
    state["status"] = "searching"

    save_state(task_id, state)
    print(f"[RESEARCH_ENGINE] Formulated {len(sub_questions)} sub-questions successfully.", flush=True)


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
    
    # Load any existing gaps or notes to customize the search query
    gaps_file = os.path.join(get_task_dir(task_id), f"{sq['id']}_gaps.json")
    search_query = sq["question"]
    
    if os.path.exists(gaps_file):
        try:
            with open(gaps_file, "r", encoding="utf-8") as f:
                gaps_data = json.load(f)
                gaps = gaps_data.get("gaps", [])
                if gaps:
                    # Incorporate the highest priority gap into query reformulation
                    search_query = f"{sq['question']} {gaps[0]}"
                    print(f"[RESEARCH_ENGINE] Reformulated query based on gaps: '{search_query}'", flush=True)
        except Exception:
            pass
            
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
        print("[RESEARCH_ENGINE] No web search, vault, or cross-task results found. Skipping extraction.", flush=True)
        # Advance state to prevent infinite retry
        sq["status"] = "done"
        state["current_sq_idx"] += 1
        state["search_depth"] = 0
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
                {"role": "system", "content": research_prompts.get_system_prompt()},
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
    
    # If notes don't exist, we can't evaluate
    if not os.path.exists(notes_file):
        print(f"[RESEARCH_ENGINE] No notes file found for {sq['id']}. Skipping evaluation.", flush=True)
        sq["status"] = "done"
        sq["confidence"] = 0
        state["current_sq_idx"] += 1
        state["current_step"] = "search"
        state["search_depth"] = 0
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
        {"role": "system", "content": research_prompts.get_system_prompt()},
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
            state["current_sq_idx"] += 1
            state["current_step"] = "search"
            state["search_depth"] = 0
            if os.path.exists(gaps_file):
                os.remove(gaps_file)
        else:
            print(f"[RESEARCH_ENGINE] SQ {sq['id']} exhausted search depth with low confidence. Pausing for guidance.", flush=True)
            sq["status"] = "needs_guidance"
            state["status"] = "needs_guidance"
            state["struggling"] = True
            
        save_state(task_id, state)
    else:
        # Loop again!
        print(f"[RESEARCH_ENGINE] SQ {sq['id']} requires further search. Running iteration {state['search_depth'] + 2}.", flush=True)
        
        # Auto-Rewrite Logic
        notes_file = os.path.join(task_dir, f"{sq['id']}_notes.md")
        current_notes = ""
        if os.path.exists(notes_file):
            with open(notes_file, "r", encoding="utf-8") as f:
                current_notes = f.read()
                
        rewrite_prompt = research_prompts.build_rewrite_prompt(sq["question"], current_notes, gaps)
        rewrite_messages = [
            {"role": "system", "content": research_prompts.get_system_prompt()},
            {"role": "user", "content": rewrite_prompt}
        ]
        state["ollama_calls"] += 1
        print(f"[RESEARCH_ENGINE] Auto-rewriting SQ {sq['id']} due to low confidence ({confidence}%)...", flush=True)
        rewritten_q = await call_ollama(rewrite_messages, num_predict=512)
        rewritten_q = rewritten_q.strip()
        
        # Semantic divergence check: prevent verbatim echoing
        if rewritten_q.lower() == sq["question"].lower() or not rewritten_q:
            print(f"[RESEARCH_ENGINE WARNING] Auto-rewrite returned identical/empty question. Keeping original.", flush=True)
        else:
            print(f"[RESEARCH_ENGINE] Rewrote SQ to: '{rewritten_q}'", flush=True)
            sq["original_question"] = sq.get("original_question", sq["question"])
            sq["question"] = rewritten_q
            # Clear gaps file since the rewrite absorbs them
            if os.path.exists(gaps_file):
                os.remove(gaps_file)
                sq["gaps"] = []

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


async def step_synthesize(task_id: str, state: Dict[str, Any]) -> None:
    """Execute the final SYNTHESIZE step of a research task.

    Compiles final reports and propagates output to the Obsidian Vault.

    Args:
        task_id: Unique task identifier.
        state: State dictionary to modify.
    """
    print(f"[RESEARCH_ENGINE] Synthesizing final report for task {task_id}...", flush=True)
    task_dir = get_task_dir(task_id)
    
    # Load all sub-question notes
    all_notes = {}
    for sq in state["plan"]["sub_questions"]:
        notes_file = os.path.join(task_dir, f"{sq['id']}_notes.md")
        if os.path.exists(notes_file):
            with open(notes_file, "r", encoding="utf-8") as f:
                all_notes[sq["question"]] = f.read()
        else:
            all_notes[sq["question"]] = "*(No evidence collected)*"
            
    prompt = research_prompts.build_synthesize_prompt(
        state["query"],
        all_notes,
        state["sources_registry"],
        domain_level=state.get("domain_level", "specialist"),
    )

    messages = [
        {"role": "system", "content": research_prompts.get_system_prompt(domain_level=state.get("domain_level", "specialist"))},
        {"role": "user", "content": prompt}
    ]

    state["ollama_calls"] += 1
    # 6144 tokens gives ~4500 words — enough for dense 8-SQ deep reports.
    # Raised from 4096 as part of the deep-scope budget review (2026-06-21).
    final_report = await call_ollama(messages, num_predict=6144)
    
    # Save report locally
    report_file = os.path.join(task_dir, "report.md")
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(final_report)
        
    # Parse actual overall confidence score out of report YAML frontmatter if present
    parsed_confidence = state["confidence"]
    try:
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", final_report, re.DOTALL)
        if fm_match:
            frontmatter_text = fm_match.group(1)
            conf_match = re.search(r"confidence:\s*(\d+)", frontmatter_text, re.IGNORECASE)
            if conf_match:
                parsed_confidence = int(conf_match.group(1))
    except Exception:
        pass
        
    state["confidence"] = parsed_confidence
    state["current_step"] = "done"
    state["status"] = "done"
    
    # --- Post-Synthesis Triage Logic ---
    state["synthesis_iterations"] = state.get("synthesis_iterations", 0) + 1
    max_synthesis_iters = getattr(cfg, "MAX_SYNTHESIS_ITERATIONS", 3)
    
    # Identify low-confidence sub-questions that haven't been removed/split
    low_conf_sqs = []
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
            {"role": "system", "content": research_prompts.get_system_prompt()},
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
            slug = re.sub(r"[^\w\s-]", "", state["query"].lower())
            slug = re.sub(r"[-\s]+", "-", slug).strip("-_")
            vault_filename = f"{slug}.md"
            
            vault_dir = getattr(cfg, "RESEARCH_VAULT_DIR", r"G:\My Drive\Obsidian_Vault\Evelyn\Research")
            os.makedirs(vault_dir, exist_ok=True)
            vault_file_path = os.path.join(vault_dir, vault_filename)
            
            # Build YAML frontmatter to match requirements
            clean_report_body = final_report
            # If the report already has frontmatter, strip it to write a unified, structured one
            if final_report.startswith("---"):
                clean_report_body = re.sub(r"^---.*?---\s*\n", "", final_report, count=1, flags=re.DOTALL)
                
            # Build tags array based on quality
            tags_list = ["research/done"]
            if state["confidence"] >= 80:
                tags_list.append("research/high-quality")
            else:
                tags_list.append("research/partial")
            
            tags_str = ", ".join(tags_list)
            
            frontmatter = (
                "---\n"
                f"title: \"{state['query']}\"\n"
                f"date created: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"research_task_id: {task_id}\n"
                f"scope: {state['scope']}\n"
                f"source_count: {state['total_sources']}\n"
                f"confidence: {state['confidence']}%\n"
                f"triggered_by: {state['triggered_by']}\n"
                f"tags: [{tags_str}]\n"
                "---\n\n"
            )
            
            with open(vault_file_path, "w", encoding="utf-8") as f:
                f.write(frontmatter + clean_report_body)
                
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

    # Check wall-clock timeout safety limit.
    timeout_limit = state.get(
        "wall_clock_timeout",
        getattr(cfg, "RESEARCH_WALL_CLOCK_TIMEOUT", 7200)
    )
    created_ts = datetime.datetime.fromisoformat(state["created_at"])
    elapsed_seconds = (datetime.datetime.now() - created_ts).total_seconds()
    if elapsed_seconds >= timeout_limit:
        print(
            f"[RESEARCH_ENGINE WARNING] Task hit wall-clock timeout "
            f"({timeout_limit}s / {timeout_limit // 3600}h for scope='{state.get('scope', 'unknown')}'). "
            f"Forcing Synthesis.",
            flush=True,
        )
        state["termination_reason"] = "timeout"
        state["current_step"] = "synthesize"
        save_state(task_id, state)
        
    step = state["current_step"]
    try:
        if step == "plan":
            await step_plan(task_id, state)
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
Do NOT include extremely broad topics, personal plans, or vague ideas. Focus on concrete, searchable questions. Keep each query to one topic.

Output ONLY a YAML block in this exact format:

```yaml
topics:
  - query: "research question 1"
    scope: "standard"
  - query: "research question 2"
    scope: "deep"
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
                if query and len(queue) < cfg.RESEARCH_MAX_QUEUE_SIZE:
                    # Check if already researched or queued
                    already_exists = any(q["query"].lower() == query.lower() for q in queue)
                    if not already_exists:
                        queue.append({
                            "query": query,
                            "scope": scope,
                            "priority": 1,
                            "source": "evelyn",
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
