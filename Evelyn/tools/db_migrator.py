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
from datetime import datetime, timezone

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
    with sqlite3.connect(db_path) as conn:
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


def get_applied_migrations(db_path: str) -> dict[str, dict]:
    """Retrieve all applied migrations from the target database's tracking table."""
    if not os.path.exists(db_path):
        return {}
    ensure_tracking_table(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        rows = cursor.execute("SELECT version, name, applied_at, execution_time_ms, status FROM schema_migrations ORDER BY version ASC").fetchall()
        return {row["version"]: dict(row) for row in rows}


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
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
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
        is_up_to_date = len(pending) == 0 and (current_v is not None and compare_versions(current_v, target) >= 0 or len(db_migrations) == 0)

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
        except Exception as e:
            print(f"[DB Migrator] [WARNING] Post-migration Chroma hook warning: {e}")

    if migration.reindex_vault:
        print(f"[DB Migrator] Triggering post-migration Vault reindex hook for {migration.version}...")
        try:
            from Evelyn.tools.vault_indexer import VaultIndexer
            indexer = VaultIndexer()
            indexer.scan_and_index()
            print("[DB Migrator] Vault reindex hook completed.")
        except Exception as e:
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
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                
                # Execute SQL DDL if present
                if migration.up_sql:
                    conn.executescript(migration.up_sql)
                
                # Execute Python transform callable if present
                if migration.up_fn:
                    migration.up_fn(conn, DB_MAP, cfg)

                elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                applied_at = datetime.now(timezone.utc).isoformat()

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
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            print(f"[DB Migrator] [ERROR] Migration failed for [{migration.target_db}] v{migration.version}: {e}")
            raise MigrationExecutionError(
                f"Failed to execute migration {migration.version} ({migration.name}) on {migration.target_db}: {e}\n"
                f"Safety snapshot preserved at: {backup_path}"
            ) from e

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
