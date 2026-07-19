# procedure_consolidator.py
# date created: 2026-07-19 08:30:00
# tags: #procedures, #consolidation, #deduplication, #idle, #background

"""
procedure_consolidator.py — Idle-time deduplication for Evelyn's procedures.

Scans live procedures in evelyn_memory.db for duplicate or overlapping triggers,
synthesizes a single merged master procedure via Ollama, and writes a pending
proposal record to the proposals table (type='procedure_merge').

Exports:
  run_procedure_consolidation()            — Top-level coroutine called from server idle loop.
  cancel_pending_procedure_consolidation() — Called on each new chat request to free Ollama.
  find_procedure_clusters()                — Finds groups of overlapping live procedures.
  generate_procedure_merge_proposal()      — Generates merge proposal via Ollama.
"""

import asyncio
import importlib
import json
import os
import re
import time
from typing import Optional

import httpx
import yaml

import evelyn_config as cfg
import memory_db

# Task and state management
_consolidating = False
_procedure_task: Optional[asyncio.Task] = None
_last_run_ts: float = 0.0

# ---------------------------------------------------------------------------
# Public Coroutines & Functions
# ---------------------------------------------------------------------------

def cancel_pending_procedure_consolidation() -> None:
    """Cancel any in-flight procedure consolidation task to free Ollama for user chat."""
    global _procedure_task, _consolidating
    if _procedure_task and not _procedure_task.done():
        _procedure_task.cancel()
        _consolidating = False
        print("[PROC_CONSOLIDATOR] Cancelled in-flight procedure consolidation (new chat request)", flush=True)
    _procedure_task = None


async def run_procedure_consolidation(force: bool = False) -> dict:
    """Main coroutine called from the server idle loop or manual test script.

    Args:
        force: If True, bypasses cooldown checks.

    Returns:
        dict: Summary of consolidation results.
    """
    global _consolidating, _procedure_task, _last_run_ts

    if _consolidating:
        return {"status": "skipped", "reason": "already_running"}

    importlib.reload(cfg)
    now = time.time()

    # Cooldown: don't run more than once per hour unless forced
    cooldown = 3600
    if not force and (now - _last_run_ts) < cooldown:
        return {"status": "skipped", "reason": "cooldown_active"}

    _consolidating = True
    _last_run_ts = now

    try:
        _procedure_task = asyncio.current_task()
        result = await _do_procedure_consolidation()
        return result
    except asyncio.CancelledError:
        print("[PROC_CONSOLIDATOR] Procedure consolidation pass cancelled", flush=True)
        return {"status": "cancelled"}
    except Exception as e:
        print(f"[PROC_CONSOLIDATOR ERROR] {type(e).__name__}: {e}", flush=True)
        return {"status": "error", "error": str(e)}
    finally:
        _consolidating = False
        _procedure_task = None


# ---------------------------------------------------------------------------
# Core Clustering & Synthesis Logic
# ---------------------------------------------------------------------------

def _extract_keywords(text: str) -> set[str]:
    """Extract lowercase semantic keywords from a trigger pattern."""
    words = re.findall(r"\b[a-z0-9_]{3,}\b", text.lower())
    stopwords = {"when", "the", "user", "says", "asks", "tells", "you", "for", "with", "that", "this", "and", "are", "you", "your", "they"}
    return {w for w in words if w not in stopwords}


def find_procedure_clusters() -> list[list[dict]]:
    """Fetch all live procedures and group entries with high trigger keyword overlap.

    Returns:
        list[list[dict]]: A list of clusters, where each cluster contains >= 2 matching procedure dicts.
    """
    live_procs = memory_db.get_all_procedures(status="live")
    if len(live_procs) < 2:
        return []

    # Get already proposed source IDs to avoid creating duplicate proposals
    con = memory_db.get_db()
    cursor = con.execute("SELECT source_ids FROM proposals WHERE status = 'pending' AND type = 'procedure_merge'")
    pending_proposed_ids = set()
    for row in cursor.fetchall():
        try:
            p_ids = json.loads(row["source_ids"])
            if isinstance(p_ids, list):
                pending_proposed_ids.update(p_ids)
        except Exception:
            pass
    con.close()

    # Filter out procedures already in pending merge proposals
    unprocessed = [p for p in live_procs if p["id"] not in pending_proposed_ids]
    if len(unprocessed) < 2:
        return []

    clusters = []
    used_ids = set()

    for i in range(len(unprocessed)):
        p1 = unprocessed[i]
        if p1["id"] in used_ids:
            continue

        kw1 = _extract_keywords(p1["trigger_pattern"])
        if not kw1:
            continue

        cluster = [p1]
        for j in range(i + 1, len(unprocessed)):
            p2 = unprocessed[j]
            if p2["id"] in used_ids:
                continue

            kw2 = _extract_keywords(p2["trigger_pattern"])
            if not kw2:
                continue

            overlap = kw1.intersection(kw2)
            # Check overlap coefficient Jaccard or minimum shared keywords
            if len(overlap) >= 2 or (len(kw1) <= 3 and len(overlap) >= 1 and ("journal" in overlap or "bed" in overlap or "research" in overlap)):
                cluster.append(p2)

        if len(cluster) >= 2:
            clusters.append(cluster)
            for c_item in cluster:
                used_ids.add(c_item["id"])

    return clusters


async def _do_procedure_consolidation() -> dict:
    """Internal implementation for finding procedure clusters and creating proposals."""
    clusters = find_procedure_clusters()
    if not clusters:
        print("[PROC_CONSOLIDATOR] No procedure clusters found for consolidation.", flush=True)
        return {"status": "success", "proposals_created": 0}

    proposals_created = 0
    print(f"[PROC_CONSOLIDATOR] Found {len(clusters)} procedure cluster(s) to consolidate.", flush=True)

    for cluster in clusters:
        proposal_id = await generate_procedure_merge_proposal(cluster)
        if proposal_id:
            proposals_created += 1

    return {"status": "success", "proposals_created": proposals_created}


async def generate_procedure_merge_proposal(cluster: list[dict]) -> Optional[int]:
    """Call Ollama to merge a cluster of procedures and write a proposal record to memory_db.

    Args:
        cluster: List of procedure records dicts.

    Returns:
        Optional[int]: Row ID of created proposal, or None on failure.
    """
    proc_ids = [p["id"] for p in cluster]
    proc_texts = []
    for p in cluster:
        proc_texts.append(
            f"Procedure ID #{p['id']}:\n"
            f"  Trigger: {p['trigger_pattern']}\n"
            f"  Steps:\n{p['steps']}\n"
            f"  Pitfalls: {p.get('pitfalls', 'None')}\n"
            f"  Verification: {p.get('verification', 'None')}"
        )

    formatted_procs = "\n\n".join(proc_texts)

    prompt = (
        "You are an expert systems archivist consolidating duplicate AI companion procedures.\n"
        "Analyze the following overlapping procedure rules and merge them into ONE single, master procedure.\n\n"
        "Requirements:\n"
        "1. Create a single 'trigger_pattern' that clearly encompasses all trigger phrases.\n"
        "2. Merge the 'steps' logically without losing important details or verification checks.\n"
        "3. Keep tone and constraints intact.\n"
        "4. Output ONLY a YAML block in this exact structure:\n\n"
        "```yaml\n"
        "topic: \"Unified Evening Journaling Procedure\"\n"
        "reason: \"Consolidated duplicate evening and journaling procedure entries into one master rule.\"\n"
        "trigger_pattern: \"When the user is ending the day, preparing for sleep, or asks for a journal entry\"\n"
        "steps: |\n"
        "  1. Step one\n"
        "  2. Step two\n"
        "pitfalls: \"Common mistakes to avoid\"\n"
        "verification: \"How to verify execution\"\n"
        "```\n\n"
        f"PROCEDURES TO MERGE:\n{formatted_procs}"
    )

    messages = [
        {"role": "system", "content": "You are a precise procedure archivist. Output ONLY the specified YAML block."},
        {"role": "user", "content": prompt}
    ]

    try:
        url = f"{cfg.OLLAMA_URL}/api/chat"
        payload = {
            "model": cfg.MODEL_NAME,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0.2, "num_ctx": cfg.NUM_CTX}
        }

        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                print(f"[PROC_CONSOLIDATOR ERROR] Ollama call failed with status {resp.status_code}", flush=True)
                return None
            data = resp.json()
            content = data.get("message", {}).get("content", "")

        yaml_match = re.search(r"```(?:yaml)?\s*\n(.*?)```", content, re.DOTALL | re.IGNORECASE)
        yaml_text = yaml_match.group(1) if yaml_match else content

        parsed = yaml.safe_load(yaml_text)
        if not isinstance(parsed, dict) or "trigger_pattern" not in parsed or "steps" not in parsed:
            print(f"[PROC_CONSOLIDATOR ERROR] Could not parse valid YAML from model response:\n{content[:200]}", flush=True)
            return None

        topic = parsed.get("topic", f"Merged Procedures {proc_ids}")
        reason = parsed.get("reason", f"Consolidated procedures {proc_ids} into a single master rule.")
        
        # Build merged procedure dict for storage as JSON/YAML in merged_observation
        merged_dict = {
            "trigger_pattern": parsed.get("trigger_pattern", ""),
            "steps": parsed.get("steps", ""),
            "pitfalls": parsed.get("pitfalls", ""),
            "verification": parsed.get("verification", ""),
            "tags": "procedure, merged"
        }
        merged_obs_yaml = yaml.dump(merged_dict, sort_keys=False)

        prop_id = memory_db.insert_proposal(
            type="procedure_merge",
            source_ids=proc_ids,
            merged_observation=merged_obs_yaml,
            suggested_category="procedures",
            reason=reason,
            topic=topic,
            confidence="high"
        )

        print(f"[PROC_CONSOLIDATOR] Created procedure merge proposal ID #{prop_id} for source IDs {proc_ids}", flush=True)
        return prop_id

    except Exception as e:
        print(f"[PROC_CONSOLIDATOR ERROR] Failed to generate procedure merge proposal: {e}", flush=True)
        return None
