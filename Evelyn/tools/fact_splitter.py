# fact_splitter.py
# date created: 2026-09-14
# date modified: 2026-09-14 20:23:52
# tags: #facts, #decomposition, #splitting, #atomic_memory

"""
fact_splitter.py — Fact Decomposition Engine for Evelyn's Memory Vault.

Child engine to fact_consolidator. Evaluates compound or bloated context entries
and generates atomic decomposition proposals with parent provenance (split_from_id).

Key capabilities:
1. generate_split_proposal: Uses LLM (think=True) to evaluate atomic vs split verdict.
2. process_split_queue: Drains user-queued split review items from SQLite.
3. find_and_split_bloated_facts: Automatically scans for entries exceeding word length threshold.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

import yaml

import evelyn_config as cfg
from Evelyn.tools import memory_db
from Evelyn.tools.tag_librarian import normalize_tag_format

logger = logging.getLogger("evelyn.fact_splitter")


async def generate_split_proposal(
    record: dict[str, Any],
    cat00: str,
    call_ollama_fn: Callable[..., Any] | None = None,
) -> str | None:
    """Evaluate a single long or compound context entry and generate a split proposal if it contains multiple facts."""
    if call_ollama_fn is None:
        from Evelyn.tools.fact_consolidator import _call_ollama
        call_ollama_fn = _call_ollama
    obs = record.get("summary") or record.get("observation", "")
    cat = record.get("category", f"Cat05-{getattr(cfg, 'SUBJECT_CODE_USER', 'U')}")
    subj = record.get("subject", getattr(cfg, "USER_NAME", "Ricky"))
    record_id = record.get("id")
    if record_id is None:
        return None
    record_id_int = int(record_id)

    prompt = (
        "You are an expert knowledge decomposition engine for a personal memory system.\n"
        "Evaluate the following context entry. Determine if it expresses 2 or more distinct, separate facts, "
        "or if it is already a single atomic observation.\n\n"
        f"ENTRY OBSERVATION:\n{obs}\n\n"
        f"CATEGORY: {cat}\n"
        f"SUBJECT: {subj}\n\n"
        f"CATEGORY REFERENCE:\n{cat00}\n\n"
        "RULES:\n"
        "1. If this entry contains only ONE coherent fact or preference (even if detailed), verdict is 'atomic' and entries is empty.\n"
        "2. If this entry contains TWO OR MORE distinct observations or domain predicates, verdict is 'split'.\n"
        "3. DO NOT lose specific details, nouns, conditions, or context from the original observation.\n"
        "4. MULTI-TIER DOMAIN TAXONOMY: Assign clean domain hierarchy tags (e.g. `Tech/Python/FastAPI`, `Home/Coffee/Espresso`, `Lore/Dungeon_Crawler_Carl`) for each split item.\n"
        "5. EXPLICIT NOUN SUBJECT GROUNDING: Each split observation MUST explicitly name the subject/actor by name at the beginning (e.g. 'Ricky prefers...', 'Evelyn maintains...', 'Fox the cat...'). NEVER begin with a subject-less verb and NEVER use ambiguous floating pronouns ('he', 'she', 'they').\n\n"
        "Output ONLY a YAML block:\n"
        "```yaml\n"
        "verdict: split   # split / atomic\n"
        "reasoning: \"Brief explanation why this entry needs splitting or is atomic.\"\n"
        "entries:\n"
        f"  - category: Cat05-{getattr(cfg, 'SUBJECT_CODE_USER', 'U')}\n"
        f"    subject: {getattr(cfg, 'USER_NAME', 'Ricky')}\n"
        "    tags: \"Tech/Python/FastAPI\"\n"
        f"    observation: \"{getattr(cfg, 'USER_NAME', 'Ricky')} prefers developing Python backend services using FastAPI.\"\n"
        f"  - category: Cat14-{getattr(cfg, 'SUBJECT_CODE_USER', 'U')}\n"
        f"    subject: {getattr(cfg, 'USER_NAME', 'Ricky')}\n"
        "    tags: \"Home/Server/ZWave\"\n"
        f"    observation: \"{getattr(cfg, 'USER_NAME', 'Ricky')} manages home automation devices using ZWave on a local server.\"\n"
        "```"
    )

    messages = [
        {"role": "system", "content": "You evaluate compound memory facts for atomic decomposition. Output only YAML."},
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

    match = re.search(r"```(?:yaml)?\s*\n(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    block = match.group(1) if match else raw
    try:
        data = yaml.safe_load(block)
    except (yaml.YAMLError, ValueError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    if str(data.get("verdict", "")).strip().lower() != "split":
        return None

    entries_list = data.get("entries", [])
    if not isinstance(entries_list, list) or len(entries_list) < 2:
        return None

    valid_entries = []
    for item in entries_list:
        if not isinstance(item, dict):
            continue
        c_obs = str(item.get("observation", "")).strip()
        if not c_obs:
            continue
        c_cat = str(item.get("category", cat)).strip()
        c_subj = str(item.get("subject", subj)).strip()
        raw_t = str(item.get("tags", "")).strip()
        norm_t = ", ".join([normalize_tag_format(t) for t in raw_t.split(",") if t.strip()])
        valid_entries.append({
            "category": c_cat,
            "subject": c_subj,
            "observation": c_obs,
            "tags": norm_t,
        })

    if len(valid_entries) < 2:
        return None

    constructed_yaml = yaml.dump({"entries": valid_entries}, default_flow_style=False, width=10000)

    try:
        pid = memory_db.insert_proposal(
            type="split",
            source_ids=[record_id_int],
            merged_observation=constructed_yaml,
            merged_tags=", ".join([e["tags"] for e in valid_entries if e["tags"]]),
            suggested_category=valid_entries[0]["category"],
            reason=str(data.get("reasoning", "Decomposed bloated compound entry into atomic context facts.")),
            topic=f"Split Compound Fact #{record_id_int}",
            confidence=str(data.get("confidence", "medium")).strip().lower(),
            status="pending",
        )
        logger.info(f"[SPLITTER] Split proposal created: ID #{pid} for entry #{record_id_int}")
        return str(pid)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[SPLITTER] Failed to write split proposal: {e}")
        return None


async def process_split_queue(
    cat00: str,
    call_ollama_fn: Callable[..., Any],
) -> int:
    """Drain user-queued entries from split_queue."""
    queued_items = memory_db.get_split_queue(status="pending")
    if not queued_items:
        return 0

    processed = 0
    for q_item in queued_items:
        entry_id = q_item["entry_id"]
        if memory_db.has_pending_proposal_for([entry_id]):
            memory_db.dequeue_split(entry_id)
            continue

        entry = memory_db.get_entry(entry_id)
        if entry and entry.get("status") == "live":
            res = await generate_split_proposal(entry, cat00, call_ollama_fn)
            if res:
                processed += 1
        memory_db.dequeue_split(entry_id)

    return processed


async def find_and_split_bloated_facts(
    records: list[dict[str, Any]],
    cat00: str,
    call_ollama_fn: Callable[..., Any],
    word_threshold: int = 35,
    limit: int = 3,
) -> int:
    """Find live entries exceeding word_threshold and propose atomic decompositions."""
    candidates = [
        r for r in records
        if len(str(r.get("observation") or r.get("summary") or "").split()) >= word_threshold
        and not memory_db.has_pending_proposal_for([int(r["id"])])
    ]

    splits_created = 0
    for cand in candidates[:limit]:
        res = await generate_split_proposal(cand, cat00, call_ollama_fn)
        if res:
            splits_created += 1

    return splits_created
