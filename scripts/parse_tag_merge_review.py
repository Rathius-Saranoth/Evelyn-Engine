#!/usr/bin/env python3
# parse_tag_merge_review.py
# date created: 2026-09-20 00:00:00
# date modified: 2026-09-20 00:00:00
# tags: #taxonomy, #review, #parsing

"""Parse a reviewed tag-merge document into applicable decisions (taxonomy §9).

Reads the markdown the reviewer edited and emits the decisions it expresses:

- `### [merge]` block, line ticked `[x]`  -> variant retires into the canonical.
- `### [merge]` block, line left `[ ]`    -> rejected; the variant stands.
- `### [remove]` block                    -> the whole group leaves the vocabulary,
                                             canonical included.

A removal is recorded as an alias to the empty string rather than a silent delete. The
canonicalization path already drops empty targets, so every tag writer stops emitting the
term automatically — whereas deleting it outright leaves nothing to stop the next
extraction re-minting it (§6.2).

Usage:
    PYTHONPATH=. python scripts/parse_tag_merge_review.py REVIEW.md --out decisions.json
"""

import argparse
import json
import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_BLOCK = re.compile(r"^### \[(\w+)\]\s+`([^`]+)`")
_ITEM = re.compile(r"^- \[([ xX])\]\s+`([^`]+)`[^`]*->\s+`([^`]+)`")


def parse_review(path: str) -> dict:
    """Extract merge and removal decisions from a reviewed document.

    Args:
        path: Path to the edited markdown.

    Returns:
        dict: {'merges': {variant: canonical}, 'removals': [term], 'rejected': [variant]}
    """
    merges: dict[str, str] = {}
    removals: list[str] = []
    rejected: list[str] = []
    kind = canonical = None

    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            block = _BLOCK.match(line)
            if block:
                kind, canonical = block.group(1).lower(), block.group(2)
                if kind == "remove" and canonical not in removals:
                    removals.append(canonical)
                continue

            item = _ITEM.match(line)
            if not item or kind is None:
                continue
            ticked, variant, target = item.group(1).lower() == "x", item.group(2), item.group(3)

            if kind == "remove":
                # The whole group leaves, whether or not the line stayed ticked.
                if variant not in removals:
                    removals.append(variant)
            elif not ticked:
                rejected.append(variant)
            elif variant != target:
                merges[variant] = target

    # A term cannot both retire into a canonical and be removed; removal wins, since it is
    # the more deliberate instruction.
    for term in removals:
        merges.pop(term, None)

    return {"merges": merges, "removals": removals, "rejected": rejected}


def main() -> int:
    ap = argparse.ArgumentParser(description="Parse a reviewed tag-merge document")
    ap.add_argument("review", help="Path to the edited markdown")
    ap.add_argument("--out", required=True, help="Where to write the decisions JSON")
    args = ap.parse_args()

    decisions = parse_review(args.review)
    alias_map = dict(decisions["merges"])
    alias_map.update(dict.fromkeys(decisions["removals"], ""))

    # An alias must not point at something itself retired or removed.
    for variant, canonical in list(alias_map.items()):
        seen = {variant}
        while canonical and canonical in alias_map and canonical not in seen:
            seen.add(canonical)
            canonical = alias_map[canonical]
        alias_map[variant] = canonical
    alias_map = {v: c for v, c in alias_map.items() if v != c}

    out = args.out if os.path.isabs(args.out) else os.path.join(_ROOT, args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"alias_map": alias_map,
                   "removals": decisions["removals"],
                   "rejected": decisions["rejected"]}, fh, indent=2)

    print(f"[PARSE] merges   : {len(decisions['merges'])}")
    print(f"[PARSE] removals : {len(decisions['removals'])}")
    print(f"[PARSE] rejected : {len(decisions['rejected'])}")
    print(f"[PARSE] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
