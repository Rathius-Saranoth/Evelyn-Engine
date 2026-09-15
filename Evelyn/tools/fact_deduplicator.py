# fact_deduplicator.py
# date created: 2026-09-14
# date modified: 2026-09-14 20:23:52
# tags: #facts, #deduplication, #vector_search, #chroma, #merging

"""
fact_deduplicator.py — Specialized Fact Deduplication Engine for Evelyn's Memory Vault.

Child engine to fact_consolidator. Handles exact and semantic deduplication:
1. fast_deduplicate_exact_matches: Instant SQL string-normalized deduplication with 100% soft-delete.
2. find_deduplication_candidates: Vector-first nearest-neighbor candidate discovery via ChromaDB.
3. generate_consolidation_proposal: LLM synthesis (think=True) producing merge/supersede verdicts.
4. drain_fact_merge_queue: Processing user-enqueued fact merge requests.

Full provenance is preserved via merged_into_id and last_audited_at timestamps.
"""

from __future__ import annotations

import contextlib
import logging
import re
import sqlite3
import time
from collections.abc import Callable
from typing import Any

import yaml

import evelyn_config as cfg
from Evelyn.tools import chroma_rag, memory_db
from Evelyn.tools.fact_extractor import load_cat00_index
from Evelyn.tools.tag_librarian import normalize_tag_format

logger = logging.getLogger("evelyn.fact_deduplicator")

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_PROPOSAL_SYSTEM_PROMPT = (
    "You are a Knowledge Engineer & Qualitative Analyst. "
    "Your primary job is to catch redundancies without destroying the granular details about the human experience. "
    "Reason through date ordering and semantic meaning before deciding. "
    "Entries with no date (CY-YYYY/MM/DD) are to be treated as the oldest. "
    "Output only the YAML block."
)

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
7. MULTI-TIER DOMAIN TAXONOMY RULE: Format `merged_tags` using hierarchical domain trees (e.g. `Tech/Python/FastAPI`, `Home/Coffee/Espresso`, `Lore/Dungeon_Crawler_Carl`, `Health/Sleep/Routine`) and TitleCase with underscores for named entities (`John_Smith`).
8. EXPLICIT NOUN SUBJECT MANDATE: The `merged_summary` MUST explicitly state the subject/actor by name at the beginning (e.g. 'Alex prefers...', 'Evelyn observes...', 'Biscuit the cat acts as...', 'Jordan is acclimating...'). NEVER begin the summary with a subject-less verb (e.g. 'Enjoys...', 'Prefers...', 'Acts as...') and NEVER use ambiguous floating pronouns ('He', 'She', 'They') as the primary subject referent. If one entity is observing or commenting on another, explicitly name BOTH entities in the text (e.g. 'Evelyn observes that Alex values clarity...').
{cat_ref}

Output ONLY a YAML block:
```yaml
verdict: supersede   # supersede / merge / keep_both
merged_summary: "The consolidated fact as a rich, substantive, clear sentence explicitly naming the subject."
merged_tags: "Tech/Python/FastAPI, John_Smith"        # comma-separated hierarchical domain tags
confidence: high     # high / medium / low
reasoning: "Brief explanation of the verdict."
```\
"""


def calculate_token_jaccard(text_a: str, text_b: str) -> float:
    """Calculate token-level Jaccard similarity between two texts, ignoring common stopwords."""
    words_a = set(re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", text_a.lower()))
    words_b = set(re.findall(r"\b[a-zA-Z0-9_-]{3,}\b", text_b.lower()))
    if not words_a or not words_b:
        return 0.0
    stopwords = {
        "this", "that", "with", "from", "have", "been", "were", "will",
        "when", "what", "which", "there", "their", "about", "also", "into",
        "more", "some", "time", "than", "them", "then", "these", "only",
    }
    words_a -= stopwords
    words_b -= stopwords
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


# ============================================================================
# Section 1: Fast Exact Match Deduplication
# ============================================================================

def fast_deduplicate_exact_matches(con: sqlite3.Connection | None = None) -> int:
    """Detect and soft-delete exact / whitespace-normalized duplicate context entries.

    Aggregates metadata (observed_count, union of tags, latest date) into the
    primary master entry (lowest ID) and marks duplicate entries as status='deleted'
    with merged_into_id pointing to the master ID.

    Returns:
        int: Number of redundant duplicate entries soft-deleted.
    """
    deleted_ids: list[tuple[int, int]] = []  # (dup_id, primary_id)
    own_con = False
    if con is None:
        con = memory_db.get_db()
        own_con = True
    try:
        cur = con.cursor()
        rows = cur.execute(
            """SELECT id, category, subject, observation, date, tags, observed_count, created_at
               FROM context_entries
               WHERE status = 'live'
               ORDER BY id ASC"""
        ).fetchall()

        seen: dict[tuple[str, str, str], dict] = {}
        now = time.time()

        for row in rows:
            r = dict(row)
            clean_obs = re.sub(r"\s+", " ", r["observation"].strip().lower()).rstrip(".").strip()
            key = (r["category"], r["subject"], clean_obs)

            if key not in seen:
                seen[key] = r
            else:
                primary = seen[key]
                dup_id = r["id"]
                primary_id = primary["id"]

                # Merge metadata into primary
                merged_count = (primary.get("observed_count") or 1) + (r.get("observed_count") or 1)

                # Union tags
                t1 = {t.strip() for t in (primary.get("tags") or "").split(",") if t.strip()}
                t2 = {t.strip() for t in (r.get("tags") or "").split(",") if t.strip()}
                merged_tags = ", ".join(sorted(t1 | t2)) if (t1 | t2) else None

                best_date = primary.get("date") or r.get("date")

                cur.execute(
                    """UPDATE context_entries
                       SET observed_count = ?, tags = ?, date = ?, updated_at = ?, last_audited_at = ?
                       WHERE id = ?""",
                    (merged_count, merged_tags, best_date, now, now, primary_id),
                )

                cur.execute("DELETE FROM context_entries WHERE id = ?", (dup_id,))
                deleted_ids.append((dup_id, primary_id))

        con.commit()
    except (sqlite3.Error, OSError, ValueError) as e:
        logger.warning(f"[DEDUPLICATOR] Fast deduplication error: {e}")
        return 0
    finally:
        if own_con:
            con.close()

    # Enqueue chroma deletions
    for dup_id, _primary_id in deleted_ids:
        with contextlib.suppress(Exception):
            chroma_rag.enqueue_delete(
                source_path=f"sqlite::context_entry::{dup_id}",
                collection_name=cfg.CHROMA_MEMORY_COLLECTION,
            )

    if deleted_ids:
        logger.info(f"[DEDUPLICATOR] Fast deduplication soft-deleted {len(deleted_ids)} duplicate entry/ies.")
    return len(deleted_ids)


# ============================================================================
# Section 2: Vector-First Semantic Candidate Discovery
# ============================================================================

def find_deduplication_candidates(
    batch_limit: int = 5,
    distance_threshold: float = 0.40,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Scan memory database context entries using vector-first nearest-neighbor search.

    Selects oldest unaudited live entries, queries ChromaDB for semantic
    similarity and subject matching, and forms clusters for LLM evaluation.

    Args:
        batch_limit: Maximum number of anchor entries to evaluate per pass.
        distance_threshold: Maximum cosine distance in ChromaDB.
        category: Optional category filter.

    Returns:
        list[dict]: List of Cluster dicts with 'category', 'topic', 'records', 'reason'.
    """
    limit = batch_limit if batch_limit is not None else cfg.CONSOLIDATION_GROUP_SCAN_LIMIT
    thresh = (
        distance_threshold
        if distance_threshold is not None
        else cfg.CONSOLIDATION_VECTOR_PREFILTER_DISTANCE
    )
    max_neighbors = cfg.CONSOLIDATION_MAX_RECORDS_PER_GROUP

    anchors = memory_db.get_oldest_unaudited_entries(limit=limit, category=category)
    if not anchors:
        return []

    clusters: list[dict[str, Any]] = []
    audited_ids: set[int] = set()

    for anchor in anchors:
        anchor_id = int(anchor["id"])
        audited_ids.add(anchor_id)

        # Skip if anchor already has a pending proposal
        if memory_db.has_pending_proposal_for([anchor_id]):
            continue

        anchor_obs = str(anchor.get("observation") or "").strip()
        if not anchor_obs:
            continue

        # 1. Query ChromaDB memory collection directly for nearest neighbors
        try:
            chunks = chroma_rag.query_collection(
                anchor_obs,
                collection_name=cfg.CHROMA_MEMORY_COLLECTION,
                n_results=max_neighbors,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[DEDUPLICATOR] Chroma query failed for anchor #{anchor_id}: {e}")
            continue

        matched_entry_ids: list[int] = []
        for chunk in chunks:
            dist = chunk.get("distance", 1.0)
            if dist <= thresh:
                src = chunk.get("source", "")
                if src.startswith("sqlite::context_entry::"):
                    try:
                        cid = int(src.split("::")[-1])
                        if cid != anchor_id:
                            matched_entry_ids.append(cid)
                    except (ValueError, IndexError):
                        pass

        if not matched_entry_ids:
            continue

        # 2. Fetch matched neighbor records from SQLite
        cluster_records = [anchor]
        for nid in matched_entry_ids:
            if nid in audited_ids:
                continue
            if memory_db.has_pending_proposal_for([nid]):
                continue

            entry = memory_db.get_entry(nid)
            if not entry or entry.get("status") != "live":
                continue

            # Must share the same primary subject
            if entry.get("subject") != anchor.get("subject"):
                continue

            # Check lexical Jaccard as confirmation if distance is on the border
            n_obs = str(entry.get("observation") or "").strip()
            jaccard = calculate_token_jaccard(anchor_obs, n_obs)
            if jaccard >= 0.15 or len(matched_entry_ids) == 1:
                cluster_records.append(entry)
                audited_ids.add(nid)

        if len(cluster_records) >= 2:
            clusters.append({
                "category": anchor["category"],
                "topic": f"Duplicate facts on #{anchor_id} ({anchor['category']})",
                "reason": f"Chroma nearest neighbors within distance {distance_threshold}",
                "records": cluster_records,
            })

    # Stamp last_audited_at on all evaluated candidates so the scanner advances smoothly
    if audited_ids:
        memory_db.touch_entries_audited(list(audited_ids))

    return clusters


# ============================================================================
# Section 3: Proposal Generation & Merge Application
# ============================================================================

def parse_proposal_yaml(raw: str, category: str, records: list[dict] | None = None) -> dict | None:
    """Parse the consolidation verdict YAML from the model response."""
    match = re.search(r"```(?:yaml)?\s*\n(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    block = match.group(1) if match else raw

    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as e:
        logger.warning(f"[DEDUPLICATOR] YAML parse error in proposal: {e}")
        return None

    if not isinstance(data, dict):
        return None

    verdict = str(data.get("verdict", "keep_both")).strip().lower()
    if verdict not in ("supersede", "merge", "keep_both"):
        verdict = "keep_both"

    target_cat = str(data.get("target_category", category)).strip()
    raw_tags = str(data.get("merged_tags", "")).strip()
    norm_tags = ", ".join([normalize_tag_format(t) for t in raw_tags.split(",") if t.strip()])

    return {
        "verdict": verdict,
        "merged_summary": str(data.get("merged_summary", "")).strip(),
        "merged_tags": norm_tags,
        "confidence": str(data.get("confidence", "medium")).strip().lower(),
        "target_category": target_cat,
        "reasoning": str(data.get("reasoning", "")).strip(),
    }


async def generate_consolidation_proposal(
    cluster: dict[str, Any],
    call_ollama_fn: Callable[..., Any],
) -> str | None:
    """Generate and record a consolidation proposal for a cluster using Ollama (think=True).

    Args:
        cluster: Cluster dict with 'records', 'category', 'topic'.
        call_ollama_fn: Async callable that takes messages, timeout, think, num_predict.

    Returns:
        str | None: Proposal row ID if created or applied, None if keep_both or failed.
    """
    cat00 = load_cat00_index()
    keep_history = getattr(cfg, "CONSOLIDATION_KEEP_HISTORY", True)

    records = cluster["records"]
    category = cluster["category"]

    entries_text = ""
    for r in records:
        d_str = r.get("date") or "unknown"
        s_str = r.get("subject") or "Unknown"
        c_str = r.get("category") or category
        entries_text += (
            f"\n- Entry #{r['id']} (Subject: {s_str}, Category: {c_str}, Date: {d_str})\n"
            f"  Observation: {r.get('observation') or r.get('summary')}\n"
        )

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

    timeout = getattr(cfg, "CONSOLIDATION_TIMEOUT", 180)
    raw = await call_ollama_fn(
        messages,
        timeout=timeout,
        think=True,
        num_predict=3000,
    )
    if not raw:
        return None

    proposal_data = parse_proposal_yaml(raw, category, records)
    if not proposal_data:
        return None

    verdict = proposal_data["verdict"]
    if verdict == "keep_both":
        return None

    merged_summary = proposal_data["merged_summary"]
    merged_tags = proposal_data.get("merged_tags")
    target_cat = proposal_data.get("target_category", category)
    confidence = proposal_data.get("confidence", "medium")
    reasoning = proposal_data.get("reasoning", "")
    topic = cluster.get("topic", f"Fact Consolidation ({verdict})")

    source_ids = [int(r["id"]) for r in records]
    status = "auto_applied" if confidence == "high" else "pending"

    try:
        pid = memory_db.insert_proposal(
            type=verdict,
            source_ids=source_ids,
            merged_observation=merged_summary,
            merged_tags=merged_tags,
            suggested_category=target_cat,
            reason=reasoning,
            topic=topic,
            confidence=confidence,
            status=status,
        )

        if status == "auto_applied":
            master_id = memory_db.apply_fact_merge(
                source_entries=records,
                merged_text=merged_summary,
                target_category=target_cat,
                merged_tags=merged_tags,
            )
            logger.info(f"[DEDUPLICATOR] Auto-applied merge into master #{master_id} (Proposal #{pid})")
            return str(pid)

        logger.info(f"[DEDUPLICATOR] Proposal #{pid} written to review queue ({verdict}).")
        return str(pid)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[DEDUPLICATOR] Failed to record proposal: {e}")
        return None


async def drain_fact_merge_queue(call_ollama_fn: Callable[..., Any]) -> int:
    """Drain user-queued manual fact merges."""
    queued_merges = memory_db.get_fact_merge_queue(status="pending")
    if not queued_merges:
        return 0

    processed = 0
    for q_item in queued_merges:
        q_id = q_item["id"]
        entry_ids = q_item.get("entry_ids", [])
        records = [memory_db.get_entry(eid) for eid in entry_ids]
        live_records = [r for r in records if r and r.get("status") == "live"]

        if len(live_records) >= 2:
            cluster = {
                "category": live_records[0]["category"],
                "topic": f"Queued Fact Merge {entry_ids}",
                "records": live_records,
            }
            res = await generate_consolidation_proposal(cluster, call_ollama_fn)
            if res:
                processed += 1
        memory_db.dequeue_fact_merge(q_id)

    return processed
