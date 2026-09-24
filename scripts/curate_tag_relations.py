#!/usr/bin/env python3
# curate_tag_relations.py
# date created: 2026-09-23 19:30:00
# date modified: 2026-09-23 19:50:26
# tags: #taxonomy, #relations, #curation, #vocabulary

"""Propose and record associative (`RT`) relations between vocabulary terms.

Flattening the hierarchy deleted relational information: `health/sleep` stated that sleep
belongs with health, and `health` + `sleep` states nothing. §6.4 is where that knowledge now
lives, and it is explicit that relations are never inferred and activated automatically.

So this reports candidates and records only what is handed to it. Candidates come from
co-occurrence, with two filters learned from the data: a pair must appear on at least
`--min-docs` documents, and across at least `--min-areas` independent parts of the corpus.
Without the second filter a single ten-tag appliance manual produces forty-five "relations"
that are really one document's tag list.

Usage:
    python scripts/curate_tag_relations.py --report
    python scripts/curate_tag_relations.py --record "cat:pet" "storm:weather"
"""

import argparse
import itertools
import os
import sqlite3
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evelyn_config as cfg
from Evelyn.tools import taxonomy_db


def _corpus() -> list[tuple[str, set[str]]]:
    """Tag sets from both substrates, each labelled with the area it came from."""
    out: list[tuple[str, set[str]]] = []
    vault = sqlite3.connect(
        f"file:{getattr(cfg, 'VAULT_DB_PATH', '/home/rathius/evelyn/data/evelyn_vault.db')}?mode=ro",
        uri=True,
    )
    for path, tags in vault.execute(
        "SELECT path, tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
    ):
        s = {t.strip() for t in str(tags).split(",") if t.strip()}
        if 1 < len(s) <= 12:
            out.append((str(path).split("/")[0], s))
    vault.close()

    mem = sqlite3.connect(
        f"file:{getattr(cfg, 'MEMORY_DB_PATH', '/home/rathius/evelyn/data/evelyn_memory.db')}?mode=ro",
        uri=True,
    )
    for (tags,) in mem.execute(
        "SELECT tags FROM context_entries WHERE status='live' AND tags IS NOT NULL AND TRIM(tags) != ''"
    ):
        s = {t.strip() for t in str(tags).split(",") if t.strip()}
        if 1 < len(s) <= 12:
            out.append(("memory", s))
    mem.close()
    return out


def report(min_docs: int, min_areas: int, min_lift: float, limit: int) -> None:
    docs = _corpus()
    n = len(docs)
    single: Counter = Counter()
    pair: Counter = Counter()
    areas: dict[tuple[str, str], set[str]] = defaultdict(set)
    for area, tags in docs:
        for t in tags:
            single[t] += 1
        for a, b in itertools.combinations(sorted(tags), 2):
            pair[(a, b)] += 1
            areas[(a, b)].add(area)

    existing = set()
    for a, b in ((r["tag"], t) for t in single for r in taxonomy_db.get_related_terms(t)):
        existing.add(tuple(sorted((a, b))))

    rows = []
    for (a, b), count in pair.items():
        if count < min_docs or len(areas[(a, b)]) < min_areas:
            continue
        lift = (count / n) / ((single[a] / n) * (single[b] / n))
        if lift < min_lift or (a, b) in existing:
            continue
        rows.append((lift, count, len(areas[(a, b)]), a, b))
    rows.sort(reverse=True)

    print(f"Corpus: {n} tagged documents across both substrates")
    print(f"Filters: >= {min_docs} documents, >= {min_areas} independent areas, lift >= {min_lift}\n")
    print(f"{'lift':>6} {'docs':>5} {'areas':>6}  candidate")
    for lift, count, area_count, a, b in rows[:limit]:
        print(f"{lift:6.1f} {count:5} {area_count:6}  {a}:{b}")
    print(f"\n{len(rows)} candidates. Review each one, then:")
    print("  --record 'a:b'                 for an associative pair (neither is broader)")
    print("  --record 'a:b' --kind narrower  when a is a kind of b (order matters)")
    print("  neither, if they are interchangeable — that is an alias (§6.2), not a relation")


def record(pairs: list[str], kind: str, weight: float, note: str) -> None:
    for spec in pairs:
        if ":" not in spec:
            print(f"  [SKIP] '{spec}' is not in a:b form")
            continue
        a, b = (part.strip() for part in spec.split(":", 1))
        taxonomy_db.record_relation(a, b, kind=kind, weight=weight, tier="reviewed", note=note)
        arrow = "is a kind of" if kind == "narrower" else "~"
        print(f"  recorded {a} {arrow} {b} (weight {weight})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true", help="List candidates.")
    ap.add_argument("--record", nargs="+", metavar="A:B", help="Record reviewed relations.")
    ap.add_argument("--kind", choices=("related", "narrower"), default="related",
                    help="'narrower' means A is a kind of B, and is directional.")
    ap.add_argument("--min-docs", type=int, default=5)
    ap.add_argument("--min-areas", type=int, default=3)
    ap.add_argument("--min-lift", type=float, default=3.0)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--weight", type=float, default=0.4)
    ap.add_argument("--note", default="Curated from co-occurrence evidence.")
    args = ap.parse_args()

    if args.record:
        record(args.record, args.kind, args.weight, args.note)
        return 0
    report(args.min_docs, args.min_areas, args.min_lift, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
