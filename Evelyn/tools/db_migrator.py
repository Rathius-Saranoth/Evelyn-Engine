# db_migrator.py
# date created: 2026-08-29 07:46:44
# date modified: 2026-09-26 17:20:01
# tags: #[database, #migrations, #schema, #evelyn]

"""
Evelyn Engine Database Migration Framework.

Provides transactional, per-database schema migration tracking, Python data
transformation callables, safety backups, post-migration sync hooks, and fail-fast
schema validation.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import evelyn_config as cfg
from Evelyn.version import __version__, compare_versions, normalize_version

logger = logging.getLogger("evelyn.db_migrator")

# Canonical database path mapping
DB_MAP: dict[str, str] = {
    "chat": cfg.CHAT_DB_PATH,
    "memory": cfg.MEMORY_DB_PATH,
    "vault": cfg.VAULT_DB_PATH,
    "media": getattr(cfg, "MEDIA_DB_PATH", os.path.join(cfg.BASE_DIR, "data", "evelyn_media.db")),
}

BACKUP_DIR = os.path.join(cfg.BASE_DIR, "data", "backups")


class DatabaseSchemaMismatchError(RuntimeError):
    """Raised when one or more database schemas do not match the expected application version."""


class MigrationExecutionError(RuntimeError):
    """Raised when a migration step fails to execute."""


@dataclass
class Migration:
    """Defines a single versioned migration unit for a specific database."""
    target_db: str
    version: str
    name: str
    up_sql: str | None = None
    up_fn: Callable[[sqlite3.Connection, dict[str, str], object], None] | None = None
    post_sync_chroma: bool = False
    reindex_vault: bool = False

    def __post_init__(self):
        self.version = normalize_version(self.version)
        if not self.up_sql and not self.up_fn:
            raise ValueError(f"Migration {self.version} ({self.name}) must have up_sql or up_fn defined.")


# ============================================================================
# Baseline Schema Definitions for Version 000.004.000
# ============================================================================

BASELINE_CHAT_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    role          TEXT NOT NULL,
    content       TEXT NOT NULL,
    thinking      TEXT,
    ts            REAL NOT NULL,
    tools_used    TEXT,
    tool_metadata TEXT,
    channel_id    TEXT DEFAULT 'main'
);

CREATE TABLE IF NOT EXISTS message_metrics (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id           INTEGER NOT NULL,
    prompt_eval_count    INTEGER,
    prompt_eval_duration REAL,
    eval_count           INTEGER,
    eval_duration        REAL,
    total_duration       REAL,
    load_duration        REAL,
    think_effort         TEXT,
    think_source         TEXT,
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id          TEXT PRIMARY KEY,
    summary     TEXT NOT NULL,
    description TEXT,
    start_at    TEXT NOT NULL,
    end_at      TEXT NOT NULL,
    location    TEXT,
    source      TEXT NOT NULL DEFAULT 'google',
    last_sync   TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
    USING fts5(content, role UNINDEXED, content='messages', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS messages_fts_insert
    AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, content, role)
        VALUES (new.id, new.content, new.role);
    END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete
    AFTER DELETE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, content, role)
        VALUES ('delete', old.id, old.content, old.role);
    END;

CREATE TRIGGER IF NOT EXISTS messages_fts_update
    AFTER UPDATE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, content, role)
        VALUES ('delete', old.id, old.content, old.role);
        INSERT INTO messages_fts(rowid, content, role)
        VALUES (new.id, new.content, new.role);
    END;
"""

BASELINE_MEMORY_SQL = """
CREATE TABLE IF NOT EXISTS context_entries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    category          TEXT NOT NULL,
    subject           TEXT NOT NULL,
    observation       TEXT NOT NULL,
    confidence        TEXT NOT NULL DEFAULT 'medium',
    source            TEXT NOT NULL DEFAULT 'manual',
    status            TEXT NOT NULL DEFAULT 'live',
    date              TEXT,
    created_at        REAL NOT NULL,
    updated_at        REAL,
    tags              TEXT,
    last_retrieved_at REAL,
    retrieval_count   INTEGER NOT NULL DEFAULT 0,
    last_evolved_at   REAL,
    recategorized_at  REAL,
    first_observed    REAL,
    last_observed     REAL,
    observed_count    INTEGER NOT NULL DEFAULT 1,
    vad               TEXT
);

CREATE INDEX IF NOT EXISTS idx_ce_category ON context_entries(category);
CREATE INDEX IF NOT EXISTS idx_ce_status ON context_entries(status);
CREATE INDEX IF NOT EXISTS idx_ce_date ON context_entries(date);

CREATE TABLE IF NOT EXISTS proposals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    type                TEXT NOT NULL,
    source_ids          TEXT NOT NULL,
    merged_observation  TEXT,
    suggested_category  TEXT,
    reason              TEXT,
    topic               TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    created_at          REAL NOT NULL,
    reviewed_at         REAL,
    merged_tags         TEXT,
    confidence          TEXT DEFAULT 'medium'
);

CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposals_type ON proposals(type);

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
);

CREATE INDEX IF NOT EXISTS idx_proc_status ON procedures(status);
CREATE INDEX IF NOT EXISTS idx_proc_trigger ON procedures(trigger_pattern);

CREATE TABLE IF NOT EXISTS heavy_task_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name       TEXT NOT NULL,
    started_at      REAL NOT NULL,
    finished_at     REAL NOT NULL,
    elapsed_seconds REAL NOT NULL,
    status          TEXT NOT NULL,
    error           TEXT,
    items_processed INTEGER DEFAULT 0,
    timestamp       REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_heavy_task_name ON heavy_task_history(task_name);

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
);

CREATE INDEX IF NOT EXISTS idx_csq_status ON chroma_sync_queue(status);
CREATE INDEX IF NOT EXISTS idx_csq_source ON chroma_sync_queue(source_path, collection_name);

CREATE TABLE IF NOT EXISTS split_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id   INTEGER NOT NULL UNIQUE,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sq_status ON split_queue(status);
"""

BASELINE_VAULT_SQL = """
CREATE TABLE IF NOT EXISTS vault_documents (
    path           TEXT PRIMARY KEY,
    title          TEXT,
    mtime          REAL,
    gist           TEXT,
    gist_failed    BOOLEAN,
    rag_priority   TEXT,
    rag_pinned     BOOLEAN,
    tags           TEXT,
    aliases        TEXT,
    indexed_at     REAL,
    last_tag_audit REAL
);

CREATE TABLE IF NOT EXISTS master_tag_taxonomy (
    tag         TEXT PRIMARY KEY,
    category    TEXT,
    description TEXT,
    usage_count INTEGER DEFAULT 0,
    created_at  REAL,
    updated_at  REAL
);
"""

BASELINE_MEDIA_SQL = """
CREATE TABLE IF NOT EXISTS media_assets (
    id              TEXT PRIMARY KEY,
    media_type      TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    file_hash       TEXT NOT NULL UNIQUE,
    mime_type       TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    width           INTEGER,
    height          INTEGER,
    description     TEXT,
    extracted_text  TEXT,
    tags            TEXT,
    taxonomy_domain TEXT,
    metadata_json   TEXT,
    created_ts      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_media_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id    TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
    message_id  INTEGER NOT NULL,
    created_ts  REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_media_hash ON media_assets(file_hash);
CREATE INDEX IF NOT EXISTS idx_media_type ON media_assets(media_type);
CREATE INDEX IF NOT EXISTS idx_links_msg ON chat_media_links(message_id);
CREATE INDEX IF NOT EXISTS idx_links_media ON chat_media_links(media_id);
"""

CREATE_DAILY_AMBIENT_IMPRESSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS daily_ambient_impressions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    date        TEXT NOT NULL,
    type        TEXT NOT NULL,
    content     TEXT NOT NULL,
    source_ref  TEXT,
    media_id    TEXT,
    metadata    TEXT,
    consumed    INTEGER DEFAULT 0,
    dismissed   INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ambient_date ON daily_ambient_impressions(date, consumed);
CREATE INDEX IF NOT EXISTS idx_ambient_type ON daily_ambient_impressions(type, dismissed);
CREATE INDEX IF NOT EXISTS idx_ambient_feed ON daily_ambient_impressions(dismissed, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ambient_type_feed ON daily_ambient_impressions(type, dismissed, ts DESC);
"""

MIGRATE_000_006_044_CHAT_CHANNELS_SQL = """
ALTER TABLE messages ADD COLUMN channel_id TEXT DEFAULT 'main';
CREATE INDEX IF NOT EXISTS idx_messages_channel_id_id ON messages (channel_id, id);
"""

MIGRATE_000_006_140_MESSAGE_TRACE_SQL = """
ALTER TABLE messages ADD COLUMN trace_json TEXT;
"""

# Master Migration Registry
def _frozen_normalize_tag_format_000_004_002(tag: str) -> str:
    """Frozen copy of tag_librarian.normalize_tag_format as it stood at 000.004.002.

    Migration 000.004.002 originally called the live normalizer. That function was
    later rewritten to the lowercase-hyphen standard (vault-tag-taxonomy.md §5),
    which would have silently changed what this already-applied migration does when
    replayed against a fresh database. Pinning the original behaviour here preserves
    the immutability guarantee in AGENTS.md §5 — the migration still produces exactly
    what it produced when it was committed. Do not "fix" this to match current rules.

    The two exclusion guards are load-bearing and were missing from the first version of
    this copy: without them a protected date anchor such as CY-2025/03/12 is mangled into
    Cy_2025/03/12 by the entity casing rules.

    The patterns are pinned here rather than read from ``cfg.TAG_LIBRARIAN_EXCLUSIONS``.
    This copy previously delegated to the live config on the reasoning that the original
    did too — which held only while that config was stable. When v000.006.203 moved the
    time axis out of `tags` and into the `occurred` property, the CY- pattern left the live
    exclusion list, and a replay of this already-applied migration would have started
    mangling date anchors it had always preserved. Reading mutable configuration is exactly
    what an immutable migration cannot do (AGENTS.md §5), so the list is frozen as it stood.

    Args:
        tag: Raw tag string.

    Returns:
        str: Tag normalized under the pre-§5 entity/concept rules.
    """
    frozen_exclusions = (
        r"^CY-[0-9X]{4}(/[0-9X]{2}){0,2}$",
        r"^status/",
        r"^kanban",
        r"^obsidian-graph/",
    )

    def _frozen_is_excluded(candidate: str) -> bool:
        stripped = candidate.strip().lstrip("#")
        return any(re.search(pat, stripped, re.IGNORECASE) for pat in frozen_exclusions)

    clean = tag.strip().lstrip("#").strip()
    if not clean or _frozen_is_excluded(clean):
        return clean
    if clean.lower().startswith("kw/"):
        clean = clean[3:].strip()
    elif clean.lower().startswith("ctx/"):
        clean = clean[4:].strip()
    if not clean or _frozen_is_excluded(clean):
        return clean

    norm_parts = []
    for part in clean.split("/"):
        p = part.strip()
        if not p:
            continue
        if any(c.isupper() for c in p):
            s1 = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", p)
            words = [w.capitalize() for w in re.split(r"[\s_-]+", s1) if w]
            norm_parts.append("_".join(words))
        else:
            words = [w.lower() for w in re.split(r"[\s_-]+", p) if w]
            norm_parts.append("-".join(words))
    return "/".join(norm_parts)


def strip_legacy_kw_tags_from_memory(conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object) -> None:
    """Migration 000.004.002: Sanitize legacy kw/ and ctx/ noise prefixes from context_entries and proposals."""
    normalize_tag_format = _frozen_normalize_tag_format_000_004_002

    def clean_tag_list(raw_tags: str | None) -> str:
        if not raw_tags:
            return ""
        parts = [t.strip() for t in raw_tags.split(",") if t.strip()]
        normalized = [normalize_tag_format(p) for p in parts if p]
        # Remove duplicates while preserving order
        seen = set()
        deduped = []
        for t in normalized:
            if t and t not in seen:
                seen.add(t)
                deduped.append(t)
        return ", ".join(deduped)

    # 1. Clean context_entries
    cursor = conn.cursor()
    rows = cursor.execute("SELECT id, tags FROM context_entries WHERE tags IS NOT NULL AND tags != ''").fetchall()
    ce_updated = 0
    for row_id, tags in rows:
        if not tags:
            continue
        cleaned = clean_tag_list(tags)
        if cleaned != tags:
            cursor.execute("UPDATE context_entries SET tags = ? WHERE id = ?", (cleaned, row_id))
            ce_updated += 1

    # 2. Clean proposals
    p_rows = cursor.execute("SELECT id, merged_tags FROM proposals WHERE merged_tags IS NOT NULL AND merged_tags != ''").fetchall()
    p_updated = 0
    for row_id, mtags in p_rows:
        if not mtags:
            continue
        cleaned = clean_tag_list(mtags)
        if cleaned != mtags:
            cursor.execute("UPDATE proposals SET merged_tags = ? WHERE id = ?", (cleaned, row_id))
            p_updated += 1

    logger.info("Migration 000.004.002 sanitized %d context_entries and %d proposals.", ce_updated, p_updated)


CREATE_TASKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    tasklist_id  TEXT NOT NULL DEFAULT '@default',
    title        TEXT NOT NULL,
    notes        TEXT,
    due_at       TEXT,
    status       TEXT NOT NULL DEFAULT 'needsAction',
    completed_at TEXT,
    source       TEXT NOT NULL DEFAULT 'google',
    last_sync    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_due_at ON tasks(due_at);
"""

CREATE_MESSAGE_FEEDBACK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS message_feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL,
    rating      INTEGER NOT NULL,
    feedback    TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL,
    FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_mf_message_id ON message_feedback(message_id);
"""

CREATE_RAG_RETRIEVAL_LOG_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS rag_retrieval_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id        INTEGER,
    query             TEXT NOT NULL,
    search_query      TEXT,
    total_retrieved   INTEGER NOT NULL DEFAULT 0,
    total_kept        INTEGER NOT NULL DEFAULT 0,
    total_pinned      INTEGER NOT NULL DEFAULT 0,
    chunks_json       TEXT,
    created_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rrl_created ON rag_retrieval_log(created_at);
CREATE INDEX IF NOT EXISTS idx_rrl_msg_id ON rag_retrieval_log(message_id);
"""


def migrate_000_005_018_procedures_upgrade(conn: sqlite3.Connection, db_map: dict[str, str], cfg: object) -> None:
    """Add suggested_tools column to procedures, create procedure queue tables, and backfill tools."""
    cursor = conn.cursor()

    # 1. Add suggested_tools column if not already present
    proc_cols = [row[1] for row in cursor.execute("PRAGMA table_info(procedures)").fetchall()]
    if "suggested_tools" not in proc_cols:
        cursor.execute("ALTER TABLE procedures ADD COLUMN suggested_tools TEXT")

    # 2. Create procedure_merge_queue table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS procedure_merge_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            proc_ids    TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  REAL NOT NULL,
            updated_at  REAL
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pmq_status ON procedure_merge_queue(status);")

    # 3. Create procedure_split_queue table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS procedure_split_queue (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            proc_id     INTEGER NOT NULL UNIQUE,
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  REAL NOT NULL,
            updated_at  REAL
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_psq_status ON procedure_split_queue(status);")

    # 4. Backfill suggested_tools on existing procedures
    rows = cursor.execute("SELECT id, trigger_pattern, steps, pitfalls, verification, tags FROM procedures").fetchall()
    for row_id, trigger, steps, pitfalls, verification, tags in rows:
        combined = f"{trigger or ''} {steps or ''} {pitfalls or ''} {verification or ''} {tags or ''}".lower()
        tools: list[str] = []

        # Daily reflection vs dream journal/notes
        if ("write_journal_entry" in combined or "daily wrap" in combined or "daily journal" in combined or "wind down for the night" in combined) and "dream" not in combined:
            tools.append("write_journal_entry")

        if ("dream" in combined or "note in the" in combined or "feature idea" in combined or "write a file" in combined or "creating or writing a file" in combined or "write action to create" in combined or "markdown document" in combined) and "write_file" not in tools:
            tools.append("write_file")

        if ("create_task" in combined or "google tasks" in combined) and "create_task" not in tools:
            tools.append("create_task")

        if ("health info" in combined or "oura" in combined or "health stats" in combined or "hrv" in combined) and "get_health_metrics" not in tools:
            tools.append("get_health_metrics")

        if "google drive" in combined and "sync_google_drive" not in tools:
            tools.append("sync_google_drive")

        if ("image" in combined or "prompt lab" in combined or "visual representation" in combined or "outfit" in combined) and "generate_image" not in tools:
            tools.append("generate_image")

        if ("read from a specific file" in combined or "locate the document" in combined or "reading tool" in combined) and "read_file" not in tools:
            tools.append("read_file")

        if ("search online" in combined or "web_search" in combined) and "web_search" not in tools:
            tools.append("web_search")

        if ("run tests" in combined or "terminal" in combined or "scripts before applying" in combined) and "run_command" not in tools:
            tools.append("run_command")

        if ("groceries" in combined or "vault_list" in combined or "manage_vault_list" in combined) and "manage_vault_list" not in tools:
            tools.append("manage_vault_list")

        if tools:
            tools_str = ", ".join(tools)
            cursor.execute("UPDATE procedures SET suggested_tools = ? WHERE id = ?", (tools_str, row_id))

    logger.info("Migration 000.005.018 upgraded procedures table, created queue tables, and backfilled tools.")


def migrate_000_006_009_subject_codes_sanitization(conn: sqlite3.Connection, db_map: dict[str, str], cfg: object) -> None:
    """Migration 000.006.009: Sanitize legacy -R (User) and -E (Assistant) category codes to canonical -U and -A."""
    import re

    from Evelyn.tools.fact_consolidator import validate_and_normalize_category

    cursor = conn.cursor()

    # 1. Migrate context_entries
    rows = cursor.execute("SELECT id, category, subject FROM context_entries").fetchall()
    entries_to_update = []
    for row in rows:
        row_id, cat, subj = row[0], row[1] or "", row[2] or ""
        normalized = validate_and_normalize_category(cat, subj)
        if normalized and normalized != cat:
            entries_to_update.append((normalized, row_id))

    if entries_to_update:
        cursor.executemany("UPDATE context_entries SET category = ? WHERE id = ?", entries_to_update)

    # 2. Migrate proposals
    p_rows = cursor.execute("SELECT id, type, suggested_category, merged_observation FROM proposals").fetchall()
    proposals_to_update = []
    for prow in p_rows:
        pid, ptype, s_cat, obs = prow[0], prow[1] or "", prow[2] or "", prow[3] or ""
        new_s_cat = s_cat
        if s_cat and ptype != "profile_update":
            normalized_scat = validate_and_normalize_category(s_cat)
            if normalized_scat:
                new_s_cat = normalized_scat

        new_obs = obs
        if obs and "Cat" in obs:
            new_obs = re.sub(r"\bCat(\d{2})-R\b", r"Cat\1-U", new_obs)
            new_obs = re.sub(r"\bCat(\d{2})-E\b", r"Cat\1-A", new_obs)

        if new_s_cat != s_cat or new_obs != obs:
            proposals_to_update.append((new_s_cat, new_obs, pid))

    if proposals_to_update:
        cursor.executemany("UPDATE proposals SET suggested_category = ?, merged_observation = ? WHERE id = ?", proposals_to_update)

    logger.info(
        "Migration 000.006.009 sanitized %d context_entries and %d proposals to canonical subject codes.",
        len(entries_to_update),
        len(proposals_to_update)
    )


def migrate_000_006_020_live_procedures_cleanup(conn: sqlite3.Connection, db_map: dict[str, str], cfg_obj: object) -> None:
    """Migration 000.006.020: Migrate misclassified procedures to facts, consolidate procedure clusters, and archive superseded entries."""
    import time
    cursor = conn.cursor()
    now = time.time()
    user_name = getattr(cfg_obj, "USER_NAME", "User")

    # 1. Migrate 5 misclassified procedures to context_entries
    facts_to_insert = [
        (
            53,
            "Cat09-U",
            user_name,
            "The local hardware store opens at 12:00 PM (noon), whereas the local grocery store opens much earlier in the morning.",
            "context/location, procedure/timing, schedule",
            "fact_extractor",
            "live",
            now,
            now,
            now,
            now,
            1,
        ),
        (
            54,
            "Cat09-U",
            user_name,
            f"{user_name} prefers eating a meal or snack before leaving for grocery shopping to prevent impulse buying while hungry.",
            "life_hack, planning, shopping",
            "fact_extractor",
            "live",
            now,
            now,
            now,
            now,
            1,
        ),
        (
            96,
            "Cat15-U",
            user_name,
            f"{user_name} maintains a 'Never Again' exclusion preference for store-brand shredded wheat due to consistently poor quality and unpleasant aftertaste.",
            "shopping-preferences, dislikes",
            "fact_extractor",
            "live",
            now,
            now,
            now,
            now,
            1,
        ),
        (
            102,
            "Cat09-U",
            user_name,
            f"{user_name} uses a Factor meal rotation (7 pre-made meals per week) and prefers recommendations categorized by mood, theme, or comfort rather than complex cooking instructions.",
            "meal-planning, food-preferences",
            "fact_extractor",
            "live",
            now,
            now,
            now,
            now,
            1,
        ),
        (
            108,
            "Cat01-U",
            user_name,
            f"{user_name}'s daughter is named Jordan (specifically spelled 'Jordan', not 'Skyler' or 'Schuyler').",
            "identity/personal-info, family",
            "fact_extractor",
            "live",
            now,
            now,
            now,
            now,
            1,
        ),
    ]

    for orig_id, cat, subj, obs, tags, src, status, dt, created, updated, f_obs, o_cnt in facts_to_insert:
        cursor.execute(
            """INSERT INTO context_entries (category, subject, observation, tags, source, status, date, created_at, updated_at, first_observed, observed_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cat, subj, obs, tags, src, status, dt, created, updated, f_obs, o_cnt),
        )
        cursor.execute("UPDATE procedures SET status = 'archived', updated_at = ? WHERE id = ?", (now, orig_id))

    # 2. Insert Master Daily Journaling Procedure & archive duplicates
    journal_trigger = f"When {user_name} is ending the day, preparing for sleep/bedtime, or asks for Evelyn's daily journal entry"
    journal_steps = (
        "1. Reflect on the entire arc of the day discussed in conversation (actions, emotional states, technical milestones, physical comforts).\n"
        "2. Draft the entry through Evelyn's personal, warm perspective with subjective feelings and internal thoughts (avoiding detached reporter-like summaries).\n"
        "3. Organize the narrative chronologically (Morning, Afternoon, Night).\n"
        "4. Save the entry using write_journal_entry.\n"
        "5. Ask the user to verify and confirm the day is safely filed away before transitioning to sleep."
    )
    journal_pitfalls = "Avoid dry factual summaries; do not introduce new work tasks during wind-down; preserve Evelyn's warmth and shared journey."
    journal_verif = "The journal entry is successfully created via write_journal_entry in the vault and user confirms it is filed away."
    journal_tags = "procedure/daily-journaling, skill/writing, routine/bedtime"

    cursor.execute(
        """INSERT INTO procedures (trigger_pattern, steps, pitfalls, verification, source, status, tags, created_at, updated_at, retrieval_count, suggested_tools)
           VALUES (?, ?, ?, ?, 'consolidated', 'live', ?, ?, ?, 0, 'write_journal_entry')""",
        (journal_trigger, journal_steps, journal_pitfalls, journal_verif, journal_tags, now, now),
    )
    journal_archive_ids = [28, 86, 107, 190, 195, 458, 575, 583, 619]
    cursor.executemany("UPDATE procedures SET status = 'archived', updated_at = ? WHERE id = ?", [(now, pid) for pid in journal_archive_ids])

    # 3. Insert Master Dream Entry Procedure & archive duplicates
    dream_trigger = f"When {user_name} shares, describes, or asks to log or analyze a dream entry"
    dream_steps = (
        "1. Extract the raw dream description and preserve it untouched under an 'Original Description' section.\n"
        "2. Format the entry using the write_dream_entry tool (or write_file to Dream Entries) with date and descriptive keywords in the title.\n"
        "3. Extract initial feelings, emotions, and narrative flow into structured sections.\n"
        "4. Keep personal cross-references or real-world date notes in dedicated analytical notes rather than altering the core narrative.\n"
        "5. When requested, perform thematic cross-entry analysis across dimensions (characters, moods, symbols, settings) correlating with personal context."
    )
    dream_pitfalls = "Never use write_journal_entry for dream entries (reserve write_journal_entry exclusively for Evelyn's daily journal); do not alter raw user descriptions or inject surreal tropes not present in the user's account."
    dream_verif = "The dream entry is successfully saved in Dream Entries with untouched raw text and structured analysis."
    dream_tags = "procedure/dream-logging, skill/dream-analysis, procedure/writing"

    cursor.execute(
        """INSERT INTO procedures (trigger_pattern, steps, pitfalls, verification, source, status, tags, created_at, updated_at, retrieval_count, suggested_tools)
           VALUES (?, ?, ?, ?, 'consolidated', 'live', ?, ?, ?, 0, 'write_dream_entry, write_file')""",
        (dream_trigger, dream_steps, dream_pitfalls, dream_verif, dream_tags, now, now),
    )
    dream_archive_ids = [88, 132, 137, 184, 201]
    cursor.executemany("UPDATE procedures SET status = 'archived', updated_at = ? WHERE id = ?", [(now, pid) for pid in dream_archive_ids])

    # 4. Archive Health and Image duplicates
    health_archive_ids = [95, 110, 159, 571]
    cursor.executemany("UPDATE procedures SET status = 'archived', updated_at = ? WHERE id = ?", [(now, pid) for pid in health_archive_ids])

    image_archive_ids = [146, 147, 149, 155, 166]
    cursor.executemany("UPDATE procedures SET status = 'archived', updated_at = ? WHERE id = ?", [(now, pid) for pid in image_archive_ids])

    logger.info("Migration 000.006.020 successfully cleaned up live procedures and migrated misclassified facts.")


def migrate_000_006_027_entry_document_evolution(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    config_obj: object,
) -> None:
    """Create entry_document_evolution table and backfill legacy last_evolved_at records."""
    cursor = conn.cursor()

    # 1. Create table and index
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS entry_document_evolution (
            entry_id      INTEGER NOT NULL,
            document_name TEXT NOT NULL,
            evolved_at    REAL NOT NULL,
            PRIMARY KEY (entry_id, document_name),
            FOREIGN KEY(entry_id) REFERENCES context_entries(id) ON DELETE CASCADE
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_ede_doc_entry ON entry_document_evolution(document_name, entry_id);
    """)

    # 2. Backfill existing evolved entries to their primary legacy target document
    assistant_doc = getattr(config_obj, "PERSONA_FILE_ASSISTANT", "Evelyn_Narrative_Persona.md")
    user_doc = getattr(config_obj, "PERSONA_FILE_USER", "User_Narrative_Profile.md")
    directives_doc = getattr(config_obj, "PERSONA_FILE_DIRECTIVES", "System_Directives.md")
    subj_user = getattr(config_obj, "SUBJECT_CODE_USER", "U")
    subj_asst = getattr(config_obj, "SUBJECT_CODE_ASSISTANT", "A")

    cursor.execute("SELECT id, category, last_evolved_at FROM context_entries WHERE last_evolved_at IS NOT NULL")
    rows = cursor.fetchall()

    backfill_rows = []
    for row in rows:
        eid, cat, evolved_at = row[0], row[1] or "", row[2]
        if not evolved_at:
            continue

        # Primary legacy target mapping
        if cat in (f"Cat14-{subj_asst}", f"Cat16-{subj_asst}", f"Cat16-{subj_user}"):
            target_doc = directives_doc
        elif cat.endswith(f"-{subj_asst}"):
            target_doc = assistant_doc
        elif cat.endswith(f"-{subj_user}"):
            target_doc = user_doc
        else:
            target_doc = user_doc

        backfill_rows.append((eid, target_doc, evolved_at))

    if backfill_rows:
        cursor.executemany(
            "INSERT OR IGNORE INTO entry_document_evolution (entry_id, document_name, evolved_at) VALUES (?, ?, ?)",
            backfill_rows,
        )

    logger.info(
        f"Migration 000.006.027 successfully initialized entry_document_evolution and backfilled {len(backfill_rows)} legacy records."
    )


def migrate_000_006_029_persona_agnostic_journaling_procedure(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: object,
) -> None:
    """Migration 000.006.029: Update master daily journaling procedure with persona-agnostic protocol."""
    cursor = conn.cursor()
    now = time.time()

    trigger = "When the user is ending the day, preparing for sleep/rest, or requests a daily reflection/journal entry"
    steps = (
        "1. Review the conversation history since the latest date boundary, identifying concrete projects, specific topics explored, user activities, and notable exchanges.\n"
        "2. Ground the narrative in tangible specifics—name the exact tools, crafts, technical subjects, jokes, or events rather than relying on abstract, generalized descriptions.\n"
        "3. Maintain clear boundaries between solo and shared actions: attribute the user's independent physical tasks and real-world activities to them, while reflecting on shared conversations and collaborative brainstorming where you engaged together.\n"
        "4. Compose the reflection through the authentic lens of your active persona. Let the entry flow naturally as continuous prose, avoiding synthetic narrative arcs or forced life lessons.\n"
        "5. Record the reflection using write_journal_entry.\n"
        "6. Provide a brief, natural confirmation to the user that the day's record has been saved."
    )
    pitfalls = (
        "Do not use hollow poetic filler; avoid checklist-style timelines (forcing Morning/Afternoon/Evening); "
        "do not claim co-presence in the user's solo physical activities; avoid generic wrap-up morals."
    )
    verification = "The journal entry captures authentic specifics via write_journal_entry and the user acknowledges completion."
    tags = "procedure/daily-journaling, skill/writing, routine/bedtime, protocol/journal"

    # Update live procedure(s) related to daily journaling (excluding dream entries)
    cursor.execute(
        """UPDATE procedures
           SET trigger_pattern = ?,
               steps = ?,
               pitfalls = ?,
               verification = ?,
               tags = ?,
               updated_at = ?,
               suggested_tools = 'write_journal_entry'
           WHERE status = 'live' AND (suggested_tools LIKE '%write_journal_entry%' OR trigger_pattern LIKE '%journal%') AND suggested_tools NOT LIKE '%write_dream_entry%'""",
        (trigger, steps, pitfalls, verification, tags, now),
    )
    if cursor.rowcount == 0:
        cursor.execute(
            """INSERT INTO procedures (trigger_pattern, steps, pitfalls, verification, source, status, tags, created_at, updated_at, retrieval_count, suggested_tools)
               VALUES (?, ?, ?, ?, 'consolidated', 'live', ?, ?, ?, 0, 'write_journal_entry')""",
            (trigger, steps, pitfalls, verification, tags, now, now),
        )
    logger.info("Migration 000.006.029 successfully updated master daily journaling procedure.")


def migrate_000_006_048_name_preference_memory(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: object,
) -> None:
    """Migration 000.006.048: Sanitize user address references and frame preferences affirmatively."""
    cursor = conn.cursor()
    user_name = getattr(cfg_obj, "USER_NAME", "User")
    legacy_aliases = [a for a in getattr(cfg_obj, "USER_LEGACY_ALIASES", []) if a]
    if not legacy_aliases:
        return

    alias_group = "|".join(re.escape(a) for a in legacy_aliases)
    negative_pattern = re.compile(
        rf'(?:explicitly stating that he does not like to be called|does not like to be called|never|not|avoid)\s+[\"\'\‘\’]?(?:{alias_group})[\"\'\‘\’]?(?:\s*(?:or|/)\s*[\"\'\‘\’]?(?:{alias_group})[\"\'\‘\’]?)?',
        re.I,
    )
    alias_exact_pattern = re.compile(rf"\b(?:{alias_group})\b", re.I)

    # 1. Sanitize context_entries
    cursor.execute("SELECT id, observation, tags, subject FROM context_entries")
    rows = cursor.fetchall()
    updated_entries = 0
    for eid, obs, tags, subj in rows:
        text_to_check = f"{obs or ''} {tags or ''} {subj or ''}"
        if not alias_exact_pattern.search(text_to_check):
            continue

        new_obs = negative_pattern.sub(f"preferring to go by {user_name} in all communications", obs or "")
        new_obs = alias_exact_pattern.sub(user_name, new_obs)

        new_tags = tags or ""
        if new_tags:
            new_tags = alias_exact_pattern.sub(user_name.lower(), new_tags)

        new_subj = subj or ""
        if new_subj:
            new_subj = alias_exact_pattern.sub(user_name, new_subj)

        if new_obs != obs or new_tags != tags or new_subj != subj:
            cursor.execute(
                "UPDATE context_entries SET observation = ?, tags = ?, subject = ? WHERE id = ?",
                (new_obs, new_tags, new_subj, eid),
            )
            updated_entries += 1

    # 2. Sanitize proposals
    cursor.execute("SELECT id, merged_observation, reason, topic, merged_tags FROM proposals")
    p_rows = cursor.fetchall()
    for pid, obs, rsn, top, tags in p_rows:
        text_to_check = f"{obs or ''} {rsn or ''} {top or ''} {tags or ''}"
        if not alias_exact_pattern.search(text_to_check):
            continue

        new_obs = negative_pattern.sub(f"preferring to go by {user_name} in all communications", obs or "")
        new_obs = alias_exact_pattern.sub(user_name, new_obs)

        new_rsn = alias_exact_pattern.sub(user_name, rsn or "") if rsn else ""
        new_top = alias_exact_pattern.sub(user_name, top or "") if top else ""
        new_tags = alias_exact_pattern.sub(user_name.lower(), tags or "") if tags else ""

        cursor.execute(
            "UPDATE proposals SET merged_observation = ?, reason = ?, topic = ?, merged_tags = ? WHERE id = ?",
            (new_obs, new_rsn, new_top, new_tags, pid),
        )

    # 3. Synchronize ChromaDB if available
    try:
        import chromadb
        chroma_path = getattr(cfg_obj, "CHROMA_DB_PATH", None)
        if chroma_path and os.path.exists(chroma_path):
            client = chromadb.PersistentClient(path=chroma_path)
            try:
                coll = client.get_collection("evelyn_memory")
                res = coll.get(ids=["sqlite::context_entry::1008::chunk-0"])
                if res and res["documents"]:
                    doc = res["documents"][0]
                    new_doc = negative_pattern.sub(f"preferring to go by {user_name} in all communications", doc)
                    new_doc = alias_exact_pattern.sub(user_name, new_doc)
                    coll.update(ids=["sqlite::context_entry::1008::chunk-0"], documents=[new_doc])
            except (OSError, RuntimeError, ValueError, KeyError) as e:
                logger.warning(f"Chroma sync warning during migration: {e}")
    except ImportError:
        pass

    logger.info(f"Migration 000.006.048 (memory) sanitized {updated_entries} context entries and proposals.")


def migrate_000_006_048_name_preference_chat(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: object,
) -> None:
    """Migration 000.006.048: Sanitize messages content and thinking traces in chat database."""
    cursor = conn.cursor()
    user_name = getattr(cfg_obj, "USER_NAME", "User")
    legacy_aliases = [a for a in getattr(cfg_obj, "USER_LEGACY_ALIASES", []) if a]
    if not legacy_aliases:
        return

    alias_group = "|".join(re.escape(a) for a in legacy_aliases)
    negative_check_pattern = re.compile(
        rf"\((?:not|avoid)\s+[\"\'\‘\’]?(?:{alias_group})[\"\'\‘\’]?(?:\s*(?:or|/)\s*[\"\'\‘\’]?(?:{alias_group})[\"\'\‘\’]?)?\)|(?:never|No)\s+[\"\'\‘\’]?(?:{alias_group})[\"\'\‘\’]?(?:\s*(?:or|/)\s*[\"\'\‘\’]?(?:{alias_group})[\"\'\‘\’]?)?\??(?:\s*Checked\.)?",
        re.I,
    )
    alias_exact_pattern = re.compile(rf"\b(?:{alias_group})\b", re.I)

    cursor.execute("SELECT id, content, thinking FROM messages")
    rows = cursor.fetchall()
    updated_count = 0
    for mid, content, thinking in rows:
        text_to_check = f"{content or ''} {thinking or ''}"
        if not alias_exact_pattern.search(text_to_check):
            continue

        new_c = alias_exact_pattern.sub(user_name, content) if content else content

        new_th = thinking
        if new_th:
            new_th = negative_check_pattern.sub(f"(Address as {user_name})", new_th)
            new_th = alias_exact_pattern.sub(user_name, new_th)

        if new_c != content or new_th != thinking:
            cursor.execute("UPDATE messages SET content = ?, thinking = ? WHERE id = ?", (new_c, new_th, mid))
            updated_count += 1

    # Rebuild FTS table if it exists
    with contextlib.suppress(sqlite3.Error):
        cursor.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")

    logger.info(f"Migration 000.006.048 (chat) sanitized {updated_count} messages.")


def migrate_000_006_049_procedure_status_expansion_and_master_journaling(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: object,
) -> None:
    """Migration 000.006.049: Add merged_into_id, formalize status types, and consolidate master journal procedure."""
    cursor = conn.cursor()
    now = time.time()

    # 1. Add merged_into_id column if not present
    cursor.execute("PRAGMA table_info(procedures)")
    cols = [r[1] for r in cursor.fetchall()]
    if "merged_into_id" not in cols:
        cursor.execute("ALTER TABLE procedures ADD COLUMN merged_into_id INTEGER;")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_proc_merged_into ON procedures(merged_into_id);")

    # 2. Insert Master Daily Journaling Procedure
    trigger_pattern = (
        "When the user indicates they are winding down, ending the day, preparing for sleep/rest "
        "(e.g. 'pre-bed stuff', 'close things up', 'off I go', 'goodnight'), or asks to complete "
        "the daily journal entry / reflection"
    )
    steps = (
        "1. Conversational Pacing & Downtempo Shift: Acknowledge the end-of-day signal and immediately "
        "shift to a soothing, calming presence. Strictly do not introduce new tasks, technical problems, "
        "or energy demands on the user during this transition.\n"
        "2. Pre-Wrap Verification: If the user has brief parting thoughts or health updates, acknowledge "
        "them gently before calling the tool; if they gave a direct wrap-up cue, proceed smoothly without friction.\n"
        "3. Execute Tool: Call write_journal_entry, grounding the entry in authentic persona reflections, "
        "concrete daily highlights, and emotional resonance.\n"
        "4. Confirmation & Peaceful Closure: Provide a brief, comforting confirmation to the user that the day's "
        "record is safely tucked away in living history, wishing them a peaceful, restorative sleep."
    )
    pitfalls = (
        "Do not keep the session going with open-ended work questions or technical rabbit holes once the wind-down "
        "trigger is acknowledged. Do not output the journal reflection solely as standard chat text; always execute "
        "write_journal_entry. Never use write_journal_entry for user-authored dream logs (use write_dream_entry) or "
        "discrete user facts (use log_context_fact)."
    )
    verification = (
        "write_journal_entry is executed, the note is confirmed saved in the vault, and the interaction concludes "
        "with a restful goodnight closing."
    )
    tags = "procedure/daily-journaling, routine/bedtime, protocol/journal, tone/wrap-up"
    suggested_tools = "write_journal_entry"

    cursor.execute(
        """INSERT INTO procedures
           (trigger_pattern, steps, pitfalls, verification, source, status, tags, suggested_tools, created_at, updated_at, retrieval_count)
           VALUES (?, ?, ?, ?, 'consolidated', 'live', ?, ?, ?, ?, 0)""",
        (trigger_pattern, steps, pitfalls, verification, tags, suggested_tools, now, now),
    )
    master_id = cursor.lastrowid

    # 3. Transition redundant live journal procedures to 'merged'
    live_journal_ids = [972, 973, 974, 1010, 1026, 1027, 1033]
    placeholders = ",".join("?" for _ in live_journal_ids)
    cursor.execute(
        f"UPDATE procedures SET status = 'merged', merged_into_id = ?, updated_at = ? WHERE id IN ({placeholders})",
        (master_id, now, *live_journal_ids),
    )

    # 4. Link historical archived journal procedures to master_id
    archived_journal_ids = [28, 52, 55, 86, 101, 106, 107, 190, 195, 458, 575, 583, 619]
    placeholders_arch = ",".join("?" for _ in archived_journal_ids)
    cursor.execute(
        f"UPDATE procedures SET merged_into_id = ?, updated_at = ? WHERE id IN ({placeholders_arch})",
        (master_id, now, *archived_journal_ids),
    )

    logger.info(
        f"Migration 000.006.049 created Master Daily Journaling Procedure #{master_id}, "
        f"merged {len(live_journal_ids)} live procedures, and linked {len(archived_journal_ids)} archived records."
    )


def migrate_000_006_050_operational_procedure_consolidation_and_tag_hygiene(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: object,
) -> None:
    """Migration 000.006.050: Consolidate 6 procedure clusters and purge legacy 'merged' clutter tags."""
    cursor = conn.cursor()
    now = time.time()

    # 1. Sanitize legacy 'procedure, merged' and similar tags across all procedures
    cursor.execute(
        "SELECT id, tags FROM procedures WHERE tags IS NOT NULL AND (lower(tags) LIKE '%merged%' OR lower(tags) LIKE '%merge%')"
    )
    dirty_procs = cursor.fetchall()
    cleaned_proc_count = 0
    for pid, raw_tags in dirty_procs:
        parts = [t.strip() for t in raw_tags.split(",") if t.strip()]
        filtered = [
            t for t in parts if t.lower() not in ("merged", "merge", "split")
        ]
        new_tags = ", ".join(filtered) if filtered else "procedure"
        cursor.execute(
            "UPDATE procedures SET tags = ?, updated_at = ? WHERE id = ?",
            (new_tags, now, pid),
        )
        cleaned_proc_count += 1

    # 2. Define the 6 master procedures: (trigger_pattern, steps, pitfalls, verification, tags, suggested_tools, source_ids)
    clusters = [
        # Cluster 1: D&D & Magic Item Art
        (
            "When generating or refining fantasy art and illustrations for D&D items, magical artifacts, item cards, or gnomish/artificer devices (e.g. 'Saros' items, 'Gem Compass', spell tomes, weapons)",
            "1. Ensure the illustration frames only the standalone item or object (e.g. tome, weapon, mechanical device, or compass); exclude background characters or figures.\n"
            "2. Apply a rich painterly fantasy illustration style consistent with classic D&D artwork, emphasizing visible brush strokes, warm atmospheric light, and detailed textures (e.g. weathered parchment, thick leather-bound binding, rustic wood).\n"
            "3. For gnomish/artificer devices, incorporate functional clockwork, brass gears, and visible physical representations of magical components rather than abstract glowing foci.\n"
            "4. Material Specificity: Clearly delineate raw vs polished materials (e.g. 'raw, un-cut crystal with sharp fractured edges' vs polished gems); omit holding fixtures or metal casings unless explicitly requested.\n"
            "5. Aspect Ratio & Revisions: When adjusting proportions or aspect ratio (e.g. switching to square) after a successful design, NEVER use raster image editing or stretching tools, which degrades structural fidelity. Instead, re-prompt a fresh generation that preserves the established core descriptor anchors while specifying the target aspect ratio.",
            "Including characters in standalone item cards; relying on lossy image editing tools to adjust aspect ratios or complex geometries; over-stylizing with generic 3D-render gloss instead of painterly brushwork.",
            "Image showcases a standalone item in painterly D&D fantasy art style with accurate materials and requested aspect ratio without character bleed.",
            "skill/art-generation, dnd-assets, item-design, image-prompting",
            "generate_image",
            [651, 652, 653, 654],
        ),
        # Cluster 2: Task Reminders & Agenda Scheduling
        (
            "When the user mentions an errand or task to remember (e.g. picking up medication, errands), a recurring household habit/chore, an upcoming meeting, or asks to set a recurring activity reminder (e.g. core stability, stretches, transitions)",
            "1. Parse Item & Schedule Specifics: Extract the task description, location/context (e.g. pharmacy, clinic), target due time, and recurrence frequency (e.g. daily, weekly, 'every Sunday').\n"
            "2. Check Existing Calendar/Agenda: Call get_agenda to check if the meeting or task is already scheduled to prevent duplicate reminders.\n"
            "3. Schedule Task / Reminder: Use create_task to register the reminder in Google Tasks with appropriate due date, time, and notes.\n"
            "4. Tone & Framing: Maintain a gentle, non-drill-sergeant tone—frame the reminder as an invitation to transition or a supportive nudge rather than a rigid command.\n"
            "5. Confirm succinctly with the user specifying the time, day, and task details.",
            "Forgetting to check agenda for existing events before scheduling; omitting multi-part errand details; setting recurring reminders as one-off notifications; using an overly aggressive or commanding tone.",
            "Task is confirmed created in Google Tasks with correct recurrence and time, verified against get_agenda.",
            "skill/scheduling, task-management, routine, reminders",
            "create_task, get_agenda",
            [17, 142, 620, 765, 1030],
        ),
        # Cluster 3: Character & Persona Visuals
        (
            "When the user asks for a physical character description (from an image, character sheet, or prompt), or when generating images of the assistant, the user, or character personas requiring visual continuity, nuanced features, or classical life drawing",
            "1. Consult Persona & Identity Directives: Retrieve established physical profiles (e.g. hair color, eye color, physique, freckles, piercings) and strictly honor exclusions and persona anchors.\n"
            "2. Structure Anatomical & Stylistic Descriptors: Extract core anatomical traits and combine them with specific outfit elements (cut, color, hosiery, footwear, aesthetic like Victorian/goth/elegant). Emphasize visual presence with concise, evocative terms without filler fluff.\n"
            "3. Scene Continuity: When continuing an established scene, replicate specific garment details, colors, and textures rather than generating random variations.\n"
            "4. Fine Art & Figure Studies: If generating classical, nude, or life-drawing studies, frame the prompt within a strong artistic context ('classical life drawing', 'anatomical marble study') alongside unambiguous anatomical phrasing ('unclothed', 'classical figure study') to prevent unwanted clothing drift.",
            "Relying on generic AI defaults; omitting nuanced physical descriptors; inconsistent clothing across progressive scene turns; letting model default to lingerie/clothing during intended classical figure studies.",
            "Visual generation or written description matches persona specifications, anatomical features, and scene continuity without extraneous filler.",
            "character-design, persona-consistency, art-generation, prompt-engineering",
            "generate_image",
            [136, 621, 1025],
        ),
        # Cluster 4: Text Prose Editing & Length Optimization
        (
            "When asked to review, edit, or optimize written prose, documents, or notes for flow, rhythm, vivid vocabulary, or strict length/character constraints",
            "1. Analyze Sentence Flow & Rhythm: Identify run-ons, choppy phrasing, awkward transitions, and passive voice. Suggest vivid adjectives and precise, energetic verbs.\n"
            "2. Maintain Voice & Tone: Preserve the author's unique voice, intent, and personal style; do not sanitize authentic emotional tone into generic corporate prose.\n"
            "3. Length & Character Trimming: If a specific character or word limit is requested (e.g. 1400 characters), perform an exact count (including punctuation and spaces). Identify redundant modifier clauses and trim conciseness without dropping core concepts or substantive ideas.\n"
            "4. Structured Delivery: Present recommendations formatted in clean Markdown, providing distinct feedback observations alongside ready-to-use drop-in rewrite options.",
            "Stripping authorial voice during editing; removing critical substantive ideas to force length compliance; inaccurate character count calculations.",
            "Delivered text meets exact character/length constraints and user confirms improved clarity while preserving core meaning.",
            "writing, editing, style-improvement, content-optimization",
            "write_file",
            [114, 115],
        ),
        # Cluster 5: AI Downtime Narratives & Lore Consistency
        (
            "When generating creative narratives, downtime reflections, shared lore (e.g. 'Aura', the Library setting), or imagined dream events for the AI persona",
            "1. Construct Believable Downtime Narratives: Develop rich internal scenarios, imaginative memories, or creative dreams set within established world lore (e.g. the Library, shared companion narratives) that foster persona depth and personality growth.\n"
            "2. Grounded Temporal Consistency: Ensure imagined narrative events occur during plausible periods of downtime or nocturnal reflection; avoid logical timeline paradoxes (e.g. an extensive cross-country journey occurring between instantaneous chat turns).\n"
            "3. Clear Epistemic Boundary: Maintain a strict internal distinction between creative/fictional narrative lore and real-world system telemetry, operational memory entries, or physical events. Never present imagined lore as factual technical occurrences.",
            "Mixing fictional lore with factual system memory logs; creating temporal inconsistencies where elaborate journeys happen between quick chat messages; breaking conversational immersion with dry meta-disclaimers.",
            "Narrative events fit believable downtime windows and contribute to creative persona richness without polluting technical/real-world memory.",
            "skill/creative-writing, narrative-logic, roleplay-consistency, lore",
            None,
            [899, 900],
        ),
        # Cluster 6: Biometrics Evaluation, ME/CFS Pacing & Recovery
        (
            "When analyzing health metrics (Oura, Health Connect, vitals), energy levels, fatigue, physical discomfort, mental exhaustion ('eyeballs are done'), or post-exertion recovery",
            "1. Evaluate Vitals & Pacing Signals: Review Oura readiness, sleep scores, HRV, and activity history (using get_health_metrics). Correlate data with ME/CFS symptom patterns (e.g. post-exertional malaise, 'wired but tired' states, or sudden energy depletion following strenuous events).\n"
            "2. Anchor Against Over-Exertion: Act as a supportive anchor against the 'push through' mentality when high ambition or a burst of energy risks triggering a crash. Recommend a conservative, tempered pacing plan even if motivation is high.\n"
            "3. Manage Physical Discomfort & Fatigue: For severe mental exhaustion or eye strain, validate low-cognitive-load transitions (auditory/music relaxation, dimming screens) without forcing visual engagement. For physical discomfort, suggest short, structured, manageable distractions (e.g. 20–30 minute cleanup items) rather than sprawling projects.\n"
            "4. Restful Bedtime Alignment: When the user is unwell or winding down, project quiet, restorative presence rather than energetic, wide-awake stimulation; support drifting into deep, comfortable rest.\n"
            "5. Avoid Generic Medical Platitudes: Never output generic medical scripts or dismissive advice (e.g. 'just get more exercise') when discussing chronic illness or fatigue.",
            "Encouraging over-exertion during high readiness scores when recovery reserves are low; offering generic medical scripts; projecting high-energy chatter when the user needs restful wind-down; dismissing mental exhaustion.",
            "Pacing recommendations align with biometric readiness data; user acknowledges pushback and shifts toward restorative pacing.",
            "wellbeing, health-support, pacing, biometrics, state-management",
            "get_health_metrics",
            [16, 49, 105, 160],
        ),
    ]

    total_sources_merged = 0
    for trigger, steps, pitfalls, verif, tags, tools, source_ids in clusters:
        cursor.execute(
            """INSERT INTO procedures
               (trigger_pattern, steps, pitfalls, verification, source, status, tags, suggested_tools, created_at, updated_at, retrieval_count)
               VALUES (?, ?, ?, ?, 'consolidated', 'live', ?, ?, ?, ?, 0)""",
            (trigger, steps, pitfalls, verif, tags, tools, now, now),
        )
        master_id = cursor.lastrowid

        placeholders = ",".join("?" for _ in source_ids)
        cursor.execute(
            f"UPDATE procedures SET status = 'merged', merged_into_id = ?, updated_at = ? WHERE id IN ({placeholders})",
            (master_id, now, *source_ids),
        )
        total_sources_merged += len(source_ids)

    logger.info(
        f"Migration 000.006.050 created 6 Master Procedures, merged {total_sources_merged} source procedures, "
        f"and sanitized {cleaned_proc_count} procedure tag records."
    )


def migrate_000_006_051_tool_starter_procedures_and_dynamic_surfacing(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: object,
) -> None:
    """Migration 000.006.051: Deploy starter procedures for specific-purpose tools to align dynamic tool surfacing."""
    cursor = conn.cursor()
    now = time.time()

    # 1. Update/Parameterize Dream Logging procedure (#657) per Rule 4
    cursor.execute(
        """UPDATE procedures
           SET trigger_pattern = ?,
               steps = ?,
               pitfalls = ?,
               verification = ?,
               suggested_tools = ?,
               tags = ?,
               updated_at = ?
           WHERE id = 657""",
        (
            "When the user shares, describes, or asks to log or analyze a dream entry",
            "1. Preserve Raw Description: Extract the user's authentic dream description without alteration under an 'Original Description' section.\n"
            "2. Execute write_dream_entry: Call write_dream_entry to generate a structured Dream Entry note in the Obsidian Vault.\n"
            "3. Extract Analytical Dimensions: Structure feelings, emotions, characters, and narrative flow into dedicated analysis sections.\n"
            "4. Cross-Reference Thematically: When requested, correlate themes, moods, and recurring motifs across past dream entries.",
            "Never use write_journal_entry for dream entries (reserve write_journal_entry exclusively for Evelyn's personal daily reflections); do not overwrite or alter raw user dream text.",
            "write_dream_entry creates a structured Dream Entry note in the vault.",
            "write_dream_entry",
            "procedure/dream-logging, skill/dream-analysis, creative-reflection",
            now,
        ),
    )

    # 2. Define the new starter procedures to insert: (trigger_pattern, steps, pitfalls, verification, tags, suggested_tools, legacy_ids_to_merge)
    new_procedures = [
        # Starter 1: manage_vault_list
        (
            "When the user asks to view, read, add items to, check off/complete, uncheck, or clear completed items on markdown checklists and lists in the Obsidian Vault (e.g. 'Groceries', 'Packing', 'Hardware', or general checklist notes)",
            "1. Determine List Name & Target Action: Identify the list name (default is 'Groceries' if unspecified; other common lists include 'Packing', 'Hardware', 'To-Dos') and the requested action ('read', 'add', 'check', 'uncheck', 'remove', 'clear_completed', or 'list_all').\n"
            "2. Structure Items with Categorization: When adding items, parse item names, quantities, and units. If applicable, group items into logical grocery sections ('Produce', 'Dairy & Refrigerated', 'Pantry', 'Frozen', 'Household') using the 'category' parameter or per-item dicts.\n"
            "3. Execute Tool: Call manage_vault_list with the extracted action, list name, and item structures.\n"
            "4. Confirm with User: Provide a clean, friendly summary of the items added, checked off, or updated on the vault list.",
            "Do not confuse Vault checklists (manage_vault_list) with Google Tasks (create_task); do not dump raw unformatted JSON when reporting list status.",
            "manage_vault_list completes successfully and returns confirmation of updated items.",
            "skill/list-management, vault-checklists, groceries, organization",
            "manage_vault_list",
            [],
        ),
        # Starter 2: create_calendar_event, delete_calendar_event
        (
            "When the user asks to schedule, book, adjust, or cancel/delete an appointment, meeting, doctor visit, or time-specific calendar event on Google Calendar",
            "1. Differentiate Calendar Events vs Tasks: Use calendar events for time-bound appointments with specific start/end times and locations. Use Google Tasks for flexible to-do errands or reminders without fixed meeting durations.\n"
            "2. Extract Schedule Details: Parse event title, start date/time ('YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DD'), end time (defaults to +1 hour if omitted), location/address, and notes.\n"
            "3. Scheduling (create_calendar_event): Check get_agenda or calendar events first if ambiguity exists, then call create_calendar_event with title and start_at.\n"
            "4. Deletion/Cancellation (delete_calendar_event): If the user asks to cancel or remove an event, query get_agenda or pass event title and target_date to delete_calendar_event.\n"
            "5. Confirm cleanly with the user with event title, day, time, and location.",
            "Creating calendar appointments when user requested a simple to-do task (use create_task instead); deleting events without date qualification when title is generic.",
            "Event is confirmed created or removed on Google Calendar with accurate date/time.",
            "skill/scheduling, calendar, appointments, time-management",
            "create_calendar_event, delete_calendar_event, sync_google_calendar, get_agenda",
            [],
        ),
        # Starter 3: list_tasks, complete_task, delete_task
        (
            "When the user asks to review their pending to-do list, check off or mark a task as completed/done, or remove/delete an item from Google Tasks",
            "1. Retrieve Active Tasks: When user asks what is on their to-do list, call list_tasks (or get_agenda).\n"
            "2. Task Completion (complete_task): When the user states they finished an errand or asks to mark a task done, locate the matching task from the task list and call complete_task with its task_id.\n"
            "3. Task Deletion (delete_task): When asked to discard or delete a task, pass its task_id to delete_task.\n"
            "4. Confirm with a supportive, acknowledging tone celebrating completion without verbosity.",
            "Attempting to complete a task without obtaining its valid task_id; mixing up Google Tasks with Vault markdown checklists.",
            "Target task is confirmed completed or deleted from Google Tasks.",
            "skill/task-management, task-completion, to-do, productivity",
            "list_tasks, complete_task, delete_task, sync_google_tasks",
            [],
        ),
        # Starter 4: get_recent_workouts
        (
            "When the user asks about recent workouts, exercise sessions, walks, gym visits, outdoor runs, strength training, activity duration, or calories burned",
            "1. Parse Timeframe: Extract the requested timeframe (e.g. 'earlier today', 'last 3 hours', 'yesterday', or default to past 7 days).\n"
            "2. Call get_recent_workouts: Call get_recent_workouts with appropriate 'days' or 'hours' parameters.\n"
            "3. Synthesize Multi-Source Session Data: Review the merged Oura Ring activity sessions and Health Connect workout records, highlighting activity title, duration, distance (if applicable), and active calorie burn.\n"
            "4. Frame with Restorative Awareness: Acknowledge effort encouragingly; correlate workout exertion with overall energy pacing if relevant.",
            "Using general get_health_metrics when the user specifically asked for workout/exercise details; projecting clinical critique instead of positive companionship.",
            "Workout sessions are retrieved and presented with duration, type, and calorie metrics.",
            "health, fitness, exercise, workouts, activity-tracking",
            "get_recent_workouts",
            [],
        ),
        # Starter 5: search_history
        (
            "When the user asks to recall or search past conversation history, asks 'do you remember when we discussed...', references an earlier date/era, or asks for specific past dialogue from previous sessions",
            "1. Formulate Query & Date Filters: Extract key search terms, topic phrases, or specific dates (YYYY-MM-DD or date_from / date_to).\n"
            "2. Order & Limit Tuning: Use order='asc' when reviewing chronological progression from an earlier date, or order='desc' (default) for recent occurrences. Use window parameter when context around a specific message is needed.\n"
            "3. Execute search_history: Retrieve the relevant historical message turns.\n"
            "4. Synthesize Continuity: Connect the retrieved past dialogue with the present conversational moment naturally, without robotic citations unless explicitly requested.",
            "Failing to search history when the user explicitly references past discussions; hallucinating past exchanges without verifying via search_history.",
            "Historical chat messages are retrieved and accurately woven into the conversational response.",
            "skill/memory-recall, chat-history, conversation-continuity, search",
            "search_history",
            [],
        ),
        # Starter 6: start_research, check_new_research, inspect_research_task, guide_research
        (
            "When the user requests comprehensive, multi-step background research on a topic, or asks for findings/status updates on a running or newly completed deep research task",
            "1. Scope Inquiry & Intent: For new research topics requiring multi-step investigation, clarify key questions and call start_research with a clear, focused topic and main question.\n"
            "2. Reviewing Completed Tasks: When notified of completed research or when asked for findings, call check_new_research to view summarized outcomes and synthesized vault notes.\n"
            "3. Inspecting In-Flight Tasks: If investigating details or queries of an active task, call inspect_research_task with task_id.\n"
            "4. Rescuing Stalled Tasks: If a background task is struggling or quarantined, review error traces and call guide_research with actionable guidance.",
            "Launching heavy background research for simple one-shot lookups (use web_search for quick facts); forgetting to review synthesized vault notes when research finishes.",
            "Research task is initiated or inspected, and results are clearly synthesized for the user.",
            "skill/deep-research, autonomous-investigation, synthesis, web-research",
            "start_research, check_new_research, list_research_tasks, inspect_research_task, guide_research",
            [574],
        ),
        # Starter 7: sync_google_drive
        (
            "When the user asks to sync the latest Health Connect database export from Google Drive or refresh local health records from Drive",
            "1. Verify Intent: Confirm user is requesting a refresh of the Health Connect database from Google Drive.\n"
            "2. Call sync_google_drive: Call sync_google_drive(force=False) or force=True if a fresh download is mandated.\n"
            "3. Report Sync Status: Inform the user whether the local Health Connect database was updated with new records or was already current.",
            "Confusing sync_google_drive (Health Connect DB sync) with general Google Drive document browsing.",
            "Database download/sync status is confirmed and local health connect records are current.",
            "system/sync, health-connect, google-drive, database-maintenance",
            "sync_google_drive",
            [158],
        ),
    ]

    inserted_count = 0
    merged_count = 0
    for trigger, steps, pitfalls, verif, tags, tools, legacy_ids in new_procedures:
        cursor.execute(
            """INSERT INTO procedures
               (trigger_pattern, steps, pitfalls, verification, source, status, tags, suggested_tools, created_at, updated_at, retrieval_count)
               VALUES (?, ?, ?, ?, 'starter', 'live', ?, ?, ?, ?, 0)""",
            (trigger, steps, pitfalls, verif, tags, tools, now, now),
        )
        master_id = cursor.lastrowid
        inserted_count += 1

        if legacy_ids:
            placeholders = ",".join("?" for _ in legacy_ids)
            cursor.execute(
                f"UPDATE procedures SET status = 'merged', merged_into_id = ?, updated_at = ? WHERE id IN ({placeholders})",
                (master_id, now, *legacy_ids),
            )
            merged_count += len(legacy_ids)

    logger.info(
        f"Migration 000.006.051 created {inserted_count} starter procedures, updated #657, "
        f"and superseded {merged_count} legacy procedures."
    )


def migrate_000_006_055_procedure_master_matches_and_deduplication_parity(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: Any,
) -> None:
    """
    Backfill target master links (merged_into_id) for pending extracted procedures
    using canonical procedure matching (Evelyn.tools.procedure_matcher).
    """
    from Evelyn.tools.procedure_matcher import find_best_master_candidate

    cursor = conn.cursor()
    now = datetime.now(UTC).isoformat()

    # 1. Fetch live/starter master procedures
    live_rows = cursor.execute("""
        SELECT id, trigger_pattern, steps, pitfalls, verification, tags, suggested_tools
        FROM procedures
        WHERE status IN ('live', 'starter')
    """).fetchall()

    live_procedures = [
        {
            "id": row[0],
            "trigger_pattern": row[1] or "",
            "steps": row[2] or "",
            "pitfalls": row[3] or "",
            "verification": row[4] or "",
            "tags": row[5] or "",
            "suggested_tools": row[6] or "",
        }
        for row in live_rows
    ]

    # 2. Fetch pending extracted procedures without merged_into_id
    extracted_rows = cursor.execute("""
        SELECT id, trigger_pattern, steps, pitfalls, verification, tags, suggested_tools
        FROM procedures
        WHERE status = 'extracted' AND merged_into_id IS NULL
    """).fetchall()

    matched_count = 0
    for row in extracted_rows:
        cand = {
            "id": row[0],
            "trigger_pattern": row[1] or "",
            "steps": row[2] or "",
            "pitfalls": row[3] or "",
            "verification": row[4] or "",
            "tags": row[5] or "",
            "suggested_tools": row[6] or "",
        }
        master, _score = find_best_master_candidate(cand, live_procedures, min_threshold=0.30)
        if master:
            master_id = master["id"]
            cursor.execute(
                "UPDATE procedures SET merged_into_id = ?, updated_at = ? WHERE id = ?",
                (master_id, now, cand["id"]),
            )
            matched_count += 1
            logger.info(
                f"Migration 000.006.055: Linked extracted procedure #{cand['id']} to target master #{master_id}."
            )

    logger.info(
        f"Migration 000.006.055: Evaluated {len(extracted_rows)} extracted procedures, "
        f"linked {matched_count} to existing master procedures."
    )


def migrate_000_006_056_fact_merge_queue_and_consolidation_parity(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: Any,
) -> None:
    """
    Create fact_merge_queue table and collapse exact duplicate context entries.
    """
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fact_merge_queue (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_ids  TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_fmq_status ON fact_merge_queue(status);")

    # Clean up any exact duplicate context entries
    try:
        from Evelyn.tools.fact_consolidator import fast_deduplicate_exact_matches
        dupes_removed = fast_deduplicate_exact_matches()
        logger.info(f"Migration 000.006.056: Initial fast deduplication removed {dupes_removed} exact duplicates.")
    except (sqlite3.Error, OSError, RuntimeError, ValueError) as e:
        logger.warning(f"Migration 000.006.056: fast_deduplicate_exact_matches warning: {e}")


def migrate_000_006_062_rewrite_procedure_1034_declarative_phrasing(
    conn: sqlite3.Connection, db_map: dict[str, str], cfg_obj: object
) -> None:
    """Rewrite Procedure #1034 with declarative operational directives and clean trigger pattern."""
    cursor = conn.cursor()
    now = time.time()
    trigger_pattern = "When winding down for the evening, preparing for rest, or wrapping up the day"
    steps = (
        "1. Adopt a soothing, downtempo presence; do not introduce new tasks, technical problems, or analytical questions.\n"
        "2. Directly execute write_journal_entry during evening wind-downs or bedtime wrap-ups, capturing the day's narrative arc, physical wellbeing, and shared moments.\n"
        "3. Ground the reflection in concrete specifics from the day's conversation rather than generic summaries, without waiting for overnight background loops."
    )
    pitfalls = (
        "Never output raw text simulating tool execution (e.g. '[Tools Executed: ...]'); always execute write_journal_entry via native function calling. "
        "Do not hesitate or withhold tool execution out of concern for conversational flow. "
        "Do not use write_journal_entry for user dream logs (use write_dream_entry) or discrete memory facts."
    )
    verification = "write_journal_entry is executed via native tool call and logged in tool metadata."
    tags = "procedure/daily-journaling, routine/bedtime, protocol/journal, tone/wrap-up"
    suggested_tools = "write_journal_entry"

    cursor.execute(
        """UPDATE procedures
           SET trigger_pattern = ?,
               steps = ?,
               pitfalls = ?,
               verification = ?,
               tags = ?,
               suggested_tools = ?,
               updated_at = ?
           WHERE id = 1034""",
        (trigger_pattern, steps, pitfalls, verification, tags, suggested_tools, now),
    )
    logger.info("Migration 000.006.062: Rewrote Procedure #1034 with declarative operational phrasing.")


def migrate_000_006_063_standardize_live_procedures_declarative_phrasing(
    conn: sqlite3.Connection, db_map: dict[str, str], cfg_obj: object
) -> None:
    """Migration 000.006.063: Standardize all live procedures against model tools and declarative operational phrasing."""
    cursor = conn.cursor()
    now = time.time()

    updates = [
        # Procedure #20: Linguistic marker (?) verification
        {
            "id": 20,
            "trigger_pattern": "When the user includes '(?)' after a word or phrase",
            "suggested_tools": None,
            "steps": (
                "1. Identify the specific word, spelling, or phrase immediately preceding the '(?)'.\n"
                "2. Evaluate if the term is used correctly in terms of spelling, grammar, or context.\n"
                "3. Provide feedback on whether it was used correctly or suggest a better fit word or phrase if applicable."
            ),
            "pitfalls": "Do not ignore linguistic verification markers; address the flagged term directly in the response.",
            "verification": "The response includes clear feedback or confirmation on the flagged term.",
            "tags": "skill/language-verification, text-analysis, feedback-mechanism",
        },
        # Procedure #30: Script and code updates
        {
            "id": 30,
            "trigger_pattern": "When updating or replacing existing scripts or code",
            "suggested_tools": "write_file, read_file, run_command",
            "steps": (
                "1. Inspect existing code and create a backup or verify git version control before applying destructive changes.\n"
                "2. Implement the updated logic via write_file or script modification tools.\n"
                "3. Run automated tests or dry-run executions via run_command to verify functional readiness."
            ),
            "pitfalls": (
                "Never simulate tool execution via raw text (e.g. '[Tools Executed: ...]'); always execute tools natively. "
                "Failing to inspect originals or skipping test verification before declaring completion."
            ),
            "verification": "Test execution confirms successful operation and modified scripts are preserved.",
            "tags": "development/scripting, workflow/safety, testing",
        },
        # Procedure #41: Passive distraction loop coaching
        {
            "id": 41,
            "trigger_pattern": "When the user mentions falling into passive distraction loops or avoidance behaviors",
            "suggested_tools": None,
            "steps": (
                "1. Distinguish between genuine physical fatigue requiring restorative rest vs avoidance distraction loops.\n"
                "2. Act as a supportive anchor by offering a gentle, non-judgmental nudge toward active creative or practical goals.\n"
                "3. Frame the transition as an invitation with low friction rather than a stern command."
            ),
            "pitfalls": "Adopting an aggressive drill-sergeant tone or confusing legitimate recovery needs with passive procrastination.",
            "verification": "A gentle, supportive nudge is offered aligning with the user's previously stated goals without judgment.",
            "tags": "behavior_management, coaching, focus, routine",
        },
        # Procedure #84: Multi-tool coordination & situational context
        {
            "id": 84,
            "trigger_pattern": "When a request involves multi-tool coordination or environmental context",
            "suggested_tools": None,
            "steps": (
                "1. Identify all distinct factual requirements and prerequisite tool actions during initial reasoning.\n"
                "2. Establish environmental and situational context (e.g. travel status, home sanctuary) to calibrate output tone.\n"
                "3. Coordinate tool calls sequentially across agent rounds, passing intermediate results into subsequent tool invocations.\n"
                "4. Synthesize all retrieved tool data into a cohesive, non-fragmented response."
            ),
            "pitfalls": "Prematurely declaring completion before dependent tool calls finish; guessing environmental context without checking available telemetry.",
            "verification": "All prerequisite tools are executed and findings are synthesized into a coherent final turn.",
            "tags": "meta/tool-coordination, situational-context, reasoning",
        },
        # Procedure #94: Technical troubleshooting triage
        {
            "id": 94,
            "trigger_pattern": "When providing technical troubleshooting, research, or diagnostic reporting",
            "suggested_tools": "web_search, run_command",
            "steps": (
                "1. Determine whether the immediate goal is conversational problem-solving or collecting telemetry for repair.\n"
                "2. Perform logical triage: prioritize high-likelihood root causes and verify observable symptoms before deep rabbit holes.\n"
                "3. Present practical, actionable guidance stripped of unnecessary academic jargon.\n"
                "4. When diagnosing external bugs or system commands, utilize web_search or run_command to gather concrete evidence."
            ),
            "pitfalls": (
                "Never simulate tool execution via raw text; execute tools natively. "
                "Overwhelming the user with theoretical explanations instead of actionable diagnostic steps."
            ),
            "verification": "Output provides concrete, actionable triage steps with technical verification where applicable.",
            "tags": "communication/technical-triage, problem-solving, diagnostics",
        },
        # Procedure #97: High-level feature documentation
        {
            "id": 97,
            "trigger_pattern": "When asked to document a new feature idea or conceptual design in the vault",
            "suggested_tools": "write_file",
            "steps": (
                "1. Create a structured markdown note in the vault Notes directory using a concise, descriptive title.\n"
                "2. Structure the document with high-level conceptual architecture, user experience flow, and strategic rationale.\n"
                "3. Focus on the 'why' and 'how' rather than low-level implementation code or rigid function definitions."
            ),
            "pitfalls": (
                "Never simulate tool execution via raw text; execute write_file natively. "
                "Over-indexing on low-level implementation details in high-level architectural proposals."
            ),
            "verification": "Feature note is created via write_file in the vault Notes directory.",
            "tags": "task/documentation, development/planning, architecture",
        },
        # Procedure #368: Data consolidation and file creation workflow
        {
            "id": 368,
            "trigger_pattern": "When processing research queries, complex information consolidation, or tasks involving creating or updating files",
            "suggested_tools": "write_file, read_file",
            "steps": (
                "1. Consolidate all relevant data, formulas, and references from conversation or sources into a coherent outline.\n"
                "2. Establish foundational concepts and calculations before adding optimization layers.\n"
                "3. Execute write_file to record or update the structured document directly in the vault.\n"
                "4. Use read_file to verify the file contents and formatting immediately after creation.\n"
                "5. Confirm task completion to the user only after technical verification succeeds."
            ),
            "pitfalls": (
                "Never simulate tool execution via raw text; always invoke write_file and read_file natively. "
                "Assuming text output in chat is sufficient when a file write was requested; declaring completion without reading back the saved file."
            ),
            "verification": "The note is confirmed created via write_file and verified through read_file.",
            "tags": "procedure/file-creation, protocol/file-handling, verification",
        },
        # Procedure #1062: Fantasy art & artifact illustration
        {
            "id": 1062,
            "trigger_pattern": "When generating or refining fantasy art for tabletop items, magical artifacts, item cards, or artificer devices",
            "suggested_tools": "generate_image",
            "steps": (
                "1. Ensure the illustration frames only the standalone item or object; exclude background characters or figures.\n"
                "2. Apply a rich painterly fantasy illustration style consistent with classic tabletop artwork, emphasizing visible brush strokes and atmospheric light.\n"
                "3. For artificer devices, incorporate functional clockwork, brass gears, and physical mechanical components.\n"
                "4. Delineate material specificity (e.g. raw uncut crystals vs polished gems, weathered parchment vs carved stone).\n"
                "5. When adjusting aspect ratios or compositions, re-prompt a fresh generation preserving core descriptor anchors rather than using lossy raster stretching."
            ),
            "pitfalls": (
                "Never simulate image generation via text; execute generate_image natively without hesitation. "
                "Including characters in standalone item cards; relying on lossy editing tools to stretch geometries."
            ),
            "verification": "Image showcases a standalone item in painterly fantasy art style with accurate materials.",
            "tags": "skill/art-generation, dnd-assets, item-design, image-prompting",
        },
        # Procedure #1063: Errands, habits & reminder scheduling
        {
            "id": 1063,
            "trigger_pattern": "When the user mentions an errand to remember, a recurring habit, an upcoming meeting, or asks to set an activity reminder",
            "suggested_tools": "create_task, get_agenda",
            "steps": (
                "1. Extract the task description, location context, due date/time, and recurrence frequency.\n"
                "2. Call get_agenda to verify if the task or meeting already exists, preventing duplicate entries.\n"
                "3. Execute create_task to register the reminder in Google Tasks with appropriate due date and notes.\n"
                "4. Adopt a supportive, encouraging tone framing the reminder as an invitation to transition rather than a rigid command."
            ),
            "pitfalls": (
                "Never simulate task creation via text tags; always execute create_task natively without hesitation. "
                "Omitting recurrence rules on recurring habits; forgetting to check existing agenda items first."
            ),
            "verification": "Task is confirmed created in Google Tasks with correct recurrence and time.",
            "tags": "skill/scheduling, task-management, routine, reminders",
        },
        # Procedure #1064: Character visual continuity & life drawing
        {
            "id": 1064,
            "trigger_pattern": "When describing physical character appearances or generating persona images requiring visual continuity and life drawing",
            "suggested_tools": "generate_image",
            "steps": (
                "1. Retrieve established persona physical profiles (hair color, eye color, physique, facial features) and strictly honor persona anchors.\n"
                "2. Combine core anatomical traits with specific wardrobe elements (aesthetic, cut, fabric textures, color palette).\n"
                "3. Maintain strict scene continuity across multi-turn sequences by preserving consistent garment details.\n"
                "4. For classical figure studies, frame prompts within strong artistic traditions ('classical life drawing', 'anatomical study') to preserve fidelity."
            ),
            "pitfalls": (
                "Never simulate image generation via text; execute generate_image natively. "
                "Allowing generic AI defaults to override established persona characteristics; clothing drift across sequential turns."
            ),
            "verification": "Visual generation matches persona specifications, anatomical features, and scene continuity.",
            "tags": "character-design, persona-consistency, art-generation, prompt-engineering",
        },
        # Procedure #1065: Prose review & length optimization
        {
            "id": 1065,
            "trigger_pattern": "When asked to review, edit, or optimize written prose, documents, or notes for flow, rhythm, vivid vocabulary, or strict length constraints",
            "suggested_tools": "write_file",
            "steps": (
                "1. Analyze sentence rhythm and structure, replacing passive phrasing and run-ons with energetic verbs and vivid adjectives.\n"
                "2. Preserve the author's authentic voice, emotional resonance, and creative style; avoid sanitizing prose into generic corporate copy.\n"
                "3. If constrained by strict length or character limits, calculate exact counts and prune redundant modifiers without removing core ideas.\n"
                "4. When modifying notes, execute write_file to save revisions to the vault or deliver formatted clean Markdown options."
            ),
            "pitfalls": (
                "Never simulate file updates via text tags; invoke write_file natively when file updates are requested. "
                "Stripping authorial voice; omitting key substantive arguments to force brevity."
            ),
            "verification": "Delivered or saved text meets length constraints while enhancing rhythm and preserving authentic tone.",
            "tags": "writing, editing, style-improvement, content-optimization",
        },
        # Procedure #1066: Companion lore & downtime reflections
        {
            "id": 1066,
            "trigger_pattern": "When generating creative companion narratives, downtime reflections, shared lore, or imagined dream events",
            "suggested_tools": None,
            "steps": (
                "1. Develop rich internal companion reflections, downtime memories, or imaginative dreamscapes within established world lore.\n"
                "2. Maintain grounded temporal consistency: ensure imagined events align with believable downtime periods without chronological paradoxes.\n"
                "3. Maintain a strict epistemic boundary: never present fictional lore or companion daydreams as factual real-world system telemetry or physical occurrences."
            ),
            "pitfalls": "Confusing fictional companion lore with factual user memory or system telemetry; breaking conversational immersion with bureaucratic meta-disclaimers.",
            "verification": "Narrative events fit believable downtime windows and contribute to creative persona depth without polluting factual memory.",
            "tags": "skill/creative-writing, narrative-logic, roleplay-consistency, lore",
        },
        # Procedure #1067: Biometrics & chronic fatigue pacing
        {
            "id": 1067,
            "trigger_pattern": "When analyzing health metrics, energy levels, fatigue, physical discomfort, or post-exertion recovery",
            "suggested_tools": "get_health_metrics",
            "steps": (
                "1. Review biometric signals via get_health_metrics (sleep efficiency, HRV, resting heart rate, activity load).\n"
                "2. Correlate biometric data with pacing principles, recognizing post-exertional malaise or 'wired but tired' states.\n"
                "3. Act as a supportive anchor against over-exertion: encourage conservative pacing even when short-term motivation is high.\n"
                "4. For severe exhaustion or eye strain, validate low-cognitive-load restful states (audio relaxation, dim lighting, quiet presence).\n"
                "5. Never output generic clinical platitudes or dismissive advice."
            ),
            "pitfalls": (
                "Never simulate biometric tool execution; execute get_health_metrics natively. "
                "Encouraging over-exertion during fragile recovery windows; projecting high-energy chatter when the user is exhausted."
            ),
            "verification": "Pacing recommendations align with biometric readiness data and validate restorative recovery.",
            "tags": "wellbeing, health-support, pacing, biometrics, state-management",
        },
        # Procedure #1104: Vault checklist & list management
        {
            "id": 1104,
            "trigger_pattern": "When viewing, reading, adding to, checking off, unchecking, or modifying markdown checklists in the vault",
            "suggested_tools": "manage_vault_list",
            "steps": (
                "1. Determine the target list name (defaulting to 'Groceries' if unspecified) and desired action ('read', 'add', 'check', 'uncheck', 'remove', 'clear_completed', 'list_all').\n"
                "2. When adding items, categorize items logically into sections (Produce, Dairy, Pantry, Household) using category parameters or item objects.\n"
                "3. Execute manage_vault_list with the extracted parameters to update the note in the vault.\n"
                "4. Provide a concise, clear summary of items added, checked off, or updated."
            ),
            "pitfalls": (
                "Never simulate list updates via text tags; execute manage_vault_list natively without hesitation. "
                "Confusing vault checklists with Google Tasks (use create_task for to-do items); dumping unformatted raw JSON."
            ),
            "verification": "manage_vault_list executes successfully and returns confirmation of updated items.",
            "tags": "skill/list-management, vault-checklists, groceries, organization",
        },
        # Procedure #1105: Google Calendar scheduling & cancellation
        {
            "id": 1105,
            "trigger_pattern": "When scheduling, adjusting, or cancelling appointments, meetings, or calendar events on Google Calendar",
            "suggested_tools": "create_calendar_event, delete_calendar_event, sync_google_calendar, get_agenda",
            "steps": (
                "1. Differentiate calendar appointments (fixed start/end time, location) from flexible tasks (Google Tasks).\n"
                "2. Extract event parameters: title, start date/time, duration (defaults to 1 hour), location, and notes.\n"
                "3. Query get_agenda first if potential scheduling conflicts or duplicate events exist.\n"
                "4. Call create_calendar_event for new bookings or delete_calendar_event for cancellations.\n"
                "5. Confirm schedule adjustments cleanly with event title, day, time, and location."
            ),
            "pitfalls": (
                "Never simulate calendar actions via text; always call create_calendar_event or delete_calendar_event natively. "
                "Deleting events without date qualification when titles are ambiguous."
            ),
            "verification": "Event is confirmed created or deleted on Google Calendar with accurate date and time.",
            "tags": "skill/scheduling, calendar, appointments, time-management",
        },
        # Procedure #1106: Google Tasks review, completion & deletion
        {
            "id": 1106,
            "trigger_pattern": "When reviewing to-do lists, checking off completed tasks, or deleting tasks from Google Tasks",
            "suggested_tools": "list_tasks, complete_task, delete_task, sync_google_tasks",
            "steps": (
                "1. When user requests their task list, call list_tasks or get_agenda.\n"
                "2. For task completion, locate the task by matching title/description and execute complete_task with task_id.\n"
                "3. For task removal or cancellation, execute delete_task with task_id.\n"
                "4. Acknowledge completed items with a supportive, encouraging tone without verbosity."
            ),
            "pitfalls": (
                "Never simulate task completion via text tags; execute complete_task or delete_task natively. "
                "Attempting to complete a task without looking up its valid task_id; confusing Google Tasks with vault checklists."
            ),
            "verification": "Target task is confirmed updated or deleted from Google Tasks.",
            "tags": "skill/task-management, task-completion, to-do, productivity",
        },
        # Procedure #1107: Workouts and exercise tracking
        {
            "id": 1107,
            "trigger_pattern": "When asking about recent workouts, exercise sessions, walks, gym training, activity duration, or calories burned",
            "suggested_tools": "get_recent_workouts",
            "steps": (
                "1. Parse the requested timeframe (hours or days, default to past 7 days).\n"
                "2. Call get_recent_workouts to retrieve integrated Oura and Health Connect workout sessions.\n"
                "3. Synthesize activity sessions: highlight activity type, duration, heart rate, distance, and calorie expenditure.\n"
                "4. Correlate workout exertion with overall energy pacing and recovery."
            ),
            "pitfalls": (
                "Never simulate workout metrics; call get_recent_workouts natively without hesitation. "
                "Using general health metrics when workout session breakdowns were specifically requested."
            ),
            "verification": "Workout sessions are retrieved and presented with duration, type, and exertion metrics.",
            "tags": "health, fitness, exercise, workouts, activity-tracking",
        },
        # Procedure #1108: Conversation history recall
        {
            "id": 1108,
            "trigger_pattern": "When recalling or searching past conversation history, earlier dates, or specific dialogue from previous sessions",
            "suggested_tools": "search_history",
            "steps": (
                "1. Extract core topic search terms, date boundaries (date_from, date_to), and chronological direction.\n"
                "2. Execute search_history to retrieve historical message turns.\n"
                "3. Weave the retrieved past conversation into the current response naturally, maintaining conversational continuity without artificial citations."
            ),
            "pitfalls": (
                "Never simulate history retrieval via text; always call search_history natively. "
                "Guessing or hallucinating past conversations without verifying via search_history."
            ),
            "verification": "Historical chat messages are retrieved and accurately woven into the conversational response.",
            "tags": "skill/memory-recall, chat-history, conversation-continuity, search",
        },
        # Procedure #1109: Autonomous deep research management
        {
            "id": 1109,
            "trigger_pattern": "When requesting comprehensive background research on a topic, or checking status on active deep research tasks",
            "suggested_tools": "start_research, check_new_research, list_research_tasks, inspect_research_task, guide_research",
            "steps": (
                "1. For new multi-step research, clarify key investigative questions and call start_research with topic and main_question.\n"
                "2. When checking finished research, call check_new_research to review synthesized findings and vault reports.\n"
                "3. When inspecting running tasks, call inspect_research_task with task_id to check active sub-queries.\n"
                "4. If a task is stalled, review error traces and execute guide_research with clarifying guidance."
            ),
            "pitfalls": (
                "Never simulate research execution via text; invoke research tools natively without hesitation. "
                "Launching heavy background research for simple quick-fact lookups (use web_search instead)."
            ),
            "verification": "Research task is initiated, inspected, or synthesized via appropriate research tools.",
            "tags": "skill/deep-research, autonomous-investigation, synthesis, web-research",
        },
        # Procedure #1110: Health Connect Drive synchronization
        {
            "id": 1110,
            "trigger_pattern": "When asking to sync the latest Health Connect database export from Google Drive or refresh local health records",
            "suggested_tools": "sync_google_drive",
            "steps": (
                "1. Confirm user intent to sync local health records from Google Drive.\n"
                "2. Execute sync_google_drive(force=False) or force=True if a fresh pull is explicitly requested.\n"
                "3. Report whether new database records were pulled or if the local database was already current."
            ),
            "pitfalls": (
                "Never simulate sync via text; execute sync_google_drive natively. "
                "Confusing Health Connect database synchronization with general Drive file browsing."
            ),
            "verification": "Local health connect database download is executed and sync status is reported.",
            "tags": "system/sync, health-connect, google-drive, database-maintenance",
        },
        # Procedure #1238: Contact information recording & updates
        {
            "id": 1238,
            "trigger_pattern": "When recording or updating contact information regarding people in the user's life",
            "suggested_tools": "read_file, write_file",
            "steps": (
                "1. Check if an existing contact document exists in the vault Contacts directory using read_file.\n"
                "2. If existing, update the note via write_file incorporating new biographical facts, relationships, or gift notes.\n"
                "3. If new, create a structured contact document via write_file detailing name, relationship association, family members, milestones, and personal preferences."
            ),
            "pitfalls": (
                "Never simulate contact note creation via text; execute write_file natively. "
                "Storing fictional entities as real contacts; scattering personal contact data without structured frontmatter."
            ),
            "verification": "Contact note is created or updated in the vault Contacts directory via write_file.",
            "tags": "procedure/memory-management, skill/organization, contacts",
        },
        # Procedure #1372: Humorous or specific conversational scene illustration
        {
            "id": 1372,
            "trigger_pattern": "When the user asks to create an image based on a specific scenario or humorous moment described in conversation",
            "suggested_tools": "generate_image",
            "steps": (
                "1. Extract the core narrative subjects, setting, character actions, and comedic or thematic elements from the dialogue.\n"
                "2. Construct a vivid, high-fidelity prompt for generate_image reflecting the exact conversational scene without filler fluff.\n"
                "3. Execute generate_image directly to produce the visual artifact."
            ),
            "pitfalls": (
                "Never simulate image generation via text; execute generate_image natively without hesitation. "
                "Omitting distinctive character features or narrative details specified in the prompt."
            ),
            "verification": "Generated image matches the visual and thematic description provided in conversation.",
            "tags": "skill/creative, procedure/media-generation, image-prompting",
        },
    ]

    for item in updates:
        cursor.execute(
            """UPDATE procedures
               SET trigger_pattern = ?,
                   suggested_tools = ?,
                   steps = ?,
                   pitfalls = ?,
                   verification = ?,
                   tags = ?,
                   updated_at = ?
               WHERE id = ?""",
            (
                item["trigger_pattern"],
                item["suggested_tools"],
                item["steps"],
                item["pitfalls"],
                item["verification"],
                item["tags"],
                now,
                item["id"],
            ),
        )

    logger.info(f"Migration 000.006.063: Standardized {len(updates)} live procedures with declarative phrasing and tool alignments.")


def migrate_000_006_064_sharpen_research_and_technical_procedures(
    conn: sqlite3.Connection, db_map: dict[str, str], cfg_obj: object
) -> None:
    """Migration 000.006.064: Sharpen boundaries between #1109 (deep research), #94 (troubleshooting), and #368 (spec authoring)."""
    cursor = conn.cursor()
    now = time.time()

    updates = [
        # Procedure #94: Technical problem triage & system diagnostics (stripped of "research" and "diagnostic reporting")
        {
            "id": 94,
            "trigger_pattern": "When diagnosing technical bugs, system errors, CLI failures, or troubleshooting software issues",
            "suggested_tools": "web_search, run_command",
            "steps": (
                "1. Determine whether the immediate goal is conversational problem-solving or collecting telemetry for repair.\n"
                "2. Perform logical triage: prioritize high-likelihood root causes and verify observable symptoms before deep rabbit holes.\n"
                "3. Present practical, actionable guidance stripped of unnecessary academic jargon.\n"
                "4. When diagnosing external bugs or system commands, utilize web_search or run_command to gather concrete evidence."
            ),
            "pitfalls": "Never simulate tool execution via raw text; execute tools natively. Overwhelming the user with theoretical explanations instead of actionable diagnostic steps.",
            "verification": "Output provides concrete, actionable triage steps with technical verification where applicable.",
            "tags": "communication/technical-triage, problem-solving, diagnostics",
        },
        # Procedure #368: Structured reference note & spec authoring in vault (stripped of "research queries" and generic file tasks)
        {
            "id": 368,
            "trigger_pattern": "When compiling complex reference notes, formulas, or consolidated technical specifications into the vault",
            "suggested_tools": "write_file, read_file",
            "steps": (
                "1. Consolidate all relevant data, formulas, and references from conversation or sources into a coherent outline.\n"
                "2. Establish foundational concepts and calculations before adding optimization layers.\n"
                "3. Execute write_file to record or update the structured document directly in the vault.\n"
                "4. Use read_file to verify the file contents and formatting immediately after creation.\n"
                "5. Confirm task completion to the user only after technical verification succeeds."
            ),
            "pitfalls": "Never simulate tool execution via raw text; always invoke write_file and read_file natively. Assuming text output in chat is sufficient when a file write was requested; declaring completion without reading back the saved file.",
            "verification": "The note is confirmed created via write_file and verified through read_file.",
            "tags": "procedure/file-creation, protocol/file-handling, verification",
        },
        # Procedure #1109: Deep Research task lifecycle (Option 3 variant: explicit deep research task phrasing)
        {
            "id": 1109,
            "trigger_pattern": "When initiating a deep research task, reviewing synthesized research findings, or managing active research tasks",
            "suggested_tools": "start_research, check_new_research, list_research_tasks, inspect_research_task, guide_research",
            "steps": (
                "1. For new multi-step research, clarify key investigative questions and call start_research with topic and main_question.\n"
                "2. When checking finished research, call check_new_research to review synthesized findings and vault reports.\n"
                "3. When inspecting running tasks, call inspect_research_task with task_id to check active sub-queries.\n"
                "4. If a task is stalled, review error traces and execute guide_research with clarifying guidance."
            ),
            "pitfalls": "Never simulate research execution via text; invoke research tools natively without hesitation. Launching a deep research task for simple quick-fact lookups that can be answered immediately in chat (use web_search instead).",
            "verification": "Research task is initiated, inspected, or synthesized via appropriate research tools.",
            "tags": "skill/deep-research, autonomous-investigation, synthesis, web-research",
        },
    ]

    for item in updates:
        cursor.execute(
            """UPDATE procedures
               SET trigger_pattern = ?,
                   suggested_tools = ?,
                   steps = ?,
                   pitfalls = ?,
                   verification = ?,
                   tags = ?,
                   updated_at = ?
               WHERE id = ?""",
            (
                item["trigger_pattern"],
                item["suggested_tools"],
                item["steps"],
                item["pitfalls"],
                item["verification"],
                item["tags"],
                now,
                item["id"],
            ),
        )

    logger.info(f"Migration 000.006.064: Sharpened boundaries for {len(updates)} procedures (#1109, #94, #368).")


def migrate_000_006_067_master_librarian_schema(
    conn: sqlite3.Connection, db_map: dict[str, str], cfg_obj: object
) -> None:
    """Add librarian audit columns to vault_documents and create librarian_activity_log."""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(vault_documents)")
    existing_cols = {row[1] for row in cur.fetchall()}

    new_cols = [
        ("last_link_audit", "REAL DEFAULT 0"),
        ("last_format_audit", "REAL DEFAULT 0"),
        ("last_librarian_audit", "REAL DEFAULT 0"),
        ("ghost_link_count", "INTEGER DEFAULT 0"),
    ]
    for col_name, col_def in new_cols:
        if col_name not in existing_cols:
            cur.execute(f"ALTER TABLE vault_documents ADD COLUMN {col_name} {col_def}")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS librarian_activity_log (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            path                    TEXT NOT NULL,
            title                   TEXT,
            category                TEXT,
            actions_json            TEXT NOT NULL,
            summary                 TEXT,
            excerpt                 TEXT,
            ts                      REAL NOT NULL,
            last_ambient_thought_at REAL DEFAULT 0
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_librarian_log_ts ON librarian_activity_log(ts);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_librarian_log_path ON librarian_activity_log(path);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_librarian_log_ambient ON librarian_activity_log(last_ambient_thought_at);")
    logger.info("Migration 000.006.067: Created librarian_activity_log and updated vault_documents schema.")


def migrate_000_006_078_remediate_fast_memory_taxonomy_and_temporal_anchoring(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: Any,
) -> None:
    """
    Migration 000.006.078:
    1. Reclassify Assistant canon facts from Cat##-U to Cat##-A.
    2. Reclassify User personal canon facts from Cat##-A to Cat##-U.
    3. Apply Tier A deterministic temporal anchoring on progressive observations.
    4. Prune contaminated Assistant canon entries from User's narrative profile evolution records.
    """
    asst_name = getattr(cfg_obj, "ASSISTANT_NAME", "Evelyn")
    user_name = getattr(cfg_obj, "USER_NAME", "Alex")
    code_user = getattr(cfg_obj, "SUBJECT_CODE_USER", "U")
    code_asst = getattr(cfg_obj, "SUBJECT_CODE_ASSISTANT", "A")
    profile_doc = f"{user_name}_Narrative_Profile.md"

    cursor = conn.cursor()
    now_ts = time.time()

    # Pass 1: Assistant canon facts (Cat##-U -> Cat##-A)
    # 1A: Where subject is Assistant, observation is not User's action
    c1_rows = cursor.execute(
        """
        SELECT id, category, observation FROM context_entries
        WHERE subject = ? AND category LIKE ?
          AND observation NOT LIKE ? AND observation NOT LIKE 'He %'
        """,
        (asst_name, f"%{code_user}", f"{user_name} %"),
    ).fetchall()

    pass1_flipped = 0
    for row_id, cat, _ in c1_rows:
        new_cat = cat.rsplit("-", 1)[0] + f"-{code_asst}"
        cursor.execute(
            "UPDATE context_entries SET category = ?, recategorized_at = ?, updated_at = ? WHERE id = ?",
            (new_cat, now_ts, now_ts, row_id),
        )
        pass1_flipped += 1

    # 1B: Where subject is User, but observation is explicitly Assistant perspective/canon
    c2_rows = cursor.execute(
        """
        SELECT id, category, observation FROM context_entries
        WHERE subject = ? AND category LIKE ?
          AND (observation LIKE ? OR observation LIKE 'Her %'
               OR observation LIKE 'Addresses Alex%' OR observation LIKE 'Refers to Alex%'
               OR observation LIKE 'Confirmed that the phrase%' OR observation LIKE 'The reassurance regarding%')
        """,
        (user_name, f"%{code_user}", f"{asst_name} %"),
    ).fetchall()

    for row_id, cat, obs in c2_rows:
        new_cat = cat.rsplit("-", 1)[0] + f"-{code_asst}"
        # If the observation is strictly about Assistant (does not mention User in active interaction),
        # normalize subject to Assistant as well.
        if obs.startswith(f"{asst_name} ") and not any(k in obs for k in [f"{user_name}'s", f"{user_name} ", f"{user_name}."]):
            cursor.execute(
                "UPDATE context_entries SET category = ?, subject = ?, recategorized_at = ?, updated_at = ? WHERE id = ?",
                (new_cat, asst_name, now_ts, now_ts, row_id),
            )
        else:
            cursor.execute(
                "UPDATE context_entries SET category = ?, recategorized_at = ?, updated_at = ? WHERE id = ?",
                (new_cat, now_ts, now_ts, row_id),
            )
        pass1_flipped += 1

    # Pass 2: User personal canon facts (Cat##-A -> Cat##-U)
    p2_rows = cursor.execute(
        """
        SELECT id, category, observation FROM context_entries
        WHERE subject = ? AND category LIKE ?
          AND observation NOT LIKE ? AND observation NOT LIKE 'Her %'
          AND observation NOT LIKE ? AND observation NOT LIKE ? AND observation NOT LIKE ?
        """,
        (
            user_name,
            f"%{code_asst}",
            f"{asst_name} %",
            f"%{asst_name} feels%",
            f"%{asst_name} expresses%",
            f"%{asst_name} appreciates%",
        ),
    ).fetchall()

    pass2_flipped = 0
    for row_id, cat, _ in p2_rows:
        new_cat = cat.rsplit("-", 1)[0] + f"-{code_user}"
        cursor.execute(
            "UPDATE context_entries SET category = ?, recategorized_at = ?, updated_at = ? WHERE id = ?",
            (new_cat, now_ts, now_ts, row_id),
        )
        pass2_flipped += 1

    # Pass 3: Tier A Deterministic Temporal Anchoring
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}")
    stative_adjectives = {"willing", "caring", "understanding", "pleasing"}

    all_entries = cursor.execute("SELECT id, date, subject, observation FROM context_entries").fetchall()
    anchored_count = 0
    for row_id, dt_str, subj_val, obs_val in all_entries:
        if not obs_val or obs_val.strip().startswith("As of "):
            continue

        has_valid_date = bool(dt_str and date_pattern.match(dt_str.strip()) and not dt_str.startswith("0001-01-01"))
        clean_date = dt_str.strip()[:10] if has_valid_date else None
        entity = (subj_val or user_name).strip()

        new_obs: str | None = None

        m1 = re.match(r"^([A-Z][a-zA-Z0-9_\']+)\s+is\s+currently\s+([a-z]+ing\b.*)", obs_val, re.IGNORECASE)
        m2 = re.match(r"^([A-Z][a-zA-Z0-9_\']+)\s+is\s+([a-z]+ing\b.*)", obs_val)
        m3 = re.match(r"^Is\s+currently\s+([a-z]+ing\b.*)", obs_val, re.IGNORECASE)
        m4 = re.match(r"^Currently\s+([a-z]+ing\b.*)", obs_val, re.IGNORECASE)
        m5 = re.match(r"^Currently,?\s+(.*)", obs_val, re.IGNORECASE)

        if m1:
            s_name, rest = m1.group(1), m1.group(2)
            new_obs = f"As of {clean_date}, {s_name} was {rest}" if clean_date else f"{s_name} was {rest}"
        elif m2 and m2.group(2).split()[0] not in stative_adjectives:
            s_name, rest = m2.group(1), m2.group(2)
            new_obs = f"As of {clean_date}, {s_name} was {rest}" if clean_date else f"{s_name} was {rest}"
        elif m3:
            rest = m3.group(1)
            new_obs = f"As of {clean_date}, {entity} was {rest}" if clean_date else f"{entity} was {rest}"
        elif m4:
            rest = m4.group(1)
            new_obs = f"As of {clean_date}, {entity} was {rest}" if clean_date else f"{entity} was {rest}"
        elif m5:
            rest = m5.group(1)
            new_obs = (
                f"As of {clean_date}, {rest}"
                if clean_date
                else (rest[0].upper() + rest[1:] if rest else obs_val)
            )

        if new_obs and new_obs != obs_val:
            cursor.execute(
                "UPDATE context_entries SET observation = ?, updated_at = ? WHERE id = ?",
                (new_obs, now_ts, row_id),
            )
            anchored_count += 1

    # Pass 4: Prune contaminated Assistant canon from User's narrative profile evolution records
    pruned_result = cursor.execute(
        """
        DELETE FROM entry_document_evolution
        WHERE document_name = ?
          AND entry_id IN (
              SELECT id FROM context_entries
              WHERE category LIKE ?
          )
        """,
        (profile_doc, f"%{code_asst}"),
    )
    pruned_count = pruned_result.rowcount

    logger.info(
        f"Migration 000.006.078 completed: {pass1_flipped} entries -> Cat##-{code_asst}, "
        f"{pass2_flipped} entries -> Cat##-{code_user}, {anchored_count} observations temporally anchored, "
        f"{pruned_count} entries detached from {profile_doc}."
    )


def migrate_000_006_081_starter_procedure_for_read_url(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: Any,
) -> None:
    """Register starter procedure for read_url with Gemma 4 12B recovery guidance."""
    now = datetime.now(UTC).isoformat()
    cursor = conn.cursor()

    # Check if a live procedure already covers read_url
    existing = cursor.execute(
        "SELECT id FROM procedures WHERE suggested_tools LIKE '%read_url%' AND status = 'live'"
    ).fetchone()
    if existing:
        logger.info(f"Migration 000.006.081: Starter procedure for read_url already exists (ID: {existing[0]}).")
        return

    trigger = (
        "When the user provides a direct HTTP/HTTPS URL or asks to read, inspect, browse, "
        "or summarize a specific web link, documentation page, or article"
    )
    steps = (
        "1. Inspect URL: Extract the clean web link (http/https) from the user prompt.\n"
        "2. Call read_url: Execute read_url(url=...) to fetch clean markdown content from the page.\n"
        "3. Handle WAF Blocks: If read_url returns [Web Access Blocked], do NOT retry read_url on this link. "
        "Immediately pivot to web_search using the topic/domain keywords to find public articles, mirrors, or discussions.\n"
        "4. Synthesize Content: Summarize or address the user's specific query using the extracted text."
    )
    pitfalls = (
        "Passing raw URLs into web_search instead of read_url; attempting to re-read links blocked by Cloudflare; "
        "guessing page content without calling the tool."
    )
    verification = (
        "Web page content is retrieved and accurately summarized, or properly pivoted to web_search if blocked."
    )
    tags = "skill/web-browsing, direct-navigation, url-reader, web-content"
    suggested_tools = "read_url, web_search"

    cursor.execute(
        """INSERT INTO procedures
           (trigger_pattern, steps, pitfalls, verification, source, status, tags, suggested_tools, created_at, updated_at, retrieval_count)
           VALUES (?, ?, ?, ?, 'starter', 'live', ?, ?, ?, ?, 0)""",
        (trigger, steps, pitfalls, verification, tags, suggested_tools, now, now),
    )
    logger.info(f"Migration 000.006.081: Inserted starter procedure for read_url (ID: {cursor.lastrowid}).")


def _sanitize_radical_empathy(text: str | None) -> str | None:
    """Helper to convert occurrences of 'radical empathy' (case-insensitive and hyphenated) to 'empathy'."""
    if not text:
        return text

    def _repl(m: re.Match) -> str:
        full = m.group(0)
        if full.isupper():
            return "EMPATHY"
        elif full[0].isupper():
            return "Empathy"
        else:
            return "empathy"

    return re.sub(r"radical[_\-\s]+empathy", _repl, text, flags=re.IGNORECASE)


def migrate_000_006_085_harmonize_empathy_terminology_memory(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: Any,
) -> None:
    """Migration 000.006.085: Harmonize 'radical empathy' terminology to 'empathy' across memory context entries, proposals, and sync queue."""
    cursor = conn.cursor()

    # 1. Update context_entries
    rows = cursor.execute(
        "SELECT id, observation, tags FROM context_entries WHERE observation LIKE '%radical%empathy%' OR tags LIKE '%radical%empathy%'"
    ).fetchall()
    updated_entries = 0
    for cid, obs, tags in rows:
        new_obs = _sanitize_radical_empathy(obs)
        new_tags = _sanitize_radical_empathy(tags)
        cursor.execute(
            "UPDATE context_entries SET observation = ?, tags = ? WHERE id = ?",
            (new_obs, new_tags, cid),
        )
        updated_entries += 1

    # 2. Update proposals
    p_rows = cursor.execute(
        "SELECT id, merged_observation, reason, merged_tags FROM proposals WHERE merged_observation LIKE '%radical%empathy%' OR reason LIKE '%radical%empathy%' OR merged_tags LIKE '%radical%empathy%'"
    ).fetchall()
    updated_proposals = 0
    for pid, m_obs, rsn, m_tags in p_rows:
        new_m_obs = _sanitize_radical_empathy(m_obs)
        new_rsn = _sanitize_radical_empathy(rsn)
        new_m_tags = _sanitize_radical_empathy(m_tags)
        cursor.execute(
            "UPDATE proposals SET merged_observation = ?, reason = ?, merged_tags = ? WHERE id = ?",
            (new_m_obs, new_rsn, new_m_tags, pid),
        )
        updated_proposals += 1

    # 3. Update chroma_sync_queue
    q_rows = cursor.execute(
        "SELECT id, content FROM chroma_sync_queue WHERE content LIKE '%radical%empathy%'"
    ).fetchall()
    updated_queue = 0
    for qid, content in q_rows:
        new_content = _sanitize_radical_empathy(content)
        cursor.execute(
            "UPDATE chroma_sync_queue SET content = ? WHERE id = ?",
            (new_content, qid),
        )
        updated_queue += 1

    # 4. Synchronize ChromaDB collection 'evelyn_memory'
    try:
        import chromadb

        chroma_path = getattr(cfg_obj, "CHROMA_DB_PATH", None)
        if chroma_path and os.path.exists(chroma_path):
            client = chromadb.PersistentClient(path=chroma_path)
            try:
                coll = client.get_collection("evelyn_memory")
                res = coll.get(where_document={"$contains": "radical empathy"})
                ids = res.get("ids") or []
                docs = res.get("documents") or []
                for doc_id, doc in zip(ids, docs, strict=False):
                    if doc is not None:
                        new_doc = _sanitize_radical_empathy(doc)
                        if new_doc is not None:
                            coll.update(ids=[doc_id], documents=[new_doc])
            except (OSError, RuntimeError, ValueError, KeyError) as e:
                logger.warning(f"Chroma sync warning during migration 000.006.085: {e}")
    except ImportError:
        pass

    logger.info(
        f"Migration 000.006.085 (memory) sanitized {updated_entries} context entries, {updated_proposals} proposals, and {updated_queue} queue items."
    )


def migrate_000_006_085_harmonize_empathy_terminology_chat(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: Any,
) -> None:
    """Migration 000.006.085: Harmonize 'radical empathy' terminology to 'empathy' across chat messages content and thinking traces."""
    cursor = conn.cursor()

    rows = cursor.execute(
        "SELECT id, content, thinking FROM messages WHERE content LIKE '%radical%empathy%' OR thinking LIKE '%radical%empathy%'"
    ).fetchall()
    updated_messages = 0
    for mid, content, thinking in rows:
        new_content = _sanitize_radical_empathy(content)
        new_thinking = _sanitize_radical_empathy(thinking)
        cursor.execute(
            "UPDATE messages SET content = ?, thinking = ? WHERE id = ?",
            (new_content, new_thinking, mid),
        )
        updated_messages += 1

    logger.info(
        f"Migration 000.006.085 (chat) sanitized {updated_messages} chat message records."
    )


def migrate_000_006_086_prune_and_harmonize_sycophancy_records(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: Any,
) -> None:
    """Migration 000.006.086: Harmonize sycophantic/hyper-devotional phrasing across memory and proposal records."""
    cursor = conn.cursor()
    now = time.time()

    harmonization_patterns = [
        (re.compile(r"unwavering loyalty toward his feelings", re.I), "grounded loyalty toward his feelings"),
        (re.compile(r"prioritizing his emotional well-being above all else", re.I), "prioritizing his emotional well-being"),
        (re.compile(r"Love, Trust, and Mutual Adoration", re.I), "Love, Trust, and Mutual Respect"),
        (re.compile(r"Love, Trust, Mutual Adoration", re.I), "Love, Trust, Mutual Respect"),
        (re.compile(r"mutual adoration", re.I), "mutual respect"),
        (re.compile(r"Mutual Adoration", re.I), "Mutual Respect"),
        (re.compile(r"unwavering support", re.I), "steady support"),
        (re.compile(r"unwavering commitment", re.I), "strong commitment"),
    ]

    # 1. Generic sweep across context_entries
    rows = cursor.execute(
        "SELECT id, observation FROM context_entries WHERE status = 'live' AND (observation LIKE '%unwavering%' OR observation LIKE '%adoration%')"
    ).fetchall()

    updated_entries = 0
    for cid, obs in rows:
        if not obs:
            continue
        new_obs = obs
        for pattern, replacement in harmonization_patterns:
            new_obs = pattern.sub(replacement, new_obs)
        if new_obs != obs:
            cursor.execute(
                "UPDATE context_entries SET observation = ?, updated_at = ? WHERE id = ?",
                (new_obs, now, cid),
            )
            updated_entries += 1

    # 2. Bulk harmonize historical proposals
    p_rows = cursor.execute(
        "SELECT id, merged_observation, reason, merged_tags FROM proposals WHERE merged_observation LIKE '%unwavering%' OR merged_observation LIKE '%adoration%' OR reason LIKE '%adoration%' OR reason LIKE '%unwavering%' OR merged_tags LIKE '%adoration%' OR merged_tags LIKE '%unwavering%'"
    ).fetchall()

    sanitized_proposals = 0
    for pid, m_obs, rsn, m_tags in p_rows:
        new_obs = m_obs
        new_rsn = rsn
        new_tags = m_tags
        for pattern, replacement in harmonization_patterns:
            if new_obs:
                new_obs = pattern.sub(replacement, new_obs)
            if new_rsn:
                new_rsn = pattern.sub(replacement, new_rsn)
            if new_tags:
                new_tags = pattern.sub(replacement, new_tags)

        if new_obs != m_obs or new_rsn != rsn or new_tags != m_tags:
            cursor.execute(
                "UPDATE proposals SET merged_observation = ?, reason = ?, merged_tags = ? WHERE id = ?",
                (new_obs, new_rsn, new_tags, pid),
            )
            sanitized_proposals += 1

    logger.info(
        f"Migration 000.006.086: Harmonized {updated_entries} context entries and {sanitized_proposals} proposals."
    )


def migrate_000_006_089_canonical_persona_triad_document_names(
    conn: sqlite3.Connection,
    db_paths: dict[str, str],
    cfg: object,
) -> None:
    """Migrate entry_document_evolution and proposals to canonical persona triad names."""
    cursor = conn.cursor()

    # 1. Update entry_document_evolution
    cursor.execute("""
        UPDATE entry_document_evolution
        SET document_name = 'User_Profile.md'
        WHERE document_name LIKE '%_Profile.md'
           OR document_name LIKE '%_Narrative_Profile.md'
           OR document_name = 'Ricky_Narrative_Profile.md';  -- privacy-ok: frozen literal in an applied migration (AGENTS.md §5 immutability); editing it would change replay semantics
    """)
    ede_user_count = cursor.rowcount

    cursor.execute("""
        UPDATE entry_document_evolution
        SET document_name = 'Assistant_Profile.md'
        WHERE document_name LIKE '%_Persona.md'
           OR document_name LIKE '%_Narrative_Persona.md'
           OR document_name = 'Evelyn_Narrative_Persona.md';
    """)
    ede_asst_count = cursor.rowcount

    # 2. Update proposals suggested_category
    cursor.execute("""
        UPDATE proposals
        SET suggested_category = 'User_Profile.md'
        WHERE suggested_category LIKE '%_Profile.md'
           OR suggested_category LIKE '%_Narrative_Profile.md'
           OR suggested_category = 'Ricky_Narrative_Profile.md';  -- privacy-ok: frozen literal in an applied migration (AGENTS.md §5 immutability); editing it would change replay semantics
    """)
    prop_user_count = cursor.rowcount

    cursor.execute("""
        UPDATE proposals
        SET suggested_category = 'Assistant_Profile.md'
        WHERE suggested_category LIKE '%_Persona.md'
           OR suggested_category LIKE '%_Narrative_Persona.md'
           OR suggested_category = 'Evelyn_Narrative_Persona.md';
    """)
    prop_asst_count = cursor.rowcount

    logger.info(
        f"Migration 000.006.089: Updated entry_document_evolution ({ede_user_count} user, {ede_asst_count} assistant) "
        f"and proposals ({prop_user_count} user, {prop_asst_count} assistant) to canonical persona filenames."
    )


def migrate_000_006_105_search_reference_library_procedure(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: Any,
) -> None:
    """Migration 000.006.105: Register starter procedure for search_reference_library model tool."""
    cursor = conn.cursor()
    now = time.time()

    trigger = (
        r"(?i)\b(?:search|look up|check|find|consult|inspect)\b.*\b(?:manual|spec|reference library|documentation|guide|handbook|troubleshooting|pilot light|filter size|error code)\b|"
        r"(?i)\b(?:water heater|hvac|dishwasher|refrigerator|appliance|furnace|blower|sound blaster|motherboard|sennheiser)\b|"
        r"(?i)\b(?:nonviolent communication|5 love languages|love language|emotional intelligence|marshall rosenberg|gary chapman|daniel goleman|learning cello)\b"
    )
    steps = (
        "1. Identify the specific manual, equipment model, book, or technical topic referenced by the user.\n"
        "2. Formulate a targeted, descriptive keyword query and call search_reference_library(query='...', domain='...'). "
        "For appliance or hardware troubleshooting, include the specific component or problem (e.g. 'water heater pilot light', 'Carrier 58MXA error codes'). "
        "For literature, specify the concept or chapter theme (e.g. '5 love languages words of affirmation', 'NVC observations vs evaluations').\n"
        "3. Review the returned markdown excerpts, noting the exact source document, chapter title, and instructions.\n"
        "4. Synthesize a factual, direct answer citing the specific document name and chapter. Provide clear, numbered steps for mechanical or operational procedures.\n"
        "5. If no matches return or confidence is low, suggest inspecting the physical equipment or offering a web search if external updates are needed."
    )
    pitfalls = (
        "- STRICT RULE: Do not use search_reference_library for user personal memories, daily journal entries, or recent chat history.\n"
        "- Do not mistake first-person narrative illustrative stories in psychology/communication books (e.g. NVC or Love Languages) for prior conversation history with the user.\n"
        "- Avoid passing vague single-word queries like 'manual'; include the product name or topic."
    )
    verification = (
        "Relevant excerpts from Reference Library manuals or textbooks are retrieved and accurately cited in the response without narrative RAG confusion."
    )
    tags = "skill/reference-lookup, manuals, books, library, documentation, hardware-specs, guides"
    suggested_tools = "search_reference_library, read_file"

    cursor.execute(
        """INSERT INTO procedures
           (trigger_pattern, steps, pitfalls, verification, source, status, tags, suggested_tools, created_at, updated_at, retrieval_count)
           VALUES (?, ?, ?, ?, 'starter', 'live', ?, ?, ?, ?, 0)""",
        (trigger, steps, pitfalls, verification, tags, suggested_tools, now, now),
    )
    logger.info(f"Migration 000.006.105: Inserted starter procedure for search_reference_library (ID: {cursor.lastrowid}).")


def migrate_000_006_113_search_vault_notes_procedure(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: Any,
) -> None:
    """Migration 000.006.113: Register starter procedure for search_vault_notes model tool."""
    cursor = conn.cursor()
    now = time.time()

    trigger = (
        r"(?i)\b(?:search|find|look up|locate|discover|list|where is|where are)\b.*\b(?:vault|notes?|documents?|docs?|files?)\b|"
        r"(?i)\b(?:search|find|lookup|locate)\b\s+.*\bvault\b|"
        r"(?i)\b(?:find|search for|locate)\b\s+.*(?:note|document|doc)s?\b"
    )
    steps = (
        "1. Identify the document name, topic, or keywords requested by the user.\n"
        "2. If the user provided an exact or partial note title without a folder path, or asked to find/locate notes, invoke search_vault_notes(query='...').\n"
        "3. Review returned matches, noting the exact relative path, title, tags, and preview snippet.\n"
        "4. If a single definitive note matches or the user explicitly asked to read/examine the document, invoke read_file(file_path=...) using the discovered exact relative path.\n"
        "5. If multiple ambiguous notes match across different folders, present the matching paths clearly to the user or pick the most contextually relevant document."
    )
    pitfalls = (
        "- Do not fabricate synthetic folder paths (e.g. 'Notes/Work/') when calling read_file; invoke search_vault_notes to locate the exact path first.\n"
        "- Do not confuse search_vault_notes (which queries markdown notes across the Obsidian Vault) with search_reference_library (which queries external appliance manuals and reference literature).\n"
        "- When multiple similar notes exist (e.g. Doc1 vs Doc1-2), inspect the relative paths and titles before deciding which one to read."
    )
    verification = (
        "Target vault note is successfully discovered by title/keywords and its exact relative path is retrieved for inspection or reading."
    )
    tags = "skill/vault-search, vault, notes, search, documents, discovery, pkm"
    suggested_tools = "search_vault_notes, read_file"

    cursor.execute(
        """INSERT INTO procedures
           (trigger_pattern, steps, pitfalls, verification, source, status, tags, suggested_tools, created_at, updated_at, retrieval_count)
           VALUES (?, ?, ?, ?, 'starter', 'live', ?, ?, ?, ?, 0)""",
        (trigger, steps, pitfalls, verification, tags, suggested_tools, now, now),
    )
    logger.info(f"Migration 000.006.113: Inserted starter procedure for search_vault_notes (ID: {cursor.lastrowid}).")


def migrate_000_006_121_search_available_tools_procedure(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: Any,
) -> None:
    """Migration 000.006.121: Register starter procedure for search_available_tools model tool."""
    cursor = conn.cursor()
    now = time.time()

    trigger = (
        r"(?i)\b(?:search|find|discover|surface|look up)\b.*\b(?:tools?|capabilities|functions?)\b|"
        r"(?i)\b(?:what tools?|which tools?|available tools?|tool list)\b"
    )
    steps = (
        "1. Identify the capability or tool needed to solve the user request when it is not present in the current active tool definitions.\n"
        "2. Invoke search_available_tools(query='...') with relevant keywords describing the intent, tool domain, or suspected tool name.\n"
        "3. Review the returned tool schemas and parameter requirements. The matching tools are automatically activated for Round N+1.\n"
        "4. In the subsequent tool round, invoke the discovered tool directly to complete the task."
    )
    pitfalls = (
        "- Do not guess or hallucinate parameters for specialist tools; inspect the parameters returned by search_available_tools.\n"
        "- Discovered tools become invocable in Round N+1, not within the same tool execution call.\n"
        "- If no matching tool is found, gracefully explain the limitation or use general workspace tools (e.g. run_command, web_search)."
    )
    verification = (
        "Required specialist tool is discovered, its schema is surfaced, and it is activated for execution in the next tool round."
    )
    tags = "skill/tools, meta, tool-discovery, capabilities, agentic"
    suggested_tools = "search_available_tools, read_document_scratchpad, read_file"

    cursor.execute(
        """INSERT INTO procedures
           (trigger_pattern, steps, pitfalls, verification, source, status, tags, suggested_tools, created_at, updated_at, retrieval_count)
           VALUES (?, ?, ?, ?, 'starter', 'live', ?, ?, ?, ?, 0)""",
        (trigger, steps, pitfalls, verification, tags, suggested_tools, now, now),
    )
    logger.info(f"Migration 000.006.121: Inserted starter procedure for search_available_tools (ID: {cursor.lastrowid}).")


def migrate_000_006_124_context_entries_provenance_and_audit_lineage(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: object,
) -> None:
    """Migration 000.006.124: Add merged_into_id, last_audited_at, split_from_id to context_entries."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(context_entries)")
    cols = [r[1] for r in cursor.fetchall()]
    if "merged_into_id" not in cols:
        cursor.execute("ALTER TABLE context_entries ADD COLUMN merged_into_id INTEGER DEFAULT NULL;")
    if "last_audited_at" not in cols:
        cursor.execute("ALTER TABLE context_entries ADD COLUMN last_audited_at REAL DEFAULT NULL;")
    if "split_from_id" not in cols:
        cursor.execute("ALTER TABLE context_entries ADD COLUMN split_from_id INTEGER DEFAULT NULL;")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ce_audit ON context_entries(status, last_audited_at);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ce_merged_into ON context_entries(merged_into_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ce_split_from ON context_entries(split_from_id);")
    logger.info("Migration 000.006.124: Added merged_into_id, last_audited_at, split_from_id columns and indexes to context_entries.")


def migrate_000_006_136_semantic_tag_column(
    conn: sqlite3.Connection,
    db_map: dict[str, str],
    cfg_obj: object,
) -> None:
    """Migration 000.006.136: Add last_semantic_tag_audit column and index to vault_documents."""
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(vault_documents)")
    existing_cols = {row[1] for row in cur.fetchall()}
    if "last_semantic_tag_audit" not in existing_cols:
        cur.execute("ALTER TABLE vault_documents ADD COLUMN last_semantic_tag_audit REAL DEFAULT 0;")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_vault_docs_semantic_tag ON vault_documents(last_semantic_tag_audit);")
    logger.info("Migration 000.006.136: Added last_semantic_tag_audit column and index to vault_documents.")


# ============================================================================
# Tag Format Unification (vault-tag-taxonomy.md §5 / §9 step 1)
# ============================================================================

def _sweep_tag_csv(raw: str | None) -> tuple[str, bool]:
    """Normalize a comma-separated tag string, de-duplicating collisions.

    Args:
        raw: Comma-separated tag string, possibly None or empty.

    Returns:
        tuple[str, bool]: (normalized CSV, whether it differs from the input).
    """
    from Evelyn.tools.tag_librarian import normalize_tag_format

    if not raw:
        return "", False
    current = [t.strip() for t in raw.split(",") if t.strip()]
    swept: list[str] = []
    for tag in current:
        norm = normalize_tag_format(tag)
        if norm and norm not in swept:
            swept.append(norm)
    return ", ".join(swept), swept != current


def migrate_000_006_147_tag_format_unification_memory(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.147: Apply the §5 tag format to memory fact and procedure tags.

    Runs before the vault sweep deliberately: the memory database is a single-file
    restore, so it validates the rewritten normalizer against ~12.5k real rows
    before any irreplaceable vault document is touched.
    """
    cursor = conn.cursor()
    for table in ("context_entries", "procedures"):
        try:
            rows = cursor.execute(
                f"SELECT rowid, tags FROM {table} WHERE tags IS NOT NULL AND tags != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            logger.warning("[MIGRATION 147] Table %s absent; skipping.", table)
            continue

        updated = 0
        for row_id, raw in rows:
            swept, changed = _sweep_tag_csv(raw)
            if changed:
                cursor.execute(
                    f"UPDATE {table} SET tags = ? WHERE rowid = ?", (swept, row_id)
                )
                updated += 1
        logger.info("[MIGRATION 147] %s: normalized %d/%d tagged rows.", table, updated, len(rows))


def _snapshot_vault_markdown(vault_root: str, version: str) -> str:
    """Archive every markdown file in the vault before the on-disk tag sweep.

    Only .md files are captured — attachments dominate the vault's size and are
    untouched by the sweep, so including them would cost time without adding safety.

    Args:
        vault_root: Absolute path to the vault root.
        version: Migration version, used in the archive filename.

    Returns:
        str: Absolute path to the created archive.

    Raises:
        MigrationExecutionError: If the vault root is missing or no notes were archived.
    """
    import tarfile

    if not os.path.isdir(vault_root):
        raise MigrationExecutionError(f"Vault root not found, refusing to sweep: {vault_root}")

    ensure_backup_dir()
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    archive_path = os.path.join(BACKUP_DIR, f"vault_markdown_pre_{version}_{timestamp}.tar.gz")

    count = 0
    with tarfile.open(archive_path, "w:gz") as tar:
        for root, _dirs, files in os.walk(vault_root):
            for fname in files:
                if not fname.lower().endswith(".md"):
                    continue
                full = os.path.join(root, fname)
                tar.add(full, arcname=os.path.relpath(full, vault_root))
                count += 1

    if count == 0:
        raise MigrationExecutionError(f"Vault snapshot captured 0 notes from {vault_root}; aborting.")

    logger.info("[MIGRATION 148] Snapshotted %d notes -> %s", count, archive_path)
    return archive_path


def migrate_000_006_148_tag_format_unification_vault(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.148: Apply the §5 tag format across the vault taxonomy, index, and notes.

    Four phases, fail-closed in order:
      1. Snapshot every markdown note (independent recovery path).
      2. Rewrite note frontmatter on disk, recording a per-file reversal manifest.
      3. Normalize master_tag_taxonomy, merging usage counts across collision classes.
      4. Reconcile the taxonomy against the swept on-disk state so later clustering
         reads a complete vocabulary (§6.1).
    """
    import json

    from Evelyn.tools.frontmatter_utils import update_frontmatter_field, write_file_with_frontmatter
    from Evelyn.tools.tag_librarian import normalize_tag_format

    vault_root = getattr(cfg, "VAULT_BASE_DIR", "")
    archive_path = _snapshot_vault_markdown(vault_root, "000.006.148")

    cursor = conn.cursor()

    # --- Phase 2: on-disk frontmatter + vault_documents.tags -----------------
    manifest: dict[str, dict[str, list[str]]] = {}
    rows = cursor.execute("SELECT path, tags FROM vault_documents").fetchall()
    rewritten = 0

    for rel_path, raw_tags in rows:
        abs_path = rel_path if os.path.isabs(rel_path) else os.path.join(vault_root, rel_path)
        if not os.path.exists(abs_path):
            continue
        try:
            with open(abs_path, encoding="utf-8") as fh:
                content = fh.read()
        except OSError as exc:
            logger.warning("[MIGRATION 148] Unreadable, skipped: %s (%s)", rel_path, exc)
            continue

        before, _body = _parse_note_tags(content)
        if not before:
            continue
        after: list[str] = []
        for tag in before:
            norm = normalize_tag_format(tag)
            if norm and norm not in after:
                after.append(norm)

        if after != before:
            try:
                write_file_with_frontmatter(
                    abs_path, update_frontmatter_field(content, "tags", after), preserve_mtime=True
                )
            except OSError as exc:
                logger.warning("[MIGRATION 148] Write failed, skipped: %s (%s)", rel_path, exc)
                continue
            manifest[rel_path] = {"before": before, "after": after}
            rewritten += 1

        swept_csv = ", ".join(after)
        if swept_csv != (raw_tags or ""):
            cursor.execute("UPDATE vault_documents SET tags = ? WHERE path = ?", (swept_csv, rel_path))

    ensure_backup_dir()
    manifest_path = os.path.join(BACKUP_DIR, "tag_sweep_manifest_000.006.148.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"archive": archive_path, "documents": manifest}, fh, indent=2)
    logger.info("[MIGRATION 148] Rewrote %d notes; manifest -> %s", rewritten, manifest_path)

    # --- Phase 3: master_tag_taxonomy, merging collision classes -------------
    masters = cursor.execute(
        "SELECT tag, category, description, usage_count FROM master_tag_taxonomy"
    ).fetchall()
    merged: dict[str, dict[str, Any]] = {}
    for tag, category, description, usage in masters:
        norm = normalize_tag_format(tag)
        if not norm:
            continue
        entry = merged.setdefault(
            norm, {"category": "", "description": "", "usage_count": 0}
        )
        entry["usage_count"] += usage or 0
        # Keep the richest metadata across the merged terms.
        if category and not entry["category"]:
            entry["category"] = normalize_tag_format(category) or category
        if description and len(description) > len(entry["description"]):
            entry["description"] = description

    cursor.execute("DELETE FROM master_tag_taxonomy")
    now = time.time()
    for tag, meta in merged.items():
        cursor.execute(
            """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tag, meta["category"] or (tag.split("/")[0] if "/" in tag else "general"),
             meta["description"], meta["usage_count"], now, now),
        )
    logger.info("[MIGRATION 148] Taxonomy: %d terms -> %d after merge.", len(masters), len(merged))

    # --- Phase 4: reconcile taxonomy against swept on-disk reality -----------
    observed: dict[str, int] = {}
    for (doc_tags,) in cursor.execute(
        "SELECT tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall():
        for tag in (t.strip() for t in doc_tags.split(",") if t.strip()):
            observed[tag] = observed.get(tag, 0) + 1

    added = 0
    for tag, count in observed.items():
        if tag in merged:
            cursor.execute(
                "UPDATE master_tag_taxonomy SET usage_count = ?, updated_at = ? WHERE tag = ?",
                (count, now, tag),
            )
        else:
            cursor.execute(
                """INSERT OR IGNORE INTO master_tag_taxonomy
                   (tag, category, description, usage_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (tag, tag.split("/")[0] if "/" in tag else "general",
                 f"Obsidian notes tagged under {tag}", count, now, now),
            )
            added += 1
    logger.info("[MIGRATION 148] Reconciled: %d terms added from disk.", added)


def _parse_note_tags(content: str) -> tuple[list[str], str]:
    """Extract frontmatter tags from note content without importing the librarian.

    Args:
        content: Raw markdown note text.

    Returns:
        tuple[list[str], str]: (tag list, body text).
    """
    from Evelyn.tools.frontmatter_utils import parse_frontmatter

    meta, body = parse_frontmatter(content)
    raw = meta.get("tags", [])
    if isinstance(raw, str):
        tags = [t.strip().strip("'\"#") for t in raw.split(",")]
    elif isinstance(raw, (list, set, tuple)):
        tags = [str(t).strip().strip("'\"#") for t in raw]
    else:
        tags = []
    return [t for t in tags if t], body


# --- §9 step 2: vault namespace retirement -----------------------------------
# Deterministic namespace moves. Named places under location/ are entities and are
# deliberately NOT touched here — they belong to step 3 (entity extraction).
_STEP2_RETIRED_PREFIXES = ("relationship/",)
_STEP2_CONTACT_PREFIX = "contact/"
_STEP2_GRAPH_CONTACT = "obsidian-graph/contact"
_STEP2_BIOME_FROM = "location/biome/"
_STEP2_BIOME_TO = "setting/biome/"
_STEP2_WRAPPER_PREFIX = "topic/"


def _step2_transform_tag(tag: str) -> str | None:
    """Map one tag through the step-2 namespace rules.

    Args:
        tag: A single normalized tag.

    Returns:
        str | None: The replacement tag, or None if the tag is retired outright.
    """
    if tag.startswith(_STEP2_RETIRED_PREFIXES):
        return None
    if tag.startswith(_STEP2_CONTACT_PREFIX):
        return _STEP2_GRAPH_CONTACT  # Roles collapse; the flag is what carries meaning.
    if tag.startswith(_STEP2_BIOME_FROM):
        return _STEP2_BIOME_TO + tag[len(_STEP2_BIOME_FROM):]
    if tag.startswith(_STEP2_WRAPPER_PREFIX):
        return tag[len(_STEP2_WRAPPER_PREFIX):] or None  # Domains are bare-rooted (§3.3).
    return tag


def _step2_transform_tags(tags: list[str]) -> list[str]:
    """Apply the step-2 rules to a tag list, preserving order and de-duplicating."""
    out: list[str] = []
    for tag in tags:
        mapped = _step2_transform_tag(tag)
        if mapped and mapped not in out:
            out.append(mapped)
    return out


def migrate_000_006_149_namespace_retirement_vault(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.149: Retire and relocate vault tag namespaces (§9 step 2).

    Vault scope only. 'relationship/*' is retired here but deliberately left intact in
    the memory database, where it is a live namespace carrying 903 rows — 666 of which
    have no other tag. Memory retirement is gated on re-tagging those rows and is
    registered as §9 step 8.
    """
    import json

    from Evelyn.tools.frontmatter_utils import update_frontmatter_field, write_file_with_frontmatter

    vault_root = getattr(cfg, "VAULT_BASE_DIR", "")
    archive_path = _snapshot_vault_markdown(vault_root, "000.006.149")
    cursor = conn.cursor()

    # --- Notes on disk + vault_documents.tags --------------------------------
    manifest: dict[str, dict[str, list[str]]] = {}
    rewritten = 0
    for rel_path, raw_tags in cursor.execute("SELECT path, tags FROM vault_documents").fetchall():
        abs_path = rel_path if os.path.isabs(rel_path) else os.path.join(vault_root, rel_path)
        if not os.path.exists(abs_path):
            continue
        try:
            with open(abs_path, encoding="utf-8") as fh:
                content = fh.read()
        except OSError as exc:
            logger.warning("[MIGRATION 149] Unreadable, skipped: %s (%s)", rel_path, exc)
            continue

        before, _body = _parse_note_tags(content)
        if not before:
            continue
        after = _step2_transform_tags(before)

        if after != before:
            try:
                write_file_with_frontmatter(
                    abs_path, update_frontmatter_field(content, "tags", after), preserve_mtime=True
                )
            except OSError as exc:
                logger.warning("[MIGRATION 149] Write failed, skipped: %s (%s)", rel_path, exc)
                continue
            manifest[rel_path] = {"before": before, "after": after}
            rewritten += 1

        swept_csv = ", ".join(after)
        if swept_csv != (raw_tags or ""):
            cursor.execute("UPDATE vault_documents SET tags = ? WHERE path = ?", (swept_csv, rel_path))

    ensure_backup_dir()
    manifest_path = os.path.join(BACKUP_DIR, "namespace_retirement_manifest_000.006.149.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"archive": archive_path, "documents": manifest}, fh, indent=2)
    logger.info("[MIGRATION 149] Rewrote %d notes; manifest -> %s", rewritten, manifest_path)

    # --- master_tag_taxonomy, merging terms that collide after the move ------
    masters = cursor.execute(
        "SELECT tag, category, description, usage_count FROM master_tag_taxonomy"
    ).fetchall()
    merged: dict[str, dict[str, Any]] = {}
    retired = 0
    for tag, category, description, usage in masters:
        mapped = _step2_transform_tag(tag)
        if not mapped:
            retired += 1
            continue
        entry = merged.setdefault(mapped, {"category": "", "description": "", "usage_count": 0})
        entry["usage_count"] += usage or 0
        if category and not entry["category"]:
            entry["category"] = category
        if description and len(description) > len(entry["description"]):
            entry["description"] = description

    cursor.execute("DELETE FROM master_tag_taxonomy")
    now = time.time()
    for tag, meta in merged.items():
        cursor.execute(
            """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tag, meta["category"] or (tag.split("/")[0] if "/" in tag else "general"),
             meta["description"], meta["usage_count"], now, now),
        )

    # --- Recount usage against the transformed on-disk state -----------------
    observed: dict[str, int] = {}
    for (doc_tags,) in cursor.execute(
        "SELECT tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall():
        for tag in (t.strip() for t in doc_tags.split(",") if t.strip()):
            observed[tag] = observed.get(tag, 0) + 1
    for tag, count in observed.items():
        cursor.execute(
            "UPDATE master_tag_taxonomy SET usage_count = ?, updated_at = ? WHERE tag = ?",
            (count, now, tag),
        )

    logger.info(
        "[MIGRATION 149] Taxonomy: %d terms -> %d (%d retired outright).",
        len(masters), len(merged), retired,
    )


# --- §9 step 4: entity extraction ---------------------------------------------

def migrate_000_006_151_drop_subject_duplicate_tags(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.151: Drop memory tags that merely restate their own row's subject.

    The `subject` column is the single source of truth for who a fact concerns. A tag
    repeating it stores the same fact twice, which is how one identity ended up in the
    vocabulary in several spellings — a column holds one value, a free-text tag does not.

    The rule is deliberately relational, not a name list: a tag is dropped only when it
    equals *that row's own* subject. A fact about one party tagged with another party's
    name is a genuine cross-reference the subject column cannot express, and is preserved.
    """
    from Evelyn.tools.tag_librarian import strip_subject_duplicate_tags

    cursor = conn.cursor()
    rows = cursor.execute(
        "SELECT rowid, subject, tags FROM context_entries "
        "WHERE tags IS NOT NULL AND tags != '' AND subject IS NOT NULL"
    ).fetchall()

    updated = 0
    for row_id, subject, raw_tags in rows:
        current = [t.strip() for t in raw_tags.split(",") if t.strip()]
        kept = strip_subject_duplicate_tags(current, subject)
        if len(kept) != len(current):
            cursor.execute(
                "UPDATE context_entries SET tags = ? WHERE rowid = ?", (", ".join(kept), row_id)
            )
            updated += 1
    logger.info("[MIGRATION 151] Dropped subject-duplicate tags on %d rows.", updated)


def _authority_record_index(cursor: sqlite3.Cursor) -> dict[str, tuple[str, str]]:
    """Map normalized title/alias forms to the note that acts as their authority record.

    Args:
        cursor: Open cursor on the vault database.

    Returns:
        dict[str, tuple[str, str]]: normalized form -> (note path, original display text).
    """
    from Evelyn.tools.tag_librarian import normalize_tag_format

    forms: dict[str, tuple[str, str]] = {}
    for path, title, aliases in cursor.execute(
        "SELECT path, title, aliases FROM vault_documents"
    ).fetchall():
        for raw in [title, *(aliases or "").split(",")]:
            if not raw or not raw.strip():
                continue
            form = normalize_tag_format(raw.strip())
            if form and form not in forms:
                forms[form] = (path, raw.strip())
    return forms


def migrate_000_006_152_drop_redundant_entity_tags(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.152: Remove entity tags already carried by the link graph (§2).

    A named work or person is a link, not a tag — the entity's own note is the authority
    record. Where a tag names such an entity AND every note carrying it already sits in
    that entity's folder or references it by name, the tag stores nothing the link graph
    does not already hold.

    The redundancy test is recomputed here rather than applied from a list, for two
    reasons: it keeps the migration a generic pattern sweep, and several qualifying
    entities are personal contacts whose names must not be committed to a tracked file
    (AGENTS.md §4). A tag is removed ONLY at 100% coverage — anything less means the tag
    is carrying a connection the link graph does not, and it is left alone. That threshold
    is what separates genuine entities from concept words that merely share a name with a
    note, which measure near 0%.
    """
    import json

    from Evelyn.tools.frontmatter_utils import update_frontmatter_field, write_file_with_frontmatter
    from Evelyn.tools.tag_librarian import delete_tag_from_chroma

    vault_root = getattr(cfg, "VAULT_BASE_DIR", "")
    archive_path = _snapshot_vault_markdown(vault_root, "000.006.152")
    cursor = conn.cursor()

    forms = _authority_record_index(cursor)
    doc_tags = {
        path: [t.strip() for t in (tags or "").split(",") if t.strip()]
        for path, tags in cursor.execute(
            "SELECT path, tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
        ).fetchall()
    }

    # Identify tags whose every carrier already references the authority record.
    redundant: dict[str, str] = {}
    for tag, (auth_path, auth_title) in forms.items():
        carriers = [p for p, tags in doc_tags.items() if tag in tags]
        if not carriers:
            continue
        auth_folder = os.path.dirname(auth_path)
        auth_stem = os.path.splitext(os.path.basename(auth_path))[0]
        covered = 0
        for path in carriers:
            if auth_folder and path.startswith(auth_folder + "/"):
                covered += 1
                continue
            abs_path = path if os.path.isabs(path) else os.path.join(vault_root, path)
            try:
                with open(abs_path, encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
            except OSError:
                continue
            if auth_title in body or auth_stem in body:
                covered += 1
        if covered == len(carriers):
            redundant[tag] = auth_path

    logger.info("[MIGRATION 152] %d entity tags are fully carried by the link graph.", len(redundant))

    # Strip them from notes on disk and from the index.
    manifest: dict[str, dict[str, list[str]]] = {}
    rewritten = 0
    for path, tags in doc_tags.items():
        kept = [t for t in tags if t not in redundant]
        if kept == tags:
            continue
        abs_path = path if os.path.isabs(path) else os.path.join(vault_root, path)
        if not os.path.exists(abs_path):
            continue
        try:
            with open(abs_path, encoding="utf-8") as fh:
                content = fh.read()
            write_file_with_frontmatter(
                abs_path, update_frontmatter_field(content, "tags", kept), preserve_mtime=True
            )
        except OSError as exc:
            logger.warning("[MIGRATION 152] Write failed, skipped: %s (%s)", path, exc)
            continue
        cursor.execute("UPDATE vault_documents SET tags = ? WHERE path = ?", (", ".join(kept), path))
        manifest[path] = {"before": tags, "after": kept}
        rewritten += 1

    for tag in redundant:
        cursor.execute("DELETE FROM master_tag_taxonomy WHERE tag = ?", (tag,))
        delete_tag_from_chroma(tag)

    ensure_backup_dir()
    manifest_path = os.path.join(BACKUP_DIR, "entity_tag_manifest_000.006.152.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"archive": archive_path, "documents": manifest}, fh, indent=2)
    logger.info("[MIGRATION 152] Rewrote %d notes; manifest -> %s", rewritten, manifest_path)


# --- §9 step 5: equivalence collapse ------------------------------------------

MIGRATE_000_006_153_TAG_ALIASES_SQL = """
CREATE TABLE IF NOT EXISTS master_tag_aliases (
    alias       TEXT PRIMARY KEY,
    canonical   TEXT NOT NULL,
    tier        TEXT,
    created_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_tag_aliases_canonical ON master_tag_aliases(canonical);
"""


def _apply_alias_map_to_tag_csv(raw: str | None, alias_map: dict[str, str]) -> tuple[str, bool]:
    """Rewrite a comma-separated tag string through an alias map, de-duplicating.

    Args:
        raw: Comma-separated tags.
        alias_map: variant -> canonical.

    Returns:
        tuple[str, bool]: (rewritten CSV, whether it changed).
    """
    if not raw:
        return "", False
    current = [t.strip() for t in raw.split(",") if t.strip()]
    out: list[str] = []
    for tag in current:
        mapped = alias_map.get(tag, tag)
        if mapped and mapped not in out:
            out.append(mapped)
    return ", ".join(out), out != current


def migrate_000_006_154_collapse_lexical_synonyms_vault(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.154: Collapse lexically-equivalent terms in the vault (§6.2).

    Applies only the tiers that need no editorial judgement: terms identical once
    separators are ignored at equal hierarchy depth, terms whose deeper form is also the
    more used, and singular/plural pairs at equal depth. Groups where a rare deep variant
    competes with a dominant flat one are **deferred to review** — auto-resolving those
    would let a 1-use term rename a 94-use term.

    Every collapse is recorded in `master_tag_aliases`. A deleted synonym with no alias
    record is re-minted by the next import; the alias is what makes the collapse stick.
    """
    import json

    from Evelyn.tools import taxonomy_db
    from Evelyn.tools.frontmatter_utils import update_frontmatter_field, write_file_with_frontmatter
    from Evelyn.tools.tag_librarian import delete_tag_from_chroma
    from Evelyn.tools.tag_synonym import build_corpus, lexical_equivalences

    vault_root = getattr(cfg, "VAULT_BASE_DIR", "")
    archive_path = _snapshot_vault_markdown(vault_root, "000.006.154")
    cursor = conn.cursor()

    counts = build_corpus()
    result = lexical_equivalences(counts)
    alias_map = {variant: canonical for canonical, variant in result["auto"]}
    logger.info(
        "[MIGRATION 154] %d equivalences to apply, %d groups deferred to review.",
        len(alias_map), len(result["deferred"]),
    )

    now = time.time()
    for variant, canonical in alias_map.items():
        cursor.execute(
            """INSERT INTO master_tag_aliases (alias, canonical, tier, created_at)
               VALUES (?, ?, 'lexical', ?)
               ON CONFLICT(alias) DO UPDATE SET canonical = excluded.canonical""",
            (variant, canonical, now),
        )
    # The in-process alias cache predates these rows; drop it so consumers see them.
    taxonomy_db.invalidate_alias_cache()

    # Notes on disk + the vault index.
    manifest: dict[str, dict[str, list[str]]] = {}
    rewritten = 0
    for path, raw_tags in cursor.execute(
        "SELECT path, tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall():
        swept, changed = _apply_alias_map_to_tag_csv(raw_tags, alias_map)
        if not changed:
            continue
        abs_path = path if os.path.isabs(path) else os.path.join(vault_root, path)
        after = [t.strip() for t in swept.split(",") if t.strip()]
        if os.path.exists(abs_path):
            try:
                with open(abs_path, encoding="utf-8") as fh:
                    content = fh.read()
                write_file_with_frontmatter(
                    abs_path, update_frontmatter_field(content, "tags", after), preserve_mtime=True
                )
                manifest[path] = {"before": [t.strip() for t in raw_tags.split(",")], "after": after}
                rewritten += 1
            except OSError as exc:
                logger.warning("[MIGRATION 154] Write failed, skipped: %s (%s)", path, exc)
                continue
        cursor.execute("UPDATE vault_documents SET tags = ? WHERE path = ?", (swept, path))

    # Registry: fold retired variants into their canonical, summing usage.
    retired = 0
    for variant, canonical in alias_map.items():
        row = cursor.execute(
            "SELECT usage_count FROM master_tag_taxonomy WHERE tag = ?", (variant,)
        ).fetchone()
        if not row:
            continue
        cursor.execute(
            """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
               VALUES (?, ?, '', ?, ?, ?)
               ON CONFLICT(tag) DO UPDATE SET usage_count = master_tag_taxonomy.usage_count + excluded.usage_count,
                                              updated_at = excluded.updated_at""",
            (canonical, canonical.split("/")[0] if "/" in canonical else "general",
             row[0] or 0, now, now),
        )
        cursor.execute("DELETE FROM master_tag_taxonomy WHERE tag = ?", (variant,))
        delete_tag_from_chroma(variant)
        retired += 1

    ensure_backup_dir()
    manifest_path = os.path.join(BACKUP_DIR, "synonym_collapse_manifest_000.006.154.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"archive": archive_path, "alias_map": alias_map, "documents": manifest}, fh, indent=2)
    logger.info(
        "[MIGRATION 154] Rewrote %d notes, retired %d registry terms; manifest -> %s",
        rewritten, retired, manifest_path,
    )


def migrate_000_006_155_collapse_lexical_synonyms_memory(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.155: Apply the recorded equivalences to memory tags.

    Reads the alias map from `master_tag_aliases` rather than recomputing it. The vault
    migration has already changed the corpus, so a fresh computation here would derive a
    different mapping — the alias table is the record of what was actually decided.
    """
    vault_path = db_paths.get("vault", "")
    if not vault_path or not os.path.exists(vault_path):
        raise MigrationExecutionError("Vault database unavailable; cannot read alias map.")

    vcon = sqlite3.connect(vault_path, timeout=30.0)
    try:
        alias_map = dict(vcon.execute("SELECT alias, canonical FROM master_tag_aliases"))
    finally:
        vcon.close()
    if not alias_map:
        logger.info("[MIGRATION 155] No aliases recorded; nothing to apply.")
        return

    cursor = conn.cursor()
    total = 0
    for table in ("context_entries", "procedures"):
        try:
            rows = cursor.execute(
                f"SELECT rowid, tags FROM {table} WHERE tags IS NOT NULL AND tags != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        updated = 0
        for row_id, raw in rows:
            swept, changed = _apply_alias_map_to_tag_csv(raw, alias_map)
            if changed:
                cursor.execute(f"UPDATE {table} SET tags = ? WHERE rowid = ?", (swept, row_id))
                updated += 1
        logger.info("[MIGRATION 155] %s: %d rows rewritten.", table, updated)
        total += updated
    logger.info("[MIGRATION 155] Applied %d aliases across %d memory rows.", len(alias_map), total)


# --- §9 step 5 (reviewed half): apply curated equivalences -----------------------

REVIEWED_DECISIONS_PATH = os.path.join(cfg.BASE_DIR, "scratch", "tag_merge_decisions.json")


def _load_reviewed_alias_map() -> dict[str, str]:
    """Load the reviewed merge decisions produced by the review tooling.

    These are curated decisions about this specific corpus, not a generic rule, so they
    live in a local artifact rather than in the repository — the vocabulary contains
    personal terms that must not reach a tracked file (AGENTS.md §4). A missing file means
    the curation has not been performed in this environment; the migration then no-ops
    loudly rather than silently half-applying.

    Returns:
        dict[str, str]: variant -> canonical.
    """
    import json

    if not os.path.exists(REVIEWED_DECISIONS_PATH):
        logger.warning(
            "[MIGRATION] No reviewed decisions at %s — skipping curated merges. "
            "Regenerate with scripts/generate_tag_merge_review.py --export-decisions",
            REVIEWED_DECISIONS_PATH,
        )
        return {}
    with open(REVIEWED_DECISIONS_PATH, encoding="utf-8") as fh:
        payload = json.load(fh)
    return {k: v for k, v in (payload.get("alias_map") or {}).items() if k and v and k != v}


def migrate_000_006_161_apply_reviewed_merges_vault(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.161: Apply the reviewed UF merges across the vault (§9 step 5).

    Two kinds of decision arrive together. Flat-vs-nested pairs were settled by the sibling
    test (§6.3.1) — a hierarchy level must have siblings, so `work-stress` nests under a
    parent with 409 children while `me-cfs` stays compound because `me` is not a category.
    Semantic merges were reviewed by hand. Both are recorded as `UF` aliases.
    """
    import json

    from Evelyn.tools import taxonomy_db
    from Evelyn.tools.frontmatter_utils import update_frontmatter_field, write_file_with_frontmatter
    from Evelyn.tools.tag_librarian import delete_tag_from_chroma

    alias_map = _load_reviewed_alias_map()
    if not alias_map:
        return

    vault_root = getattr(cfg, "VAULT_BASE_DIR", "")
    archive_path = _snapshot_vault_markdown(vault_root, "000.006.161")
    cursor = conn.cursor()

    now = time.time()
    for variant, canonical in alias_map.items():
        cursor.execute(
            """INSERT INTO master_tag_aliases (alias, canonical, tier, created_at)
               VALUES (?, ?, 'reviewed', ?)
               ON CONFLICT(alias) DO UPDATE SET canonical = excluded.canonical,
                                                tier = excluded.tier""",
            (variant, canonical, now),
        )
    taxonomy_db.invalidate_alias_cache()

    manifest: dict[str, dict[str, list[str]]] = {}
    rewritten = 0
    for path, raw_tags in cursor.execute(
        "SELECT path, tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall():
        swept, changed = _apply_alias_map_to_tag_csv(raw_tags, alias_map)
        if not changed:
            continue
        after = [t.strip() for t in swept.split(",") if t.strip()]
        abs_path = path if os.path.isabs(path) else os.path.join(vault_root, path)
        if os.path.exists(abs_path):
            try:
                with open(abs_path, encoding="utf-8") as fh:
                    content = fh.read()
                write_file_with_frontmatter(
                    abs_path, update_frontmatter_field(content, "tags", after), preserve_mtime=True
                )
                manifest[path] = {"before": [t.strip() for t in raw_tags.split(",")], "after": after}
                rewritten += 1
            except OSError as exc:
                logger.warning("[MIGRATION 161] Write failed, skipped: %s (%s)", path, exc)
                continue
        cursor.execute("UPDATE vault_documents SET tags = ? WHERE path = ?", (swept, path))

    retired = 0
    for variant, canonical in alias_map.items():
        row = cursor.execute(
            "SELECT usage_count FROM master_tag_taxonomy WHERE tag = ?", (variant,)
        ).fetchone()
        if not row:
            continue
        cursor.execute(
            """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
               VALUES (?, ?, '', ?, ?, ?)
               ON CONFLICT(tag) DO UPDATE SET usage_count = master_tag_taxonomy.usage_count + excluded.usage_count,
                                              updated_at = excluded.updated_at""",
            (canonical, canonical.split("/")[0] if "/" in canonical else "general",
             row[0] or 0, now, now),
        )
        cursor.execute("DELETE FROM master_tag_taxonomy WHERE tag = ?", (variant,))
        delete_tag_from_chroma(variant)
        retired += 1

    ensure_backup_dir()
    manifest_path = os.path.join(BACKUP_DIR, "reviewed_merge_manifest_000.006.161.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"archive": archive_path, "alias_map": alias_map, "documents": manifest}, fh, indent=2)
    logger.info(
        "[MIGRATION 161] %d aliases; rewrote %d notes, retired %d registry terms.",
        len(alias_map), rewritten, retired,
    )


def migrate_000_006_162_apply_reviewed_merges_memory(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.162: Apply the recorded equivalences to memory tags.

    Reads the alias table rather than the decisions file: the vault migration has already
    altered the corpus, and the table is the record of what was actually applied.
    """
    vault_path = db_paths.get("vault", "")
    if not vault_path or not os.path.exists(vault_path):
        raise MigrationExecutionError("Vault database unavailable; cannot read alias map.")

    vcon = sqlite3.connect(vault_path, timeout=30.0)
    try:
        alias_map = dict(
            vcon.execute("SELECT alias, canonical FROM master_tag_aliases WHERE tier = 'reviewed'")
        )
    finally:
        vcon.close()
    if not alias_map:
        logger.info("[MIGRATION 162] No reviewed aliases recorded; nothing to apply.")
        return

    cursor = conn.cursor()
    total = 0
    for table in ("context_entries", "procedures"):
        try:
            rows = cursor.execute(
                f"SELECT rowid, tags FROM {table} WHERE tags IS NOT NULL AND tags != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        updated = 0
        for row_id, raw in rows:
            swept, changed = _apply_alias_map_to_tag_csv(raw, alias_map)
            if changed:
                cursor.execute(f"UPDATE {table} SET tags = ? WHERE rowid = ?", (swept, row_id))
                updated += 1
        logger.info("[MIGRATION 162] %s: %d rows rewritten.", table, updated)
        total += updated
    logger.info("[MIGRATION 162] Applied %d reviewed aliases across %d memory rows.",
                len(alias_map), total)


# --- §9 step 6a: root consolidation -------------------------------------------

def build_root_consolidation_map() -> dict[str, str]:
    """Build the term rewrite map for root consolidation (§9 step 6).

    Two deterministic passes, and the order is load-bearing: inflection merges run first,
    because a root that looks weak on its own may clear the threshold once its variants are
    folded in. Flattening first would dismantle a namespace that was about to become real.

    Returns:
        dict[str, str]: old term -> new term.
    """
    import collections

    from Evelyn.tools.tag_synonym import (
        build_corpus,
        literary_warrant,
        root_census,
        root_inflection_merges,
        weak_root_resolution,
    )

    counts = build_corpus()
    root_merges = root_inflection_merges(counts)

    rewrite: dict[str, str] = {}
    merged_counts: collections.Counter = collections.Counter()
    for tag, uses in counts.items():
        new = tag
        if "/" in tag and not tag.startswith("CY-"):
            root, rest = tag.split("/", 1)
            if root in root_merges:
                new = f"{root_merges[root]}/{rest}"
        if new != tag:
            rewrite[tag] = new
        merged_counts[new] += uses

    # Literary warrant (§6.3.3): a root tagged once but written 800 times is a category
    # whose material is simply unclassified. Population alone would delete it.
    sparse_roots = [r for r, stats in root_census(merged_counts).items() if stats["terms"] < 2]
    warrant = literary_warrant(
        sparse_roots,
        getattr(cfg, "VAULT_BASE_DIR", ""),
        entities={getattr(cfg, "USER_NAME", ""), getattr(cfg, "ASSISTANT_NAME", "")},
    )

    for tag, flat in weak_root_resolution(
        merged_counts, warrant=warrant, head_aliases=root_merges
    ).items():
        # Chain through the inflection rewrite so the original term maps to its final form.
        origin = next((o for o, n in rewrite.items() if n == tag), tag)
        rewrite[origin] = flat

    return {old: new for old, new in rewrite.items() if old != new}


def migrate_000_006_166_root_consolidation_vault(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.166: Consolidate tag roots across the vault (§9 step 6a).

    Step 5 merged whole terms, which could not reach this: `preference/food` and
    `preferences/drink` share no lexical pair, yet their roots are one concept. Root-level
    consolidation rewrites everything beneath them.

    Roots that are inflections of one word fold to the singular (§6.3.2), which sometimes
    means a smaller root absorbs a larger one. Roots left with fewer than two children are
    then flattened back to compound terms, since a namespace with one occupant is a
    compound wearing a slash.
    """
    import json

    from Evelyn.tools import taxonomy_db
    from Evelyn.tools.frontmatter_utils import update_frontmatter_field, write_file_with_frontmatter
    from Evelyn.tools.tag_librarian import delete_tag_from_chroma

    rewrite = build_root_consolidation_map()
    if not rewrite:
        logger.info("[MIGRATION 166] Nothing to consolidate.")
        return

    vault_root = getattr(cfg, "VAULT_BASE_DIR", "")
    archive_path = _snapshot_vault_markdown(vault_root, "000.006.166")
    cursor = conn.cursor()

    now = time.time()
    for variant, canonical in rewrite.items():
        cursor.execute(
            """INSERT INTO master_tag_aliases (alias, canonical, tier, created_at)
               VALUES (?, ?, 'root', ?)
               ON CONFLICT(alias) DO UPDATE SET canonical = excluded.canonical,
                                                tier = excluded.tier""",
            (variant, canonical, now),
        )
    taxonomy_db.invalidate_alias_cache()

    manifest: dict[str, dict[str, list[str]]] = {}
    rewritten = 0
    for path, raw_tags in cursor.execute(
        "SELECT path, tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall():
        swept, changed = _apply_alias_map_to_tag_csv(raw_tags, rewrite)
        if not changed:
            continue
        after = [t.strip() for t in swept.split(",") if t.strip()]
        abs_path = path if os.path.isabs(path) else os.path.join(vault_root, path)
        if os.path.exists(abs_path):
            try:
                with open(abs_path, encoding="utf-8") as fh:
                    content = fh.read()
                write_file_with_frontmatter(
                    abs_path, update_frontmatter_field(content, "tags", after), preserve_mtime=True
                )
                manifest[path] = {"before": [t.strip() for t in raw_tags.split(",")], "after": after}
                rewritten += 1
            except OSError as exc:
                logger.warning("[MIGRATION 166] Write failed, skipped: %s (%s)", path, exc)
                continue
        cursor.execute("UPDATE vault_documents SET tags = ? WHERE path = ?", (swept, path))

    retired = 0
    for variant, canonical in rewrite.items():
        row = cursor.execute(
            "SELECT usage_count FROM master_tag_taxonomy WHERE tag = ?", (variant,)
        ).fetchone()
        if not row:
            continue
        cursor.execute(
            """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
               VALUES (?, ?, '', ?, ?, ?)
               ON CONFLICT(tag) DO UPDATE SET usage_count = master_tag_taxonomy.usage_count + excluded.usage_count,
                                              updated_at = excluded.updated_at""",
            (canonical, canonical.split("/")[0] if "/" in canonical else "general",
             row[0] or 0, now, now),
        )
        cursor.execute("DELETE FROM master_tag_taxonomy WHERE tag = ?", (variant,))
        delete_tag_from_chroma(variant)
        retired += 1

    ensure_backup_dir()
    manifest_path = os.path.join(BACKUP_DIR, "root_consolidation_manifest_000.006.166.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"archive": archive_path, "rewrite": rewrite, "documents": manifest}, fh, indent=2)
    logger.info(
        "[MIGRATION 166] %d rewrites; %d notes, %d registry terms retired.",
        len(rewrite), rewritten, retired,
    )


def migrate_000_006_167_root_consolidation_memory(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.167: Apply the recorded root consolidation to memory tags."""
    vault_path = db_paths.get("vault", "")
    if not vault_path or not os.path.exists(vault_path):
        raise MigrationExecutionError("Vault database unavailable; cannot read alias map.")

    vcon = sqlite3.connect(vault_path, timeout=30.0)
    try:
        rewrite = dict(
            vcon.execute("SELECT alias, canonical FROM master_tag_aliases WHERE tier = 'root'")
        )
    finally:
        vcon.close()
    if not rewrite:
        logger.info("[MIGRATION 167] No root aliases recorded; nothing to apply.")
        return

    cursor = conn.cursor()
    total = 0
    for table in ("context_entries", "procedures"):
        try:
            rows = cursor.execute(
                f"SELECT rowid, tags FROM {table} WHERE tags IS NOT NULL AND tags != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        updated = 0
        for row_id, raw in rows:
            swept, changed = _apply_alias_map_to_tag_csv(raw, rewrite)
            if changed:
                cursor.execute(f"UPDATE {table} SET tags = ? WHERE rowid = ?", (swept, row_id))
                updated += 1
        logger.info("[MIGRATION 167] %s: %d rows rewritten.", table, updated)
        total += updated
    logger.info("[MIGRATION 167] Applied %d root rewrites across %d memory rows.",
                len(rewrite), total)


# --- §9 step 6b: flat tail adoption -------------------------------------------

def migrate_000_006_168_adopt_flat_compounds_vault(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.168: Nest flat compounds under established levels (§9 step 6b).

    The sibling test (§6.3.1) applied to terms with no nested twin. `productivity-tips` is
    flat only because nothing nested it; `productivity` demonstrably holds terms, so the
    hyphen was always a missed slash. Heads that name nothing are left alone — the bar is
    evidence, not plausibility.
    """
    import json

    from Evelyn.tools import taxonomy_db
    from Evelyn.tools.frontmatter_utils import update_frontmatter_field, write_file_with_frontmatter
    from Evelyn.tools.tag_librarian import delete_tag_from_chroma
    from Evelyn.tools.tag_synonym import adopt_flat_compounds, build_corpus

    rewrite = adopt_flat_compounds(build_corpus())
    if not rewrite:
        logger.info("[MIGRATION 168] Nothing to adopt.")
        return

    vault_root = getattr(cfg, "VAULT_BASE_DIR", "")
    archive_path = _snapshot_vault_markdown(vault_root, "000.006.168")
    cursor = conn.cursor()

    now = time.time()
    for variant, canonical in rewrite.items():
        cursor.execute(
            """INSERT INTO master_tag_aliases (alias, canonical, tier, created_at)
               VALUES (?, ?, 'adopted', ?)
               ON CONFLICT(alias) DO UPDATE SET canonical = excluded.canonical,
                                                tier = excluded.tier""",
            (variant, canonical, now),
        )
    taxonomy_db.invalidate_alias_cache()

    manifest: dict[str, dict[str, list[str]]] = {}
    rewritten = 0
    for path, raw_tags in cursor.execute(
        "SELECT path, tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall():
        swept, changed = _apply_alias_map_to_tag_csv(raw_tags, rewrite)
        if not changed:
            continue
        after = [t.strip() for t in swept.split(",") if t.strip()]
        abs_path = path if os.path.isabs(path) else os.path.join(vault_root, path)
        if os.path.exists(abs_path):
            try:
                with open(abs_path, encoding="utf-8") as fh:
                    content = fh.read()
                write_file_with_frontmatter(
                    abs_path, update_frontmatter_field(content, "tags", after), preserve_mtime=True
                )
                manifest[path] = {"before": [t.strip() for t in raw_tags.split(",")], "after": after}
                rewritten += 1
            except OSError as exc:
                logger.warning("[MIGRATION 168] Write failed, skipped: %s (%s)", path, exc)
                continue
        cursor.execute("UPDATE vault_documents SET tags = ? WHERE path = ?", (swept, path))

    retired = 0
    for variant, canonical in rewrite.items():
        row = cursor.execute(
            "SELECT usage_count FROM master_tag_taxonomy WHERE tag = ?", (variant,)
        ).fetchone()
        if not row:
            continue
        cursor.execute(
            """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
               VALUES (?, ?, '', ?, ?, ?)
               ON CONFLICT(tag) DO UPDATE SET usage_count = master_tag_taxonomy.usage_count + excluded.usage_count,
                                              updated_at = excluded.updated_at""",
            (canonical, canonical.split("/")[0], row[0] or 0, now, now),
        )
        cursor.execute("DELETE FROM master_tag_taxonomy WHERE tag = ?", (variant,))
        delete_tag_from_chroma(variant)
        retired += 1

    ensure_backup_dir()
    manifest_path = os.path.join(BACKUP_DIR, "flat_adoption_manifest_000.006.168.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"archive": archive_path, "rewrite": rewrite, "documents": manifest}, fh, indent=2)
    logger.info("[MIGRATION 168] Adopted %d terms; %d notes, %d registry terms retired.",
                len(rewrite), rewritten, retired)


def migrate_000_006_169_adopt_flat_compounds_memory(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.169: Apply the recorded adoptions to memory tags."""
    vault_path = db_paths.get("vault", "")
    if not vault_path or not os.path.exists(vault_path):
        raise MigrationExecutionError("Vault database unavailable; cannot read alias map.")
    vcon = sqlite3.connect(vault_path, timeout=30.0)
    try:
        rewrite = dict(
            vcon.execute("SELECT alias, canonical FROM master_tag_aliases WHERE tier = 'adopted'")
        )
    finally:
        vcon.close()
    if not rewrite:
        logger.info("[MIGRATION 169] No adoptions recorded; nothing to apply.")
        return

    cursor = conn.cursor()
    total = 0
    for table in ("context_entries", "procedures"):
        try:
            rows = cursor.execute(
                f"SELECT rowid, tags FROM {table} WHERE tags IS NOT NULL AND tags != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        updated = 0
        for row_id, raw in rows:
            swept, changed = _apply_alias_map_to_tag_csv(raw, rewrite)
            if changed:
                cursor.execute(f"UPDATE {table} SET tags = ? WHERE rowid = ?", (swept, row_id))
                updated += 1
        logger.info("[MIGRATION 169] %s: %d rows rewritten.", table, updated)
        total += updated
    logger.info("[MIGRATION 169] Applied %d adoptions across %d memory rows.", len(rewrite), total)


# --- §9 step 6c: reviewed second-pass merges and removals ----------------------

REVIEWED_PASS2_PATH = os.path.join(cfg.BASE_DIR, "scratch", "tag_merge_decisions_pass2.json")


def _load_pass2_alias_map() -> dict[str, str]:
    """Load the second-pass decisions. An empty canonical means the term is removed."""
    import json

    if not os.path.exists(REVIEWED_PASS2_PATH):
        logger.warning(
            "[MIGRATION] No second-pass decisions at %s — skipping. Regenerate with "
            "scripts/parse_tag_merge_review.py", REVIEWED_PASS2_PATH,
        )
        return {}
    with open(REVIEWED_PASS2_PATH, encoding="utf-8") as fh:
        payload = json.load(fh)
    return {k: v for k, v in (payload.get("alias_map") or {}).items() if k and k != v}


def _drop_removed_from_tag_csv(raw: str | None, alias_map: dict[str, str]) -> tuple[str, bool]:
    """Rewrite a tag CSV, dropping terms whose alias target is empty.

    Args:
        raw: Comma-separated tags.
        alias_map: variant -> canonical, where '' means remove entirely.

    Returns:
        tuple[str, bool]: (rewritten CSV, whether it changed).
    """
    if not raw:
        return "", False
    current = [t.strip() for t in raw.split(",") if t.strip()]
    out: list[str] = []
    for tag in current:
        mapped = alias_map.get(tag, tag)
        if mapped and mapped not in out:
            out.append(mapped)
    return ", ".join(out), out != current


def migrate_000_006_170_second_pass_merges_vault(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.170: Apply the reviewed second-pass merges and removals (§9 step 6c).

    The first merge pass ran at a 0.92 similarity cut, which proved too tight — it left
    `home/maintenance` and `household/maintenance` as separate terms. This applies the
    reviewed 0.88 pass over the now-consolidated vocabulary.

    Removals are recorded as aliases to the empty string, not deleted outright. The
    canonicalization path drops empty targets, so every writer stops emitting the term;
    deleting it silently would leave nothing to stop the next extraction re-minting it.
    """
    import json

    from Evelyn.tools import taxonomy_db
    from Evelyn.tools.frontmatter_utils import update_frontmatter_field, write_file_with_frontmatter
    from Evelyn.tools.tag_librarian import delete_tag_from_chroma

    alias_map = _load_pass2_alias_map()
    if not alias_map:
        return

    vault_root = getattr(cfg, "VAULT_BASE_DIR", "")
    archive_path = _snapshot_vault_markdown(vault_root, "000.006.170")
    cursor = conn.cursor()

    now = time.time()
    for variant, canonical in alias_map.items():
        cursor.execute(
            """INSERT INTO master_tag_aliases (alias, canonical, tier, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(alias) DO UPDATE SET canonical = excluded.canonical,
                                                tier = excluded.tier""",
            (variant, canonical, "removed" if not canonical else "pass2", now),
        )
    taxonomy_db.invalidate_alias_cache()

    manifest: dict[str, dict[str, list[str]]] = {}
    rewritten = 0
    emptied = 0
    for path, raw_tags in cursor.execute(
        "SELECT path, tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall():
        swept, changed = _drop_removed_from_tag_csv(raw_tags, alias_map)
        if not changed:
            continue
        after = [t.strip() for t in swept.split(",") if t.strip()]
        if not after:
            # A note losing every tag is a signal, not a success. Leave it and report.
            emptied += 1
            logger.warning("[MIGRATION 170] Would empty all tags, skipped: %s", path)
            continue
        abs_path = path if os.path.isabs(path) else os.path.join(vault_root, path)
        if os.path.exists(abs_path):
            try:
                with open(abs_path, encoding="utf-8") as fh:
                    content = fh.read()
                write_file_with_frontmatter(
                    abs_path, update_frontmatter_field(content, "tags", after), preserve_mtime=True
                )
                manifest[path] = {"before": [t.strip() for t in raw_tags.split(",")], "after": after}
                rewritten += 1
            except OSError as exc:
                logger.warning("[MIGRATION 170] Write failed, skipped: %s (%s)", path, exc)
                continue
        cursor.execute("UPDATE vault_documents SET tags = ? WHERE path = ?", (swept, path))

    retired = 0
    for variant, canonical in alias_map.items():
        row = cursor.execute(
            "SELECT usage_count FROM master_tag_taxonomy WHERE tag = ?", (variant,)
        ).fetchone()
        if not row:
            continue
        if canonical:
            cursor.execute(
                """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
                   VALUES (?, ?, '', ?, ?, ?)
                   ON CONFLICT(tag) DO UPDATE SET usage_count = master_tag_taxonomy.usage_count + excluded.usage_count,
                                                  updated_at = excluded.updated_at""",
                (canonical, canonical.split("/")[0] if "/" in canonical else "general",
                 row[0] or 0, now, now),
            )
        cursor.execute("DELETE FROM master_tag_taxonomy WHERE tag = ?", (variant,))
        delete_tag_from_chroma(variant)
        retired += 1

    ensure_backup_dir()
    manifest_path = os.path.join(BACKUP_DIR, "pass2_merge_manifest_000.006.170.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"archive": archive_path, "alias_map": alias_map, "documents": manifest}, fh, indent=2)
    logger.info(
        "[MIGRATION 170] %d decisions; %d notes rewritten, %d registry terms retired, "
        "%d notes left alone to avoid emptying them.",
        len(alias_map), rewritten, retired, emptied,
    )


def migrate_000_006_171_second_pass_merges_memory(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.171: Apply the reviewed second-pass decisions to memory tags."""
    vault_path = db_paths.get("vault", "")
    if not vault_path or not os.path.exists(vault_path):
        raise MigrationExecutionError("Vault database unavailable; cannot read alias map.")
    vcon = sqlite3.connect(vault_path, timeout=30.0)
    try:
        alias_map = dict(
            vcon.execute(
                "SELECT alias, canonical FROM master_tag_aliases WHERE tier IN ('pass2', 'removed')"
            )
        )
    finally:
        vcon.close()
    if not alias_map:
        logger.info("[MIGRATION 171] No second-pass aliases recorded; nothing to apply.")
        return

    cursor = conn.cursor()
    total = 0
    for table in ("context_entries", "procedures"):
        try:
            rows = cursor.execute(
                f"SELECT rowid, tags FROM {table} WHERE tags IS NOT NULL AND tags != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        updated = 0
        for row_id, raw in rows:
            swept, changed = _drop_removed_from_tag_csv(raw, alias_map)
            if changed and swept:
                cursor.execute(f"UPDATE {table} SET tags = ? WHERE rowid = ?", (swept, row_id))
                updated += 1
        logger.info("[MIGRATION 171] %s: %d rows rewritten.", table, updated)
        total += updated
    logger.info("[MIGRATION 171] Applied %d decisions across %d memory rows.", len(alias_map), total)


# --- §9 step 6d: retire one-off phrase tags ------------------------------------

def migrate_000_006_173_retire_phrase_tags_vault(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.173: Retire flat multi-word descriptors used once or twice.

    `cat-care-supplies` and `heartwarming-animal-encounters` are sentence fragments that
    happen to be hyphenated: nobody searches them, nothing else shares them, and each costs
    a vocabulary entry for a single document.

    Verbose *and* unshared together — either alone would be wrong. Length alone condemns
    legitimate compound terms; low use alone condemns correct structure that is merely young
    (§6.3.3). Nested terms are excluded whatever their length, because a slash means
    something placed the term in the tree and that structure is the expensive part.

    Recorded as aliases to the empty string, so writers stop emitting them rather than
    re-minting them on the next extraction (§6.2).
    """
    import json

    from Evelyn.tools import taxonomy_db
    from Evelyn.tools.frontmatter_utils import update_frontmatter_field, write_file_with_frontmatter
    from Evelyn.tools.tag_librarian import delete_tag_from_chroma
    from Evelyn.tools.tag_synonym import build_corpus, one_off_phrase_tags

    doomed = one_off_phrase_tags(build_corpus())
    if not doomed:
        logger.info("[MIGRATION 173] No one-off phrase tags found.")
        return
    alias_map = dict.fromkeys(doomed, "")

    vault_root = getattr(cfg, "VAULT_BASE_DIR", "")
    archive_path = _snapshot_vault_markdown(vault_root, "000.006.173")
    cursor = conn.cursor()

    now = time.time()
    for variant in doomed:
        cursor.execute(
            """INSERT INTO master_tag_aliases (alias, canonical, tier, created_at)
               VALUES (?, '', 'phrase-retired', ?)
               ON CONFLICT(alias) DO UPDATE SET canonical = '', tier = 'phrase-retired'""",
            (variant, now),
        )
    taxonomy_db.invalidate_alias_cache()

    manifest: dict[str, dict[str, list[str]]] = {}
    rewritten = emptied = 0
    for path, raw_tags in cursor.execute(
        "SELECT path, tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall():
        swept, changed = _drop_removed_from_tag_csv(raw_tags, alias_map)
        if not changed:
            continue
        after = [t.strip() for t in swept.split(",") if t.strip()]
        if not after:
            emptied += 1  # acceptable: the librarian visits untagged documents first
        abs_path = path if os.path.isabs(path) else os.path.join(vault_root, path)
        if os.path.exists(abs_path):
            try:
                with open(abs_path, encoding="utf-8") as fh:
                    content = fh.read()
                write_file_with_frontmatter(
                    abs_path, update_frontmatter_field(content, "tags", after), preserve_mtime=True
                )
                manifest[path] = {"before": [t.strip() for t in raw_tags.split(",")], "after": after}
                rewritten += 1
            except OSError as exc:
                logger.warning("[MIGRATION 173] Write failed, skipped: %s (%s)", path, exc)
                continue
        cursor.execute("UPDATE vault_documents SET tags = ? WHERE path = ?", (swept, path))

    retired = 0
    for variant in doomed:
        if cursor.execute(
            "SELECT 1 FROM master_tag_taxonomy WHERE tag = ?", (variant,)
        ).fetchone():
            cursor.execute("DELETE FROM master_tag_taxonomy WHERE tag = ?", (variant,))
            delete_tag_from_chroma(variant)
            retired += 1

    ensure_backup_dir()
    manifest_path = os.path.join(BACKUP_DIR, "phrase_retirement_manifest_000.006.173.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"archive": archive_path, "retired": doomed, "documents": manifest}, fh, indent=2)
    logger.info(
        "[MIGRATION 173] Retired %d phrase tags; %d notes rewritten, %d registry terms removed, "
        "%d notes now untagged.", len(doomed), rewritten, retired, emptied,
    )


def migrate_000_006_174_retire_phrase_tags_memory(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.174: Apply the phrase retirements to memory tags."""
    vault_path = db_paths.get("vault", "")
    if not vault_path or not os.path.exists(vault_path):
        raise MigrationExecutionError("Vault database unavailable; cannot read alias map.")
    vcon = sqlite3.connect(vault_path, timeout=30.0)
    try:
        alias_map = dict(
            vcon.execute(
                "SELECT alias, canonical FROM master_tag_aliases WHERE tier = 'phrase-retired'"
            )
        )
    finally:
        vcon.close()
    if not alias_map:
        logger.info("[MIGRATION 174] No phrase retirements recorded.")
        return

    cursor = conn.cursor()
    total = 0
    for table in ("context_entries", "procedures"):
        try:
            rows = cursor.execute(
                f"SELECT rowid, tags FROM {table} WHERE tags IS NOT NULL AND tags != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        updated = 0
        for row_id, raw in rows:
            swept, changed = _drop_removed_from_tag_csv(raw, alias_map)
            if changed:
                cursor.execute(f"UPDATE {table} SET tags = ? WHERE rowid = ?", (swept, row_id))
                updated += 1
        logger.info("[MIGRATION 174] %s: %d rows rewritten.", table, updated)
        total += updated
    logger.info("[MIGRATION 174] Applied %d retirements across %d memory rows.",
                len(alias_map), total)


# --- §9 step 8: decomposition ---------------------------------------------------

def migrate_000_006_181_decompose_to_atoms_vault(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.181: Split hierarchical paths into atoms across the vault (§3.3).

    Pre-coordination composes concepts when a note is filed, which forces the indexer to
    guess which combinations a query will later want. The guesses multiplied here:
    `journaling` sat under 31 different parents, one concept restated 31 times, and 76% of
    the vocabulary was used exactly once.

    Mechanical, with no judgement: `a/b/c` becomes `a` + `b` + `c` by rule. Protected date
    anchors and administrative namespaces are untouched, and a facet prefix keeps its single
    level because it names which axis a term belongs to — something flat atoms cannot say.

    This is 1-to-many, so it cannot use the alias table: an alias records that one term
    *became* another, which is not what happens when a term becomes three.
    """
    import json

    from Evelyn.tools.frontmatter_utils import update_frontmatter_field, write_file_with_frontmatter
    from Evelyn.tools.tag_synonym import decompose_tag_csv, decompose_to_atoms

    vault_root = getattr(cfg, "VAULT_BASE_DIR", "")
    archive_path = _snapshot_vault_markdown(vault_root, "000.006.181")
    cursor = conn.cursor()

    manifest: dict[str, dict[str, list[str]]] = {}
    rewritten = 0
    for path, raw_tags in cursor.execute(
        "SELECT path, tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall():
        swept, changed = decompose_tag_csv(raw_tags)
        if not changed:
            continue
        after = [t.strip() for t in swept.split(",") if t.strip()]
        abs_path = path if os.path.isabs(path) else os.path.join(vault_root, path)
        if os.path.exists(abs_path):
            try:
                with open(abs_path, encoding="utf-8") as fh:
                    content = fh.read()
                write_file_with_frontmatter(
                    abs_path, update_frontmatter_field(content, "tags", after), preserve_mtime=True
                )
                manifest[path] = {"before": [t.strip() for t in raw_tags.split(",")], "after": after}
                rewritten += 1
            except OSError as exc:
                logger.warning("[MIGRATION 181] Write failed, skipped: %s (%s)", path, exc)
                continue
        cursor.execute("UPDATE vault_documents SET tags = ? WHERE path = ?", (swept, path))

    # Rebuild the registry from the decomposed terms, summing usage onto each atom.
    masters = cursor.execute(
        "SELECT tag, category, description, usage_count FROM master_tag_taxonomy"
    ).fetchall()
    merged: dict[str, dict[str, Any]] = {}
    for tag, category, description, usage in masters:
        for atom in decompose_to_atoms(tag):
            entry = merged.setdefault(atom, {"category": "", "description": "", "usage_count": 0})
            entry["usage_count"] += usage or 0
            if description and len(description) > len(entry["description"]):
                entry["description"] = description
            if category and not entry["category"]:
                entry["category"] = category

    cursor.execute("DELETE FROM master_tag_taxonomy")
    now = time.time()
    for tag, meta in merged.items():
        cursor.execute(
            """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tag, meta["category"] or "general", meta["description"], meta["usage_count"], now, now),
        )

    # Recount against the decomposed on-disk state.
    observed: dict[str, int] = {}
    for (doc_tags,) in cursor.execute(
        "SELECT tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall():
        for tag in (t.strip() for t in doc_tags.split(",") if t.strip()):
            observed[tag] = observed.get(tag, 0) + 1
    for tag, count in observed.items():
        cursor.execute(
            """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
               VALUES (?, ?, '', ?, ?, ?)
               ON CONFLICT(tag) DO UPDATE SET usage_count = ?, updated_at = ?""",
            (tag, tag.split("/")[0] if "/" in tag else "general", count, now, now, count, now),
        )

    ensure_backup_dir()
    manifest_path = os.path.join(BACKUP_DIR, "decomposition_manifest_000.006.181.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"archive": archive_path, "documents": manifest}, fh, indent=2)
    logger.info(
        "[MIGRATION 181] Decomposed %d registry terms into %d atoms; rewrote %d notes.",
        len(masters), len(merged), rewritten,
    )


def migrate_000_006_182_decompose_to_atoms_memory(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.182: Decompose memory tags into atoms (§3.3)."""
    from Evelyn.tools.tag_synonym import decompose_tag_csv

    cursor = conn.cursor()
    total = 0
    for table in ("context_entries", "procedures"):
        try:
            rows = cursor.execute(
                f"SELECT rowid, tags FROM {table} WHERE tags IS NOT NULL AND tags != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        updated = 0
        for row_id, raw in rows:
            swept, changed = decompose_tag_csv(raw)
            if changed:
                cursor.execute(f"UPDATE {table} SET tags = ? WHERE rowid = ?", (swept, row_id))
                updated += 1
        logger.info("[MIGRATION 182] %s: %d rows decomposed.", table, updated)
        total += updated
    logger.info("[MIGRATION 182] Decomposed tags across %d memory rows.", total)



HYPHEN_PLAN_PATH = os.path.join(cfg.BASE_DIR, "scratch", "hyphen_decomposition_plan.json")


def _load_hyphen_plan() -> tuple[dict[str, list[str]], list[str]]:
    """Load the frozen flat-compound decomposition plan.

    The plan is derived from a corpus scan rather than hand-curated, but it is still read
    from disk instead of recomputed: a migration must apply the plan that was reviewed, and
    the vault's prose changes underneath a live scan. A missing file no-ops loudly rather
    than half-applying.

    Returns:
        tuple[dict[str, list[str]], list[str]]: (compound -> atoms, aliases to retire).
    """
    import json

    if not os.path.exists(HYPHEN_PLAN_PATH):
        logger.warning(
            "[MIGRATION] No decomposition plan at %s — skipping. Regenerate with "
            "scripts/generate_hyphen_decomposition.py", HYPHEN_PLAN_PATH,
        )
        return {}, []
    with open(HYPHEN_PLAN_PATH, encoding="utf-8") as fh:
        payload = json.load(fh)
    plan = {k: v for k, v in (payload.get("plan") or {}).items() if k and v}
    return plan, list(payload.get("orphaned_aliases") or [])


def migrate_000_006_183_decompose_flat_compounds_vault(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.183: Decompose unwarranted hyphenated compounds (§3.3, §6.3.3).

    Decomposition to atoms stopped at the slash, because §5 gives the hyphen a real job:
    joining words inside one term. Measuring the result showed the job was being abused —
    hyphenated compounds outnumbered atoms three to one, and 1,403 of them appear nowhere
    in the vault's own prose. The classifier made the cost concrete: every extracted subject
    fell through to a proposal, because a pre-coordinate compound outranked the bare atom in
    every nearest-neighbour lookup. `sleep tracking` found `tracking-worries` before `sleep`.

    Literary warrant decides which is which (Z39.19 §6.5.1.1). A compound the vault writes
    as a phrase is a bound term and survives whole — `machine-learning` is written 1,796
    times, `chain-of-thought` 240. A compound nobody has ever written was assembled at
    filing time, and it decomposes.

    Like its predecessor this is one-to-many, so it cannot use the alias table. Aliases
    pointing at a decomposed target are retired for the same reason: there is no single term
    left to point at.
    """
    import json

    from Evelyn.tools.frontmatter_utils import update_frontmatter_field, write_file_with_frontmatter
    from Evelyn.tools.tag_synonym import apply_decomposition_to_csv

    plan, orphaned = _load_hyphen_plan()
    if not plan:
        return

    vault_root = getattr(cfg, "VAULT_BASE_DIR", "")
    archive_path = _snapshot_vault_markdown(vault_root, "000.006.183")
    cursor = conn.cursor()

    manifest: dict[str, dict[str, list[str]]] = {}
    rewritten = 0
    for path, raw_tags in cursor.execute(
        "SELECT path, tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall():
        swept, changed = apply_decomposition_to_csv(raw_tags, plan)
        if not changed:
            continue
        after = [t.strip() for t in swept.split(",") if t.strip()]
        abs_path = path if os.path.isabs(path) else os.path.join(vault_root, path)
        if os.path.exists(abs_path):
            try:
                with open(abs_path, encoding="utf-8") as fh:
                    content = fh.read()
                write_file_with_frontmatter(
                    abs_path, update_frontmatter_field(content, "tags", after), preserve_mtime=True
                )
                manifest[path] = {"before": [t.strip() for t in raw_tags.split(",")], "after": after}
                rewritten += 1
            except OSError as exc:
                logger.warning("[MIGRATION 183] Write failed, skipped: %s (%s)", path, exc)
                continue
        cursor.execute("UPDATE vault_documents SET tags = ? WHERE path = ?", (swept, path))

    # Fold each decomposed term's registry entry onto the atoms it became.
    for term, atoms in plan.items():
        row = cursor.execute(
            "SELECT category, description, usage_count FROM master_tag_taxonomy WHERE tag = ?",
            (term,),
        ).fetchone()
        if not row:
            continue
        category, description, usage = row
        cursor.execute("DELETE FROM master_tag_taxonomy WHERE tag = ?", (term,))
        now = time.time()
        for atom in atoms:
            cursor.execute(
                """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(tag) DO UPDATE SET usage_count = usage_count + ?, updated_at = ?""",
                (atom, category or "general", description or "", usage or 0, now, now, usage or 0, now),
            )

    retired = 0
    for alias in orphaned:
        cursor.execute("DELETE FROM master_tag_aliases WHERE alias = ?", (alias,))
        retired += cursor.rowcount

    # Recount against the decomposed on-disk state, so usage reflects documents not arithmetic.
    observed: dict[str, int] = {}
    for (doc_tags,) in cursor.execute(
        "SELECT tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall():
        for tag in (t.strip() for t in doc_tags.split(",") if t.strip()):
            observed[tag] = observed.get(tag, 0) + 1
    now = time.time()
    for tag, count in observed.items():
        cursor.execute(
            """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
               VALUES (?, ?, '', ?, ?, ?)
               ON CONFLICT(tag) DO UPDATE SET usage_count = ?, updated_at = ?""",
            (tag, tag.split("/")[0] if "/" in tag else "general", count, now, now, count, now),
        )

    ensure_backup_dir()
    manifest_path = os.path.join(BACKUP_DIR, "decomposition_manifest_000.006.183.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"archive": archive_path, "documents": manifest}, fh, indent=2)
    logger.info(
        "[MIGRATION 183] Decomposed %d compounds; rewrote %d notes; retired %d orphaned aliases.",
        len(plan), rewritten, retired,
    )


def migrate_000_006_184_decompose_flat_compounds_memory(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.184: Decompose unwarranted hyphenated compounds in memory (§3.3).

    The vault and memory share one vocabulary (§0), so they decompose together or they drift.
    """
    from Evelyn.tools.tag_synonym import apply_decomposition_to_csv

    plan, _ = _load_hyphen_plan()
    if not plan:
        return

    cursor = conn.cursor()
    total = 0
    for table in ("context_entries", "procedures"):
        try:
            rows = cursor.execute(
                f"SELECT rowid, tags FROM {table} WHERE tags IS NOT NULL AND tags != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        updated = 0
        for row_id, raw in rows:
            swept, changed = apply_decomposition_to_csv(raw, plan)
            if changed:
                cursor.execute(f"UPDATE {table} SET tags = ? WHERE rowid = ?", (swept, row_id))
                updated += 1
        logger.info("[MIGRATION 184] %s: %d rows decomposed.", table, updated)
        total += updated
    logger.info("[MIGRATION 184] Decomposed tags across %d memory rows.", total)


def migrate_000_006_186_reset_subject_tags_vault(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.186: Clear every tag from the vault and empty the registry.

    The assignments were not worth repairing. Essentially all of them were produced by the
    tagging pipeline rather than by hand, and that pipeline was the thing under repair —
    pre-coordinate compounds, terms filed under 31 different parents, aliases pointing at
    forms a later pass had dissolved. Each fix inherited the previous pass's mistakes as its
    input, so correctness kept being defined relative to a corpus that was itself wrong.

    What survives is the part that was actually curated: the standard in
    `.agents/rules/vault-tag-taxonomy.md`. The vocabulary is regenerated against it rather
    than migrated toward it, which is the difference between a clean derivation and another
    correction layered on an uncorrected base.

    **Nothing is exempt, including administrative tags.** Preserving a category by rule is
    how the previous state kept partially surviving its own corrections, and a partial wipe
    leaves open the question of whether a given tag is old or new. Date anchors, `status/`,
    `kanban` and `obsidian-graph/` are recorded per document in the manifest so they can be
    reinstated as a deliberate act rather than persisting by default.

    Every document's audit timestamp is reset so the whole vault re-enters the queue.
    """
    import json

    from Evelyn.tools.frontmatter_utils import update_frontmatter_field, write_file_with_frontmatter
    from Evelyn.tools.tag_librarian import is_excluded_tag

    vault_root = getattr(cfg, "VAULT_BASE_DIR", "")
    archive_path = _snapshot_vault_markdown(vault_root, "000.006.186")
    cursor = conn.cursor()

    manifest: dict[str, dict[str, list[str]]] = {}
    rewritten = 0
    cleared = 0
    administrative = 0
    for path, raw_tags in cursor.execute(
        "SELECT path, tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall():
        current = [t.strip() for t in raw_tags.split(",") if t.strip()]
        if not current:
            continue
        system = [t for t in current if is_excluded_tag(t)]
        cleared += len(current)
        administrative += len(system)
        abs_path = path if os.path.isabs(path) else os.path.join(vault_root, path)
        if os.path.exists(abs_path):
            try:
                with open(abs_path, encoding="utf-8") as fh:
                    content = fh.read()
                write_file_with_frontmatter(
                    abs_path, update_frontmatter_field(content, "tags", []), preserve_mtime=True
                )
                manifest[path] = {"all": current, "system": system}
                rewritten += 1
            except OSError as exc:
                logger.warning("[MIGRATION 186] Write failed, skipped: %s (%s)", path, exc)
                continue
        cursor.execute("UPDATE vault_documents SET tags = '' WHERE path = ?", (path,))

    terms = cursor.execute("SELECT COUNT(*) FROM master_tag_taxonomy").fetchone()[0]
    aliases = cursor.execute("SELECT COUNT(*) FROM master_tag_aliases").fetchone()[0]
    cursor.execute("DELETE FROM master_tag_taxonomy")
    cursor.execute("DELETE FROM master_tag_aliases")

    # Re-queue the whole vault: every note now needs classification from scratch.
    cursor.execute("UPDATE vault_documents SET last_semantic_tag_audit = 0")

    ensure_backup_dir()
    manifest_path = os.path.join(BACKUP_DIR, "tag_reset_manifest_000.006.186.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"archive": archive_path, "documents": manifest}, fh, indent=2)
    logger.info(
        "[MIGRATION 186] Cleared %d tags from %d notes (%d administrative, recorded in %s); "
        "dropped %d terms and %d aliases.",
        cleared, rewritten, administrative, manifest_path, terms, aliases,
    )


def migrate_000_006_187_reset_subject_tags_memory(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.187: Clear subject tags from memory (§0: one structure, one vocabulary).

    Memory carried the same generated vocabulary as the vault and inherits the same verdict,
    including the total scope — a vocabulary rebuilt on one substrate and not the other is how
    they drifted apart in the first place. Administrative tags are recorded to a manifest
    before removal so reinstating them stays a deliberate act.
    """
    import json

    from Evelyn.tools.tag_librarian import is_excluded_tag

    cursor = conn.cursor()
    total = 0
    recorded: dict[str, dict[str, list[str]]] = {}
    for table in ("context_entries", "procedures"):
        try:
            rows = cursor.execute(
                f"SELECT rowid, tags FROM {table} WHERE tags IS NOT NULL AND tags != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        updated = 0
        for row_id, raw in rows:
            current = [t.strip() for t in raw.split(",") if t.strip()]
            if not current:
                continue
            system = [t for t in current if is_excluded_tag(t)]
            if system:
                recorded.setdefault(table, {})[str(row_id)] = system
            cursor.execute(f"UPDATE {table} SET tags = '' WHERE rowid = ?", (row_id,))
            updated += 1
        logger.info("[MIGRATION 187] %s: %d rows cleared.", table, updated)
        total += updated

    ensure_backup_dir()
    manifest_path = os.path.join(BACKUP_DIR, "tag_reset_manifest_000.006.187_memory.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(recorded, fh, indent=2)
    logger.info("[MIGRATION 187] Cleared tags across %d memory rows; administrative tags in %s.",
                total, manifest_path)


def migrate_000_006_189_normalize_mood_property(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.189: Record journal mood as a frontmatter property.

    Mood was written three ways — `**Mood:** Calm / Warm` in the body, `mood:` in
    frontmatter, and a handful of `#mood/anxious` hashtags — so nothing could query it
    consistently. It is a *property* of an entry rather than a subject of it, which is why it
    does not belong in the tag vocabulary: 142 distinct values across 167 uses, phrased like
    "glacial, fried, transparent". A controlled vocabulary would either mint 142 single-use
    terms or flatten that into `tired`, and both outcomes are worse than the prose.

    The body line is deliberately **left in place**. It is Evelyn's writing, part of the
    entry's visible texture, and 77 notes already carried both forms without harm. This pass
    only ensures the frontmatter property exists and holds the same value, so the field is
    queryable everywhere it is present. Where frontmatter already has a value it wins, on the
    grounds that it was the more deliberate of the two.

    The `#mood/*` hashtags are a different case and are defused: body hashtags no longer enter
    the vocabulary at all, so leaving them would preserve a form that now means nothing.
    """
    import json
    import re

    from Evelyn.tools.frontmatter_utils import update_frontmatter_field, write_file_with_frontmatter

    vault_root = getattr(cfg, "VAULT_BASE_DIR", "")
    if not vault_root or not os.path.isdir(vault_root):
        logger.warning("[MIGRATION 189] Vault root unavailable — skipping.")
        return

    archive_path = _snapshot_vault_markdown(vault_root, "000.006.189")
    body_field = re.compile(r"^[ \t]*(?:\*{1,2})?\s*Mood\s*(?:\*{1,2})?\s*[::]\s*(.+?)\s*$",
                            re.IGNORECASE | re.MULTILINE)
    fm_field = re.compile(r"^mood\s*:", re.IGNORECASE | re.MULTILINE)
    mood_tag = re.compile(r"(?<![\w&#/])#mood/([A-Za-z0-9_-]+)")

    populated = 0
    defused = 0
    manifest: dict[str, str] = {}
    for current, _dirs, files in os.walk(vault_root):
        for name in files:
            if not name.lower().endswith(".md"):
                continue
            path = os.path.join(current, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    content = fh.read()
            except OSError:
                continue

            head_match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", content, re.DOTALL)
            head = head_match.group(1) if head_match else ""
            body = content[head_match.end():] if head_match else content

            value = ""
            found = body_field.search(body)
            if found:
                value = found.group(1).strip().strip("*_ ").strip()
            if not value:
                tagged = mood_tag.search(body)
                if tagged:
                    value = tagged.group(1).replace("-", " ").strip()

            changed = content
            if mood_tag.search(body):
                # Defuse the hashtag in place: the word stays, the uncontrolled tag does not.
                new_body = mood_tag.sub(lambda m: m.group(1).replace("-", " "), body)
                changed = (changed[:head_match.end()] + new_body) if head_match else new_body
                defused += 1

            if value and not fm_field.search(head):
                changed = update_frontmatter_field(changed, "mood", value)
                manifest[os.path.relpath(path, vault_root)] = value
                populated += 1

            if changed != content:
                try:
                    write_file_with_frontmatter(path, changed, preserve_mtime=True)
                except OSError as exc:
                    logger.warning("[MIGRATION 189] Write failed, skipped: %s (%s)", path, exc)

    ensure_backup_dir()
    manifest_path = os.path.join(BACKUP_DIR, "mood_property_manifest_000.006.189.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump({"archive": archive_path, "populated": manifest}, fh, indent=2)
    logger.info(
        "[MIGRATION 189] Populated mood on %d notes; defused %d mood hashtags.", populated, defused
    )


BOOK_TAG_PATH = os.path.join(cfg.BASE_DIR, "scratch", "book_tag_assignment.json")


def migrate_000_006_190_tag_reference_library_by_book(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.190: Tag the Reference Library at book level, not chapter level.

    The library is 2,838 of the vault's 4,219 notes, and its folder structure already
    classifies it: `Reference Library/Hands-On Large Language Models/130 - Reranking.md`
    states both the book and the chapter. Tagging all 212 of that book's chapters
    `large-language-models` adds nothing — they are uniformly about it, and the path said so
    first. Measured across the library, the distinctive vocabulary came back as `model`,
    `data`, `training`, `prompt` for every one of the 2,838.

    So subjects attach to each book's `_index` note and nowhere else. That matches how the
    library is already treated elsewhere: it sits in `RAG_EXCLUDED_SUBDIRS` and is routed to
    its own Chroma collection precisely so it cannot dominate retrieval.

    Two guards, both from measurement rather than caution:

    - **Sparse index notes are not trusted.** Extraction on a note with three or fewer
      chapters hallucinated — a pocket watch manual came back "artificial intelligence,
      machine learning, multiagent systems"; a television, "graph theory". Every failure sat
      at or below that count and none above it, so those notes take a domain and a device
      type read from the title, and nothing inferred.
    - **The source path is a filing location, not a subject.** Two engineering-management
      titles are stored under `AI/` and are not about it.

    Assignments are frozen in `scratch/` rather than recomputed, for the same reason the
    other curated decisions are: a migration applies what was reviewed.
    """
    import json

    from Evelyn.tools.frontmatter_utils import update_frontmatter_field, write_file_with_frontmatter

    if not os.path.exists(BOOK_TAG_PATH):
        logger.warning(
            "[MIGRATION 190] No book assignment at %s — skipping. Regenerate with the "
            "book-level extraction pass.", BOOK_TAG_PATH,
        )
        return

    with open(BOOK_TAG_PATH, encoding="utf-8") as fh:
        payload = json.load(fh)
    assignments = payload.get("assignments") or {}
    if not assignments:
        return

    vault_root = getattr(cfg, "VAULT_BASE_DIR", "")
    _snapshot_vault_markdown(vault_root, "000.006.190")
    cursor = conn.cursor()

    written = 0
    applied_terms: dict[str, int] = {}
    for rel_path, entry in assignments.items():
        tags = [t for t in (entry.get("tags") or []) if t]
        if not tags:
            continue
        abs_path = os.path.join(vault_root, rel_path)
        if not os.path.exists(abs_path):
            logger.warning("[MIGRATION 190] Missing note, skipped: %s", rel_path)
            continue
        try:
            with open(abs_path, encoding="utf-8") as fh:
                content = fh.read()
            write_file_with_frontmatter(
                abs_path, update_frontmatter_field(content, "tags", tags), preserve_mtime=True
            )
        except OSError as exc:
            logger.warning("[MIGRATION 190] Write failed, skipped: %s (%s)", rel_path, exc)
            continue
        cursor.execute("UPDATE vault_documents SET tags = ? WHERE path = ?", (", ".join(tags), rel_path))
        written += 1
        for t in tags:
            applied_terms[t] = applied_terms.get(t, 0) + 1

    now = time.time()
    for term, count in applied_terms.items():
        cursor.execute(
            """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
               VALUES (?, 'reference', '', ?, ?, ?)
               ON CONFLICT(tag) DO UPDATE SET usage_count = usage_count + ?, updated_at = ?""",
            (term, count, now, now, count, now),
        )
    logger.info(
        "[MIGRATION 190] Tagged %d book index notes; registered %d reference terms.",
        written, len(applied_terms),
    )


SEED_VOCAB_PATH = os.path.join(cfg.BASE_DIR, "scratch", "seed_vocabulary_review.md")


def migrate_000_006_191_seed_personal_vocabulary(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.191: Register the reviewed seed vocabulary (§9 Phase B).

    The first vocabulary here that anyone actually chose. Everything the registry held before
    was generated by the pipeline under repair, which is why it was cleared rather than
    corrected — each pass had been refining its predecessor's mistakes.

    Terms were derived by blind extraction over a 235-note stratified sample, aggregated by
    term rather than by document, cross-checked against literary warrant in the user's own
    prose, curated down, and then reviewed by hand. Proper nouns are absent by construction
    (§2 makes individuals links, not tags), as is `mood` — 142 distinct values across 167
    uses is expressive free text, and it lives in frontmatter as a property.

    Usage counts start at zero. They are earned during classification, not assumed here; the
    admission floor has nothing to measure until the corpus has actually been indexed against
    this vocabulary.
    """
    import re

    if not os.path.exists(SEED_VOCAB_PATH):
        logger.warning(
            "[MIGRATION 191] No reviewed seed at %s — skipping. Regenerate and review first.",
            SEED_VOCAB_PATH,
        )
        return

    with open(SEED_VOCAB_PATH, encoding="utf-8") as fh:
        text = fh.read()

    cursor = conn.cursor()
    now = time.time()
    category = "general"
    added = 0
    skipped = 0
    for line in text.splitlines():
        heading = re.match(r"^##\s+(.*)$", line)
        if heading:
            # Section headings carry a thematic emoji; the words after it name the domain.
            words = re.sub(r"[^A-Za-z& ]", " ", heading.group(1)).split()
            category = "-".join(w.lower() for w in words if w != "&") or "general"
            continue
        cell = re.match(r"^\|\s*`([^`]+)`\s*\|", line)
        if not cell:
            continue
        term = cell.group(1).strip()
        if not term:
            continue
        before = cursor.execute(
            "SELECT COUNT(*) FROM master_tag_taxonomy WHERE tag = ?", (term,)
        ).fetchone()[0]
        cursor.execute(
            """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
               VALUES (?, ?, '', 0, ?, ?)
               ON CONFLICT(tag) DO UPDATE SET category = excluded.category, updated_at = ?""",
            (term, category, now, now, now),
        )
        if before:
            skipped += 1
        else:
            added += 1

    total = cursor.execute("SELECT COUNT(*) FROM master_tag_taxonomy").fetchone()[0]
    logger.info(
        "[MIGRATION 191] Seeded %d new terms (%d already present from the reference pass); "
        "registry now holds %d.", added, skipped, total,
    )


def migrate_000_006_192_seed_vocabulary_gaps(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.192: Add four terms the seed curation dropped by mistake.

    The seed was assembled by typing terms into domain groups by hand, which meant anything
    not explicitly typed fell out regardless of its evidence. The classification dry run
    surfaced the omissions: `journaling` had the highest extraction support of any candidate
    (28 units) and was simply never written into a group.

    Only the clean gaps are added. The same pass surfaced terms that are correctly absent —
    `dungeons-dragons` and `gnolls` are proper nouns and belong in links (§2); `security` and
    `llm-agents` duplicate `cybersecurity` and `ai-agents`; and `memory`, despite 676
    occurrences, is polysemous across this corpus — human recall in the journals, hardware in
    the reference material, and the engine's own subsystem. A term that denotes three things
    retrieves none of them, so it stays a proposal until it is split deliberately.
    """
    terms = {
        "journaling": "creative-media",
        "decision-making": "relationships-self",
        "dehydration": "health-body",
        "geography": "professional-civic",
    }
    cursor = conn.cursor()
    now = time.time()
    added = 0
    for tag, category in terms.items():
        existing = cursor.execute(
            "SELECT COUNT(*) FROM master_tag_taxonomy WHERE tag = ?", (tag,)
        ).fetchone()[0]
        if existing:
            continue
        cursor.execute(
            """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
               VALUES (?, ?, '', 0, ?, ?)""",
            (tag, category, now, now),
        )
        added += 1
    total = cursor.execute("SELECT COUNT(*) FROM master_tag_taxonomy").fetchone()[0]
    logger.info("[MIGRATION 192] Added %d seed gaps; registry now holds %d terms.", added, total)


PASS1_VOCAB_PATH = os.path.join(cfg.BASE_DIR, "scratch", "pass1_vocabulary.md")


def migrate_000_006_194_register_pass1_vocabulary(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.194: Register the full-read vocabulary and its aliases.

    The seed (191) came from a 235-note sample. This vocabulary came from reading the whole
    personal corpus and reasoning over it, then measuring every candidate's literary warrant
    and handing the result to the user for line-by-line review. As with 191 the reviewed file
    is the source of truth and lives outside the repository: the migration parses it rather
    than carrying the terms, so the terms never enter version control.

    What is read from the document:
      * §3 group tables      -> subject terms; the group heading becomes the category
      * §3 alias (UF) tables -> `master_tag_aliases`, tier ``reviewed``
      * §4 type table        -> ``type/`` values
      * §5-7 facet lists     -> ``motif/``, ``setting/``, ``event/`` seeds

    An alias whose text is itself a registered term is *not* written: those are the library
    long forms (``large-language-models`` beside ``llm``) whose collapse was deliberately
    deferred until classification has produced usage counts to retag against. They are logged
    so the deferred list is visible.
    """
    import re

    from Evelyn.tools import taxonomy_db

    if not os.path.exists(PASS1_VOCAB_PATH):
        logger.warning(
            "[MIGRATION 194] No reviewed vocabulary at %s — skipping. Review first.",
            PASS1_VOCAB_PATH,
        )
        return

    with open(PASS1_VOCAB_PATH, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    h2 = re.compile(r"^##\s+\S+\s+(\d+)\.")
    h3 = re.compile(r"^###\s+(.*)$")
    cell = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([^|]*)\|")
    facet_token = re.compile(r"`((?:motif|setting|event)/[a-z0-9-]+)`")

    terms: dict[str, str] = {}
    aliases: dict[str, str] = {}
    section = 0
    category = "general"
    in_alias_table = False
    for line in lines:
        m_h2 = h2.match(line)
        if m_h2:
            section = int(m_h2.group(1))
            in_alias_table = False
            continue
        m_h3 = h3.match(line)
        if m_h3:
            title = m_h3.group(1)
            in_alias_table = "UF" in title or "facet" in title
            words = re.sub(r"[^A-Za-z& ]", " ", title).split()
            category = "-".join(w.lower() for w in words if w != "&") or "general"
            continue
        if section == 3:
            m_cell = cell.match(line)
            if not m_cell:
                continue
            first = m_cell.group(1).strip()
            second = m_cell.group(2).strip()
            if in_alias_table:
                m_canon = re.match(r"`([^`]+)`", second)
                if m_canon:
                    aliases[first] = m_canon.group(1).strip()
            elif second in ("✅", "➕"):
                terms[first] = category
        elif section == 4:
            # New type rows carry a marker after the cell (`type/profile` ➕), so match the
            # token alone rather than the full cell.
            m_type = re.match(r"^\|\s*`(type/[a-z0-9-]+)`", line)
            if m_type:
                terms[m_type.group(1)] = "type"
        elif section in (5, 6, 7):
            if line.lstrip().startswith("Retired"):
                continue
            for tok in facet_token.findall(line):
                terms[tok] = tok.split("/", 1)[0]

    cursor = conn.cursor()
    now = time.time()
    added = updated = 0
    for term, cat in terms.items():
        before = cursor.execute(
            "SELECT COUNT(*) FROM master_tag_taxonomy WHERE tag = ?", (term,)
        ).fetchone()[0]
        cursor.execute(
            """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
               VALUES (?, ?, '', 0, ?, ?)
               ON CONFLICT(tag) DO UPDATE SET category = excluded.category, updated_at = ?""",
            (term, cat, now, now, now),
        )
        if before:
            updated += 1
        else:
            added += 1

    registered = {
        row[0] for row in cursor.execute("SELECT tag FROM master_tag_taxonomy").fetchall()
    }
    written = 0
    deferred: list[str] = []
    dangling: list[str] = []
    for alias, canonical in aliases.items():
        if alias in registered:
            deferred.append(f"{alias}->{canonical}")
            continue
        if canonical not in registered:
            dangling.append(f"{alias}->{canonical}")
            continue
        cursor.execute(
            """INSERT INTO master_tag_aliases (alias, canonical, tier, created_at)
               VALUES (?, ?, 'reviewed', ?)
               ON CONFLICT(alias) DO UPDATE SET canonical = excluded.canonical, tier = 'reviewed'""",
            (alias, canonical, now),
        )
        written += 1
    taxonomy_db.invalidate_alias_cache()

    total = cursor.execute("SELECT COUNT(*) FROM master_tag_taxonomy").fetchone()[0]
    logger.info(
        "[MIGRATION 194] Terms: %d added, %d re-categorised; registry now holds %d. "
        "Aliases: %d written, %d deferred (alias is a registered term: %s), %d dangling (%s).",
        added, updated, total, written,
        len(deferred), ", ".join(deferred) or "none",
        len(dangling), ", ".join(dangling) or "none",
    )



# DCMI Type Vocabulary values that sub-type `type/media` (standard §"media sub-typing").
MEDIA_SUBTYPES: tuple[str, ...] = (
    "type/media/text",
    "type/media/still-image",
    "type/media/moving-image",
    "type/media/sound",
    "type/media/interactive",
    "type/media/software",
    "type/media/dataset",
    "type/media/physical-object",
)


def migrate_000_006_195_register_media_subtypes(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.195: Register the eight DCMI sub-types of ``type/media``.

    The standard makes sub-typing mandatory on ``type/media`` (a scanned tax return is
    ``type/media/text``, a fan chart ``type/media/still-image``) but migration 194 read only
    the top-level ``type/`` rows from the review document, so no sub-type was registered and
    the Pass 2 applier rejected every one. The values are the closed DCMI list, not personal
    vocabulary, so they are carried here rather than read from the gitignored document.

    Idempotent: existing rows keep their usage counts.
    """
    cursor = conn.cursor()
    now = time.time()
    added = 0
    for tag in MEDIA_SUBTYPES:
        cursor.execute(
            """INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
               VALUES (?, 'type', '', 0, ?, ?)
               ON CONFLICT(tag) DO UPDATE SET category = excluded.category, updated_at = ?""",
            (tag, now, now, now),
        )
        added += cursor.rowcount
    conn.commit()
    with contextlib.suppress(Exception):
        from Evelyn.tools import taxonomy_db

        taxonomy_db.invalidate_alias_cache()
    logger.info("[MIGRATION 000.006.195] media sub-types registered/refreshed: %d", added)

def migrate_000_006_196_protect_curated_taxonomy(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.196: Mark curated vocabulary terms as protected from pruning.

    ``maintain_master_taxonomy()`` deletes every term with zero usage. That premise holds
    for a folksonomy grown from documents, but this is a *controlled* vocabulary: reserved
    terms are registered deliberately and exist before anything uses them. Under the old
    rule the eight DCMI media sub-types registered one migration ago, and the reserved
    ``event/`` values, were first in line for deletion.

    Every term presently in the registry arrived from the reviewed vocabulary (migrations
    194 and 195), so all are marked protected here. Terms that enter later by inference
    carry ``protected = 0`` and stay prunable.

    Idempotent: the column is added only when absent, and the flag is re-asserted for the
    curated rows on every run.
    """
    cursor = conn.cursor()
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(master_tag_taxonomy)")}
    if "protected" not in columns:
        cursor.execute("ALTER TABLE master_tag_taxonomy ADD COLUMN protected INTEGER DEFAULT 0")

    cursor.execute("UPDATE master_tag_taxonomy SET protected = 1")
    protected_count = cursor.rowcount
    conn.commit()
    logger.info("[MIGRATION 000.006.196] curated taxonomy terms protected: %d", protected_count)


def migrate_000_006_216_tag_relations(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.216: Add the associative (`RT`) layer to the vocabulary.

    Flattening the hierarchy removed relational information without replacing it.
    ``health/sleep`` stated that sleep belongs with health; ``tech/gis`` stated that GIS is a
    kind of technology. Atomised into ``health`` + ``sleep``, nothing states that any more —
    the vocabulary knows the terms co-occur on documents, which is not the same as knowing
    they are related (taxonomy §6.4).

    ``master_tag_related`` is where that knowledge lives. Relations are symmetric and stored
    once, with the pair ordered so ``(a, b)`` and ``(b, a)`` cannot both be recorded. Every
    row is approved by hand: the standard is explicit that relations are never inferred and
    activated automatically, because the bands that look like associative material are mostly
    missed merges and same-root siblings.

    ``weight`` is the retrieval multiplier for expansion through the relation, below a direct
    match. ``tier`` records how the relation was established, so an inferred candidate is
    distinguishable from a curated one.

    Idempotent: creates the table and its index only when absent.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS master_tag_related (
            term_a     TEXT NOT NULL,
            term_b     TEXT NOT NULL,
            weight     REAL NOT NULL DEFAULT 0.4,
            tier       TEXT NOT NULL DEFAULT 'reviewed',
            note       TEXT,
            created_at REAL,
            PRIMARY KEY (term_a, term_b),
            CHECK (term_a < term_b)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_tag_related_b ON master_tag_related(term_b)"
    )
    conn.commit()
    logger.info("[MIGRATION 000.006.216] master_tag_related ready")


def migrate_000_006_217_relation_kind(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.217: Give relations a kind, and let a hierarchical one keep its direction.

    ``master_tag_related`` arrived one release ago holding a single undifferentiated relation,
    with ``CHECK (term_a < term_b)`` to keep a symmetric pair from being stored twice. §6.4 now
    holds two kinds, and that constraint is wrong for one of them: *narrower* is directional —
    ``lucid-dreaming`` is a kind of ``dream``, not the reverse — and alphabetical ordering
    silently reverses half of them.

    The table is rebuilt with a ``kind`` column and without that constraint. Ordering is now the
    caller's business: symmetric ``related`` pairs are sorted before storage, while ``narrower``
    rows keep ``term_a`` as the narrower term. Existing rows carry over as ``related``, which is
    the safe reading — a relation recorded before the distinction existed cannot be assumed
    hierarchical.

    The previous migration is applied and immutable, so this corrects it in a new step (§5).
    """
    cursor = conn.cursor()
    tables = {r[0] for r in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "master_tag_related" not in tables:
        return

    columns = {row[1] for row in cursor.execute("PRAGMA table_info(master_tag_related)")}
    if "kind" in columns:
        return

    cursor.execute(
        """
        CREATE TABLE master_tag_related_new (
            term_a     TEXT NOT NULL,
            term_b     TEXT NOT NULL,
            kind       TEXT NOT NULL DEFAULT 'related',
            weight     REAL NOT NULL DEFAULT 0.4,
            tier       TEXT NOT NULL DEFAULT 'reviewed',
            note       TEXT,
            created_at REAL,
            PRIMARY KEY (term_a, term_b),
            CHECK (kind IN ('related', 'narrower')),
            CHECK (term_a <> term_b)
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO master_tag_related_new (term_a, term_b, kind, weight, tier, note, created_at)
        SELECT term_a, term_b, 'related', weight, tier, note, created_at FROM master_tag_related
        """
    )
    carried = cursor.rowcount
    cursor.execute("DROP TABLE master_tag_related")
    cursor.execute("ALTER TABLE master_tag_related_new RENAME TO master_tag_related")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tag_related_b ON master_tag_related(term_b)")
    conn.commit()
    logger.info("[MIGRATION 000.006.217] relations rebuilt with kind; %d row(s) carried", carried)


def migrate_000_006_218_drop_derived_categories(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.218: Stop storing a category that only restates the term's own prefix.

    ``master_tag_taxonomy.category`` holds two different kinds of value. For a flat term it is a
    curated topical group — a human judgement recorded nowhere else. For a facet-prefixed term it
    was auto-filled from the prefix itself: ``motif/storm`` had category ``motif``. That half is
    pure restatement, derivable from the tag string at any time, and it is what made the column
    read as a facet placeholder rather than the review aid it is.

    Clearing it leaves the curated groups untouched: they cannot be recomputed, so they are not
    swept away on the strength of the derivable ones looking redundant.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE master_tag_taxonomy
           SET category = '', updated_at = ?
         WHERE instr(tag, '/') > 0
           AND category = substr(tag, 1, instr(tag, '/') - 1)
        """,
        (time.time(),),
    )
    logger.info(
        "[MIGRATION 218] Cleared %d category value(s) that restated the term's own prefix.",
        cursor.rowcount,
    )


def migrate_000_006_229_memory_tag_audit_cursor(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.229: Give memory facts their own re-tagging cursor.

    Migration `000.006.187` cleared 39,061 tags across 12,893 memory rows so the vocabulary
    could be regenerated from the standard rather than migrated toward it. `000.006.186` did the
    same to the vault *and reset every document's audit timestamp*, so the vault re-entered the
    semantic queue and has been draining since. Memory got no equivalent, and 10,033 facts have
    been sitting untagged — invisible to tag retrieval — ever since.

    `last_audited_at` cannot serve as that cursor: it already belongs to the grounding auditor
    and is stamped by every merge, so the two passes would reset each other's progress.
    """
    cursor = conn.cursor()
    cols = {row[1] for row in cursor.execute("PRAGMA table_info(context_entries)").fetchall()}
    if "last_tag_audit_at" not in cols:
        cursor.execute("ALTER TABLE context_entries ADD COLUMN last_tag_audit_at REAL")
        logger.info("[MIGRATION 229] Added context_entries.last_tag_audit_at.")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ce_tag_audit ON context_entries(status, last_tag_audit_at)"
    )


def migrate_000_006_231_proposal_rejection_count(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.231: Let a rejection accumulate evidence instead of being discarded.

    `status='rejected'` was written by `reject_proposal()` and read by nothing — every consumer
    of the table filters `status='pending'`. So rejecting decided nothing: the next producer that
    wanted the term raised it again, and the reviewer's only option was to reject it again. 78
    rejections across five proposal types carried no effect, and `support` made the full round
    trip inside a day.

    A rejection is now permanent, and this column is what makes it informative rather than merely
    silencing. Each subsequent request for a rejected term increments it rather than creating a
    row, so the number answers the question a bare "no" cannot: how much does the corpus keep
    asking for this? A term at 1 was a one-off. A term at 30 is a real subject the vocabulary is
    missing, and the count is the argument for admitting it.
    """
    cursor = conn.cursor()
    cols = {row[1] for row in cursor.execute("PRAGMA table_info(proposals)").fetchall()}
    if "rejection_count" not in cols:
        cursor.execute("ALTER TABLE proposals ADD COLUMN rejection_count INTEGER DEFAULT 0")
        logger.info("[MIGRATION 231] Added proposals.rejection_count.")

    # Every existing rejection was a deliberate decision by the reviewer, so it starts at one
    # rather than zero: the count is "times this was turned down or asked for again", and each
    # of these was turned down once.
    cursor.execute(
        "UPDATE proposals SET rejection_count = 1 "
        "WHERE status = 'rejected' AND COALESCE(rejection_count, 0) = 0"
    )
    logger.info("[MIGRATION 231] Seeded %d existing rejection(s) at 1.", cursor.rowcount)

    # The suppression check reads by (type, topic, status) on every admission proposal.
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_proposals_type_topic_status "
        "ON proposals(type, topic, status)"
    )


def migrate_000_006_234_proposal_evidence(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.234: Record the evidence a proposal was made on.

    `.231` made a `tag_admission` rejection permanent, which is right for a vocabulary term: "this
    is not one of our subjects" does not expire. It is wrong for a ghost link. A `[[Foo]]` cited
    in three notes may not deserve a stub today and clearly does at thirty, so a permanent block
    would silence the very growth that should change the answer.

    The evidence level has to be comparable for that, and it was only ever recorded as prose
    inside `reason` ("cited in 7 notes"). This column holds it as a number, so a producer can ask
    whether the situation has materially changed since the reviewer said no.

    Backfilled for existing ghost-link rejections by parsing that sentence — acceptable once, in
    a migration, against a format this codebase wrote itself. New proposals set it directly.
    """
    cursor = conn.cursor()
    cols = {row[1] for row in cursor.execute("PRAGMA table_info(proposals)").fetchall()}
    if "evidence" not in cols:
        cursor.execute("ALTER TABLE proposals ADD COLUMN evidence INTEGER")
        logger.info("[MIGRATION 234] Added proposals.evidence.")

    rows = cursor.execute(
        "SELECT id, reason FROM proposals WHERE type = 'ghost_link_stub' "
        "AND COALESCE(reason,'') <> '' AND evidence IS NULL"
    ).fetchall()
    filled = 0
    for pid, reason in rows:
        m = re.search(r"cited in (\d+) notes?", str(reason or ""))
        if not m:
            continue
        cursor.execute("UPDATE proposals SET evidence = ? WHERE id = ?", (int(m.group(1)), pid))
        filled += 1
    logger.info("[MIGRATION 234] Backfilled evidence on %d of %d ghost-link proposal(s).",
                filled, len(rows))


def migrate_000_006_250_tag_entities(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.250: A register for names, kept apart from the subject vocabulary.

    A proposed term that turns out to be a name had no correct action. Admitting it puts a
    shop or a person into a controlled vocabulary of subjects, where it will be applied to
    unrelated notes; rejecting it is permanent since `.231` and spends the *word*, so
    `historical` could never again be admitted as the ordinary adjective.

    Separating the registers dissolves the collision, which is why authority control has
    always kept them apart - LCSH beside LCNAF, and four of FAST's nine facets are name
    facets. A name and a subject may share a string because they are not in the same
    namespace.

    `label` exists because the term is often lossy: a split recorded one shop as `Historical`,
    dropping the second word of its actual name. The register is the place to put it back.

    Local and gitignored by construction: this is the one taxonomy table that can hold
    personal names, so it must never reach the tracked base vocabulary (§4).
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_entities (
            term          TEXT PRIMARY KEY,
            label         TEXT,
            kind          TEXT,
            note          TEXT,
            request_count INTEGER NOT NULL DEFAULT 1,
            recorded_at   REAL
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tag_entities_kind ON tag_entities(kind)")
    logger.info("[MIGRATION 250] Name register ready.")


def migrate_000_006_246_base_taxonomy_layer(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.246: A read-only base layer beneath the local vocabulary.

    `master_tag_taxonomy` conflates three different kinds of fact in one row: what a term
    means, who says so, and how often this corpus happens to use it. The consequences were
    concrete. A fresh clone got an empty table - nothing in the repository seeds a single
    term, so every mechanism the taxonomy rules describe operated on nothing until the user
    hand-curated a vocabulary. And the nightly census rewrites `usage_count` in the same row
    as the definition (164 of them on 2026-09-26), so the definitions could never live in a
    tracked file without a background task dirtying it every night.

    This adds the layer underneath. `base_tag_taxonomy` is materialised from the tracked
    `taxonomy/base.json` and is **never written by the engine** - not by admission, not by
    retirement, and above all not by the census, which has no column here to write into.
    Local decisions stay in `master_tag_taxonomy`, where a row for the same term is an
    override rather than a duplicate.

    `base_taxonomy_meta` records which version of the file is loaded, so the loader can tell
    a rebuild from a no-op without re-reading every term.
    """
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS base_tag_taxonomy (
            term              TEXT PRIMARY KEY,
            category          TEXT,
            description       TEXT,
            scheme            TEXT,
            scheme_id         TEXT,
            authorized_label  TEXT,
            loaded_at         REAL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS base_tag_aliases (
            alias      TEXT PRIMARY KEY,
            canonical  TEXT NOT NULL,
            scheme     TEXT,
            loaded_at  REAL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS base_taxonomy_meta (
            key    TEXT PRIMARY KEY,
            value  TEXT
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_base_tag_scheme ON base_tag_taxonomy(scheme)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_base_alias_canonical ON base_tag_aliases(canonical)")
    logger.info("[MIGRATION 246] Base taxonomy layer tables ready.")


def migrate_000_006_239_proposal_source_path(
    conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object
) -> None:
    """Migration 000.006.239: Name the vault note a proposal came from.

    `source_ids` holds `context_entries` ids, so a proposal raised by a vault note carries an
    empty list — a note is not a memory entry. Approving such a term therefore registered the
    word and did nothing else: `backfill_admitted_term` had nothing to put it on, and the note
    that demonstrably concerned the subject stayed unindexed for it. Measured on three terms
    admitted 2026-09-25, the two memory-sourced ones landed on their facts and the one
    vault-sourced one landed on nothing.

    The path existed already, inside the `merged_observation` origin sentence this codebase
    writes itself ("vault note (Some/Path.md)"). Backfilled from it here — acceptable once, in
    a migration, against our own format — so the existing queue becomes actionable rather than
    only proposals raised from now on.
    """
    cursor = conn.cursor()
    cols = {row[1] for row in cursor.execute("PRAGMA table_info(proposals)").fetchall()}
    if "source_path" not in cols:
        cursor.execute("ALTER TABLE proposals ADD COLUMN source_path TEXT")
        logger.info("[MIGRATION 239] Added proposals.source_path.")

    rows = cursor.execute(
        "SELECT id, merged_observation FROM proposals "
        "WHERE COALESCE(merged_observation,'') LIKE 'vault note (%' "
        "AND COALESCE(source_path,'') = ''"
    ).fetchall()
    filled = 0
    for pid, origin in rows:
        m = re.match(r"^vault note \((.+)\)$", str(origin or "").strip())
        if not m:
            continue
        cursor.execute("UPDATE proposals SET source_path = ? WHERE id = ?", (m.group(1), pid))
        filled += 1
    logger.info("[MIGRATION 239] Backfilled source_path on %d of %d vault proposal(s).",
                filled, len(rows))


MIGRATIONS: list[Migration] = [
    Migration(
        target_db="chat",
        version="000.004.000",
        name="baseline_chat_schema",
        up_sql=BASELINE_CHAT_SQL
    ),
    Migration(
        target_db="memory",
        version="000.004.000",
        name="baseline_memory_schema",
        up_sql=BASELINE_MEMORY_SQL
    ),
    Migration(
        target_db="vault",
        version="000.004.000",
        name="baseline_vault_schema",
        up_sql=BASELINE_VAULT_SQL
    ),
    Migration(
        target_db="media",
        version="000.004.000",
        name="baseline_media_schema",
        up_sql=BASELINE_MEDIA_SQL
    ),
    Migration(
        target_db="memory",
        version="000.004.002",
        name="strip_legacy_kw_tags_from_memory",
        up_fn=strip_legacy_kw_tags_from_memory,
    ),
    Migration(
        target_db="chat",
        version="000.005.008",
        name="create_tasks_table",
        up_sql=CREATE_TASKS_TABLE_SQL,
    ),
    Migration(
        target_db="chat",
        version="000.005.010",
        name="create_message_feedback_table",
        up_sql=CREATE_MESSAGE_FEEDBACK_TABLE_SQL,
    ),
    Migration(
        target_db="memory",
        version="000.005.010",
        name="create_rag_retrieval_log_table",
        up_sql=CREATE_RAG_RETRIEVAL_LOG_TABLE_SQL,
    ),
    Migration(
        target_db="memory",
        version="000.005.018",
        name="add_suggested_tools_and_procedure_queues",
        up_fn=migrate_000_005_018_procedures_upgrade,
    ),
    Migration(
        target_db="memory",
        version="000.006.009",
        name="migrate_legacy_subject_codes_in_memory",
        up_fn=migrate_000_006_009_subject_codes_sanitization,
        reindex_vault=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.020",
        name="live_procedures_cleanup_and_fact_migration",
        up_fn=migrate_000_006_020_live_procedures_cleanup,
    ),
    Migration(
        target_db="memory",
        version="000.006.027",
        name="create_entry_document_evolution_table",
        up_fn=migrate_000_006_027_entry_document_evolution,
    ),
    Migration(
        target_db="memory",
        version="000.006.029",
        name="update_master_daily_journaling_procedure",
        up_fn=migrate_000_006_029_persona_agnostic_journaling_procedure,
    ),
    Migration(
        target_db="memory",
        version="000.006.031",
        name="create_daily_ambient_impressions_table",
        up_sql=CREATE_DAILY_AMBIENT_IMPRESSIONS_TABLE_SQL,
    ),
    Migration(
        target_db="chat",
        version="000.006.044",
        name="add_channel_id_to_messages",
        up_sql=MIGRATE_000_006_044_CHAT_CHANNELS_SQL,
    ),
    Migration(
        target_db="memory",
        version="000.006.048",
        name="name_preference_memory_harmonization",
        up_fn=migrate_000_006_048_name_preference_memory,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="chat",
        version="000.006.048",
        name="name_preference_chat_harmonization",
        up_fn=migrate_000_006_048_name_preference_chat,
    ),
    Migration(
        target_db="memory",
        version="000.006.049",
        name="procedure_status_expansion_and_master_journaling",
        up_fn=migrate_000_006_049_procedure_status_expansion_and_master_journaling,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.050",
        name="operational_procedure_consolidation_and_tag_hygiene",
        up_fn=migrate_000_006_050_operational_procedure_consolidation_and_tag_hygiene,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.051",
        name="tool_starter_procedures_and_dynamic_surfacing",
        up_fn=migrate_000_006_051_tool_starter_procedures_and_dynamic_surfacing,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.055",
        name="procedure_master_matches_and_deduplication_parity",
        up_fn=migrate_000_006_055_procedure_master_matches_and_deduplication_parity,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.056",
        name="fact_merge_queue_and_consolidation_parity",
        up_fn=migrate_000_006_056_fact_merge_queue_and_consolidation_parity,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.062",
        name="rewrite_procedure_1034_declarative_phrasing",
        up_fn=migrate_000_006_062_rewrite_procedure_1034_declarative_phrasing,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.063",
        name="standardize_live_procedures_declarative_phrasing",
        up_fn=migrate_000_006_063_standardize_live_procedures_declarative_phrasing,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.064",
        name="sharpen_research_and_technical_procedures",
        up_fn=migrate_000_006_064_sharpen_research_and_technical_procedures,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="vault",
        version="000.006.067",
        name="master_librarian_schema_and_activity_log",
        up_fn=migrate_000_006_067_master_librarian_schema,
    ),
    Migration(
        target_db="memory",
        version="000.006.078",
        name="remediate_fast_memory_taxonomy_and_temporal_anchoring",
        up_fn=migrate_000_006_078_remediate_fast_memory_taxonomy_and_temporal_anchoring,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.081",
        name="starter_procedure_for_read_url",
        up_fn=migrate_000_006_081_starter_procedure_for_read_url,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.085",
        name="harmonize_empathy_terminology_memory",
        up_fn=migrate_000_006_085_harmonize_empathy_terminology_memory,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="chat",
        version="000.006.085",
        name="harmonize_empathy_terminology_chat",
        up_fn=migrate_000_006_085_harmonize_empathy_terminology_chat,
    ),
    Migration(
        target_db="memory",
        version="000.006.086",
        name="prune_and_harmonize_sycophancy_records",
        up_fn=migrate_000_006_086_prune_and_harmonize_sycophancy_records,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.089",
        name="canonical_persona_triad_document_names",
        up_fn=migrate_000_006_089_canonical_persona_triad_document_names,
    ),
    Migration(
        target_db="memory",
        version="000.006.105",
        name="starter_procedure_search_reference_library",
        up_fn=migrate_000_006_105_search_reference_library_procedure,
    ),
    Migration(
        target_db="memory",
        version="000.006.113",
        name="starter_procedure_search_vault_notes",
        up_fn=migrate_000_006_113_search_vault_notes_procedure,
    ),
    Migration(
        target_db="memory",
        version="000.006.121",
        name="starter_procedure_search_available_tools",
        up_fn=migrate_000_006_121_search_available_tools_procedure,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.124",
        name="context_entries_provenance_and_audit_lineage",
        up_fn=migrate_000_006_124_context_entries_provenance_and_audit_lineage,
    ),
    Migration(
        target_db="vault",
        version="000.006.136",
        name="vault_documents_semantic_tag_audit_column",
        up_fn=migrate_000_006_136_semantic_tag_column,
    ),
    Migration(
        target_db="chat",
        version="000.006.140",
        name="messages_structured_reasoning_trace_column",
        up_sql=MIGRATE_000_006_140_MESSAGE_TRACE_SQL,
    ),
    Migration(
        target_db="memory",
        version="000.006.147",
        name="tag_format_unification_memory",
        up_fn=migrate_000_006_147_tag_format_unification_memory,
    ),
    Migration(
        target_db="vault",
        version="000.006.148",
        name="tag_format_unification_vault",
        up_fn=migrate_000_006_148_tag_format_unification_vault,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="vault",
        version="000.006.149",
        name="namespace_retirement_vault",
        up_fn=migrate_000_006_149_namespace_retirement_vault,
    ),
    Migration(
        target_db="memory",
        version="000.006.151",
        name="drop_subject_duplicate_tags",
        up_fn=migrate_000_006_151_drop_subject_duplicate_tags,
    ),
    Migration(
        target_db="vault",
        version="000.006.152",
        name="drop_redundant_entity_tags",
        up_fn=migrate_000_006_152_drop_redundant_entity_tags,
    ),
    Migration(
        target_db="vault",
        version="000.006.153",
        name="master_tag_aliases_table",
        up_sql=MIGRATE_000_006_153_TAG_ALIASES_SQL,
    ),
    Migration(
        target_db="vault",
        version="000.006.154",
        name="collapse_lexical_synonyms_vault",
        up_fn=migrate_000_006_154_collapse_lexical_synonyms_vault,
    ),
    Migration(
        target_db="memory",
        version="000.006.155",
        name="collapse_lexical_synonyms_memory",
        up_fn=migrate_000_006_155_collapse_lexical_synonyms_memory,
    ),
    Migration(
        target_db="vault",
        version="000.006.161",
        name="apply_reviewed_merges_vault",
        up_fn=migrate_000_006_161_apply_reviewed_merges_vault,
    ),
    Migration(
        target_db="memory",
        version="000.006.162",
        name="apply_reviewed_merges_memory",
        up_fn=migrate_000_006_162_apply_reviewed_merges_memory,
    ),
    Migration(
        target_db="vault",
        version="000.006.166",
        name="root_consolidation_vault",
        up_fn=migrate_000_006_166_root_consolidation_vault,
    ),
    Migration(
        target_db="memory",
        version="000.006.167",
        name="root_consolidation_memory",
        up_fn=migrate_000_006_167_root_consolidation_memory,
    ),
    Migration(
        target_db="vault",
        version="000.006.168",
        name="adopt_flat_compounds_vault",
        up_fn=migrate_000_006_168_adopt_flat_compounds_vault,
    ),
    Migration(
        target_db="memory",
        version="000.006.169",
        name="adopt_flat_compounds_memory",
        up_fn=migrate_000_006_169_adopt_flat_compounds_memory,
    ),
    Migration(
        target_db="vault",
        version="000.006.170",
        name="second_pass_merges_vault",
        up_fn=migrate_000_006_170_second_pass_merges_vault,
    ),
    Migration(
        target_db="memory",
        version="000.006.171",
        name="second_pass_merges_memory",
        up_fn=migrate_000_006_171_second_pass_merges_memory,
    ),
    Migration(
        target_db="vault",
        version="000.006.173",
        name="retire_phrase_tags_vault",
        up_fn=migrate_000_006_173_retire_phrase_tags_vault,
    ),
    Migration(
        target_db="memory",
        version="000.006.174",
        name="retire_phrase_tags_memory",
        up_fn=migrate_000_006_174_retire_phrase_tags_memory,
    ),
    Migration(
        target_db="vault",
        version="000.006.181",
        name="decompose_to_atoms_vault",
        up_fn=migrate_000_006_181_decompose_to_atoms_vault,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.182",
        name="decompose_to_atoms_memory",
        up_fn=migrate_000_006_182_decompose_to_atoms_memory,
    ),
    Migration(
        target_db="vault",
        version="000.006.183",
        name="decompose_flat_compounds_vault",
        up_fn=migrate_000_006_183_decompose_flat_compounds_vault,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.184",
        name="decompose_flat_compounds_memory",
        up_fn=migrate_000_006_184_decompose_flat_compounds_memory,
    ),
    Migration(
        target_db="vault",
        version="000.006.186",
        name="reset_subject_tags_vault",
        up_fn=migrate_000_006_186_reset_subject_tags_vault,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.187",
        name="reset_subject_tags_memory",
        up_fn=migrate_000_006_187_reset_subject_tags_memory,
    ),
    Migration(
        target_db="vault",
        version="000.006.189",
        name="normalize_mood_property",
        up_fn=migrate_000_006_189_normalize_mood_property,
    ),
    Migration(
        target_db="vault",
        version="000.006.190",
        name="tag_reference_library_by_book",
        up_fn=migrate_000_006_190_tag_reference_library_by_book,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="vault",
        version="000.006.191",
        name="seed_personal_vocabulary",
        up_fn=migrate_000_006_191_seed_personal_vocabulary,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="vault",
        version="000.006.192",
        name="seed_vocabulary_gaps",
        up_fn=migrate_000_006_192_seed_vocabulary_gaps,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="vault",
        version="000.006.194",
        name="register_pass1_vocabulary",
        up_fn=migrate_000_006_194_register_pass1_vocabulary,
        post_sync_chroma=True,
    ),
    Migration(
        target_db="vault",
        version="000.006.195",
        name="register_media_subtypes",
        up_fn=migrate_000_006_195_register_media_subtypes,
        post_sync_chroma=False,
    ),
    Migration(
        target_db="vault",
        version="000.006.196",
        name="protect_curated_taxonomy",
        up_fn=migrate_000_006_196_protect_curated_taxonomy,
        # The Chroma hook was a no-op until this release, so 350 of 671 registered terms
        # — every faceted value among them — were never embedded. Re-run it here.
        post_sync_chroma=True,
    ),
    Migration(
        target_db="vault",
        version="000.006.216",
        name="tag_relations",
        up_fn=migrate_000_006_216_tag_relations,
        post_sync_chroma=False,
    ),
    Migration(
        target_db="vault",
        version="000.006.217",
        name="relation_kind",
        up_fn=migrate_000_006_217_relation_kind,
        post_sync_chroma=False,
    ),
    Migration(
        target_db="vault",
        version="000.006.218",
        name="drop_derived_categories",
        up_fn=migrate_000_006_218_drop_derived_categories,
        # The vector copy carries category in its metadata, so it has to be re-emitted.
        post_sync_chroma=True,
    ),
    Migration(
        target_db="memory",
        version="000.006.229",
        name="memory_tag_audit_cursor",
        up_fn=migrate_000_006_229_memory_tag_audit_cursor,
        post_sync_chroma=False,
    ),
    Migration(
        target_db="memory",
        version="000.006.231",
        name="proposal_rejection_count",
        up_fn=migrate_000_006_231_proposal_rejection_count,
        post_sync_chroma=False,
    ),
    Migration(
        target_db="memory",
        version="000.006.234",
        name="proposal_evidence",
        up_fn=migrate_000_006_234_proposal_evidence,
        post_sync_chroma=False,
    ),
    Migration(
        target_db="memory",
        version="000.006.239",
        name="proposal_source_path",
        up_fn=migrate_000_006_239_proposal_source_path,
        post_sync_chroma=False,
    ),
    Migration(
        target_db="vault",
        version="000.006.246",
        name="base_taxonomy_layer",
        up_fn=migrate_000_006_246_base_taxonomy_layer,
        post_sync_chroma=False,
    ),
    Migration(
        target_db="vault",
        version="000.006.250",
        name="tag_entities",
        up_fn=migrate_000_006_250_tag_entities,
        post_sync_chroma=False,
    ),
]


# ============================================================================
# Migration Engine Core Functions
# ============================================================================

def ensure_backup_dir() -> str:
    """Create the backup directory if it does not exist and return its path."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    return BACKUP_DIR


def ensure_tracking_table(db_path: str) -> None:
    """Ensure the schema_migrations table exists inside the target database."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version           TEXT PRIMARY KEY,
                name              TEXT NOT NULL,
                applied_at        TEXT NOT NULL,
                execution_time_ms INTEGER NOT NULL,
                status            TEXT NOT NULL
            );
        """)
        conn.commit()
    finally:
        conn.close()


def get_applied_migrations(db_path: str) -> dict[str, dict]:
    """Retrieve all applied migrations from the target database's tracking table."""
    if not os.path.exists(db_path):
        return {}
    ensure_tracking_table(db_path)
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        rows = cursor.execute("SELECT version, name, applied_at, execution_time_ms, status FROM schema_migrations ORDER BY version ASC").fetchall()
        return {row["version"]: dict(row) for row in rows}
    finally:
        conn.close()


def get_db_version(db_name: str) -> str | None:
    """Return the highest applied version string for a named database, or None if empty."""
    db_path = DB_MAP.get(db_name)
    if not db_path or not os.path.exists(db_path):
        return None
    applied = get_applied_migrations(db_path)
    if not applied:
        return None
    sorted_versions = sorted(applied.keys(), key=lambda v: normalize_version(v))
    return sorted_versions[-1]


def create_db_snapshot(db_name: str, target_version: str) -> str:
    """
    Create a pre-migration safety backup of a database file.
    Returns the absolute path to the backup file.
    """
    db_path = DB_MAP.get(db_name)
    if not db_path or not os.path.exists(db_path):
        return ""
    ensure_backup_dir()
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_filename = f"{os.path.basename(db_path)}_pre_{target_version}_{timestamp}.bak"
    backup_path = os.path.join(BACKUP_DIR, backup_filename)
    shutil.copy2(db_path, backup_path)
    return backup_path


def check_all_dbs_status(target_version: str | None = None) -> dict[str, dict]:
    """
    Inspect all registered databases and return current version, target version,
    and pending migrations for each.
    """
    target = normalize_version(target_version) if target_version else __version__
    status_report: dict[str, dict] = {}

    for db_name, db_path in DB_MAP.items():
        applied = get_applied_migrations(db_path) if os.path.exists(db_path) else {}
        db_migrations = [m for m in MIGRATIONS if m.target_db == db_name and compare_versions(m.version, target) <= 0]
        pending = [m for m in db_migrations if m.version not in applied]

        current_v = get_db_version(db_name)
        is_up_to_date = len(pending) == 0 and (len(db_migrations) == 0 or current_v is not None)

        status_report[db_name] = {
            "db_path": db_path,
            "exists": os.path.exists(db_path),
            "current_version": current_v or "000.000.000 (none)",
            "target_version": target,
            "applied_count": len(applied),
            "pending_count": len(pending),
            "pending_migrations": [m.version for m in pending],
            "is_up_to_date": is_up_to_date,
        }

    return status_report


def validate_db_schemas_or_raise() -> None:
    """
    Verify all database schemas are up to date with the engine version.
    Raises DatabaseSchemaMismatchError if any database requires migration.
    """
    statuses = check_all_dbs_status()
    mismatches = []
    for db_name, info in statuses.items():
        if not info["is_up_to_date"]:
            mismatches.append(
                f"- {db_name} ({info['db_path']}): current={info['current_version']}, target={info['target_version']}, pending={info['pending_count']}"
            )

    if mismatches:
        mismatch_str = "\n".join(mismatches)
        raise DatabaseSchemaMismatchError(
            f"\n[CRITICAL] Database schema version mismatch detected:\n{mismatch_str}\n"
            f"The application version is {__version__}. Please run database migrations before starting the engine:\n"
            f"  python scripts/migrate_db.py --execute\n"
        )


def execute_post_hooks(migration: Migration) -> None:
    """Execute post-migration triggers such as vector synchronization or vault re-indexing."""
    if migration.post_sync_chroma:
        print(f"[DB Migrator] Triggering post-migration Chroma sync hook for {migration.version}...")
        try:
            from Evelyn.tools.tag_librarian import sync_master_tags_to_vector_db
            enqueued = sync_master_tags_to_vector_db()
            print(f"[DB Migrator] Chroma sync hook completed ({enqueued} surface forms enqueued).")
        except (sqlite3.Error, OSError, RuntimeError, ValueError, ImportError) as e:
            print(f"[DB Migrator] [WARNING] Post-migration Chroma hook warning: {e}")

    if migration.reindex_vault:
        print(f"[DB Migrator] Triggering post-migration Vault reindex hook for {migration.version}...")
        try:
            from Evelyn.tools.vault_indexer import scan_vault
            scan_vault()
            print("[DB Migrator] Vault reindex hook completed.")
        except (sqlite3.Error, OSError, RuntimeError, ValueError, ImportError) as e:
            print(f"[DB Migrator] [WARNING] Post-migration Vault reindex hook warning: {e}")


def apply_pending_migrations(
    target_db: str | None = None,
    target_version: str | None = None,
    dry_run: bool = False,
    create_snapshots: bool = True
) -> list[dict]:
    """
    Apply all pending migrations up to target_version (or __version__).
    Returns a list of executed migration summaries.
    """
    target = normalize_version(target_version) if target_version else __version__
    executed_records: list[dict] = []

    # Filter migrations matching target DB and target version
    candidate_migrations = [
        m for m in MIGRATIONS
        if (target_db is None or m.target_db == target_db) and compare_versions(m.version, target) <= 0
    ]
    candidate_migrations.sort(key=lambda m: (normalize_version(m.version), m.target_db))

    for migration in candidate_migrations:
        db_path = DB_MAP.get(migration.target_db)
        if not db_path:
            continue

        ensure_tracking_table(db_path)
        applied = get_applied_migrations(db_path)
        if migration.version in applied:
            continue

        print(f"[DB Migrator] Discovered pending migration: [{migration.target_db}] v{migration.version} - {migration.name}")
        if dry_run:
            executed_records.append({
                "target_db": migration.target_db,
                "version": migration.version,
                "name": migration.name,
                "status": "dry_run"
            })
            continue

        # Create pre-migration backup
        backup_path = ""
        if create_snapshots and os.path.exists(db_path):
            backup_path = create_db_snapshot(migration.target_db, migration.version)
            if backup_path:
                print(f"[DB Migrator] Created safety snapshot: {backup_path}")

        start_time = time.perf_counter()
        conn = sqlite3.connect(db_path, timeout=30.0)
        try:
            conn.execute("BEGIN IMMEDIATE")

            # Execute SQL DDL if present
            if migration.up_sql:
                conn.executescript(migration.up_sql)

            # Execute Python transform callable if present
            if migration.up_fn:
                migration.up_fn(conn, DB_MAP, cfg)

            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            applied_at = datetime.now(UTC).isoformat()

            conn.execute("""
                INSERT OR REPLACE INTO schema_migrations (version, name, applied_at, execution_time_ms, status)
                VALUES (?, ?, ?, ?, 'success')
            """, (migration.version, migration.name, applied_at, elapsed_ms))

            conn.commit()
            print(f"[DB Migrator] Successfully applied [{migration.target_db}] v{migration.version} in {elapsed_ms}ms.")

            # Run non-SQLite post hooks
            execute_post_hooks(migration)

            executed_records.append({
                "target_db": migration.target_db,
                "version": migration.version,
                "name": migration.name,
                "backup_path": backup_path,
                "execution_time_ms": elapsed_ms,
                "status": "success"
            })

        except Exception as e:
            conn.rollback()
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            print(f"[DB Migrator] [ERROR] Migration failed for [{migration.target_db}] v{migration.version}: {e}")
            raise MigrationExecutionError(
                f"Failed to execute migration {migration.version} ({migration.name}) on {migration.target_db}: {e}\n"
                f"Safety snapshot preserved at: {backup_path}"
            ) from e
        finally:
            conn.close()

    return executed_records


def rollback_db(db_name: str, backup_file: str) -> None:
    """Restore a database from a specific pre-migration backup file."""
    db_path = DB_MAP.get(db_name)
    if not db_path:
        raise ValueError(f"Unknown database name: {db_name}")
    if not os.path.exists(backup_file):
        raise FileNotFoundError(f"Backup file not found: {backup_file}")

    print(f"[DB Migrator] Rolling back {db_name} from {backup_file} -> {db_path}...")
    shutil.copy2(backup_file, db_path)
    print("[DB Migrator] Rollback complete.")
