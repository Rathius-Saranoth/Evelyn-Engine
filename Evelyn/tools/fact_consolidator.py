# fact_consolidator.py
# date created: 2026-05-03 18:07:33
# date modified: 2026-09-15 17:23:43
# tags: #facts, #consolidation, #deduplication, #orchestrator, #governance

"""
fact_consolidator.py — Idle-Time Fact Consolidation & Memory Governance Orchestrator.

Orchestrates idle-time memory maintenance across specialized child engines:
1. fact_deduplicator: Fast exact deduplication and ChromaDB vector-driven nearest-neighbor clustering.
2. fact_splitter: Decomposition of compound/bloated context entries (>35 words) into atomic facts.
3. fact_categorizer: Taxonomy placement audits against Cat00 with anti-flip-flop hysteresis.

Exports:
  run_consolidation()              — Top-level coroutine; called from the server idle loop.
  cancel_pending_consolidation()   — Called on each new chat request to free Ollama.
  find_consolidation_candidates()  — Legacy facade delegating to fact_deduplicator.
  generate_consolidation_proposal() — Legacy facade delegating to fact_deduplicator.
  generate_split_proposal()        — Legacy facade delegating to fact_splitter.
  fast_deduplicate_exact_matches() — Legacy facade delegating to fact_deduplicator.
  validate_and_normalize_category() — Legacy facade delegating to fact_categorizer.
  remediate_database_categories()  — Legacy facade delegating to fact_categorizer.
  scan_context_entries()           — Fetch all live FactRecords from SQLite.

Key config: evelyn_config.py (CONSOLIDATION_*, THINK, NUM_CTX)
"""

from __future__ import annotations

import asyncio
import datetime
import importlib
import json
import logging
import re
import sqlite3
import time

import httpx

import evelyn_config as cfg
from Evelyn.tools import (
    fact_categorizer,
    fact_deduplicator,
    fact_extractor,
    fact_splitter,
    memory_db,
)
from Evelyn.tools.fact_categorizer import (
    remediate_database_categories,
)
from Evelyn.tools.fact_categorizer import (
    validate_and_normalize_category as validate_and_normalize_category,
)
from Evelyn.tools.fact_deduplicator import (
    fast_deduplicate_exact_matches,
)
from Evelyn.tools.fact_extractor import load_cat00_index
from Evelyn.tools.fact_splitter import generate_split_proposal as generate_split_proposal

logger = logging.getLogger("evelyn.fact_consolidator")

DATETIME_MIN = datetime.datetime.min.replace(tzinfo=datetime.UTC)
_background_tasks: set[asyncio.Task] = set()

_consolidating = False
_last_run_ts: float = 0.0
_group_start_index: int = 0
_consolidation_task: asyncio.Task | None = None


# ============================================================================
# Section 0 — Infrastructure & State
# ============================================================================

def _extracting_elsewhere() -> bool:
    """Check if fact_extractor is currently running an LLM call."""
    try:
        return bool(fact_extractor._extracting)
    except AttributeError:
        return False


def _heavy_tasks_running() -> bool:
    """Check if any heavy server background task is running."""
    import task_manager
    return task_manager.is_any_running(exclude="consolidator")


def _set_status_in_server(
    status: str | None,
    error: str | None = None,
    summary: str | None = None,
    sub_status: dict | None = None,
    diagnostics: dict | None = None,
    phase: str | None = None,
    items_processed: int = 0,
) -> None:
    """Register or clear consolidator status in the server's central registry."""
    import task_manager
    if status == "running":
        task_manager.set_running("consolidator", phase=phase, sub_status=sub_status, diagnostics=diagnostics)
    else:
        task_manager.clear_running(
            "consolidator",
            status=status or "idle",
            error=error,
            summary=summary,
            sub_status=sub_status,
            diagnostics=diagnostics,
            items_processed=items_processed,
        )


async def _call_ollama(
    messages: list[dict],
    timeout: int = 60,
    think: bool = True,
    num_predict: int = 1000,
) -> str:
    """Canonical streaming Ollama call for consolidator tasks, returns content string."""
    importlib.reload(cfg)
    override = getattr(cfg, "SUMMARY_MODEL_OVERRIDE", "default")
    model = cfg.MODEL_NAME if override == "default" else override

    options = {
        "num_ctx": cfg.NUM_CTX,
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
        "num_predict": num_predict,
    }

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": options,
        "think": think,
    }

    content_buffer = ""

    try:
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream("POST", f"{cfg.OLLAMA_URL}/api/chat", json=payload) as resp,
        ):
            resp.raise_for_status()
            aiter = resp.aiter_lines()
            while True:
                try:
                    line = await asyncio.wait_for(aiter.__anext__(), timeout=120.0)
                except StopAsyncIteration:
                    break
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                    msg = chunk.get("message", {})
                    content_buffer += msg.get("content", "")
                except json.JSONDecodeError:
                    continue

        return content_buffer.strip()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[CONSOLIDATOR] Ollama call failed: {e}")
        return ""


def cancel_pending_consolidation() -> None:
    """Cancel any currently running consolidation task to release Ollama."""
    global _consolidating, _consolidation_task
    if _consolidation_task and not _consolidation_task.done():
        logger.info("[CONSOLIDATOR] Preempting background consolidation pass for incoming chat.")
        _consolidation_task.cancel()
        _consolidation_task = None
        _consolidating = False
        _set_status_in_server("idle", summary="Consolidation pass cancelled (chat preemption)")
    _consolidating = False


def _backup_memory_db() -> None:
    """Create a rolling hot-copy of evelyn_memory.db before any consolidation mutations."""
    src_path = cfg.MEMORY_DB_PATH
    bak_path = src_path + ".bak"
    try:
        src_con = sqlite3.connect(src_path)
        bak_con = sqlite3.connect(bak_path)
        src_con.backup(bak_con)
        bak_con.close()
        src_con.close()
    except (sqlite3.Error, OSError) as e:
        logger.warning(f"[CONSOLIDATOR] DB backup warning (non-fatal): {e}")


def scan_context_entries() -> list[dict]:
    """Fetch all live context entries from SQLite and format as FactRecords."""
    statuses = ["live"]
    importlib.reload(cfg)
    if getattr(cfg, "CONSOLIDATION_INCLUDE_EXTRACTED", True):
        statuses.append("extracted")

    rows = memory_db.get_all_entries(statuses=statuses)
    records = []

    for row in rows:
        cat = row["category"]
        cat_num_match = re.search(r"Cat(\d{2})", cat)
        cat_num = int(cat_num_match.group(1)) if cat_num_match else 0

        try:
            entry_date = (
                datetime.datetime.strptime(row["date"], "%Y-%m-%d").replace(tzinfo=datetime.UTC)
                if row.get("date")
                else DATETIME_MIN
            )
        except ValueError:
            entry_date = DATETIME_MIN

        records.append({
            "id": row["id"],
            "path": str(row["id"]),
            "rel_path": str(row["id"]),
            "category": cat,
            "cat_num": cat_num,
            "subject": row["subject"],
            "date": entry_date,
            "summary": row["observation"],
            "observation": row["observation"],
            "filename": f"Entry_{row['id']}",
        })

    return records


# ============================================================================
# Section 1 — Facade Exports for Backwards Compatibility
# ============================================================================

async def generate_consolidation_proposal(cluster: dict) -> str | None:
    """Generate consolidation proposal delegating to fact_deduplicator."""
    return await fact_deduplicator.generate_consolidation_proposal(cluster, _call_ollama)


# ============================================================================
# Section 2 — Orchestration Pipeline
# ============================================================================

async def _do_consolidation() -> None:
    """Execute the multi-phase memory consolidation pass."""
    importlib.reload(cfg)
    logger.info("[CONSOLIDATOR] Starting modular consolidation pass...")

    _backup_memory_db()
    remediate_database_categories()

    # Step 1: Fast exact match deduplication (instant SQL merge & soft-delete)
    dupes_collapsed = fast_deduplicate_exact_matches()

    # Step 2: Drain user-queued manual merges
    queued_merges_count = await fact_deduplicator.drain_fact_merge_queue(_call_ollama)

    # Step 3: Vector-First Semantic Deduplication
    records = scan_context_entries()
    if not records:
        _set_status_in_server("idle", summary="No active context entries found to scan.")
        return

    _set_status_in_server(
        "running",
        phase="vector_deduplication",
        sub_status={
            "total_active_facts": len(records),
            "dupes_collapsed": dupes_collapsed,
            "queued_merges": queued_merges_count,
        },
    )

    clusters = fact_deduplicator.find_deduplication_candidates(
        batch_limit=getattr(cfg, "CONSOLIDATION_BATCH_SIZE", 6),
        distance_threshold=getattr(cfg, "CONSOLIDATION_VECTOR_PREFILTER_DISTANCE", 0.40),
    )

    merges_applied = 0
    for cluster in clusters:
        res = await fact_deduplicator.generate_consolidation_proposal(cluster, _call_ollama)
        if res:
            merges_applied += 1

    # Step 4: Fact Splitting / Decomposition
    cat00 = load_cat00_index()
    splits_processed = await fact_splitter.process_split_queue(cat00, _call_ollama)
    auto_splits = 0
    if getattr(cfg, "CONSOLIDATION_SPLIT_ENABLED", True):
        threshold = getattr(cfg, "CONSOLIDATION_SPLIT_WORD_THRESHOLD", 35)
        auto_splits = await fact_splitter.find_and_split_bloated_facts(
            records, cat00, _call_ollama, word_threshold=threshold, limit=3
        )

    # Step 5: Safe Category Auditing (with anti-flip-flop protection)
    recats_applied = 0
    if records:
        # Sample candidates from active categories
        sample_batch = records[:10]
        if sample_batch:
            applied = await fact_categorizer.audit_category_classifications(
                sample_batch[0]["category"], sample_batch, cat00, _call_ollama, limit=5
            )
            recats_applied = len(applied)

    # Collect aggregate telemetry
    metrics = memory_db.get_fact_deduplication_metrics()
    summary = (
        f"Consolidation pass complete: {dupes_collapsed} exact dupe(s) collapsed, "
        f"{merges_applied} semantic merge(s) applied, "
        f"{splits_processed + auto_splits} split(s), "
        f"{recats_applied} recat(s)."
    )
    logger.info(f"[CONSOLIDATOR] {summary}")

    _set_status_in_server(
        "idle",
        items_processed=len(records),
        summary=summary,
        sub_status={
            "total_active_facts": metrics["live_facts"],
            "total_merged_facts": metrics["merged_facts"],
            "exact_collapsed_count": dupes_collapsed,
            "pending_proposals": metrics["pending_merges"],
            "last_run_scanned": len(records),
            "clusters_evaluated": len(clusters),
            "merges_applied": merges_applied,
            "splits_applied": splits_processed + auto_splits,
            "recats_applied": recats_applied,
        },
    )


async def run_consolidation() -> None:
    """Entrypoint called from server background loop when system is idle."""
    global _consolidating, _last_run_ts, _consolidation_task

    if not getattr(cfg, "CONSOLIDATION_ENABLED", True):
        return

    if _consolidating:
        return

    if _extracting_elsewhere() or _heavy_tasks_running():
        return

    now = time.time()
    cooldown = getattr(cfg, "CONSOLIDATION_COOLDOWN", 300)
    if now - _last_run_ts < cooldown:
        return

    _consolidating = True
    _last_run_ts = now
    _consolidation_task = asyncio.current_task()

    _set_status_in_server("running", phase="starting")
    try:
        await _do_consolidation()
    except asyncio.CancelledError:
        logger.info("[CONSOLIDATOR] Task cancelled cleanly.")
    except Exception as e:
        logger.error(f"[CONSOLIDATOR] Error during consolidation: {e}", exc_info=True)
        _set_status_in_server("error", error=str(e))
    finally:
        _consolidating = False
        _consolidation_task = None
