"""
fact_consolidator.py — Idle-time context entry consolidation for Evelyn's memory system.

Runs during server idle time to scan live context entries for duplicates, contradictions,
and miscategorized facts. Uses the loaded LLM with thinking tokens for nuanced semantic
reasoning. Produces human-readable proposal files in the Pending folder — nothing is
auto-applied to the live vault.

Architecture:
  - scan_context_entries()              — Walk all live Cat##/Cat##-{E,R}/*.md files
  - find_consolidation_candidates()     — Group by category, detect conflict clusters
                                          and standalone recategorization items
  - generate_consolidation_proposal()   — LLM-driven merge verdict (think=True)
  - _write_recategorization_proposal()  — Instant file output for category moves (no LLM)
  - run_consolidation()                 — Top-level coroutine for idle-time scheduling
  - cancel_pending_consolidation()      — Cancels any in-flight run (called on new chat)

Output file types (both written to PENDING_DIR):
  CONSOLIDATION_*.md   — Merge/supersede proposal for 2+ overlapping entries.
                         source_date frontmatter = most-recent source entry date.
  RECATEGORIZE_*.md    — Single-entry category move proposal. No LLM call needed.

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

import evelyn_config as cfg

# Import full module so we can read fact_extractor._extracting for mutual exclusion.
import fact_extractor
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _ensure_pending_dir() -> None:
    """Create PENDING_DIR and write a placeholder README if it doesn't exist.

    The placeholder prevents sync tools (Google Drive, Obsidian Sync) from
    removing the folder when it has no proposal files in it.
    """
    importlib.reload(cfg)
    pending_dir = cfg.PENDING_DIR
    os.makedirs(pending_dir, exist_ok=True)

    readme_path = os.path.join(pending_dir, "_README.md")
    if not os.path.exists(readme_path):
        try:
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(
                    "# Consolidation Proposals\n\n"
                    "This folder contains automatically generated consolidation "
                    "proposal files created by `fact_consolidator.py`.\n\n"
                    "Each `CONSOLIDATION_*.md` file describes potential duplicates "
                    "or conflicts found in the live context entries, with a "
                    "recommended merge/supersede action.\n\n"
                    "**To use:** Review each proposal, apply or skip the "
                    "recommendation, then delete the proposal file.\n"
                )
            print("[CONSOLIDATOR] Created PENDING_DIR placeholder README.", flush=True)
        except OSError as e:
            print(f"[CONSOLIDATOR] Warning: could not write README: {e}", flush=True)


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


# Load persisted scan state on module import.
_load_scan_state()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Step 1: Scan live context entries
# ---------------------------------------------------------------------------

_SUMMARY_RE = re.compile(r"\*\*Summary:\*\*\s*(.+)", re.IGNORECASE)
_DATE_FROM_FILENAME_RE = re.compile(r"(?:CE|EX)_(\d{4}-\d{2}-\d{2})")
_CAT_CODE_RE = re.compile(r"(Cat\d{2}-[ER])", re.IGNORECASE)


def scan_context_entries() -> list[dict]:
    """Walk live context entry markdown files and parse their key fields.

    Scans the directory tree rooted at CONTEXT_ENTRIES_DIR, visiting folders
    that match the Cat##/Cat##-{E,R}/ layout.

    When CONSOLIDATION_INCLUDE_EXTRACTED is True, also includes EX_*.md files
    from the Extracted/ staging folder so the consolidator can flag duplicate
    auto-extracted facts before they are promoted to live CE_ entries.

    Returns:
        List of FactRecord dicts, sorted oldest-first within each category.
    """
    importlib.reload(cfg)
    entries_dir = cfg.CONTEXT_ENTRIES_DIR
    include_extracted = cfg.CONSOLIDATION_INCLUDE_EXTRACTED

    records = []
    category_counts: dict[str, int] = {}
    skip_dirs = {"Pending"}  # always skip proposal output dir
    if not include_extracted:
        skip_dirs.add("Extracted")

    for cat_dir in sorted(Path(entries_dir).iterdir()):
        if not cat_dir.is_dir() or cat_dir.name in skip_dirs:
            continue

        # Handle Extracted/ separately — flat structure (no Cat##-R subfolders)
        if cat_dir.name == "Extracted":
            for md_file in sorted(cat_dir.glob("EX_*.md")):
                record = _parse_entry_file(md_file, "Extracted", "R", entries_dir)
                if record:
                    records.append(record)
                    category_counts["Extracted"] = category_counts.get("Extracted", 0) + 1
            continue

        for subcat_dir in sorted(cat_dir.iterdir()):
            if not subcat_dir.is_dir():
                continue
            cat_match = _CAT_CODE_RE.match(subcat_dir.name)
            if not cat_match:
                continue
            category = cat_match.group(1)
            subject = category[-1]  # "E" or "R"

            for md_file in sorted(subcat_dir.glob("*.md")):
                record = _parse_entry_file(md_file, category, subject, entries_dir)
                if record:
                    records.append(record)
                    category_counts[category] = category_counts.get(category, 0) + 1

    print(
        f"[CONSOLIDATOR] Scanned {len(records)} context entry file(s) "
        f"across {len(category_counts)} category group(s):",
        flush=True,
    )
    for cat, count in sorted(category_counts.items()):
        print(f"  {cat}: {count} file(s)", flush=True)
    return records


def _parse_entry_file(
    path: Path, category: str, subject: str, entries_dir: str
) -> dict | None:
    """Parse a single context entry file into a FactRecord dict.

    Args:
        path:        Absolute Path to the .md file.
        category:    Category code from the containing folder name, e.g. "Cat05-R".
        subject:     "E" or "R".
        entries_dir: Root of the Context Entries tree (for rel_path computation).

    Returns:
        FactRecord dict or None if the file lacks a parseable summary.
    """
    # For EX_ files (from Extracted/ folder) the category is encoded in the
    # filename — EX_2024-04-25_Cat05-R.md — not the parent folder name.
    if category == "Extracted":
        cat_match = _CAT_CODE_RE.search(path.name)
        if cat_match:
            category = cat_match.group(1)
            subject = category[-1]
        else:
            return None  # No recognisable category in filename — skip

    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[CONSOLIDATOR] Could not read {path.name}: {e}", flush=True)
        return None

    # Extract summary line
    summ_match = _SUMMARY_RE.search(text)
    if not summ_match:
        return None
    summary = summ_match.group(1).strip()

    # Extract date from filename
    date_match = _DATE_FROM_FILENAME_RE.search(path.name)
    if date_match:
        try:
            entry_date = datetime.datetime.strptime(date_match.group(1), "%Y-%m-%d")
        except ValueError:
            entry_date = datetime.datetime.min
    else:
        entry_date = datetime.datetime.min

    # Infer cat number for sorting
    cat_num_match = re.search(r"Cat(\d{2})", category)
    cat_num = int(cat_num_match.group(1)) if cat_num_match else 0

    return {
        "path": str(path),
        "rel_path": str(path.relative_to(entries_dir)),
        "category": category,
        "cat_num": cat_num,
        "subject": subject,
        "date": entry_date,
        "summary": summary,
        "filename": path.name,
    }


# ---------------------------------------------------------------------------
# Step 2: Group entries and find conflict clusters
# ---------------------------------------------------------------------------


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
        new_clusters, new_recats = await _detect_clusters_in_group(
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


def _build_anchor_prompt(
    category: str,
    anchor: dict,
    comparison_window: list[dict],
    cat00: str,
) -> str:
    """Build the anchor-based cluster detection prompt.

    The anchor entry is always index [1]. Comparison entries follow as [2]..[N].
    This focused structure gives the LLM a specific yes/no question per call
    ("does this entry conflict with any of these?") rather than asking it to
    find all clusters among a crowd — a cleaner, less ambiguous task.

    Args:
        category:         Category code being audited, e.g. "Cat11-E".
        anchor:           The fixed FactRecord for this batch.
        comparison_window: Up to (CONSOLIDATION_MAX_RECORDS_PER_GROUP - 1)
                          other FactRecords to compare the anchor against.
        cat00:            Cat00 taxonomy text for category reference.

    Returns:
        Formatted prompt string.
    """
    def _fmt(r: dict, idx: int) -> str:
        date_str = (
            r["date"].strftime("%Y-%m-%d")
            if r["date"] != datetime.datetime.min
            else "unknown"
        )
        return f"\n[{idx}] {r['filename']} ({date_str})\n    Summary: {r['summary']}\n"

    anchor_text = _fmt(anchor, 1)
    comparison_text = "".join(_fmt(r, i + 2) for i, r in enumerate(comparison_window))
    cat_ref = f"\n\nCATEGORY REFERENCE:\n{cat00}" if cat00 else ""

    return (
        f"You are auditing one context memory entry against a comparison set "
        f"for category {category}. Your task has TWO parts:\n\n"
        "PART A — CLUSTER DETECTION: Compare the ANCHOR ENTRY [1] to the COMPARISON SET. "
        "Only flag entries for consolidation if they meet one of these three exact conditions:\n"
        "1. LITERAL REDUNDANCY: The entries describe the EXACT same specific fact.\n"
        "2. DIRECT SUPERSEDENCE: One entry provides a newer state of a mutable fact (e.g., a changed preference or location).\n"
        "3. FACTUAL ENHANCEMENT: The entries describe the exact same core fact about the same subject, and one adds a qualifying detail that refines it (e.g., 'Plays Skyrim' vs 'Plays Skyrim with heavily customized mods'). Do NOT flag entries that merely share a subject but describe different aspects of it.\n\n"
        "STRICT DISTINCTNESS (THE ATOMIC RULE):\n"
        "- Do NOT flag entries merely because they share a broad topic. Two entries may relate to food or hobbies but describe completely different items (e.g., 'Likes French Toast' vs 'Likes Bacon'). These are DISTINCT and must remain separate.\n"
        "- Do NOT group specific, distinct details into a general category summary.\n"
        "- When in doubt, do not flag the entry for consolidation.\n\n"
        "PART B — CATEGORY AUDIT: Does the ANCHOR ENTRY or any COMPARISON ENTRY "
        "primarily belong in a different category? Suggest recategorization ONLY "
        "when the entry's main subject matter fits another category better — not "
        "because it references or touches on another topic. Entries that relate to "
        "multiple categories stay in the category with the most weight; cross-category "
        "relevance is captured via Secondary links, not file moves."
        f"{cat_ref}\n\n"
        f"ANCHOR ENTRY (always [1]):\n{anchor_text}\n"
        f"COMPARISON ENTRIES:\n{comparison_text}\n"
        "Output ONLY a YAML block in this exact format:\n\n"
        "```yaml\n"
        "clusters:\n"
        "  - topic: \"brief topic label\"\n"
        "    entry_indices: [1, 3]  # must include [1] (the anchor) if involved\n"
        "    reason: \"why these are the same specific fact\"\n"
        "recategorize:\n"
        "  - entry_index: 2\n"
        "    suggested_category: Cat08-R\n"
        "    topic: \"brief topic label\"\n"
        "    reason: \"why primary weight belongs in the other category\"\n"
        "```\n\n"
        "If no clusters or recategorizations are needed, output empty lists."
    )


async def _detect_clusters_in_group(
    category: str, records: list[dict], cat00: str
) -> tuple[list[dict], list[dict]]:
    """Run one anchor-based detection call for a category.

    Uses _get_anchor_batch() to retrieve the current (anchor, comparison_window)
    pair from persisted state, then advances the state via _advance_scan_state()
    after the LLM call — whether or not it succeeded — to prevent getting stuck
    on a failing batch.

    Records must already be sorted oldest-first with filename tiebreaker
    (guaranteed by _group_by_category).

    Args:
        category: The category code being examined.
        records:  All FactRecords for this category, oldest-first.
        cat00:    Cat00 index text for category reference.

    Returns:
        Tuple of (clusters, recat_items): Cluster dicts and standalone
        recategorization dicts found in this anchor batch (may be empty lists).
    """
    anchor, comparison_window = _get_anchor_batch(category, records)

    if not comparison_window:
        # Edge case: only one entry in the category, or state is at boundary.
        _advance_scan_state(category, len(records))
        return [], []

    anchor_idx = records.index(anchor)
    print(
        f"[CONSOLIDATOR] {category}: anchor=[{anchor_idx}] '{anchor['filename']}' "
        f"vs {len(comparison_window)} comparison entry/ies.",
        flush=True,
    )

    prompt = _build_anchor_prompt(category, anchor, comparison_window, cat00)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a precise memory auditor. "
                "Output only the YAML block, nothing else."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    raw = await _call_ollama(
        messages,
        timeout=cfg.CONSOLIDATION_TIMEOUT,
        think=False,      # Classification task — no reasoning chain needed
        num_predict=512,  # YAML block only; no thinking trace to budget for
    )

    # Always advance state — prevents a persistent LLM failure on one batch
    # from blocking all future progress for this category.
    _advance_scan_state(category, len(records))

    if not raw:
        return [], []

    # Index resolution: combined list is [anchor] + comparison_window (1-based).
    # Anchor is always index [1] in the prompt → records[0] here.
    combined_records = [anchor] + comparison_window
    return _parse_cluster_yaml(raw, category, combined_records)


def _parse_cluster_yaml(raw: str, category: str, records: list[dict]) -> list[dict]:
    """Parse cluster detection output into Cluster dicts.

    Args:
        raw:      Raw model response string.
        category: Category being processed.
        records:  Combined list [anchor] + comparison_window as passed to the
                  prompt. Index [1] in the YAML → records[0] (the anchor).
                  Index [N] → records[N-1]. Resolution is purely 1-based.

    Returns:
        Tuple of (clusters, recat_items): lists of Cluster dicts and
        recategorization dicts respectively (both may be empty).
    """
    # Extract YAML block
    match = re.search(r"```(?:yaml)?\s*\n(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    block = match.group(1) if match else raw

    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as e:
        print(f"[CONSOLIDATOR] YAML parse error in cluster detection: {e}", flush=True)
        return [], []

    if not isinstance(data, dict):
        return [], []

    clusters = []

    # Process conflict clusters
    for c in data.get("clusters") or []:
        if not isinstance(c, dict):
            continue
        indices = c.get("entry_indices") or []
        topic = str(c.get("topic", "unknown topic")).strip()
        reason = str(c.get("reason", "")).strip()

        # Resolve 1-based indices to records
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

    # Parse standalone recategorization items — returned separately from clusters
    # and written as individual RECATEGORIZE_*.md files (no LLM call needed).
    recats = data.get("recategorize") or []
    recat_items = []
    for rc in recats:
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

    # If there are only recategorization items (no conflict clusters), return
    # just the recat list — they will be written as separate RECATEGORIZE_*.md
    # files without needing an LLM proposal call.
    return clusters, recat_items


# ---------------------------------------------------------------------------
# Step 3: Generate consolidation proposals
# ---------------------------------------------------------------------------


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
    topic = cluster["topic"]

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

    prompt = (
        f"You are consolidating memory entries for category {category}, topic: \"{topic}\".\n\n"
        f"ENTRIES:\n{entries_text}\n\n"
        f"TASK:\n"
        f"1. Analyze the entries above. Determine the most accurate current state of this fact.\n"
        f"2. {history_instruction}\n"
        f"3. Choose a verdict: 'supersede' (one entry replaces the other), 'merge' (synthesize overlapping granular details into one comprehensive fact), "
        f"or 'keep_both' (entries are genuinely distinct specific facts that should remain separate, e.g., two different breakfast foods).\n"
        f"4. If verdict is 'keep_both', set merged_summary to an empty string.\n"
        f"5. NEVER produce a merged_summary that abstracts specific facts into a vague category label. The output must be as specific as the input."
        f"{cat_ref}\n\n"
        "Output ONLY a YAML block:\n\n"
        "```yaml\n"
        "verdict: supersede   # supersede / merge / keep_both\n"
        "merged_summary: \"The consolidated fact as a single clear sentence.\"\n"
        "target_category: Cat05-R   # confirm or correct category; use same as input if correct\n"
        "reasoning: \"Brief explanation of the verdict.\"\n"
        "```"
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a careful memory consolidator. "
                "Reason through date ordering and semantic meaning before deciding. "
                "Output only the YAML block."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    raw = await _call_ollama(
        messages,
        timeout=cfg.CONSOLIDATION_TIMEOUT,
        think=True,        # Proposal genuinely needs reasoning
        num_predict=3072,  # Headroom for reasoning trace + YAML verdict
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
        "target_category": str(data.get("target_category", category)).strip(),
        "reasoning": str(data.get("reasoning", "")).strip(),
    }


def _write_proposal(cluster: dict, proposal: dict) -> str | None:
    """Write a human-readable consolidation proposal markdown file to Pending/.

    The proposal file contains strictly one action: merge or supersede the
    listed source entries. Recategorization suggestions are written as
    separate RECATEGORIZE_*.md files by _write_recategorization_proposal().

    The `source_date` frontmatter field is set to the most-recent date across
    all source entries. The reviewer script uses this to name the new CE_ file
    chronologically rather than using today's date.

    Args:
        cluster:  The Cluster dict describing the conflict group.
        proposal: Parsed verdict dict from _parse_proposal_yaml().

    Returns:
        Absolute path to the written file, or None on write failure.
    """
    importlib.reload(cfg)
    pending_dir = cfg.PENDING_DIR
    os.makedirs(pending_dir, exist_ok=True)

    now = datetime.datetime.now()
    timestamp = now.strftime("%Y-%m-%d_%H%M%S")
    filename = f"CONSOLIDATION_{timestamp}_{cluster['category']}.md"
    filepath = os.path.join(pending_dir, filename)

    category = cluster["category"]
    topic = cluster["topic"]
    verdict = proposal["verdict"]
    merged = proposal["merged_summary"]
    target_cat = proposal["target_category"]
    reasoning = proposal["reasoning"]
    records = cluster["records"]

    # Compute the most-recent source entry date for chronological CE_ naming.
    valid_dates = [
        r["date"] for r in records if r["date"] != datetime.datetime.min
    ]
    source_date = max(valid_dates).strftime("%Y-%m-%d") if valid_dates else now.strftime("%Y-%m-%d")

    # Build entries section
    entries_block = "\n".join(
        f"- `{r['filename']}` — {r['summary']}"
        for r in records
    )

    keep_history_note = (
        "*(History-preserving merge — fact evolution retained.)*"
        if cfg.CONSOLIDATION_KEEP_HISTORY
        else "*(Overwrite mode — older facts discarded.)*"
    )

    if verdict == "keep_both":
        action_section = (
            "## Verdict: Keep Both\n\n"
            "These entries represent genuinely distinct facts and should remain separate.\n\n"
            "*Action: Delete this proposal file to acknowledge.*"
        )
    else:
        verb = "Supersede" if verdict == "supersede" else "Merge"
        action_section = (
            f"## Proposed Summary\n\n"
            f"> {merged}\n\n"
            f"**Verdict:** {verb}  "
            f"**Target Category:** `{target_cat}`"
            + (
                f" *(correction from `{category}`)*"
                if target_cat != category else ""
            )
            + f"\n\n{keep_history_note}"
        )

    content = (
        f"---\n"
        f"tags: [consolidation-proposal]\n"
        f"created: {now.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"source_date: {source_date}\n"
        f"verdict: {verdict}\n"
        f"category: {category}\n"
        f"topic: \"{topic}\"\n"
        f"---\n\n"
        f"# Consolidation Proposal — {now.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"## Reasoning\n\n"
        f"**Flagged:** {cluster.get('reason', 'Duplicate or conflicting entries detected.')}\n\n"
        f"**Analysis:** {reasoning}\n\n"
        f"## Source Entries\n\n"
        f"{entries_block}\n\n"
        f"{action_section}\n"
    )

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[CONSOLIDATOR] Proposal written: {filename}", flush=True)
        return filepath
    except OSError as e:
        print(f"[CONSOLIDATOR] Failed to write proposal: {e}", flush=True)
        return None


# ---------------------------------------------------------------------------
# Step 3b: Write recategorization proposals (no LLM call needed)
# ---------------------------------------------------------------------------


def _write_recategorization_proposal(recat_item: dict) -> str | None:
    """Write a single-action recategorization proposal file to Pending/.

    The detection LLM already provides the suggested category and reason,
    so no additional LLM call is needed. The reviewer script handles the
    physical file move on approval.

    Args:
        recat_item: Dict with keys 'record', 'suggested_category', 'reason'.

    Returns:
        Absolute path to the written RECATEGORIZE_*.md file, or None on failure.
    """
    importlib.reload(cfg)
    pending_dir = cfg.PENDING_DIR
    os.makedirs(pending_dir, exist_ok=True)

    record = recat_item["record"]
    suggested = recat_item["suggested_category"]
    reason = recat_item["reason"]
    old_cat = record["category"]
    filename = record["filename"]
    old_path = record["path"]

    # Compute suggested new path — rename the category code in the filename too.
    cat_num = suggested[:5]  # "Cat12"
    new_filename = filename.replace(old_cat, suggested)
    new_rel = os.path.join(
        cfg.CONTEXT_ENTRIES_DIR, cat_num, suggested, new_filename
    )

    now = datetime.datetime.now()
    timestamp = now.strftime("%Y-%m-%d_%H%M%S")
    out_filename = f"RECATEGORIZE_{timestamp}_{old_cat}.md"

    # Derive source_date from the CE filename (e.g. CE_2025-07-25_Cat03-R.md → 2025-07-25)
    date_m = _DATE_FROM_FILENAME_RE.search(filename)
    source_date = date_m.group(1) if date_m else now.strftime("%Y-%m-%d")
    filepath = os.path.join(pending_dir, out_filename)

    content = (
        f"---\n"
        f"tags: [recategorize-proposal]\n"
        f"created: {now.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"source_date: {source_date}\n"
        f"current_cat: {old_cat}\n"
        f"suggested_cat: {suggested}\n"
        f"topic: \"{recat_item.get('topic', reason[:60])}\"\n"
        f"source_path: {old_path}\n"
        f"suggested_path: {new_rel}\n"
        f"---\n\n"
        f"# Recategorization Proposal — {now.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"## Reasoning\n\n"
        f"{reason}\n\n"
        f"## Entry\n\n"
        f"`{filename}`\n"
    )

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[CONSOLIDATOR] Recategorize proposal written: {out_filename}", flush=True)
        return filepath
    except OSError as e:
        print(f"[CONSOLIDATOR] Failed to write recategorize proposal: {e}", flush=True)
        return None

# ---------------------------------------------------------------------------
# Step 4: Top-level orchestration
# ---------------------------------------------------------------------------


async def _do_consolidation():
    """Core consolidation logic. Called by run_consolidation()."""
    importlib.reload(cfg)
    print("[CONSOLIDATOR] Starting idle-time consolidation pass...", flush=True)
    start = time.time()

    # Ensure the output folder exists before scanning — prevents sync tools
    # from removing an empty Pending/ between runs.
    _ensure_pending_dir()

    cat00 = load_cat00_index()
    records = scan_context_entries()

    if not records:
        print("[CONSOLIDATOR] No context entries found.", flush=True)
        return

    clusters, recat_items = await find_consolidation_candidates(records, cat00)

    # Write standalone recategorization proposals immediately — no LLM call needed.
    recats_written = 0
    for recat_item in recat_items:
        if _write_recategorization_proposal(recat_item):
            recats_written += 1

    if not clusters:
        print("[CONSOLIDATOR] No consolidation candidates found.", flush=True)
    
    proposals_written = 0
    for cluster in clusters:
        result = await generate_consolidation_proposal(cluster)
        if result:
            proposals_written += 1

    # Persist anchor scan state so the next idle pass continues from where
    # this one left off rather than restarting all categories from anchor=0.
    _save_scan_state()

    elapsed = time.time() - start
    print(
        f"[CONSOLIDATOR] Done. {proposals_written} consolidation proposal(s), "
        f"{recats_written} recategorization proposal(s) written in {elapsed:.1f}s.",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Shared Ollama helper (think=True for reasoning-quality consolidation)
# ---------------------------------------------------------------------------


async def _call_ollama(
    messages: list[dict],
    timeout: int = 60,
    think: bool = True,
    num_predict: int = 2048,
) -> str:
    """Generic non-streaming Ollama call for consolidator tasks, returns content string.

    Matches main model config to avoid VRAM eviction. The thinking trace (when
    enabled) is consumed server-side and not written to any file.

    Call sites should choose their own think/num_predict based on task type:
      - Detection (classification):  think=False, num_predict=512
        A structured yes/no task with a fixed YAML schema. No reasoning chain
        needed; the model just needs enough tokens to emit the YAML block.
      - Proposal generation (reasoning): think=True, num_predict=3072
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
