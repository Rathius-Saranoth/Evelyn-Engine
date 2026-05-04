"""
fact_consolidator.py — Idle-time context entry consolidation for Evelyn's memory system.

Runs during server idle time to scan live context entries for duplicates, contradictions,
and miscategorized facts. Uses the loaded LLM with thinking tokens enabled for nuanced
semantic reasoning. Produces human-readable consolidation proposal files in the Pending
folder — nothing is auto-applied to the live vault.

Architecture:
  - scan_context_entries()             — Walk all live Cat##/Cat##-{E,R}/*.md files
  - find_consolidation_candidates()    — Group by category, detect conflict clusters
  - generate_consolidation_proposal()  — LLM-driven merge + re-categorization reasoning
  - run_consolidation()                — Top-level coroutine for idle-time scheduling
  - cancel_pending_consolidation()     — Cancels any in-flight run (called on new chat)

Key behaviors:
  - CONSOLIDATION_KEEP_HISTORY (True)  — Preserve fact evolution in merged summaries
  - CONSOLIDATION_KEEP_HISTORY (False) — Overwrite with the most recent fact only
  - Category correction                — If a fact belongs in a different Cat##, the
    proposal includes the target path so the user can move/rename the file.
  - Think tokens                       — Enabled for consolidation reasoning calls so
    the model can weigh evidence carefully before reaching a verdict.
  - Cancellation                       — A new chat request immediately cancels any
    in-flight consolidation pass so Ollama is freed for the user's message.

All config is read from evelyn_config.py (single source of truth).
"""

import asyncio
import datetime
import importlib
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

# Rotating offset into the sorted group list.
# Each run advances by CONSOLIDATION_GROUP_SCAN_LIMIT so every category
# eventually gets scanned across multiple idle passes.
_group_start_index: int = 0

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
    """Walk all live context entry markdown files and parse their key fields.

    Scans the directory tree rooted at CONTEXT_ENTRIES_DIR, visiting folders
    that match the Cat##/Cat##-{E,R}/ layout. Skips the Extracted/ and
    Pending/ staging folders.

    Returns:
        List of FactRecord dicts, sorted oldest-first within each category.
    """
    importlib.reload(cfg)
    entries_dir = cfg.CONTEXT_ENTRIES_DIR

    records = []
    skip_dirs = {"Extracted", "Pending"}

    for cat_dir in sorted(Path(entries_dir).iterdir()):
        if not cat_dir.is_dir() or cat_dir.name in skip_dirs:
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

    print(f"[CONSOLIDATOR] Scanned {len(records)} context entry file(s).", flush=True)
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
    """Group FactRecords by category code, sorted oldest-first within each group."""
    groups: dict[str, list[dict]] = {}
    for r in records:
        groups.setdefault(r["category"], []).append(r)
    # Sort each group chronologically
    for key in groups:
        groups[key].sort(key=lambda r: r["date"])
    return groups


async def find_consolidation_candidates(
    records: list[dict], cat00: str
) -> list[dict]:
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
        List of Cluster dicts: {category, topic, records}.
    """
    global _group_start_index

    all_groups = _group_by_category(records)  # OrderedDict by category code
    group_items = list(all_groups.items())    # [(category, records), ...]
    total_groups = len(group_items)

    if total_groups == 0:
        return []

    # Wrap start index in case categories were added/removed since last run
    start = _group_start_index % total_groups

    # Rotate: take groups from `start` forward, wrapping around if needed
    rotated = group_items[start:] + group_items[:start]

    clusters: list[dict] = []
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
        new_clusters = await _detect_clusters_in_group(category, group_records, cat00)
        clusters.extend(new_clusters)

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
    return clusters


def _build_cluster_detection_prompt(
    category: str, records: list[dict], cat00: str
) -> str:
    """Build the prompt for identifying duplicate/conflicting topic clusters.

    Caps the number of records shown to CONSOLIDATION_MAX_RECORDS_PER_GROUP
    (newest-first) to prevent prompt overflow on high-volume categories.
    """
    max_records = cfg.CONSOLIDATION_MAX_RECORDS_PER_GROUP
    # Sort newest-first for truncation so we keep the most recent context
    sorted_records = sorted(records, key=lambda r: r["date"], reverse=True)
    truncated = sorted_records[:max_records]
    was_truncated = len(records) > max_records

    entries_text = ""
    for i, r in enumerate(truncated, 1):
        date_str = r["date"].strftime("%Y-%m-%d") if r["date"] != datetime.datetime.min else "unknown"
        entries_text += f"\n[{i}] {r['filename']} ({date_str})\n    Summary: {r['summary']}\n"

    if was_truncated:
        entries_text += (
            f"\n[Note: {len(records) - max_records} older entries omitted "
            f"(total {len(records)}). Showing newest {max_records} only.]"
        )

    cat_ref = f"\n\nCATEGORY REFERENCE:\n{cat00}" if cat00 else ""

    return (
        f"You are auditing context memory entries for category {category}. "
        "Your task has TWO parts:\n\n"
        "PART A — CLUSTER DETECTION: Identify which entries address the same topic "
        "and may be duplicates, contradictions, or superseded facts. "
        "Only flag clusters where consolidation would genuinely improve clarity. "
        "If entries are distinct facts, do NOT cluster them.\n\n"
        "PART B — CATEGORY AUDIT: Check whether any entry is miscategorized and "
        "would be better placed in a different category. Use the Category Reference below."
        f"{cat_ref}\n\n"
        f"ENTRIES IN {category}:\n{entries_text}\n\n"
        "Output ONLY a YAML block in this exact format:\n\n"
        "```yaml\n"
        "clusters:\n"
        "  - topic: \"brief topic label\"\n"
        "    entry_indices: [1, 3]  # 1-based indices from the list above\n"
        "    reason: \"why these conflict or overlap\"\n"
        "recategorize:\n"
        "  - entry_index: 2\n"
        "    suggested_category: Cat08-R\n"
        "    reason: \"why this better fits the other category\"\n"
        "```\n\n"
        "If no clusters or recategorizations are needed, output empty lists."
    )


async def _detect_clusters_in_group(
    category: str, records: list[dict], cat00: str
) -> list[dict]:
    """Ask the LLM to identify conflict clusters within a single category group.

    The records list passed here may be longer than CONSOLIDATION_MAX_RECORDS_PER_GROUP;
    the prompt builder handles truncation internally. Index resolution in
    _parse_cluster_yaml uses the same truncated slice the prompt was built from.

    Args:
        category: The category code being examined.
        records:  All FactRecords for this category, sorted oldest-first.
        cat00:    Cat00 index text for category reference.

    Returns:
        List of Cluster dicts found within this category.
    """
    # Truncate records for index resolution to match what the prompt sees
    max_records = cfg.CONSOLIDATION_MAX_RECORDS_PER_GROUP
    prompt_records = sorted(records, key=lambda r: r["date"], reverse=True)[:max_records]
    prompt = _build_cluster_detection_prompt(category, records, cat00)

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

    raw = await _call_ollama_think(messages, timeout=cfg.CONSOLIDATION_TIMEOUT)
    if not raw:
        return []

    # Pass prompt_records (the truncated slice) so index resolution is consistent
    return _parse_cluster_yaml(raw, category, prompt_records)


def _parse_cluster_yaml(raw: str, category: str, records: list[dict]) -> list[dict]:
    """Parse cluster detection output into Cluster dicts.

    Args:
        raw:      Raw model response string.
        category: Category being processed.
        records:  The truncated slice of FactRecords the prompt was built from
                  (1-based indices in the YAML correspond to this list).

    Returns:
        List of Cluster dicts (may be empty).
    """
    # Extract YAML block
    match = re.search(r"```(?:yaml)?\s*\n(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    block = match.group(1) if match else raw

    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as e:
        print(f"[CONSOLIDATOR] YAML parse error in cluster detection: {e}", flush=True)
        return []

    if not isinstance(data, dict):
        return []

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
                    "recategorize": [],
                }
            )

    # Attach recategorization hints to the appropriate cluster (or as standalone)
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
                "reason": str(rc.get("reason", "")).strip(),
            }
        )

    # If there are only recategorization items (no conflict clusters), wrap
    # them in a synthetic cluster so they still generate a proposal file.
    if recat_items and not clusters:
        clusters.append(
            {
                "category": category,
                "topic": "Category Audit",
                "reason": "Entries may be miscategorized.",
                "records": [ri["record"] for ri in recat_items],
                "recategorize": recat_items,
            }
        )
    elif recat_items and clusters:
        # Attach to first cluster for the proposal
        clusters[0]["recategorize"] = recat_items

    return clusters


# ---------------------------------------------------------------------------
# Step 3: Generate consolidation proposals
# ---------------------------------------------------------------------------


async def generate_consolidation_proposal(cluster: dict) -> str | None:
    """Ask the LLM to produce a merged summary and verdict for a cluster.

    Uses think=True so the model can reason carefully about conflicting facts,
    date ordering, and category correctness before committing to a verdict.

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

    recat_block = ""
    if cluster.get("recategorize"):
        recat_text = "\n".join(
            f"  - {rc['record']['filename']} → {rc['suggested_category']}: {rc['reason']}"
            for rc in cluster["recategorize"]
        )
        recat_block = (
            f"\n\nADDITIONAL TASK — CATEGORY CORRECTION:\n"
            f"The following entries may belong in a different category. "
            f"Confirm or refute each suggestion using the Category Reference below.\n"
            f"{recat_text}"
        )

    cat_ref = f"\n\nCATEGORY REFERENCE:\n{cat00}" if cat00 else ""

    prompt = (
        f"You are consolidating memory entries for category {category}, topic: \"{topic}\".\n\n"
        f"ENTRIES:\n{entries_text}\n\n"
        f"TASK:\n"
        f"1. Analyze the entries above. Determine the most accurate current state of this fact.\n"
        f"2. {history_instruction}\n"
        f"3. Choose a verdict: 'supersede' (one entry wins), 'merge' (combine insights), "
        f"or 'keep_both' (genuinely distinct facts that should remain separate).\n"
        f"4. If verdict is 'keep_both', set merged_summary to an empty string."
        f"{recat_block}"
        f"{cat_ref}\n\n"
        "Output ONLY a YAML block:\n\n"
        "```yaml\n"
        "verdict: supersede   # supersede / merge / keep_both\n"
        "merged_summary: \"The consolidated fact as a single clear sentence.\"\n"
        "target_category: Cat05-R   # confirm or correct category; use same as input if correct\n"
        "reasoning: \"Brief explanation of the verdict and any category correction.\"\n"
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

    raw = await _call_ollama_think(messages, timeout=cfg.CONSOLIDATION_TIMEOUT)
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

    # Build entries section
    entries_block = "\n".join(
        f"- `{r['filename']}` — {r['summary']}"
        for r in records
    )

    # Build category correction section (if applicable)
    recat_section = ""
    if cluster.get("recategorize"):
        recat_lines = []
        for rc in cluster["recategorize"]:
            old_cat = rc["record"]["category"]
            new_cat = rc["suggested_category"]
            fname = rc["record"]["filename"]
            old_path = rc["record"]["path"]

            # Compute what the new path would be (for user convenience)
            # Pattern: Context Entries/Cat##/Cat##-{E,R}/filename
            entries_dir = cfg.CONTEXT_ENTRIES_DIR
            cat_num = new_cat[:5]  # "Cat05"
            new_rel = os.path.join(entries_dir, cat_num, new_cat, fname)

            recat_lines.append(
                f"- **{fname}**\n"
                f"  - Current: `{old_cat}` → `{old_path}`\n"
                f"  - Suggested: `{new_cat}` → `{new_rel}`\n"
                f"  - Reason: {rc['reason']}"
            )
        recat_section = (
            "\n## Category Correction Suggestions\n\n"
            + "\n".join(recat_lines)
            + "\n\n*To apply: move the file to the suggested path and update its **Primary:** line.*"
        )

    # Build merged summary section
    if verdict == "keep_both":
        action_section = (
            "## Verdict: Keep Both\n\n"
            "These entries represent genuinely distinct facts and should remain separate.\n\n"
            "*Action: Delete this proposal file to acknowledge.*"
        )
    else:
        verb = "Supersede" if verdict == "supersede" else "Merge"
        action_section = (
            f"## Verdict: {verb}\n\n"
            f"**Proposed Merged Summary:**\n> {merged}\n\n"
            f"**Target Category:** `{target_cat}`"
            + (
                f" *(correction from `{category}`)*"
                if target_cat != category else ""
            )
            + "\n\n"
            "*Action Required:*\n"
            "1. Review and edit the merged summary above if needed.\n"
            "2. Create a new `CE_` entry in the target category with the merged summary.\n"
            "3. Delete the source entries listed above.\n"
            "4. Delete this proposal file when done.\n"
            "*(Or delete this file to skip this consolidation.)*"
        )

    keep_history_note = (
        "*(History-preserving merge — fact evolution retained.)*"
        if cfg.CONSOLIDATION_KEEP_HISTORY
        else "*(Overwrite mode — older facts discarded.)*"
    )

    content = (
        f"---\n"
        f"tags: [consolidation-proposal]\n"
        f"created: {now.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"---\n\n"
        f"# Consolidation Proposal — {now.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"**Category:** `{category}` | **Topic:** {topic}\n\n"
        f"**Reason flagged:** {cluster.get('reason', 'Duplicate or conflicting entries detected.')}\n\n"
        f"{keep_history_note}\n\n"
        f"## Source Entries\n\n"
        f"{entries_block}\n\n"
        f"## Reasoning\n\n"
        f"{reasoning}\n\n"
        f"{action_section}"
        f"{recat_section}\n"
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
# Step 4: Top-level orchestration
# ---------------------------------------------------------------------------


async def _do_consolidation():
    """Core consolidation logic. Called by run_consolidation()."""
    importlib.reload(cfg)
    print("[CONSOLIDATOR] Starting idle-time consolidation pass...", flush=True)
    start = time.time()

    cat00 = load_cat00_index()
    records = scan_context_entries()

    if not records:
        print("[CONSOLIDATOR] No context entries found.", flush=True)
        return

    clusters = await find_consolidation_candidates(records, cat00)

    if not clusters:
        print("[CONSOLIDATOR] No consolidation candidates found.", flush=True)
        return

    proposals_written = 0
    for cluster in clusters:
        result = await generate_consolidation_proposal(cluster)
        if result:
            proposals_written += 1

    elapsed = time.time() - start
    print(
        f"[CONSOLIDATOR] Done. {proposals_written} proposal(s) written in {elapsed:.1f}s.",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Shared Ollama helper (think=True for reasoning-quality consolidation)
# ---------------------------------------------------------------------------


async def _call_ollama_think(messages: list[dict], timeout: int = 60) -> str:
    """Non-streaming Ollama call with think=True, returns content string.

    Matches main model config to avoid VRAM eviction. The thinking trace is
    consumed server-side and not written to any file.

    Args:
        messages: List of {role, content} dicts.
        timeout:  Request timeout in seconds.

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
    options["num_predict"] = 768  # Consolidation output is bounded
    if cfg.STOP_SEQUENCES:
        options["stop"] = cfg.STOP_SEQUENCES

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": options,
        "think": True,  # Deliberate — consolidation requires careful reasoning
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{cfg.OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
            result = resp.json()
        content = result.get("message", {}).get("content", "").strip()
        thinking = result.get("message", {}).get("thinking", "")
        if thinking and cfg.DEBUG_LOGGING:
            print(
                f"[CONSOLIDATOR] Think trace ({len(thinking.split())} words): "
                f"{thinking[:300]}...",
                flush=True,
            )
        return content
    except Exception as e:
        print(f"[CONSOLIDATOR] Ollama call failed: {e}", flush=True)
        return ""
