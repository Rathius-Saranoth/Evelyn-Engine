# memory_db.py
# date created: 2026-05-24 09:51:58
# date modified: 2026-08-19 19:46:33
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
        "ALTER TABLE context_entries ADD COLUMN vad TEXT",
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

    con.execute("""
        CREATE TABLE IF NOT EXISTS chroma_sync_queue (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            action              TEXT NOT NULL,
            source_path         TEXT NOT NULL,
            collection_name     TEXT NOT NULL DEFAULT 'evelyn_memory',
            content             TEXT,
            extra_metadata_json TEXT,
            status              TEXT NOT NULL DEFAULT 'pending',
            retry_count         INTEGER NOT NULL DEFAULT 0,
            error_msg           TEXT,
            created_at          REAL NOT NULL,
            updated_at          REAL NOT NULL
        )
    """)

    con.execute("""
        CREATE TABLE IF NOT EXISTS split_queue (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id   INTEGER NOT NULL UNIQUE,
            status     TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
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
        "CREATE INDEX IF NOT EXISTS idx_csq_status ON chroma_sync_queue(status)",
        "CREATE INDEX IF NOT EXISTS idx_csq_source ON chroma_sync_queue(source_path, collection_name)",
        "CREATE INDEX IF NOT EXISTS idx_sq_status ON split_queue(status)",
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
        "vad",
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


def split_entry(source_entry_id: int, new_entries: list[dict]) -> list[int]:
    """Split a single compound context entry into multiple atomic context entries.

    Atomically soft-deletes the source entry and inserts the new child entries.
    Also removes the source_entry_id from any pending proposals.

    Args:
        source_entry_id: Row ID of the composite context entry to split.
        new_entries: List of dicts representing the atomic child entries to insert.
            Each dict should have keys: category, subject, observation, and optional tags, date, confidence.

    Returns:
        list[int]: List of newly generated row IDs.
    """
    if not new_entries:
        return []

    source = get_entry(source_entry_id)
    if not source:
        return []

    default_subject = source.get("subject", getattr(cfg, "USER_NAME", "Ricky"))
    default_date = source.get("date")
    default_status = source.get("status", "live")
    parent_first_obs = source.get("first_observed") or source.get("created_at") or time.time()
    parent_last_obs = source.get("last_observed") or time.time()
    parent_obs_count = source.get("observed_count") or 1
    now = time.time()

    new_ids = []
    con = get_db()
    try:
        # 1. Soft-delete source entry
        con.execute(
            "UPDATE context_entries SET status = 'deleted', updated_at = ? WHERE id = ?",
            (now, source_entry_id),
        )

        # 2. Insert new child entries (inheriting temporal lineage from parent)
        for item in new_entries:
            cat = item.get("category") or source.get("category", "Cat05-R")
            subj = item.get("subject") or default_subject
            obs = item.get("observation", "").strip()
            if not obs:
                continue
            conf = item.get("confidence", "medium")
            tags = item.get("tags")
            entry_date = item.get("date") or default_date
            status = item.get("status") or default_status
            first_obs = item.get("first_observed") or parent_first_obs
            last_obs = item.get("last_observed") or parent_last_obs
            obs_cnt = item.get("observed_count") or parent_obs_count

            cur = con.execute(
                """INSERT INTO context_entries
                   (category, subject, observation, confidence, source, status,
                    date, tags, created_at, first_observed, last_observed, observed_count)
                   VALUES (?, ?, ?, ?, 'split', ?, ?, ?, ?, ?, ?, ?)""",
                (cat, subj, obs, conf, status, entry_date, tags, now, first_obs, last_obs, obs_cnt),
            )
            new_ids.append(cur.lastrowid)

        con.commit()
    finally:
        con.close()

    # 3. Clean up proposals referencing source_entry_id
    remove_source_id_from_pending_proposals(source_entry_id)

    return new_ids



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
# Split queue support (for prioritized consolidator splitting)
# ---------------------------------------------------------------------------


def enqueue_split(entry_id: int) -> bool:
    """Enqueue a context entry to be evaluated for splitting during the next consolidation run.

    Args:
        entry_id: Row ID of the context entry to queue.

    Returns:
        bool: True if queued successfully.
    """
    con = get_db()
    now = time.time()
    try:
        con.execute(
            """INSERT INTO split_queue (entry_id, status, created_at, updated_at)
               VALUES (?, 'pending', ?, ?)
               ON CONFLICT(entry_id) DO UPDATE SET status = 'pending', updated_at = ?""",
            (entry_id, now, now, now),
        )
        con.commit()
        return True
    except Exception:
        return False
    finally:
        con.close()


def get_split_queue(status: str = "pending") -> list[dict]:
    """Retrieve all context entries currently in the split review queue.

    Args:
        status: Filter by queue status ('pending', 'completed', etc.). Default 'pending'.

    Returns:
        list[dict]: List of queue record dicts.
    """
    con = get_db()
    rows = con.execute(
        "SELECT id, entry_id, status, created_at, updated_at FROM split_queue WHERE status = ? ORDER BY created_at ASC",
        (status,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def dequeue_split(entry_id: int) -> bool:
    """Remove or mark completed a context entry in the split review queue.

    Args:
        entry_id: Row ID of the context entry.

    Returns:
        bool: True if deleted or marked.
    """
    con = get_db()
    try:
        cur = con.execute("DELETE FROM split_queue WHERE entry_id = ?", (entry_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def get_all_queued_split_entry_ids() -> set[int]:
    """Return a set of all entry IDs currently pending in the split review queue."""
    con = get_db()
    rows = con.execute("SELECT entry_id FROM split_queue WHERE status = 'pending'").fetchall()
    con.close()
    return {r["entry_id"] for r in rows}


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
    _STATIC_STOPWORDS = {
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
        "these", "there", "should", "really",
    }
    stopwords = _STATIC_STOPWORDS | {
        getattr(cfg, "USER_NAME", "Ricky").lower(),
        getattr(cfg, "ASSISTANT_NAME", "Evelyn").lower(),
    }
    words = set()
    for word in text.lower().split():
        # Strip punctuation from edges
        clean = word.strip(".,;:!?\"'()[]{}—–-")
        if len(clean) >= 3 and clean not in stopwords:
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


def update_proposal(proposal_id: int, **fields) -> bool:
    """Update specific fields on an existing proposal.

    Args:
        proposal_id: Row ID of the proposal to update.
        **fields: Key-value pairs of columns and their new values.

    Returns:
        bool: True if updated, False otherwise.
    """
    valid_cols = {
        "type", "source_ids", "merged_observation", "merged_tags",
        "suggested_category", "reason", "topic", "confidence", "status"
    }
    updates = {k: v for k, v in fields.items() if k in valid_cols}
    if not updates:
        return False

    if isinstance(updates.get("source_ids"), (list, dict)):
        updates["source_ids"] = json.dumps(updates["source_ids"])

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [proposal_id]

    con = get_db()
    cur = con.execute(
        f"UPDATE proposals SET {set_clause} WHERE id = ?", values
    )
    con.commit()
    affected = cur.rowcount
    con.close()
    return affected > 0


def remove_proposal_source_id(proposal_id: int, entry_id: int) -> bool:
    """Remove a source entry ID from a proposal's source_ids list.

    Args:
        proposal_id: Row ID of the proposal.
        entry_id: Row ID of the context entry to remove.

    Returns:
        bool: True if source_id was present and removed, False otherwise.
    """
    con = get_db()
    row = con.execute("SELECT source_ids FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
    con.close()
    if not row or not row["source_ids"]:
        return False
    try:
        source_ids = json.loads(row["source_ids"])
    except (json.JSONDecodeError, TypeError):
        source_ids = []

    if entry_id in source_ids:
        source_ids = [s for s in source_ids if s != entry_id]
        return update_proposal(proposal_id, source_ids=source_ids)
    return False


def remove_source_id_from_pending_proposals(entry_id: int) -> int:
    """Remove an entry ID from source_ids across all pending proposals.

    Args:
        entry_id: Row ID of the context entry.

    Returns:
        int: Number of pending proposals updated.
    """
    pending = get_pending_proposals()
    updated_count = 0
    for p in pending:
        sids = p.get("source_ids", [])
        if entry_id in sids:
            new_sids = [s for s in sids if s != entry_id]
            if update_proposal(p["id"], source_ids=new_sids):
                updated_count += 1
    return updated_count


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
# Procedures — CRUD & Queue Management
# ===========================================================================

def insert_procedure(
    trigger_pattern: str,
    steps: str,
    pitfalls: Optional[str] = None,
    verification: Optional[str] = None,
    source: str = "extracted",
    status: str = "live",
    tags: Optional[str] = None,
    suggested_tools: Optional[str] = None,
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
        suggested_tools: Comma-separated list of engine tool names (e.g. 'write_file, read_file').

    Returns:
        int: The database row ID of the new procedure.
    """
    con = get_db()
    cur = con.execute(
        """INSERT INTO procedures
           (trigger_pattern, steps, pitfalls, verification, source, status,
            tags, suggested_tools, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (trigger_pattern, steps, pitfalls, verification, source, status,
         tags, suggested_tools, time.time()),
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


def get_all_procedures(status: Optional[str] = "live") -> list[dict]:
    """Fetch all procedures matching a given status (or all if status is None or 'all').

    Args:
        status: Status filter, e.g. 'live', 'extracted', 'archived', or None/'all'.

    Returns:
        list[dict]: A list of procedure dictionaries.
    """
    con = get_db()
    if status and status != "all":
        rows = con.execute(
            "SELECT * FROM procedures WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT * FROM procedures ORDER BY created_at DESC"
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
    stopwords = {
        "when", "what", "with", "that", "this", "your", "have", "from", "about",
        "user", "says", "asks", "tells", "like", "will", "would", "could", "should",
        "they", "them", "their", "there", "then", "into", "onto", "over", "under",
        "make", "want", "need", "some", "time", "just", "also", "been", "were"
    }
    raw_words = [w.strip(".,;:!?\"'()[]{}") for w in query.lower().split()]
    meaningful_words = [w for w in raw_words if len(w) > 3 and w not in stopwords]
    if not meaningful_words:
        meaningful_words = [w for w in raw_words if len(w) > 3]
    if not meaningful_words:
        return []

    con = get_db()
    clauses = []
    params = [status]
    for w in meaningful_words[:4]:
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
        "source", "status", "tags", "suggested_tools", "last_retrieved_at", "retrieval_count",
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


# ---------------------------------------------------------------------------
# Procedure Queue Management (Manual Merge & Split)
# ---------------------------------------------------------------------------

def enqueue_procedure_merge(proc_ids: list[int]) -> int:
    """Enqueue a list of procedure IDs to be merged during the next consolidation pass.

    Args:
        proc_ids: List of procedure row IDs.

    Returns:
        int: Row ID of the created queue item.
    """
    con = get_db()
    now = time.time()
    ids_str = ",".join(str(i) for i in sorted(set(proc_ids)))
    cur = con.execute(
        """INSERT INTO procedure_merge_queue (proc_ids, status, created_at, updated_at)
           VALUES (?, 'pending', ?, ?)""",
        (ids_str, now, now),
    )
    queue_id = cur.lastrowid
    con.commit()
    con.close()
    return queue_id


def get_procedure_merge_queue(status: str = "pending") -> list[dict]:
    """Retrieve all pending procedure merge queue items.

    Args:
        status: Filter by status ('pending', 'completed', etc.).

    Returns:
        list[dict]: Queue records with parsed 'proc_ids' list.
    """
    con = get_db()
    rows = con.execute(
        "SELECT id, proc_ids, status, created_at, updated_at FROM procedure_merge_queue WHERE status = ? ORDER BY created_at ASC",
        (status,),
    ).fetchall()
    con.close()
    results = []
    for r in rows:
        d = dict(r)
        d["proc_id_list"] = [int(x) for x in d["proc_ids"].split(",") if x.strip().isdigit()]
        results.append(d)
    return results


def dequeue_procedure_merge(queue_id: int) -> bool:
    """Remove a merge queue item once processed.

    Args:
        queue_id: Row ID of the queue item.

    Returns:
        bool: True if deleted.
    """
    con = get_db()
    try:
        cur = con.execute("DELETE FROM procedure_merge_queue WHERE id = ?", (queue_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def get_all_queued_procedure_merge_ids() -> set[int]:
    """Return a set of all procedure IDs currently waiting in the merge queue."""
    queued = get_procedure_merge_queue(status="pending")
    all_ids = set()
    for item in queued:
        all_ids.update(item.get("proc_id_list", []))
    return all_ids


def enqueue_procedure_split(proc_id: int) -> bool:
    """Enqueue a procedure to be evaluated for splitting during the next consolidation pass.

    Args:
        proc_id: Row ID of the procedure.

    Returns:
        bool: True if enqueued.
    """
    con = get_db()
    now = time.time()
    try:
        con.execute(
            """INSERT INTO procedure_split_queue (proc_id, status, created_at, updated_at)
               VALUES (?, 'pending', ?, ?)
               ON CONFLICT(proc_id) DO UPDATE SET status = 'pending', updated_at = ?""",
            (proc_id, now, now, now),
        )
        con.commit()
        return True
    except Exception:
        return False
    finally:
        con.close()


def get_procedure_split_queue(status: str = "pending") -> list[dict]:
    """Retrieve all procedures currently in the split queue."""
    con = get_db()
    rows = con.execute(
        "SELECT id, proc_id, status, created_at, updated_at FROM procedure_split_queue WHERE status = ? ORDER BY created_at ASC",
        (status,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def dequeue_procedure_split(proc_id: int) -> bool:
    """Remove a procedure from the split queue once processed."""
    con = get_db()
    try:
        cur = con.execute("DELETE FROM procedure_split_queue WHERE proc_id = ?", (proc_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def get_all_queued_procedure_split_ids() -> set[int]:
    """Return a set of procedure IDs currently waiting in the split queue."""
    items = get_procedure_split_queue(status="pending")
    return {item["proc_id"] for item in items}
