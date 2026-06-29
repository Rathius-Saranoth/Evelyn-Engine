# profile_evolver.py
# date created: 2026-06-27 08:45:00
# date modified: 2026-06-29
# tags: #persona, #evolution, #profile, #directives, #llm

"""
profile_evolver.py — Idle-time auto-evolution for Evelyn's persona and profile documents.

Reviews accumulated context entries in memory_db against the three core identity
documents (Evelyn's persona, Ricky's profile, and System directives) and proposes
targeted updates staged for human review.

Evolution is split into batches of PROFILE_EVOLUTION_BATCH_SIZE entries per Ollama
call to avoid context-window saturation. Each pass uses the previous output as the
working document. Progress is persisted to disk after every successful pass so a
cancelled run (e.g. interrupted by an incoming chat) resumes from where it left off
rather than starting over.

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

_DATA_DIR = os.path.dirname(os.path.abspath(cfg.CHAT_DB_PATH))


def _draft_path(filename: str) -> str:
    """Return the absolute path to the per-document evolution draft file.

    The draft captures the accumulated working document after each successful
    pass so evolution can resume across interrupted runs.

    Args:
        filename: Document basename, e.g. 'Ricky_Narrative_Profile.md'.

    Returns:
        str: Absolute path to the draft file.
    """
    safe = filename.replace(" ", "_").replace(".md", "")
    return os.path.join(_DATA_DIR, f"evelyn_evolution_draft_{safe}.md")


def _load_evolution_state() -> dict:
    """Load evolution state from disk.

    State contains:
      - last_run_per_doc: Unix timestamp of the last *completed* evolution run
        per document. Only advances when a proposal is successfully created.
      - draft_cursor_per_doc: Max last_touched timestamp of entries already
        incorporated into the current in-progress draft. Used to resume
        interrupted multi-pass runs without reprocessing completed batches.

    Returns:
        dict: State dict with guaranteed keys for all tracked documents.
    """
    doc_keys = list(DOCUMENT_CATEGORIES.keys())
    default_state = {
        "last_run_per_doc":    {k: 0.0 for k in doc_keys},
        "draft_cursor_per_doc": {k: 0.0 for k in doc_keys},
    }
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "last_run_per_doc" in data:
                # Merge — guarantee all keys exist for both sub-dicts
                for k in doc_keys:
                    if k not in data["last_run_per_doc"]:
                        data["last_run_per_doc"][k] = 0.0
                if "draft_cursor_per_doc" not in data:
                    data["draft_cursor_per_doc"] = {k: 0.0 for k in doc_keys}
                else:
                    for k in doc_keys:
                        if k not in data["draft_cursor_per_doc"]:
                            data["draft_cursor_per_doc"][k] = 0.0
                return data
    except Exception as e:
        print(f"[PROFILE EVOLVER] Warning: could not load state file: {e}", flush=True)
    return default_state


def _save_evolution_state(state: dict) -> None:
    """Save evolution state to disk.

    Args:
        state: State dictionary to persist.
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
    Draft progress is already persisted to disk at each pass boundary, so no work
    is lost — the next idle window will resume from the last completed pass.
    """
    global _evolver_task, _evolving
    if _evolver_task and not _evolver_task.done():
        _evolver_task.cancel()
        _evolving = False
        _set_status_in_server(None)
        print("[PROFILE EVOLVER] Cancelled (new chat request). Draft progress saved.", flush=True)
    _evolver_task = None

async def run_profile_evolution():
    """Run the profile auto-evolution process as a background task.

    Coordinates mutual exclusion, checks cooldowns, and processes each document.
    If a document has an in-progress draft from a previous interrupted run, it
    resumes from the last completed pass rather than starting over.
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
            last_run    = state["last_run_per_doc"].get(filename, 0.0)
            draft_cursor = state["draft_cursor_per_doc"].get(filename, 0.0)
            cooldown    = getattr(cfg, "PROFILE_EVOLUTION_COOLDOWN", 86400)

            # Skip if cooldown hasn't elapsed AND no in-progress draft exists.
            # A draft means work was interrupted — always resume it regardless
            # of the cooldown, since last_run hasn't advanced yet.
            draft_exists = os.path.exists(_draft_path(filename))
            if now - last_run < cooldown and not draft_exists:
                continue

            # Collect all entries changed since the last *completed* run.
            # The draft_cursor tracks what's already incorporated in the draft,
            # so entries up to draft_cursor are silently skipped inside
            # _evolve_document() — they're already in the working document.
            changed_entries = []
            for cat in categories:
                entries = memory_db.get_entries_by_category(cat, status="live")
                for entry in entries:
                    created_at   = entry.get("created_at", 0.0) or 0.0
                    updated_at   = entry.get("updated_at", 0.0)  or 0.0
                    last_touched = max(created_at, updated_at)
                    if last_touched > last_run:
                        changed_entries.append(entry)

            min_entries = getattr(cfg, "PROFILE_EVOLUTION_MIN_ENTRIES", 5)

            # Allow resume even if the new-entry count is below the minimum:
            # the draft already captured the bulk of the work — finishing is cheap.
            if len(changed_entries) < min_entries and not draft_exists:
                print(
                    f"[PROFILE EVOLVER] {filename}: Only {len(changed_entries)} new/updated "
                    f"entries (need {min_entries}). Skipping.",
                    flush=True,
                )
                continue

            resume_msg = " (resuming from draft)" if draft_exists else ""
            print(
                f"[PROFILE EVOLVER] Evolving {filename} with {len(changed_entries)} "
                f"new/updated entries{resume_msg}...",
                flush=True,
            )
            success = await _evolve_document(filename, changed_entries, state)
            if success:
                # Only advance last_run on a successfully created proposal
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


async def _evolve_document(filename: str, new_entries: list[dict], state: dict) -> bool:
    """Propose updates to a narrative persona/profile document.

    Processes qualifying context entries in successive batches to avoid
    context-window saturation. Each pass uses the previous output as the
    working document, progressively layering evidence. Progress is saved to a
    draft file after every successful pass so an interrupted run resumes at the
    next unprocessed batch rather than restarting from scratch.

    Draft lifecycle:
      - Created / updated after each successful pass.
      - Deleted on successful proposal creation or if no changes are detected.
      - Left on disk if an error prevents proposal creation — next run resumes.

    Args:
        filename: Document basename, e.g. 'Ricky_Narrative_Profile.md'.
        new_entries: All entries changed since the last completed run
            (last_run timestamp). Entries already incorporated in a prior
            partial run are identified via draft_cursor and skipped.
        state: Mutable evolution state dict. draft_cursor_per_doc is updated
            in-place after each pass and persisted to disk.

    Returns:
        bool: True if a proposal was successfully created, False otherwise.
    """
    importlib.reload(cfg)
    persona_dir = getattr(cfg, "PERSONA_DIR", None)
    if not persona_dir:
        persona_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "persona"
        )

    fpath = os.path.join(persona_dir, filename)
    if not os.path.exists(fpath):
        print(f"[PROFILE EVOLVER] Error: document file not found at {fpath}", flush=True)
        return False

    with open(fpath, "r", encoding="utf-8") as f:
        current_content = f.read()

    # ---------------------------------------------------------------------------
    # Resume detection — load draft if a prior run was interrupted mid-pass
    # ---------------------------------------------------------------------------
    draft_file  = _draft_path(filename)
    draft_cursor = state["draft_cursor_per_doc"].get(filename, 0.0)

    if os.path.exists(draft_file) and draft_cursor > 0.0:
        with open(draft_file, "r", encoding="utf-8") as f:
            accumulated = f.read()
        print(
            f"[PROFILE EVOLVER] {filename}: Loaded draft from disk "
            f"(cursor={datetime.datetime.fromtimestamp(draft_cursor).strftime('%Y-%m-%d %H:%M')}). "
            f"Resuming from last completed pass.",
            flush=True,
        )
    else:
        accumulated = current_content
        draft_cursor = 0.0

    # ---------------------------------------------------------------------------
    # Partition entries into already-done vs. remaining
    # ---------------------------------------------------------------------------
    batch_size = getattr(cfg, "PROFILE_EVOLUTION_BATCH_SIZE", 40)

    # Sort oldest-first so later batches layer refinements on top of earlier work
    sorted_entries = sorted(
        new_entries,
        key=lambda e: max(e.get("created_at", 0) or 0, e.get("updated_at", 0) or 0),
    )

    # Entries with last_touched <= draft_cursor are already in the draft
    remaining_entries = [
        e for e in sorted_entries
        if max(e.get("created_at", 0) or 0, e.get("updated_at", 0) or 0) > draft_cursor
    ]

    if not remaining_entries:
        # All entries incorporated in a previous run — the draft IS the proposed doc.
        print(
            f"[PROFILE EVOLVER] {filename}: All entries already in draft. "
            "Proceeding directly to proposal creation.",
            flush=True,
        )
    else:
        batches = [
            remaining_entries[i:i + batch_size]
            for i in range(0, len(remaining_entries), batch_size)
        ]
        total_batches   = len(batches)
        already_done    = len(sorted_entries) - len(remaining_entries)
        global_total    = len(sorted_entries)

        # ---------------------------------------------------------------------------
        # Batched accumulation
        # ---------------------------------------------------------------------------
        for batch_idx, batch in enumerate(batches, 1):
            global_pass = already_done // batch_size + batch_idx
            global_total_passes = (global_total + batch_size - 1) // batch_size

            formatted_entries = [
                f"- [{e.get('date', 'Unknown Date')}] {e.get('observation', '')}"
                for e in batch
            ]
            evidence_block = "\n".join(formatted_entries)

            is_final_batch = batch_idx == total_batches

            if global_total_passes == 1:
                pass_note = ""
            elif is_final_batch:
                pass_note = (
                    f"\nNOTE: This is the final evidence batch "
                    f"(pass {global_pass}/{global_total_passes}). "
                    "Produce the complete, finalized document — this output will be used as the proposal."
                )
            else:
                pass_note = (
                    f"\nNOTE: This is evidence batch {global_pass} of {global_total_passes}. "
                    "Incorporate this batch into the working document. More evidence follows — "
                    "keep the document complete and coherent, but you will have further "
                    "opportunities to refine it."
                )

            prompt = (
                f"You are refining a living persona/directives document based on accumulated "
                f"evidence from recent conversations.\n\n"
                f"CURRENT DOCUMENT ({filename}):\n"
                f"---\n"
                f"{accumulated}\n"
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
                f"- Output ONLY the markdown document content, no explanation, no markdown code "
                f"blocks wrapping it.\n"
                f"- If no changes are warranted, output the document exactly as it is."
                f"{pass_note}"
            )

            messages = [
                {
                    "role": "system",
                    "content": "You are a precise document updater. Output the complete updated document content only.",
                },
                {"role": "user", "content": prompt},
            ]

            if global_total_passes > 1:
                print(
                    f"[PROFILE EVOLVER] {filename}: Evidence pass {global_pass}/{global_total_passes} "
                    f"({len(batch)} entries)...",
                    flush=True,
                )

            try:
                result = await _call_ollama(messages)
            except asyncio.CancelledError:
                # Propagate cancellation — draft already saved from prior passes
                print(
                    f"[PROFILE EVOLVER] {filename}: Cancelled at pass {global_pass}. "
                    "Draft saved; will resume next idle window.",
                    flush=True,
                )
                raise
            except Exception as e:
                print(
                    f"[PROFILE EVOLVER ERROR] {filename}: Failed on pass {global_pass}: {e}",
                    flush=True,
                )
                if batch_idx == 1 and draft_cursor == 0.0:
                    return False  # Nothing useful produced yet
                # Draft from earlier passes is already on disk — next run resumes
                return False

            if not result:
                print(
                    f"[PROFILE EVOLVER] {filename}: Empty response on pass {global_pass}. "
                    f"{'Aborting — no draft to save.' if batch_idx == 1 and draft_cursor == 0.0 else 'Draft from prior passes preserved.'}",
                    flush=True,
                )
                if batch_idx == 1 and draft_cursor == 0.0:
                    return False
                return False  # Resume next time from last saved cursor

            # Strip markdown code fences if the model wrapped the output
            if result.startswith("```"):
                lines = result.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                result = "\n".join(lines).strip()

            accumulated = result

            # Advance cursor to max last_touched of this batch's entries
            batch_max_ts = max(
                max(e.get("created_at", 0) or 0, e.get("updated_at", 0) or 0)
                for e in batch
            )
            draft_cursor = max(draft_cursor, batch_max_ts)

            # Persist draft and cursor after every successful pass
            try:
                with open(draft_file, "w", encoding="utf-8") as f:
                    f.write(accumulated)
                state["draft_cursor_per_doc"][filename] = draft_cursor
                _save_evolution_state(state)
                print(
                    f"[PROFILE EVOLVER] {filename}: Pass {global_pass} complete. "
                    f"Draft saved (cursor={datetime.datetime.fromtimestamp(draft_cursor).strftime('%Y-%m-%d %H:%M')}).",
                    flush=True,
                )
            except Exception as e:
                print(
                    f"[PROFILE EVOLVER] Warning: could not save draft after pass {global_pass}: {e}",
                    flush=True,
                )

    # ---------------------------------------------------------------------------
    # Proposal creation
    # ---------------------------------------------------------------------------
    proposed_content = accumulated

    if proposed_content == current_content.strip() or not proposed_content:
        print(f"[PROFILE EVOLVER] No changes proposed for {filename}.", flush=True)
        _clear_draft(filename, state)
        return False

    # Generate a concise reason summary
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
        merged_tags=current_content,
        reason=reason,
        source_ids=source_ids,
    )
    print(f"[PROFILE EVOLVER] Created profile_update proposal for {filename}.", flush=True)

    # Proposal created — clean up the draft so the next run starts fresh
    _clear_draft(filename, state)
    return True


def _clear_draft(filename: str, state: dict) -> None:
    """Delete the draft file and reset the cursor for a document.

    Called after a proposal is successfully created, or when the accumulated
    content is identical to the current document (no changes warranted).

    Args:
        filename: Document basename.
        state: Mutable evolution state dict to update in-place.
    """
    draft_file = _draft_path(filename)
    if os.path.exists(draft_file):
        try:
            os.remove(draft_file)
        except Exception as e:
            print(f"[PROFILE EVOLVER] Warning: could not delete draft file: {e}", flush=True)
    state["draft_cursor_per_doc"][filename] = 0.0
    _save_evolution_state(state)
