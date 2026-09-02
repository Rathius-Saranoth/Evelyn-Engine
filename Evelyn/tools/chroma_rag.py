
# chroma_rag.py
# date created: 2026-03-23 15:39:48
# date modified: 2026-09-01 21:57:25
# tags: #rag, #vector, #chromadb, #embeddings, #query

# Chroma Rag.py

"""
chroma_rag.py — Chroma vector DB wrapper for Evelyn's RAG pipeline.

Exports:
  ingest_markdown_file()     — Add/update a file in a collection (auto-chunked).
  delete_document()          — Remove all chunks for a document by source path.
  query_collection()         — Retrieve top-K relevant chunks for a query.
  get_or_create_collection() — Idempotently get a named Chroma collection.
  build_rag_context()        — Query Chroma collection, apply priority boosting and
                                pinned doc injection; return formatted context block.
                                Also fires touch_entry_retrieved() for SQLite context
                                entries served to the model (retrieval tracking).

Collection: evelyn_memory (full markdown files & SQLite context entries)

Embedding model: BAAI/bge-large-en-v1.5 (1024-dim) via local HuggingFace / ONNX runtime.
  CPU-only to avoid VRAM eviction of the chat model.

Index: HNSW with cosine distance (0.0 = identical, 1.0 = orthogonal).
Chunking: ~1600-char overlapping chunks, YAML frontmatter stripped before embedding.
Priority/Pinning: rag_priority multiplier adjusts cosine distance before threshold filter;
  rag_pinned=true guarantees injection when any alias appears in the query.
"""


import datetime
import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager, suppress
from typing import Any

import chromadb
from chromadb.utils import embedding_functions

import evelyn_config as cfg

try:
    from Evelyn.tools.frontmatter_utils import parse_frontmatter
except ImportError:
    from frontmatter_utils import parse_frontmatter

_CHROMA_DIR = getattr(cfg, "CHROMA_DB_PATH", r"/home/rathius/evelyn/data/chroma_db")
CHROMA_LOCK_FILE = os.path.join(_CHROMA_DIR, ".chroma_write.lock")
_MEMORY_DB_PATH = getattr(cfg, "MEMORY_DB_PATH", r"/home/rathius/evelyn/data/evelyn_memory.db")


def _get_queue_db() -> sqlite3.Connection:
    """Return a WAL-mode SQLite connection for chroma_sync_queue operations."""
    con = sqlite3.connect(_MEMORY_DB_PATH, timeout=20.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con



@contextmanager
def acquire_chroma_write_lock(timeout: float = 60.0, non_blocking: bool = False):
    """Acquire an exclusive cross-process file lock for ChromaDB write operations.

    Guarantees that only one process or thread can write to ChromaDB at any given time,
    preventing Rust HNSW segment writer and compaction desynchronization.

    Args:
        timeout: Maximum seconds to wait for the lock when non_blocking is False.
        non_blocking: If True, raises BlockingIOError immediately if lock is held.

    Yields:
        None
    """
    os.makedirs(os.path.dirname(CHROMA_LOCK_FILE), exist_ok=True)
    with open(CHROMA_LOCK_FILE, "a+") as lock_file:
        start = time.time()
        acquired = False
        try:
            while True:
                try:
                    # Always use LOCK_NB so flock returns immediately if held by another process/thread,
                    # allowing the timeout loop to enforce the deadline without kernel-level deadlock.
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (BlockingIOError, OSError) as err:
                    if non_blocking:
                        raise BlockingIOError("ChromaDB write lock is held by another process") from err
                    if (time.time() - start) >= timeout:
                        raise TimeoutError(f"Could not acquire ChromaDB write lock after {timeout:.1f}s") from err
                    time.sleep(0.1)
            yield
        finally:
            if acquired:
                with suppress(OSError):
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

# ---------------------------------------------------------------------------
# Chunking config
# ---------------------------------------------------------------------------
# BAAI/bge-large-en-v1.5 has a 512 WordPiece token context window (~2000 chars).
# We chunk at 1600 chars (~400 tokens) with 200-char overlap to maximize note integrity
# and preserve full paragraphs without truncation.
CHUNK_SIZE    = 1600  # chars per chunk (fits ~400 tokens)
CHUNK_OVERLAP = 200   # chars of overlap between consecutive chunks


def extract_abstract_callout(body: str) -> str:
    """Extract text from an Obsidian Visual PKM [!ABSTRACT] callout block if present.

    Args:
        body: Markdown body text.

    Returns:
        str: Extracted callout text (with leading '>' stripped), or empty string.
    """
    if not body:
        return ""
    # Matches > [!ABSTRACT] followed by blockquote lines
    match = re.search(
        r"(?m)^\s*>\s*\[!ABSTRACT\][^\n]*\n((?:[ \t]*>[^\n]*\n?)+)",
        body,
        flags=re.IGNORECASE,
    )
    if match:
        raw_lines = match.group(1).splitlines()
        cleaned_lines = [re.sub(r"^[ \t]*>[ \t]?", "", line) for line in raw_lines]
        return "\n".join(cleaned_lines).strip()

    # Single-line callout variant: > [!ABSTRACT] Some text...
    single_match = re.search(
        r"(?m)^\s*>\s*\[!ABSTRACT\][ \t]+([^\n]+)",
        body,
        flags=re.IGNORECASE,
    )
    if single_match:
        return single_match.group(1).strip()

    return ""


def preprocess_markdown_for_indexing(content: str) -> tuple[str, dict[str, Any]]:
    """Preprocess markdown content before chunking and embedding into ChromaDB.

    Performs:
    1. Canonical YAML frontmatter extraction via frontmatter_utils.
    2. Executive Callout ([!ABSTRACT]) extraction into metadata['abstract'].
    3. Top-of-note navigation header and breadcrumb removal (> Navigation: ...).
    4. Trailing link list / footer removal (## 🔗 Related Notes, ## Navigation, ## Footnotes).
    5. Normalization of blank line spacing.

    Args:
        content: Raw markdown text (including frontmatter).

    Returns:
        tuple[str, dict[str, Any]]: (clean_body, extracted_meta)
    """
    if not content or not content.strip():
        return "", {}

    meta, body = parse_frontmatter(content)
    extracted_meta: dict[str, Any] = dict(meta) if meta else {}

    # 1. Extract abstract if present
    abstract = extract_abstract_callout(body)
    if abstract:
        extracted_meta["abstract"] = abstract

    # 2. Strip top-of-file breadcrumbs & navigation lines
    body = re.sub(r"(?mi)^\s*>\s*Navigation:.*$\n?", "", body)
    body = re.sub(r"(?mi)^\s*>\s*\[!NAV\].*$\n?", "", body)
    body = re.sub(r"(?mi)^\s*\[!NAV\].*$\n?", "", body)

    # 3. Strip trailing link lists, related notes, footnotes with Unicode/emoji support
    body = re.sub(
        r"(?mi)\n##\s*[^\n]*?\b(?:related\s*notes|navigation|footnotes|see\s*also)\b[\s\S]*$",
        "",
        body,
    )

    # 4. Normalize excessive blank lines
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body, extracted_meta


def clean_rag_chunk_content(content: str) -> str:
    """Downstream safety net to sanitize retrieved chunks before context assembly.

    Strips any leftover frontmatter delimiters, severed frontmatter lines,
    navigation headers, and excessive whitespace from legacy or un-synced chunks.

    Args:
        content: Raw chunk text.

    Returns:
        str: Sanitized chunk text.
    """
    if not content:
        return ""

    text = content
    # Strip complete leading YAML frontmatter if present
    text = re.sub(r"^---\n.*?\n---\n?", "", text, count=1, flags=re.DOTALL)
    # Strip severed single frontmatter lines at the top (e.g. "tags: [...]" or "title: ...")
    if text.startswith("---"):
        text = re.sub(r"^---[^\n]*\n", "", text)

    # Strip navigation lines
    text = re.sub(r"(?mi)^\s*>\s*Navigation:.*$\n?", "", text)
    text = re.sub(r"(?mi)^\s*>\s*\[!NAV\].*$\n?", "", text)
    text = re.sub(r"(?mi)^\s*\[!NAV\].*$\n?", "", text)

    # Strip trailing related notes / footers if chunk captured them
    text = re.sub(
        r"(?mi)\n##\s*[^\n]*?\b(?:related\s*notes|navigation|footnotes|see\s*also)\b[\s\S]*$",
        "",
        text,
    )

    return re.sub(r"\n{3,}", "\n\n", text).strip()


def sanitize_chroma_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Sanitize metadata dictionary to ensure all keys and values conform to ChromaDB constraints.

    ChromaDB requires:
    - Values must be str, int, float, bool, or a non-empty list of str/int/float/bool.
    - datetime / date objects must be converted to ISO format strings.
    - Empty lists, dicts, or unsupported objects must be converted to strings or filtered.
    """
    clean: dict[str, Any] = {}
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, (bool, int, float)):
            clean[k] = v
        elif isinstance(v, str):
            clean[k] = v
        elif isinstance(v, (datetime.date, datetime.datetime)):
            clean[k] = v.isoformat()
        elif isinstance(v, (list, tuple, set)):
            if not v:
                clean[k] = ""
            else:
                clean_list = []
                for item in v:
                    if isinstance(item, (datetime.date, datetime.datetime)):
                        clean_list.append(item.isoformat())
                    elif isinstance(item, (str, int, float, bool)):
                        clean_list.append(item)
                    elif item is not None:
                        clean_list.append(str(item))
                clean[k] = clean_list if clean_list else ""
        elif isinstance(v, dict):
            clean[k] = json.dumps(v, default=str)
        else:
            clean[k] = str(v)
    return clean


def chunk_text(content: str) -> list[str]:
    """Split content into overlapping chunks of at most CHUNK_SIZE characters.

    Tries to split on paragraph boundaries first.

    Args:
        content: The text content to split.

    Returns:
        list[str]: A list of text chunks, ensuring at least one non-empty chunk.
    """
    if len(content) <= CHUNK_SIZE:
        return [content] if content.strip() else ["(empty)"]

    chunks = []
    # Split on paragraph boundaries
    paragraphs = content.split("\n\n")
    current = ""
    for para in paragraphs:
        candidate = (current + "\n\n" + para).lstrip() if current else para
        if len(candidate) <= CHUNK_SIZE:
            current = candidate
        else:
            if current:
                chunks.append(current)
            # Para itself may be too long — hard-split it
            if len(para) > CHUNK_SIZE:
                for i in range(0, len(para), CHUNK_SIZE - CHUNK_OVERLAP):
                    part = para[i : i + CHUNK_SIZE]
                    if part.strip():
                        chunks.append(part)
                current = ""
            else:
                current = para
    if current:
        chunks.append(current)

    # Ensure no empty chunks
    return [c for c in chunks if c.strip()] or ["(empty)"]

# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------
_client = None
_embedding_fn = None

def _get_client() -> chromadb.PersistentClient:
    """Return the cached chromadb PersistentClient singleton.

    Returns:
        chromadb.PersistentClient: The cached database client singleton.
    """
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=cfg.CHROMA_DB_PATH)
    return _client


def _get_embedding_fn():
    """Return a cached instance of BAAI/bge-large-en-v1.5 embedding function.

    Model is BAAI/bge-large-en-v1.5 (1024-dim, 512-token context window).

    Returns:
        embedding_functions.SentenceTransformerEmbeddingFunction: Cached embedding function.
    """
    global _embedding_fn
    if _embedding_fn is None:
        _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="BAAI/bge-large-en-v1.5"
        )
    return _embedding_fn


def get_or_create_collection(name: str) -> chromadb.Collection:
    """Idempotently get or create a named Chroma collection.

    Args:
        name: The name of the collection to get or create.

    Returns:
        chromadb.Collection: The retrieved or newly created Chroma collection.
    """
    client = _get_client()
    return client.get_or_create_collection(
        name=name,
        embedding_function=_get_embedding_fn(),
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# Staging Queue & Direct Ingest Operations
# ---------------------------------------------------------------------------

def enqueue_upsert(source_path: str, content: str, collection_name: str = "evelyn_memory",
                   extra_metadata: dict | None = None) -> bool:
    """Enqueue a document or context entry for asynchronous insertion/update in ChromaDB.

    Uses SQLite WAL mode with coalescing: if an update for (source_path, collection_name)
    is already pending, it updates the existing row with the latest content to eliminate
    redundant embedding computation.

    Args:
        source_path: Absolute file path or sqlite URI (e.g. 'sqlite::context_entry::123').
        content: Raw markdown text or context observation.
        collection_name: Target Chroma collection name.
        extra_metadata: Optional dictionary of metadata attributes.

    Returns:
        bool: True on successful enqueue, False on failure.
    """
    now = time.time()
    meta_json = json.dumps(extra_metadata) if extra_metadata else None
    con = _get_queue_db()
    try:
        cur = con.cursor()
        # Coalescing: check if a pending row exists for this source and collection
        cur.execute(
            """SELECT id FROM chroma_sync_queue
               WHERE source_path = ? AND collection_name = ? AND status = 'pending'""",
            (source_path, collection_name),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                """UPDATE chroma_sync_queue
                   SET action = 'upsert', content = ?, extra_metadata_json = ?,
                       updated_at = ?, retry_count = 0, error_msg = NULL
                   WHERE id = ?""",
                (content, meta_json, now, row["id"]),
            )
        else:
            cur.execute(
                """INSERT INTO chroma_sync_queue
                   (action, source_path, collection_name, content, extra_metadata_json,
                    status, retry_count, created_at, updated_at)
                   VALUES ('upsert', ?, ?, ?, ?, 'pending', 0, ?, ?)""",
                (source_path, collection_name, content, meta_json, now, now),
            )
        con.commit()
        return True
    except (sqlite3.Error, OSError, ValueError) as e:
        print(f"[chroma_rag] enqueue_upsert error for {source_path}: {e}", flush=True)
        return False
    finally:
        con.close()


def enqueue_delete(source_path: str, collection_name: str = "evelyn_memory") -> bool:
    """Enqueue a document for deletion from ChromaDB.

    Coalesces with any pending upserts for the same source document.

    Args:
        source_path: The document source path or context entry URI to remove.
        collection_name: Target Chroma collection name.

    Returns:
        bool: True on successful enqueue, False on failure.
    """
    now = time.time()
    con = _get_queue_db()
    try:
        cur = con.cursor()
        cur.execute(
            """SELECT id FROM chroma_sync_queue
               WHERE source_path = ? AND collection_name = ? AND status = 'pending'""",
            (source_path, collection_name),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                """UPDATE chroma_sync_queue
                   SET action = 'delete', content = NULL, extra_metadata_json = NULL,
                       updated_at = ?, retry_count = 0, error_msg = NULL
                   WHERE id = ?""",
                (now, row["id"]),
            )
        else:
            cur.execute(
                """INSERT INTO chroma_sync_queue
                   (action, source_path, collection_name, content, extra_metadata_json,
                    status, retry_count, created_at, updated_at)
                   VALUES ('delete', ?, ?, NULL, NULL, 'pending', 0, ?, ?)""",
                (source_path, collection_name, now, now),
            )
        con.commit()
        return True
    except (sqlite3.Error, OSError, ValueError) as e:
        print(f"[chroma_rag] enqueue_delete error for {source_path}: {e}", flush=True)
        return False
    finally:
        con.close()


def enqueue_remap(old_source_path: str, new_source_path: str,
                  collection_name: str = "evelyn_memory") -> bool:
    """Enqueue a document source path remap for atomic transfer in ChromaDB without re-embedding.

    Args:
        old_source_path: The previous document path or URI.
        new_source_path: The new document path or URI.
        collection_name: Target Chroma collection name.

    Returns:
        bool: True on successful enqueue, False on failure.
    """
    now = time.time()
    con = _get_queue_db()
    try:
        cur = con.cursor()
        cur.execute(
            """SELECT id FROM chroma_sync_queue
               WHERE source_path = ? AND collection_name = ? AND status = 'pending'""",
            (old_source_path, collection_name),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                """UPDATE chroma_sync_queue
                   SET action = 'remap', content = ?, extra_metadata_json = NULL,
                       updated_at = ?, retry_count = 0, error_msg = NULL
                   WHERE id = ?""",
                (new_source_path, now, row["id"]),
            )
        else:
            cur.execute(
                """INSERT INTO chroma_sync_queue
                   (action, source_path, collection_name, content, extra_metadata_json,
                    status, retry_count, created_at, updated_at)
                   VALUES ('remap', ?, ?, ?, NULL, 'pending', 0, ?, ?)""",
                (old_source_path, collection_name, new_source_path, now, now),
            )
        con.commit()
        return True
    except (sqlite3.Error, OSError, ValueError) as e:
        print(f"[chroma_rag] enqueue_remap error from {old_source_path} to {new_source_path}: {e}", flush=True)
        return False
    finally:
        con.close()


def direct_upsert(file_path: str, content: str, collection_name: str,
                  extra_metadata: dict | None = None) -> bool:
    """Directly chunk and upsert a document into ChromaDB (single custodian / drainer execution).

    Args:
        file_path: Document source path or URI.
        content: Text content to embed and store.
        collection_name: Target collection name.
        extra_metadata: Optional dictionary of metadata attributes.

    Returns:
        bool: True on success, False on failure.
    """
    try:
        col = get_or_create_collection(collection_name)
        _delete_chunks_by_source(col, file_path)

        clean_content, extracted_meta = preprocess_markdown_for_indexing(content)
        chunks = chunk_text(clean_content)
        ids = [f"{file_path}::chunk-{i}" for i in range(len(chunks))]
        metadatas = []
        for i in range(len(chunks)):
            meta: dict[str, Any] = {"source": file_path, "chunk": i, "total_chunks": len(chunks)}
            if extracted_meta:
                meta.update(extracted_meta)
            if extra_metadata:
                meta.update(extra_metadata)
            meta.setdefault("rag_priority", "normal")
            meta.setdefault("rag_pinned", False)
            meta.setdefault("aliases", "")
            metadatas.append(sanitize_chroma_metadata(meta))

        col.upsert(ids=ids, documents=chunks, metadatas=metadatas)
        return True
    except Exception as e:
        print(f"[chroma_rag] direct_upsert failed for {file_path}: {e}", flush=True)
        raise


def direct_delete(file_path: str, collection_name: str) -> bool:
    """Directly delete a document's chunks from ChromaDB (single custodian / drainer execution).

    Args:
        file_path: Document source path or URI.
        collection_name: Target Chroma collection name.

    Returns:
        bool: True on success, False on failure.
    """
    try:
        col = get_or_create_collection(collection_name)
        _delete_chunks_by_source(col, file_path)
        return True
    except Exception as e:
        print(f"[chroma_rag] direct_delete failed for {file_path}: {e}", flush=True)
        raise


def direct_remap(old_source_path: str, new_source_path: str, collection_name: str = "evelyn_memory") -> bool:
    """Remap existing document chunks from old_source_path to new_source_path in ChromaDB.

    Transfers chunk texts, metadata, and precomputed embeddings without re-embedding.

    Args:
        old_source_path: Previous document path.
        new_source_path: New document path.
        collection_name: Target Chroma collection name.

    Returns:
        bool: True if chunks were successfully remapped, False otherwise.
    """
    try:
        col = get_or_create_collection(collection_name)
        results = col.get(where={"source": old_source_path}, include=["documents", "metadatas", "embeddings"])
        old_ids = results.get("ids", [])
        if not old_ids:
            return False

        docs = results.get("documents", [])
        metas = results.get("metadatas", [])
        embeddings = results.get("embeddings", [])

        new_ids = []
        new_metas = []
        for i, meta in enumerate(metas):
            chunk_num = meta.get("chunk", i)
            new_ids.append(f"{new_source_path}::chunk-{chunk_num}")
            updated_meta = dict(meta)
            updated_meta["source"] = new_source_path
            new_metas.append(updated_meta)

        kwargs = {
            "ids": new_ids,
            "documents": docs,
            "metadatas": new_metas,
        }
        if embeddings is not None and len(embeddings) == len(new_ids):
            kwargs["embeddings"] = embeddings

        col.upsert(**kwargs)
        _delete_chunks_by_source(col, old_source_path)
        return True
    except (RuntimeError, ValueError, KeyError, OSError) as e:
        print(f"[chroma_rag] direct_remap failed from {old_source_path} to {new_source_path}: {e}", flush=True)
        return False


def remap_document(old_source_path: str, new_source_path: str, collection_name: str = "evelyn_memory") -> bool:
    """Synchronously remap a document source path in Chroma without re-embedding."""
    with acquire_chroma_write_lock():
        return direct_remap(old_source_path, new_source_path, collection_name)


def drain_sync_queue(batch_size: int = 50, source_prefix: str = "", deadline: float | None = None) -> int:
    """Drain and process pending items from chroma_sync_queue in batch.

    Isolates dead-letter failures so malformed payloads retry up to 3 times
    before transitioning to status='error' without stalling the queue.
    Supports an optional deadline timestamp; if exceeded mid-batch, remaining
    unprocessed records are reverted from 'processing' to 'pending'.

    Args:
        batch_size: Maximum number of records to process in one drain cycle.
        source_prefix: Optional source_path prefix filter (e.g. 'test::' for unit tests).
        deadline: Optional monotonic/epoch timestamp deadline. If exceeded, batch halts.

    Returns:
        int: Number of items successfully processed.
    """
    con = _get_queue_db()
    processed_count = 0
    try:
        cur = con.cursor()
        if source_prefix:
            cur.execute(
                """SELECT id, action, source_path, collection_name, content,
                          extra_metadata_json, retry_count
                   FROM chroma_sync_queue
                   WHERE status = 'pending' AND source_path LIKE ?
                   ORDER BY id ASC
                   LIMIT ?""",
                (f"{source_prefix}%", batch_size),
            )
        else:
            cur.execute(
                """SELECT id, action, source_path, collection_name, content,
                          extra_metadata_json, retry_count
                   FROM chroma_sync_queue
                   WHERE status = 'pending'
                   ORDER BY id ASC
                   LIMIT ?""",
                (batch_size,),
            )
        rows = cur.fetchall()
        if not rows:
            return 0

        # Mark selected rows as 'processing'
        ids = [r["id"] for r in rows]
        cur.execute(
            f"UPDATE chroma_sync_queue SET status = 'processing', updated_at = ? WHERE id IN ({','.join(map(str, ids))})",
            (time.time(),),
        )
        con.commit()

        # Process each item with individual failure isolation and deadline guard
        for idx, r in enumerate(rows):
            # Check deadline before processing current item
            if deadline is not None and time.time() >= deadline:
                # Deadline exceeded: revert remaining unprocessed rows in this batch to 'pending'
                unprocessed_ids = [row["id"] for row in rows[idx:]]
                if unprocessed_ids:
                    cur.execute(
                        f"UPDATE chroma_sync_queue SET status = 'pending', updated_at = ? WHERE id IN ({','.join(map(str, unprocessed_ids))})",
                        (time.time(),),
                    )
                    con.commit()
                break

            item_id = r["id"]
            action = r["action"]
            src = r["source_path"]
            col_name = r["collection_name"]
            content = r["content"] or ""
            retries = r["retry_count"] or 0
            extra_meta = None
            if r["extra_metadata_json"]:
                try:
                    extra_meta = json.loads(r["extra_metadata_json"])
                except (json.JSONDecodeError, TypeError, ValueError):
                    extra_meta = None

            try:
                if action == "upsert":
                    direct_upsert(src, content, col_name, extra_meta)
                elif action == "delete":
                    direct_delete(src, col_name)
                elif action == "remap":
                    direct_remap(src, content, col_name)
                else:
                    raise ValueError(f"Unknown staging queue action: {action}")

                # Success: mark done
                cur.execute(
                    "UPDATE chroma_sync_queue SET status = 'done', updated_at = ? WHERE id = ?",
                    (time.time(), item_id),
                )
                processed_count += 1

            except (RuntimeError, ValueError, KeyError, OSError, sqlite3.Error) as e:
                new_retries = retries + 1
                if new_retries >= 3:
                    # Dead-letter: mark error so queue is never blocked
                    print(f"[chroma_rag] Dead-letter poison pill on item #{item_id} ({src}): {e}", flush=True)
                    cur.execute(
                        """UPDATE chroma_sync_queue
                           SET status = 'error', retry_count = ?, error_msg = ?, updated_at = ?
                           WHERE id = ?""",
                        (new_retries, str(e), time.time(), item_id),
                    )
                else:
                    # Re-queue for next retry
                    cur.execute(
                        """UPDATE chroma_sync_queue
                           SET status = 'pending', retry_count = ?, error_msg = ?, updated_at = ?
                           WHERE id = ?""",
                        (new_retries, str(e), time.time(), item_id),
                    )
            con.commit()

        return processed_count
    finally:
        con.close()


def flush_sync_queue(timeout: float = 5.0, source_prefix: str = "") -> bool:
    """Synchronously drain pending items from chroma_sync_queue until empty or timeout.

    Guarantees read-your-own-writes consistency for immediate RAG turns.

    Args:
        timeout: Maximum seconds to wait for the queue to completely drain.
        source_prefix: Optional source_path prefix filter (e.g. 'test::').

    Returns:
        bool: True if queue was completely emptied, False if timeout reached.
    """
    start = time.time()
    deadline = start + max(0.1, timeout)
    while time.time() < deadline:
        drained = drain_sync_queue(batch_size=50, source_prefix=source_prefix, deadline=deadline)
        if drained == 0:
            # Check if any pending items remain
            con = _get_queue_db()
            try:
                cur = con.cursor()
                if source_prefix:
                    cur.execute(
                        "SELECT COUNT(*) AS cnt FROM chroma_sync_queue WHERE status = 'pending' AND source_path LIKE ?",
                        (f"{source_prefix}%",),
                    )
                else:
                    cur.execute("SELECT COUNT(*) AS cnt FROM chroma_sync_queue WHERE status = 'pending'")
                rem = cur.fetchone()["cnt"]
                if rem == 0:
                    return True
            finally:
                con.close()
            time.sleep(0.05)
    return False


def check_chroma_health() -> dict:
    """Run an active canary probe query against ChromaDB to verify index & segment integrity.

    Returns:
        dict: {"status": "healthy" | "corrupt", "error": str | None, "count": int}
    """
    try:
        col = get_or_create_collection(cfg.CHROMA_MEMORY_COLLECTION)
        doc_count = col.count()
        # Probe query: test cosine similarity and HNSW segment reader
        col.query(query_texts=["system health probe canary"], n_results=min(1, max(1, doc_count)))
        return {"status": "healthy", "error": None, "count": doc_count}
    except (RuntimeError, ValueError, KeyError, OSError) as e:
        err_msg = str(e)
        print(f"[chroma_rag] HEALTH PROBE FAILED: {err_msg}", flush=True)
        return {"status": "corrupt", "error": err_msg, "count": 0}


def repair_corrupted_chroma(background: bool = True) -> None:
    """Purge corrupted vector database segments and launch an automated full-vault migration.

    Args:
        background: If True, spawns sync_full_vault_to_chroma.py detached in background.
    """
    global _client
    print(f"[chroma_rag] Initiating ChromaDB self-healing repair at: {_CHROMA_DIR}...", flush=True)
    _client = None

    if os.path.exists(_CHROMA_DIR):
        try:
            shutil.rmtree(_CHROMA_DIR, ignore_errors=True)
        except OSError as e:
            print(f"[chroma_rag] Warning: Could not purge {_CHROMA_DIR}: {e}", flush=True)

    # Clear state files
    for state_path in [
        getattr(cfg, "VAULT_SYNC_STATE", r"/home/rathius/evelyn/data/vault_sync_state.json"),
        getattr(cfg, "GIST_SYNC_STATE", r"/home/rathius/evelyn/data/gist_sync_state.json"),
    ]:
        if os.path.exists(state_path):
            with suppress(Exception):
                os.remove(state_path)

    script_path = os.path.join(cfg.BASE_DIR if hasattr(cfg, "BASE_DIR") else "/home/rathius/evelyn", "scripts", "sync_full_vault_to_chroma.py")
    py_bin = sys.executable
    if background:
        subprocess.Popen([py_bin, script_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[chroma_rag] Dispatched background sync_full_vault_to_chroma.py repair.", flush=True)
    else:
        subprocess.run([py_bin, script_path], check=False)
        print("[chroma_rag] Synchronous sync_full_vault_to_chroma.py repair finished.", flush=True)


def ingest_markdown_file(file_path: str, content: str, collection_name: str,
                         extra_metadata: dict | None = None) -> bool:
    """Compatibility wrapper: enqueues markdown file for ingestion via staging queue."""
    return enqueue_upsert(file_path, content, collection_name, extra_metadata)


def _delete_chunks_by_source(col: chromadb.Collection, file_path: str):
    """Delete all chunk documents for a given source path from the collection.

    Args:
        col: The Chroma collection to delete from.
        file_path: The source document path to filter by.
    """
    try:
        results = col.get(where={"source": file_path}, include=[])
        ids_to_delete = results.get("ids", [])
        if ids_to_delete:
            col.delete(ids=ids_to_delete)
    except (RuntimeError, ValueError, KeyError, OSError) as e:
        print(f"[chroma_rag] chunk cleanup failed for {file_path}: {e}")


def delete_document(file_path: str, collection_name: str) -> bool:
    """Compatibility wrapper: enqueues document for deletion via staging queue."""
    return enqueue_delete(file_path, collection_name)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def query_collection(query: str, collection_name: str, n_results: int | None = None) -> list[dict]:
    """Retrieve the top-K most relevant chunks from a collection.

    Args:
        query: Natural language query string.
        collection_name: Collection to search.
        n_results: Optional number of results.

    Returns:
        list[dict]: List of dictionaries containing 'content', 'source', 'distance', 'metadata'.
    """
    if n_results is None:
        n_results = cfg.RAG_TOP_K
    try:
        col = get_or_create_collection(collection_name)
        count = col.count()
        if count == 0:
            return []
        results = col.query(
            query_texts=[query],
            n_results=min(n_results, count),
            include=["documents", "metadatas", "distances"],
        )
        chunks = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0], strict=False,
        ):
            chunks.append({
                "content":  doc,
                "source":   meta.get("source", ""),
                "distance": dist,
                "metadata": meta,
            })
        return chunks
    except (RuntimeError, ValueError, KeyError, OSError) as e:
        # Always log query failures (not debug-gated) — a failed query means
        # total RAG context loss for this turn, which should never be silent.
        print(f"[chroma_rag] QUERY FAILED ({collection_name}): {e}", flush=True)
        return []


def _apply_priority_boost(chunks: list[dict]) -> list[dict]:
    """Adjust each chunk's distance based on its rag_priority metadata.

    Args:
        chunks: List of retrieved chunk dictionaries.

    Returns:
        list[dict]: The updated and re-sorted chunk list.
    """
    multipliers = getattr(cfg, "RAG_PRIORITY_MULTIPLIERS", {"high": 0.75, "normal": 1.0, "low": 1.25})
    for c in chunks:
        meta = c.get("metadata") or {}
        priority = meta.get("rag_priority", "normal") if isinstance(meta, dict) else "normal"
        dist = c.get("distance", 1.0)
        c["distance"] = dist * multipliers.get(priority, 1.0)
    chunks.sort(key=lambda x: x.get("distance", 1.0))
    return chunks


def _fetch_pinned_chunks(query: str) -> list[dict]:
    """Scan both collections for pinned documents whose aliases appear in the query.

    Args:
        query: The raw query string to check aliases against.

    Returns:
        list[dict]: A list of matched pinned chunks.
    """
    max_chunks = getattr(cfg, "RAG_PINNED_MAX_CHUNKS", 2)
    query_lower = query.lower()
    pinned = []
    seen_sources = set()

    for collection_name in [cfg.CHROMA_MEMORY_COLLECTION]:
        try:
            col = get_or_create_collection(collection_name)
            if col.count() == 0:
                continue

            # Fetch all pinned docs — metadata-only first to avoid pulling all embeddings
            pinned_results = col.get(
                where={"rag_pinned": True},
                include=["metadatas"],
            )
            if not pinned_results or not pinned_results.get("metadatas"):
                continue

            # Group unique source paths from pinned docs
            pinned_sources = {}
            for meta in pinned_results["metadatas"]:
                src = meta.get("source", "")
                if src and src not in pinned_sources:
                    aliases_raw = meta.get("aliases", "")
                    # aliases stored as comma-separated string
                    aliases = [a.strip().lower() for a in aliases_raw.split(",") if a.strip()]
                    # Also add the filename stem as an implicit alias
                    stem = os.path.splitext(os.path.basename(src))[0].lower()
                    # Strip " (persona)" suffix for matching
                    stem_clean = re.sub(r"\s*\(.*?\)\s*$", "", stem).strip()
                    aliases.append(stem_clean)
                    pinned_sources[src] = aliases

            # Check which pinned docs' aliases appear in the query (using word boundaries)
            for src, aliases in pinned_sources.items():
                if src in seen_sources:
                    continue
                matched = any(
                    bool(re.search(rf"\b{re.escape(alias)}\b", query_lower))
                    for alias in aliases
                    if alias
                )
                if not matched:
                    continue

                # Fetch the actual chunks for this source
                chunk_results = col.get(
                    where={"source": src},
                    include=["documents", "metadatas"],
                )
                docs = chunk_results.get("documents", [])
                metas = chunk_results.get("metadatas", [])
                for doc, meta in list(zip(docs, metas, strict=False))[:max_chunks]:
                    pinned.append({
                        "content":  doc,
                        "source":   src,
                        "distance": 0.0,  # Pinned docs bypass distance scoring
                        "metadata": meta,
                        "pinned":   True,
                    })
                seen_sources.add(src)
                if cfg.DEBUG_LOGGING:
                    matched_alias = next(
                        (a for a in aliases if a and re.search(rf"\b{re.escape(a)}\b", query_lower)),
                        "?"
                    )
                    chunks_injected = min(len(docs), max_chunks)
                    print(
                        f"[RAG] PINNED src={os.path.basename(src)}"
                        f" matched_alias='{matched_alias}'"
                        f" aliases_checked={len(aliases)}"
                        f" chunks_injected={chunks_injected}",
                        flush=True,
                    )

        except (RuntimeError, ValueError, KeyError, OSError) as e:
            print(f"[chroma_rag] pinned fetch failed ({collection_name}): {e}")

    return pinned


def get_vault_relative_path(path: str) -> str:
    """Convert an absolute or already relative path to a vault-relative path.

    Args:
        path: Absolute or relative file path.

    Returns:
        str: Vault-relative path using forward slashes.
    """
    if path.startswith("sqlite::"):
        return path
    vault_base = getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
    try:
        norm_path = os.path.normpath(path)
        norm_base = os.path.normpath(vault_base)
        if os.path.isabs(norm_path):
            rel = os.path.relpath(norm_path, norm_base)
            return rel.replace('\\', '/')
        else:
            return norm_path.replace('\\', '/')
    except (ValueError, OSError):
        return path.replace('\\', '/')


def log_rag_retrieval(
    query: str,
    search_query: str,
    pinned_chunks: list[dict],
    all_chunks: list[dict],
    relevant: list[dict],
    matching_procedures: list[dict] | None = None,
    message_id: int | None = None,
) -> int | None:
    """Log a RAG retrieval event into rag_retrieval_log in evelyn_memory.db.

    Fire-and-forget, exception-shielded.
    """
    try:
        pinned_sources = {c["source"] for c in pinned_chunks} if pinned_chunks else set()
        threshold = cfg.RAG_DISTANCE_THRESHOLD

        # Add pinned chunks
        chunk_records = [
            {
                "source": get_vault_relative_path(c.get("source", "")),
                "chunk": c.get("metadata", {}).get("chunk", 0),
                "total_chunks": c.get("metadata", {}).get("total_chunks", 1),
                "distance": 0.0,
                "priority": c.get("metadata", {}).get("rag_priority", "pinned"),
                "status": "pinned",
                "preview": (c.get("content") or "")[:120],
                "tags": c.get("metadata", {}).get("tags", ""),
            }
            for c in (pinned_chunks or [])
        ]

        # Add queried vector chunks
        for c in (all_chunks or []):
            src = c.get("source", "")
            is_pinned = src in pinned_sources
            dist = c.get("distance", 1.0)
            status = "pinned_duplicate" if is_pinned else ("kept" if dist <= threshold else "dropped")
            chunk_records.append({
                "source": get_vault_relative_path(src),
                "chunk": c.get("metadata", {}).get("chunk", 0),
                "total_chunks": c.get("metadata", {}).get("total_chunks", 1),
                "distance": round(dist, 4) if isinstance(dist, (int, float)) else dist,
                "priority": c.get("metadata", {}).get("rag_priority", "normal"),
                "status": status,
                "preview": (c.get("content") or "")[:120],
                "tags": c.get("metadata", {}).get("tags", ""),
            })

        # Add procedures if any
        chunk_records.extend([
            {
                "source": f"procedure::{p.get('id', '')}",
                "chunk": 0,
                "total_chunks": 1,
                "distance": 0.0,
                "priority": "procedure",
                "status": "procedure",
                "preview": f"Trigger: {p.get('trigger_pattern', '')}",
                "tags": p.get("tags", ""),
            }
            for p in (matching_procedures or [])
        ])

        con = _get_queue_db()
        cur = con.cursor()
        cur.execute(
            """INSERT INTO rag_retrieval_log
               (message_id, query, search_query, total_retrieved, total_kept, total_pinned, chunks_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message_id,
                query,
                search_query,
                len(all_chunks or []),
                len(relevant or []),
                len(pinned_chunks or []),
                json.dumps(chunk_records),
                time.time(),
            ),
        )
        row_id = cur.lastrowid
        con.commit()
        con.close()
        return row_id
    except (sqlite3.Error, OSError, ValueError) as exc:
        if cfg.DEBUG_LOGGING:
            print(f"[RAG] Warning: telemetry logging failed: {exc}", flush=True)
        return None


def get_recent_rag_telemetry(limit: int = 50, offset: int = 0, days: float | None = None) -> list[dict]:
    """Retrieve recent RAG retrieval events from rag_retrieval_log."""
    con = _get_queue_db()
    try:
        cur = con.cursor()
        if days is not None and days > 0:
            cutoff = time.time() - (days * 86400.0)
            rows = cur.execute(
                """SELECT id, message_id, query, search_query, total_retrieved,
                          total_kept, total_pinned, chunks_json, created_at
                   FROM rag_retrieval_log
                   WHERE created_at >= ?
                   ORDER BY id DESC
                   LIMIT ? OFFSET ?""",
                (cutoff, limit, offset),
            ).fetchall()
        else:
            rows = cur.execute(
                """SELECT id, message_id, query, search_query, total_retrieved,
                          total_kept, total_pinned, chunks_json, created_at
                   FROM rag_retrieval_log
                   ORDER BY id DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            if d.get("chunks_json"):
                try:
                    d["chunks"] = json.loads(d["chunks_json"])
                except (json.JSONDecodeError, TypeError, ValueError):
                    d["chunks"] = []
            else:
                d["chunks"] = []
            results.append(d)
        return results
    finally:
        con.close()


def link_rag_telemetry_to_message(telemetry_id: int, message_id: int) -> None:
    """Link an existing rag_retrieval_log entry to an assistant or user message_id."""
    with suppress(sqlite3.Error, OSError):
        con = _get_queue_db()
        con.execute("UPDATE rag_retrieval_log SET message_id = ? WHERE id = ?", (message_id, telemetry_id))
        con.commit()
        con.close()


def build_rag_context(query: str, message_id: int | None = None) -> str:
    """Query Chroma vector store and return a formatted context block.

    Args:
        query: The raw incoming query string.
        message_id: Optional message ID to associate with the retrieval log.

    Returns:
        str: A formatted context block of retrieved/pinned chunks, or empty string.
    """
    try:
        from Evelyn.tools.query_reformulator import reformulate_query
    except ImportError:
        from query_reformulator import reformulate_query
    search_query = reformulate_query(query)

    # Step 1: Pinned guaranteed chunks (uses ORIGINAL query for alias substring matching)
    pinned_chunks = _fetch_pinned_chunks(query)
    pinned_sources = {c["source"] for c in pinned_chunks}

    # Step 2: Normal vector search (uses REFORMULATED query for semantic matching)
    # Query evelyn_memory (full-text index of all vault notes and context entries).
    all_chunks = query_collection(search_query, cfg.CHROMA_MEMORY_COLLECTION)

    # Step 3: Priority re-ranking
    all_chunks = _apply_priority_boost(all_chunks)

    threshold = cfg.RAG_DISTANCE_THRESHOLD

    # Step 4 & 5: Filter by threshold and deduplicate against pinned
    if cfg.DEBUG_LOGGING:
        for c in all_chunks:
            kept = "KEEP" if c["distance"] <= threshold else "DROP"
            pinned_flag = " [already pinned]" if c["source"] in pinned_sources else ""
            priority = c["metadata"].get("rag_priority", "normal")
            chunk_idx = c["metadata"].get("chunk", "?")
            total = c["metadata"].get("total_chunks", "?")
            preview = c["content"][:120].replace("\n", " ") if c.get("content") else ""
            print(
                f"[RAG]   {kept} dist={c['distance']:.3f} priority={priority}"
                f" src={os.path.basename(c['source'])} chunk={chunk_idx}/{total}"
                f"{pinned_flag} preview='{preview}'",
                flush=True,
            )

    relevant = [
        c for c in all_chunks
        if c["distance"] <= threshold and c["source"] not in pinned_sources
    ]

    # RAG query summary (debug-gated, structured for grep)
    if cfg.DEBUG_LOGGING:
        dropped = len(all_chunks) - len(relevant)
        deduped = sum(1 for c in all_chunks if c["source"] in pinned_sources and c["distance"] <= threshold)
        print(
            f"[RAG] SUMMARY query='{query[:80]}'"
            f" retrieved={len(all_chunks)} kept={len(relevant)}"
            f" pinned={len(pinned_chunks)} dropped={dropped}"
            f" deduped={deduped}",
            flush=True,
        )

    # Step 6: Assemble Context
    all_context = pinned_chunks + relevant

    matching_procedures = []
    try:
        try:
            import memory_db as _memory_db
        except ImportError:
            import Evelyn.tools.memory_db as _memory_db
        matching_procedures = _memory_db.search_procedures_by_trigger(query, status="live")[:3]
        for proc in matching_procedures:
            _memory_db.touch_procedure_retrieved(proc["id"])
    except (sqlite3.Error, OSError, ValueError) as e:
        print(f"[RAG] Warning: procedure search failed: {e}", flush=True)

    # Telemetry logging (fire-and-forget)
    log_rag_retrieval(
        query=query,
        search_query=search_query,
        pinned_chunks=pinned_chunks,
        all_chunks=all_chunks,
        relevant=relevant,
        matching_procedures=matching_procedures,
        message_id=message_id,
    )

    if not all_context and not matching_procedures:
        return ""

    # Touch retrieval counters for any SQLite context entries in the result set.
    # These have synthetic source paths: "sqlite::context_entry::{id}".
    # Fire-and-forget — a tracking failure must never affect context delivery.
    with suppress(sqlite3.Error, OSError, ValueError, KeyError):
        try:
            import memory_db as _memory_db
        except ImportError:
            import Evelyn.tools.memory_db as _memory_db
        for _chunk in all_context:
            _src = _chunk.get("source", "")
            if _src.startswith("sqlite::context_entry::"):
                with suppress(ValueError, sqlite3.Error, OSError):
                    _entry_id = int(_src.rsplit("::", 1)[-1])
                    _memory_db.touch_entry_retrieved(_entry_id)

    # Separate context types to structure the final block
    pinned_by_source = {}
    sqlite_entries = []
    normal_files_by_source = {}

    for chunk in all_context:
        src = chunk.get("source", "")
        if chunk.get("pinned"):
            if src not in pinned_by_source:
                pinned_by_source[src] = []
            pinned_by_source[src].append(chunk)
        elif src.startswith("sqlite::context_entry::"):
            if chunk not in sqlite_entries:
                sqlite_entries.append(chunk)
        else:
            if src not in normal_files_by_source:
                normal_files_by_source[src] = []
            normal_files_by_source[src].append(chunk)

    try:
        from Evelyn.tools.string_utils import (
            build_context_retrieval_envelope,
            escape_xml_content,
            wrap_xml_envelope,
        )
    except ImportError:
        from string_utils import (
            build_context_retrieval_envelope,
            escape_xml_content,
            wrap_xml_envelope,
        )

    retrieval_items = []

    # 0. Procedures: Show active repeatable instructions if matched
    if matching_procedures:
        for proc in matching_procedures:
            proc_children = [
                f"<steps>\n{escape_xml_content(proc['steps'])}\n</steps>"
            ]
            if proc.get("suggested_tools"):
                proc_children.append(f"<suggested_tools>{escape_xml_content(proc['suggested_tools'])}</suggested_tools>")
            if proc.get("pitfalls"):
                proc_children.append(f"<pitfalls>{escape_xml_content(proc['pitfalls'])}</pitfalls>")
            if proc.get("verification"):
                proc_children.append(f"<verification>{escape_xml_content(proc['verification'])}</verification>")
            retrieval_items.append(
                wrap_xml_envelope(
                    "protocol",
                    body=proc_children,
                    trigger_pattern=proc.get("trigger_pattern", ""),
                )
            )

    # 1. Pinned (Primary Source) Documents: Show full content of matching chunks in order
    for src, chunks in pinned_by_source.items():
        chunks.sort(key=lambda x: x.get("metadata", {}).get("chunk", 0))
        rel_path = get_vault_relative_path(src)
        content_parts = [clean_rag_chunk_content(c.get("content", "")) for c in chunks if clean_rag_chunk_content(c.get("content", ""))]
        full_content = "\n...\n".join(content_parts)
        retrieval_items.append(
            wrap_xml_envelope(
                "document",
                body=escape_xml_content(full_content),
                type="primary_source",
                path=rel_path,
            )
        )

    # 2. SQLite Context Entries: Show observation text
    for chunk in sqlite_entries:
        src = chunk["source"]
        entry_id = src.rsplit("::", 1)[-1]
        retrieval_items.append(
            wrap_xml_envelope(
                "memory_entry",
                body=escape_xml_content(chunk["content"]),
                id=entry_id,
            )
        )

    # 3. Vault Documents: Show direct relevant content chunks with abstract anchoring
    for src, chunks in normal_files_by_source.items():
        chunks.sort(key=lambda x: x.get("metadata", {}).get("chunk", 0))
        rel_path = get_vault_relative_path(src)
        first_meta = chunks[0].get("metadata", {})

        title = first_meta.get("title", "")
        if not title:
            title = os.path.splitext(os.path.basename(src))[0]

        tags_raw = first_meta.get("tags", "")

        content_parts = [clean_rag_chunk_content(c.get("content", "")) for c in chunks if clean_rag_chunk_content(c.get("content", ""))]
        matched_content = "\n...\n".join(content_parts)

        # Abstract anchoring: If abstract is available in metadata and not already in excerpt
        abstract = first_meta.get("abstract", "")
        if abstract and isinstance(abstract, str) and abstract.strip():
            abstract_clean = abstract.strip()
            if abstract_clean not in matched_content:
                matched_content = f"[!ABSTRACT]\n{abstract_clean}\n\n{matched_content}"

        doc_attrs = {
            "path": rel_path,
            "title": title,
        }
        if tags_raw:
            doc_attrs["tags"] = tags_raw

        retrieval_items.append(
            wrap_xml_envelope(
                "document",
                body=escape_xml_content(matched_content),
                **doc_attrs,
            )
        )

    return build_context_retrieval_envelope(source="vault", query=query, items=retrieval_items)


def find_semantic_neighbors(
    query_or_text: str,
    collection_name: str = "evelyn_memory",
    limit: int = 3,
    min_similarity: float = 0.65,
    exclude_source: str = "",
) -> list[dict]:
    """Find the top semantically related vault notes for a given text snippet or document.

    Args:
        query_or_text: Search text snippet, gist, or summary.
        collection_name: Chroma collection to query.
        limit: Maximum number of distinct related notes to return.
        min_similarity: Minimum cosine similarity threshold (0.0 to 1.0).
        exclude_source: File path to exclude from the results (e.g. self).

    Returns:
        list[dict]: List of dicts with 'source', 'title', 'similarity', 'snippet', 'tags'.
    """
    if not query_or_text or not query_or_text.strip():
        return []

    # Query for extra raw chunks so we can deduplicate by source note
    raw_chunks = query_collection(query_or_text, collection_name=collection_name, n_results=limit * 4)
    if not raw_chunks:
        return []

    exclude_norm = exclude_source.replace('\\', '/').lower() if exclude_source else ""
    by_source: dict[str, dict] = {}

    for chunk in raw_chunks:
        src = chunk.get("source", "")
        if not src or src.startswith("sqlite::"):
            continue
        src_norm = src.replace('\\', '/').lower()
        if exclude_norm and (exclude_norm in src_norm or src_norm in exclude_norm):
            continue

        dist = chunk.get("distance", 1.0)
        # Cosine distance: 0 = identical, 1 = orthogonal, 2 = opposite
        similarity = max(0.0, 1.0 - (dist / 2.0)) if dist > 1.0 else max(0.0, 1.0 - dist)
        if similarity < min_similarity:
            continue

        if src not in by_source or by_source[src]["similarity"] < similarity:
            meta = chunk.get("metadata") or {}
            title = meta.get("title") or ""
            if not title:
                title = os.path.splitext(os.path.basename(src))[0]
                title = re.sub(r"^(Ch\d+\s*[-_:]\s*|EX_\d+\s*[-_:]\s*)", "", title).strip()

            tags = meta.get("tags") or ""
            by_source[src] = {
                "source": src,
                "title": title,
                "similarity": round(float(similarity), 3),
                "snippet": chunk.get("content", "")[:300].strip(),
                "tags": tags,
            }

    results = sorted(by_source.values(), key=lambda x: x["similarity"], reverse=True)
    return results[:limit]

