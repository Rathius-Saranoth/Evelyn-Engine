#!/usr/bin/env python3
# sqlite_mcp_server.py
# date created: 2026-08-28 12:29:50
# date modified: 2026-09-01 21:46:05
# tags: 

"""
sqlite_mcp_server.py — Comprehensive MCP Server for Evelyn's Databases, Chroma Vectors, & FastAPI Services.

Exposes standard MCP tools over stdio:
  SQLite:
    - list_databases
    - list_tables
    - describe_table
    - query_database
  ChromaDB:
    - list_chroma_collections
    - query_chroma
    - get_chroma_status
  FastAPI / Services:
    - get_server_status
    - get_heavy_tasks
    - get_pending_reviews
    - get_ollama_status
"""

import asyncio
import json
import os
import sqlite3
import ssl
import urllib.error
import urllib.request
from typing import Any

from mcp.server.mcpserver import MCPServer

from Evelyn.tools.ollama_client import get_ollama_status as fetch_ollama_status

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
CHROMA_DIR = os.path.join(DATA_DIR, "chroma_db")
CHROMA_LOCK = os.path.join(CHROMA_DIR, ".chroma_write.lock")

DB_MAP = {
    "chat": os.path.join(DATA_DIR, "evelyn_chat.db"),
    "memory": os.path.join(DATA_DIR, "evelyn_memory.db"),
    "vault": os.path.join(DATA_DIR, "evelyn_vault.db"),
    "media": os.path.join(DATA_DIR, "evelyn_media.db"),
    "health": os.path.join(DATA_DIR, "health", "health_connect.db"),
}

SERVER_URL = "https://localhost:7860"
API_KEY = os.environ.get("EVELYN_API_KEY", "evelyn-secret-key")

server = MCPServer("evelyn-sqlite")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def resolve_db_path(db_name_or_path: str) -> str:
    """Resolve database key or path safely."""
    key = db_name_or_path.lower().strip()
    if key in DB_MAP:
        return DB_MAP[key]

    base_key = key.replace(".db", "")
    if base_key in DB_MAP:
        return DB_MAP[base_key]

    if os.path.isabs(db_name_or_path) and os.path.exists(db_name_or_path):
        return db_name_or_path

    candidate = os.path.join(DATA_DIR, db_name_or_path)
    if os.path.exists(candidate):
        return candidate

    candidate_db = os.path.join(DATA_DIR, f"{db_name_or_path}.db")
    if os.path.exists(candidate_db):
        return candidate_db

    raise ValueError(
        f"Unknown database: '{db_name_or_path}'. Available aliases: {list(DB_MAP.keys())}"
    )


def http_get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """Perform synchronous HTTP GET request with SSL verification disabled for local self-signed certs."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": f"HTTP {e.code}: {e.reason}", "body": body}
    except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError, json.JSONDecodeError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# SQLite Tools
# ---------------------------------------------------------------------------

@server.tool()
def list_databases() -> str:
    """List all available Evelyn SQLite databases (chat, memory, vault, health) with file paths and sizes in MB."""
    results = []
    for alias, path in DB_MAP.items():
        exists = os.path.exists(path)
        size_mb = round(os.path.getsize(path) / (1024 * 1024), 2) if exists else 0
        results.append({
            "alias": alias,
            "path": path,
            "exists": exists,
            "size_mb": size_mb,
        })
    return json.dumps(results, indent=2)


@server.tool()
def list_tables(database: str) -> str:
    """List all tables and their row counts in a specified SQLite database.

    Args:
        database: Database alias ('chat', 'memory', 'vault', 'health') or path.
    """
    db_path = resolve_db_path(database)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        tables = cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        out = []
        for (tbl,) in tables:
            try:
                count = cur.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
            except (sqlite3.Error, OSError):
                count = -1
            out.append({"table": tbl, "row_count": count})
        return json.dumps(out, indent=2)
    finally:
        con.close()


@server.tool()
def describe_table(database: str, table_name: str) -> str:
    """Inspect the schema, columns, data types, primary keys, and indexes of a table.

    Args:
        database: Database alias ('chat', 'memory', 'vault', 'health') or path.
        table_name: Name of the table to describe.
    """
    db_path = resolve_db_path(database)
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        cols = cur.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        if not cols:
            return json.dumps({"error": f"Table '{table_name}' not found in {db_path}"})

        columns_info = [
            {
                "cid": c["cid"],
                "name": c["name"],
                "type": c["type"],
                "notnull": bool(c["notnull"]),
                "dflt_value": c["dflt_value"],
                "pk": bool(c["pk"]),
            }
            for c in cols
        ]

        indexes = cur.execute(f'PRAGMA index_list("{table_name}")').fetchall()
        indexes_info = [dict(idx) for idx in indexes]

        sql_def = cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?", (table_name,)
        ).fetchone()

        result = {
            "database": db_path,
            "table": table_name,
            "columns": columns_info,
            "indexes": indexes_info,
            "create_sql": sql_def["sql"] if sql_def else None,
        }
        return json.dumps(result, indent=2)
    finally:
        con.close()


@server.tool()
def query_database(database: str, sql: str, limit: int = 50) -> str:
    """Execute a read-only SQL query (SELECT / PRAGMA / EXPLAIN / WITH) on a database and return rows as JSON.

    Args:
        database: Database alias ('chat', 'memory', 'vault', 'health') or path.
        sql: Read-only SQL statement to execute.
        limit: Maximum number of rows to return (default: 50).
    """
    db_path = resolve_db_path(database)
    query_str = sql.strip()

    first_word = query_str.split()[0].upper() if query_str else ""
    if first_word not in ("SELECT", "PRAGMA", "EXPLAIN", "WITH"):
        return json.dumps({
            "error": f"Forbidden query type '{first_word}'. Only read-only queries (SELECT, PRAGMA, EXPLAIN, WITH) are allowed."
        })

    if limit and "LIMIT" not in query_str.upper() and first_word in ("SELECT", "WITH"):
        query_str = f"{query_str} LIMIT {int(limit)}"

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        rows = cur.execute(query_str).fetchall()
        data = [dict(r) for r in rows]
        result = {
            "database": db_path,
            "row_count": len(data),
            "rows": data,
        }
        return json.dumps(result, indent=2, default=str)
    except (sqlite3.Error, OSError, ValueError) as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
    finally:
        con.close()


# ---------------------------------------------------------------------------
# ChromaDB Vector Tools
# ---------------------------------------------------------------------------

@server.tool()
def list_chroma_collections() -> str:
    """List all collections in Evelyn's ChromaDB vector store with vector counts and distance metrics."""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        cols = client.list_collections()
        out = [
            {
                "name": c.name,
                "count": c.count(),
                "metadata": c.metadata,
            }
            for c in cols
        ]
        return json.dumps({"chroma_path": CHROMA_DIR, "collections": out}, indent=2)
    except (OSError, RuntimeError, ValueError, ImportError) as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


@server.tool()
def query_chroma(query_text: str, collection_name: str = "evelyn_memory", n_results: int = 5) -> str:
    """Perform a semantic vector similarity search against a ChromaDB collection.

    Args:
        query_text: Natural language query string to embed and search.
        collection_name: Target collection ('evelyn_memory', 'evelyn_tag_taxonomy', etc. Default: 'evelyn_memory').
        n_results: Number of nearest vector matches to retrieve (default: 5).
    """
    try:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        col = client.get_collection(collection_name)
        res = col.query(query_texts=[query_text], n_results=n_results)

        ids = res.get("ids", [[]])[0]
        distances = res.get("distances", [[]])[0]
        documents = res.get("documents", [[]])[0]
        metadatas = res.get("metadatas", [[]])[0]

        matches = [
            {
                "id": ids[i],
                "cosine_distance": round(float(distances[i]), 4) if i < len(distances) else None,
                "document": documents[i] if i < len(documents) else None,
                "metadata": metadatas[i] if i < len(metadatas) else None,
            }
            for i in range(len(ids))
        ]

        return json.dumps({
            "collection": collection_name,
            "query": query_text,
            "match_count": len(matches),
            "matches": matches,
        }, indent=2, default=str)
    except (OSError, RuntimeError, ValueError, ImportError) as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


@server.tool()
def get_chroma_status() -> str:
    """Inspect ChromaDB write lock state, storage footprint, and pending sync queue items."""
    try:
        lock_exists = os.path.exists(CHROMA_LOCK)
        queue_count = 0
        memory_db = DB_MAP["memory"]
        if os.path.exists(memory_db):
            con = sqlite3.connect(f"file:{memory_db}?mode=ro", uri=True)
            try:
                cur = con.cursor()
                tbls = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if "chroma_sync_queue" in tbls:
                    queue_count = cur.execute("SELECT COUNT(*) FROM chroma_sync_queue WHERE status = 'pending'").fetchone()[0]
            finally:
                con.close()

        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        cols = client.list_collections()
        total_vectors = sum(c.count() for c in cols)

        return json.dumps({
            "chroma_dir": CHROMA_DIR,
            "write_lock_active": lock_exists,
            "pending_sync_queue": queue_count,
            "total_vectors": total_vectors,
            "collections": [{"name": c.name, "vectors": c.count()} for c in cols],
        }, indent=2)
    except (sqlite3.Error, OSError, RuntimeError, ValueError, ImportError) as exc:
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


# ---------------------------------------------------------------------------
# FastAPI & System Service Tools
# ---------------------------------------------------------------------------

@server.tool()
def get_server_status() -> str:
    """Check Evelyn's FastAPI backend status, active model, thinking parameters, and context length."""
    headers = {"X-Evelyn-Key": API_KEY}
    res = http_get_json(f"{SERVER_URL}/status", headers=headers)
    return json.dumps(res, indent=2)


@server.tool()
def get_heavy_tasks() -> str:
    """Get live status of background pipelines (Fact Extractor, Fact Consolidator, Profile Evolver, Tag Librarian, Memory Refresh)."""
    headers = {"X-Evelyn-Key": API_KEY}
    res = http_get_json(f"{SERVER_URL}/api/heavy_tasks", headers=headers)
    return json.dumps(res, indent=2)


@server.tool()
def get_pending_reviews() -> str:
    """Get all pending triage items waiting in the review queue (extractions, proposals, profile updates, procedure merges)."""
    headers = {"X-Evelyn-Key": API_KEY}
    res = http_get_json(f"{SERVER_URL}/api/review/unified", headers=headers)
    return json.dumps(res, indent=2)


@server.tool()
def get_ollama_status() -> str:
    """Inspect active Ollama models loaded in VRAM, memory usage, and context configurations."""
    ps_data = fetch_ollama_status()
    return json.dumps(ps_data, indent=2)


if __name__ == "__main__":
    asyncio.run(server.run_stdio_async())
