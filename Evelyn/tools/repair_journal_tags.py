"""
repair_journal_tags.py — Restore structural tags lost from journal entries.

Fixes journal files that are missing their base structural tags:
  - CY-YYYY/MM/DD  (derived from filename)

Content-specific mood tags set by the model at write time are unrecoverable,
but the structural tags that identify a file as a journal entry are deterministic.

Usage:
    python repair_journal_tags.py --dry-run    # Preview
    python repair_journal_tags.py              # Apply
"""

# repair_journal_tags.py

import argparse
import os
import re
import time

JOURNAL_DIR = r"G:\My Drive\Obsidian_Vault\Evelyn\Evelyn's Journal"
DATE_PATTERN = re.compile(r'Journal Entry (\d{4})-(\d{2})-(\d{2})\.md$')


def get_expected_base_tags(filename: str) -> list[str]:
    """Derive the two structural tags from the journal filename."""
    m = DATE_PATTERN.search(filename)
    if not m:
        return []
    yyyy, mm, dd = m.group(1), m.group(2), m.group(3)
    return [f"CY-{yyyy}/{mm}/{dd}"]


def repair_content(content: str, base_tags: list[str]) -> str | None:
    """Inject missing base tags into file content.

    Returns new content string, or None if no change needed.
    """
    if not content.startswith("---"):
        return None

    end_match = re.search(r'\n---[ \t]*\n', content[3:])
    if not end_match:
        return None

    fm_start = 0
    fm_end = 3 + end_match.end()
    fm_block = content[fm_start:fm_end]

    tag_match = re.search(r'^(tags:\s*\[([^\]]*)\])', fm_block, re.MULTILINE)
    if not tag_match:
        return None

    old_tags_line = tag_match.group(1)
    raw_tags = tag_match.group(2)
    current_tags = [t.strip() for t in raw_tags.split(",") if t.strip()]

    missing = [t for t in base_tags if t not in current_tags]
    if not missing:
        return None  # Already correct

    # Insert missing tags before the kw/ tags
    kw_tags = [t for t in current_tags if t.startswith("kw/") or t.startswith("ctx/")]
    manual_tags = [t for t in current_tags if not t.startswith("kw/") and not t.startswith("ctx/")]

    # Merge: base tags first, then any existing manual, then kw/
    merged = list(dict.fromkeys(base_tags + manual_tags))  # dedupe, base first
    new_tags_line = f"tags: [{', '.join(merged + kw_tags)}]"

    new_fm_block = fm_block.replace(old_tags_line, new_tags_line, 1)
    return content[:fm_start] + new_fm_block + content[fm_end:]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    stats = {"checked": 0, "repaired": 0, "already_ok": 0, "skipped": 0, "errors": 0}
    start = time.time()

    for root, dirs, files in os.walk(JOURNAL_DIR):
        for fname in files:
            if not fname.endswith(".md"):
                continue

            base_tags = get_expected_base_tags(fname)
            if not base_tags:
                stats["skipped"] += 1
                continue

            full_path = os.path.join(root, fname)
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                print(f"  [ERROR] Read {fname}: {e}")
                stats["errors"] += 1
                continue

            stats["checked"] += 1
            new_content = repair_content(content, base_tags)

            if new_content is None:
                stats["already_ok"] += 1
                continue

            rel = os.path.relpath(full_path, JOURNAL_DIR)
            if args.dry_run:
                # Show current tags vs what they'll become
                m = re.search(r'tags:\s*\[([^\]]*)\]', content)
                current = [t.strip() for t in m.group(1).split(",")] if m else []
                missing = [t for t in base_tags if t not in current]
                print(f"  [DRY-RUN] {rel}")
                print(f"    Missing base tags: {missing}")
            else:
                try:
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                except Exception as e:
                    print(f"  [ERROR] Write {fname}: {e}")
                    stats["errors"] += 1
                    continue

            stats["repaired"] += 1

    elapsed = time.time() - start
    print()
    print("=" * 60)
    print(f"Journal Tag Repair {'(DRY RUN) ' if args.dry_run else ''}Complete")
    print(f"  Time:         {elapsed:.1f}s")
    print(f"  Checked:      {stats['checked']}")
    print(f"  Repaired:     {stats['repaired']}")
    print(f"  Already OK:   {stats['already_ok']}")
    print(f"  Skipped:      {stats['skipped']}")
    print(f"  Errors:       {stats['errors']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
