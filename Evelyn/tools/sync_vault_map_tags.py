"""
sync_vault_map_tags.py — Re-sync the tags field in vault_map_data.json from
the actual files on disk, without touching gists or triggering LLM calls.

After the keyword-tag backfill + repairs, vault_map_data.json has stale
data.tags for ~1800+ files (we updated mtimes but not the tags field).
This script reads the current frontmatter tags from each file and writes
them back to vault_map_data.json in-place.

No LLM calls. No mtime changes. Fast (~10s for the full vault).

Usage:
    python sync_vault_map_tags.py --dry-run    # Preview stats only
    python sync_vault_map_tags.py              # Apply
"""

import argparse
import json
import os
import re
import time

VAULT_BASE = r"G:\My Drive\Obsidian_Vault"
VAULT_MAP_FILE = r"C:\Projects\LocalAI\Vault_Map\vault_map_data.json"


def read_frontmatter_tags(content: str) -> list[str] | None:
    """Read the current tags from file frontmatter (inline or multiline).

    Returns list of tag strings, or None if no frontmatter found.
    """
    if not content.startswith("---"):
        return None

    end_match = re.search(r'\n---[ \t]*\n', content[3:])
    if not end_match:
        return None

    fm_text = content[3: 3 + end_match.start()]

    # Inline: tags: [a, b, c]
    inline = re.search(r'^tags:\s*\[([^\]]*)\]', fm_text, re.MULTILINE)
    if inline:
        raw = inline.group(1)
        return [t.strip() for t in raw.split(",") if t.strip()]

    # Multiline: tags:\n  - a\n  - b
    if re.search(r'^tags:\s*$', fm_text, re.MULTILINE):
        tags = re.findall(r'^\s+-\s+(.+)$', fm_text, re.MULTILINE)
        return [t.strip() for t in tags]

    return None


def main():
    parser = argparse.ArgumentParser(description="Sync vault_map tags from actual files.")
    parser.add_argument("--dry-run", action="store_true", help="Show stats only, no writes.")
    args = parser.parse_args()

    print(f"Loading vault map from {VAULT_MAP_FILE}...")
    with open(VAULT_MAP_FILE, "r", encoding="utf-8") as f:
        vault_data = json.load(f)
    print(f"  {len(vault_data)} entries loaded.\n")

    stats = {"updated": 0, "unchanged": 0, "missing_file": 0, "no_frontmatter": 0, "errors": 0}
    start = time.time()

    for rel_path, entry in vault_data.items():
        full_path = os.path.join(VAULT_BASE, rel_path.replace("/", os.sep))

        if not os.path.exists(full_path):
            stats["missing_file"] += 1
            continue

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            print(f"  [ERROR] {rel_path}: {e}")
            stats["errors"] += 1
            continue

        actual_tags = read_frontmatter_tags(content)
        if actual_tags is None:
            stats["no_frontmatter"] += 1
            continue

        stored_tags = entry.get("data", {}).get("tags", [])
        # Compare as sorted string lists to handle order differences
        if sorted(str(t) for t in stored_tags) == sorted(actual_tags):
            stats["unchanged"] += 1
            continue

        # Update in-place
        if "data" not in entry:
            entry["data"] = {}
        entry["data"]["tags"] = actual_tags
        stats["updated"] += 1

    elapsed = time.time() - start

    if not args.dry_run and stats["updated"] > 0:
        print(f"Writing updated vault map ({stats['updated']} entries changed)...")
        with open(VAULT_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(vault_data, f, indent=2, ensure_ascii=False)
        print("  Done.\n")

    print("=" * 60)
    print(f"Vault Map Tag Sync {'(DRY RUN) ' if args.dry_run else ''}Complete")
    print(f"  Time:              {elapsed:.1f}s")
    print(f"  Tags updated:      {stats['updated']}")
    print(f"  Already in sync:   {stats['unchanged']}")
    print(f"  No frontmatter:    {stats['no_frontmatter']}")
    print(f"  File not found:    {stats['missing_file']}")
    print(f"  Errors:            {stats['errors']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
