# memory_db.py
# date created: 2026-05-24 09:51:58
# date modified: 2026-05-25 19:50:51
# tags: #database, #sqlite, #memory, #schemas, #connections

"""
memory_db.py — SQLite access layer for Evelyn's context memory database.

Provides CRUD operations for the context_entries and proposals tables
in evelyn_memory.db. Keeps context/memory data separate from chat history
(evelyn_chat.db).

Schema:
  context_entries — Stores all context facts (live, extracted, pending_review).
                    Replaces the Cat##/Cat##-{E,R}/*.md flat-file layout.
  proposals       — Stores consolidation and recategorization proposals.
                    Replaces CONSOLIDATION_*.md and RECATEGORIZE_*.md files.

Usage:
  import memory_db
  memory_db.init_db()           # Idempotent — safe to call on every startup
  entry_id = memory_db.insert_entry(category='Cat05-R', subject='Ricky', ...)
  entries  = memory_db.get_entries_by_category('Cat05-R')

All functions use short-lived connections (no module-level state).
"""

import json
import sqlite3
import time
from typing import Optional

import evelyn_config as cfg


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    """Open a connection to evelyn_memory.db with row_factory enabled.

    Returns:
        sqlite3.Connection configured for dict-like row access.
    """
    con = sqlite3.connect(cfg.MEMORY_DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")  # Concurrent reads during writes
    return con


# ---------------------------------------------------------------------------
# Schema initialization
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create tables and indexes if they don't exist (idempotent).

    Safe to call on every server startup. Uses IF NOT EXISTS so existing
    data is never touched.
    """
    con = get_db()

    con.execute("""
        CREATE TABLE IF NOT EXISTS context_entries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            category        TEXT NOT NULL,
            subject         TEXT NOT NULL,
            observation     TEXT NOT NULL,
            confidence      TEXT NOT NULL DEFAULT 'medium',
            source          TEXT NOT NULL DEFAULT 'manual',
            status          TEXT NOT NULL DEFAULT 'live',
            date            TEXT,
            tags            TEXT,
            created_at      REAL NOT NULL,
            updated_at      REAL
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS proposals (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            type                TEXT NOT NULL,
            source_ids          TEXT NOT NULL,
            merged_observation  TEXT,
            merged_tags         TEXT,
            suggested_category  TEXT,
            reason              TEXT,
            topic               TEXT,
            confidence          TEXT NOT NULL DEFAULT 'medium',
            status              TEXT NOT NULL DEFAULT 'pending',
            created_at          REAL NOT NULL,
            reviewed_at         REAL
        )
    """)

    # Indexes — IF NOT EXISTS prevents errors on re-init
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_ce_category ON context_entries(category)",
        "CREATE INDEX IF NOT EXISTS idx_ce_status ON context_entries(status)",
        "CREATE INDEX IF NOT EXISTS idx_ce_date ON context_entries(date)",
        "CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status)",
        "CREATE INDEX IF NOT EXISTS idx_proposals_type ON proposals(type)",
    ]:
        con.execute(stmt)

    con.commit()
    con.close()


# ===========================================================================
# Context Entries — CRUD
# ===========================================================================


def insert_entry(
    category: str,
    subject: str,
    observation: str,
    confidence: str = "medium",
    source: str = "manual",
    status: str = "live",
    date: Optional[str] = None,
    tags: Optional[str] = None,
) -> int:
    """Insert a new context entry and return its row ID.

    Args:
        category:       Category code, e.g. 'Cat05-R'.
        subject:        'Ricky' or 'Evelyn'.
        observation:    The factual observation text.
        confidence:     'high', 'medium', or 'low'.
        source:         'manual', 'extracted', or 'consolidated'.
        status:         'live', 'extracted', or 'pending_review'.
        date:           YYYY-MM-DD when the fact was discussed/observed.
        secondary_cats: Comma-separated secondary category codes.
        original_file:  Original filename (for migration traceability).

    Returns:
        The auto-generated row ID of the new entry.
    """
    con = get_db()
    cur = con.execute(
        """INSERT INTO context_entries
           (category, subject, observation, confidence, source, status,
            date, tags, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (category, subject, observation, confidence, source, status,
         date, tags, time.time()),
    )
    row_id = cur.lastrowid
    con.commit()
    con.close()
    return row_id


def get_entry(entry_id: int) -> Optional[dict]:
    """Fetch a single context entry by ID.

    Returns:
        Dict of the entry's fields, or None if not found.
    """
    con = get_db()
    row = con.execute(
        "SELECT * FROM context_entries WHERE id = ?", (entry_id,)
    ).fetchone()
    con.close()
    return dict(row) if row else None


def get_entries_by_category(
    category: str, status: str = "live"
) -> list[dict]:
    """Fetch all entries for a given category and status.

    Args:
        category: Category code, e.g. 'Cat05-R'.
        status:   Filter by status. Default 'live'.

    Returns:
        List of entry dicts, sorted oldest-first by date.
    """
    con = get_db()
    rows = con.execute(
        "SELECT * FROM context_entries WHERE category = ? AND status = ? "
        "ORDER BY date ASC, id ASC",
        (category, status),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_all_entries(statuses: Optional[list[str]] = None) -> list[dict]:
    """Fetch all context entries, optionally filtered by status list.

    Args:
        statuses: List of status values to include. Default: ['live'].

    Returns:
        List of entry dicts, sorted by category then date.
    """
    if statuses is None:
        statuses = ["live"]
    con = get_db()
    placeholders = ",".join("?" for _ in statuses)
    rows = con.execute(
        f"SELECT * FROM context_entries WHERE status IN ({placeholders}) "
        f"ORDER BY category ASC, date ASC, id ASC",
        statuses,
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def update_entry(entry_id: int, **fields) -> bool:
    """Update specific fields on an existing context entry.

    Automatically sets updated_at to the current time.

    Args:
        entry_id: Row ID of the entry to update.
        **fields: Column name → new value pairs. Only valid column names
                  are accepted; unknown fields are silently ignored.

    Returns:
        True if the row was found and updated, False otherwise.
    """
    valid_cols = {
        "category", "subject", "observation", "confidence", "source",
        "status", "date", "tags",
    }
    updates = {k: v for k, v in fields.items() if k in valid_cols}
    if not updates:
        return False

    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [entry_id]

    con = get_db()
    cur = con.execute(
        f"UPDATE context_entries SET {set_clause} WHERE id = ?", values
    )
    con.commit()
    affected = cur.rowcount
    con.close()
    return affected > 0


def delete_entry(entry_id: int) -> bool:
    """Soft delete a context entry by ID.

    Sets status='deleted' instead of removing the row. This allows
    the Chroma garbage collector to naturally remove it from RAG.

    Args:
        entry_id: Row ID of the entry to soft delete.

    Returns:
        True if a row was updated, False if not found.
    """
    return update_entry(entry_id, status="deleted")


def count_entries(status: Optional[str] = None) -> int:
    """Count context entries, optionally filtered by status.

    Args:
        status: If provided, count only entries with this status.

    Returns:
        Integer count.
    """
    con = get_db()
    if status:
        row = con.execute(
            "SELECT COUNT(*) FROM context_entries WHERE status = ?", (status,)
        ).fetchone()
    else:
        row = con.execute("SELECT COUNT(*) FROM context_entries").fetchone()
    con.close()
    return row[0]


def count_entries_by_category() -> dict[str, int]:
    """Return a dict of category → count for all live entries.

    Returns:
        OrderedDict-like mapping, e.g. {'Cat01-E': 12, 'Cat01-R': 8, ...}.
    """
    con = get_db()
    rows = con.execute(
        "SELECT category, COUNT(*) as cnt FROM context_entries "
        "WHERE status = 'live' GROUP BY category ORDER BY category"
    ).fetchall()
    con.close()
    return {r["category"]: r["cnt"] for r in rows}


# ---------------------------------------------------------------------------
# Deduplication support (for fact_extractor)
# ---------------------------------------------------------------------------


def find_similar_entries(
    category: str,
    observation_text: str,
    min_overlap: float = 0.5,
    status: str = "live",
) -> list[dict]:
    """Find entries in the same category with high keyword overlap.

    Uses a simple normalized keyword overlap ratio. No model calls.

    Args:
        category:         Category to search within.
        observation_text: The candidate observation to check against.
        min_overlap:      Minimum keyword overlap ratio (0.0–1.0) to
                          consider a match. Default 0.5 (50% overlap).
        status:           Filter by status. Default 'live'.

    Returns:
        List of matching entry dicts with an added 'overlap' key.
    """
    candidates = get_entries_by_category(category, status=status)
    if not candidates:
        return []

    new_kws = _extract_keywords(observation_text)
    if not new_kws:
        return []

    matches = []
    for entry in candidates:
        existing_kws = _extract_keywords(entry["observation"])
        if not existing_kws:
            continue
        overlap = len(new_kws & existing_kws) / min(len(new_kws), len(existing_kws))
        if overlap >= min_overlap:
            entry_copy = dict(entry)
            entry_copy["overlap"] = round(overlap, 3)
            matches.append(entry_copy)

    return matches


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from observation text.

    Strips common stopwords and short tokens to produce a set of
    normalized content words for overlap comparison.

    Args:
        text: Raw observation string.

    Returns:
        Set of lowercase keyword strings (3+ chars).
    """
    _STOPWORDS = {
        "the", "and", "for", "are", "but", "not", "you", "all", "can",
        "had", "her", "was", "one", "our", "out", "has", "his", "how",
        "its", "may", "new", "now", "old", "see", "way", "who", "did",
        "get", "let", "say", "she", "too", "use", "been", "each", "have",
        "from", "into", "just", "like", "made", "many", "some", "than",
        "them", "then", "they", "this", "very", "when", "with", "that",
        "what", "will", "your", "also", "back", "been", "come", "could",
        "does", "even", "good", "here", "know", "more", "most", "much",
        "only", "over", "such", "take", "their", "well", "were", "which",
        "about", "after", "being", "could", "every", "first", "other",
        "since", "still", "those", "under", "where", "while", "would",
        "these", "there", "should", "really", "ricky", "evelyn",
    }
    words = set()
    for word in text.lower().split():
        # Strip punctuation from edges
        clean = word.strip(".,;:!?\"'()[]{}—–-")
        if len(clean) >= 3 and clean not in _STOPWORDS:
            words.add(clean)
    return words


# ===========================================================================
# Proposals — CRUD
# ===========================================================================


def insert_proposal(
    type: str,
    source_ids: list[int],
    merged_observation: Optional[str] = None,
    merged_tags: Optional[str] = None,
    suggested_category: Optional[str] = None,
    reason: Optional[str] = None,
    topic: Optional[str] = None,
    confidence: str = "medium",
    status: str = "pending",
) -> int:
    """Insert a new proposal and return its row ID.

    Args:
        type:                'merge', 'supersede', 'recategorize', 'keep_both'.
        source_ids:          List of context_entry IDs involved.
        verdict:             Proposed action summary.
        merged_observation:  Proposed merged text (for merge/supersede).
        merged_tags:         Proposed tags.
        suggested_category:  New category (for recategorize).
        reason:              LLM reasoning for the proposal.
        topic:               LLM-identified topic label.
        status:              'pending', 'applied', 'rejected', 'auto_applied'.

    Returns:
        The auto-generated row ID.
    """
    con = get_db()
    cur = con.execute(
        """
        INSERT INTO proposals (
            type, source_ids, merged_observation, merged_tags,
            suggested_category, reason, topic, confidence, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            type, json.dumps(source_ids), merged_observation, merged_tags,
            suggested_category, reason, topic, confidence, status, time.time()
        ),
    )
    row_id = cur.lastrowid
    con.commit()
    con.close()
    return row_id


def get_pending_proposals(
    type: Optional[str] = None,
) -> list[dict]:
    """Fetch all pending proposals, optionally filtered by type.

    Args:
        type: If provided, filter to this proposal type only.

    Returns:
        List of proposal dicts with source_ids parsed from JSON.
    """
    con = get_db()
    if type:
        rows = con.execute(
            "SELECT * FROM proposals WHERE status = 'pending' AND type = ? "
            "ORDER BY created_at ASC",
            (type,),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM proposals WHERE status = 'pending' "
            "ORDER BY created_at ASC"
        ).fetchall()
    con.close()

    results = []
    for r in rows:
        d = dict(r)
        try:
            d["source_ids"] = json.loads(d["source_ids"])
        except (json.JSONDecodeError, TypeError):
            d["source_ids"] = []
        results.append(d)
    return results


def apply_proposal(proposal_id: int) -> bool:
    """Mark a proposal as applied.

    Args:
        proposal_id: Row ID of the proposal.

    Returns:
        True if updated, False if not found.
    """
    con = get_db()
    cur = con.execute(
        "UPDATE proposals SET status = 'applied', reviewed_at = ? WHERE id = ?",
        (time.time(), proposal_id),
    )
    con.commit()
    affected = cur.rowcount
    con.close()
    return affected > 0


def reject_proposal(proposal_id: int) -> bool:
    """Mark a proposal as rejected.

    Args:
        proposal_id: Row ID of the proposal.

    Returns:
        True if updated, False if not found.
    """
    con = get_db()
    cur = con.execute(
        "UPDATE proposals SET status = 'rejected', reviewed_at = ? WHERE id = ?",
        (time.time(), proposal_id),
    )
    con.commit()
    affected = cur.rowcount
    con.close()
    return affected > 0


def has_pending_proposal_for(entry_ids: list[int], type: Optional[str] = None) -> bool:
    """Check if any pending proposal already references the given entry IDs.

    Used by the consolidator to avoid generating duplicate proposals for
    entries that already have an unreviewed proposal.

    Args:
        entry_ids: List of context_entry IDs to check.
        type:      If provided, restrict check to this proposal type.

    Returns:
        True if at least one pending proposal references any of the IDs.
    """
    con = get_db()
    if type:
        rows = con.execute(
            "SELECT source_ids FROM proposals WHERE status = 'pending' AND type = ?",
            (type,),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT source_ids FROM proposals WHERE status = 'pending'"
        ).fetchall()
    con.close()

    check_set = set(entry_ids)
    for row in rows:
        try:
            existing = set(json.loads(row["source_ids"]))
            if check_set & existing:
                return True
        except (json.JSONDecodeError, TypeError):
            continue
    return False
