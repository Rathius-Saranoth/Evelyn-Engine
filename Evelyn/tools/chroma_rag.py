"""
chroma_rag.py — Chroma vector DB wrapper for Evelyn's RAG pipeline.

Provides:
  - ingest_markdown_file()    — add/update a file in a collection (auto-chunked)
  - delete_document()         — remove all chunks for a document by source path
  - query_collection()        — retrieve top-K relevant chunks for a query
  - get_or_create_collection() — idempotently get a named collection

Collections:
  evelyn_memory  — full markdown files (journals, context entries)
  evelyn_gists   — LLM-generated gist summaries from vault map

Chunking:
  nomic-embed-text has a hard 8192-token context limit. Files are split into
  overlapping chunks before embedding to avoid HTTP 400 errors on large files.
"""

import os
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
    """Use nomic-embed-text via Ollama for embeddings (same model already running)."""
    return embedding_functions.OllamaEmbeddingFunction(
        url=f"{cfg.OLLAMA_URL}/api/embeddings",
        model_name="nomic-embed-text",
    )


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
                meta.update(extra_metadata)
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
        List of dicts with keys: 'content', 'source', 'distance'.
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
        )
        chunks = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            chunks.append({
                "content": doc,
                "source": meta.get("source", ""),
                "distance": dist,
            })
        return chunks
    except Exception as e:
        print(f"[chroma_rag] query failed ({collection_name}): {e}")
        return []


def build_rag_context(query: str) -> str:
    """
    Query both collections and return a formatted context block for injection
    into the system prompt.

    Chunks with cosine distance above cfg.RAG_DISTANCE_THRESHOLD are discarded
    as too dissimilar to be useful. Returns an empty string if nothing passes
    the threshold so no context block is injected for casual turns.
    """
    memory_chunks = query_collection(query, cfg.CHROMA_MEMORY_COLLECTION)
    gist_chunks   = query_collection(query, cfg.CHROMA_GISTS_COLLECTION)
    all_chunks = memory_chunks + gist_chunks

    if not all_chunks:
        return ""

    threshold = cfg.RAG_DISTANCE_THRESHOLD
    relevant = [c for c in all_chunks if c["distance"] <= threshold]

    # Debug: show what was retrieved and what was kept
    if cfg.DEBUG_LOGGING:
        for c in all_chunks:
            kept = "KEEP" if c["distance"] <= threshold else "DROP"
            print(
                f"[RAG] {kept} dist={c['distance']:.3f} src={os.path.basename(c['source'])}",
                flush=True,
            )

    if not relevant:
        return ""

    parts = ["--- Retrieved Context ---"]
    for chunk in relevant:
        src = os.path.basename(chunk["source"])
        parts.append(f"[{src}]\n{chunk['content']}")
    parts.append("--- End Context ---")
    return "\n\n".join(parts)
