# profile_evolver.py
# date created: 2026-06-27 08:45:00
# date modified: 2026-08-08 07:03:32
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
import sqlite3
import time

import httpx
import memory_db

import evelyn_config as cfg


def _sync_read_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _sync_write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

# ---------------------------------------------------------------------------
# Category-to-document mapping
# ---------------------------------------------------------------------------
DOCUMENT_CATEGORIES = {
    cfg.PERSONA_FILE_ASSISTANT: [
        f"Cat01-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat02-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat03-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat04-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat10-{cfg.SUBJECT_CODE_ASSISTANT}",
    ],
    cfg.PERSONA_FILE_USER: [
        f"Cat01-{cfg.SUBJECT_CODE_USER}",
        f"Cat03-{cfg.SUBJECT_CODE_USER}",
        f"Cat04-{cfg.SUBJECT_CODE_USER}",
        f"Cat06-{cfg.SUBJECT_CODE_USER}",
        f"Cat09-{cfg.SUBJECT_CODE_USER}",
        f"Cat12-{cfg.SUBJECT_CODE_USER}",
    ],
    cfg.PERSONA_FILE_DIRECTIVES: [
        f"Cat14-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat16-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat16-{cfg.SUBJECT_CODE_USER}",
    ],
}

# ---------------------------------------------------------------------------
# Grammatical perspective rules per document to prevent viewpoint drift
# ---------------------------------------------------------------------------
DOCUMENT_RULES = {
    cfg.PERSONA_FILE_ASSISTANT: {
        "description": f"{cfg.ASSISTANT_NAME}'s own identity, narrative, archetype, and values.",
        "perspective": f"First-person singular (using 'I', 'me', 'my', 'myself'). Never refer to {cfg.ASSISTANT_NAME} as 'she', 'her', or '{cfg.ASSISTANT_NAME}' in this document.",
        "guidelines": (
            f"- Write about {cfg.ASSISTANT_NAME} in the first person.\n"
            f"- Write about {cfg.USER_NAME} or others in the third person. Do NOT convert facts about {cfg.USER_NAME} into 'I' statements.\n"
            f"- Example 1 ({cfg.ASSISTANT_NAME} fact): '{cfg.ASSISTANT_NAME} prefers quiet mornings' -> 'I value quiet mornings.'\n"
            f"- Example 2 ({cfg.USER_NAME}/Relationship fact): '{cfg.USER_NAME} prefers small gifts' -> 'I know {cfg.USER_NAME} prefers small gifts.' or '{cfg.USER_NAME} prefers small gifts.' (Do NOT write 'I prefer small gifts')"
        ),
    },
    cfg.PERSONA_FILE_USER: {
        "description": f"{cfg.USER_NAME}'s preferences, history, and traits.",
        "perspective": f"Third-person singular (using '{cfg.USER_NAME}', 'he', 'him', 'his'). Never refer to {cfg.USER_NAME} in the first person ('I', 'me', 'my').",
        "guidelines": (
            f"- Write about {cfg.USER_NAME} in the third person.\n"
            f"- Write about {cfg.ASSISTANT_NAME} in the third person (using '{cfg.ASSISTANT_NAME}', 'she', 'her').\n"
            f"- Never use 'I', 'me', 'my', or 'you' in this document.\n"
            f"- Example 1 ({cfg.USER_NAME} fact): '{cfg.USER_NAME} likes small gifts' -> 'He prefers small gifts.'\n"
            f"- Example 2 (Relationship/{cfg.ASSISTANT_NAME} fact): '{cfg.ASSISTANT_NAME} values my feedback' -> '{cfg.ASSISTANT_NAME} values his feedback.' (Translate 'my' to 'his')"
        ),
    },
    cfg.PERSONA_FILE_DIRECTIVES: {
        "description": "Behavioral constraints, routines, and instructions for the AI.",
        "perspective": "Second-person (using 'You', 'your', 'yours') addressing the AI.",
        "guidelines": (
            "- Direct the AI's behavior in the second person.\n"
            f"- Refer to {cfg.USER_NAME} in the third person.\n"
            f"- Example 1 (AI instruction): '{cfg.ASSISTANT_NAME} should respond casually' -> 'You respond in natural conversational form.'\n"
            f"- Example 2 ({cfg.USER_NAME}'s habit): '{cfg.USER_NAME} Sunday routine is laundry' -> 'You recognize {cfg.USER_NAME}\\'s Sunday routine of laundry.'\n"
            "- Must always preserve the anti-drafting constraint: AI must never draft, outline, or rehearse responses inside <think> tags."
        ),
    },
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


def split_frontmatter(content: str) -> tuple[str, str]:
    """Split YAML frontmatter from the markdown body.

    Returns:
        tuple[str, str]: (frontmatter, body). frontmatter includes the '---' bounds.
                         If no frontmatter is found, returns ("", content).
    """
    content_stripped = content.strip()
    if content_stripped.startswith("---"):
        parts = content_stripped.split("---", 2)
        if len(parts) >= 3:
            frontmatter = "---" + parts[1] + "---"
            body = parts[2].strip()
            return frontmatter, body
    return "", content


def update_frontmatter_modified_date(frontmatter: str, new_date: str) -> str:
    """Replace the date modified value in the YAML frontmatter string.

    Args:
        frontmatter: The YAML frontmatter block.
        new_date: String representation of the date.

    Returns:
        str: Updated frontmatter string.
    """
    if not frontmatter:
        return ""
    pattern = r"^(date modified:\s*).*$"
    updated, count = re.subn(pattern, f"\\g<1>{new_date}", frontmatter, flags=re.MULTILINE)
    if count == 0:
        lines = frontmatter.strip().splitlines()
        if len(lines) >= 2 and lines[-1] == "---":
            lines.insert(-1, f"date modified: {new_date}")
            updated = "\n".join(lines)
    return updated


def extract_markdown_content(text: str) -> str:
    """Robustly extract the core markdown content from an LLM response.

    If the response is wrapped in code fences, extracts the content inside.
    Otherwise, returns the text with leading/trailing whitespace cleaned.

    Args:
        text: Raw response string from the model.

    Returns:
        str: Cleaned markdown content.
    """
    text = text.strip()
    match = re.search(r"```(?:markdown|md|yaml)?\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def normalize_document_text(text: str) -> str:
    """Normalize and clean common LLM formatting errors and typos.

    Args:
        text: The raw markdown text to clean.

    Returns:
        str: The normalized and cleaned text.
    """
    # 1. Fix header spacing typos like '##_Directives' or '###_Section'
    text = re.sub(r"^(#+)_([A-Za-z0-9])", r"\1 \2", text, flags=re.MULTILINE)

    # 2. Fix the specific 'human-10AI' category leak typo
    text = re.sub(r"\bhuman-\d*AI\b", "human-AI", text)
    text = re.sub(r"\bhuman-\d*[-]?AI\b", "human-AI", text)

    # 3. Fix typical quote mangling typos like "Nourishmen"t -> "Nourishment"
    text = text.replace('"Nourishmen"t', '"Nourishment"')
    text = text.replace('"Nourishment"t', '"Nourishment"')
    text = text.replace('Nourishmen"t', '"Nourishment"')

    # 4. Strip trailing whitespace from lines
    lines = [line.rstrip() for line in text.splitlines()]

    return "\n".join(lines).strip()


STATUS_LABELS = {
    "APPROVED": "Profile Updated & Applied",
    "PROPOSAL_STAGED": "Proposal Pending Approval",
    "NO_CORE_CHANGES": "Evaluated — Up to Date",
    "BELOW_THRESHOLD": "Skipped — Below Threshold",
    "COOLDOWN_ACTIVE": "Skipped — Cooldown Active",
    "PENDING_EXISTS": "Skipped — Proposal Pending",
    "INTERRUPTED_SAVED": "Interrupted — Draft Saved",
    "MODEL_ERROR": "Error — Generation Failed",
}


def _load_evolution_state() -> dict:
    """Load evolution state from disk.

    State contains:
      - last_run_per_doc: Unix timestamp of the last *completed* evolution run
        per document. Only advances when a proposal is successfully created.
      - draft_cursor_per_doc: Max last_touched timestamp of entries already
        incorporated into the current in-progress draft. Used to resume
        interrupted multi-pass runs without reprocessing completed batches.
      - last_status_per_doc: Per-document status dictionary containing code,
        label, timestamp, and detail message.

    Returns:
        dict: State dict with guaranteed keys for all tracked documents.
    """
    doc_keys = list(DOCUMENT_CATEGORIES.keys())
    default_state = {
        "last_run_per_doc":     dict.fromkeys(doc_keys, 0.0),
        "draft_cursor_per_doc": dict.fromkeys(doc_keys, 0.0),
        "last_status_per_doc":  {},
    }
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "last_run_per_doc" in data:
                # Merge — guarantee all keys exist for sub-dicts
                for k in doc_keys:
                    if k not in data["last_run_per_doc"]:
                        data["last_run_per_doc"][k] = 0.0
                if "draft_cursor_per_doc" not in data:
                    data["draft_cursor_per_doc"] = dict.fromkeys(doc_keys, 0.0)
                else:
                    for k in doc_keys:
                        if k not in data["draft_cursor_per_doc"]:
                            data["draft_cursor_per_doc"][k] = 0.0
                if "last_status_per_doc" not in data:
                    data["last_status_per_doc"] = {}
                return data
    except (OSError, json.JSONDecodeError, ValueError) as e:
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
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"[PROFILE EVOLVER] Warning: could not save state file: {e}", flush=True)


def update_doc_status(state: dict, filename: str, code: str, details: str = "") -> None:
    """Record a structured status outcome for a target document in evolution state.

    Args:
        state: Evolution state dictionary.
        filename: Document basename.
        code: Status code key from STATUS_LABELS.
        details: Optional detail context (e.g. entry counts, reason).
    """
    if "last_status_per_doc" not in state:
        state["last_status_per_doc"] = {}
    label = STATUS_LABELS.get(code, code)
    state["last_status_per_doc"][filename] = {
        "code": code,
        "label": label,
        "timestamp": time.time(),
        "details": details,
    }
    _save_evolution_state(state)


def get_profile_evolution_statuses() -> dict:
    """Retrieve current per-document status records for API exposure.

    Returns:
        dict: Mapping of filename to status dictionary.
    """
    state = _load_evolution_state()
    statuses = state.get("last_status_per_doc", {})
    # Guarantee entries for all categories
    for doc in DOCUMENT_CATEGORIES:
        if doc not in statuses:
            last_run = state.get("last_run_per_doc", {}).get(doc, 0.0)
            statuses[doc] = {
                "code": "NEVER_RUN" if not last_run else "COOLDOWN_ACTIVE",
                "label": "Never Run" if not last_run else "Skipped — Cooldown Active",
                "timestamp": last_run,
                "details": "No status recorded yet" if not last_run else "Cooldown active",
            }
    return statuses


def advance_doc_run_timestamp(filename: str) -> None:
    """Advance last_run_per_doc for a document to the current time.

    Called when a profile_update proposal is approved by the user. Resets the
    per-document cooldown clock from the approval timestamp rather than from the
    original proposal generation time and updates the document status to APPROVED.

    Args:
        filename: Document basename, e.g. 'Ricky_Narrative_Profile.md'.
    """
    state = _load_evolution_state()
    state["last_run_per_doc"][filename] = time.time()
    update_doc_status(state, filename, "APPROVED", "Proposal approved & applied to profile note")

# ---------------------------------------------------------------------------
# Infrastructure & Mutual Exclusion
# ---------------------------------------------------------------------------

def _other_heavy_tasks_running() -> bool:
    """Check if any other heavy background task is currently active.

    Delegates to task_manager.is_any_running() — the single canonical
    source of truth for mutual exclusion across all heavy tasks.

    Returns:
        bool: True if another heavy task is active, False otherwise.
    """
    import task_manager
    return task_manager.is_any_running(exclude="profile_evolver")

def _set_status_in_server(status: str | None, error: str | None = None) -> None:
    """Register or clear status in the server's background task registry.

    Delegates to task_manager.set_running() / task_manager.clear_running().

    Args:
        status: Status string (e.g. 'running'), or None/status string on completion.
        error: Optional error message string.
    """
    import task_manager
    if status == "running":
        task_manager.set_running("profile_evolver")
    else:
        task_manager.clear_running("profile_evolver", status=status or "idle", error=error)

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
        _set_status_in_server("cancelled")
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
    _evolver_task = asyncio.current_task()
    _set_status_in_server("running")

    try:
        state = _load_evolution_state()
        now = time.time()

        # Check for pending profile updates
        pending_props = memory_db.get_pending_proposals("profile_update")
        pending_files = {p["suggested_category"] for p in pending_props}

        for filename, categories in DOCUMENT_CATEGORIES.items():
            if filename in pending_files:
                print(
                    f"[PROFILE EVOLVER] {filename} has a pending profile update. Skipping.",
                    flush=True,
                )
                update_doc_status(state, filename, "PENDING_EXISTS", "Pending proposal awaiting review")
                continue

            last_run    = state["last_run_per_doc"].get(filename, 0.0)
            state["draft_cursor_per_doc"].get(filename, 0.0)
            cooldown    = getattr(cfg, "PROFILE_EVOLUTION_COOLDOWN", 86400)

            # Skip if cooldown hasn't elapsed AND no in-progress draft exists.
            # A draft means work was interrupted — always resume it regardless
            # of the cooldown, since last_run hasn't advanced yet.
            draft_exists = os.path.exists(_draft_path(filename))
            if now - last_run < cooldown and not draft_exists:
                rem_h = round((cooldown - (now - last_run)) / 3600.0, 1)
                update_doc_status(state, filename, "COOLDOWN_ACTIVE", f"Cooldown active ({rem_h}h remaining)")
                continue

            # Collect all entries changed since the last *completed* run.
            # The draft_cursor tracks what's already incorporated in the draft,
            # so entries up to draft_cursor are silently skipped inside
            # _evolve_document() — they're already in the working document.
            changed_entries = []
            for cat in categories:
                entries = memory_db.get_entries_by_category(cat, status="live")
                for entry in entries:
                    entry.get("created_at", 0.0) or 0.0
                    updated_at      = entry.get("updated_at", 0.0)  or 0.0
                    last_evolved_at = entry.get("last_evolved_at")

                    # Qualifies if it has never been evolved OR if observation content was updated after evolution
                    if last_evolved_at is None or updated_at > last_evolved_at:
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
                update_doc_status(state, filename, "BELOW_THRESHOLD", f"{len(changed_entries)}/{min_entries} qualifying entries")
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

        import task_manager
        task_manager.save_last_run_ts("profile_evolver")
        _set_status_in_server("idle")

    except asyncio.CancelledError:
        print("[PROFILE EVOLVER] Execution cancelled.", flush=True)
        _set_status_in_server("cancelled")
    except (sqlite3.Error, OSError, RuntimeError, ValueError, KeyError, httpx.HTTPError) as e:
        print(f"[PROFILE EVOLVER ERROR] Exception: {e}", flush=True)
        _set_status_in_server("error", error=f"{type(e).__name__}: {e}")
    finally:
        _evolving = False
        _evolver_task = None

# ---------------------------------------------------------------------------
# Evolution core
# ---------------------------------------------------------------------------

async def _call_ollama(messages: list[dict], num_predict: int = -1) -> str:
    """Async helper to call Ollama.

    Args:
        messages: List of message dictionaries.
        num_predict: Maximum prediction tokens (-1 for unlimited).

    Returns:
        str: Response content from the model.
    """
    importlib.reload(cfg)
    override = getattr(cfg, "PROFILE_EVOLUTION_MODEL_OVERRIDE", "default")
    model = cfg.MODEL_NAME if override == "default" else override

    options = {
        "num_ctx": cfg.NUM_CTX,
        "num_predict": num_predict,
        **{
            key: val
            for key, val in {
                "temperature": cfg.TEMPERATURE,
                "min_p": cfg.MIN_P,
                "top_k": cfg.TOP_K,
                "top_p": cfg.TOP_P,
                "repeat_penalty": cfg.REPEAT_PENALTY,
                "repeat_last_n": cfg.REPEAT_LAST_N,
                "seed": cfg.SEED,
            }.items()
            if val is not None
        },
    }

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": options,
        "think": True,  # Proposing evolution requires thinking / reasoning
    }

    content_buffer = ""
    timeout = 180  # Generous timeout for reasoning and response generation

    async with (
        httpx.AsyncClient(timeout=timeout) as client,
        client.stream("POST", f"{cfg.OLLAMA_URL}/api/chat", json=payload) as resp,
    ):
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
    rules = DOCUMENT_RULES.get(filename, {})
    description = rules.get("description", "document body")
    perspective = rules.get("perspective", "appropriate perspective")
    guidelines = rules.get("guidelines", "")

    # Load target word limit
    limits = getattr(cfg, "PROFILE_EVOLUTION_LIMITS", {})
    target_limit = limits.get(filename, 600)

    persona_dir = getattr(cfg, "PERSONA_DIR", None)
    if not persona_dir:
        persona_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "persona"
        )

    # Load other documents for Cross-Document Reviewer context (redundancy check)
    other_docs_context = []
    for other_name in DOCUMENT_CATEGORIES:
        if other_name == filename:
            continue
        # Load draft if exists, else live document
        other_path = _draft_path(other_name)
        if not os.path.exists(other_path):
            other_path = os.path.join(persona_dir, other_name)
        if os.path.exists(other_path):
            try:
                other_content = await asyncio.to_thread(_sync_read_file, other_path)
                _, other_body = split_frontmatter(other_content)
                other_docs_context.append(f"DOCUMENT: {other_name}\nCONTENT:\n{other_body.strip()}")
            except OSError as e_other:
                print(f"[PROFILE EVOLVER] Warning: could not load other doc {other_name}: {e_other}", flush=True)
    other_docs_str = "\n\n".join(other_docs_context) if other_docs_context else "None"

    fpath = os.path.join(persona_dir, filename)
    if not os.path.exists(fpath):
        print(f"[PROFILE EVOLVER] Error: document file not found at {fpath}", flush=True)
        return False

    current_content = await asyncio.to_thread(_sync_read_file, fpath)

    # Extract original frontmatter and markdown body
    frontmatter, current_body = split_frontmatter(current_content)

    # ---------------------------------------------------------------------------
    # Resume detection — load draft if a prior run was interrupted mid-pass
    # ---------------------------------------------------------------------------
    draft_file  = _draft_path(filename)
    draft_cursor = state["draft_cursor_per_doc"].get(filename, 0.0)

    if os.path.exists(draft_file) and draft_cursor > 0.0:
        accumulated = await asyncio.to_thread(_sync_read_file, draft_file)
        # Ensure we are using the body content only
        _, accumulated_body = split_frontmatter(accumulated)
        accumulated = accumulated_body
        print(
            f"[PROFILE EVOLVER] {filename}: Loaded draft from disk "
            f"(cursor={datetime.datetime.fromtimestamp(draft_cursor, tz=datetime.UTC).astimezone().strftime('%Y-%m-%d %H:%M')}). "
            f"Resuming from last completed pass.",
            flush=True,
        )
    else:
        accumulated = current_body
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
                    "Produce the complete, finalized document body — this output will be used as the proposal."
                )
            else:
                pass_note = (
                    f"\nNOTE: This is evidence batch {global_pass} of {global_total_passes}. "
                    "Incorporate this batch into the working document body. More evidence follows — "
                    "keep the body complete and coherent, but you will have further "
                    "opportunities to refine it."
                )

            prompt = (
                f"You are refining the content body of a living persona/directives document based on "
                f"accumulated evidence from recent conversations.\n\n"
                f"DOCUMENT: {filename}\n"
                f"DESCRIPTION: {description}\n"
                f"TARGET PERSPECTIVE: {perspective}\n\n"
                f"PERSPECTIVE RULES:\n"
                f"{guidelines}\n\n"
                f"OTHER ACTIVE SYSTEM PROMPT DOCUMENTS (Do NOT duplicate any information or topics covered here):\n"
                f"--- \n"
                f"{other_docs_str}\n"
                f"--- \n\n"
                f"CURRENT DOCUMENT BODY:\n"
                f"---\n"
                f"{accumulated}\n"
                f"---\n\n"
                f"ACCUMULATED EVIDENCE (recent memory updates):\n"
                f"{evidence_block}\n\n"
                f"INSTRUCTIONS:\n"
                f"- Evolve the document body authentically based on the accumulated evidence.\n"
                f"- Apply the PERSPECTIVE RULES strictly. Ensure evidence is translated to the correct perspective and attribute facts to the correct subject.\n"
                f"- PRIORITIZE BEHAVIORAL DIRECTIVES & CORE TRAITS: Focus on personality traits, psychological/health conditions (e.g., anxiety, core identity), governing ethics, voice/cadence guidelines, relationship rules/boundaries, routines, and interaction preferences.\n"
                f"- IMPORTANCE HIERARCHY: Core behavioral directives, psychological/health traits, and governing ethics are high priority. Casual preferences (e.g. food/snack likes, minor item interests) must NEVER displace or replace core traits or directives.\n"
                f"- EXCLUDE EPISODIC/FACTUAL MEMORIES: Do not add or retain specific historical events, physical locations, dates, or lists of minor personal facts. These belong in episodic RAG memory, not this prompt file. Remove any such facts from the document if they are not behavioral guides.\n"
                f"- PREVENT REDUNDANCY: Do not repeat any details that are already documented in the OTHER ACTIVE SYSTEM PROMPT DOCUMENTS shown above.\n"
                f"- TARGET WORD COUNT: Ensure the updated document is concise and stays under {target_limit} words.\n"
                f"- If a section does not have any new evidence or modifications, preserve it exactly as it is "
                f"in the CURRENT DOCUMENT BODY, but prune any parts that violate the word count budget, represent redundant facts, or are covered in other files.\n"
                f"- Do NOT use placeholders like '[Content remains unchanged]' or '[...]'. You must output "
                f"the complete content of the document in full.\n"
                f"- Do NOT add speculative or single-source observations.\n"
                f"- Do NOT include any YAML frontmatter or title blocks. Start directly with the first markdown header.\n"
                f"- Output ONLY the markdown document content, no explanation, no markdown code blocks wrapping it.\n"
                f"- If no changes are warranted, output the document body exactly as it is."
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
            except (httpx.HTTPError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
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

            # Robustly extract markdown from response
            result = extract_markdown_content(result)

            # Normalize and clean text to remove LLM quirks/typos
            result = normalize_document_text(result)

            # Safeguard validation checks to prevent catastrophic data loss or laziness
            if len(result) < len(accumulated) * 0.3:
                print(
                    f"[PROFILE EVOLVER ERROR] {filename}: LLM response is suspiciously short on pass {global_pass} "
                    f"({len(result)} chars vs original {len(accumulated)} chars). "
                    f"Discarding to prevent data deletion.",
                    flush=True,
                )
                return False

            placeholders = ["remains unchanged", "remains the same", "same as original", "no changes"]
            if any(p in result.lower() for p in placeholders) and len(result) < len(accumulated) * 0.9:
                print(
                    f"[PROFILE EVOLVER ERROR] {filename}: LLM response contains lazy placeholders on pass {global_pass}. "
                    f"Discarding to prevent data loss.",
                    flush=True,
                )
                return False

            accumulated = result

            # Advance cursor to max last_touched of this batch's entries
            batch_max_ts = max(
                max(e.get("created_at", 0) or 0, e.get("updated_at", 0) or 0)
                for e in batch
            )
            draft_cursor = max(draft_cursor, batch_max_ts)

            # Persist draft and cursor after every successful pass
            try:
                await asyncio.to_thread(_sync_write_file, draft_file, accumulated)
                state["draft_cursor_per_doc"][filename] = draft_cursor
                _save_evolution_state(state)
                print(
                    f"[PROFILE EVOLVER] {filename}: Pass {global_pass} complete. "
                    f"Draft saved (cursor={datetime.datetime.fromtimestamp(draft_cursor, tz=datetime.UTC).astimezone().strftime('%Y-%m-%d %H:%M')}).",
                    flush=True,
                )
            except OSError as e:
                print(
                    f"[PROFILE EVOLVER] Warning: could not save draft after pass {global_pass}: {e}",
                    flush=True,
                )

    # ---------------------------------------------------------------------------
    # Word Count Validation & Compaction Pass (Reviewer Stage)
    # ---------------------------------------------------------------------------
    proposed_body = accumulated
    word_count = len(proposed_body.split())
    word_buffer = int(target_limit * 1.05)  # 5% buffer

    if word_count > word_buffer:
        print(
            f"[PROFILE EVOLVER] {filename}: Proposed body is {word_count} words (limit {target_limit}). "
            f"Invoking Compaction Pass...",
            flush=True,
        )
        compaction_prompt = (
            f"You are a strict editor refining a persona/directives document for an AI. "
            f"The document is currently {word_count} words, which exceeds the limit of {target_limit} words.\n\n"
            f"DOCUMENT: {filename}\n"
            f"TARGET PERSPECTIVE: {perspective}\n\n"
            f"PERSPECTIVE RULES:\n"
            f"{guidelines}\n\n"
            f"OTHER ACTIVE SYSTEM PROMPT DOCUMENTS (Do NOT duplicate any information here):\n"
            f"--- \n"
            f"{other_docs_str}\n"
            f"--- \n\n"
            f"OVER-LENGTH DOCUMENT BODY:\n"
            f"---\n"
            f"{proposed_body}\n"
            f"---\n\n"
            f"INSTRUCTIONS:\n"
            f"- Condense and prune the document body so it is strictly under {target_limit} words.\n"
            f"- Focus 100% on high-level behavioral directives, tone guidelines, communication rules, and operational routines.\n"
            f"- Completely remove specific episodic/factual memories, historical anecdotes, dates, physical locations, or lists of symptoms (e.g. Navy details, family relocation events). These are handled by RAG and are redundant here.\n"
            f"- Ensure there is zero duplicate info with the OTHER ACTIVE SYSTEM PROMPT DOCUMENTS listed above.\n"
            f"- Maintain the correct TARGET PERSPECTIVE and PERSPECTIVE RULES strictly.\n"
            f"- Do NOT use placeholders or summary statements. Output the entire document in full.\n"
            f"- Output ONLY the markdown document content, no explanation, no markdown code blocks wrapping it."
        )

        compaction_messages = [
            {
                "role": "system",
                "content": "You are a precise editor. Output the fully pruned and complete markdown document body under the word limit.",
            },
            {"role": "user", "content": compaction_prompt},
        ]

        try:
            compacted_result = await _call_ollama(compaction_messages)
            if compacted_result:
                compacted_result = extract_markdown_content(compacted_result)
                compacted_result = normalize_document_text(compacted_result)

                compacted_word_count = len(compacted_result.split())
                if compacted_word_count < word_count:
                    proposed_body = compacted_result
                    print(
                        f"[PROFILE EVOLVER] {filename}: Compaction pass successful. "
                        f"Reduced from {word_count} to {compacted_word_count} words.",
                        flush=True,
                    )
                else:
                    print(
                        f"[PROFILE EVOLVER WARNING] {filename}: Compaction pass did not reduce word count "
                        f"({compacted_word_count} vs {word_count}). Keeping original.",
                        flush=True,
                    )
        except (httpx.HTTPError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
            print(f"[PROFILE EVOLVER ERROR] {filename}: Compaction pass failed: {e}", flush=True)

    # ---------------------------------------------------------------------------
    # Proposal creation
    # ---------------------------------------------------------------------------
    proposed_body = proposed_body

    if proposed_body == current_body.strip() or not proposed_body:
        print(f"[PROFILE EVOLVER] No changes proposed for {filename}.", flush=True)
        _clear_draft(filename, state)
        update_doc_status(state, filename, "NO_CORE_CHANGES", f"{len(new_entries)} entries evaluated; no core changes")
        return False

    # Generate a concise reason summary
    reason_prompt = (
        f"Compare the current document body and the proposed update body. Summarize what changed and why, "
        f"citing the context entries that supported this evolution.\n\n"
        f"CURRENT BODY:\n{current_body}\n\n"
        f"PROPOSED BODY:\n{proposed_body}\n\n"
        f"Output a brief, one-to-two sentence explanation only."
    )
    reason_messages = [
        {"role": "system", "content": "You are a helpful summarizing assistant. Output one or two sentences only."},
        {"role": "user", "content": reason_prompt},
    ]
    reason = await _call_ollama(reason_messages, num_predict=150)
    if not reason:
        reason = "Evolving profile based on recent context entries."

    # Update modified date in original YAML frontmatter block
    current_time_str = datetime.datetime.now(datetime.UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    updated_frontmatter = update_frontmatter_modified_date(frontmatter, current_time_str)

    # Reconstruct complete proposed document
    proposed_content = updated_frontmatter + "\n\n" + proposed_body if updated_frontmatter else proposed_body

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
    # NOTE: touch_entry_evolved() is intentionally NOT called here.
    # last_evolved_at must only be stamped when the proposal is *approved*,
    # not when it is created. If the user rejects the proposal, entries must
    # remain eligible for re-evaluation. See evelyn_server.py profile_update handler.
    print(f"[PROFILE EVOLVER] Created profile_update proposal for {filename}.", flush=True)
    update_doc_status(state, filename, "PROPOSAL_STAGED", f"Proposal staged ({len(new_entries)} entries)")

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
        except OSError as e:
            print(f"[PROFILE EVOLVER] Warning: could not delete draft file: {e}", flush=True)
    state["draft_cursor_per_doc"][filename] = 0.0
    _save_evolution_state(state)
