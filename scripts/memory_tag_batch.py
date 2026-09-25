#!/usr/bin/env python3
# memory_tag_batch.py
# date created: 2026-09-24
# date modified: 2026-09-24 20:44:26
# tags: #memory, #taxonomy, #backfill, #curation

"""Export a batch of untagged memory facts for hand curation, and import the decisions.

The counterpart to `backfill_memory_tags.py`, which drives the same backlog through the local
model. Reading the facts directly produces better tags and far fewer admission proposals, at the
cost of somebody's attention — the same trade the Pass 2 vault assignment made.

    --export 150 [--category Cat05-U] > batch.txt
    --import decisions.json [--propose]

`decisions.json` is `{"<entry id>": "tag, tag"}`. **Every tag must already be registered.** An
unregistered one aborts the import naming it, so a curation pass cannot quietly mint vocabulary;
`--propose` instead routes the unknown ones to the admission queue and applies the rest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/rathius/evelyn")

from Evelyn.tools import memory_db, tag_librarian, taxonomy_db


def export(limit: int, category: str | None) -> None:
    rows = memory_db.fetch_next_entries_for_tag_audit(batch_size=limit * 4 if category else limit)
    if category:
        rows = [r for r in rows if r.get("category") == category][:limit]
    for r in rows:
        obs = " ".join(str(r.get("observation") or "").split())
        print(f"{r['id']}\t{r.get('category')}\t{obs}")


def import_decisions(path: str, propose: bool) -> int:
    decisions: dict[str, str] = json.loads(Path(path).read_text(encoding="utf-8"))
    known = {m["tag"] for m in taxonomy_db.get_master_tags()} | set(taxonomy_db.get_aliases())

    unknown: dict[str, list[str]] = {}
    for entry_id, raw in decisions.items():
        terms = [tag_librarian.normalize_tag_format(t) for t in str(raw).split(",")]
        for t in terms:
            if t and t not in known:
                unknown.setdefault(t, []).append(entry_id)

    if unknown and not propose:
        print(f"[ABORT] {len(unknown)} term(s) are not registered. Register them first, or "
              f"re-run with --propose to route them to review:")
        for term, ids in sorted(unknown.items()):
            print(f"   {term:<32} wanted by {len(ids)} fact(s), e.g. #{ids[0]}")
        return 1

    applied = proposed = 0
    for entry_id, raw in decisions.items():
        terms = [t for t in (tag_librarian.normalize_tag_format(x) for x in str(raw).split(",")) if t]
        keep = [t for t in terms if t in known]
        hold = [t for t in terms if t not in known]
        if hold and propose:
            raised = tag_librarian.propose_tag_admission(
                hold,
                origin=f"memory fact #{entry_id}",
                reason="Subject named by a curated pass over the untagged memory backlog.",
                source_ids=[int(entry_id)],
            )
            proposed += len(raised)
        memory_db.mark_entry_tag_audited(
            int(entry_id), tags=", ".join(dict.fromkeys(keep)) if keep else None
        )
        applied += 1

    untagged, total = memory_db.count_entries_awaiting_tag_audit()
    print(f"Applied {applied} fact(s); {proposed} term(s) proposed.")
    print(f"Remaining: {total} awaiting audit, {untagged} untagged.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export", type=int, metavar="N", help="Print N untagged facts.")
    ap.add_argument("--category", help="Restrict the export to one Cat##-X code.")
    ap.add_argument("--import", dest="import_path", metavar="FILE", help="Apply a decisions file.")
    ap.add_argument("--propose", action="store_true", help="Route unregistered terms to review.")
    ap.add_argument("--vocabulary", action="store_true", help="Print the registered vocabulary.")
    args = ap.parse_args()

    if args.vocabulary:
        by_cat: dict[str, list[str]] = {}
        for m in taxonomy_db.get_master_tags():
            by_cat.setdefault(m.get("category") or "(uncategorised)", []).append(m["tag"])
        for cat in sorted(by_cat):
            print(f"\n## {cat} ({len(by_cat[cat])})")
            print("  " + ", ".join(sorted(by_cat[cat])))
        return 0
    if args.export:
        export(args.export, args.category)
        return 0
    if args.import_path:
        return import_decisions(args.import_path, args.propose)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
