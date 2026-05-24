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

def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    """Create the initial table schema."""
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
):
    """Insert or update a document in the vault map."""
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
    con = get_db()
    row = con.execute("SELECT * FROM vault_documents WHERE path = ?", (path,)).fetchone()
    con.close()
    return dict(row) if row else None

def get_all_documents() -> List[Dict[str, Any]]:
    con = get_db()
    rows = con.execute("SELECT * FROM vault_documents").fetchall()
    con.close()
    return [dict(r) for r in rows]

def get_failed_gists() -> List[Dict[str, Any]]:
    con = get_db()
    rows = con.execute("SELECT * FROM vault_documents WHERE gist_failed = 1").fetchall()
    con.close()
    return [dict(r) for r in rows]

def delete_document(path: str):
    con = get_db()
    con.execute("DELETE FROM vault_documents WHERE path = ?", (path,))
    con.commit()
    con.close()

def search_documents(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Scores and returns documents matching a keyword query.
    Same heuristic logic previously found in context_manager.py.
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
