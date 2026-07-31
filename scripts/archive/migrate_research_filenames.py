#!/usr/bin/env python
# migrate_research_filenames.py
# date created: 2026-07-04
# tags: #migration, #metadata, #research, #obsidian, #utility

"""migrate_research_filenames.py — Batch updates previous research files to be named by Short Title.

Moves filename to slug of Short Title, updates title to Short Title, removes aliases,
and adds research_query.

Usage:
  python scripts/migrate_research_filenames.py --dir "c:\\Temp\\Research" --dry-run
  python scripts/migrate_research_filenames.py --dir "G:\\My Drive\\Obsidian_Vault\\Evelyn\\Research"
"""

import os
import re
import sys
import argparse
import datetime
import yaml
from typing import Tuple, Dict, Any, List

# Ensure project directories are in system path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import evelyn_config as cfg

def parse_existing_file(filepath: str) -> Tuple[Dict[str, Any], str]:
    """Parse frontmatter and return metadata dict and body content."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Match standard YAML frontmatter
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if fm_match:
        frontmatter_text = fm_match.group(1)
        body = content[fm_match.end():]
        try:
            metadata = yaml.safe_load(frontmatter_text)
            if isinstance(metadata, dict):
                return metadata, body
        except Exception as e:
            print(f"  [YAML ERROR] Failed to parse yaml for {os.path.basename(filepath)}: {e}")
    return {}, content

def slugify(text: str) -> str:
    """Create a safe filename slug from text."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[-\s]+", "-", slug).strip("-_")
    return slug

def migrate_file(filepath: str, dry_run: bool = False) -> Tuple[bool, str]:
    """Migrate frontmatter and rename file. Returns (success, new_filepath)."""
    metadata, body = parse_existing_file(filepath)
    if not metadata:
        print(f"  [SKIPPED] No YAML frontmatter found in {os.path.basename(filepath)}")
        return False, filepath

    # If already migrated (has research_query and no aliases), check if filename is already correct
    original_query = metadata.get("research_query")
    short_title = None

    if original_query:
        # Already has research_query, so 'title' is the short title
        short_title = metadata.get("title")
    else:
        # Legacy file, extract short title from aliases or use title fallback
        aliases = metadata.get("aliases")
        if aliases and isinstance(aliases, list) and len(aliases) > 0:
            short_title = str(aliases[0]).strip()
        elif aliases and isinstance(aliases, str):
            short_title = aliases.strip()
        
        # If no aliases, generate short title from title
        if not short_title:
            title_val = metadata.get("title", "")
            words = title_val.split()
            short_title = " ".join(words[:5]) + "..." if len(words) > 5 else title_val

        original_query = metadata.get("title", "")

    if not short_title:
        print(f"  [ERROR] Could not extract short title for {os.path.basename(filepath)}")
        return False, filepath

    # 1. Rebuild metadata dictionary
    new_meta = {
        "title": short_title,
        "research_query": original_query
    }

    # Copy over all other metadata fields except title, aliases, research_query
    for k, v in metadata.items():
        if k in ("title", "aliases", "research_query"):
            continue
        new_meta[k] = v

    # 2. Build yaml frontmatter string
    standard_keys = [
        "title", "research_query", "date created", "date modified", 
        "research_task_id", "scope", "source_count", "confidence", 
        "triggered_by", "tags"
    ]
    
    fm_lines = ["---"]
    written = set()
    for k in standard_keys:
        if k in new_meta:
            val = new_meta[k]
            if k == "triggered_by" and isinstance(val, str) and val.lower() == "evelyn":
                val = "Evelyn"
            if isinstance(val, str):
                val_escaped = val.replace('"', '\\"')
                fm_lines.append(f"{k}: \"{val_escaped}\"")
            elif isinstance(val, list):
                list_str = ", ".join(f"\"{item}\"" if isinstance(item, str) else str(item) for item in val)
                fm_lines.append(f"{k}: [{list_str}]")
            else:
                fm_lines.append(f"{k}: {val}")
            written.add(k)

    for k, val in new_meta.items():
        if k not in written:
            if isinstance(val, str):
                val_escaped = val.replace('"', '\\"')
                fm_lines.append(f"{k}: \"{val_escaped}\"")
            elif isinstance(val, list):
                list_str = ", ".join(f"\"{item}\"" if isinstance(item, str) else str(item) for item in val)
                fm_lines.append(f"{k}: [{list_str}]")
            else:
                fm_lines.append(f"{k}: {val}")

    fm_lines.append("---\n")
    frontmatter = "\n".join(fm_lines)
    new_content = frontmatter + body

    # 3. Determine new path
    dir_name = os.path.dirname(filepath)
    new_filename = f"{slugify(short_title)}.md"
    new_filepath = os.path.join(dir_name, new_filename)

    # Log changes
    print(f"  Short Title: \"{short_title}\"")
    print(f"  Query:       \"{original_query}\"")
    if os.path.basename(filepath) != new_filename:
        print(f"  Rename:      {os.path.basename(filepath)} -> {new_filename}")
    else:
        print(f"  Update:      No rename needed ({new_filename})")

    if dry_run:
        print("  [DRY-RUN] Would write changes and rename file.")
    else:
        # Write content
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(new_content)
        
        # Rename if filename is different
        if filepath != new_filepath:
            if os.path.exists(new_filepath):
                # Target already exists, delete old one and overwrite new one
                print(f"  [WARNING] Target file {new_filename} already exists. Overwriting.")
                with open(new_filepath, "w", encoding="utf-8") as f:
                    f.write(new_content)
                os.remove(filepath)
            else:
                os.rename(filepath, new_filepath)
        print("  [SUCCESS] Updated and renamed successfully.")

    return True, new_filepath

def main():
    parser = argparse.ArgumentParser(description="Migrate research filenames to Short Title.")
    parser.add_argument(
        "--dir",
        required=True,
        help="Target research folder containing markdown reports."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate changes without writing to disk."
    )
    args = parser.parse_args()

    target_dir = args.dir
    if not os.path.exists(target_dir):
        print(f"Target directory {target_dir} does not exist.")
        sys.exit(1)

    print(f"Starting research filenames migration on directory: {target_dir}")
    if args.dry_run:
        print("--- RUNNING IN DRY-RUN MODE ---")

    # Find all md files recursively
    md_files = []
    for root, dirs, files in os.walk(target_dir):
        for f in files:
            if f.endswith(".md"):
                md_files.append(os.path.join(root, f))

    if not md_files:
        print("No markdown files found.")
        sys.exit(0)

    print(f"Found {len(md_files)} markdown files to process.")
    
    success_count = 0
    for idx, filepath in enumerate(md_files, 1):
        print(f"\n[{idx}/{len(md_files)}] Processing: {os.path.relpath(filepath, target_dir)}")
        try:
            success, _ = migrate_file(filepath, dry_run=args.dry_run)
            if success:
                success_count += 1
        except Exception as e:
            print(f"  [ERROR] Failed to migrate file: {e}")

    print(f"\nMigration complete: {success_count}/{len(md_files)} files successfully processed.")

if __name__ == "__main__":
    main()
