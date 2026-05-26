# fact_consolidator.py
# date created: 2026-05-03 18:07:33
# date modified: 2026-05-24 15:55:13
# tags: facts, consolidation, duplicates, deduplication, entities

"""
fact_consolidator.py — Idle-time context entry consolidation for Evelyn's memory system.

Runs during server idle time to scan live context entries for duplicates, contradictions,
and miscategorized facts. Uses the loaded LLM with thinking tokens for nuanced semantic
reasoning. Produces human-readable proposal files in the Pending folder — nothing is
auto-applied to the live vault.

Architecture (top-to-bottom execution order):
  SECTION 0 — Infrastructure
    _extracting_elsewhere()             — Mutual-exclusion check against fact_extractor
    _heavy_tasks_running()              — Defer if vault-map / sync tasks are active
    _load_scan_state()                  — Restore per-category anchor pointers from disk
    _save_scan_state()                  — Persist anchor pointers after each pass
    _call_ollama()                      — Shared non-streaming Ollama call primitive

  SECTION 1 — Public API
    cancel_pending_consolidation()      — Called on new chat request to free Ollama
    run_consolidation()                 — Top-level coroutine for idle-time scheduling

  SECTION 2 — Step 1: Scan
    scan_context_entries()              — Walk all live Cat##/Cat##-{E,R}/*.md files
    _parse_entry_file()                 — Parse one file into a FactRecord dict

  SECTION 3 — Step 2: Detection (two sequential focused calls per category)
    Step 2a — Consolidation detection (duplicates/overlaps):
      _CONSOL_DETECT_SYSTEM_PROMPT      — ⬅ Edit model persona for consol detection
      _CONSOL_DETECT_PROMPT             — ⬅ Edit duplicate detection rules here
      _build_consol_prompt()            — Format prompt for one anchor batch
      _detect_consol_in_group()         — Run one LLM call (think=False)
      _parse_consol_yaml()              — Decode YAML → Cluster dicts

    Step 2b — Recategorization detection (category audit):
      _RECAT_DETECT_SYSTEM_PROMPT       — ⬅ Edit model persona for recat detection
      _RECAT_DETECT_PROMPT              — ⬅ Edit category audit rules here
      _build_recat_prompt()             — Format prompt for one entry batch
      _detect_recat_in_group()          — Run one LLM call (think=False)
      _parse_recat_yaml()               — Decode YAML → recat item dicts

    _detect_in_group()                  — Orchestrate both calls per category
    find_consolidation_candidates()     — Rotate through categories with cost controls

  SECTION 4 — Step 3: Consolidation Proposals
    _PROPOSAL_SYSTEM_PROMPT             — ⬅ Edit model persona for proposals here
    _PROPOSAL_PROMPT                    — ⬅ Edit merge/supersede verdict rules here
    generate_consolidation_proposal()   — LLM-driven merge verdict (think=True)
    _parse_proposal_yaml()              — Decode proposal YAML → verdict dict
    _write_proposal()                   — Write CONSOLIDATION_*.md to Pending/

  SECTION 5 — Step 3b: Recategorization Proposals
    _write_recategorization_proposal()  — Write RECATEGORIZE_*.md (no LLM call)

  SECTION 6 — Step 4: Orchestration
    _build_pending_index()              — Index existing proposals to prevent re-runs
    _do_consolidation()                 — Core pipeline; called by run_consolidation()

Output (written to SQLite memory DB):
  CONSOLIDATION proposals   — Merge/supersede proposal for 2+ overlapping entries.
  RECATEGORIZE proposals    — Single-entry category move proposal. No LLM call needed.

Key behaviors:
  - CONSOLIDATION_KEEP_HISTORY (True)  — Preserve fact evolution in merged summaries
  - CONSOLIDATION_KEEP_HISTORY (False) — Overwrite with the most recent fact only
  - Detection uses think=False          — Fast classification; YAML schema only
  - Proposal uses think=True            — Careful reasoning before merge verdict
  - Cancellation                        — A new chat request immediately cancels any
    in-flight consolidation pass so Ollama is freed for the user's message.

All config is read from evelyn_config.py (single source of truth).
"""

import asyncio
import datetime
import importlib
import json
import os
import re
import time
from pathlib import Path

import httpx
import yaml

import evelyn_config as cfg # [[evelyn_config.py]]

# Import full module so we can read fact_extractor._extracting for mutual exclusion.
import fact_extractor # [[fact_extractor.py]]
from fact_extractor import load_cat00_index


# ---------------------------------------------------------------------------
# Typed data structures (plain dicts — no dataclass overhead)
# ---------------------------------------------------------------------------
# FactRecord = {
#     "path": str,          absolute path to the file
#     "rel_path": str,      path relative to CONTEXT_ENTRIES_DIR
#     "category": str,      "Cat05-R"
#     "cat_num": int,       5
#     "subject": str,       "R" | "E"
#     "date": datetime,     parsed from filename or frontmatter
#     "summary": str,       text of **Summary:** line
#     "filename": str,      basename only
# }
#
# Cluster = {
#     "category": str,      "Cat05-R"
#     "topic": str,         LLM-identified topic label e.g. "coffee preferences"
#     "records": list[FactRecord]
# }


# ---------------------------------------------------------------------------
# Compiled patterns
# Module-level for efficiency; referenced across Sections 2, 3, and 5.
# ---------------------------------------------------------------------------

_SUMMARY_RE = re.compile(r"\*\*Summary:\*\*\s*(.+)", re.IGNORECASE | re.DOTALL)
_DATE_FROM_FILENAME_RE = re.compile(r"(?:CE|EX)_(\d{4}-\d{2}-\d{2})")
_CAT_CODE_RE = re.compile(r"(Cat\d{2}-[ER])", re.IGNORECASE)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_consolidating = False
_last_run_ts: float = 0.0

# Rotating index into the category group list for round-robin category selection.
# Each run starts from this category so all categories are visited over time.
_group_start_index: int = 0

# Per-category anchor-based scan state.
# Maps category_code → {"anchor": int, "offset": int, "n": int}
#   anchor: index of current anchor entry in oldest-first sorted records
#   offset: index into comparison pool (all entries except the anchor)
#   n:      record count when state was saved (reset trigger if N changes)
# Loaded from disk on startup; saved after each _do_consolidation pass.
_category_scan_state: dict[str, dict] = {}

# State file lives next to the chat DB for colocation with other state files.
_SCAN_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(cfg.CHAT_DB_PATH)),
    "evelyn_consolidation_offsets.json",
)

# Reference to the in-flight consolidation asyncio.Task so it can be cancelled
# when a new chat request arrives (same pattern as context_summarizer._summary_task).
_consolidation_task = None


# ============================================================================
# SECTION 0 — INFRASTRUCTURE
# Primitives that every other section depends on. No pipeline logic lives here.
# ============================================================================


def _extracting_elsewhere() -> bool:
    """Return True if fact_extractor is currently running an LLM call."""
    try:
        return bool(fact_extractor._extracting)
    except AttributeError:
        return False


def _heavy_tasks_running() -> bool:
    """Return True if any heavy server background task is running.

    Checks the _background_tasks dict in evelyn_server.py. Any task with
    status="running" (e.g. "vault_map", "sync", or future tasks) will
    cause this to return True, preventing Ollama overload.
    """
    import sys
    server = sys.modules.get("evelyn_server")
    if server:
        tasks = getattr(server, "_background_tasks", {})
        for task in tasks.values():
            if task.get("status") == "running":
                return True
    return False





def _load_scan_state() -> None:
    """Load per-category anchor scan state from disk into _category_scan_state.

    Called once at module import so progress survives server restarts.
    Falls back to an empty dict (all categories start fresh) on first run
    or if the file is unreadable.
    """
    global _category_scan_state
    try:
        with open(_SCAN_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _category_scan_state = data
            print(
                f"[CONSOLIDATOR] Loaded anchor scan state for {len(data)} category/ies.",
                flush=True,
            )
    except FileNotFoundError:
        _category_scan_state = {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"[CONSOLIDATOR] Warning: could not load scan state: {e}", flush=True)
        _category_scan_state = {}


def _save_scan_state() -> None:
    """Persist _category_scan_state to disk after each consolidation pass."""
    try:
        with open(_SCAN_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(_category_scan_state, f, indent=2)
    except OSError as e:
        print(f"[CONSOLIDATOR] Warning: could not save scan state: {e}", flush=True)


async def _call_ollama(
    messages: list[dict],
    timeout: int = 60,
    think: bool = True,
    num_predict: int = 1000,
) -> str:
    """Generic non-streaming Ollama call for consolidator tasks, returns content string.

    Matches main model config to avoid VRAM eviction. The thinking trace (when
    enabled) is consumed server-side and not written to any file.

    Call sites should choose their own think/num_predict based on task type:
      - Detection (classification):  think=False, num_predict=1000
        A structured yes/no task with a fixed YAML schema. No reasoning chain
        needed; the model just needs enough tokens to emit the YAML block.
      - Proposal generation (reasoning): think=True, num_predict=3000
        Weighing date ordering, semantic meaning, and category correctness
        before committing to a merge/supersede verdict. The larger budget
        covers the thinking trace plus the final YAML output.

    NOTE on stop sequences: the main-chat stop sequences ("(Send).", etc.) are
    designed to prevent chat looping, not for structured LLM calls. They are
    intentionally omitted here so they cannot truncate YAML mid-block.

    Args:
        messages:    List of {role, content} dicts.
        timeout:     Request timeout in seconds.
        think:       Whether to enable reasoning tokens (think=True for proposals,
                     think=False for detection).
        num_predict: Maximum tokens to generate. Covers reasoning trace + output
                     when think=True, output only when think=False.

    Returns:
        The model's content response string, or "" on failure.
    """
    importlib.reload(cfg)
    override = cfg.SUMMARY_MODEL_OVERRIDE
    model = cfg.MODEL_NAME if override == "default" else override

    options = {"num_ctx": cfg.NUM_CTX}
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
    # Stop sequences are deliberately omitted — see docstring above.
    options["num_predict"] = num_predict

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": options,
        "think": think,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{cfg.OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
            result = resp.json()
        content = result.get("message", {}).get("content", "").strip()
        thinking_trace = result.get("message", {}).get("thinking", "")
        if thinking_trace and cfg.DEBUG_LOGGING:
            print(
                f"[CONSOLIDATOR] Think trace ({len(thinking_trace.split())} words): "
                f"{thinking_trace[:300]}...",
                flush=True,
            )
        if not content:
            # Diagnostic: surface what Ollama actually returned so we can
            # identify future model-specific quirks without guessing.
            msg_keys = list(result.get("message", {}).keys())
            done_reason = result.get("done_reason", "unknown")
            print(
                f"[CONSOLIDATOR] Warning: empty content from model. "
                f"done_reason={done_reason!r} message_keys={msg_keys} "
                f"think={think} num_predict={num_predict}",
                flush=True,
            )
        return content
    except httpx.ReadTimeout:
        print(
            f"[CONSOLIDATOR] Ollama call timed out after {timeout}s "
            f"(think={think}, num_predict={num_predict}).",
            flush=True,
        )
        return ""
    except Exception as e:
        print(f"[CONSOLIDATOR] Ollama call failed: {type(e).__name__}: {e}", flush=True)
        return ""


# Load persisted scan state on module import.
_load_scan_state()


# ============================================================================
# SECTION 1 — PUBLIC API
# Entry points called by the server's idle-time loop and chat handler.
# ============================================================================


def cancel_pending_consolidation():
    """Cancel any in-flight consolidation task.

    Called at the top of chat_stream() so Ollama is freed immediately when
    the user sends a message, even if consolidation is mid-run.
    The cancelled run does NOT count against the cooldown — it will be
    eligible to retry after the next idle window.
    """
    global _consolidation_task, _consolidating
    if _consolidation_task and not _consolidation_task.done():
        _consolidation_task.cancel()
        _consolidating = False
        print("[CONSOLIDATOR] Cancelled (new chat request).", flush=True)
    _consolidation_task = None


async def run_consolidation():
    """Top-level coroutine — called from the server's idle-time loop.

    Skips silently if consolidation is disabled, already running, or within
    the cooldown window. Only updates _last_run_ts on *successful* completion
    so a cancelled run doesn't lock out the next idle window.
    """
    global _consolidating, _last_run_ts
    importlib.reload(cfg)

    if not cfg.CONSOLIDATION_ENABLED:
        return

    if _consolidating:
        print("[CONSOLIDATOR] Already running — skipping.", flush=True)
        return

    # Defer if the extractor is currently making an LLM call.
    # Both share the same Ollama instance; running in parallel causes timeouts.
    if _extracting_elsewhere():
        print(
            "[CONSOLIDATOR] Extractor is running — deferring consolidation.",
            flush=True,
        )
        return

    # Defer if a heavy server background task (Vault Map Gen, Sync, etc) is running.
    if _heavy_tasks_running():
        print(
            "[CONSOLIDATOR] Server background task is running — deferring consolidation.",
            flush=True,
        )
        return

    now = time.time()
    if (now - _last_run_ts) < cfg.CONSOLIDATION_COOLDOWN:
        remaining = int(cfg.CONSOLIDATION_COOLDOWN - (now - _last_run_ts))
        print(
            f"[CONSOLIDATOR] Cooldown active — {remaining}s remaining. Skipping.",
            flush=True,
        )
        return

    _consolidating = True
    completed = False
    try:
        await _do_consolidation()
        completed = True
    except asyncio.CancelledError:
        print("[CONSOLIDATOR] Cancelled — cooldown not applied.", flush=True)
    except Exception as e:
        print(f"[CONSOLIDATOR ERROR] {type(e).__name__}: {e}", flush=True)
    finally:
        _consolidating = False
        # Only lock the cooldown on a successful (non-cancelled) run
        if completed:
            _last_run_ts = time.time()


# ============================================================================
# SECTION 2 — STEP 1: SCAN CONTEXT ENTRIES
# Walk the vault and parse every live CE_*.md file into a FactRecord dict.
# ============================================================================


def scan_context_entries() -> list[dict]:
    """Fetch all live context entries from SQLite and format as FactRecords.

    Returns:
        List of FactRecord dicts, sorted oldest-first within each category.
    """
    import memory_db
    import datetime

    # Get live entries (and optionally extracted ones if configured)
    statuses = ["live"]
    importlib.reload(cfg)
    if cfg.CONSOLIDATION_INCLUDE_EXTRACTED:
        statuses.append("extracted")

    rows = memory_db.get_all_entries(statuses=statuses)
    records = []

    category_counts: dict[str, int] = {}

    for row in rows:
        cat = row["category"]
        # Infer cat number for sorting
        cat_num_match = re.search(r"Cat(\d{2})", cat)
        cat_num = int(cat_num_match.group(1)) if cat_num_match else 0
        
        # Parse date
        try:
            if row["date"]:
                entry_date = datetime.datetime.strptime(row["date"], "%Y-%m-%d")
            else:
                entry_date = datetime.datetime.min
        except ValueError:
            entry_date = datetime.datetime.min

        records.append({
            "id": row["id"],
            "path": str(row["id"]),     # Stub for legacy path usage
            "rel_path": str(row["id"]), # Stub for legacy path usage
            "category": cat,
            "cat_num": cat_num,
            "subject": row["subject"],
            "date": entry_date,
            "summary": row["observation"],
            "filename": f"Entry_{row['id']}",
        })
        category_counts[cat] = category_counts.get(cat, 0) + 1

    print(
        f"[CONSOLIDATOR] Scanned {len(records)} context entries "
        f"across {len(category_counts)} category group(s):",
        flush=True,
    )
    for cat, count in sorted(category_counts.items()):
        print(f"  {cat}: {count} entry/ies", flush=True)
        
    return records


# ============================================================================
# SECTION 3 — STEP 2: DETECTION
# Group entries by category, then run two focused LLM detection calls per
# category: one for consolidation (duplicates/overlaps), one for
# recategorization (category audit). Each task gets its own prompt so it
# can be tuned independently without affecting the other.
# ============================================================================

# ── PROMPT: CONSOLIDATION DETECTION — SYSTEM ─────────────────────────────────
# Controls the model's persona for the duplicate/overlap detection task.
# This is a classification call (think=False); tuning here affects precision
# and recall of duplicate flagging.

_CONSOL_DETECT_SYSTEM_PROMPT = (
    "You are a Fact Identifier. Your job is to identify duplicate facts. "
    "Only flag facts that express the same underlying truth about the primary subject of the entry. "
    "Output only the YAML block, nothing else."
)

# ── PROMPT: CONSOLIDATION DETECTION ──────────────────────────────────────────
# Anchor-based comparison template for finding duplicate/overlapping entries.
# One call per category per pass. Focused on cluster detection only.
# Format variables (filled by _build_consol_prompt):
#   {category}        — category code being audited, e.g. "Cat11-E"
#   {anchor_text}     — formatted anchor entry string
#   {comparison_text} — formatted comparison entries block
# NOTE: This is a .format() template. Any literal { or } must be written as {{ or }}.

_CONSOL_DETECT_PROMPT = """\
You are auditing one context memory entry against a comparison set for category {category}. \
Compare the ANCHOR ENTRY [1] to the COMPARISON SET. Only flag entries for consolidation if they meet one of these conditions:
    1. PRIMARY SUBJECT: The entries share the same primary subject. Some entries may contain multiple subjects, determine the primary subject of each entry. Only flag entries for consolidation if they share the same primary subject and at least one other condition is met.
    2. FACTUAL EQUIVALENCE: The entries express the same underlying truth or specific preference, even if the wording or phrasing is different (e.g., "Drinks black coffee" vs "Consumes coffee without milk or sugar").
    3. DIRECT SUPERSEDENCE: One entry provides a newer state of fact from the anchor memory (e.g., a changed preference or location).
    4. FACTUAL ENHANCEMENT: One entry is a DIRECT, more detailed version of the other (e.g., 'Plays Skyrim' vs 'Plays Skyrim with heavily customized mods').

STRICT DISTINCTNESS (THE ATOMIC RULE):
    - KEYWORD MATCHES DO NOT EQUAL FACT MATCHES: Do NOT flag entries just because they share identical names, places, or keywords. Two entries can feature the same person or project but describe entirely distinct details.
    - Do NOT flag entries merely because they share a broad topic. (e.g., 'Likes French Toast' vs 'Likes Bacon' are distinct facts. 'Upgraded PC CPU' and 'Installed PC GPU' are distinct facts). These MUST remain separate.
    - THE EVENT EXCEPTION: Distinguish between 'States' (persistent, mutable facts) and 'Events' (specific, time-bound occurrences, daily logs, tasks). Sequential events on different dates are distinct historical records. Do NOT flag them.

ANCHOR ENTRY (always [1]):
{anchor_text}

COMPARISON ENTRIES:
{comparison_text}

Output ONLY a YAML block in this exact format:

```yaml
clusters:
    - topic: "brief topic label"
      entry_indices: [1, 3]  # must include [1] (the anchor) if involved
      reason: "why these are the same specific fact"
```
If no clusters are found, output an empty list.\
"""

# ── PROMPT: RECATEGORIZATION DETECTION — SYSTEM ──────────────────────────────
# Controls the model's persona for the category audit task.
# This is a classification call (think=False); tuning here affects the rate
# of recategorization proposals generated.

_RECAT_DETECT_SYSTEM_PROMPT = (
    "You are a Category Auditor for a personal memory system. "
    "Your job is to evaluate whether entries are filed in the correct category. "
    "Output only the YAML block, nothing else."
)

# ── PROMPT: RECATEGORIZATION DETECTION ───────────────────────────────────────
# Evaluates all entries in an anchor batch for correct category placement.
# Separate from consolidation detection so each task gets focused attention.
# Format variables (filled by _build_recat_prompt):
#   {category}     — category code being audited, e.g. "Cat11-E"
#   {cat_ref}      — Cat00 taxonomy block (required for category audit)
#   {entries_text} — flat numbered list of all entries in the batch
# NOTE: This is a .format() template. Any literal { or } must be written as {{ or }}.

_RECAT_DETECT_PROMPT = """\
You are auditing context memory entries in category {category} for correct categorization.

For each entry below, evaluate whether it primarily belongs in a different category. \
Suggest recategorization ONLY when the entry's main subject matter fits another category better \
— not because it references or touches on another topic. Entries that relate to multiple \
categories stay in the category with the most weight; cross-category relevance is captured \
via Secondary links, not file moves.
{cat_ref}

ENTRIES TO AUDIT:
{entries_text}

Output ONLY a YAML block in this exact format:

```yaml
recategorize:
    - entry_index: 1
      suggested_category: Cat08-R
      topic: "brief topic label"
      reason: "why primary weight belongs in the other category"
```
If no recategorizations are needed, output an empty list.\
"""


def _group_by_category(records: list[dict]) -> dict[str, list[dict]]:
    """Group FactRecords by category code, sorted oldest-first within each group.

    The filename is used as a tiebreaker so the sort is fully deterministic.
    Stable ordering is required for anchor-based scanning: the same index must
    refer to the same entry across successive passes.
    """
    groups: dict[str, list[dict]] = {}
    for r in records:
        groups.setdefault(r["category"], []).append(r)
    for key in groups:
        groups[key].sort(key=lambda r: (r["date"], r["filename"]))
    return groups


def _get_anchor_batch(
    category: str, records: list[dict]
) -> tuple[dict, list[dict]]:
    """Return the current (anchor_record, comparison_window) for a category.

    Records must be sorted oldest-first (with filename tiebreaker) before
    calling this so index positions are stable across passes.

    If the stored N differs from the current N (new entries extracted since
    the last pass), the category state is reset to (anchor=0, offset=0).

    Args:
        category: The category code, e.g. "Cat11-E".
        records:  All FactRecords for this category, sorted oldest-first.

    Returns:
        Tuple of (anchor_record, comparison_window).
        anchor_record:     The single fixed entry for this detection batch.
        comparison_window: Up to (CONSOLIDATION_MAX_RECORDS_PER_GROUP - 1)
                           other entries to compare the anchor against,
                           drawn oldest-first from the comparison pool.
    """
    global _category_scan_state
    n = len(records)
    max_records = cfg.CONSOLIDATION_MAX_RECORDS_PER_GROUP
    batch = max_records - 1  # comparison slots (1 slot reserved for the anchor)

    state = _category_scan_state.get(category, {"anchor": 0, "offset": 0, "n": n})

    # Reset if the number of entries changed (extractor wrote new files).
    if state.get("n", n) != n:
        print(
            f"[CONSOLIDATOR] {category}: entry count changed "
            f"({state.get('n')} → {n}) — resetting scan state.",
            flush=True,
        )
        state = {"anchor": 0, "offset": 0, "n": n}
        _category_scan_state[category] = state

    anchor_idx = state["anchor"] % n
    offset = state["offset"]

    anchor_record = records[anchor_idx]
    # Comparison pool: all entries except the anchor, oldest-first order preserved.
    comparison_pool = records[:anchor_idx] + records[anchor_idx + 1 :]
    comparison_window = comparison_pool[offset : offset + batch]
    return anchor_record, comparison_window


def _advance_scan_state(category: str, n: int) -> None:
    """Advance anchor/offset pointers after one detection call.

    When a comparison pool is exhausted the anchor advances to the next entry.
    When the last anchor is exhausted the full cycle completes and resets to
    (anchor=0, offset=0) so the next cycle begins from scratch.

    Args:
        category: The category code.
        n:        Current number of records in this category.
    """
    global _category_scan_state
    max_records = cfg.CONSOLIDATION_MAX_RECORDS_PER_GROUP
    batch = max_records - 1
    comparison_pool_size = n - 1

    state = _category_scan_state.get(category, {"anchor": 0, "offset": 0, "n": n})
    anchor = state.get("anchor", 0)
    offset = state.get("offset", 0)

    next_offset = offset + batch
    if next_offset >= comparison_pool_size:
        # This anchor has seen all other entries — advance to the next anchor.
        next_anchor = (anchor + 1) % n
        next_offset = 0
        if next_anchor == 0:
            print(
                f"[CONSOLIDATOR] {category}: full anchor cycle complete — "
                f"all {n} entries compared. Restarting cycle.",
                flush=True,
            )
    else:
        next_anchor = anchor

    _category_scan_state[category] = {
        "anchor": next_anchor,
        "offset": next_offset,
        "n": n,
    }


def _fmt_entry(r: dict, idx: int) -> str:
    """Format a single FactRecord for use in detection prompts.

    Args:
        r:   FactRecord dict.
        idx: 1-based display index.

    Returns:
        Formatted entry string with index, filename, date, and summary.
    """
    date_str = (
        r["date"].strftime("%Y-%m-%d")
        if r["date"] != datetime.datetime.min
        else "unknown"
    )
    return f"\n[{idx}] {r['filename']} ({date_str})\n    Summary: {r['summary']}\n"


# ── Step 2a: Consolidation detection ─────────────────────────────────────────


def _build_consol_prompt(
    category: str,
    anchor: dict,
    comparison_window: list[dict],
) -> str:
    """Build the anchor-based consolidation detection prompt (Part A only).

    The anchor entry is always index [1]. Comparison entries follow as [2]..[N].

    Args:
        category:          Category code being audited, e.g. "Cat11-E".
        anchor:            The fixed FactRecord for this batch.
        comparison_window: Other FactRecords to compare the anchor against.

    Returns:
        Formatted prompt string.
    """
    anchor_text = _fmt_entry(anchor, 1)
    comparison_text = "".join(
        _fmt_entry(r, i + 2) for i, r in enumerate(comparison_window)
    )

    return _CONSOL_DETECT_PROMPT.format(
        category=category,
        anchor_text=anchor_text,
        comparison_text=comparison_text,
    )


async def _detect_consol_in_group(
    category: str,
    anchor: dict,
    comparison_window: list[dict],
) -> list[dict]:
    """Run one focused consolidation detection call for a category.

    Asks the LLM only about duplicate/overlapping entries (no category audit).

    Args:
        category:          The category code being examined.
        anchor:            The anchor FactRecord for this batch.
        comparison_window: Comparison FactRecords for this batch.

    Returns:
        List of Cluster dicts (may be empty).
    """
    prompt = _build_consol_prompt(category, anchor, comparison_window)
    messages = [
        {"role": "system", "content": _CONSOL_DETECT_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    raw = await _call_ollama(
        messages,
        timeout=cfg.CONSOLIDATION_TIMEOUT,
        think=False,
        num_predict=3000,
    )

    if not raw:
        return []

    combined_records = [anchor] + comparison_window
    return _parse_consol_yaml(raw, category, combined_records)


def _parse_consol_yaml(
    raw: str, category: str, records: list[dict]
) -> list[dict]:
    """Parse consolidation detection YAML into Cluster dicts.

    Args:
        raw:      Raw model response string.
        category: Category being processed.
        records:  Combined list [anchor] + comparison_window (1-based index).

    Returns:
        List of Cluster dicts (may be empty).
    """
    match = re.search(r"```(?:yaml)?\s*\n(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    block = match.group(1) if match else raw

    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as e:
        print(f"[CONSOLIDATOR] YAML parse error in consol detection: {e}", flush=True)
        return []

    if not isinstance(data, dict):
        return []

    clusters = []
    for c in data.get("clusters") or []:
        if not isinstance(c, dict):
            continue
        indices = c.get("entry_indices") or []
        topic = str(c.get("topic", "unknown topic")).strip()
        reason = str(c.get("reason", "")).strip()

        cluster_records = []
        for idx in indices:
            if isinstance(idx, int) and 1 <= idx <= len(records):
                cluster_records.append(records[idx - 1])

        if len(cluster_records) >= 2:
            clusters.append(
                {
                    "category": category,
                    "topic": topic,
                    "reason": reason,
                    "records": cluster_records,
                }
            )

    return clusters


# ── Step 2b: Recategorization detection ──────────────────────────────────────


def _build_recat_prompt(
    category: str,
    entries: list[dict],
    cat00: str,
) -> str:
    """Build the recategorization detection prompt (Part B only).

    Lists all entries in the batch with sequential indices for individual
    category-fit evaluation. No anchor/comparison distinction needed here.

    Args:
        category: Category code being audited.
        entries:  All FactRecords in the batch (anchor + comparison window).
        cat00:    Cat00 taxonomy text for category reference.

    Returns:
        Formatted prompt string.
    """
    entries_text = "".join(
        _fmt_entry(r, i + 1) for i, r in enumerate(entries)
    )
    cat_ref = f"\n\nCATEGORY REFERENCE:\n{cat00}" if cat00 else ""

    return _RECAT_DETECT_PROMPT.format(
        category=category,
        cat_ref=cat_ref,
        entries_text=entries_text,
    )


async def _detect_recat_in_group(
    category: str,
    entries: list[dict],
    cat00: str,
) -> list[dict]:
    """Run one focused recategorization detection call for a category.

    Asks the LLM only about category placement (no duplicate detection).

    Args:
        category: The category code being examined.
        entries:  All FactRecords in the batch (anchor + comparison window).
        cat00:    Cat00 taxonomy text for category reference.

    Returns:
        List of recategorization item dicts (may be empty).
    """
    prompt = _build_recat_prompt(category, entries, cat00)
    messages = [
        {"role": "system", "content": _RECAT_DETECT_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    raw = await _call_ollama(
        messages,
        timeout=cfg.CONSOLIDATION_TIMEOUT,
        think=False,
        num_predict=1000,
    )

    if not raw:
        return []

    return _parse_recat_yaml(raw, entries)


def _parse_recat_yaml(raw: str, records: list[dict]) -> list[dict]:
    """Parse recategorization detection YAML into recat item dicts.

    Args:
        raw:     Raw model response string.
        records: Entry list as passed to the prompt (1-based index).

    Returns:
        List of recategorization item dicts (may be empty).
    """
    match = re.search(r"```(?:yaml)?\s*\n(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    block = match.group(1) if match else raw

    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as e:
        print(f"[CONSOLIDATOR] YAML parse error in recat detection: {e}", flush=True)
        return []

    if not isinstance(data, dict):
        return []

    recat_items = []
    for rc in data.get("recategorize") or []:
        if not isinstance(rc, dict):
            continue
        idx = rc.get("entry_index")
        if not isinstance(idx, int) or not (1 <= idx <= len(records)):
            continue
        recat_items.append(
            {
                "record": records[idx - 1],
                "suggested_category": str(rc.get("suggested_category", "")).strip(),
                "topic": str(rc.get("topic", "")).strip(),
                "reason": str(rc.get("reason", "")).strip(),
            }
        )

    return recat_items


# ── Step 2 orchestration: run both detection calls per category ──────────────


async def _detect_in_group(
    category: str, records: list[dict], cat00: str
) -> tuple[list[dict], list[dict]]:
    """Run both detection calls (consolidation + recategorization) for a category.

    Retrieves the anchor batch once, then runs two sequential focused LLM calls:
      Step 2a — consolidation detection (duplicates/overlaps)
      Step 2b — recategorization detection (category audit)

    Advances the scan state once after both calls complete.

    Args:
        category: The category code being examined.
        records:  All FactRecords for this category, oldest-first.
        cat00:    Cat00 index text for category reference.

    Returns:
        Tuple of (clusters, recat_items).
    """
    anchor, comparison_window = _get_anchor_batch(category, records)

    if not comparison_window:
        _advance_scan_state(category, len(records))
        return [], []

    anchor_idx = records.index(anchor)
    print(
        f"[CONSOLIDATOR] {category}: anchor=[{anchor_idx}] '{anchor['filename']}' "
        f"vs {len(comparison_window)} comparison entry/ies.",
        flush=True,
    )

    combined_entries = [anchor] + comparison_window

    # Step 2a — Consolidation detection (focused on duplicates/overlaps)
    clusters = await _detect_consol_in_group(
        category, anchor, comparison_window
    )

    # Step 2b — Recategorization detection (focused on category audit)
    recat_items = await _detect_recat_in_group(
        category, combined_entries, cat00
    )

    # Advance scan state once after both calls
    _advance_scan_state(category, len(records))

    return clusters, recat_items


async def find_consolidation_candidates(
    records: list[dict], cat00: str
) -> tuple[list[dict], list[dict]]:
    """Identify groups of entries that address the same topic within a category.

    Uses a rotating start index (_group_start_index) so successive runs scan
    different slices of the category list. With 30 categories and a scan limit
    of 8, the full vault cycles in ~4 runs.

    Cost bounds (both checked per iteration):
      CONSOLIDATION_GROUP_SCAN_LIMIT  — max LLM detection calls per run
      CONSOLIDATION_BATCH_SIZE        — max proposal clusters returned

    Args:
        records: All scanned FactRecords.
        cat00:   Cat00 taxonomy text for category reference.

    Returns:
        Tuple of (clusters, recat_items):
          - clusters:    Cluster dicts ready for proposal generation.
          - recat_items: Standalone recategorization dicts for direct file output.
    """
    global _group_start_index

    all_groups = _group_by_category(records)  # OrderedDict by category code
    group_items = list(all_groups.items())    # [(category, records), ...]
    total_groups = len(group_items)

    if total_groups == 0:
        return [], []

    # Wrap start index in case categories were added/removed since last run
    start = _group_start_index % total_groups

    # Rotate: take groups from `start` forward, wrapping around if needed
    rotated = group_items[start:] + group_items[:start]

    clusters: list[dict] = []
    recat_items: list[dict] = []
    group_scan_limit = cfg.CONSOLIDATION_GROUP_SCAN_LIMIT
    batch_limit = cfg.CONSOLIDATION_BATCH_SIZE
    groups_scanned = 0

    for category, group_records in rotated:
        if groups_scanned >= group_scan_limit:
            print(
                f"[CONSOLIDATOR] Group scan limit ({group_scan_limit}) reached. "
                f"Stopping detection.",
                flush=True,
            )
            break

        if len(group_records) < 2:
            # Single entry — nothing to consolidate; don't count against scan limit
            continue

        groups_scanned += 1
        new_clusters, new_recats = await _detect_in_group(
            category, group_records, cat00
        )
        clusters.extend(new_clusters)
        recat_items.extend(new_recats)

        if len(clusters) >= batch_limit:
            clusters = clusters[:batch_limit]
            break

    # Advance the rotating index for the next run
    next_start = (start + group_scan_limit) % total_groups
    if next_start < start:
        print(
            "[CONSOLIDATOR] Full category cycle complete — wrapping to Cat01.",
            flush=True,
        )
    _group_start_index = next_start

    print(
        f"[CONSOLIDATOR] Scanned {groups_scanned} group(s) "
        f"(starting from index {start}, next run starts at {next_start}). "
        f"Found {len(clusters)} candidate cluster(s).",
        flush=True,
    )
    return clusters, recat_items


# ============================================================================
# SECTION 4 — STEP 3: CONSOLIDATION PROPOSALS
# For each detected cluster, call the LLM (think=True) to produce a reasoned
# merge/supersede/keep_both verdict and write a CONSOLIDATION_*.md file.
# ============================================================================

# ── PROMPT: CONSOLIDATION PROPOSAL — SYSTEM ──────────────────────────────────
# Controls the model's persona and caution level for the proposal task.
# This call uses think=True; tuning here affects reasoning quality and the
# rate of over-merging vs. over-splitting.
# NOTE: This is a .format() template. Any literal { or } must be written as {{ or }}.

_PROPOSAL_SYSTEM_PROMPT = (
    "You are a Knowledge Engineer & Qualitative Analyst. "
    "Your primary job is to catch redundancies without destroying the granular details about the human experience. "
    "Reason through date ordering and semantic meaning before deciding. "
    "Entries with no date (CY-YYYY/MM/DD) are to be treated as the oldest. "
    "Output only the YAML block."
)

# ── PROMPT: CONSOLIDATION PROPOSAL ───────────────────────────────────────────
# Verdict template. One call per detected cluster (think=True).
# Format variables (filled by generate_consolidation_proposal):
#   {category}            — category code of the cluster, e.g. "Cat05-R"
#   {entries_text}        — formatted block listing each source entry and summary
#   {history_instruction} — "Preserve evolution…" or "Use only most recent…"
#                           (derived from CONSOLIDATION_KEEP_HISTORY config flag)
#   {cat_ref}             — Cat00 taxonomy block, or empty string if unavailable
# NOTE: This is a .format() template. Any literal { or } must be written as {{ or }}.

_PROPOSAL_PROMPT = """\
You are evaluating a cluster of memory entries.
{category} ENTRIES:

{entries_text}

TASK:
1. Analyze the entries above for:
  A) The primary subject of the entries.
  B) FACTUAL EQUIVALENCE: The same underlying truth; OR distinct facts/events that merely share a theme or keyword.
2. {history_instruction}
3. Choose a verdict:
   - 'supersede' (one entry outright replaces the outdated other)
   - 'merge' (synthesize overlapping granular details into one comprehensive fact, retaining key details from all entries)
   - 'keep_both' (entries are genuinely distinct specific facts, events, or observations that MUST remain separate).
4. THE EVENT EXCEPTION: If the entries represent discrete events, moods, or occurrences tied to different dates (State-Data vs Time-Series Data), they are a historical log. You MUST choose 'keep_both'.
5. If verdict is 'keep_both', set merged_summary to an empty string.
6. DATA PRESERVATION RULE: If you choose 'merge' or 'supersede', the resulting `merged_summary` MUST include every specific noun, condition, and contextual detail present in the source entries. Do not generalize or drop context to make the sentence read more smoothly. If combining them causes a loss of specific detail, you must choose 'keep_both'.
{cat_ref}

Output ONLY a YAML block:
```yaml
verdict: supersede   # supersede / merge / keep_both
merged_summary: "The consolidated fact as a single clear sentence."
merged_tags: "kw/tag1, kw/tag2" # comma-separated semantic tags starting with kw/
confidence: high     # high / medium / low
reasoning: "Brief explanation of the verdict."
```\
"""


async def generate_consolidation_proposal(cluster: dict) -> str | None:
    """Ask the LLM to produce a merged summary and verdict for a cluster.

    Uses think=True so the model can reason carefully about conflicting facts
    and date ordering before committing to a merge/supersede/keep_both verdict.
    Category corrections are handled separately via _write_recategorization_proposal.

    Args:
        cluster: A Cluster dict from find_consolidation_candidates().

    Returns:
        str: Absolute path to the written proposal file, or None on failure.
    """
    importlib.reload(cfg)
    cat00 = load_cat00_index()
    keep_history = cfg.CONSOLIDATION_KEEP_HISTORY

    records = cluster["records"]
    category = cluster["category"]

    # Build entry block for the prompt
    entries_text = ""
    for r in records:
        date_str = r["date"].strftime("%Y-%m-%d") if r["date"] != datetime.datetime.min else "unknown"
        entries_text += f"\n- File: {r['filename']} (dated {date_str})\n  Summary: {r['summary']}\n"

    history_instruction = (
        "Preserve the evolution of the fact in the merged summary "
        "(e.g., 'Previously X as of [date], now Y as of [date]')."
        if keep_history
        else "Use only the most recent fact in the merged summary; discard older versions."
    )

    cat_ref = f"\n\nCATEGORY REFERENCE:\n{cat00}" if cat00 else ""

    prompt = _PROPOSAL_PROMPT.format(
        category=category,
        entries_text=entries_text,
        history_instruction=history_instruction,
        cat_ref=cat_ref,
    )

    messages = [
        {"role": "system", "content": _PROPOSAL_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    raw = await _call_ollama(
        messages,
        timeout=cfg.CONSOLIDATION_TIMEOUT,
        think=True,        # Proposal genuinely needs reasoning
        num_predict=3000,  # Headroom for reasoning trace + YAML verdict
    )
    if not raw:
        return None

    proposal_data = _parse_proposal_yaml(raw, category)
    if not proposal_data:
        return None

    return _write_proposal(cluster, proposal_data)


def _parse_proposal_yaml(raw: str, category: str) -> dict | None:
    """Parse the consolidation verdict YAML from the model response."""
    match = re.search(r"```(?:yaml)?\s*\n(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    block = match.group(1) if match else raw

    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as e:
        print(f"[CONSOLIDATOR] YAML parse error in proposal: {e}", flush=True)
        return None

    if not isinstance(data, dict):
        return None

    verdict = str(data.get("verdict", "keep_both")).strip().lower()
    if verdict not in ("supersede", "merge", "keep_both"):
        verdict = "keep_both"

    return {
        "verdict": verdict,
        "merged_summary": str(data.get("merged_summary", "")).strip(),
        "merged_tags": str(data.get("merged_tags", "")).strip(),
        "confidence": str(data.get("confidence", "medium")).strip().lower(),
        "target_category": str(data.get("target_category", category)).strip(),
        "reasoning": str(data.get("reasoning", "")).strip(),
    }


def _write_proposal(cluster: dict, proposal: dict) -> str | None:
    """Write a consolidation proposal to the SQLite database.

    Args:
        cluster:  The Cluster dict describing the conflict group.
        proposal: Parsed verdict dict from _parse_proposal_yaml().

    Returns:
        Proposal ID string, or None on write failure.
    """
    import memory_db
    
    category = cluster["category"]
    topic = cluster["topic"]
    verdict = proposal["verdict"]
    merged = proposal["merged_summary"]
    target_cat = proposal["target_category"]
    reasoning = proposal["reasoning"]
    records = cluster["records"]

    if verdict == "keep_both":
        return None  # We don't need to write 'keep_both' proposals to SQLite

    source_ids = [r["id"] for r in records]

    confidence = proposal.get("confidence", "medium")
    
    # Auto-apply if confidence is high
    status = "auto_applied" if confidence == "high" else "pending"

    try:
        pid = memory_db.insert_proposal(
            type=verdict,
            source_ids=source_ids,
            merged_observation=merged,
            merged_tags=proposal.get("merged_tags"),
            suggested_category=target_cat,
            reason=reasoning,
            topic=topic,
            confidence=confidence,
            status=status
        )
        
        # If auto-applied, perform the merge immediately
        if status == "auto_applied":
            for sid in source_ids:
                memory_db.delete_entry(sid)
            
            # Union tags for fallback
            merged_tags_set = set()
            for r in records:
                if r.get("tags"):
                    for t in r["tags"].split(","):
                        if t.strip():
                            merged_tags_set.add(t.strip())
            fallback_tags = ", ".join(sorted(merged_tags_set)) if merged_tags_set else None
            final_tags = proposal.get("merged_tags") or fallback_tags
            
            subject = records[0]["subject"] if records else "R"
            date = records[0]["date"] if records else None
            
            memory_db.insert_entry(
                category=target_cat,
                subject=subject,
                observation=merged,
                source="consolidated",
                date=date,
                tags=final_tags
            )
            print(f"[CONSOLIDATOR] Auto-applied merge (Proposal {pid})", flush=True)
            return str(pid)

        print(f"[CONSOLIDATOR] Proposal created: ID {pid} ({verdict})", flush=True)
        return str(pid)
    except Exception as e:
        print(f"[CONSOLIDATOR] Failed to write proposal: {e}", flush=True)
        return None


# ============================================================================
# SECTION 5 — STEP 3b: RECATEGORIZATION PROPOSALS
# Write single-action RECATEGORIZE_*.md files for entries the detection LLM
# flagged as belonging in a different category. No additional LLM call needed.
# ============================================================================


def _write_recategorization_proposal(
    recat_item: dict,
    pending_recat_sources: set[str] | None = None,
) -> str | None:
    """Auto-apply recategorization and record an audit trail in SQLite.

    Args:
        recat_item:            Dict with keys 'record', 'suggested_category',
                               'topic', and 'reason'.
        pending_recat_sources: Ignored. Used in legacy signature.

    Returns:
        Proposal ID string, or None on failure.
    """
    import memory_db

    record = recat_item["record"]
    suggested = recat_item["suggested_category"]
    reason = recat_item["reason"]
    topic = recat_item.get("topic", reason[:60])
    
    # We auto-apply recats per user preference (1 pass).
    try:
        pid = memory_db.insert_proposal(
            type="recategorize",
            source_ids=[record["id"]],
            suggested_category=suggested,
            reason=reason,
            topic=topic,
            status="auto_applied"
        )
        memory_db.update_entry(record["id"], category=suggested)
        print(f"[CONSOLIDATOR] Auto-applied recategorization (Proposal {pid}): entry {record['id']} -> {suggested}", flush=True)
        return str(pid)
    except Exception as e:
        print(f"[CONSOLIDATOR] Failed to auto-apply recategorization: {e}", flush=True)
        return None


# ============================================================================
# SECTION 6 — STEP 4: ORCHESTRATION
# Top-level pipeline that sequences all prior steps for one consolidation pass.
# ============================================================================


async def _do_consolidation():
    """Core consolidation pipeline. Called by run_consolidation().

    Execution order:
      1. Scan all live context entries into FactRecord dicts.
      2. Detect duplicate clusters and recategorization candidates.
      3. Auto-apply recategorization candidates.
      4. Write consolidation proposals via LLM proposal calls (think=True).
      5. Persist anchor scan state to disk.
    """
    importlib.reload(cfg)
    import memory_db
    print("[CONSOLIDATOR] Starting idle-time consolidation pass...", flush=True)
    start = time.time()

    # Step 1 — Scan vault
    cat00 = load_cat00_index()
    records = scan_context_entries()

    if not records:
        print("[CONSOLIDATOR] No context entries found.", flush=True)
        return

    # Step 2 — Detect clusters and recategorization candidates
    clusters, recat_items = await find_consolidation_candidates(records, cat00)

    # Step 3 — Write recategorization proposals (Auto-applied)
    recats_written = recats_skipped = 0
    for recat_item in recat_items:
        if memory_db.has_pending_proposal_for([recat_item["record"]["id"]]):
            recats_skipped += 1
            continue
            
        result = _write_recategorization_proposal(recat_item, None)
        if result:
            recats_written += 1

    if not clusters:
        print("[CONSOLIDATOR] No consolidation candidates found.", flush=True)

    # Step 4 — Write consolidation proposals via LLM
    proposals_written = proposals_skipped = 0
    for cluster in clusters:
        # Skip if any source file in this cluster already has an open proposal.
        cluster_ids = [r["id"] for r in cluster["records"]]
        if memory_db.has_pending_proposal_for(cluster_ids):
            print(
                f"[CONSOLIDATOR] Consol skip (already pending): "
                f"{cluster['topic']} ({len(cluster_ids)} entries)",
                flush=True,
            )
            proposals_skipped += 1
            continue
            
        result = await generate_consolidation_proposal(cluster)
        if result:
            proposals_written += 1

    # Step 5 — Persist anchor scan state
    _save_scan_state()

    elapsed = time.time() - start
    print(
        f"[CONSOLIDATOR] Done. {proposals_written} consolidation proposal(s) written, "
        f"{proposals_skipped} skipped (already pending). "
        f"{recats_written} recat proposal(s) written, "
        f"{recats_skipped} skipped (already pending). "
        f"Elapsed: {elapsed:.1f}s.",
        flush=True,
    )

