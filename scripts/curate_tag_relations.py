#!/usr/bin/env python3
# curate_tag_relations.py
# date created: 2026-09-23 19:30:00
# date modified: 2026-09-25 18:23:56
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

Candidates are then split by whether both terms sit in the same curated category. That is a
prior a person made, and a better one than lift: two terms someone deliberately filed
together are likely related, and two filed apart need the argument made. Measured on the 153
clean candidates it splits 102/51, and the spurious pairs concentrate in the second bucket.

A third filter is not statistical. Facet values co-occur with subjects by construction —
documents about ttrpg do tend to be profiles — so lift ranks `ttrpg:type/profile` highly
while saying nothing about an association between subjects, which is the only thing an `RT`
relation may assert (§6.4). Both sides of a candidate must be subjects
(`tag_librarian.is_subject_term`, the same guard the subject pass uses).

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
from Evelyn.tools.tag_librarian import is_subject_term


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


def _categories() -> dict[str, str]:
    """Each registered term's curated category, for the co-membership split."""
    return {
        str(t["tag"]): str(t.get("category") or "").strip()
        for t in taxonomy_db.get_master_tags()
    }


def _print_bucket(title: str, note: str, rows: list, labels: dict[tuple[str, str], str]) -> None:
    """One ranked section, with the category that placed it here."""
    print(f"\n{title} — {len(rows)}")
    print(f"  {note}\n")
    if not rows:
        print("  (none)")
        return
    print(f"{'lift':>6} {'docs':>5} {'areas':>6}  {'candidate':<44} category")
    for lift, count, area_count, a, b in rows:
        print(f"{lift:6.1f} {count:5} {area_count:6}  {a + ':' + b:<44} {labels[(a, b)]}")


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
    facet_pairs = 0
    for (a, b), count in pair.items():
        if count < min_docs or len(areas[(a, b)]) < min_areas:
            continue
        lift = (count / n) / ((single[a] / n) * (single[b] / n))
        if lift < min_lift or (a, b) in existing:
            continue
        if not (is_subject_term(a) and is_subject_term(b)):
            # A statement about document class, not about subjects. Counted after the lift
            # filter so the number reports what was removed from the list, not every facet
            # pair in the corpus.
            facet_pairs += 1
            continue
        rows.append((lift, count, len(areas[(a, b)]), a, b))
    rows.sort(reverse=True)

    print(f"Corpus: {n} tagged documents across both substrates")
    print(f"Filters: >= {min_docs} documents, >= {min_areas} independent areas, lift >= {min_lift}\n")
    # The 603 curated category groupings are a human judgement about what belongs with
    # what. Lift rediscovers association statistically and has no such judgement, so it
    # cannot tell `cpap:sleep` from `cat:sleep` — one is a relation, the other is where the
    # cat sleeps. Splitting on co-membership puts the second kind where it gets read
    # properly instead of scrolling past in one undifferentiated list.
    categories = _categories()
    same: list = []
    cross: list = []
    labels: dict[tuple[str, str], str] = {}
    for row in rows[:limit]:
        a, b = row[3], row[4]
        ca, cb = categories.get(a, ""), categories.get(b, "")
        if ca and ca == cb:
            labels[(a, b)] = ca
            same.append(row)
        else:
            labels[(a, b)] = f"{ca or '?'} | {cb or '?'}"
            cross.append(row)

    _print_bucket(
        "SAME CATEGORY", "Filed together by a person. Likely real — review fast.", same, labels
    )
    _print_bucket(
        "CROSS CATEGORY",
        "Filed apart. The argument has to be made — this is where co-occurrence misleads.",
        cross, labels,
    )

    if facet_pairs:
        print(f"\n{facet_pairs} facet pair(s) excluded — a facet is not a subject (§3.4, §6.4).")
    print(f"\n{len(rows)} candidates ({len(same)} same-category, {len(cross)} cross). Review each one, then:")
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
