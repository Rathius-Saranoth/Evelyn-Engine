# benchmark_rag.py
# date created: 2026-04-26 12:18:17
# date modified: 2026-05-25 20:03:05
# tags: #rag, #benchmark, #evaluation, #testing, #metrics

"""
benchmark_rag.py — RAG retrieval accuracy benchmark for Evelyn's Chroma pipeline.

Runs a set of golden queries against build_rag_context() and query_collection(),
measuring how well the retrieval pipeline surfaces expected documents.

Metrics:
  - Recall@K:  Did at least one expected source appear in the top-K results?
  - MRR:       Mean Reciprocal Rank — average of 1/rank for the first expected hit.
  - Hit Rate:  Fraction of queries where at least one expected source matched.

Categories:
  exact_name     — Direct name lookups (Ricky, Schyler, Evelyn)
  semantic       — Natural language questions requiring semantic understanding
  cross_reference — Queries spanning multiple category domains
  temporal       — Time-sensitive queries (recent journals, etc.)
  negative       — Queries that should return few/no relevant results
  ambiguous      — Short or vague queries testing robustness

Usage:
  python benchmark_rag.py                    # Full run, prints table + summary
  python benchmark_rag.py --verbose          # Include per-chunk detail
  python benchmark_rag.py --json             # Output results as JSON

Requires: reference/rag_benchmark_queries.json (golden test set)
"""

import sys
import os
import json
import time
import argparse

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
ROOT_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for _d in (ROOT_DIR, TOOLS_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import chroma_rag  # noqa: E402
import evelyn_config as cfg  # noqa: E402

GOLDEN_FILE = os.path.join(ROOT_DIR, "reference", "rag_benchmark_queries.json")

# ANSI colors
_RST = "\033[0m"
_BLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[91m"
_GRN = "\033[92m"
_YEL = "\033[93m"
_CYN = "\033[96m"


def load_golden_queries(path: str) -> list[dict]:
    """Load and validate the golden query test set."""
    with open(path, "r", encoding="utf-8") as f:
        queries = json.load(f)
    for q in queries:
        assert "id" in q, f"Query missing 'id': {q}"
        assert "query" in q, f"Query missing 'query': {q}"
        assert "expected_sources_contain" in q, f"Query missing 'expected_sources_contain': {q}"
    return queries


def run_query(query: str, n_results: int = None, reformulate: bool = False) -> list[dict]:
    """Run a raw query across both collections, apply priority boost, return all chunks."""
    if n_results is None:
        n_results = cfg.RAG_TOP_K

    # Optionally reformulate the query before embedding
    search_query = query
    if reformulate:
        from query_reformulator import reformulate_query
        search_query = reformulate_query(query)

    all_chunks = chroma_rag.query_collection(search_query, cfg.CHROMA_MEMORY_COLLECTION, n_results)
    all_chunks = chroma_rag._apply_priority_boost(all_chunks)
    return all_chunks


def check_source_match(source_basename: str, expected_patterns: list[str]) -> bool:
    """Check if a source basename matches any of the expected patterns (substring match)."""
    source_lower = source_basename.lower()
    return any(pat.lower() in source_lower for pat in expected_patterns)


def evaluate_query(query_def: dict, all_chunks: list[dict], threshold: float) -> dict:
    """Evaluate a single query against expected sources.

    Returns a result dict with:
      - hit: bool — did any expected source appear in kept chunks?
      - reciprocal_rank: float — 1/rank of first expected hit (0 if no hit)
      - kept_count: int — chunks that passed threshold
      - total_count: int — chunks returned from query
      - first_match_rank: int or None
      - distances: list of (source, distance, matched) tuples
    """
    expected = query_def["expected_sources_contain"]
    is_negative = query_def.get("category") == "negative"

    kept = [c for c in all_chunks if c["distance"] <= threshold]
    distances = []
    first_match_rank = None

    for rank, chunk in enumerate(kept, 1):
        src = os.path.basename(chunk["source"])
        matched = check_source_match(src, expected)
        distances.append((src, chunk["distance"], matched))
        if matched and first_match_rank is None:
            first_match_rank = rank

    if is_negative:
        # For negative tests: success = no chunks passed threshold, or very few
        hit = len(kept) <= 2  # Allow up to 2 marginal hits
        reciprocal_rank = 1.0 if hit else 0.0
    else:
        hit = first_match_rank is not None
        reciprocal_rank = (1.0 / first_match_rank) if first_match_rank else 0.0

    return {
        "id": query_def["id"],
        "query": query_def["query"],
        "category": query_def.get("category", "unknown"),
        "hit": hit,
        "reciprocal_rank": reciprocal_rank,
        "first_match_rank": first_match_rank,
        "kept_count": len(kept),
        "total_count": len(all_chunks),
        "distances": distances,
    }


def print_results_table(results: list[dict], verbose: bool = False):
    """Print a formatted results table to stdout."""
    print(f"\n{_BLD}{'='*80}{_RST}")
    print(f"{_BLD}  RAG Retrieval Benchmark Results{_RST}")
    print(f"{_BLD}{'='*80}{_RST}")
    print(f"  Embedding: all-MiniLM-L6-v2 | Threshold: {cfg.RAG_DISTANCE_THRESHOLD}")
    print(f"  Chunk size: {chroma_rag.CHUNK_SIZE} chars | Top-K: {cfg.RAG_TOP_K}")

    mem_col = chroma_rag.get_or_create_collection(cfg.CHROMA_MEMORY_COLLECTION)
    print(f"  Collections: memory={mem_col.count()} chunks")
    print("-" * 80)

    # Group by category
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r)

    for cat, cat_results in categories.items():
        cat_hits = sum(1 for r in cat_results if r["hit"])
        cat_mrr = sum(r["reciprocal_rank"] for r in cat_results) / len(cat_results) if cat_results else 0
        color = _GRN if cat_hits == len(cat_results) else _YEL if cat_hits > 0 else _RED
        print(f"\n  {_BLD}[{cat.upper()}]{_RST}  Hit Rate: {color}{cat_hits}/{len(cat_results)}{_RST}  MRR: {cat_mrr:.3f}")

        for r in cat_results:
            status = f"{_GRN}+{_RST}" if r["hit"] else f"{_RED}x{_RST}"
            rank_str = f"rank={r['first_match_rank']}" if r["first_match_rank"] else "no match"
            print(
                f"    {status} {r['id']:<30s} kept={r['kept_count']}/{r['total_count']} "
                f"{rank_str:<12s} MRR={r['reciprocal_rank']:.3f}  q=\"{r['query'][:40]}\""
            )
            if verbose and r["distances"]:
                for src, dist, matched in r["distances"][:5]:
                    marker = f"{_GRN}<{_RST}" if matched else " "
                    print(f"      {marker} dist={dist:.3f}  {src}")

    # Summary
    total = len(results)
    hits = sum(1 for r in results if r["hit"])
    overall_mrr = sum(r["reciprocal_rank"] for r in results) / total if total else 0
    non_negative = [r for r in results if r["category"] != "negative"]
    non_neg_hits = sum(1 for r in non_negative if r["hit"])
    non_neg_mrr = sum(r["reciprocal_rank"] for r in non_negative) / len(non_negative) if non_negative else 0

    print("\n" + "-" * 80)
    print(f"  {_BLD}OVERALL{_RST}")
    color = _GRN if hits == total else _YEL if hits > total * 0.7 else _RED
    print(f"    Hit Rate:    {color}{hits}/{total} ({100*hits/total:.0f}%){_RST}")
    print(f"    MRR:         {overall_mrr:.3f}")
    print(f"    (excl. neg)  Hit Rate: {non_neg_hits}/{len(non_negative)} ({100*non_neg_hits/len(non_negative):.0f}%)  MRR: {non_neg_mrr:.3f}")
    print(f"{'='*80}\n")


# ---------------------------------------------------------------------------
# Model comparison: ingest into temp collections with candidate embedding fn
# ---------------------------------------------------------------------------

CANDIDATE_MODELS = {
    "L6": "all-MiniLM-L6-v2",    # current default (baseline)
    "L12": "all-MiniLM-L12-v2",   # deeper, same architecture
}


def _get_candidate_embedding_fn(model_name: str):
    """Create a SentenceTransformer embedding function for a candidate model."""
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    return SentenceTransformerEmbeddingFunction(model_name=model_name)


def _ingest_into_temp_collections(model_key: str):
    """Copy all documents from production collections into temp collections
    using the candidate embedding model. Returns (mem_col_name, gist_col_name)."""
    model_name = CANDIDATE_MODELS[model_key]
    embed_fn = _get_candidate_embedding_fn(model_name)

    client = chroma_rag._get_client()
    mem_temp = f"bench_{model_key}_memory"
    gist_temp = f"bench_{model_key}_gists"

    # Delete existing temp collections if present
    for name in (mem_temp, gist_temp):
        try:
            client.delete_collection(name)
        except Exception:
            pass

    # Create temp collections with candidate embedding fn
    mem_col = client.get_or_create_collection(
        name=mem_temp, embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"},
    )
    gist_col = client.get_or_create_collection(
        name=gist_temp, embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"},
    )

    # Copy data from production collections
    for src_name, dst_col in [
        (cfg.CHROMA_MEMORY_COLLECTION, mem_col),
    ]:
        src_col = chroma_rag.get_or_create_collection(src_name)
        count = src_col.count()
        if count == 0:
            continue

        print(f"  Ingesting {count} chunks from {src_name} -> {dst_col.name}...", flush=True)
        batch_size = 100
        for offset in range(0, count, batch_size):
            batch = src_col.get(
                include=["documents", "metadatas"],
                limit=batch_size,
                offset=offset,
            )
            if batch["ids"]:
                dst_col.upsert(
                    ids=batch["ids"],
                    documents=batch["documents"],
                    metadatas=batch["metadatas"],
                )
            done = min(offset + batch_size, count)
            print(f"    {done}/{count}", end="\r", flush=True)
        print(f"    {count}/{count} done.", flush=True)

    return mem_temp, gist_temp


def _cleanup_temp_collections(model_key: str):
    """Delete temporary benchmark collections."""
    client = chroma_rag._get_client()
    for name in (f"bench_{model_key}_memory", f"bench_{model_key}_gists"):
        try:
            client.delete_collection(name)
        except Exception:
            pass


def run_query_with_collections(query: str, mem_col_name: str, gist_col_name: str,
                                n_results: int = None) -> list[dict]:
    """Run query against specific collection names (for candidate model testing)."""
    if n_results is None:
        n_results = cfg.RAG_TOP_K
    memory_chunks = chroma_rag.query_collection(query, mem_col_name, n_results)
    gist_chunks = chroma_rag.query_collection(query, gist_col_name, n_results)
    all_chunks = memory_chunks + gist_chunks
    all_chunks = chroma_rag._apply_priority_boost(all_chunks)
    return all_chunks


def main():
    parser = argparse.ArgumentParser(description="RAG retrieval benchmark")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-chunk distances")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--golden", default=GOLDEN_FILE, help="Path to golden query file")
    parser.add_argument(
        "--compare", metavar="MODEL",
        help="Compare against a candidate model. Options: " + ", ".join(CANDIDATE_MODELS.keys())
             + ". Ingests into temp collections, benchmarks, cleans up."
    )
    parser.add_argument("--keep-temp", action="store_true",
                        help="Don't delete temp collections after --compare (for re-runs)")
    parser.add_argument("--reformulate", "-r", action="store_true",
                        help="Pass queries through the LLM reformulator before embedding")
    args = parser.parse_args()

    # Suppress debug logging during benchmark
    original_debug = cfg.DEBUG_LOGGING
    cfg.DEBUG_LOGGING = False

    queries = load_golden_queries(args.golden)
    threshold = cfg.RAG_DISTANCE_THRESHOLD

    if args.compare:
        model_key = args.compare.upper()
        if model_key not in CANDIDATE_MODELS:
            print(f"Unknown model: {args.compare}. Options: {', '.join(CANDIDATE_MODELS.keys())}")
            return

        model_name = CANDIDATE_MODELS[model_key]
        print(f"{'='*80}")
        print(f"  Model Comparison: baseline (all-MiniLM-L6-v2) vs {model_name}")
        print(f"{'='*80}")

        # Run baseline first
        print(f"\n--- Baseline (L6) ---")
        print(f"Running {len(queries)} queries...", flush=True)
        start = time.perf_counter()
        baseline_results = []
        for q in queries:
            chunks = run_query(q["query"])
            result = evaluate_query(q, chunks, threshold)
            baseline_results.append(result)
        base_elapsed = time.perf_counter() - start

        # Ingest with candidate model
        print(f"\n--- Candidate ({model_key}: {model_name}) ---")
        print("Ingesting into temp collections (one-time)...", flush=True)
        ingest_start = time.perf_counter()
        mem_temp, gist_temp = _ingest_into_temp_collections(model_key)
        ingest_elapsed = time.perf_counter() - ingest_start
        print(f"Ingest completed in {ingest_elapsed:.1f}s", flush=True)

        # Run candidate benchmark
        print(f"Running {len(queries)} queries...", flush=True)
        start = time.perf_counter()
        candidate_results = []
        for q in queries:
            chunks = run_query_with_collections(q["query"], mem_temp, gist_temp)
            result = evaluate_query(q, chunks, threshold)
            candidate_results.append(result)
        cand_elapsed = time.perf_counter() - start

        cfg.DEBUG_LOGGING = original_debug

        # Print comparison
        print(f"\n{'='*80}")
        print(f"  COMPARISON RESULTS")
        print(f"{'='*80}")

        base_hits = sum(1 for r in baseline_results if r["hit"])
        cand_hits = sum(1 for r in candidate_results if r["hit"])
        base_mrr = sum(r["reciprocal_rank"] for r in baseline_results) / len(baseline_results)
        cand_mrr = sum(r["reciprocal_rank"] for r in candidate_results) / len(candidate_results)

        print(f"\n  {'Metric':<25s} {'Baseline (L6)':>15s} {f'Candidate ({model_key})':>15s} {'Delta':>10s}")
        print(f"  {'-'*65}")
        print(f"  {'Hit Rate':<25s} {f'{base_hits}/{len(queries)}':>15s} {f'{cand_hits}/{len(queries)}':>15s} {f'{cand_hits-base_hits:+d}':>10s}")
        print(f"  {'MRR':<25s} {base_mrr:>15.3f} {cand_mrr:>15.3f} {cand_mrr-base_mrr:>+10.3f}")
        print(f"  {'Query time':<25s} {f'{base_elapsed:.2f}s':>15s} {f'{cand_elapsed:.2f}s':>15s}")

        # Per-query comparison for mismatches
        changes = []
        for b, c in zip(baseline_results, candidate_results):
            if b["hit"] != c["hit"]:
                direction = "GAINED" if c["hit"] else "LOST"
                changes.append((direction, b["id"], b["query"]))

        if changes:
            print(f"\n  Changes:")
            for direction, qid, query in changes:
                color = _GRN if direction == "GAINED" else _RED
                print(f"    {color}{direction}{_RST}  {qid:<30s} q=\"{query[:40]}\"")

        print(f"\n{'='*80}\n")

        # Cleanup
        if not args.keep_temp:
            print("Cleaning up temp collections...", flush=True)
            _cleanup_temp_collections(model_key)
            print("Done.")
        else:
            print(f"Temp collections kept: bench_{model_key}_memory, bench_{model_key}_gists")

    else:
        # Normal single-model benchmark
        mode = "with reformulation" if args.reformulate else "raw queries"
        print(f"Running {len(queries)} benchmark queries ({mode})...", flush=True)
        start = time.perf_counter()

        results = []
        for q in queries:
            chunks = run_query(q["query"], reformulate=args.reformulate)
            result = evaluate_query(q, chunks, threshold)
            results.append(result)

        elapsed = time.perf_counter() - start

        cfg.DEBUG_LOGGING = original_debug

        if args.json:
            for r in results:
                r["distances"] = [(s, round(d, 4), m) for s, d, m in r["distances"]]
            print(json.dumps(results, indent=2))
        else:
            print_results_table(results, verbose=args.verbose)
            print(f"  Completed in {elapsed:.2f}s ({elapsed/len(queries)*1000:.0f}ms per query)")


if __name__ == "__main__":
    main()

