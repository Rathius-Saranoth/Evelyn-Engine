# grounding_auditor.py
# date created: 2026-09-14
# date modified: 2026-09-14 20:23:52
# tags: #memory, #auditor, #grounding, #pronouns, #review_queue

"""
grounding_auditor.py — Review-Gated Subject & Pronoun Grounding Auditor.

Discovers ambiguous context entries (subject-less verbs, floating pronouns,
and subject/pronoun contradictions) in evelyn_memory.db and stages review proposals
into the proposals table for human review on dev.html.

Key capabilities:
1. find_surrounding_chat_context: Ephemeral on-demand FTS5 lookup against evelyn_chat.db.
2. detect_grounding_issues: Scans live entries for pronoun starts, contradictions, and bare verbs.
3. stage_grounding_proposals: Inserts non-destructive review proposals into SQLite.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

import evelyn_config as cfg
from Evelyn.tools import memory_db

logger = logging.getLogger("evelyn.grounding_auditor")

_PRONOUN_STARTS = (
    ("He ", "he"),
    ("She ", "she"),
    ("They ", "they"),
    ("His ", "his"),
    ("Her ", "her"),
    ("Their ", "their"),
)

_COMMON_BARE_VERBS = (
    "Enjoys ",
    "Prefers ",
    "Believes ",
    "Maintains ",
    "Values ",
    "Acts as ",
    "Uses ",
    "Dislikes ",
    "Finds ",
    "Seeks ",
    "Identified ",
    "Acknowledges ",
    "Prioritizes ",
    "Articulates ",
    "Recognizes ",
    "Clarified ",
    "Recently watched ",
    "Recently read ",
    "Started ",
    "Completed ",
)

_STOPWORDS = {
    "about", "after", "again", "against", "all", "also", "and", "any", "are",
    "because", "been", "before", "being", "between", "both", "but", "could",
    "each", "for", "from", "further", "have", "having", "here", "how", "into",
    "more", "most", "other", "our", "should", "some", "such", "than", "that",
    "the", "their", "them", "then", "there", "these", "they", "this", "those",
    "through", "very", "was", "were", "what", "when", "where", "which", "while",
    "who", "whom", "will", "with", "would",
}


def find_surrounding_chat_context(
    entry_id: int,
    window: int = 3,
    chat_db_path: str | None = None,
) -> dict[str, Any] | None:
    """Locate the original chat conversation turns for a context entry on-demand.

    Uses FTS5 BM25 search on evelyn_chat.db with key nouns extracted from the
    entry's observation text to pinpoint the source message and fetch surrounding turns.

    Args:
        entry_id: context_entries row ID.
        window: Number of conversational turns before and after to return.
        chat_db_path: Optional path override for tests.

    Returns:
        dict with 'matched_message_id' and 'messages' list, or None if not found.
    """
    entry = memory_db.get_entry(entry_id)
    if not entry:
        return None

    obs = str(entry.get("observation") or "").strip()
    if not obs:
        return None

    # Extract significant content tokens
    raw_words = re.findall(r"\b[a-zA-Z0-9_-]{4,}\b", obs)
    sig_words = [w for w in raw_words if w.lower() not in _STOPWORDS]
    if not sig_words:
        return None

    # Pick up to 5 highest-signal words (unique, preserving order)
    seen = set()
    query_tokens = []
    for w in sig_words:
        wl = w.lower()
        if wl not in seen:
            seen.add(wl)
            query_tokens.append(w)
            if len(query_tokens) >= 5:
                break

    fts_query = " OR ".join(query_tokens)
    db_file = chat_db_path or cfg.CHAT_DB_PATH

    try:
        con = sqlite3.connect(db_file, timeout=5.0)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        # Query messages_fts for best BM25 match
        cur.execute(
            """
            SELECT m.id, m.role, m.content, m.ts, rank
            FROM messages_fts fts
            JOIN messages m ON m.id = fts.rowid
            WHERE messages_fts MATCH ?
            ORDER BY rank
            LIMIT 1
            """,
            (fts_query,),
        )
        match_row = cur.fetchone()
        if not match_row:
            con.close()
            return None

        matched_id = int(match_row["id"])
        min_id = max(1, matched_id - window)
        max_id = matched_id + window

        cur.execute(
            """
            SELECT id, role, content, ts
            FROM messages
            WHERE id BETWEEN ? AND ?
            ORDER BY id ASC
            """,
            (min_id, max_id),
        )
        surrounding_rows = cur.fetchall()
        con.close()

        messages = [
            {
                "id": int(r["id"]),
                "role": str(r["role"]),
                "content": str(r["content"]),
                "ts": float(r["ts"] or 0.0),
                "is_match": int(r["id"]) == matched_id,
            }
            for r in surrounding_rows
        ]

        return {
            "matched_message_id": matched_id,
            "messages": messages,
        }
    except (sqlite3.Error, OSError, ValueError) as e:
        logger.warning(f"[GROUNDING_AUDITOR] Chat context lookup failed for #{entry_id}: {e}")
        return None


def detect_grounding_issues(
    limit: int = 25,
    category: str | None = None,
    subject: str | None = None,
) -> list[dict[str, Any]]:
    """Scan live context entries for floating pronouns, contradictions, and bare verbs.

    Args:
        limit: Maximum number of ungrounded entries to return.
        category: Optional category filter (e.g. 'Cat05-U').
        subject: Optional subject filter.

    Returns:
        List of Issue dicts ready to be previewed or staged as proposals.
    """
    con = memory_db.get_db()
    cur = con.cursor()

    query = """
        SELECT id, category, subject, observation, tags, date
        FROM context_entries
        WHERE status = 'live'
    """
    params: list[Any] = []
    if category:
        query += " AND category = ?"
        params.append(category)
    if subject:
        query += " AND subject = ?"
        params.append(subject)

    query += " ORDER BY id ASC"
    rows = cur.execute(query, params).fetchall()
    con.close()

    user_name = getattr(cfg, "USER_NAME", "Alex")
    assistant_name = getattr(cfg, "ASSISTANT_NAME", "Evelyn")

    issues: list[dict[str, Any]] = []

    for row in rows:
        eid = int(row["id"])
        if memory_db.has_pending_proposal_for([eid]):
            continue

        cat = str(row["category"])
        subj = str(row["subject"])
        obs = str(row["observation"]).strip()
        tags = str(row["tags"] or "")
        entry_date = row["date"]

        is_user_canon = cat.endswith("-U")
        is_assistant_canon = cat.endswith("-A")

        issue_type = None
        grounded_obs = None
        reason = None
        suggested_subject = subj

        # 1. Check for pronoun start
        matched_pronoun = None
        for p_start, _ in _PRONOUN_STARTS:
            if obs.startswith(p_start):
                matched_pronoun = p_start
                break

        if matched_pronoun:
            # Check for subject contradiction: e.g. subj=Evelyn but obs starts with "He "
            if matched_pronoun in ("He ", "His ") and subj == assistant_name and is_user_canon:
                issue_type = "contradiction_pronoun_subject"
                # The text is about user, but subject tag was set to assistant
                suggested_subject = user_name
                remainder = obs[len(matched_pronoun):]
                grounded_obs = (
                    f"{user_name} {remainder}"
                    if matched_pronoun == "He "
                    else f"{user_name}'s {remainder}"
                )
                reason = (
                    f"Subject column was set to '{subj}', but observation refers to '{matched_pronoun.strip()}'. "
                    f"Corrected subject to '{user_name}' and grounded text."
                )
            elif matched_pronoun in ("She ", "Her ") and subj == user_name and is_assistant_canon:
                issue_type = "contradiction_pronoun_subject"
                suggested_subject = assistant_name
                remainder = obs[len(matched_pronoun):]
                grounded_obs = (
                    f"{assistant_name} {remainder}"
                    if matched_pronoun == "She "
                    else f"{assistant_name}'s {remainder}"
                )
                reason = (
                    f"Subject column was set to '{subj}', but observation refers to '{matched_pronoun.strip()}'. "
                    f"Corrected subject to '{assistant_name}' and grounded text."
                )
            else:
                # Floating pronoun matching the subject column
                issue_type = "floating_pronoun"
                remainder = obs[len(matched_pronoun):]
                target_name = subj if subj not in ("Unknown", "") else (user_name if is_user_canon else assistant_name)
                if matched_pronoun in ("He ", "She ", "They "):
                    grounded_obs = f"{target_name} {remainder}"
                elif matched_pronoun in ("His ", "Her "):
                    grounded_obs = f"{target_name}'s {remainder}"
                else:
                    grounded_obs = f"{target_name}'s {remainder}"
                reason = f"Grounded floating pronoun '{matched_pronoun.strip()}' with explicit noun '{target_name}'."

        # 2. Check for bare verbs (subject-less start)
        if not issue_type:
            for v_start in _COMMON_BARE_VERBS:
                if obs.startswith(v_start):
                    issue_type = "bare_verb"
                    target_name = subj if subj not in ("Unknown", "") else (user_name if is_user_canon else assistant_name)
                    # Lowercase first character of verb if needed
                    v_rest = obs[0].lower() + obs[1:]
                    grounded_obs = f"{target_name} {v_rest}"
                    reason = f"Grounded subject-less verb '{v_start.strip()}' with explicit subject '{target_name}'."
                    break

        if issue_type and grounded_obs:
            issues.append({
                "entry_id": eid,
                "current_category": cat,
                "current_subject": subj,
                "suggested_subject": suggested_subject,
                "original_observation": obs,
                "grounded_observation": grounded_obs,
                "issue_type": issue_type,
                "reason": reason,
                "tags": tags,
                "date": entry_date,
            })
            if len(issues) >= limit:
                break

    return issues


def stage_grounding_proposals(issues: list[dict[str, Any]]) -> list[int]:
    """Insert non-destructive 'rephrase' review proposals for identified issues.

    Args:
        issues: List of Issue dictionaries from detect_grounding_issues().

    Returns:
        List of generated proposal IDs.
    """
    staged_ids: list[int] = []

    for issue in issues:
        eid = issue["entry_id"]
        if memory_db.has_pending_proposal_for([eid]):
            continue

        subj_note = (
            f"Subject: {issue['suggested_subject']}"
            if issue.get("suggested_subject") != issue.get("current_subject")
            else ""
        )

        pid = memory_db.insert_proposal(
            type="rephrase",
            source_ids=[eid],
            merged_observation=issue["grounded_observation"],
            merged_tags=issue.get("tags") or "",
            suggested_category=issue.get("current_category"),
            reason=issue.get("reason"),
            topic=subj_note or f"Ground Observation #{eid}",
            confidence="high",
            status="pending",
        )
        staged_ids.append(pid)

    return staged_ids
