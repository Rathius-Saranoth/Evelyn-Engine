#!/usr/bin/env python3
# scripts/migrate_subject_codes.py
"""
migrate_subject_codes.py — One-time migration for Fast Memory category codes.

Migrates category subject code suffixes:
  Cat##-R -> Cat##-U (User)
  Cat##-E -> Cat##-A (Assistant)

Performs:
1. Strict regex migration on SQLite context_entries.category and fast_memory_proposals.
2. Renames physical vault context entry files matching CE_Cat##-[RE]_*.md and updates frontmatter.
3. Supports --dry-run for validation before making changes.
"""

import os
import sys
import re
import sqlite3
import argparse
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for d in (ROOT_DIR, TOOLS_DIR):
    if d not in sys.path:
        sys.path.insert(0, d)

import evelyn_config as cfg

CAT_REGEX = re.compile(r"^Cat(\d{2})-([RE])$")
CODE_MAP = {
    "R": getattr(cfg, "SUBJECT_CODE_USER", "U"),
    "E": getattr(cfg, "SUBJECT_CODE_ASSISTANT", "A"),
}


def migrate_database(db_path: str, dry_run: bool = False) -> dict:
    """Migrate context_entries and fast_memory_proposals in SQLite."""
    if not os.path.exists(db_path):
        print(f"[DB] Error: Database not found at {db_path}")
        return {"entries_updated": 0, "proposals_updated": 0}

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 1. context_entries
    rows = cur.execute("SELECT id, category FROM context_entries").fetchall()
    entries_to_update = []
    for row in rows:
        cat = row["category"] or ""
        m = CAT_REGEX.match(cat)
        if m:
            num, suffix = m.group(1), m.group(2)
            new_cat = f"Cat{num}-{CODE_MAP[suffix]}"
            entries_to_update.append((new_cat, row["id"]))

    # 2. proposals
    p_rows = cur.execute("SELECT id, suggested_category, merged_observation FROM proposals").fetchall()
    proposals_to_update = []
    for prow in p_rows:
        s_cat = prow["suggested_category"] or ""
        obs = prow["merged_observation"] or ""
        new_s_cat = s_cat
        m = CAT_REGEX.match(s_cat)
        if m:
            num, suffix = m.group(1), m.group(2)
            new_s_cat = f"Cat{num}-{CODE_MAP[suffix]}"

        new_obs = obs
        # Replace category: Cat##-R / Cat##-E in YAML
        if obs and "Cat" in obs:
            new_obs = re.sub(r"\bCat(\d{2})-R\b", r"Cat\1-U", new_obs)
            new_obs = re.sub(r"\bCat(\d{2})-E\b", r"Cat\1-A", new_obs)

        if new_s_cat != s_cat or new_obs != obs:
            proposals_to_update.append((new_s_cat, new_obs, prow["id"]))

    print(f"[DB] Found {len(entries_to_update)} context_entries and {len(proposals_to_update)} proposals to migrate.")

    if not dry_run:
        cur.executemany("UPDATE context_entries SET category = ? WHERE id = ?", entries_to_update)
        cur.executemany("UPDATE proposals SET suggested_category = ?, merged_observation = ? WHERE id = ?", proposals_to_update)
        con.commit()
        print(f"[DB] Successfully committed database migrations.")

    con.close()
    return {"entries_updated": len(entries_to_update), "proposals_updated": len(proposals_to_update)}


def migrate_vault_files(vault_base: str, dry_run: bool = False) -> int:
    """Rename and update CE_Cat##-[RE]_*.md notes in Obsidian Vault."""
    if not os.path.exists(vault_base):
        print(f"[VAULT] Vault root not found: {vault_base}")
        return 0

    file_pattern = re.compile(r"^CE_Cat(\d{2})-([RE])_(.*)\.md$")
    renamed_count = 0

    for root, _, files in os.walk(vault_base):
        for f in files:
            m = file_pattern.match(f)
            if m:
                num, suffix, rest = m.group(1), m.group(2), m.group(3)
                new_filename = f"CE_Cat{num}-{CODE_MAP[suffix]}_{rest}.md"
                src_path = os.path.join(root, f)
                dst_path = os.path.join(root, new_filename)

                # Update file contents
                try:
                    with open(src_path, "r", encoding="utf-8") as fp:
                        content = fp.read()

                    # Update internal category tag or frontmatter
                    new_content = re.sub(r"\bCat(\d{2})-R\b", r"Cat\1-U", content)
                    new_content = re.sub(r"\bCat(\d{2})-E\b", r"Cat\1-A", new_content)

                    if not dry_run:
                        if new_content != content:
                            with open(src_path, "w", encoding="utf-8") as fp:
                                fp.write(new_content)
                        os.rename(src_path, dst_path)

                    renamed_count += 1
                    print(f"  [RENAME] {f} -> {new_filename}")
                except Exception as ex:
                    print(f"  [ERROR] Failed migrating file {src_path}: {ex}")

    print(f"[VAULT] Migrated {renamed_count} context entry vault files.")
    return renamed_count


def main():
    parser = argparse.ArgumentParser(description="Migrate Fast Memory category codes from -R/-E to -U/-A.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying DB or files.")
    args = parser.parse_args()

    mode_str = "DRY RUN" if args.dry_run else "LIVE MIGRATION"
    print("=" * 70)
    print(f"SUBJECT CODE TAXONOMY MIGRATION ({mode_str})")
    print(f"Target: -R -> -{CODE_MAP['R']}, -E -> -{CODE_MAP['E']}")
    print("=" * 70)

    db_path = getattr(cfg, "MEMORY_DB_PATH", r"/home/rathius/evelyn/data/evelyn_memory.db")
    vault_dir = getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")

    db_res = migrate_database(db_path, dry_run=args.dry_run)
    vault_res = migrate_vault_files(vault_dir, dry_run=args.dry_run)

    print("\n" + "=" * 70)
    print(f"MIGRATION SUMMARY:")
    print(f"  Context Entries: {db_res['entries_updated']}")
    print(f"  Proposals:       {db_res['proposals_updated']}")
    print(f"  Vault Files:     {vault_res}")
    print("=" * 70)


if __name__ == "__main__":
    main()
