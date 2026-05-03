"""
repair_lost_tags.py — Restore manual tags lost during the keyword-tag backfill.

The vault_map_data.json was scanned BEFORE the backfill ran, so its
data.tags field reflects the original manual tags for each file.

This script:
  1. Reads vault_map for each file's expected manual tags
  2. Reads the file's current tags
  3. If manual tags are missing, merges them back in (preserving kw/ tags)

Usage:
    python repair_lost_tags.py --dry-run    # Preview only
    python repair_lost_tags.py              # Apply repairs
"""

import argparse
import json
import os
import re
import sys
import time

VAULT_BASE = r"G:\My Drive\Obsidian_Vault"
VAULT_MAP_FILE = r"C:\Projects\LocalAI\Vault_Map\vault_map_data.json"


# ---------------------------------------------------------------------------
# Frontmatter helpers (inline format only — vault standard)
# ---------------------------------------------------------------------------

def get_current_tags(content: str) -> tuple[list[str], str, str] | None:
    """Parse current inline tags from file content.

    Returns (tags_list, old_tags_line, fm_block) or None if no frontmatter.
    """
    if not content.startswith("---"):
        return None

    end_match = re.search(r'\n---[ \t]*\n', content[3:])
    if not end_match:
        return None

    fm_start = 0
    fm_end = 3 + end_match.end()
    fm_block = content[fm_start:fm_end]

    # Try inline format first
    tag_match = re.search(r'^(tags:\s*\[([^\]]*)\])', fm_block, re.MULTILINE)
    if tag_match:
        raw = tag_match.group(2)
        tags = [t.strip() for t in raw.split(",") if t.strip()]
        return tags, tag_match.group(1), fm_block

    # Multiline format
    ml_match = re.search(r'^(tags:\s*\n(?:\s+-\s+[^\n]*\n?)*)', fm_block, re.MULTILINE)
    if ml_match:
        tags = re.findall(r'^\s+-\s+(.+)$', ml_match.group(1), re.MULTILINE)
        return [t.strip() for t in tags], ml_match.group(1).rstrip('\n'), fm_block

    return None


def rebuild_inline_tags(all_tags: list[str]) -> str:
    if not all_tags:
        return "tags: []"
    return f"tags: [{', '.join(all_tags)}]"


def apply_repair(content: str, recovered_tags: list[str], current_tags: list[str],
                 old_tags_line: str, fm_block: str) -> str:
    """Merge recovered manual tags back into the file, preserving kw/ tags."""
    # Remove duplicates, preserve order: recovered manual first, then kw/
    kw_tags = [t for t in current_tags if t.startswith("kw/") or t.startswith("ctx/")]
    manual_tags = list(dict.fromkeys(recovered_tags))  # dedupe, preserve order
    new_tags_line = rebuild_inline_tags(manual_tags + kw_tags)
    new_fm_block = fm_block.replace(old_tags_line, new_tags_line, 1)
    return content.replace(fm_block, new_fm_block, 1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Repair manual tags lost during keyword backfill.")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no writes.")
    args = parser.parse_args()

    print(f"Loading vault map from {VAULT_MAP_FILE}...")
    with open(VAULT_MAP_FILE, "r", encoding="utf-8") as f:
        vault_data = json.load(f)
    print(f"  {len(vault_data)} entries loaded.\n")

    stats = {"checked": 0, "repaired": 0, "already_ok": 0, "skipped": 0, "errors": 0}
    start = time.time()

    for rel_path, entry in vault_data.items():
        # Expected manual tags from vault_map (pre-backfill ground truth)
        expected_tags = entry.get("data", {}).get("tags", [])
        # vault_map stored tags as strings; filter to non-kw/non-ctx manual tags
        expected_manual = [str(t) for t in expected_tags
                           if not str(t).startswith("kw/") and not str(t).startswith("ctx/")]

        if not expected_manual:
            stats["skipped"] += 1
            continue

        full_path = os.path.join(VAULT_BASE, rel_path.replace("/", os.sep))
        if not os.path.exists(full_path):
            stats["errors"] += 1
            continue

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"  [ERROR] Read {rel_path}: {e}")
            stats["errors"] += 1
            continue

        result = get_current_tags(content)
        if result is None:
            stats["skipped"] += 1
            continue

        current_tags, old_tags_line, fm_block = result
        current_manual = [t for t in current_tags
                          if not t.startswith("kw/") and not t.startswith("ctx/")]

        stats["checked"] += 1

        # Check if any expected manual tags are missing
        missing = [t for t in expected_manual if t not in current_manual]
        if not missing:
            stats["already_ok"] += 1
            continue

        # Repair needed
        if args.dry_run:
            print(f"  [DRY-RUN] {rel_path}")
            print(f"    Current manual:  {current_manual}")
            print(f"    Missing:         {missing}")
            print(f"    Full expected:   {expected_manual}")
        else:
            try:
                new_content = apply_repair(content, expected_manual, current_tags,
                                           old_tags_line, fm_block)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
            except Exception as e:
                print(f"  [ERROR] Write {rel_path}: {e}")
                stats["errors"] += 1
                continue

        stats["repaired"] += 1

    elapsed = time.time() - start
    print()
    print("=" * 60)
    print(f"Tag Repair {'(DRY RUN) ' if args.dry_run else ''}Complete")
    print(f"  Time:          {elapsed:.1f}s")
    print(f"  Files checked: {stats['checked']}")
    print(f"  Repaired:      {stats['repaired']}")
    print(f"  Already OK:    {stats['already_ok']}")
    print(f"  Skipped:       {stats['skipped']}")
    print(f"  Errors:        {stats['errors']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
