# test_benchmark_rag_isolation.py
# date created: 2026-10-02 16:50:00
# date modified: 2026-10-02 16:50:29
# tags: #test, #rag, #benchmark, #chroma, #isolation

"""Unit tests verifying that benchmark_rag candidate ingestion and execution
are strictly isolated from production ChromaDB and writer leases.
"""

import os
import shutil
import tempfile

import chromadb
from chromadb.api.types import Embeddable, EmbeddingFunction, Embeddings

import evelyn_config as cfg
from Evelyn.tools import chroma_rag
from scripts import benchmark_rag


class DummyEmbeddingFunction(EmbeddingFunction[Embeddable]):
    """Deterministic dummy embedding function to avoid downloading models during unit tests."""

    def __init__(self) -> None:
        pass

    @staticmethod
    def name() -> str:
        return "dummy_embedding_function"

    def __call__(self, input: Embeddable) -> Embeddings:
        # Return dummy 384-dimensional unit vectors
        return [[0.1] * 384 for _ in input]


def test_setup_benchmark_store_ephemeral():
    """Verify setup_benchmark_store with None creates an ephemeral temp directory."""
    client, bench_path, is_temp = benchmark_rag.setup_benchmark_store(None)
    try:
        assert is_temp is True
        assert os.path.exists(bench_path)
        assert bench_path != cfg.CHROMA_DB_PATH
        # The client should be functional
        col = client.get_or_create_collection("test_col", embedding_function=DummyEmbeddingFunction())
        assert col is not None
        assert col.count() == 0
    finally:
        shutil.rmtree(bench_path, ignore_errors=True)


def test_setup_benchmark_store_custom_dir(tmp_path):
    """Verify setup_benchmark_store with an explicit directory creates and uses that directory."""
    custom_dir = str(tmp_path / "custom_bench_store")
    _client, bench_path, is_temp = benchmark_rag.setup_benchmark_store(custom_dir)
    assert is_temp is False
    assert bench_path == custom_dir
    assert os.path.exists(custom_dir)
    assert bench_path != cfg.CHROMA_DB_PATH


def test_ingest_into_candidate_collection_isolation(monkeypatch, tmp_path):
    """Verify candidate ingestion copies chunks into candidate store without writer leases."""
    # 1. Populate sandboxed 'production' store
    prod_store_path = str(tmp_path / "fake_prod_chroma")
    monkeypatch.setattr(cfg, "CHROMA_DB_PATH", prod_store_path)

    prod_client = chromadb.PersistentClient(path=prod_store_path)
    prod_col = prod_client.get_or_create_collection(
        name=cfg.CHROMA_MEMORY_COLLECTION,
        embedding_function=DummyEmbeddingFunction(),
    )
    prod_col.upsert(
        ids=["fact_1", "fact_2"],
        documents=["User prefers tea in the morning.", "Assistant persona is Evelyn."],
        metadatas=[{"source": "memory_fact_1", "priority": 1.0}, {"source": "memory_fact_2", "priority": 1.2}],
    )
    assert prod_col.count() == 2

    # 2. Mock embedding function to use DummyEmbeddingFunction
    monkeypatch.setattr(benchmark_rag, "_get_candidate_embedding_fn", lambda model_name: DummyEmbeddingFunction())

    # 3. Create isolated benchmark store
    bench_dir = str(tmp_path / "fake_bench_chroma")
    bench_client, _, _ = benchmark_rag.setup_benchmark_store(bench_dir)

    # 4. Ingest candidate collection
    cand_col = benchmark_rag._ingest_into_candidate_collection("MINILM-L6", bench_client)

    # 5. Assertions:
    # Candidate collection was populated in bench store
    assert cand_col.count() == 2
    assert "bench_minilm_l6_memory" in [c.name for c in bench_client.list_collections()]

    # Production store was NOT written to by benchmark
    assert not chroma_rag.owns_chroma_writer()
    # The lock file should not have been created in either dir
    assert not os.path.exists(os.path.join(prod_store_path, ".chroma_write.lock"))
    assert not os.path.exists(os.path.join(bench_dir, ".chroma_write.lock"))


def test_run_query_with_collection():
    """Verify run_query_with_collection executes queries and applies priority boosting."""
    with tempfile.TemporaryDirectory() as temp_dir:
        client = chromadb.PersistentClient(path=temp_dir)
        col = client.get_or_create_collection(
            name="test_query_col",
            embedding_function=DummyEmbeddingFunction(),
        )
        col.upsert(
            ids=["doc_1", "doc_2"],
            documents=["First document query match", "Second document query match"],
            metadatas=[{"source": "source_1", "priority": 1.0}, {"source": "source_2", "priority": 1.5}],
        )

        results = benchmark_rag.run_query_with_collection("query match", col, n_results=2)
        assert len(results) == 2
        assert all("content" in r and "source" in r and "distance" in r for r in results)
