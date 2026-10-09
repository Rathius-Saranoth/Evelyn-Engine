#!/usr/bin/env python3
# migrate_date_tags_to_property.py
# date created: 2026-09-22 21:40:00
# date modified: 2026-09-22 20:05:07
# tags: #vault, #tags, #taxonomy, #migration, #dates, #cli

"""migrate_date_tags_to_property.py — Move `CY-` date tags into the `occurred` property.

The time axis was stored as a tag (`CY-2026/09/22`) purely because an Obsidian tag cannot
begin with a digit. A property has no such restriction, and a date is an attribute of a note
rather than a subject the note is about. Tags also cannot answer a range query — "notes
between March and June" — which is the primary access pattern for the one facet that is a
date. Keeping it as a tag additionally forced every tag consumer to special-case it.

This rewrites each note's frontmatter: the `CY-` tag is removed from `tags` and its value is
written to `occurred` in canonical EDTF form (`2026-09-22`, `2026-05`, `XXXX-11-16`).
A note whose only date was `CY-XXXX` (undated) simply loses the tag; an absent key is a
clearer statement of "no date known" than a token meaning the same thing.

Usage:
    python scripts/migrate_date_tags_to_property.py            # dry run
    python scripts/migrate_date_tags_to_property.py --execute
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import evelyn_config as cfg
from Evelyn.tools.frontmatter_utils import (
    parse_frontmatter,
    update_frontmatter_field,
    write_file_with_frontmatter,
)
from Evelyn.tools.tag_librarian import OCCURRED_PROPERTY, canonicalize_occurred

UNDATED = "XXXX"


def _tags_of(meta: dict) -> list[str]:
    raw = meta.get("tags") or []
    if isinstance(raw, str):
        return [t.strip().strip("'\"#") for t in raw.split(",") if t.strip().strip("'\"#")]
    if isinstance(raw, (list, tuple, set)):
        return [str(t).strip().strip("'\"#") for t in raw if str(t).strip().strip("'\"#")]
    return []


def plan_note(path: str) -> dict | None:
    """Return the change this note needs, or None when it needs none."""
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return None

    meta, _body = parse_frontmatter(content)
    if not meta:
        return None

    tags = _tags_of(meta)
    date_tags = [t for t in tags if canonicalize_occurred(t) and t.upper().startswith("CY")]
    if not date_tags:
        return None

    # Most specific wins when a note carries several (a dated tag beats a bare year).
    values = sorted(
        (v for v in (canonicalize_occurred(t) for t in date_tags) if v),
        key=lambda v: (v.count("-"), UNDATED not in v),
        reverse=True,
    )
    occurred = next((v for v in values if v != UNDATED), "")

    return {
        "path": path,
        "content": content,
        "remove": date_tags,
        "kept_tags": [t for t in tags if t not in set(date_tags)],
        "occurred": occurred,
        "existing": str(meta.get(OCCURRED_PROPERTY, "") or "").strip(),
    }


def apply_note(change: dict) -> bool:
    """Rewrite one note's frontmatter."""
    new = update_frontmatter_field(change["content"], "tags", change["kept_tags"])
    if change["occurred"]:
        new = update_frontmatter_field(new, OCCURRED_PROPERTY, change["occurred"])
    if new == change["content"]:
        return False
    write_file_with_frontmatter(change["path"], new)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Move CY- date tags into the occurred property.")
    parser.add_argument("--execute", action="store_true", help="Apply changes (default: dry run)")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N notes (for a trial run)")
    args = parser.parse_args()

    changes = []
    for root, _dirs, files in os.walk(cfg.VAULT_BASE_DIR):
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            change = plan_note(os.path.join(root, name))
            if change:
                changes.append(change)
        if args.limit and len(changes) >= args.limit:
            break

    if args.limit:
        changes = changes[: args.limit]

    dated = [c for c in changes if c["occurred"]]
    undated = [c for c in changes if not c["occurred"]]
    conflicts = [c for c in changes if c["existing"] and c["existing"] != c["occurred"]]

    print(f"notes carrying a CY- tag : {len(changes)}")
    print(f"  -> occurred: <date>    : {len(dated)}")
    print(f"  -> tag dropped, undated: {len(undated)}")
    print(f"  conflicting existing   : {len(conflicts)}")
    for c in conflicts[:10]:
        print(f"    {c['path'].replace(cfg.VAULT_BASE_DIR, '')}: has {c['existing']!r}, would set {c['occurred']!r}")

    print("\nsample:")
    for c in changes[:5]:
        rel = c["path"].replace(cfg.VAULT_BASE_DIR, "")
        print(f"  {rel[:70]:<70} {c['remove']} -> occurred: {c['occurred'] or '(none)'}")

    if not args.execute:
        print("\nDry run. Re-run with --execute to apply.")
        return 0

    if conflicts:
        print("\n[ABORT] Some notes already carry a different `occurred` value. Resolve these first.")
        return 1

    written = sum(1 for c in changes if apply_note(c))
    print(f"\nRewrote {written} note(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
