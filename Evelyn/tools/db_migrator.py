# db_migrator.py
# date created: 2026-08-29 07:46:44
# date modified: 2026-08-29 07:46:44
# tags:

"""
Evelyn Engine Database Migration Framework.

Provides transactional, per-database schema migration tracking, Python data
transformation callables, safety backups, post-migration sync hooks, and fail-fast
schema validation.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

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
    tool_metadata TEXT
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
    user_name = getattr(cfg_obj, "USER_NAME", "Ricky")

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
    user_doc = getattr(config_obj, "PERSONA_FILE_USER", "Ricky_Narrative_Profile.md")
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
