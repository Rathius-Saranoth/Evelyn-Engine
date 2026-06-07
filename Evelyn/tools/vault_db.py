# vault_db.py
# date created: 2026-05-24 17:44:20
# date modified: 2026-06-07 10:29:14
# tags: #vault, #database, #sqlite, #indexing, #filesystem

"""
vault_db.py - SQLite interface for the Obsidian Vault Map

Stores metadata and LLM-generated gists for every markdown file in the vault.
Replaces the flat vault_map_data.json file to enable partial updates and fast querying.
"""
import sqlite3
import os
import time
from typing import Optional, List, Dict, Any

DB_PATH = r"C:\Projects\LocalAI\data\evelyn_vault.db"

def get_db() -> sqlite3.Connection:
    """Return a new SQLite connection to the vault database with row_factory set.

    Returns:
        sqlite3.Connection: A database connection.
    """
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
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
            gist_failed BOOLEAN,
            rag_priority TEXT,
            rag_pinned BOOLEAN,
            tags TEXT,
            aliases TEXT,
            indexed_at REAL
        );
    """)
    con.commit()
    con.close()

def upsert_document(
    path: str, title: str, mtime: float, gist: str, 
    gist_failed: bool, rag_priority: str, rag_pinned: bool,
    tags: str, aliases: str
) -> None:
    """Insert or update a document in the vault map.

    Args:
        path: Relative or absolute path of the document.
        title: Title of the document.
        mtime: Modification time.
        gist: The summary/gist text.
        gist_failed: Whether gist generation failed.
        rag_priority: 'high', 'normal', or 'low'.
        rag_pinned: Whether document is pinned in RAG context.
        tags: Comma-separated tag string.
        aliases: Comma-separated aliases string.
    """
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

def get_failed_gists() -> List[Dict[str, Any]]:
    """Return metadata dicts for all indexed documents that failed to generate an LLM gist.

    Returns:
        List[Dict[str, Any]]: A list of metadata dicts for documents with failed gists.
    """
    con = get_db()
    rows = con.execute("SELECT * FROM vault_documents WHERE gist_failed = 1").fetchall()
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
        gist = (row["gist"] or "").lower()
        
        score = 0
        if query_lower in title: score += 10
        if query_lower in tags: score += 5
        if query_lower in gist: score += 2
        
        if score > 0:
            results.append({
                "path": row["path"],
                "title": row["title"],
                "score": score,
                "tags": [t.strip() for t in row["tags"].split(",")] if row["tags"] else [],
                "gist": row["gist"]
            })
            
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]
