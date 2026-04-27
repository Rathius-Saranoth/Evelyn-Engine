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

Embedding model:
  all-MiniLM-L6-v2 (22.7M params, 384-dim) via Chroma's built-in ONNX runtime.
  Runs on CPU (~100ms/query) to avoid VRAM eviction of the main chat model.
  Hard context limit: 256 WordPiece tokens (~1000 chars). Chunks exceeding this
  are silently truncated at embedding time.

Index:
  HNSW (Hierarchical Navigable Small World) with cosine distance metric.
  Cosine distance range: 0.0 (identical) to 1.0 (orthogonal).

Chunking:
  Files are split into overlapping chunks of ~1000 chars to stay within the
  embedding model's 256-token context window. YAML frontmatter is stripped
  before chunking since it is stored separately as Chroma metadata.

Priority & Pinning:
  Documents ingested with rag_priority=high|low receive a score multiplier
  that adjusts their effective cosine distance before threshold filtering.
  Note: priority boost can promote chunks past the distance threshold.
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
# all-MiniLM-L6-v2 has a 256 WordPiece token context window (~1000 chars for
# typical English text). We chunk at 1000 chars with 150-char overlap to stay
# within the embedding window and avoid silent truncation.
CHUNK_SIZE    = 1000  # chars per chunk (fits ~250 tokens)
CHUNK_OVERLAP = 150   # chars of overlap between consecutive chunks


def chunk_text(content: str) -> list[str]:
    """
    Split content into overlapping chunks of at most CHUNK_SIZE characters.

    Tries to split on paragraph boundaries (double newline) first for
    cleaner semantic chunks; falls back to hard character splits.
    Returns a list of at least one chunk (even for empty content).

    Note: CHUNK_SIZE (1000 chars) is calibrated to fit within the embedding
    model's 256-token context window. Chunks exceeding the token limit are
    silently truncated by the model, degrading retrieval accuracy.
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
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=cfg.CHROMA_DB_PATH)
    return _client


def _get_embedding_fn():
    """Return a cached instance of Chroma's default embedding model.

    Model: all-MiniLM-L6-v2 (22.7M params, 384-dim, 256-token context).
    Runs via ONNX on CPU (~100ms per query). Cached as a module-level
    singleton to avoid re-loading the ONNX session on every call.

    This avoids calling Ollama for embeddings, which would evict the main
    chat model from VRAM and cause a ~20-30 second model swap penalty.
    """
    global _embedding_fn
    if _embedding_fn is None:
        _embedding_fn = embedding_functions.DefaultEmbeddingFunction()
    return _embedding_fn


def get_or_create_collection(name: str) -> chromadb.Collection:
    """Idempotently get or create a named Chroma collection.

    Configures the collection with:
      - Embedding: all-MiniLM-L6-v2 via ONNX (CPU, cached singleton)
      - Index: HNSW (Hierarchical Navigable Small World)
      - Distance: cosine (0.0 = identical, 1.0 = orthogonal)

    The HNSW index and cosine metric are set at creation time and cannot
    be changed without recreating the collection.
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
    """
    Upsert a markdown file into a Chroma collection, split into chunks.

    Each chunk is stored as a separate document with ID:
        {file_path}::chunk-{n}

    Re-ingesting the same file first removes all old chunks for that path,
    then upserts the new set — so the chunk count can grow or shrink cleanly.

    YAML frontmatter (---\n...\n---) is stripped before chunking since the
    ingestion scripts extract metadata fields separately. This preserves
    embedding token budget for actual content.

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

    Uses HNSW index with cosine distance for approximate nearest-neighbor search.

    Args:
        query:           Natural language query string.
        collection_name: Collection to search.
        n_results:       Number of results (defaults to cfg.RAG_TOP_K).

    Returns:
        List of dicts with keys: 'content', 'source', 'distance', 'metadata'.
        Distance is cosine distance: 0.0 = identical, 1.0 = orthogonal.
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
        # Always log query failures (not debug-gated) — a failed query means
        # total RAG context loss for this turn, which should never be silent.
        print(f"[chroma_rag] QUERY FAILED ({collection_name}): {e}", flush=True)
        return []


def _apply_priority_boost(chunks: list[dict]) -> list[dict]:
    """
    Adjust each chunk's distance based on its rag_priority metadata.

    Uses cfg.RAG_PRIORITY_MULTIPLIERS:
      high   → distance × 0.75  (moves chunk closer, raises rank)
      normal → distance × 1.0   (unchanged)
      low    → distance × 1.25  (pushes chunk further, lowers rank)

    Note: boosted distances can cross the RAG_DISTANCE_THRESHOLD, promoting
    otherwise-irrelevant chunks into context. For example, a 'high' priority
    chunk at raw distance 0.72 becomes 0.54, passing a 0.55 threshold.
    This is by design — priority is an intentional relevance override.

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


def build_rag_context(query: str) -> str:
    """
    Query both collections and return a formatted context block for injection
    into the system prompt.

    Pipeline:
      0. Query reformulation — extract search keywords from conversational text.
      1. Pinned doc scan — guaranteed inject for any contact/primary-source
         whose alias appears in the ORIGINAL query (substring match, not semantic).
      2. Normal top-K vector search across both collections using the
         REFORMULATED query for better embedding accuracy.
      3. Priority re-ranking — adjust distances by rag_priority multiplier.
      4. Distance threshold filter — drop chunks above RAG_DISTANCE_THRESHOLD.
      5. Deduplicate — pinned chunks already in context are not duplicated.
      6. Assemble formatted block, pinned chunks always listed first.

    Returns an empty string if nothing passes the threshold or no query matches,
    so no context block is injected for casual turns.
    """
    # Step 0: Query reformulation — extract search keywords from conversational text
    from query_reformulator import reformulate_query
    search_query = reformulate_query(query)

    # Step 1: Pinned guaranteed chunks (uses ORIGINAL query for alias substring matching)
    pinned_chunks = _fetch_pinned_chunks(query)
    pinned_sources = {c["source"] for c in pinned_chunks}

    # Step 2: Normal vector search (uses REFORMULATED query for semantic matching)
    memory_chunks = query_collection(search_query, cfg.CHROMA_MEMORY_COLLECTION)
    gist_chunks   = query_collection(search_query, cfg.CHROMA_GISTS_COLLECTION)
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
