# benchmark_rag.py
# date created: 2026-04-26 12:18:17
# date modified: 2026-10-02 17:45:18
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
  exact_name     — Direct name lookups (Alex, Jordan, Evelyn)
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

import argparse
import json
import os
import sys
import time
from contextlib import suppress

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
ROOT_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for _d in (ROOT_DIR, TOOLS_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import shutil
import tempfile

import chroma_rag
import chromadb

import evelyn_config as cfg

GOLDEN_FILE = os.path.join(ROOT_DIR, "reference", "rag_benchmark_queries.json")

# ANSI colors
_RST = "\033[0m"
_BLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[91m"
_GRN = "\033[92m"
_YEL = "\033[93m"
_CYN = "\033[96m"


class StaleGoldenSet(RuntimeError):
    """Raised when the golden set has been marked as no longer measuring anything."""


def load_golden_queries(path: str) -> list[dict]:
    """Load and validate the golden query test set.

    Refuses a set marked ``"status": "stale"``. Supports dynamic {USER_NAME} and
    {ASSISTANT_NAME} templating and optional local overlay file.

    Args:
        path: Path to the golden query JSON.

    Returns:
        list[dict]: Validated and templated query definitions.

    Raises:
        StaleGoldenSet: If the file declares itself stale.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        if data.get("status") == "stale":
            lines = [
                "",
                f"{_RED}{_BLD}This golden set is marked STALE and will not be scored.{_RST}",
                f"  {path}",
                "",
                f"  {data.get('summary', '')}",
                "",
                f"{_BLD}  Why it cannot simply be repaired:{_RST}",
            ]
            for i, d in enumerate(data.get("defects", []), 1):
                lines.append(f"    {i}. {d}")
            if data.get("rebuild_guidance"):
                lines += ["", f"{_BLD}  When rebuilding:{_RST}", f"    {data['rebuild_guidance']}"]
            lines += ["", f"  Marked stale {data.get('stale_since', '?')}.", ""]
            raise StaleGoldenSet("\n".join(lines))
        queries = data.get("queries", [])
    else:
        queries = data

    # Optional local overlay support
    local_path = os.path.join(ROOT_DIR, "reference", "rag_benchmark_queries.local.json")
    if os.path.isfile(local_path):
        try:
            with open(local_path, encoding="utf-8") as lf:
                local_data = json.load(lf)
            local_queries = local_data.get("queries", local_data) if isinstance(local_data, dict) else local_data
            by_id = {q["id"]: q for q in queries}
            for lq in local_queries:
                by_id[lq["id"]] = lq
            queries = list(by_id.values())
        except Exception as exc:  # noqa: BLE001
            print(f"Warning: Failed loading local overlay {local_path}: {exc}")

    # Parameterize template tokens {USER_NAME} and {ASSISTANT_NAME}
    fmt = {"USER_NAME": cfg.USER_NAME, "ASSISTANT_NAME": cfg.ASSISTANT_NAME}
    templated_queries = []
    for q in queries:
        assert "id" in q, f"Query missing 'id': {q}"
        assert "query" in q, f"Query missing 'query': {q}"
        qd = dict(q)
        with suppress(KeyError, IndexError):
            qd["query"] = qd["query"].format(**fmt)
        if "expected_ground_truth" in qd:
            templated_gt = []
            for item in qd["expected_ground_truth"]:
                formatted_item = item
                with suppress(KeyError, IndexError):
                    formatted_item = item.format(**fmt)
                templated_gt.append(formatted_item)
            qd["expected_ground_truth"] = templated_gt
        if "expected_sources_contain" in qd:
            templated_src = []
            for item in qd["expected_sources_contain"]:
                formatted_item = item
                with suppress(KeyError, IndexError):
                    formatted_item = item.format(**fmt)
                templated_src.append(formatted_item)
            qd["expected_sources_contain"] = templated_src
        templated_queries.append(qd)

    return templated_queries


def run_query(query: str, n_results: int | None = None, reformulate: bool = False) -> list[dict]:
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


def is_chunk_relevant(chunk: dict, query_def: dict) -> tuple[bool, str]:
    """Determine if a retrieved chunk satisfies any relevance criteria:

    1. Source file name or path matches `expected_sources_contain`
    2. Category matches `expected_categories`
    3. Ground truth factual keywords appear in chunk content (`expected_ground_truth`)
    4. Tags match `expected_tags`

    Returns:
        tuple[bool, str]: (is_relevant, match_reason)
    """
    src = str(chunk.get("source", ""))
    src_base = os.path.basename(src).lower()
    meta = chunk.get("metadata") or {}
    meta_src = str(meta.get("source", "")).lower()
    meta_title = str(meta.get("title", "")).lower()
    content_lower = str(chunk.get("content", "")).lower()
    cat = str(meta.get("category", "")).upper()
    tags = str(meta.get("tags", "")).lower()

    # 1. Source match
    expected_sources = query_def.get("expected_sources_contain") or []
    for pat in expected_sources:
        p_low = pat.lower()
        if p_low in src.lower() or p_low in src_base or p_low in meta_src or p_low in meta_title:
            return True, f"source:{pat}"

    # 2. Category match
    expected_categories = [c.upper() for c in (query_def.get("expected_categories") or [])]
    if cat and cat in expected_categories:
        return True, f"category:{cat}"
    for c in expected_categories:
        if f"category: {c.lower()}" in content_lower:
            return True, f"category_text:{c}"

    # 3. Ground truth keyword / phrase match in content
    expected_gt = query_def.get("expected_ground_truth") or []
    for gt in expected_gt:
        if gt.lower() in content_lower:
            return True, f"ground_truth:{gt}"

    # 4. Tag match
    expected_tags = query_def.get("expected_tags") or []
    for t in expected_tags:
        t_low = t.lower()
        if t_low in tags or f"tags: {t_low}" in content_lower:
            return True, f"tag:{t}"

    return False, ""


def evaluate_query(query_def: dict, all_chunks: list[dict], threshold: float) -> dict:
    """Evaluate a single query against multi-criteria relevance expectations.

    Returns a result dict with:
      - hit: bool — did any expected source/fact appear in kept chunks?
      - reciprocal_rank: float — 1/rank of first expected hit (0 if no hit)
      - precision_at_k: float — relevant chunks / kept chunks
      - kept_count: int — chunks that passed threshold
      - total_count: int — chunks returned from query
      - first_match_rank: int or None
      - distances: list of (source, distance, matched, reason) tuples
    """
    is_negative = query_def.get("category") == "negative"

    kept = [c for c in all_chunks if c["distance"] <= threshold]
    distances = []
    first_match_rank = None
    match_count = 0

    for rank, chunk in enumerate(kept, 1):
        matched, reason = is_chunk_relevant(chunk, query_def)
        src_label = chunk.get("source", "")
        if "sqlite::context_entry" in src_label:
            cat = (chunk.get("metadata") or {}).get("category", "")
            src_label = f"{src_label} [{cat}]" if cat else src_label
        else:
            src_label = os.path.basename(src_label)

        distances.append((src_label, chunk["distance"], matched, reason))
        if matched:
            match_count += 1
            if first_match_rank is None:
                first_match_rank = rank

    if is_negative:
        # For negative tests: success if no chunks matched ground truth (match_count == 0)
        # AND top chunk distance indicates low relevance (either min_dist >= 0.40 or len(kept) <= 2)
        min_dist = min((c["distance"] for c in all_chunks), default=1.0)
        hit = (match_count == 0) and (min_dist >= 0.40 or len(kept) <= 2)
        reciprocal_rank = 1.0 if hit else 0.0
        precision_at_k = 1.0 if hit else 0.0
    else:
        hit = first_match_rank is not None
        reciprocal_rank = (1.0 / first_match_rank) if first_match_rank else 0.0
        precision_at_k = (match_count / len(kept)) if kept else 0.0

    return {
        "id": query_def["id"],
        "query": query_def["query"],
        "category": query_def.get("category", "unknown"),
        "hit": hit,
        "reciprocal_rank": reciprocal_rank,
        "precision_at_k": precision_at_k,
        "first_match_rank": first_match_rank,
        "match_count": match_count,
        "kept_count": len(kept),
        "total_count": len(all_chunks),
        "distances": distances,
    }


def print_results_table(results: list[dict], verbose: bool = False):
    """Print a formatted results table to stdout."""
    print(f"\n{_BLD}{'='*80}{_RST}")
    print(f"{_BLD}  RAG Retrieval Benchmark Results (Ragas/BEIR Evaluation){_RST}")
    print(f"{_BLD}{'='*80}{_RST}")
    print(f"  Embedding: {BASELINE_MODEL} | Threshold: {cfg.RAG_DISTANCE_THRESHOLD}")
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
        cat_prec = sum(r["precision_at_k"] for r in cat_results) / len(cat_results) if cat_results else 0
        color = _GRN if cat_hits == len(cat_results) else _YEL if cat_hits > 0 else _RED
        print(f"\n  {_BLD}[{cat.upper()}]{_RST}  Recall: {color}{cat_hits}/{len(cat_results)}{_RST}  MRR: {cat_mrr:.3f}  Prec@K: {cat_prec:.3f}")

        for r in cat_results:
            status = f"{_GRN}+{_RST}" if r["hit"] else f"{_RED}x{_RST}"
            rank_str = f"rank={r['first_match_rank']}" if r["first_match_rank"] else "no match"
            print(
                f"    {status} {r['id']:<34s} kept={r['kept_count']}/{r['total_count']} "
                f"{rank_str:<12s} MRR={r['reciprocal_rank']:.3f}  q=\"{r['query'][:40]}\""
            )
            if verbose and r["distances"]:
                for src, dist, matched, reason in r["distances"][:5]:
                    marker = f"{_GRN}<{_RST}" if matched else " "
                    reason_str = f" [{reason}]" if reason else ""
                    print(f"      {marker} dist={dist:.3f}  {src}{reason_str}")

    # Summary
    total = len(results)
    hits = sum(1 for r in results if r["hit"])
    overall_mrr = sum(r["reciprocal_rank"] for r in results) / total if total else 0
    overall_prec = sum(r["precision_at_k"] for r in results) / total if total else 0
    non_negative = [r for r in results if r["category"] != "negative"]
    non_neg_count = len(non_negative)
    non_neg_hits = sum(1 for r in non_negative if r["hit"])
    non_neg_mrr = sum(r["reciprocal_rank"] for r in non_negative) / non_neg_count if non_neg_count else 0.0
    non_neg_prec = sum(r["precision_at_k"] for r in non_negative) / non_neg_count if non_neg_count else 0.0

    print("\n" + "-" * 80)
    print(f"  {_BLD}OVERALL EVALUATION{_RST}")
    color = _GRN if hits == total else _YEL if hits > total * 0.7 else _RED
    print(f"    Recall@K:     {color}{hits}/{total} ({100*hits/total:.0f}%){_RST}")
    print(f"    MRR:          {overall_mrr:.3f}")
    print(f"    Precision@K:  {overall_prec:.3f}")
    if non_neg_count:
        print(f"    (excl. neg)   Recall@K: {non_neg_hits}/{non_neg_count} ({100*non_neg_hits/non_neg_count:.0f}%)  MRR: {non_neg_mrr:.3f}  Prec@K: {non_neg_prec:.3f}")


# ---------------------------------------------------------------------------
# Model comparison: ingest into isolated store with candidate embedding fn
# ---------------------------------------------------------------------------

BASELINE_MODEL = "BAAI/bge-large-en-v1.5"

CANDIDATE_MODELS = {
    "MINILM-L6": "all-MiniLM-L6-v2",
    "MINILM-L12": "all-MiniLM-L12-v2",
    "BGE-BASE": "BAAI/bge-base-en-v1.5",
    "BGE-SMALL": "BAAI/bge-small-en-v1.5",
    "NOMIC": "nomic-ai/nomic-embed-text-v1.5",
}


def _get_candidate_embedding_fn(model_name: str):
    """Create a SentenceTransformer embedding function for a candidate model."""
    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    return SentenceTransformerEmbeddingFunction(model_name=model_name)


def setup_benchmark_store(bench_dir: str | None = None) -> tuple[chromadb.PersistentClient, str, bool]:
    """Create an isolated ChromaDB client for candidate benchmark collections.

    Never touches the production store or leases. Writes go to an isolated directory.

    Args:
        bench_dir: Optional custom path. If None, creates an ephemeral TemporaryDirectory.

    Returns:
        tuple[chromadb.PersistentClient, str, bool]: (client, storage_path, is_temporary)
    """
    if bench_dir:
        os.makedirs(bench_dir, exist_ok=True)
        return chromadb.PersistentClient(path=bench_dir), bench_dir, False
    temp_path = tempfile.mkdtemp(prefix="chroma_bench_")
    return chromadb.PersistentClient(path=temp_path), temp_path, True


def _ingest_into_candidate_collection(model_key: str, bench_client: chromadb.PersistentClient) -> chromadb.Collection:
    """Copy documents from production memory collection into the isolated candidate store.

    Production store is accessed strictly read-only. Candidate writes are completely
    isolated from data/chroma_db.

    Args:
        model_key: Key in CANDIDATE_MODELS.
        bench_client: Isolated benchmark ChromaDB client.

    Returns:
        chromadb.Collection: Candidate collection handle.
    """
    model_name = CANDIDATE_MODELS[model_key]
    embed_fn = _get_candidate_embedding_fn(model_name)
    mem_temp = f"bench_{model_key.lower().replace('-', '_')}_memory"

    # Create collection in isolated benchmark store
    dst_col = bench_client.get_or_create_collection(
        name=mem_temp,
        embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"},
    )

    # Read from production store read-only
    prod_client = chromadb.PersistentClient(path=cfg.CHROMA_DB_PATH)
    try:
        src_col = prod_client.get_collection(cfg.CHROMA_MEMORY_COLLECTION)
    except Exception as exc:  # noqa: BLE001
        print(f"  {_RED}Warning: Could not open production collection '{cfg.CHROMA_MEMORY_COLLECTION}' read-only: {exc}{_RST}")
        return dst_col

    count = src_col.count()
    if count == 0:
        return dst_col

    print(f"  Ingesting {count} chunks from production '{cfg.CHROMA_MEMORY_COLLECTION}' -> candidate '{mem_temp}'...", flush=True)
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

    return dst_col


def run_query_with_collection(query: str, col: chromadb.Collection,
                               n_results: int | None = None) -> list[dict]:
    """Run query directly against a candidate collection, applying priority boost."""
    if n_results is None:
        n_results = cfg.RAG_TOP_K
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
        meta = meta or {}
        chunks.append({
            "content":  doc,
            "source":   meta.get("source", ""),
            "distance": dist,
            "metadata": meta,
        })
    return chroma_rag._apply_priority_boost(chunks)


def main():
    parser = argparse.ArgumentParser(description="RAG retrieval benchmark")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-chunk distances")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--case", help="Filter by specific query ID")
    parser.add_argument("--golden", default=GOLDEN_FILE, help="Path to golden query file")
    parser.add_argument(
        "--compare", metavar="MODEL",
        help="Compare against a candidate model. Options: " + ", ".join(CANDIDATE_MODELS.keys())
             + ". Ingests into isolated temp store, benchmarks, cleans up."
    )
    parser.add_argument("--keep-temp", action="store_true",
                        help="Preserve candidate collections in data/chroma_bench/ instead of auto-deleting")
    parser.add_argument("--reformulate", "-r", action="store_true",
                        help="Pass queries through the LLM reformulator before embedding")
    args = parser.parse_args()

    # Suppress debug logging during benchmark
    original_debug = cfg.DEBUG_LOGGING
    cfg.DEBUG_LOGGING = False

    try:
        queries = load_golden_queries(args.golden)
    except StaleGoldenSet as e:
        print(e)
        cfg.DEBUG_LOGGING = original_debug
        return 2

    if args.case:
        queries = [q for q in queries if q.get("id") == args.case]
        if not queries:
            print(f"No query found with ID: {args.case}")
            cfg.DEBUG_LOGGING = original_debug
            return 1
    threshold = cfg.RAG_DISTANCE_THRESHOLD

    if args.compare:
        model_key = args.compare.upper()
        if model_key not in CANDIDATE_MODELS:
            print(f"Unknown model: {args.compare}. Options: {', '.join(CANDIDATE_MODELS.keys())}")
            return

        model_name = CANDIDATE_MODELS[model_key]
        print(f"{'='*80}")
        print(f"  Model Comparison: baseline ({BASELINE_MODEL}) vs candidate ({model_name})")
        print(f"{'='*80}")

        # Run baseline first (read-only against production store)
        print(f"\n--- Baseline ({BASELINE_MODEL}) ---")
        print(f"Running {len(queries)} queries...", flush=True)
        start = time.perf_counter()
        baseline_results = []
        for q in queries:
            chunks = run_query(q["query"])
            result = evaluate_query(q, chunks, threshold)
            baseline_results.append(result)
        base_elapsed = time.perf_counter() - start

        # Set up isolated benchmark store
        bench_dir = os.path.join(cfg.BASE_DIR, "data", "chroma_bench") if args.keep_temp else None
        bench_client, bench_path, is_temp = setup_benchmark_store(bench_dir)

        # Ingest with candidate model into isolated store
        print(f"\n--- Candidate ({model_key}: {model_name}) ---")
        print(f"Ingesting into isolated benchmark store at {bench_path}...", flush=True)
        ingest_start = time.perf_counter()
        cand_col = _ingest_into_candidate_collection(model_key, bench_client)
        ingest_elapsed = time.perf_counter() - ingest_start
        print(f"Ingest completed in {ingest_elapsed:.1f}s", flush=True)

        # Run candidate benchmark
        print(f"Running {len(queries)} queries against candidate...", flush=True)
        start = time.perf_counter()
        candidate_results = []
        for q in queries:
            chunks = run_query_with_collection(q["query"], cand_col)
            result = evaluate_query(q, chunks, threshold)
            candidate_results.append(result)
        cand_elapsed = time.perf_counter() - start

        cfg.DEBUG_LOGGING = original_debug

        # Print comparison
        print(f"\n{'='*80}")
        print("  COMPARISON RESULTS")
        print(f"{'='*80}")

        base_hits = sum(1 for r in baseline_results if r["hit"])
        cand_hits = sum(1 for r in candidate_results if r["hit"])
        base_mrr = sum(r["reciprocal_rank"] for r in baseline_results) / len(baseline_results)
        cand_mrr = sum(r["reciprocal_rank"] for r in candidate_results) / len(candidate_results)

        print(f"\n  {'Metric':<25s} {f'Baseline ({BASELINE_MODEL})':>30s} {f'Candidate ({model_key})':>20s} {'Delta':>10s}")
        print(f"  {'-'*88}")
        print(f"  {'Hit Rate':<25s} {f'{base_hits}/{len(queries)}':>30s} {f'{cand_hits}/{len(queries)}':>20s} {f'{cand_hits-base_hits:+d}':>10s}")
        print(f"  {'MRR':<25s} {base_mrr:>30.3f} {cand_mrr:>20.3f} {cand_mrr-base_mrr:>+10.3f}")
        print(f"  {'Query time':<25s} {f'{base_elapsed:.2f}s':>30s} {f'{cand_elapsed:.2f}s':>20s}")

        # Per-query comparison for mismatches
        changes = []
        for b, c in zip(baseline_results, candidate_results, strict=False):
            if b["hit"] != c["hit"]:
                direction = "GAINED" if c["hit"] else "LOST"
                changes.append((direction, b["id"], b["query"]))

        if changes:
            print("\n  Changes:")
            for direction, qid, query in changes:
                color = _GRN if direction == "GAINED" else _RED
                print(f"    {color}{direction}{_RST}  {qid:<30s} q=\"{query[:40]}\"")

        print(f"\n{'='*80}\n")

        # Cleanup
        if is_temp:
            print("Cleaning up temporary benchmark store...", flush=True)
            shutil.rmtree(bench_path, ignore_errors=True)
            print("Done.")
        else:
            print(f"Candidate store preserved at: {bench_path}")

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
    sys.exit(main() or 0)

