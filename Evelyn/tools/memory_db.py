# memory_db.py
# date created: 2026-05-24 09:51:58
# date modified: 2026-07-30 20:46:52
# tags: #database, #sqlite, #memory, #schemas, #connections

"""
memory_db.py — SQLite access layer for Evelyn's context memory database.

Provides CRUD operations for the context_entries and proposals tables
in evelyn_memory.db. Keeps context/memory data separate from chat history
(evelyn_chat.db).

Schema:
  context_entries — Stores all context facts (live, extracted, pending_review).
                    Replaces the Cat##/Cat##-{E,R}/*.md flat-file layout.
                    Columns added over time (all idempotent migrations in init_db):
                      last_retrieved_at / retrieval_count — RAG access tracking (2026-06-14)
                      last_evolved_at   — Stamps when profile_evolver incorporated entry (2026-07-30)
                      recategorized_at  — Admin category-change timestamp; kept separate from
                                          updated_at so category fixes don't retrigger evolution (2026-07-30)
                      first_observed / last_observed / observed_count — Recurrence tracking (2026-07-30)
  proposals       — Stores consolidation and recategorization proposals.
                    Replaces CONSOLIDATION_*.md and RECATEGORIZE_*.md files.

Usage:
  import memory_db
  memory_db.init_db()                         # Idempotent — safe to call on every startup
  entry_id = memory_db.insert_entry(category='Cat05-R', subject='Ricky', ...)
  entries  = memory_db.get_entries_by_category('Cat05-R')
  memory_db.touch_entry_retrieved(entry_id)   # Fire-and-forget RAG retrieval tracking
  memory_db.touch_entry_evolved(entry_id, ts) # Fire-and-forget; called on profile_update approval
  memory_db.increment_entry_observed(entry_id) # Increment observed_count on duplicate merge

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
        sqlite3.Connection: A database connection configured for dict-like row access.
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

    Returns:
        None
    """
    con = get_db()

    con.execute("""
        CREATE TABLE IF NOT EXISTS context_entries (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            category          TEXT NOT NULL,
            subject           TEXT NOT NULL,
            observation       TEXT NOT NULL,
            confidence        TEXT NOT NULL DEFAULT 'medium',
            source            TEXT NOT NULL DEFAULT 'manual',
            status            TEXT NOT NULL DEFAULT 'live',
            date              TEXT,
            tags              TEXT,
            created_at        REAL NOT NULL,
            updated_at        REAL,
            last_retrieved_at REAL,
            retrieval_count   INTEGER NOT NULL DEFAULT 0,
            last_evolved_at   REAL,
            recategorized_at  REAL,
            first_observed    REAL,
            last_observed     REAL,
            observed_count    INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Migrate: add usage-tracking and evolution bookkeeping columns if missing on existing databases.
    # sqlite3.backup() runs before this on every consolidation cycle, so the
    # schema change is always protected by a recent hot-copy.
    for _migration in [
        "ALTER TABLE context_entries ADD COLUMN last_retrieved_at REAL",
        "ALTER TABLE context_entries ADD COLUMN retrieval_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE context_entries ADD COLUMN last_evolved_at REAL",
        "ALTER TABLE context_entries ADD COLUMN recategorized_at REAL",
        "ALTER TABLE context_entries ADD COLUMN first_observed REAL",
        "ALTER TABLE context_entries ADD COLUMN last_observed REAL",
        "ALTER TABLE context_entries ADD COLUMN observed_count INTEGER NOT NULL DEFAULT 1",
    ]:
        try:
            con.execute(_migration)
        except Exception:
            pass  # Column already exists — expected on all existing DBs

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

    con.execute("""
        CREATE TABLE IF NOT EXISTS procedures (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_pattern   TEXT NOT NULL,
            steps             TEXT NOT NULL,
            pitfalls          TEXT,
            verification      TEXT,
            source            TEXT NOT NULL DEFAULT 'extracted',
            status            TEXT NOT NULL DEFAULT 'live',
            tags              TEXT,
            created_at        REAL NOT NULL,
            updated_at        REAL,
            last_retrieved_at REAL,
            retrieval_count   INTEGER NOT NULL DEFAULT 0
        )
    """)

    # Indexes — IF NOT EXISTS prevents errors on re-init
    for stmt in [
        "CREATE INDEX IF NOT EXISTS idx_ce_category ON context_entries(category)",
        "CREATE INDEX IF NOT EXISTS idx_ce_status ON context_entries(status)",
        "CREATE INDEX IF NOT EXISTS idx_ce_date ON context_entries(date)",
        "CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status)",
        "CREATE INDEX IF NOT EXISTS idx_proposals_type ON proposals(type)",
        "CREATE INDEX IF NOT EXISTS idx_proc_status ON procedures(status)",
        "CREATE INDEX IF NOT EXISTS idx_proc_trigger ON procedures(trigger_pattern)",
    ]:
        con.execute(stmt)

    con.commit()
    con.close()









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
        category: Category code, e.g. 'Cat05-R'.
        subject: 'Ricky' or 'Evelyn'.
        observation: The factual observation text.
        confidence: 'high', 'medium', or 'low'.
        source: 'manual', 'extracted', or 'consolidated'.
        status: 'live', 'extracted', or 'pending_review'.
        date: Optional YYYY-MM-DD date when the fact was discussed.
        tags: Optional comma-separated keyword tags.

    Returns:
        int: The auto-generated row ID of the new entry.
    """
    now = time.time()
    con = get_db()
    cur = con.execute(
        """INSERT INTO context_entries
           (category, subject, observation, confidence, source, status,
            date, tags, created_at, first_observed, last_observed, observed_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (category, subject, observation, confidence, source, status,
         date, tags, now, now, now),
    )
    row_id = cur.lastrowid
    con.commit()
    con.close()
    return row_id


def get_entry(entry_id: int) -> Optional[dict]:
    """Fetch a single context entry by ID.

    Args:
        entry_id: The database ID of the entry.

    Returns:
        dict | None: The entry record as a dictionary, or None if not found.
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
        status: Filter by status. Default 'live'.

    Returns:
        list[dict]: A list of entry dictionaries, sorted chronologically.
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
        statuses: List of status values to include. Default is ['live'].

    Returns:
        list[dict]: A list of entry dictionaries, sorted by category and date.
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

    Args:
        entry_id: Row ID of the entry to update.
        **fields: Key-value pairs of columns and their new values.

    Returns:
        bool: True if the row was found and updated, False otherwise.
    """
    valid_cols = {
        "category", "subject", "observation", "confidence", "source",
        "status", "date", "tags", "last_retrieved_at", "retrieval_count",
        "last_evolved_at", "recategorized_at", "first_observed", "last_observed", "observed_count",
    }
    updates = {k: v for k, v in fields.items() if k in valid_cols}
    if not updates:
        return False

    now = time.time()
    if "category" in updates and "recategorized_at" not in updates:
        updates["recategorized_at"] = now

    if any(k in updates for k in ("observation", "subject", "status", "confidence")) and "updated_at" not in updates:
        updates["updated_at"] = now

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


def touch_entry_evolved(entry_id: int, timestamp: Optional[float] = None) -> None:
    """Update last_evolved_at timestamp for a context entry.

    Called when an entry has been processed into a profile_update proposal.

    Args:
        entry_id: Row ID of the context entry.
        timestamp: Unix timestamp. Defaults to current time.
    """
    ts = timestamp or time.time()
    try:
        con = get_db()
        con.execute(
            "UPDATE context_entries SET last_evolved_at = ? WHERE id = ?",
            (ts, entry_id),
        )
        con.commit()
        con.close()
    except Exception:
        pass


def increment_entry_observed(entry_id: int, count_delta: int = 1) -> None:
    """Increment observed_count and set last_observed to current time.

    Args:
        entry_id: Row ID of the context entry.
        count_delta: Amount to increase observed_count by. Default 1.
    """
    now = time.time()
    try:
        con = get_db()
        con.execute(
            """
            UPDATE context_entries
            SET observed_count = COALESCE(observed_count, 1) + ?,
                last_observed  = ?
            WHERE id = ?
            """,
            (count_delta, now, entry_id),
        )
        con.commit()
        con.close()
    except Exception:
        pass


def touch_entry_retrieved(entry_id: int) -> None:
    """Increment retrieval_count and update last_retrieved_at for a context entry.

    Called whenever a context entry is served to the model via RAG retrieval.
    Provides data for future memory health analysis: entries with retrieval_count
    of zero are candidates for pruning, consolidation, or quality review.

    This function is fire-and-forget: all exceptions are silently suppressed so
    that a tracking write failure never affects the caller or RAG delivery.

    Args:
        entry_id: Row ID of the context entry that was retrieved.
    """
    try:
        con = get_db()
        con.execute(
            """
            UPDATE context_entries
            SET retrieval_count   = retrieval_count + 1,
                last_retrieved_at = ?
            WHERE id = ?
            """,
            (time.time(), entry_id),
        )
        con.commit()
        con.close()
    except Exception:
        pass  # Tracking failure must never propagate to the caller


def delete_entry(entry_id: int) -> bool:
    """Soft delete a context entry by ID.

    Args:
        entry_id: Row ID of the entry to soft delete.

    Returns:
        bool: True if updated, False otherwise.
    """
    return update_entry(entry_id, status="deleted")


def count_entries(status: Optional[str] = None) -> int:
    """Count context entries, optionally filtered by status.

    Args:
        status: Optional status string to filter by.

    Returns:
        int: The count of matching entries.
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
    """Return a dict of category -> count for all live entries.

    Returns:
        dict[str, int]: Mapping of category codes to their respective entry counts.
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

    Args:
        category: Category to search within.
        observation_text: The candidate observation to check against.
        min_overlap: Minimum keyword overlap ratio (0.0–1.0). Default 0.5.
        status: Filter by status. Default 'live'.

    Returns:
        list[dict]: Matching entry dictionaries with an added 'overlap' key.
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

    Args:
        text: Raw observation string.

    Returns:
        set[str]: Set of lowercase keyword strings.
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
        type: 'merge', 'supersede', 'recategorize', 'keep_both'.
        source_ids: List of context_entry IDs involved.
        merged_observation: Proposed merged text.
        merged_tags: Proposed tags.
        suggested_category: New category code.
        reason: Reasoning for the proposal.
        topic: Topic label.
        confidence: Proposal confidence level.
        status: Status of the proposal.

    Returns:
        int: The auto-generated row ID.
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
        type: Optional proposal type to filter by.

    Returns:
        list[dict]: A list of proposal dictionaries.
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
        bool: True if updated, False otherwise.
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
        bool: True if updated, False otherwise.
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

    Args:
        entry_ids: List of context_entry IDs to check.
        type: Optional proposal type to restrict checking to.

    Returns:
        bool: True if at least one pending proposal matches, False otherwise.
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


# ===========================================================================
# Procedures — CRUD
# ===========================================================================

def insert_procedure(
    trigger_pattern: str,
    steps: str,
    pitfalls: Optional[str] = None,
    verification: Optional[str] = None,
    source: str = "extracted",
    status: str = "live",
    tags: Optional[str] = None,
) -> int:
    """Insert a new procedure and return its row ID.

    Args:
        trigger_pattern: Description of the situation or trigger.
        steps: Markdown list of instructions to carry out.
        pitfalls: Mistakes or warnings.
        verification: Success confirmation.
        source: 'extracted', 'manual', or 'model'.
        status: 'live', 'extracted', or 'archived'.
        tags: Semantic tags.

    Returns:
        int: The database row ID of the new procedure.
    """
    con = get_db()
    cur = con.execute(
        """INSERT INTO procedures
           (trigger_pattern, steps, pitfalls, verification, source, status,
            tags, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (trigger_pattern, steps, pitfalls, verification, source, status,
         tags, time.time()),
    )
    row_id = cur.lastrowid
    con.commit()
    con.close()
    return row_id


def get_procedure(proc_id: int) -> Optional[dict]:
    """Fetch a single procedure by ID.

    Args:
        proc_id: Row ID of the procedure.

    Returns:
        dict | None: The procedure record as a dict, or None if not found.
    """
    con = get_db()
    row = con.execute(
        "SELECT * FROM procedures WHERE id = ?", (proc_id,)
    ).fetchone()
    con.close()
    return dict(row) if row else None


def get_all_procedures(status: str = "live") -> list[dict]:
    """Fetch all procedures matching a given status.

    Args:
        status: Status filter, e.g. 'live' or 'extracted'.

    Returns:
        list[dict]: A list of procedure dictionaries.
    """
    con = get_db()
    rows = con.execute(
        "SELECT * FROM procedures WHERE status = ? ORDER BY created_at DESC",
        (status,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def search_procedures_by_trigger(query: str, status: str = "live") -> list[dict]:
    """Find procedures whose trigger pattern matches keywords in the query text.

    Args:
        query: User message query string.
        status: Filter status, default 'live'.

    Returns:
        list[dict]: List of matching procedure dictionaries.
    """
    # Simple word tokenization to build a search pattern
    words = [w.strip(".,;:!?") for w in query.lower().split() if len(w) > 3]
    if not words:
        return []

    con = get_db()
    # We do a direct LIKE check for the first few main keywords or direct match
    clauses = []
    params = [status]
    for w in words[:4]:
        clauses.append("trigger_pattern LIKE ?")
        params.append(f"%{w}%")

    query_str = f"SELECT * FROM procedures WHERE status = ? AND ({' OR '.join(clauses)}) ORDER BY retrieval_count DESC"
    rows = con.execute(query_str, params).fetchall()
    con.close()
    return [dict(r) for r in rows]


def touch_procedure_retrieved(proc_id: int) -> None:
    """Increment retrieval count and update last retrieval timestamp of a procedure.

    Args:
        proc_id: Database ID of the procedure.
    """
    try:
        con = get_db()
        con.execute(
            """UPDATE procedures
               SET retrieval_count = retrieval_count + 1,
                   last_retrieved_at = ?
               WHERE id = ?""",
            (time.time(), proc_id),
        )
        con.commit()
        con.close()
    except Exception:
        pass


def update_procedure(proc_id: int, **fields) -> bool:
    """Update specific fields of a procedure.

    Args:
        proc_id: Database ID of the procedure.
        **fields: Keyword arguments of column names and values to update.

    Returns:
        bool: True if updated, False otherwise.
    """
    valid_cols = {
        "trigger_pattern", "steps", "pitfalls", "verification",
        "source", "status", "tags", "last_retrieved_at", "retrieval_count",
    }
    updates = {k: v for k, v in fields.items() if k in valid_cols}
    if not updates:
        return False
    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [proc_id]

    con = get_db()
    cur = con.execute(
        f"UPDATE procedures SET {set_clause} WHERE id = ?", values
    )
    con.commit()
    affected = cur.rowcount
    con.close()
    return affected > 0


def delete_procedure(proc_id: int) -> bool:
    """Soft delete a procedure by changing status to 'archived'.

    Args:
        proc_id: Database ID of the procedure.

    Returns:
        bool: True if archived, False otherwise.
    """
    return update_procedure(proc_id, status="archived")
