# research_engine.py
# date created: 2026-05-26
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


def save_state(task_id: str, state: Dict[str, Any]) -> None:
    """Persist the current research task state to disk.

    Ensures the task directory exists before writing.

    Args:
        task_id: The unique task identifier.
        state: State dictionary to save.
    """
    task_dir = get_task_dir(task_id)
    os.makedirs(task_dir, exist_ok=True)
    state_file = os.path.join(task_dir, "state.json")
    
    # Update timestamps
    state["updated_at"] = datetime.datetime.now().isoformat()
    
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[RESEARCH_ENGINE ERROR] Failed to save state for {task_id}: {e}", flush=True)


def create_research_task(query: str, scope: str = "standard", triggered_by: str = "user") -> str:
    """Initialize a brand-new research task and persist its base state.

    Args:
        query: The main search query or research topic.
        scope: Scope of the research ('quick', 'standard', 'deep').
        triggered_by: Identifies the initiator ('user', 'idle', 'evelyn').

    Returns:
        str: Generated unique task_id.
    """
    importlib.reload(cfg)
    
    # Generate unique task_id based on timestamp
    task_id = f"task_{int(time.time())}_{os.urandom(4).hex()}"
    
    # Establish scope presets
    presets = {
        "quick": {
            "sub_questions_limit": 3,
            "threshold": 70,
            "max_depth": 2,
            "max_sources": 5,
        },
        "standard": {
            "sub_questions_limit": 5,
            "threshold": 80,
            "max_depth": 3,
            "max_sources": 8,
        },
        "deep": {
            "sub_questions_limit": 6,
            "threshold": 85,
            "max_depth": 5,
            "max_sources": 10,
        }
    }
    
    # Default fallback to standard if scope invalid
    scope = scope.lower() if scope.lower() in presets else "standard"
    scope_cfg = presets[scope]
    
    state = {
        "task_id": task_id,
        "query": query,
        "scope": scope,
        "status": "pending",
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
        
        # Scoped thresholds
        "confidence_threshold": scope_cfg["threshold"],
        "max_search_depth": scope_cfg["max_depth"],
        "max_sources_per_sq": scope_cfg["max_sources"],
        "sub_questions_limit": scope_cfg["sub_questions_limit"]
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
        "stream": False,
        "options": options,
        "think": False # Native reasoning off to fit maximum factual context
    }
    
    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(f"{cfg.OLLAMA_URL}/api/chat", json=payload)
        resp.raise_for_status()
        result = resp.json()
        
    content = result.get("message", {}).get("content", "").strip()
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


async def step_plan(task_id: str, state: Dict[str, Any]) -> None:
    """Execute the PLAN step of a research task.

    Args:
        task_id: Unique task identifier.
        state: State dictionary to modify.
    """
    print(f"[RESEARCH_ENGINE] Planning sub-questions for task {task_id}...", flush=True)
    
    prompt = research_prompts.build_plan_prompt(
        state["query"],
        state["scope"],
        state["sub_questions_limit"]
    )
    
    messages = [
        {"role": "system", "content": research_prompts.get_system_prompt()},
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
    
    if os.path.exists(gaps_file) and state["search_depth"] > 0:
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
    
    if not parsed_sources:
        print("[RESEARCH_ENGINE] No web search results found for query. Skipping extraction.", flush=True)
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
            
        # Scrape page first to verify success before counting it against constraints
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
        
        print(f"[RESEARCH_ENGINE] Extracting facts from: '{title}' [{src_id}]", flush=True)
        
        # We extract chunk by chunk if the page has multiple chunks
        chunks = scrape_result["chunks"]
        for idx, chunk in enumerate(chunks):
            prompt = research_prompts.build_extract_prompt(
                sq["question"],
                src_id,
                title,
                url,
                chunk,
                current_notes
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
        # Clean potential markdown wrapping out of the JSON response
        cleaned_json = raw_response.strip()
        if cleaned_json.startswith("```"):
            # Strip first line
            cleaned_json = cleaned_json.split("\n", 1)[1]
            if cleaned_json.endswith("```"):
                cleaned_json = cleaned_json.rsplit("\n", 1)[0]
        cleaned_json = cleaned_json.strip()
        
        evaluation = json.loads(cleaned_json)
        confidence = int(evaluation.get("confidence", 0))
        gaps = evaluation.get("gaps", [])
    except Exception as e:
        print(f"[RESEARCH_ENGINE WARNING] Failed to parse evaluate JSON: {e}. Output was: '{raw_response}'", flush=True)
        # Default fallback
        confidence = 0
        gaps = ["Insufficient evidence collected."]
        
    print(f"[RESEARCH_ENGINE] Evaluation results -> Confidence: {confidence}%, Target: {state['confidence_threshold']}%", flush=True)
    
    sq["confidence"] = confidence
    
    # Save gaps on disk for the next search iteration
    gaps_file = os.path.join(task_dir, f"{sq['id']}_gaps.json")
    with open(gaps_file, "w", encoding="utf-8") as f:
        json.dump({"gaps": gaps}, f, indent=2)
        
    # Evaluate termination decisions
    is_sufficient = confidence >= state["confidence_threshold"]
    depth_exhausted = state["search_depth"] >= state["max_search_depth"] - 1
    
    if is_sufficient or depth_exhausted:
        # Sub-question complete!
        sq["status"] = "done"
        if is_sufficient:
            print(f"[RESEARCH_ENGINE] SQ {sq['id']} fully resolved (Threshold met).", flush=True)
        else:
            print(f"[RESEARCH_ENGINE] SQ {sq['id']} complete (Search depth exhausted).", flush=True)
            
        state["current_sq_idx"] += 1
        state["current_step"] = "search"
        state["search_depth"] = 0
    else:
        # Loop again!
        print(f"[RESEARCH_ENGINE] SQ {sq['id']} requires further search. Running iteration {state['search_depth'] + 2}.", flush=True)
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
        state["sources_registry"]
    )
    
    messages = [
        {"role": "system", "content": research_prompts.get_system_prompt()},
        {"role": "user", "content": prompt}
    ]
    
    state["ollama_calls"] += 1
    final_report = await call_ollama(messages, num_predict=4096)
    
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
    
    # Copy file to Obsidian Vault
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
            
        frontmatter = (
            "---\n"
            f"title: \"{state['query']}\"\n"
            f"date created: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"research_task_id: {task_id}\n"
            f"scope: {state['scope']}\n"
            f"source_count: {state['total_sources']}\n"
            f"confidence: {state['confidence']}%\n"
            f"triggered_by: {state['triggered_by']}\n"
            "---\n\n"
        )
        
        with open(vault_file_path, "w", encoding="utf-8") as f:
            f.write(frontmatter + clean_report_body)
            
        state["vault_path"] = vault_file_path
        print(f"[RESEARCH_ENGINE] Saved report to Obsidian Vault: {vault_file_path}", flush=True)
    except Exception as e:
        print(f"[RESEARCH_ENGINE ERROR] Failed to copy report to Vault: {e}", flush=True)
        
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
        
    if state["status"] in ("done", "error", "cancelled"):
        return True
        
    # Increment high-level orchestrator turns (steps)
    if "orchestrator_turns" not in state:
        state["orchestrator_turns"] = 0
    state["orchestrator_turns"] += 1
    
    # Verify safety net limits (Emergency Brakes on State Loop)
    turn_limit = getattr(cfg, "RESEARCH_MAX_ORCHESTRATOR_TURNS", 50)
    if state["orchestrator_turns"] >= turn_limit:
        print(f"[RESEARCH_ENGINE WARNING] Safety cap reached ({turn_limit} orchestrator turns). Forcing Synthesis.", flush=True)
        state["termination_reason"] = "turn_cap"
        state["current_step"] = "synthesize"
        save_state(task_id, state)
        
    # Check wall-clock timeout safety limit
    timeout_limit = getattr(cfg, "RESEARCH_WALL_CLOCK_TIMEOUT", 7200)
    created_ts = datetime.datetime.fromisoformat(state["created_at"])
    elapsed_seconds = (datetime.datetime.now() - created_ts).total_seconds()
    if elapsed_seconds >= timeout_limit:
        print(f"[RESEARCH_ENGINE WARNING] Task hit wall-clock timeout ({timeout_limit}s). Forcing Synthesis.", flush=True)
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
        state["error"] = f"{type(e).__name__}: {str(e)}"
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


if __name__ == "__main__":
    # CLI Testing capability (Phase 1 Checklist)
    import argparse
    
    parser = argparse.ArgumentParser(description="Evelyn Deep Research CLI Runner")
    parser.add_argument("query", type=str, help="Research topic or search query")
    parser.add_argument("--scope", type=str, default="standard", choices=["quick", "standard", "deep"], help="Research depth scope")
    
    args = parser.parse_args()
    
    async def main():
        if args.query.startswith("task_") and os.path.exists(os.path.join(cfg.RESEARCH_DATA_DIR, args.query)):
            task_id = args.query
            print(f"[RESEARCH_ENGINE] Resuming existing task: {task_id}", flush=True)
            state = load_state(task_id)
            if state and state["status"] == "error":
                state["status"] = "searching" if state["current_step"] in ("search", "evaluate") else "pending"
                state["error"] = None
                save_state(task_id, state)
        else:
            task_id = create_research_task(args.query, scope=args.scope, triggered_by="user")
        await run_full_research(task_id)
        
    asyncio.run(main())
