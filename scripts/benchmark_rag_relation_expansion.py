#!/usr/bin/env python3
# benchmark_rag_relation_expansion.py
# date created: 2026-09-30
# tags: [rag, benchmark, taxonomy, relations, expansion, evaluation]

"""Benchmark script evaluating RAG Relation Expansion (_apply_relation_boost).

Measures:
1. Retrieval re-ranking behavior with RAG_RELATION_EXPANSION_ENABLED = False vs True.
2. Latency impact of expansion queries against master_tag_related.
3. Promotion accuracy: does relation expansion bring relevant associative notes into Top-K?
"""

import os
import sys
import time
from typing import Any

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for _d in (ROOT_DIR, TOOLS_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import evelyn_config as cfg
from Evelyn.tools import chroma_rag, taxonomy_db


def benchmark_synthetic_pools() -> dict[str, Any]:
    """Test re-ranking across synthetic candidate pools with known relation topologies."""
    test_cases = [
        {
            "name": "TTRPG → Campaign Promotion",
            "seed_tags": "ttrpg",
            "chunks": [
                {"source": "Session_Notes.md", "distance": 0.20, "metadata": {"tags": "ttrpg"}},
                {"source": "General_Writing.md", "distance": 0.24, "metadata": {"tags": "writing"}},
                {"source": "Random_Notes.md", "distance": 0.26, "metadata": {"tags": "misc"}},
                {"source": "Campaign_World.md", "distance": 0.27, "metadata": {"tags": "campaign"}},
            ],
            "expected_boost_source": "Campaign_World.md",
        },
        {
            "name": "Exercise → Fitness Promotion",
            "seed_tags": "exercise",
            "chunks": [
                {"source": "Daily_Walk.md", "distance": 0.15, "metadata": {"tags": "exercise"}},
                {"source": "Unrelated_Task.md", "distance": 0.20, "metadata": {"tags": "chore"}},
                {"source": "Desk_Setup.md", "distance": 0.22, "metadata": {"tags": "hardware"}},
                {"source": "Fitness_Goals.md", "distance": 0.23, "metadata": {"tags": "fitness"}},
            ],
            "expected_boost_source": "Fitness_Goals.md",
        },
        {
            "name": "Sleep → Bedtime Routine Promotion",
            "seed_tags": "sleep",
            "chunks": [
                {"source": "Sleep_Log.md", "distance": 0.18, "metadata": {"tags": "sleep"}},
                {"source": "Office_Layout.md", "distance": 0.22, "metadata": {"tags": "design"}},
                {"source": "Reading_List.md", "distance": 0.24, "metadata": {"tags": "reading"}},
                {"source": "Evening_Routine.md", "distance": 0.25, "metadata": {"tags": "bedtime"}},
            ],
            "expected_boost_source": "Evening_Routine.md",
        },
    ]

    results = []
    total_time_ms = 0.0

    for tc in test_cases:
        chunks = [dict(c) for c in tc["chunks"]]
        t0 = time.perf_counter()
        boosted_chunks, stats = chroma_rag._apply_relation_boost(chunks)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        total_time_ms += elapsed_ms

        expected = tc["expected_boost_source"]
        hit = next((c for c in boosted_chunks if c["source"] == expected), None)
        orig_hit = next((c for c in tc["chunks"] if c["source"] == expected), None)

        boosted_ok = False
        if hit and orig_hit and hit["distance"] < orig_hit["distance"]:
            boosted_ok = True

        results.append({
            "test": tc["name"],
            "elapsed_ms": elapsed_ms,
            "stats": stats,
            "boosted_expected": boosted_ok,
            "orig_rank": [c["source"] for c in tc["chunks"]].index(expected),
            "new_rank": [c["source"] for c in boosted_chunks].index(expected),
            "orig_dist": orig_hit["distance"] if orig_hit else None,
            "new_dist": hit["distance"] if hit else None,
            "matched_tags": hit.get("relation_matched", []) if hit else [],
        })

    return {
        "results": results,
        "avg_latency_ms": total_time_ms / len(test_cases) if test_cases else 0.0,
    }


def main():
    print("=" * 80)
    print("🌌 RAG Relation Expansion Benchmark (_apply_relation_boost)")
    print("=" * 80)

    # 1. Inspect relation graph connectivity
    all_tags = taxonomy_db.get_master_tags()
    tag_count = len(all_tags)
    relations_count = 0
    con = taxonomy_db.get_db()
    try:
        relations_count = con.execute("SELECT count(*) FROM master_tag_related").fetchone()[0]
    finally:
        con.close()

    print(f"Taxonomy Scope: {tag_count} master tags, {relations_count} curated tag relations.")
    print(f"Current Config: RAG_RELATION_EXPANSION_ENABLED = {getattr(cfg, 'RAG_RELATION_EXPANSION_ENABLED', False)}")
    print(f"Boost Settings: seed_k={getattr(cfg, 'RAG_RELATION_SEED_K', 3)}, cap={getattr(cfg, 'RAG_RELATION_BOOST_CAP', 0.15)}, overfetch={getattr(cfg, 'RAG_RELATION_OVERFETCH', 2)}")
    print("-" * 80)

    # 2. Run synthetic pool evaluation
    data = benchmark_synthetic_pools()
    print("\nBenchmark Scenarios:")
    for r in data["results"]:
        status = "✅ PASS" if r["boosted_expected"] else "⚠️ NO-BOOST"
        print(f"\n{status} | Scenario: {r['test']}")
        print(f"       Target: {r['orig_dist']:.4f} (Rank #{r['orig_rank'] + 1}) → {r['new_dist']:.4f} (Rank #{r['new_rank'] + 1})")
        print(f"       Matched Relation Tags: {r['matched_tags']}")
        print(f"       Expansion Stats: seeds={r['stats'].get('seeds')}, boosted={r['stats'].get('boosted')}, reordered={r['stats'].get('reordered')}")
        print(f"       Pass Latency: {r['elapsed_ms']:.3f}ms")

    print("-" * 80)
    print(f"Average Relation Re-Rank Latency: {data['avg_latency_ms']:.3f}ms")
    print("=" * 80)


if __name__ == "__main__":
    main()
