# sweep_legacy_date_lines.py
# date created: 2026-09-22 22:20:00
# date modified: 2026-09-22 20:15:53
# tags: #vault, #cleanup, #dates, #journal, #cli

"""sweep_legacy_date_lines.py — Remove retired `CY-` metadata lines from note bodies.

Older journal and dream entries carry a footer line that is nothing but retired metadata —
`CY-2025/11/23 Journal/Evelyn`, `**Tags:** CY-2026/01/10` — left over from when the time
axis was a tag. The frontmatter migration (v000.006.203) moved the real date into the
`occurred` property; these lines are inert prose that neither the indexer nor the watcher
reads, but they still teach a convention that no longer exists.

A line is removed only when the note's date is already safely recorded: either `occurred`
matches the line's date, or the note has no `occurred` and one is set from the line first.
A line carrying anything beyond the retired metadata is left alone — prose is not swept.

Usage:
    python scripts/sweep_legacy_date_lines.py            # dry run
    python scripts/sweep_legacy_date_lines.py --execute
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import evelyn_config as cfg
from Evelyn.tools.frontmatter_utils import (
    parse_frontmatter,
    update_frontmatter_field,
    write_file_with_frontmatter,
)
from Evelyn.tools.tag_librarian import OCCURRED_PROPERTY, canonicalize_occurred

# A whole line that is only a retired date token plus optional retired tag/wikilink noise.
LEGACY_LINE = re.compile(
    r"^\s*(?:\*\*Tags:\*\*\s*)?#?(CY-[0-9X]{4}[-/][0-9X]{2}[-/][0-9X]{2}|CY-[0-9X]{4})"
    r"(?:\s+(?:#?Journal/[\w'-]+|\[\[[^\]]+\]\]))*\s*$",
    re.IGNORECASE,
)


def plan_note(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError):
        return None
    if "CY-" not in content:
        return None

    meta, _body = parse_frontmatter(content)
    split = content.find("\n---", 3) + 4 if content.startswith("---") else 0
    head, body = content[:split], content[split:]

    kept, removed, found_dates = [], [], []
    for line in body.splitlines(keepends=True):
        m = LEGACY_LINE.match(line.rstrip("\n"))
        if m:
            removed.append(line.rstrip("\n"))
            value = canonicalize_occurred(m.group(1))
            if value:
                found_dates.append(value)
        else:
            kept.append(line)

    if not removed:
        return None

    existing = str(meta.get(OCCURRED_PROPERTY, "") or "").strip()
    return {
        "path": path, "head": head, "new_body": "".join(kept),
        "removed": removed, "existing": existing,
        "set_occurred": "" if existing else (found_dates[0] if found_dates else ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove retired CY- metadata lines from note bodies.")
    parser.add_argument("--execute", action="store_true", help="Apply changes (default: dry run)")
    args = parser.parse_args()

    changes = []
    for root, _dirs, files in os.walk(cfg.VAULT_BASE_DIR):
        for name in sorted(files):
            if name.endswith(".md"):
                c = plan_note(os.path.join(root, name))
                if c:
                    changes.append(c)

    lines = sum(len(c["removed"]) for c in changes)
    backfill = [c for c in changes if c["set_occurred"]]
    print(f"notes with a retired date line : {len(changes)}")
    print(f"  lines to remove              : {lines}")
    print(f"  already have `occurred`      : {len(changes) - len(backfill)}")
    print(f"  `occurred` set from the line : {len(backfill)}")
    print("\nsample:")
    for c in changes[:5]:
        print(f"  {c['path'].replace(cfg.VAULT_BASE_DIR, '')[:64]:<64} {c['removed'][0][:34]!r}")

    if not args.execute:
        print("\nDry run. Re-run with --execute to apply.")
        return 0

    written = 0
    for c in changes:
        new = c["head"] + c["new_body"]
        if c["set_occurred"]:
            new = update_frontmatter_field(new, OCCURRED_PROPERTY, c["set_occurred"])
        write_file_with_frontmatter(c["path"], new)
        written += 1
    print(f"\nRewrote {written} note(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
