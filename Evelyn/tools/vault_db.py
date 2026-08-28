# vault_db.py
# date created: 2026-05-24 17:44:20
# date modified: 2026-08-28 11:41:29
# tags: #vault, #database, #sqlite, #indexing, #filesystem

"""
vault_db.py - SQLite interface for the Obsidian Vault Map

Stores metadata, tags, and text preview snippets for every markdown file in the vault.
Enables fast querying and structural vault inspection without full disk walks.
"""
import sqlite3
import os
import time
from typing import Optional, List, Dict, Any
import evelyn_config as cfg

DB_PATH = getattr(cfg, "VAULT_DB_PATH", r"/home/rathius/evelyn/data/evelyn_vault.db")


def get_db() -> sqlite3.Connection:
    """Return a new SQLite connection to the vault database with row_factory set.

    Configured with WAL mode and busy timeout to prevent lock contention.

    Returns:
        sqlite3.Connection: A database connection.
    """
    con = sqlite3.connect(DB_PATH, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def init_db() -> None:
    """Create the initial table schema.

    Returns:
        None
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = get_db()
    # path is the relative path from the vault root
    con.executescript("""
        CREATE TABLE IF NOT EXISTS vault_documents (
            path TEXT PRIMARY KEY,
            title TEXT,
            mtime REAL,
            gist TEXT,
            gist_failed BOOLEAN DEFAULT 0,
            rag_priority TEXT,
            rag_pinned BOOLEAN,
            tags TEXT,
            aliases TEXT,
            indexed_at REAL,
            last_tag_audit REAL
        );

        CREATE TABLE IF NOT EXISTS master_tag_taxonomy (
            tag TEXT PRIMARY KEY,
            category TEXT,
            description TEXT,
            usage_count INTEGER DEFAULT 0,
            created_at REAL,
            updated_at REAL
        );
    """)
    # Migration check: Ensure last_tag_audit column exists if table was created previously
    try:
        con.execute("ALTER TABLE vault_documents ADD COLUMN last_tag_audit REAL")
    except sqlite3.OperationalError:
        pass  # Column already exists
    con.commit()
    con.close()


def upsert_document(
    path: str, title: str, mtime: float, gist: str = "", 
    gist_failed: bool = False, rag_priority: str = "normal", rag_pinned: bool = False,
    tags: str = "", aliases: str = ""
) -> None:
    """Insert or update a document in the vault map.

    Args:
        path: Relative or absolute path of the document.
        title: Title of the document.
        mtime: Modification time.
        gist: The text preview snippet.
        gist_failed: Unused legacy flag (defaults to False).
        rag_priority: 'high', 'normal', or 'low'.
        rag_pinned: Whether document is pinned in RAG context.
        tags: Comma-separated tag string.
        aliases: Comma-separated aliases string.
    """
    path = path.replace('\\', '/')
    con = get_db()
    con.execute("""
        INSERT INTO vault_documents 
        (path, title, mtime, gist, gist_failed, rag_priority, rag_pinned, tags, aliases, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            title = excluded.title,
            mtime = excluded.mtime,
            gist = excluded.gist,
            gist_failed = excluded.gist_failed,
            rag_priority = excluded.rag_priority,
            rag_pinned = excluded.rag_pinned,
            tags = excluded.tags,
            aliases = excluded.aliases,
            indexed_at = excluded.indexed_at
    """, (path, title, mtime, gist, gist_failed, rag_priority, rag_pinned, tags, aliases, time.time()))
    con.commit()
    con.close()


def get_document(path: str) -> Optional[Dict[str, Any]]:
    """Retrieve a single document's metadata from the vault map by its relative path.

    Args:
        path: Relative path of the document from the vault root.

    Returns:
        Dict of metadata fields, or None if the document is not indexed.
    """
    path = path.replace('\\', '/')
    con = get_db()
    row = con.execute("SELECT * FROM vault_documents WHERE path = ?", (path,)).fetchone()
    con.close()
    return dict(row) if row else None


def get_all_documents() -> List[Dict[str, Any]]:
    """Return metadata dicts for all documents currently indexed in the vault.

    Returns:
        List[Dict[str, Any]]: A list of all document metadata dicts.
    """
    con = get_db()
    rows = con.execute("SELECT * FROM vault_documents").fetchall()
    con.close()
    return [dict(r) for r in rows]


def delete_document(path: str) -> None:
    """Delete a document's indexed metadata from the vault map database.

    Args:
        path: Relative or absolute path of the document to delete.
    """
    con = get_db()
    con.execute("DELETE FROM vault_documents WHERE path = ?", (path,))
    con.commit()
    con.close()


def search_documents(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Scores and returns documents matching a keyword query.

    Args:
        query: The keyword search query.
        limit: The maximum number of results to return. Default is 5.

    Returns:
        List[Dict[str, Any]]: A list of matching document dictionaries.
    """
    con = get_db()
    rows = con.execute("SELECT * FROM vault_documents").fetchall()
    con.close()
    
    query_lower = query.lower()
    results = []
    
    for row in rows:
        title = (row["title"] or "").lower()
        tags = (row["tags"] or "").lower()
        snippet = (row["gist"] or "").lower()
        
        score = 0
        if query_lower in title: score += 10
        if query_lower in tags: score += 5
        if query_lower in snippet: score += 2
        
        if score > 0:
            results.append({
                "path": row["path"],
                "title": row["title"],
                "score": score,
                "tags": [t.strip() for t in row["tags"].split(",")] if row["tags"] else [],
                "snippet": row["gist"]
            })
            
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


# =============================================================================
# Tag Librarian Database Operations
# =============================================================================

def get_master_tags() -> List[Dict[str, Any]]:
    """Return all active master tags with their categories, descriptions, and usage counts.

    Prioritizes high-frequency tags first (usage_count DESC).

    Returns:
        List[Dict[str, Any]]: A list of master tag dictionaries.
    """
    init_db()
    con = get_db()
    rows = con.execute("SELECT * FROM master_tag_taxonomy ORDER BY usage_count DESC, category ASC, tag ASC").fetchall()
    con.close()
    return [dict(r) for r in rows]


def upsert_master_tag(tag: str, category: str = "", description: str = "", usage_count: int = 0) -> None:
    """Insert or update a master tag entry in the taxonomy table.

    Args:
        tag: The tag string (e.g. 'tech/python').
        category: Top-level category name (e.g. 'tech').
        description: Short 1-sentence scope statement.
        usage_count: Current count of notes using this tag.
    """
    init_db()
    con = get_db()
    now = time.time()
    con.execute("""
        INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(tag) DO UPDATE SET
            category = excluded.category,
            description = CASE WHEN excluded.description != '' THEN excluded.description ELSE master_tag_taxonomy.description END,
            usage_count = excluded.usage_count,
            updated_at = excluded.updated_at
    """, (tag, category, description, usage_count, now, now))
    con.commit()
    con.close()

def delete_master_tag(tag: str) -> None:
    """Delete a tag from the master taxonomy table.

    Args:
        tag: The tag string to remove.
    """
    init_db()
    con = get_db()
    con.execute("DELETE FROM master_tag_taxonomy WHERE tag = ?", (tag,))
    con.commit()
    con.close()

def fetch_next_document_for_tag_audit() -> Optional[Dict[str, Any]]:
    """Fetch the next vault document eligible for tag auditing.

    Priority Tiers:
        1. Un-audited documents with no tags (missing tags entirely)
        2. Un-audited documents with multi-dash flat tags (e.g. 'bad-coding-habits')
        3. Un-audited documents with simple flat tags (no hierarchy slashes)
        4. Other un-audited documents (e.g. existing nested tags)
        5. Routine rotation of previously audited documents (oldest last_tag_audit first)

    Excluded documents (via TAG_LIBRARIAN_EXCLUDED_DOCUMENTS) are strictly omitted.

    Returns:
        Optional[Dict[str, Any]]: Document metadata dict or None if vault is empty.
    """
    init_db()
    con = get_db()
    excluded_paths = getattr(cfg, "TAG_LIBRARIAN_EXCLUDED_DOCUMENTS", [])
    
    where_clause = ""
    params = []
    if excluded_paths:
        placeholders = ", ".join(["?"] * len(excluded_paths))
        where_clause = f"WHERE path NOT IN ({placeholders})"
        params = list(excluded_paths)

    query = f"""
        SELECT * FROM vault_documents
        {where_clause}
        ORDER BY
            -- Prioritize un-audited docs over audited docs
            CASE WHEN last_tag_audit IS NULL OR last_tag_audit = 0 THEN 0 ELSE 1 END ASC,
            -- Tiered urgency among un-audited docs
            CASE 
                -- Tier 1: No tags at all
                WHEN (last_tag_audit IS NULL OR last_tag_audit = 0) AND (tags IS NULL OR trim(tags) = '' OR trim(tags) = '[]') THEN 1
                -- Tier 2: Multi-dash flat tags
                WHEN (last_tag_audit IS NULL OR last_tag_audit = 0) AND (tags LIKE '%-%') AND (tags NOT LIKE '%/%') THEN 2
                -- Tier 3: Simple flat tags without slashes
                WHEN (last_tag_audit IS NULL OR last_tag_audit = 0) AND (tags NOT LIKE '%/%') THEN 3
                -- Tier 4: Un-audited documents with existing hierarchy
                WHEN (last_tag_audit IS NULL OR last_tag_audit = 0) THEN 4
                -- Tier 5: Audited documents
                ELSE 5
            END ASC,
            last_tag_audit ASC,
            mtime DESC
        LIMIT 1
    """
    row = con.execute(query, params).fetchone()
    con.close()
    return dict(row) if row else None


def update_document_tag_audit(path: str, tags: Optional[str] = None) -> None:
    """Update the last_tag_audit timestamp (and optionally tags) for a vault document.

    Args:
        path: Relative or absolute path of the document.
        tags: Optional updated comma-separated tags string.
    """
    vault_base = getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
    norm_path = path.replace('\\', '/')
    norm_vault = vault_base.replace('\\', '/').rstrip('/')
    if norm_path.startswith(norm_vault + "/"):
        norm_path = norm_path[len(norm_vault) + 1:]

    init_db()
    con = get_db()
    now = time.time()
    if tags is not None:
        con.execute(
            "UPDATE vault_documents SET last_tag_audit = ?, tags = ? WHERE path = ?",
            (now, tags, norm_path)
        )
    else:
        con.execute(
            "UPDATE vault_documents SET last_tag_audit = ? WHERE path = ?",
            (now, norm_path)
        )
    con.commit()
    con.close()


def move_document(old_path: str, new_path: str) -> bool:
    """Atomically update a document's relative path in the vault map on rename/move.

    Preserves all existing metadata, gists, tags, aliases, and audit timestamps.

    Args:
        old_path: Original relative path of the document.
        new_path: New relative path of the document.

    Returns:
        bool: True if an existing document was updated, False otherwise.
    """
    old_norm = old_path.replace('\\', '/')
    new_norm = new_path.replace('\\', '/')
    if old_norm == new_norm:
        return True

    init_db()
    con = get_db()
    # Remove any stale record at destination if present
    con.execute("DELETE FROM vault_documents WHERE path = ?", (new_norm,))
    cursor = con.execute("UPDATE vault_documents SET path = ? WHERE path = ?", (new_norm, old_norm))
    updated = cursor.rowcount > 0
    con.commit()
    con.close()
    return updated


def get_all_entities() -> List[Dict[str, Any]]:
    """Return all known vault note titles and aliases for entity linking.

    Returns:
        List[Dict[str, Any]]: List of dicts containing 'path', 'title', and 'aliases' (list of strings).
    """
    init_db()
    con = get_db()
    rows = con.execute("SELECT path, title, aliases FROM vault_documents WHERE title IS NOT NULL AND title != ''").fetchall()
    con.close()
    entities = []
    for r in rows:
        title = r["title"].strip()
        aliases_raw = r["aliases"] or ""
        aliases = [a.strip() for a in aliases_raw.split(",") if a.strip()]
        entities.append({
            "path": r["path"],
            "title": title,
            "aliases": aliases
        })
    return entities

