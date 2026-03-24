"""
chroma_rag.py — Chroma vector DB wrapper for Evelyn's RAG pipeline.

Provides:
  - ingest_markdown_file()    — add/update a file in a collection
  - delete_document()         — remove a document by its source path ID
  - query_collection()        — retrieve top-K relevant chunks for a query
  - get_or_create_collection() — idempotently get a named collection

Collections:
  evelyn_memory  — full markdown files (journels, context entries)
  evelyn_gists   — LLM-generated gist summaries from vault map
"""

import os
import chromadb
from chromadb.utils import embedding_functions

import evelyn_config as cfg

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
    Upsert a markdown file into a Chroma collection.

    Uses the file_path as the document ID so re-ingesting the same file
    replaces the previous version cleanly.

    Args:
        file_path:       Absolute path — used as the unique document ID.
        content:         Text content to embed and store.
        collection_name: Target collection (evelyn_memory or evelyn_gists).
        extra_metadata:  Optional extra fields stored alongside the document.

    Returns:
        True on success, False on failure.
    """
    try:
        col = get_or_create_collection(collection_name)
        meta = {"source": file_path}
        if extra_metadata:
            meta.update(extra_metadata)
        col.upsert(
            ids=[file_path],
            documents=[content],
            metadatas=[meta],
        )
        return True
    except Exception as e:
        print(f"[chroma_rag] ingest failed for {file_path}: {e}")
        return False


def delete_document(file_path: str, collection_name: str) -> bool:
    """Remove a document from a collection by its source file path ID."""
    try:
        col = get_or_create_collection(collection_name)
        col.delete(ids=[file_path])
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

    Returns an empty string if nothing relevant is found.
    """
    memory_chunks = query_collection(query, cfg.CHROMA_MEMORY_COLLECTION)
    gist_chunks   = query_collection(query, cfg.CHROMA_GISTS_COLLECTION)
    all_chunks = memory_chunks + gist_chunks

    if not all_chunks:
        return ""

    parts = ["--- Retrieved Context ---"]
    for chunk in all_chunks:
        src = os.path.basename(chunk["source"])
        parts.append(f"[{src}]\n{chunk['content']}")
    parts.append("--- End Context ---")
    return "\n\n".join(parts)
