"""
apply_keyword_tags.py — Phase 1 Keyword-to-Tag backfill script.

Reads existing keywords from vault_map_data.json gist strings and writes
them back into each Obsidian markdown file's YAML frontmatter as #kw/ tags.

Uses the "Clean Slate" strategy:
  1. Parse existing tags from frontmatter
  2. Purge all tags starting with kw/ (clear stale LLM-generated tags)
  3. Inject newly extracted kw/ tags
  4. Preserve all manual tags untouched

After writing, updates the mtime in vault_map_data.json so that
generate_vault_map.py skips the file on its next run.

Usage:
    python apply_keyword_tags.py              # Full run
    python apply_keyword_tags.py --dry-run    # Preview only, no file writes
    python apply_keyword_tags.py --limit 10   # Process only the first 10 files
"""

# apply_keyword_tags.py

import argparse
import json
import os
import re
import sys
import time

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
VAULT_BASE = r"G:\My Drive\Obsidian_Vault"
VAULT_MAP_FILE = r"C:\Projects\LocalAI\Vault_Map\vault_map_data.json" # [[vault_map_data.json]]

# ---------------------------------------------------------------------------
# Keyword parsing
# ---------------------------------------------------------------------------

def extract_keywords_from_gist(gist: str) -> list[str]:
    """Extract keywords from a gist string's 'Keywords: ...' line.

    Args:
        gist: The full gist string from vault_map_data.json.

    Returns:
        List of raw keyword strings, or empty list if no Keywords line found.
    """
    # Match "Keywords:" anywhere in the gist (usually at the end)
    match = re.search(r'Keywords?:\s*(.+)', gist, re.IGNORECASE)
    if not match:
        return []

    raw = match.group(1).strip()
    # Split on comma, strip whitespace from each keyword
    keywords = [kw.strip() for kw in raw.split(",") if kw.strip()]
    return keywords


def slugify_keyword(keyword: str) -> str:
    """Convert a keyword string to a tag-safe slug.

    Rules:
        - Lowercase
        - Replace spaces with hyphens
        - Remove characters that aren't alphanumeric, hyphens, or slashes
        - Collapse multiple hyphens
        - Strip leading/trailing hyphens

    Examples:
        "GIS Work"        -> "gis-work"
        "Trust"           -> "trust"
        "AI software"     -> "ai-software"
        "cat-friendly"    -> "cat-friendly"

    Args:
        keyword: Raw keyword string.

    Returns:
        Slugified string suitable for use as a tag.
    """
    slug = keyword.lower().strip()
    slug = slug.replace(" ", "-")
    slug = re.sub(r'[^a-z0-9\-/]', '', slug)
    slug = re.sub(r'-{2,}', '-', slug)
    slug = slug.strip("-")
    return slug


# ---------------------------------------------------------------------------
# Frontmatter manipulation
# ---------------------------------------------------------------------------

def parse_frontmatter(content: str) -> tuple[dict, int, int]:
    """Parse YAML frontmatter from a markdown file's content.

    Handles both inline and multiline YAML tag formats:
        inline:    tags: [tag1, tag2, tag3]
        multiline: tags:
                     - tag1
                     - tag2

    Args:
        content: Full file content string.

    Returns:
        (frontmatter_dict, fm_start, fm_end) where fm_start/fm_end are
        character positions of the opening and closing '---' delimiters.
        Returns ({}, -1, -1) if no valid frontmatter found.
    """
    if not content.startswith("---"):
        return {}, -1, -1

    # Find the closing ---
    end_match = re.search(r'\n---[ \t]*\n', content[3:])
    if not end_match:
        return {}, -1, -1

    fm_start = 0
    fm_end = 3 + end_match.end()
    fm_text = content[3: 3 + end_match.start()]

    result = {}
    lines = fm_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        # Inline format: tags: [a, b, c]
        inline_match = re.match(r'^tags:\s*\[(.*)\]\s*$', line)
        if inline_match:
            raw = inline_match.group(1)
            tags = [t.strip() for t in raw.split(",") if t.strip()]
            result["tags"] = tags
            result["tags_format"] = "inline"
            i += 1
            continue
        # Multiline format: tags:\n  - a\n  - b
        if re.match(r'^tags:\s*$', line):
            tags = []
            i += 1
            while i < len(lines) and re.match(r'^\s+-\s+', lines[i]):
                tag = re.match(r'^\s+-\s+(.*)', lines[i]).group(1).strip()
                tags.append(tag)
                i += 1
            result["tags"] = tags
            result["tags_format"] = "multiline"
            continue
        i += 1

    return result, fm_start, fm_end


def rebuild_tags_line(manual_tags: list[str], kw_tags: list[str], fmt: str = "inline") -> str:
    """Build a new tags block with manual tags preserved and kw/ tags appended.

    Args:
        manual_tags: Tags that don't start with kw/ or ctx/ (preserved as-is).
        kw_tags: New kw/ tags to inject.
        fmt: 'inline' or 'multiline' — matches original file format.

    Returns:
        Formatted tags string, either inline or multiline YAML.
    """
    all_tags = manual_tags + kw_tags
    if fmt == "multiline":
        if not all_tags:
            return "tags:"
        lines = ["tags:"]
        for tag in all_tags:
            lines.append(f"  - {tag}")
        return "\n".join(lines)
    else:
        if not all_tags:
            return "tags: []"
        return f"tags: [{', '.join(all_tags)}]"


def apply_tags_to_content(content: str, new_kw_tags: list[str]) -> str | None:
    """Apply Clean Slate tag merge to file content.

    1. Parse existing frontmatter tags (inline or multiline)
    2. Remove all kw/ and ctx/ prefixed tags
    3. Append new kw/ tags
    4. Rewrite the tags block in the frontmatter, preserving format

    Args:
        content: Full file content string.
        new_kw_tags: List of kw/ prefixed tag strings to inject.

    Returns:
        Modified content string, or None if frontmatter couldn't be parsed
        or no changes were needed.
    """
    fm, fm_start, fm_end = parse_frontmatter(content)
    if fm_start == -1:
        return None
    if "tags" not in fm:
        return None

    fmt = fm.get("tags_format", "inline")
    existing_tags = fm["tags"]
    # Preserve all manual tags (not kw/ or ctx/), keeping their original string form
    manual_tags = [t for t in existing_tags
                   if not str(t).startswith("kw/") and not str(t).startswith("ctx/")]

    # Always write inline format — vault linter enforces inline, all tools write inline.
    # Multiline files are normalized to inline on first write.
    new_tags_block = rebuild_tags_line(manual_tags, new_kw_tags, "inline")

    fm_block = content[fm_start:fm_end]

    if fmt == "multiline":
        # Match the full multiline tags block: 'tags:\n  - a\n  - b'
        old_block_match = re.search(
            r'^tags:\s*\n(?:\s+-\s+[^\n]*\n?)*',
            fm_block,
            re.MULTILINE
        )
        if not old_block_match:
            return None
        old_tags_block = old_block_match.group(0).rstrip("\n")
    else:
        # Match the inline tags line
        old_block_match = re.search(r'^tags:\s*\[.*?\]', fm_block, re.MULTILINE)
        if not old_block_match:
            return None
        old_tags_block = old_block_match.group(0)

    if old_tags_block == new_tags_block:
        return None  # No change needed

    new_fm_block = fm_block.replace(old_tags_block, new_tags_block, 1)
    return content[:fm_start] + new_fm_block + content[fm_end:]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Apply keyword tags to Obsidian vault frontmatter.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing files.")
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N files (0 = all).")
    args = parser.parse_args()

    # Load vault map
    print(f"Loading vault map from {VAULT_MAP_FILE}...")
    with open(VAULT_MAP_FILE, "r", encoding="utf-8") as f:
        vault_data = json.load(f)
    print(f"  {len(vault_data)} entries loaded.")

    stats = {
        "processed": 0,
        "tagged": 0,
        "skipped_no_keywords": 0,
        "skipped_no_frontmatter": 0,
        "skipped_no_change": 0,
        "errors": 0,
    }

    checkpoint_interval = 50
    modified_entries = {}  # path -> updated mtime

    entries = list(vault_data.items())
    if args.limit > 0:
        entries = entries[:args.limit]
        print(f"  Limited to first {args.limit} files.")

    print()
    start_time = time.time()

    for i, (rel_path, entry) in enumerate(entries):
        gist = entry.get("data", {}).get("gist", "")
        if not gist:
            stats["skipped_no_keywords"] += 1
            continue

        # Extract keywords
        keywords = extract_keywords_from_gist(gist)
        if not keywords:
            stats["skipped_no_keywords"] += 1
            continue

        # Slugify to kw/ tags
        kw_tags = []
        for kw in keywords:
            slug = slugify_keyword(kw)
            if slug:
                kw_tags.append(f"kw/{slug}")

        if not kw_tags:
            stats["skipped_no_keywords"] += 1
            continue

        # Build full file path
        full_path = os.path.join(VAULT_BASE, rel_path.replace("/", os.sep))
        if not os.path.exists(full_path):
            stats["errors"] += 1
            print(f"  [ERROR] File not found: {rel_path}")
            continue

        # Read file content
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            stats["errors"] += 1
            print(f"  [ERROR] Reading {rel_path}: {e}")
            continue

        # Apply tags
        new_content = apply_tags_to_content(content, kw_tags)
        if new_content is None:
            if parse_frontmatter(content)[1] == -1:
                stats["skipped_no_frontmatter"] += 1
            else:
                stats["skipped_no_change"] += 1
            continue

        stats["processed"] += 1
        stats["tagged"] += 1

        if args.dry_run:
            # Show what would change
            fm = parse_frontmatter(content)[0]
            old_tags = fm.get("tags", [])
            manual = [t for t in old_tags if not t.startswith("kw/") and not t.startswith("ctx/")]
            print(f"  [DRY-RUN] {rel_path}")
            print(f"    Manual tags kept: {manual}")
            print(f"    New kw/ tags: {kw_tags}")
        else:
            # Write file
            try:
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(new_content)

                # Read back the new mtime
                new_mtime = os.path.getmtime(full_path)
                modified_entries[rel_path] = new_mtime

            except Exception as e:
                stats["errors"] += 1
                print(f"  [ERROR] Writing {rel_path}: {e}")
                continue

        # Periodic checkpoint (save updated mtimes to vault map)
        if not args.dry_run and stats["tagged"] % checkpoint_interval == 0 and stats["tagged"] > 0:
            _save_mtime_updates(vault_data, modified_entries)
            print(f"  [CHECKPOINT] {stats['tagged']} files tagged, mtimes saved.")

    # Final save
    if not args.dry_run and modified_entries:
        _save_mtime_updates(vault_data, modified_entries)

    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print(f"Keyword Tag Backfill {'(DRY RUN) ' if args.dry_run else ''}Complete")
    print(f"  Time: {elapsed:.1f}s")
    print(f"  Files tagged:              {stats['tagged']}")
    print(f"  Skipped (no keywords):     {stats['skipped_no_keywords']}")
    print(f"  Skipped (no frontmatter):  {stats['skipped_no_frontmatter']}")
    print(f"  Skipped (no change):       {stats['skipped_no_change']}")
    print(f"  Errors:                    {stats['errors']}")
    print("=" * 60)


def _save_mtime_updates(vault_data: dict, modified_entries: dict):
    """Update mtime values in vault_data and save to disk.

    Args:
        vault_data: The full vault map dictionary (mutated in place).
        modified_entries: Dict of {rel_path: new_mtime} for files that were written.
    """
    for path, mtime in modified_entries.items():
        if path in vault_data:
            vault_data[path]["mtime"] = mtime

    with open(VAULT_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(vault_data, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
