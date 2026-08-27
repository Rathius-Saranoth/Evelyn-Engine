# procedure_consolidator.py
# date created: 2026-07-19 08:30:00
# date modified: 2026-08-15 11:30:47
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
        import task_manager
        task_manager.clear_running("procedure_consolidator", status="cancelled")
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
    import task_manager
    _last_run_ts = task_manager.get_last_run_ts("procedure_consolidator")
    now = time.time()

    # Cooldown: don't run more than once per hour unless forced
    cooldown = 3600
    if not force and (now - _last_run_ts) < cooldown:
        return {"status": "skipped", "reason": "cooldown_active"}

    _consolidating = True
    _last_run_ts = task_manager.save_last_run_ts("procedure_consolidator", now)
    task_manager.set_running("procedure_consolidator", phase="deduplicating_procedures")

    try:
        _procedure_task = asyncio.current_task()
        result = await _do_procedure_consolidation()
        status_res = result.get("status", "idle") if isinstance(result, dict) else "idle"
        summary_text = f"Audited {result.get('total_procedures', 0)} procedures. Created {result.get('proposals_created', 0)} merge proposal(s)."
        task_manager.clear_running(
            "procedure_consolidator",
            status="idle",
            summary=summary_text,
            sub_status=result.get("sub_status"),
            items_processed=result.get("total_procedures", 0),
        )
        return result
    except asyncio.CancelledError:
        print("[PROC_CONSOLIDATOR] Procedure consolidation pass cancelled", flush=True)
        task_manager.clear_running("procedure_consolidator", status="cancelled")
        return {"status": "cancelled"}
    except Exception as e:
        err_cls = type(e).__name__
        err_msg = str(e).strip()
        formatted_err = f"{err_cls}: {err_msg}" if err_msg else err_cls
        print(f"[PROC_CONSOLIDATOR ERROR] {formatted_err}", flush=True)
        task_manager.clear_running("procedure_consolidator", status="error", error=formatted_err)
        return {"status": "error", "error": formatted_err}
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
    """Internal implementation for processing manual queues and finding procedure clusters."""
    import memory_db
    import task_manager
    live_procs = memory_db.get_all_procedures(status="live")
    total_procs = len(live_procs)
    proposals_created = 0

    # 1. Process manually queued procedure merges
    merge_queue = memory_db.get_procedure_merge_queue(status="pending")
    if merge_queue:
        print(f"[PROC_CONSOLIDATOR] Found {len(merge_queue)} manual procedure merge request(s) in queue.", flush=True)
        for q_item in merge_queue:
            task_manager.set_running(
                "procedure_consolidator",
                phase=f"Processing manual merge for procedures: {q_item.get('proc_ids')}",
                sub_status={
                    "total_procedures": total_procs,
                    "manual_merges_queued": len(merge_queue),
                    "proposals_created": proposals_created,
                },
            )
            cluster = []
            for pid in q_item.get("proc_id_list", []):
                proc = memory_db.get_procedure(pid)
                if proc and proc.get("status") == "live":
                    cluster.append(proc)

            if len(cluster) >= 2:
                prop_id = await generate_procedure_merge_proposal(cluster)
                if prop_id:
                    proposals_created += 1

            memory_db.dequeue_procedure_merge(q_item["id"])

    # 2. Process manually queued procedure splits
    split_queue = memory_db.get_procedure_split_queue(status="pending")
    if split_queue:
        print(f"[PROC_CONSOLIDATOR] Found {len(split_queue)} manual procedure split request(s) in queue.", flush=True)
        for s_item in split_queue:
            pid = s_item["proc_id"]
            proc = memory_db.get_procedure(pid)
            if proc and proc.get("status") == "live":
                task_manager.set_running(
                    "procedure_consolidator",
                    phase=f"Processing manual split for procedure #{pid}",
                    sub_status={
                        "total_procedures": total_procs,
                        "manual_splits_queued": len(split_queue),
                        "proposals_created": proposals_created,
                    },
                )
                prop_id = await generate_procedure_split_proposal(proc)
                if prop_id:
                    proposals_created += 1

            memory_db.dequeue_procedure_split(pid)

    # 3. Process automatic clustering on remaining procedures
    clusters = find_procedure_clusters()
    if clusters:
        print(f"[PROC_CONSOLIDATOR] Found {len(clusters)} automated procedure cluster(s) to consolidate.", flush=True)
        for idx, cluster in enumerate(clusters):
            task_manager.set_running(
                "procedure_consolidator",
                phase=f"Merging cluster {idx + 1}/{len(clusters)} ({len(cluster)} procedures)",
                sub_status={
                    "total_procedures": total_procs,
                    "clusters_found": len(clusters),
                    "proposals_created": proposals_created,
                },
            )
            proposal_id = await generate_procedure_merge_proposal(cluster)
            if proposal_id:
                proposals_created += 1

    return {
        "status": "success",
        "proposals_created": proposals_created,
        "total_procedures": total_procs,
        "sub_status": {
            "total_procedures": total_procs,
            "clusters_found": len(clusters) if clusters else 0,
            "proposals_created": proposals_created,
        },
    }


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
            f"  Suggested Tools: {p.get('suggested_tools') or 'None'}\n"
            f"  Pitfalls: {p.get('pitfalls') or 'None'}\n"
            f"  Verification: {p.get('verification') or 'None'}\n"
            f"  Tags: {p.get('tags') or 'None'}"
        )

    formatted_procs = "\n\n".join(proc_texts)

    prompt = (
        "You are an expert systems archivist consolidating duplicate AI companion procedures.\n"
        "Analyze the following overlapping procedure rules and merge them into ONE single, master procedure.\n\n"
        "Active Tools: write_file, read_file, write_journal_entry, create_task, complete_task, list_tasks, "
        "get_agenda, get_health_metrics, get_recent_workouts, manage_vault_list, run_command, web_search, start_research, generate_image.\n"
        "Note: Use 'write_file' for creating/updating notes, dream journals, and vault files. Reserve 'write_journal_entry' ONLY for daily narrative reflections.\n\n"
        "Requirements:\n"
        "1. Create a single 'trigger_pattern' that clearly encompasses all trigger phrases.\n"
        "2. Merge the 'steps' logically without losing important details or verification checks.\n"
        "3. Select or combine the accurate 'suggested_tools' (comma-separated if multiple, or None).\n"
        "4. Keep tone and constraints intact.\n"
        "5. Output ONLY a YAML block in this exact structure:\n\n"
        "```yaml\n"
        "topic: \"Unified Evening Journaling Procedure\"\n"
        "reason: \"Consolidated duplicate evening and journaling procedure entries into one master rule.\"\n"
        "trigger_pattern: \"When the user is ending the day, preparing for sleep, or asks for a journal entry\"\n"
        "steps: |\n"
        "  1. Step one\n"
        "  2. Step two\n"
        "suggested_tools: \"write_journal_entry\"\n"
        "pitfalls: \"Common mistakes to avoid\"\n"
        "verification: \"How to verify execution\"\n"
        "tags: \"procedure, merged\"\n"
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

        async with httpx.AsyncClient(timeout=180.0) as client:
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
        
        tools_val = parsed.get("suggested_tools") or ""
        if isinstance(tools_val, list):
            tools_val = ", ".join([str(t).strip() for t in tools_val if str(t).strip()])
        else:
            tools_val = str(tools_val).strip()

        # Build merged procedure dict for storage as JSON/YAML in merged_observation
        merged_dict = {
            "trigger_pattern": parsed.get("trigger_pattern", ""),
            "steps": parsed.get("steps", ""),
            "suggested_tools": tools_val,
            "pitfalls": parsed.get("pitfalls") or "",
            "verification": parsed.get("verification") or "",
            "tags": parsed.get("tags") or "procedure, merged"
        }
        merged_obs_yaml = yaml.dump(merged_dict, sort_keys=False, default_flow_style=False, width=10000)

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


async def generate_procedure_split_proposal(proc: dict) -> Optional[int]:
    """Call Ollama to split a compound procedure into multiple focused atomic procedures.

    Args:
        proc: Single procedure record dict.

    Returns:
        Optional[int]: Row ID of created proposal, or None on failure.
    """
    proc_id = proc["id"]
    prompt = (
        "You are an expert systems archivist refining AI companion operational procedures.\n"
        "Analyze the following compound procedure rule and split it into TWO or more focused, atomic, distinct procedures.\n\n"
        "Active Tools: write_file, read_file, write_journal_entry, create_task, complete_task, list_tasks, "
        "get_agenda, get_health_metrics, get_recent_workouts, manage_vault_list, run_command, web_search, start_research, generate_image.\n"
        "Note: Use 'write_file' for creating/updating notes, dream journals, and vault files. Reserve 'write_journal_entry' ONLY for daily narrative reflections.\n\n"
        "Output ONLY a YAML block in this exact structure:\n\n"
        "```yaml\n"
        f"topic: \"Split Procedure #{proc_id}\"\n"
        "reason: \"Decomposed compound procedure into distinct atomic operational rules.\"\n"
        "procedures:\n"
        "  - trigger_pattern: \"When X happens\"\n"
        "    steps: |\n"
        "      1. First step.\n"
        "    suggested_tools: \"write_file\"\n"
        "    pitfalls: \"Common mistakes to avoid\"\n"
        "    verification: \"Verification check\"\n"
        "    tags: \"skill/x, procedure/y\"\n"
        "  - trigger_pattern: \"When Y happens\"\n"
        "    steps: |\n"
        "      1. Step for second rule.\n"
        "    suggested_tools: \"create_task\"\n"
        "    pitfalls: \"None\"\n"
        "    verification: \"Verification check\"\n"
        "    tags: \"skill/z, procedure/w\"\n"
        "```\n\n"
        f"COMPOUND PROCEDURE TO SPLIT:\n"
        f"Procedure ID #{proc_id}:\n"
        f"  Trigger: {proc['trigger_pattern']}\n"
        f"  Steps:\n{proc['steps']}\n"
        f"  Suggested Tools: {proc.get('suggested_tools') or 'None'}\n"
        f"  Pitfalls: {proc.get('pitfalls') or 'None'}\n"
        f"  Verification: {proc.get('verification') or 'None'}\n"
        f"  Tags: {proc.get('tags') or 'None'}"
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

        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                print(f"[PROC_CONSOLIDATOR ERROR] Ollama call failed with status {resp.status_code}", flush=True)
                return None
            data = resp.json()
            content = data.get("message", {}).get("content", "")

        yaml_match = re.search(r"```(?:yaml)?\s*\n(.*?)```", content, re.DOTALL | re.IGNORECASE)
        yaml_text = yaml_match.group(1) if yaml_match else content

        parsed = yaml.safe_load(yaml_text)
        if not isinstance(parsed, dict) or "procedures" not in parsed:
            print(f"[PROC_CONSOLIDATOR ERROR] Could not parse valid YAML procedures list from model response:\n{content[:200]}", flush=True)
            return None

        topic = parsed.get("topic", f"Split Procedure #{proc_id}")
        reason = parsed.get("reason", f"Decomposed compound procedure #{proc_id} into distinct atomic operational rules.")

        for p_item in parsed.get("procedures", []):
            if isinstance(p_item, dict) and "suggested_tools" in p_item:
                tools_val = p_item.get("suggested_tools") or ""
                if isinstance(tools_val, list):
                    p_item["suggested_tools"] = ", ".join([str(t).strip() for t in tools_val if str(t).strip()])
                else:
                    p_item["suggested_tools"] = str(tools_val).strip()

        prop_id = memory_db.insert_proposal(
            type="procedure_split",
            source_ids=[proc_id],
            merged_observation=yaml.dump(parsed, sort_keys=False, default_flow_style=False, width=10000),
            suggested_category="procedures",
            reason=reason,
            topic=topic,
            confidence="high"
        )

        print(f"[PROC_CONSOLIDATOR] Created procedure split proposal ID #{prop_id} for procedure #{proc_id}", flush=True)
        return prop_id

    except Exception as e:
        print(f"[PROC_CONSOLIDATOR ERROR] Failed to generate procedure split proposal: {e}", flush=True)
        return None

