#!/usr/bin/env python3
# curate_tag_relations.py
# date created: 2026-09-23 19:30:00
# date modified: 2026-09-25 19:20:57
# tags: #taxonomy, #relations, #curation, #vocabulary

"""Report and record associative (`RT`) relations between vocabulary terms.

A thin CLI over `Evelyn.tools.tag_relations`, which holds the measurement and documents it.
This file owns the printing and the `--record` path and nothing else — the computation used to
live here, which meant the engine could not reach it and no candidate ever entered the review
queue.

Candidates print in two sections, by whether both terms sit in the same curated category. That
is a prior a person made and a better one than lift, but it is **a prior and not a verdict**:
reviewed in full, 42 of 51 cross-category pairs were real.

Usage:
    python scripts/curate_tag_relations.py --report
    python scripts/curate_tag_relations.py --record "cat:pet" "storm:weather"
    python scripts/curate_tag_relations.py --record "lucid-dreaming:dream" --kind narrower
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Evelyn.tools import tag_relations, taxonomy_db


def _print_bucket(title: str, note: str, rows: list) -> None:
    """One ranked section, with the category that placed it here."""
    print(f"\n{title} — {len(rows)}")
    print(f"  {note}\n")
    if not rows:
        print("  (none)")
        return
    print(f"{'lift':>6} {'docs':>5} {'areas':>6}  {'candidate':<44} category")
    for c in rows:
        label = c.category_a if c.same_category else f"{c.category_a or '?'} | {c.category_b or '?'}"
        print(f"{c.lift:6.1f} {c.documents:5} {c.areas:6}  {c.pair:<44} {label}")


def report(min_docs: int, min_areas: int, min_lift: float, limit: int) -> None:
    candidates, stats = tag_relations.find_relation_candidates(min_docs, min_areas, min_lift)
    shown = candidates[:limit]

    print(f"Corpus: {stats['corpus']} tagged documents across both substrates")
    print(f"Filters: >= {min_docs} documents, >= {min_areas} independent areas, lift >= {min_lift}")

    # The 603 curated category groupings are a human judgement about what belongs with what.
    # Lift rediscovers association statistically and has none, so it ranks `cpap:sleep` and
    # `hydration:rest` alike — one a relation, the other two things that land in the same daily
    # note. Splitting on co-membership puts the second kind where it gets read properly instead
    # of scrolling past in one undifferentiated list.
    same = [c for c in shown if c.same_category]
    cross = [c for c in shown if not c.same_category]

    _print_bucket("SAME CATEGORY", "Filed together by a person. Likely real — review fast.", same)
    _print_bucket(
        "CROSS CATEGORY",
        "Filed apart. The argument has to be made — this is where co-occurrence misleads.",
        cross,
    )

    if stats["facet_pairs"]:
        print(f"\n{stats['facet_pairs']} facet pair(s) excluded — "
              "a facet is not a subject (§3.4, §6.4).")
    print(f"\n{len(candidates)} candidates ({len(same)} same-category, {len(cross)} cross). "
          "Review each one — the questions are in §6.4.1 — then:")
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
    ap.add_argument("--min-docs", type=int, default=tag_relations.MIN_DOCS)
    ap.add_argument("--min-areas", type=int, default=tag_relations.MIN_AREAS)
    ap.add_argument("--min-lift", type=float, default=tag_relations.MIN_LIFT)
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
