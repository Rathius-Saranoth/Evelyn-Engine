# fact_splitter.py
# date created: 2026-09-14
# date modified: 2026-10-17 12:11:38
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
from Evelyn.tools import memory_db, string_utils, taxonomy_db
from Evelyn.tools.tag_librarian import normalize_tag_format

logger = logging.getLogger("evelyn.fact_splitter")


async def generate_split_proposal(
    record: dict[str, Any],
    cat00: str = "",
    call_ollama_fn: Callable[..., Any] | None = None,
) -> str | None:
    """Evaluate a single long or compound context entry and generate a split proposal if it contains multiple facts."""
    if call_ollama_fn is None:
        from Evelyn.tools.fact_consolidator import _call_ollama
        call_ollama_fn = _call_ollama
    obs = record.get("summary") or record.get("observation", "")
    subj = record.get("subject", getattr(cfg, "USER_NAME", "Alex"))
    record_id = record.get("id")
    if record_id is None:
        return None
    record_id_int = int(record_id)

    prompt = (
        "You are an expert knowledge decomposition engine for a personal memory system.\n"
        "Evaluate the following context entry. Determine if it expresses 2 or more distinct, separate facts, "
        "or if it is already a single atomic observation.\n\n"
        f"ENTRY OBSERVATION:\n{obs}\n\n"
        f"SUBJECT: {subj}\n\n"
        "RULES:\n"
        "1. If this entry contains only ONE coherent fact or preference (even if detailed), verdict is 'atomic' and entries is empty.\n"
        "2. If this entry contains TWO OR MORE distinct observations or domain predicates, verdict is 'split'.\n"
        "3. STRICT ATOMICITY & LENGTH CEILING: Each split observation MUST be a single, discrete, atomic fact (MAXIMUM 35 WORDS). NEVER emit a compound or bloated child observation. If an entry combines multiple events or routines, split into further discrete entries.\n"
        f"4. STANDALONE FULLNESS OF CONTEXT (THE VACUUM TEST): Each split observation MUST be completely intelligible and self-contained in isolation ('in a vacuum'). Facts are retrieved independently in vector RAG without access to surrounding entries. NEVER use dangling demonstratives, relative references, or unresolved anaphora such as 'these traits', 'this decision', 'that event', 'the aforementioned', 'such changes', or ambiguous pronouns ('it', 'they', 'them') pointing to something outside this specific observation. If a fact describes a reaction, decision, agreement, or feeling about an entity's traits or actions, you MUST explicitly inline the specific traits/actions (e.g. write '{getattr(cfg, 'USER_NAME', 'Alex')} loved the pointed ears and small nose in {getattr(cfg, 'ASSISTANT_NAME', 'Evelyn')}\\'s imagery', NEVER '{getattr(cfg, 'USER_NAME', 'Alex')} loved these traits').\n"
        f"5. STRIP CONVERSATION DATE CLUTTER BUT PRESERVE INTRINSIC EVENT DATES: The entry conversation date is tracked in metadata and RAG envelopes. DO NOT restate the current conversation's timestamp inside the observation body for routine daily occurrences, moods, or pet activities (e.g. do NOT write 'On YYYY-MM-DD, {getattr(cfg, 'USER_NAME', 'Alex')} mowed the lawn' or '...mowed the lawn on YYYY-MM-DD' or 'As of YYYY-MM-DD...'). If an entry documents an evolution across dates (e.g. 'Previously X as of [date1], now Y as of [date2]'), split them into distinct atomic facts. HOWEVER, DO PRESERVE specific calendar dates that are the actual substance of the memory itself — such as birthdays, anniversaries, future scheduled appointments, deadlines, court dates, trips, or historical milestones (e.g. 'Flu shot scheduled for 10:40 AM on 2026-10-12', 'Birthday is May 23rd', 'Named {getattr(cfg, 'ASSISTANT_NAME', 'Evelyn')} on 2025-05-05').\n"
        "6. DO NOT lose specific details, nouns, conditions, or context from the original observation.\n"
        "7. ACCURATE ENTITY SUBJECT ATTRIBUTION: Do not blindly assign every split entry to the parent subject. If a split fact is primarily about a third-party entity (e.g. family member, pet, colleague), assign 'subject' to that specific entity name.\n"
        "8. EXPLICIT NOUN SUBJECT GROUNDING: Each split observation MUST explicitly name the subject/actor by name at the beginning (e.g. 'Alex prefers...', 'Evelyn maintains...', 'Biscuit the cat...'). NEVER begin with a subject-less verb and NEVER use ambiguous floating pronouns ('he', 'she', 'they').\n"
        "9. CONTROLLED VOCABULARY TAGS (Zero-Slash Invariant): Tags are flat subject terms combined at retrieval "
        "time, not a tree. Write `coffee, routine`, never `Home/Coffee/Espresso`. Slashes are strictly prohibited (Zero-Slash Invariant). "
        "Lowercase always; hyphens join words; singular count nouns by default (`routine`, `cat`, not `routines`, `cats`). "
        "Named entities follow the same rule — no TitleCase, no underscores. Prefer terms that already exist over inventing near-duplicates. "
        "Name the specific subject, never the container: `work`, `home`, `tools`, `pets`, `wellness`, `system` and the like are folder headings a flat vocabulary removes and narrow nothing. Write `firewall` not `work`, `cat` not `pets`.\n\n"
        "Output ONLY a YAML block:\n"
        "```yaml\n"
        "verdict: split   # split / atomic\n"
        "reasoning: \"Brief explanation why this entry needs splitting or is atomic.\"\n"
        "entries:\n"
        f"  - subject: {getattr(cfg, 'USER_NAME', 'Alex')}\n"
        "    tags: \"python, automation\"\n"
        f"    observation: \"{getattr(cfg, 'USER_NAME', 'Alex')} prefers developing Python backend services using FastAPI.\"\n"
        f"  - subject: {getattr(cfg, 'USER_NAME', 'Alex')}\n"
        "    tags: \"homelab, automation\"\n"
        f"    observation: \"{getattr(cfg, 'USER_NAME', 'Alex')} manages home automation devices using ZWave on a local server.\"\n"
        f"  - subject: {getattr(cfg, 'USER_NAME', 'Alex')}\n"
        "    tags: \"collaboration, software-architecture\"\n"
        f"    observation: \"{getattr(cfg, 'USER_NAME', 'Alex')} and {getattr(cfg, 'ASSISTANT_NAME', 'Evelyn')} made a joint decision to adopt FastAPI for local services.\"\n"
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
    max_words = getattr(cfg, "CONSOLIDATION_MERGE_MAX_WORDS", 35)

    for item in entries_list:
        if not isinstance(item, dict):
            continue
        c_obs = string_utils.strip_redundant_date_anchors(
            str(item.get("observation", "")).strip(), target_date=record.get("date")
        )
        if not c_obs:
            continue
        if len(c_obs.split()) > max_words:
            logger.warning(
                f"[SPLITTER] Split child observation exceeds {max_words} words ({len(c_obs.split())} words) — rejecting bloated proposal: {c_obs[:60]}..."
            )
            return None

        # Check Standalone Fullness of Context (The Vacuum Test)
        is_self_contained, vacuum_reason = string_utils.check_standalone_context(c_obs)
        if not is_self_contained:
            logger.warning(
                f"[SPLITTER] Split child observation failed Standalone Vacuum Test ({vacuum_reason}) — rejecting proposal: {c_obs[:60]}..."
            )
            return None

        c_subj = memory_db.normalize_subject_attribution(
            str(item.get("subject", subj)).strip(), c_obs
        )

        raw_t = str(item.get("tags", "")).strip()
        split_tags = taxonomy_db.canonicalize_tags(
            [normalize_tag_format(t) for t in raw_t.split(",") if t.strip()]
        )
        norm_t = ", ".join(split_tags)
        valid_entries.append({
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
    cat00: str = "",
    call_ollama_fn: Callable[..., Any] | None = None,
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
        if entry and _split_was_rejected_and_unchanged(entry):
            # Queued by hand, but already declined and unedited since. Drop it rather than
            # putting the same question back in front of the reviewer.
            memory_db.dequeue_split(entry_id)
            continue

        if entry and entry.get("status") == "live":
            res = await generate_split_proposal(entry, cat00, call_ollama_fn)
            if res:
                processed += 1
        memory_db.dequeue_split(entry_id)

    return processed


def _split_was_rejected_and_unchanged(entry: dict[str, Any]) -> bool:
    """True when the reviewer declined to split this fact and the text has not moved since.

    A `split` rejection says "this observation is atomic enough", which stays true exactly as
    long as the observation does (G1, user's call 2026-09-25). Re-proposing an unchanged fact
    offers the reviewer nothing but the chance to decline it again; re-proposing an *edited* one
    is a fair question, because what they judged no longer exists.

    This mirrors the only rejection in the codebase that already bound: denying a
    `profile_update` stamps `entry_document_evolution`, and the evolver re-opens an entry when
    `updated_at` moves past that stamp. Same shape, different table.
    """
    entry_id = int(entry["id"])
    updated = float(entry.get("updated_at") or 0.0)
    for prop in memory_db.get_rejected_proposals("split"):
        if entry_id not in (prop.get("source_ids") or []):
            continue
        reviewed = float(prop.get("reviewed_at") or prop.get("created_at") or 0.0)
        if updated <= reviewed:
            memory_db.record_rejected_request("split", str(prop.get("topic") or ""))
            return True
    return False


async def find_and_split_bloated_facts(
    records: list[dict[str, Any]],
    cat00: str = "",
    call_ollama_fn: Callable[..., Any] | None = None,
    word_threshold: int | None = None,
    limit: int = 3,
) -> int:
    """Find live entries exceeding word_threshold and propose atomic decompositions."""
    thresh = (
        word_threshold
        if word_threshold is not None
        else getattr(cfg, "CONSOLIDATION_SPLIT_WORD_THRESHOLD", 35)
    )
    candidates = []
    for r in records:
        text = str(r.get("observation") or r.get("summary") or "").strip()
        words = len(text.split())
        if words < thresh:
            continue
        rid = int(r["id"])
        if memory_db.has_pending_proposal_for([rid]):
            continue
        if _split_was_rejected_and_unchanged(r):
            continue
        # Exemption: entries produced by an approved split (split_from_id is not None)
        # were already deliberately decomposed and approved by the reviewer.
        # Do not automatically re-split them in background idle sweeps unless they
        # have been edited since creation or are excessively bloated (>= 70 words).
        if r.get("split_from_id") is not None:
            updated = float(r.get("updated_at") or 0.0)
            created = float(r.get("created_at") or 0.0)
            is_edited = updated > (created + 1.0)
            if not is_edited and words < 70:
                continue
        candidates.append(r)

    splits_created = 0
    for cand in candidates[:limit]:
        res = await generate_split_proposal(cand, cat00, call_ollama_fn)
        if res:
            splits_created += 1

    return splits_created
