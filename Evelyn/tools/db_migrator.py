# db_migrator.py
# date created: 2026-08-29 07:46:44
# date modified: 2026-09-04 17:44:24
# tags: 

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

# Master Migration Registry
def strip_legacy_kw_tags_from_memory(conn: sqlite3.Connection, db_paths: dict[str, str], cfg: object) -> None:
    """Migration 000.004.002: Sanitize legacy kw/ and ctx/ noise prefixes from context_entries and proposals."""
    from Evelyn.tools.tag_librarian import normalize_tag_format

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
            f"{user_name}'s daughter is named Schyler (specifically spelled 'Schyler', not 'Skyler' or 'Schuyler').",
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
            print("[DB Migrator] Chroma sync hook completed.")
        except (sqlite3.Error, OSError, RuntimeError, ValueError) as e:
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
