"""
chroma_rag.py — Chroma vector DB wrapper for Evelyn's RAG pipeline.

Provides:
  - ingest_markdown_file()    — add/update a file in a collection (auto-chunked)
  - delete_document()         — remove all chunks for a document by source path
  - query_collection()        — retrieve top-K relevant chunks for a query
  - get_or_create_collection() — idempotently get a named collection
  - build_rag_context()       — query both collections, apply priority boosting
                                and pinned doc injection, return formatted block

Collections:
  evelyn_memory  — full markdown files (journals, context entries)
  evelyn_gists   — LLM-generated gist summaries from vault map

Chunking:
  nomic-embed-text has a hard 8192-token context limit. Files are split into
  overlapping chunks before embedding to avoid HTTP 400 errors on large files.

Priority & Pinning:
  Documents ingested with rag_priority=high|low receive a score multiplier
  that adjusts their effective cosine distance before threshold filtering.
  Documents with rag_pinned=true are guaranteed-injected into context when any
  of their aliases appear in the user query, regardless of cosine score.
"""

import os
import re
import chromadb
from chromadb.utils import embedding_functions

import evelyn_config as cfg

# ---------------------------------------------------------------------------
# Chunking config
# ---------------------------------------------------------------------------
# nomic-embed-text has a hard 8192-token context limit (~6000 chars for typical
# text). We chunk conservatively at 1800 chars with 200-char overlap so no
# single embed call can exceed the limit, even for dense markdown.
CHUNK_SIZE    = 1800  # chars per chunk
CHUNK_OVERLAP = 200   # chars of overlap between consecutive chunks


def chunk_text(content: str) -> list[str]:
    """
    Split content into overlapping chunks of at most CHUNK_SIZE characters.

    Tries to split on paragraph boundaries (double newline) first for
    cleaner semantic chunks; falls back to hard character splits.
    Returns a list of at least one chunk (even for empty content).
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

def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=cfg.CHROMA_DB_PATH)
    return _client


def _get_embedding_fn():
    """Use ChromaDB's built-in default embedding model (all-MiniLM-L6-v2 via ONNX).

    Runs entirely on CPU (~100ms per query). This avoids calling Ollama for
    embeddings, which would evict the main chat model from VRAM on every turn
    and cause a ~20-30 second model swap penalty.
    """
    return embedding_functions.DefaultEmbeddingFunction()


def get_or_create_collection(name: str) -> chromadb.Collection:
    """Idempotently get or create a named Chroma collection."""
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
    """
    Upsert a markdown file into a Chroma collection, split into chunks.

    Each chunk is stored as a separate document with ID:
        {file_path}::chunk-{n}

    Re-ingesting the same file first removes all old chunks for that path,
    then upserts the new set — so the chunk count can grow or shrink cleanly.

    Args:
        file_path:       Absolute path — used as the document ID prefix.
        content:         Text content to embed and store.
        collection_name: Target collection (evelyn_memory or evelyn_gists).
        extra_metadata:  Optional extra fields stored alongside each chunk.
                         Supports: rag_priority, rag_pinned, aliases.

    Returns:
        True on success, False on failure.
    """
    try:
        col = get_or_create_collection(collection_name)

        # Remove old chunks for this file before upserting the new set
        _delete_chunks_by_source(col, file_path)

        chunks = chunk_text(content)
        ids       = [f"{file_path}::chunk-{i}" for i in range(len(chunks))]
        metadatas = []
        for i in range(len(chunks)):
            meta = {"source": file_path, "chunk": i, "total_chunks": len(chunks)}
            if extra_metadata:
                # Chroma metadata values must be str/int/float/bool — coerce booleans
                for k, v in extra_metadata.items():
                    if isinstance(v, bool):
                        meta[k] = v  # Chroma supports bool natively
                    else:
                        meta[k] = v
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
    """Internal: delete all chunk documents for a given source path."""
    try:
        results = col.get(where={"source": file_path}, include=[])
        ids_to_delete = results.get("ids", [])
        if ids_to_delete:
            col.delete(ids=ids_to_delete)
    except Exception as e:
        print(f"[chroma_rag] chunk cleanup failed for {file_path}: {e}")


def delete_document(file_path: str, collection_name: str) -> bool:
    """Remove all chunks for a document from a collection by source path."""
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
    """
    Retrieve the top-K most relevant chunks from a collection.

    Args:
        query:           Natural language query string.
        collection_name: Collection to search.
        n_results:       Number of results (defaults to cfg.RAG_TOP_K).

    Returns:
        List of dicts with keys: 'content', 'source', 'distance', 'metadata'.
        Empty list if collection is empty or query fails.
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
        print(f"[chroma_rag] query failed ({collection_name}): {e}")
        return []


def _apply_priority_boost(chunks: list[dict]) -> list[dict]:
    """
    Adjust each chunk's distance based on its rag_priority metadata.

    Uses cfg.RAG_PRIORITY_MULTIPLIERS:
      high   → distance × 0.75  (moves chunk closer, raises rank)
      normal → distance × 1.0   (unchanged)
      low    → distance × 1.25  (pushes chunk further, lowers rank)

    Returns the same list with distances updated in place, re-sorted.
    """
    multipliers = getattr(cfg, "RAG_PRIORITY_MULTIPLIERS", {"high": 0.75, "normal": 1.0, "low": 1.25})
    for c in chunks:
        priority = c["metadata"].get("rag_priority", "normal")
        c["distance"] = c["distance"] * multipliers.get(priority, 1.0)
    chunks.sort(key=lambda x: x["distance"])
    return chunks


def _fetch_pinned_chunks(query: str) -> list[dict]:
    """
    Scan both collections for pinned documents whose aliases appear in the query.

    For each matched pinned document, fetch up to RAG_PINNED_MAX_CHUNKS chunks
    directly by source path (bypassing cosine search) and return them.

    These chunks are prepended to the context regardless of distance score.
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
                    print(
                        f"[RAG] PINNED '{os.path.basename(src)}' matched alias in query",
                        flush=True,
                    )

        except Exception as e:
            print(f"[chroma_rag] pinned fetch failed ({collection_name}): {e}")

    return pinned


def build_rag_context(query: str) -> str:
    """
    Query both collections and return a formatted context block for injection
    into the system prompt.

    Pipeline:
      1. Pinned doc scan — guaranteed inject for any contact/primary-source
         whose alias appears in the query.
      2. Normal top-K vector search across both collections.
      3. Priority re-ranking — adjust distances by rag_priority multiplier.
      4. Distance threshold filter — drop chunks above RAG_DISTANCE_THRESHOLD.
      5. Deduplicate — pinned chunks already in context are not duplicated.
      6. Assemble formatted block, pinned chunks always listed first.

    Returns an empty string if nothing passes the threshold or no query matches,
    so no context block is injected for casual turns.
    """
    # Step 1: Pinned guaranteed chunks
    pinned_chunks = _fetch_pinned_chunks(query)
    pinned_sources = {c["source"] for c in pinned_chunks}

    # Step 2: Normal vector search
    memory_chunks = query_collection(query, cfg.CHROMA_MEMORY_COLLECTION)
    gist_chunks   = query_collection(query, cfg.CHROMA_GISTS_COLLECTION)
    all_chunks = memory_chunks + gist_chunks

    # Step 3: Priority re-ranking
    all_chunks = _apply_priority_boost(all_chunks)

    threshold = cfg.RAG_DISTANCE_THRESHOLD

    # Step 4 & 5: Filter by threshold and deduplicate against pinned
    if cfg.DEBUG_LOGGING:
        for c in all_chunks:
            kept = "KEEP" if c["distance"] <= threshold else "DROP"
            pinned_flag = " [already pinned]" if c["source"] in pinned_sources else ""
            priority = c["metadata"].get("rag_priority", "normal")
            print(
                f"[RAG] {kept} dist={c['distance']:.3f} priority={priority}"
                f" src={os.path.basename(c['source'])}{pinned_flag}",
                flush=True,
            )

    relevant = [
        c for c in all_chunks
        if c["distance"] <= threshold and c["source"] not in pinned_sources
    ]

    # Step 6: Assemble
    all_context = pinned_chunks + relevant
    if not all_context:
        return ""

    parts = ["--- Retrieved Context ---"]
    for chunk in all_context:
        src = os.path.basename(chunk["source"])
        pin_marker = " [primary source]" if chunk.get("pinned") else ""
        parts.append(f"[{src}{pin_marker}]\n{chunk['content']}")
    parts.append("--- End Context ---")
    return "\n\n".join(parts)
