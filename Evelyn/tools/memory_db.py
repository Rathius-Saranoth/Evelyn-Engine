# memory_db.py
# date created: 2026-05-24 09:51:58
# date modified: 2026-08-30 16:33:03
# tags: #database, #sqlite, #memory, #schemas, #connections

"""
memory_db.py — SQLite access layer for Evelyn's context memory database.

Provides CRUD operations for the context_entries and proposals tables
in evelyn_memory.db. Keeps context/memory data separate from chat history
(evelyn_chat.db).

Schema:
  context_entries — Stores all context facts (live, extracted, pending_review).
                    Replaces the Cat##/Cat##-{U,A}/*.md flat-file layout.
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
  entry_id = memory_db.insert_entry(category='Cat05-U', subject='Ricky', ...)
  entries  = memory_db.get_entries_by_category('Cat05-U')
  memory_db.touch_entry_retrieved(entry_id)   # Fire-and-forget RAG retrieval tracking
  memory_db.touch_entry_evolved(entry_id, ts) # Fire-and-forget; called on profile_update approval
  memory_db.increment_entry_observed(entry_id) # Increment observed_count on duplicate merge

All functions use short-lived connections (no module-level state).
"""

import contextlib
from datetime import UTC, datetime
import json
import re
import sqlite3
import time

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
    con.execute("PRAGMA foreign_keys = ON")
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
        with contextlib.suppress(sqlite3.OperationalError):
            con.execute(_migration)

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

    con.execute("""
        CREATE TABLE IF NOT EXISTS entry_document_evolution (
            entry_id      INTEGER NOT NULL,
            document_name TEXT NOT NULL,
            evolved_at    REAL NOT NULL,
            PRIMARY KEY (entry_id, document_name),
            FOREIGN KEY(entry_id) REFERENCES context_entries(id) ON DELETE CASCADE
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
        "CREATE INDEX IF NOT EXISTS idx_ede_doc_entry ON entry_document_evolution(document_name, entry_id)",
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
    date: str | None = None,
    tags: str | None = None,
) -> int:
    """Insert a new context entry and return its row ID.

    Args:
        category: Category code, e.g. 'Cat05-U'.
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


def get_entry(entry_id: int) -> dict | None:
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
        category: Category code, e.g. 'Cat05-U'.
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


def get_all_entries(statuses: list[str] | None = None) -> list[dict]:
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
    values = [*list(updates.values()), entry_id]

    con = get_db()
    cur = con.execute(
        f"UPDATE context_entries SET {set_clause} WHERE id = ?", values
    )
    con.commit()
    affected = cur.rowcount
    con.close()
    return affected > 0


def get_entries_by_category_for_document(
    category: str, document_name: str, status: str = "live"
) -> list[dict]:
    """Fetch qualifying un-evolved (or modified since evolution) entries for a specific document.

    Uses a LEFT JOIN on entry_document_evolution for the target document so that
    entries evolved for other documents remain eligible for this document.

    Args:
        category: Category code, e.g. 'Cat05-U'.
        document_name: Target document filename, e.g. 'Ricky_Narrative_Profile.md'.
        status: Filter by status. Default 'live'.

    Returns:
        list[dict]: A list of qualifying entry dictionaries, sorted chronologically.
    """
    con = get_db()
    query = """
        SELECT ce.*
        FROM context_entries ce
        LEFT JOIN entry_document_evolution ede
               ON ede.entry_id = ce.id
              AND ede.document_name = ?
        WHERE ce.category = ?
          AND ce.status = ?
          AND (ede.evolved_at IS NULL OR ce.updated_at > ede.evolved_at)
        ORDER BY ce.date ASC, ce.id ASC
    """
    rows = con.execute(query, (document_name, category, status)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_entry_document_evolutions(entry_id: int) -> dict[str, float]:
    """Fetch all per-document evolution timestamps for a given entry.

    Args:
        entry_id: Row ID of the context entry.

    Returns:
        dict[str, float]: Mapping of document_name to evolved_at timestamp.
    """
    con = get_db()
    rows = con.execute(
        "SELECT document_name, evolved_at FROM entry_document_evolution WHERE entry_id = ?",
        (entry_id,),
    ).fetchall()
    con.close()
    return {r["document_name"]: r["evolved_at"] for r in rows}


def touch_entry_evolved(
    entry_id: int,
    document_name: str | None = None,
    timestamp: float | None = None,
) -> None:
    """Update evolution timestamp for a context entry.

    When document_name is provided, records the evolution event specifically
    for that target document in entry_document_evolution. Also maintains the
    legacy last_evolved_at column on context_entries as a global fallback.

    Args:
        entry_id: Row ID of the context entry.
        document_name: Optional target document filename (e.g. 'Ricky_Narrative_Profile.md').
        timestamp: Unix timestamp. Defaults to current time.
    """
    ts = timestamp or time.time()
    try:
        con = get_db()
        if document_name:
            con.execute(
                """
                INSERT INTO entry_document_evolution (entry_id, document_name, evolved_at)
                VALUES (?, ?, ?)
                ON CONFLICT(entry_id, document_name) DO UPDATE SET evolved_at = excluded.evolved_at
                """,
                (entry_id, document_name, ts),
            )
        con.execute(
            "UPDATE context_entries SET last_evolved_at = ? WHERE id = ?",
            (ts, entry_id),
        )
        con.commit()
        con.close()
    except sqlite3.Error as e:
        print(
            f"[MEMORY_DB] Warning: failed to update evolution state for entry {entry_id} (doc={document_name}): {e}"
        )


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
    except sqlite3.Error as e:
        print(f"[MEMORY_DB] Warning: failed to increment observed count for entry {entry_id}: {e}")


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
    except sqlite3.Error:
        pass  # Tracking failure must never propagate to the caller


def delete_entry(entry_id: int) -> bool:
    """Soft delete a context entry by ID.

    Args:
        entry_id: Row ID of the entry to soft delete.

    Returns:
        bool: True if updated, False otherwise.
    """
    return update_entry(entry_id, status="deleted")


def hard_delete_entry(entry_id: int) -> bool:
    """Permanently delete a context entry from the database.

    Args:
        entry_id: Row ID of the context entry.

    Returns:
        bool: True if deleted, False otherwise.
    """
    con = get_db()
    remove_source_id_from_pending_proposals(entry_id)
    cur = con.execute("DELETE FROM context_entries WHERE id = ?", (entry_id,))
    con.commit()
    affected = cur.rowcount
    con.close()
    return affected > 0


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
            cat = item.get("category") or source.get("category", f"Cat05-{getattr(cfg, 'SUBJECT_CODE_USER', 'U')}")
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



def count_entries(status: str | None = None) -> int:
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
    except sqlite3.Error as e:
        print(f"[MEMORY_DB] Error enqueueing entry {entry_id} for split: {e}")
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
        "what", "will", "your", "also", "back", "come", "could",
        "does", "even", "good", "here", "know", "more", "most", "much",
        "only", "over", "such", "take", "their", "well", "were", "which",
        "about", "after", "being", "every", "first", "other",
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
    merged_observation: str | None = None,
    merged_tags: str | None = None,
    suggested_category: str | None = None,
    reason: str | None = None,
    topic: str | None = None,
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
    type: str | None = None,
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


def delete_proposal(proposal_id: int) -> bool:
    """Permanently delete a proposal from the database.

    Args:
        proposal_id: Row ID of the proposal.

    Returns:
        bool: True if deleted, False otherwise.
    """
    con = get_db()
    cur = con.execute("DELETE FROM proposals WHERE id = ?", (proposal_id,))
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
    values = [*list(updates.values()), proposal_id]

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


def has_pending_proposal_for(entry_ids: list[int], type: str | None = None) -> bool:
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
    pitfalls: str | None = None,
    verification: str | None = None,
    source: str = "extracted",
    status: str = "live",
    tags: str | None = None,
    suggested_tools: str | None = None,
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


def get_procedure(proc_id: int) -> dict | None:
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


def get_all_procedures(status: str | None = "live") -> list[dict]:
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
        list[dict]: List of matching procedure dictionaries sorted by relevance.
    """
    stopwords = {
        "when", "what", "with", "that", "this", "your", "have", "from", "about",
        "user", "says", "asks", "tells", "like", "will", "would", "could", "should",
        "they", "them", "their", "there", "then", "into", "onto", "over", "under",
        "make", "want", "need", "some", "time", "just", "also", "been", "were",
        "here", "more", "done", "know", "good", "well", "very"
    }
    raw_words = [w.strip(".,;:!?\"'()[]{}") for w in query.lower().split()]
    query_kws = {w for w in raw_words if len(w) >= 3 and w not in stopwords}
    if not query_kws:
        return []

    con = get_db()
    rows = con.execute("SELECT * FROM procedures WHERE status = ?", (status,)).fetchall()
    con.close()

    scored = []
    for r in rows:
        p_dict = dict(r)
        trigger_text = f"{p_dict.get('trigger_pattern') or ''} {p_dict.get('tags') or ''}".lower()
        trigger_words = set(re.findall(r"\b[a-z0-9_]{3,}\b", trigger_text)) - stopwords
        overlap = query_kws & trigger_words
        if not overlap:
            continue

        score = len(overlap)
        scored.append((score, p_dict.get("retrieval_count", 0), p_dict))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [item[2] for item in scored]


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
    except sqlite3.Error:
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
    values = [*list(updates.values()), proc_id]

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


def hard_delete_procedure(proc_id: int) -> bool:
    """Permanently delete a procedure from the database and remove queue references.

    Args:
        proc_id: Database ID of the procedure.

    Returns:
        bool: True if deleted, False otherwise.
    """
    con = get_db()
    con.execute("DELETE FROM procedure_split_queue WHERE proc_id = ?", (proc_id,))
    # Remove from any pending merge queues
    cursor = con.execute("SELECT id, proc_ids FROM procedure_merge_queue WHERE status = 'pending'")
    for q_id, proc_ids_str in cursor.fetchall():
        ids = [int(x.strip()) for x in proc_ids_str.split(",") if x.strip().isdigit()]
        if proc_id in ids:
            remaining = [x for x in ids if x != proc_id]
            if len(remaining) < 2:
                con.execute("DELETE FROM procedure_merge_queue WHERE id = ?", (q_id,))
            else:
                con.execute(
                    "UPDATE procedure_merge_queue SET proc_ids = ?, updated_at = ? WHERE id = ?",
                    (",".join(str(i) for i in remaining), time.time(), q_id),
                )
    cur = con.execute("DELETE FROM procedures WHERE id = ?", (proc_id,))
    con.commit()
    affected = cur.rowcount
    con.close()
    return affected > 0


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
    except sqlite3.Error as e:
        print(f"[MEMORY_DB] Error enqueueing procedure {proc_id} for split: {e}")
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


# ---------------------------------------------------------------------------
# Ambient Impressions & Ephemeral Feed Support
# ---------------------------------------------------------------------------


def record_ambient_impression(
    type: str,
    content: str,
    source_ref: str | None = None,
    media_id: str | None = None,
    metadata: dict | None = None,
    target_date: str | None = None,
    ts: float | None = None,
) -> int:
    """Record an ambient impression (thought, media share, alert) in the memory database.

    Args:
        type: Impression type ("thought", "media_share", "proactive_msg", "system_alert").
        content: The substantive thought text, media caption, or observation.
        source_ref: Optional origin reference (e.g. "chat:30928", "task:178811").
        media_id: Optional media UUID referencing evelyn_media.db.
        metadata: Optional metadata dictionary (tags, mood, thumbnail URL).
        target_date: Optional local date string (YYYY-MM-DD). Defaults to local today.
        ts: Optional UNIX timestamp. Defaults to current time.

    Returns:
        int: The newly created impression row ID.
    """
    now_ts = ts if ts is not None else time.time()
    if target_date is None:
        target_date = datetime.now(UTC).astimezone().strftime("%Y-%m-%d")

    meta_json = json.dumps(metadata) if metadata is not None else None

    con = get_db()
    try:
        cur = con.execute(
            """INSERT INTO daily_ambient_impressions
               (ts, date, type, content, source_ref, media_id, metadata, consumed, dismissed)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)""",
            (now_ts, target_date, type, content.strip(), source_ref, media_id, meta_json),
        )
        con.commit()
        return int(cur.lastrowid or 0)
    finally:
        con.close()


def get_unconsumed_ambient_impressions(
    target_date: str,
    types: list[str] | None = None,
) -> list[dict]:
    """Retrieve all unconsumed impressions for a given local date (for journal compilation).

    Args:
        target_date: Local calendar date string (YYYY-MM-DD).
        types: Optional list of impression types to filter by.

    Returns:
        list[dict]: Unconsumed impression records ordered chronologically.
    """
    con = get_db()
    try:
        if types:
            placeholders = ",".join("?" for _ in types)
            query = f"""SELECT id, ts, date, type, content, source_ref, media_id, metadata, consumed, dismissed
                        FROM daily_ambient_impressions
                        WHERE date = ? AND consumed = 0 AND type IN ({placeholders})
                        ORDER BY ts ASC"""
            rows = con.execute(query, (target_date, *types)).fetchall()
        else:
            query = """SELECT id, ts, date, type, content, source_ref, media_id, metadata, consumed, dismissed
                       FROM daily_ambient_impressions
                       WHERE date = ? AND consumed = 0
                       ORDER BY ts ASC"""
            rows = con.execute(query, (target_date,)).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            if d.get("metadata"):
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    d["metadata"] = json.loads(d["metadata"])
            results.append(d)
        return results
    finally:
        con.close()


def get_active_ambient_feed(
    limit: int = 10,
    type_filter: str | None = None,
) -> list[dict]:
    """Retrieve active (undismissed) ambient feed items ordered newest-first.

    Utilizes idx_ambient_feed or idx_ambient_type_feed for optimized index scans.

    Args:
        limit: Maximum number of records to return.
        type_filter: Optional type string ("thought", "media_share", etc.).

    Returns:
        list[dict]: Active ambient feed records.
    """
    con = get_db()
    try:
        if type_filter:
            query = """SELECT id, ts, date, type, content, source_ref, media_id, metadata, consumed, dismissed
                       FROM daily_ambient_impressions
                       WHERE type = ? AND dismissed = 0
                       ORDER BY ts DESC LIMIT ?"""
            rows = con.execute(query, (type_filter, limit)).fetchall()
        else:
            query = """SELECT id, ts, date, type, content, source_ref, media_id, metadata, consumed, dismissed
                       FROM daily_ambient_impressions
                       WHERE dismissed = 0
                       ORDER BY ts DESC LIMIT ?"""
            rows = con.execute(query, (limit,)).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            if d.get("metadata"):
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    d["metadata"] = json.loads(d["metadata"])
            results.append(d)
        return results
    finally:
        con.close()


def mark_ambient_impressions_consumed(impression_ids: list[int]) -> None:
    """Mark a list of ambient impression IDs as consumed by the daily journal compiler.

    Args:
        impression_ids: List of impression primary keys.
    """
    if not impression_ids:
        return
    con = get_db()
    try:
        placeholders = ",".join("?" for _ in impression_ids)
        con.execute(
            f"UPDATE daily_ambient_impressions SET consumed = 1 WHERE id IN ({placeholders})",
            impression_ids,
        )
        con.commit()
    finally:
        con.close()


def mark_ambient_impression_dismissed(impression_id: int) -> bool:
    """Mark a single ambient impression as dismissed/read in the UI.

    Args:
        impression_id: Primary key of the impression.

    Returns:
        bool: True if updated, False otherwise.
    """
    con = get_db()
    try:
        cur = con.execute(
            "UPDATE daily_ambient_impressions SET dismissed = 1 WHERE id = ?",
            (impression_id,),
        )
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def get_latest_ambient_impression(type_filter: str = "thought") -> dict | None:
    """Retrieve the single most recent ambient impression of a given type.

    Args:
        type_filter: Impression type string (defaults to "thought").

    Returns:
        dict | None: The latest matching record, or None if none exist.
    """
    items = get_active_ambient_feed(limit=1, type_filter=type_filter)
    return items[0] if items else None
