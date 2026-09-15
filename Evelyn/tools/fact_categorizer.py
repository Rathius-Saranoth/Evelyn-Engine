# fact_categorizer.py
# date created: 2026-09-14
# date modified: 2026-09-14 20:23:52
# tags: #facts, #categories, #taxonomy, #recategorization, #hysteresis

"""
fact_categorizer.py — Category Governance & Taxonomy Audit Engine for Evelyn's Memory Vault.

Child engine to fact_consolidator. Audits category placement against the Cat00 taxonomy
with anti-flip-flop hysteresis protection to prevent repetitive churn.

Key capabilities:
1. validate_and_normalize_category: Verifies category against canonical taxonomy & subject codes (-U/-A).
2. remediate_database_categories: Fixes malformed or legacy category labels in SQLite.
3. audit_category_classifications: Audits records with 30-day anti-hysteresis to prevent ping-pong loops.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any

import yaml

import evelyn_config as cfg
from Evelyn.tools import memory_db

logger = logging.getLogger("evelyn.fact_categorizer")

_RECAT_DETECT_SYSTEM_PROMPT = (
    "You are a Category Auditor for a personal memory system. "
    "Your job is to evaluate whether entries are filed in the correct category. "
    "Output only the YAML block, nothing else."
)

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
      suggested_category: Cat08-U
      topic: "brief topic label"
      reason: "why primary weight belongs in the other category"
```
If no recategorizations are needed, output an empty list.\
"""

_CATEGORY_PATTERN = re.compile(
    r"^Cat(\d{2})-([A-Z])$", re.IGNORECASE
)


def validate_and_normalize_category(
    cat_str: str | None, subject: str | None = None
) -> str | None:
    """Validate and normalize a category string to canonical Cat##-[UA] format."""
    user_code = getattr(cfg, "SUBJECT_CODE_USER", "U")
    assistant_code = getattr(cfg, "SUBJECT_CODE_ASSISTANT", "A")

    if not cat_str or not isinstance(cat_str, str) or not cat_str.strip():
        return None

    cat_str = cat_str.strip()
    if cat_str.endswith(".md"):
        return None

    num_match = re.search(r"(?i)(?:cat|kat|cad|kad|ca|ka|c|k)\s*(\d{1,2})", cat_str)
    if not num_match:
        return None

    num = int(num_match.group(1))
    if not (1 <= num <= 16):
        return None

    cat_base = f"Cat{num:02d}"

    # Determine suffix: map legacy 'R' -> 'U' and 'E' -> 'A'
    suffix = None
    suffix_match = re.search(r"(?i)(?:-|/|\s)?([erua])$", cat_str)
    if suffix_match:
        raw_code = suffix_match.group(1).upper()
        if raw_code in ("R", "U"):
            suffix = user_code
        elif raw_code in ("E", "A"):
            suffix = assistant_code
    else:
        after_num = cat_str[num_match.end():].upper()
        has_u = "U" in after_num or "R" in after_num
        has_a = "A" in after_num or "E" in after_num
        if has_u and not has_a:
            suffix = user_code
        elif has_a and not has_u:
            suffix = assistant_code

    if not suffix and subject:
        subj_lower = subject.lower()
        if getattr(cfg, "USER_NAME", "Ricky").lower() in subj_lower:
            suffix = user_code
        elif getattr(cfg, "ASSISTANT_NAME", "Evelyn").lower() in subj_lower:
            suffix = assistant_code

    if not suffix:
        suffix = user_code

    return f"{cat_base}-{suffix}"


def remediate_database_categories() -> int:
    """Remediate malformed or legacy category codes across live entries and proposals."""
    con = memory_db.get_db()
    remediated = 0
    try:
        cur = con.cursor()
        rows = cur.execute(
            """SELECT id, category, subject FROM context_entries
               WHERE category NOT GLOB 'Cat[0-9][0-9]-[UA]' OR category GLOB 'Cat[0-9][0-9]-[RE]'"""
        ).fetchall()

        now = time.time()
        for r in rows:
            entry_id = r["id"]
            curr_cat = r["category"]
            subject = r["subject"]

            norm_cat = validate_and_normalize_category(curr_cat, subject)
            if norm_cat and norm_cat != curr_cat:
                cur.execute(
                    "UPDATE context_entries SET category = ?, recategorized_at = ? WHERE id = ?",
                    (norm_cat, now, entry_id),
                )
                remediated += 1

        # 2. Remediate proposals (excluding procedure/profile updates and inspecting non-canonical / legacy suffixes)
        prop_rows = cur.execute(
            """SELECT id, type, suggested_category, merged_observation FROM proposals
               WHERE (suggested_category IS NOT NULL AND type NOT IN ('procedure', 'procedure_merge', 'profile_update')
                      AND (suggested_category NOT GLOB 'Cat[0-9][0-9]-[UA]' OR suggested_category GLOB 'Cat[0-9][0-9]-[RE]'))
                  OR (merged_observation LIKE '%-R%' OR merged_observation LIKE '%-E%')"""
        ).fetchall()

        for pr in prop_rows:
            prop_id = pr["id"]
            ptype = pr["type"]
            cat = pr["suggested_category"]
            obs = pr["merged_observation"] or ""

            normalized_cat = cat
            if cat and ptype not in ("procedure", "procedure_merge", "profile_update") and not cat.endswith(".md"):
                norm = validate_and_normalize_category(cat)
                if norm:
                    normalized_cat = norm

            new_obs = obs
            if obs and "Cat" in obs:
                new_obs = re.sub(r"\bCat(\d{2})-R\b", r"Cat\1-U", new_obs)
                new_obs = re.sub(r"\bCat(\d{2})-E\b", r"Cat\1-A", new_obs)

            if normalized_cat != cat or new_obs != obs:
                cur.execute(
                    "UPDATE proposals SET suggested_category = ?, merged_observation = ? WHERE id = ?",
                    (normalized_cat, new_obs, prop_id),
                )
                remediated += 1

        con.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[CATEGORIZER] Category remediation warning: {e}")
    finally:
        con.close()

    if remediated:
        logger.info(f"[CATEGORIZER] Remediated {remediated} malformed category code(s) in context_entries/proposals.")
    return remediated


def _fmt_entry(r: dict, idx: int) -> str:
    d_str = r.get("date") or "unknown"
    return f"\n[{idx}] Entry #{r.get('id', idx)} ({d_str})\n    Observation: {r.get('observation') or r.get('summary')}\n"


def is_recategorization_suppressed(entry_id: int, suggested_cat: str, cooldown_days: int = 30) -> bool:
    """Check whether a recategorization should be suppressed to prevent ping-pong loops.

    Suppression triggers if:
    1. The entry was recategorized within the last `cooldown_days` (default 30 days).
    2. The entry was previously proposed/moved to `suggested_cat` in past proposals.
    """
    entry = memory_db.get_entry(entry_id)
    if not entry:
        return True

    recat_at = entry.get("recategorized_at")
    if recat_at and (time.time() - float(recat_at)) < (cooldown_days * 86400):
        return True

    con = memory_db.get_db()
    try:
        # Check proposal history for this entry
        cursor = con.execute(
            """SELECT suggested_category FROM proposals
               WHERE type = 'recategorize' AND source_ids LIKE ?""",
            (f"%{entry_id}%",),
        )
        for row in cursor.fetchall():
            if str(row["suggested_category"]).strip() == suggested_cat:
                return True
    finally:
        con.close()

    return False


async def audit_category_classifications(
    category: str,
    entries: list[dict[str, Any]],
    cat00: str,
    call_ollama_fn: Callable[..., Any],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Audit category placement for a batch of entries with anti-flip-flop protection.

    Args:
        category: Category code being evaluated.
        entries: Candidate context entries.
        cat00: Cat00 taxonomy reference text.
        call_ollama_fn: Async LLM caller.
        limit: Max entries to include in audit prompt.

    Returns:
        list[dict]: List of successfully applied recategorization records.
    """
    batch = entries[:limit]
    if not batch:
        return []

    entries_text = "".join(_fmt_entry(r, i + 1) for i, r in enumerate(batch))
    cat_ref = f"\n\nCATEGORY REFERENCE:\n{cat00}" if cat00 else ""
    prompt = _RECAT_DETECT_PROMPT.format(
        category=category,
        cat_ref=cat_ref,
        entries_text=entries_text,
    )

    messages = [
        {"role": "system", "content": _RECAT_DETECT_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    timeout = getattr(cfg, "CONSOLIDATION_TIMEOUT", 180)
    raw = await call_ollama_fn(messages, timeout=timeout, think=False, num_predict=1000)
    if not raw:
        return []

    match = re.search(r"```(?:yaml)?\s*\n(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    block = match.group(1) if match else raw
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return []

    if not isinstance(data, dict):
        return []

    applied_recats: list[dict[str, Any]] = []
    now = time.time()

    for rc in data.get("recategorize") or []:
        if not isinstance(rc, dict):
            continue
        idx = rc.get("entry_index")
        if not isinstance(idx, int) or not (1 <= idx <= len(batch)):
            continue
        suggested = str(rc.get("suggested_category", "")).strip()
        record = batch[idx - 1]
        entry_id = int(record["id"])

        normalized = validate_and_normalize_category(suggested, record.get("subject"))
        if not normalized or normalized == record.get("category"):
            continue

        # Anti-flip-flop hysteresis check
        if is_recategorization_suppressed(entry_id, normalized):
            logger.info(f"[CATEGORIZER] Suppressed ping-pong recat for #{entry_id} -> {normalized}")
            continue

        reason = str(rc.get("reason", "Audited category placement")).strip()
        topic = str(rc.get("topic", reason[:60])).strip()

        try:
            pid = memory_db.insert_proposal(
                type="recategorize",
                source_ids=[entry_id],
                suggested_category=normalized,
                reason=reason,
                topic=topic,
                status="auto_applied",
            )
            memory_db.update_entry(entry_id, category=normalized, recategorized_at=now)
            applied_recats.append({
                "entry_id": entry_id,
                "proposal_id": pid,
                "old_category": record.get("category"),
                "new_category": normalized,
                "reason": reason,
            })
            logger.info(f"[CATEGORIZER] Safely recategorized #{entry_id}: {record.get('category')} -> {normalized}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"[CATEGORIZER] Failed to apply recategorization: {e}")

    return applied_recats
