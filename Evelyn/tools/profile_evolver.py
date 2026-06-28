# profile_evolver.py
# date created: 2026-06-27 08:45:00
# date modified: 2026-06-27 08:46:04
# tags: #persona, #evolution, #profile, #directives, #llm

"""
profile_evolver.py — Idle-time auto-evolution for Evelyn's persona and profile documents.

Reviews accumulated context entries in memory_db against the three core identity
documents (Evelyn's persona, Ricky's profile, and System directives) and proposes
targeted updates staged for human review.

Exports:
  run_profile_evolution()       — Idle-time entry point; called from the server loop.
  cancel_pending_evolution()    — Called on each new chat request to free Ollama.
"""

import asyncio
import datetime
import importlib
import json
import os
import re
import time
import sys
import httpx
import yaml

import evelyn_config as cfg
import memory_db

# ---------------------------------------------------------------------------
# Category-to-document mapping
# ---------------------------------------------------------------------------
DOCUMENT_CATEGORIES = {
    "Evelyn_Narrative_Persona.md": [
        "Cat01-E", "Cat02-E", "Cat03-E", "Cat04-E", "Cat10-E",
    ],
    "Ricky_Narrative_Profile.md": [
        "Cat01-R", "Cat03-R", "Cat04-R", "Cat06-R", "Cat09-R", "Cat12-R",
    ],
    "System_Directives.md": [
        "Cat14-E", "Cat16-E", "Cat16-R",
    ],
}

# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------
_evolving = False
_evolver_task = None

_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(cfg.CHAT_DB_PATH)),
    "evelyn_evolution_state.json",
)

def _load_evolution_state() -> dict:
    """Load evolution high-water marks from disk.

    Returns:
        dict: State dict containing last_run_per_doc mapping.
    """
    default_state = {
        "last_run_per_doc": {
            "Evelyn_Narrative_Persona.md": 0.0,
            "Ricky_Narrative_Profile.md": 0.0,
            "System_Directives.md": 0.0,
        }
    }
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "last_run_per_doc" in data:
                # Merge with default to guarantee all keys exist
                for k, v in default_state["last_run_per_doc"].items():
                    if k not in data["last_run_per_doc"]:
                        data["last_run_per_doc"][k] = v
                return data
    except Exception as e:
        print(f"[PROFILE EVOLVER] Warning: could not load state file: {e}", flush=True)
    return default_state

def _save_evolution_state(state: dict) -> None:
    """Save evolution state to disk.

    Args:
        state: State dictionary to save.
    """
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[PROFILE EVOLVER] Warning: could not save state file: {e}", flush=True)

# ---------------------------------------------------------------------------
# Infrastructure & Mutual Exclusion
# ---------------------------------------------------------------------------

def _other_heavy_tasks_running() -> bool:
    """Check if any other heavy background task is currently active.

    Returns:
        bool: True if another heavy task is active, False otherwise.
    """
    # Check if fact extractor or consolidator is running
    try:
        import fact_extractor
        if fact_extractor._extracting:
            return True
    except (ImportError, AttributeError):
        pass

    try:
        import fact_consolidator
        if fact_consolidator._consolidating:
            return True
    except (ImportError, AttributeError):
        pass

    # Check server background tasks
    for mod_name in ("evelyn_server", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod:
            tasks = getattr(mod, "_background_tasks", None)
            if isinstance(tasks, dict):
                for k, task in tasks.items():
                    if k == "profile_evolver":
                        continue
                    if k.startswith("task_"):
                        if task.get("status") in ("running", "searching", "synthesizing"):
                            return True
                    elif task.get("status") == "running":
                        return True
    return False

def _set_status_in_server(status: str | None) -> None:
    """Register or clear status in the server's background task registry.

    Args:
        status: Status string (e.g. 'running'), or None to clear.
    """
    for mod_name in ("evelyn_server", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod:
            tasks = getattr(mod, "_background_tasks", None)
            if isinstance(tasks, dict):
                if status == "running":
                    tasks["profile_evolver"] = {
                        "status": "running",
                        "started_at": time.time(),
                    }
                else:
                    tasks.pop("profile_evolver", None)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cancel_pending_evolution():
    """Cancel any in-flight profile evolution task.

    Frees the Ollama instance immediately when a new user chat request is received.
    """
    global _evolver_task, _evolving
    if _evolver_task and not _evolver_task.done():
        _evolver_task.cancel()
        _evolving = False
        _set_status_in_server(None)
        print("[PROFILE EVOLVER] Cancelled (new chat request).", flush=True)
    _evolver_task = None

async def run_profile_evolution():
    """Run the profile auto-evolution process as a background task.

    Coordinates mutual exclusion, checks cooldowns, and processes each document.
    """
    global _evolving, _evolver_task
    importlib.reload(cfg)

    if not getattr(cfg, "PROFILE_EVOLUTION_ENABLED", False):
        return

    if _evolving:
        print("[PROFILE EVOLVER] Already running — skipping.", flush=True)
        return

    if _other_heavy_tasks_running():
        print("[PROFILE EVOLVER] Deferring execution due to other active heavy tasks.", flush=True)
        return

    _evolving = True
    _set_status_in_server("running")
    _evolver_task = asyncio.current_task()

    try:
        state = _load_evolution_state()
        now = time.time()

        for filename, categories in DOCUMENT_CATEGORIES.items():
            last_run = state["last_run_per_doc"].get(filename, 0.0)
            cooldown = getattr(cfg, "PROFILE_EVOLUTION_COOLDOWN", 86400)
            if now - last_run < cooldown:
                continue

            # Fetch context entries active since last run.
            # An entry qualifies if it was CREATED or UPDATED after the last
            # evolution run. This ensures that entries refined/corrected by the
            # fact consolidator (which bumps updated_at without changing
            # created_at) are counted as fresh signal for evolution.
            changed_entries = []
            for cat in categories:
                entries = memory_db.get_entries_by_category(cat, status="live")
                for entry in entries:
                    created_at  = entry.get("created_at", 0.0) or 0.0
                    updated_at  = entry.get("updated_at", 0.0)  or 0.0
                    last_touched = max(created_at, updated_at)
                    if last_touched > last_run:
                        changed_entries.append(entry)

            min_entries = getattr(cfg, "PROFILE_EVOLUTION_MIN_ENTRIES", 5)
            if len(changed_entries) < min_entries:
                print(f"[PROFILE EVOLVER] {filename}: Only {len(changed_entries)} new/updated entries (need {min_entries}). Skipping.", flush=True)
                continue

            print(f"[PROFILE EVOLVER] Evolving {filename} with {len(changed_entries)} new/updated entries...", flush=True)
            success = await _evolve_document(filename, changed_entries)
            if success:
                # Only update state on successful proposal creation
                state["last_run_per_doc"][filename] = now
                _save_evolution_state(state)

    except asyncio.CancelledError:
        print("[PROFILE EVOLVER] Execution cancelled.", flush=True)
    except Exception as e:
        print(f"[PROFILE EVOLVER ERROR] Exception: {e}", flush=True)
    finally:
        _evolving = False
        _set_status_in_server(None)
        _evolver_task = None

# ---------------------------------------------------------------------------
# Evolution core
# ---------------------------------------------------------------------------

async def _call_ollama(messages: list[dict], num_predict: int = 1500) -> str:
    """Async helper to call Ollama.

    Args:
        messages: List of message dictionaries.
        num_predict: Maximum prediction tokens.

    Returns:
        str: Response content from the model.
    """
    importlib.reload(cfg)
    override = getattr(cfg, "PROFILE_EVOLUTION_MODEL_OVERRIDE", "default")
    model = cfg.MODEL_NAME if override == "default" else override

    options = {
        "num_ctx": cfg.NUM_CTX,
        "num_predict": num_predict,
    }
    for key, val in {
        "temperature": cfg.TEMPERATURE,
        "min_p": cfg.MIN_P,
        "top_k": cfg.TOP_K,
        "top_p": cfg.TOP_P,
        "repeat_penalty": cfg.REPEAT_PENALTY,
        "repeat_last_n": cfg.REPEAT_LAST_N,
        "seed": cfg.SEED,
    }.items():
        if val is not None:
            options[key] = val

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": options,
        "think": True,  # Proposing evolution requires thinking / reasoning
    }

    content_buffer = ""
    timeout = 180  # Generous timeout for reasoning and response generation

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", f"{cfg.OLLAMA_URL}/api/chat", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                    msg = chunk.get("message", {})
                    content_buffer += msg.get("content", "")
                except json.JSONDecodeError:
                    continue

    return content_buffer.strip()

async def _evolve_document(filename: str, new_entries: list[dict]) -> bool:
    """Propose updates to a narrative persona/profile document.

    Args:
        filename: Name of the file, e.g. "Evelyn_Narrative_Persona.md".
        new_entries: List of newly added context entries.

    Returns:
        bool: True if a proposal was successfully created, False otherwise.
    """
    importlib.reload(cfg)
    persona_dir = getattr(cfg, "PERSONA_DIR", None)
    if not persona_dir:
        persona_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "persona")

    fpath = os.path.join(persona_dir, filename)
    if not os.path.exists(fpath):
        print(f"[PROFILE EVOLVER] Error: document file not found at {fpath}", flush=True)
        return False

    with open(fpath, "r", encoding="utf-8") as f:
        current_content = f.read()

    # Format the accumulated context entries
    formatted_entries = []
    for entry in new_entries:
        date = entry.get("date", "Unknown Date")
        obs = entry.get("observation", "")
        formatted_entries.append(f"- [{date}] {obs}")
    evidence_block = "\n".join(formatted_entries)

    # Prompt design encouraging natural tone/identity evolution
    prompt = (
        f"You are refining a living persona/directives document based on accumulated evidence from "
        f"recent conversations.\n\n"
        f"CURRENT DOCUMENT ({filename}):\n"
        f"---\n"
        f"{current_content}\n"
        f"---\n\n"
        f"ACCUMULATED EVIDENCE (recent memory updates):\n"
        f"{evidence_block}\n\n"
        f"INSTRUCTIONS:\n"
        f"- This is a LIVING document. You are encouraged to evolve it authentically.\n"
        f"- Add new insights that are well-supported by the evidence.\n"
        f"- Refine existing points if evidence shows they have evolved.\n"
        f"- Tone, personality, and style changes ARE welcome if the evidence supports them.\n"
        f"  Evelyn is a growing identity — not a frozen snapshot.\n"
        f"- Do NOT add speculative or single-source observations.\n"
        f"- Do NOT remove existing content unless the evidence clearly contradicts it.\n"
        f"- Keep the document concise — it is injected into every system prompt.\n"
        f"- Output the COMPLETE updated document (including YAML frontmatter and markdown headers).\n"
        f"- Output ONLY the markdown document content, no explanation, no markdown code blocks wrapping it.\n"
        f"- If no changes are warranted, output the document exactly as it is."
    )

    messages = [
        {
            "role": "system",
            "content": "You are a precise document updater. Output the complete updated document content only.",
        },
        {"role": "user", "content": prompt},
    ]

    try:
        proposed_content = await _call_ollama(messages)
        if not proposed_content:
            print(f"[PROFILE EVOLVER] Warning: empty proposed content received from model.", flush=True)
            return False

        # Clean proposed content if the model wrapped it in markdown code blocks
        if proposed_content.startswith("```"):
            lines = proposed_content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            proposed_content = "\n".join(lines).strip()

        # Check if changes were made
        if proposed_content == current_content.strip() or not proposed_content:
            print(f"[PROFILE EVOLVER] No changes proposed for {filename}.", flush=True)
            return False

        # Create proposal record in the database
        reason_prompt = (
            f"Compare the current document and the proposed update. Summarize what changed and why, "
            f"citing the context entries that supported this evolution.\n\n"
            f"CURRENT:\n{current_content}\n\n"
            f"PROPOSED:\n{proposed_content}\n\n"
            f"Output a brief, one-to-two sentence explanation only."
        )
        reason_messages = [
            {"role": "system", "content": "You are a helpful summarizing assistant. Output one or two sentences only."},
            {"role": "user", "content": reason_prompt},
        ]
        reason = await _call_ollama(reason_messages, num_predict=150)
        if not reason:
            reason = "Evolving profile based on recent context entries."

        source_ids = [int(entry["id"]) for entry in new_entries if entry.get("id")]

        # We repurpose suggested_category to hold the filename,
        # merged_observation to hold the proposed new file content,
        # merged_tags to hold the original content (to render diff in UI),
        # type = 'profile_update', and status = 'pending'.
        memory_db.insert_proposal(
            type="profile_update",
            suggested_category=filename,
            merged_observation=proposed_content,
            merged_tags=current_content,  # Save current_content as original for diffing
            reason=reason,
            source_ids=source_ids,
        )
        print(f"[PROFILE EVOLVER] Created profile_update proposal for {filename}.", flush=True)
        return True

    except Exception as e:
        print(f"[PROFILE EVOLVER ERROR] Failed to evolve {filename}: {e}", flush=True)
        return False
