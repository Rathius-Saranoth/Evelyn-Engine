#!/usr/bin/env python3
# migrate_legacy_type_property.py
# date created: 2026-09-23 18:30:00
# date modified: 2026-09-23 18:05:38
# tags: #migration, #taxonomy, #vault, #frontmatter

"""Retire the legacy `type:` frontmatter property in favour of the `type/` facet tag.

Three unrelated things shared the name `type`. The facet tag (`type/reference`, `type/moc`, ...)
is the form axis the taxonomy defines and the class profiles gate on. The legacy `type:`
property is an un-standardised ingestion field the PDF importers wrote, carrying three values
and read by exactly one consumer. This removes the property and ensures the equivalent facet
tag is present.

    reference-chapter -> type/reference     (a chapter of an imported work)
    literature/card   -> type/moc           (all 47 are `*_index.md` landing notes)
    document/card     -> type/media/text    (already carried by all 30; property just dropped)

Usage:
    python scripts/migrate_legacy_type_property.py            # report only
    python scripts/migrate_legacy_type_property.py --execute  # rewrite
"""

import argparse
import os
import re
import shutil
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evelyn_config as cfg
from Evelyn.tools.frontmatter_utils import parse_frontmatter

FACET_FOR = {
    "reference-chapter": "type/reference",
    "literature/card": "type/moc",
    "document/card": "type/media/text",
}
# Consumes the trailing newline so removing the line does not leave a blank one mid-block —
# but the newline must be optional, because when `type:` is the final frontmatter line the
# partition below has already taken it as part of the closing delimiter. Requiring it silently
# skipped 2,790 of 2,880 notes: the tag was added and the property left in place.
TYPE_LINE = re.compile(r"^type:[ \t]*(.+?)[ \t]*(?:\n|\Z)", re.MULTILINE)
TAGS_LINE = re.compile(r"^tags:[ \t]*(.*)$", re.MULTILINE)


def rewrite(text: str) -> tuple[str, str, str] | None:
    """Return (new_text, legacy_value, facet) or None when nothing applies."""
    meta, _ = parse_frontmatter(text)
    legacy = str(meta.get("type", "") or "").strip()
    if legacy not in FACET_FOR:
        return None
    facet = FACET_FOR[legacy]

    head, sep, body = text.partition("\n---\n")
    if not sep:
        return None

    # Drop the property.
    head = TYPE_LINE.sub("", head, count=1).rstrip("\n")

    # Ensure exactly one form-axis tag, and that it is the right one. An index note previously
    # tagged `type/media/text` is a map of content, not the media itself.
    m = TAGS_LINE.search(head)
    existing = []
    if m:
        inner = m.group(1).strip()
        if inner.startswith("[") and inner.endswith("]"):
            existing = [t.strip() for t in inner[1:-1].split(",") if t.strip()]
        elif inner:
            existing = [t.strip() for t in inner.split(",") if t.strip()]
    kept = [t for t in existing if not t.startswith("type/")]
    new_tags = [facet, *kept]
    tags_line = f"tags: [{', '.join(new_tags)}]"
    head = TAGS_LINE.sub(lambda _m: tags_line, head, count=1) if m else head.rstrip("\n") + "\n" + tags_line + "\n"

    return head + sep + body, legacy, facet


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    vault = getattr(cfg, "VAULT_BASE_DIR", os.path.expanduser("~/obsidian_vault"))
    backup = f"/home/rathius/evelyn/scratch/legacy_type_backup-{time.strftime('%Y%m%d-%H%M%S')}"
    counts, changed, failed = Counter(), [], []

    for root, _dirs, files in os.walk(vault):
        for name in files:
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as exc:
                failed.append((path, str(exc)))
                continue
            result = rewrite(text)
            if result is None:
                continue
            new_text, legacy, facet = result
            counts[f"{legacy} -> {facet}"] += 1
            changed.append((path, new_text))

    print("Legacy `type:` property migration\n")
    for k, v in sorted(counts.items()):
        print(f"  {v:5d}  {k}")
    print(f"\n  {len(changed)} notes to rewrite")
    if failed:
        print(f"  {len(failed)} unreadable:")
        for p, e in failed[:5]:
            print(f"    {p}: {e}")

    if not args.execute:
        print("\nReport only. Re-run with --execute to rewrite.")
        return 0

    os.makedirs(backup, exist_ok=True)
    written = 0
    for path, new_text in changed:
        rel = os.path.relpath(path, vault)
        dest = os.path.join(backup, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(path, dest)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        written += 1
    print(f"\nRewrote {written} notes. Originals copied to {backup}")

    leftover = 0
    for root, _dirs, files in os.walk(vault):
        for name in files:
            if name.endswith(".md"):
                with open(os.path.join(root, name), encoding="utf-8") as fh:
                    if TYPE_LINE.search(fh.read(4096)):
                        leftover += 1
    print(f"Verification: {leftover} notes still carry a `type:` property.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
