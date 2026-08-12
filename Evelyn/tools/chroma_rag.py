
# chroma_rag.py
# date created: 2026-03-23 15:39:48
# date modified: 2026-06-27 09:16:06
# tags: #rag, #vector, #chromadb, #embeddings, #query

# Chroma Rag.py

"""
chroma_rag.py — Chroma vector DB wrapper for Evelyn's RAG pipeline.

Exports:
  ingest_markdown_file()     — Add/update a file in a collection (auto-chunked).
  delete_document()          — Remove all chunks for a document by source path.
  query_collection()         — Retrieve top-K relevant chunks for a query.
  get_or_create_collection() — Idempotently get a named Chroma collection.
  build_rag_context()        — Query both collections, apply priority boosting and
                               pinned doc injection; return formatted context block.
                               Also fires touch_entry_retrieved() for SQLite context
                               entries served to the model (retrieval tracking).

Collections: evelyn_memory (full markdown files), evelyn_gists (LLM-generated gist summaries)

Embedding model: all-MiniLM-L6-v2 (22.7M params, 384-dim) via Chroma's ONNX runtime.
  CPU-only to avoid VRAM eviction of the chat model. Hard context: 256 WordPiece tokens
  (~1000 chars). Chunks exceeding the limit are silently truncated by the model.

Index: HNSW with cosine distance (0.0 = identical, 1.0 = orthogonal).
Chunking: ~1000-char overlapping chunks, YAML frontmatter stripped before embedding.
Priority/Pinning: rag_priority multiplier adjusts cosine distance before threshold filter;
  rag_pinned=true guarantees injection when any alias appears in the query.
"""


import os
import re
import chromadb
from chromadb.utils import embedding_functions

import evelyn_config as cfg

# ---------------------------------------------------------------------------
# Chunking config
# ---------------------------------------------------------------------------
# BAAI/bge-large-en-v1.5 has a 512 WordPiece token context window (~2000 chars).
# We chunk at 1600 chars (~400 tokens) with 200-char overlap to maximize note integrity
# and preserve full paragraphs without truncation.
CHUNK_SIZE    = 1600  # chars per chunk (fits ~400 tokens)
CHUNK_OVERLAP = 200   # chars of overlap between consecutive chunks


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
# Ingest
# ---------------------------------------------------------------------------

def ingest_markdown_file(file_path: str, content: str, collection_name: str,
                          extra_metadata: dict = None) -> bool:
    """Upsert a markdown file into a Chroma collection, split into chunks.

    Args:
        file_path: Absolute path — used as the document ID prefix.
        content: Text content to embed and store.
        collection_name: Target collection (evelyn_memory or evelyn_gists).
        extra_metadata: Optional extra fields stored alongside each chunk.

    Returns:
        bool: True on success, False on failure.
    """
    try:
        col = get_or_create_collection(collection_name)

        # Remove old chunks for this file before upserting the new set
        _delete_chunks_by_source(col, file_path)

        # Strip YAML frontmatter before chunking — metadata is already
        # extracted by the ingestion scripts and stored via extra_metadata.
        clean_content = re.sub(r"^---\n.*?\n---\n?", "", content, count=1, flags=re.DOTALL)

        chunks = chunk_text(clean_content)
        ids       = [f"{file_path}::chunk-{i}" for i in range(len(chunks))]
        metadatas = []
        for i in range(len(chunks)):
            meta = {"source": file_path, "chunk": i, "total_chunks": len(chunks)}
            if extra_metadata:
                meta.update(extra_metadata)
            # Defaults so the fields always exist for query-time inspection
            meta.setdefault("rag_priority", "normal")
            meta.setdefault("rag_pinned", False)
            meta.setdefault("aliases", "")
            metadatas.append(meta)

        col.upsert(ids=ids, documents=chunks, metadatas=metadatas)
        return True
    except Exception as e:
        print(f"[chroma_rag] ingest failed for {file_path}: {e}")
        return False


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
    except Exception as e:
        print(f"[chroma_rag] chunk cleanup failed for {file_path}: {e}")


def delete_document(file_path: str, collection_name: str) -> bool:
    """Remove all chunks for a document from a collection by source path.

    Args:
        file_path: The source path of the document to delete.
        collection_name: The name of the collection.

    Returns:
        bool: True if deletion was successful, False otherwise.
    """
    try:
        col = get_or_create_collection(collection_name)
        _delete_chunks_by_source(col, file_path)
        return True
    except Exception as e:
        print(f"[chroma_rag] delete failed for {file_path}: {e}")
        return False


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

def query_collection(query: str, collection_name: str, n_results: int = None) -> list[dict]:
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
            results["distances"][0],
        ):
            chunks.append({
                "content":  doc,
                "source":   meta.get("source", ""),
                "distance": dist,
                "metadata": meta,
            })
        return chunks
    except Exception as e:
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
        priority = c["metadata"].get("rag_priority", "normal")
        c["distance"] = c["distance"] * multipliers.get(priority, 1.0)
    chunks.sort(key=lambda x: x["distance"])
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

    for collection_name in [cfg.CHROMA_MEMORY_COLLECTION, cfg.CHROMA_GISTS_COLLECTION]:
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

            # Check which pinned docs' aliases appear in the query
            for src, aliases in pinned_sources.items():
                if src in seen_sources:
                    continue
                matched = any(alias in query_lower for alias in aliases if alias)
                if not matched:
                    continue

                # Fetch the actual chunks for this source
                chunk_results = col.get(
                    where={"source": src},
                    include=["documents", "metadatas"],
                )
                docs = chunk_results.get("documents", [])
                metas = chunk_results.get("metadatas", [])
                for doc, meta in list(zip(docs, metas))[:max_chunks]:
                    pinned.append({
                        "content":  doc,
                        "source":   src,
                        "distance": 0.0,  # Pinned docs bypass distance scoring
                        "metadata": meta,
                        "pinned":   True,
                    })
                seen_sources.add(src)
                if cfg.DEBUG_LOGGING:
                    matched_alias = next((a for a in aliases if a and a in query_lower), "?")
                    chunks_injected = min(len(docs), max_chunks)
                    print(
                        f"[RAG] PINNED src={os.path.basename(src)}"
                        f" matched_alias='{matched_alias}'"
                        f" aliases_checked={len(aliases)}"
                        f" chunks_injected={chunks_injected}",
                        flush=True,
                    )

        except Exception as e:
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
    except Exception:
        return path.replace('\\', '/')


def get_document_gist(path: str) -> str | None:
    """Retrieve the gist summary of a document from vault_db SQLite.

    Args:
        path: File path (absolute or relative).

    Returns:
        str | None: The gist summary, or None if not found/error.
    """
    import vault_db
    rel_path = get_vault_relative_path(path)
    try:
        doc = vault_db.get_document(rel_path)
        if doc and doc.get("gist"):
            return doc["gist"]
    except Exception:
        pass
    return None


def build_rag_context(query: str) -> str:
    """Query both collections and return a formatted context block.

    Args:
        query: The raw incoming query string.

    Returns:
        str: A formatted context block of retrieve/pinned chunks, or empty string.
    """
    # Step 0: Query reformulation — extract search keywords from conversational text
    from query_reformulator import reformulate_query
    search_query = reformulate_query(query)

    # Step 1: Pinned guaranteed chunks (uses ORIGINAL query for alias substring matching)
    pinned_chunks = _fetch_pinned_chunks(query)
    pinned_sources = {c["source"] for c in pinned_chunks}

    # Step 2: Normal vector search (uses REFORMULATED query for semantic matching)
    # Query evelyn_memory (full-text index of all vault notes and context entries).
    # evelyn_gists lookups are retired to eliminate redundant vector searches.
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

    # Step 6: Assemble with Progressive Vault Disclosure
    all_context = pinned_chunks + relevant

    # Retrieve procedures (matches user conversational trigger keywords)
    matching_procedures = []
    try:
        import memory_db as _memory_db
        matching_procedures = _memory_db.search_procedures_by_trigger(query, status="live")[:3]
        for proc in matching_procedures:
            _memory_db.touch_procedure_retrieved(proc["id"])
    except Exception as e:
        print(f"[RAG] Warning: procedure search failed: {e}", flush=True)

    if not all_context and not matching_procedures:
        return ""

    # Touch retrieval counters for any SQLite context entries in the result set.
    # These have synthetic source paths: "sqlite::context_entry::{id}".
    # Fire-and-forget — a tracking failure must never affect context delivery.
    try:
        import memory_db as _memory_db
        for _chunk in all_context:
            _src = _chunk.get("source", "")
            if _src.startswith("sqlite::context_entry::"):
                try:
                    _entry_id = int(_src.rsplit("::", 1)[-1])
                    _memory_db.touch_entry_retrieved(_entry_id)
                except Exception:
                    pass
    except Exception:
        pass

    # Separate context types to structure the final block and apply progressive disclosure
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
            # Ensure SQLite entries are unique in our output
            if chunk not in sqlite_entries:
                sqlite_entries.append(chunk)
        else:
            # Standard file-based chunk: keep the first matched chunk to fetch the gist
            if src not in normal_files_by_source:
                normal_files_by_source[src] = chunk

    parts = ["--- Retrieved Context ---"]

    # 0. Procedures: Show active repeatable instructions if matched
    if matching_procedures:
        proc_blocks = []
        for proc in matching_procedures:
            pitfalls_str = f"\nPitfalls to Avoid: {proc['pitfalls']}" if proc.get("pitfalls") else ""
            verif_str = f"\nVerification: {proc['verification']}" if proc.get("verification") else ""
            proc_blocks.append(
                f"[Procedure: {proc['trigger_pattern']}]\n"
                f"Steps:\n{proc['steps']}"
                f"{pitfalls_str}"
                f"{verif_str}"
            )
        parts.append("--- Relevant Procedures ---\n" + "\n\n".join(proc_blocks))

    # 1. Pinned (Primary Source) Documents: Show full content of matching chunks in order
    for src, chunks in pinned_by_source.items():
        chunks.sort(key=lambda x: x.get("metadata", {}).get("chunk", 0))
        rel_path = get_vault_relative_path(src)
        content_parts = [c["content"] for c in chunks]
        full_content = "\n...\n".join(content_parts)
        parts.append(f"[Primary Source Document: {rel_path}]\n{full_content}")

    # 2. SQLite Context Entries: Show observation text
    for chunk in sqlite_entries:
        src = chunk["source"]
        entry_id = src.rsplit("::", 1)[-1]
        parts.append(f"[Context Entry (ID: {entry_id})]\n{chunk['content']}")

    # 3. Normal Vault/Memory Documents: Show Gist Summary
    for src, chunk in normal_files_by_source.items():
        rel_path = get_vault_relative_path(src)
        
        # Get gist summary
        gist = None
        if "gist" in chunk.get("metadata", {}):
            gist = chunk["metadata"]["gist"]
        
        if not gist:
            gist = get_document_gist(src)
            
        if not gist:
            # Fallback snippet
            gist = chunk["content"][:300] + "..." if len(chunk["content"]) > 300 else chunk["content"]

        title = chunk.get("metadata", {}).get("title", "")
        if not title:
            title = os.path.splitext(os.path.basename(src))[0]

        tags_raw = chunk.get("metadata", {}).get("tags", "")
        tags_str = f"Tags: {tags_raw}\n" if tags_raw else ""

        parts.append(
            f"[Vault Document: {rel_path}]\n"
            f"Title: {title}\n"
            f"{tags_str}"
            f"Gist Summary: {gist}\n"
            f"(Note: To read the full content of this document, call recall_specific_memory(file_path=\"{rel_path}\"))"
        )

    parts.append("--- End Context ---")
    return "\n\n".join(parts)

