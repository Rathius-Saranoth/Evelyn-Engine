#!/usr/bin/env python3
# remap_unvetted_memory_tags.py
# date created: 2026-09-27
# date modified: 2026-09-27 17:29:13
# tags: #memory, #taxonomy, #curation, #maintenance

"""Remap unvetted named entity and alias tags in context_entries to authorized subject domain atoms.

Replaces:
  - comfyui -> image-generation
  - ollama -> large-language-models
  - dnd -> ttrpg
  - oura-ring -> biometrics
  - youtube -> media
  - relationship-dynamics -> relationship

Usage:
    PYTHONPATH=. venv/bin/python scripts/remap_unvetted_memory_tags.py --dry-run
    PYTHONPATH=. venv/bin/python scripts/remap_unvetted_memory_tags.py --execute
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

MEMORY_DB_PATH = Path("/home/rathius/evelyn/data/evelyn_memory.db")
VAULT_DB_PATH = Path("/home/rathius/evelyn/data/evelyn_vault.db")

REMAP_MAP: dict[str, str] = {
    "comfyui": "image-generation",
    "ollama": "large-language-models",
    "dnd": "ttrpg",
    "oura-ring": "biometrics",
    "youtube": "media",
    "relationship-dynamics": "relationship",
}


def remap_tag_list(tags: list[str]) -> list[str]:
    """Replace unvetted tags with their canonical subject mapping, deduplicate and sort."""
    new_tags: set[str] = set()
    for t in tags:
        t_clean = t.strip()
        if not t_clean:
            continue
        mapped = REMAP_MAP.get(t_clean, t_clean)
        new_tags.add(mapped)
    return sorted(new_tags)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply changes to evelyn_memory.db. Default is dry-run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate run without writing to database.",
    )
    args = parser.parse_args()
    execute = args.execute and not args.dry_run

    if not MEMORY_DB_PATH.exists():
        print(f"[ERROR] Memory database not found: {MEMORY_DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(MEMORY_DB_PATH)
    cursor = conn.cursor()

    rows = cursor.execute("SELECT id, status, tags FROM context_entries").fetchall()
    modified_rows: list[tuple[int, str, str, str]] = []

    for row_id, status, tags_str in rows:
        if not tags_str:
            continue
        curr_tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        if any(t in REMAP_MAP for t in curr_tags):
            new_tags = remap_tag_list(curr_tags)
            new_tags_str = ", ".join(new_tags)
            if new_tags_str != tags_str:
                modified_rows.append((row_id, status, tags_str, new_tags_str))

    print(f"[REMAP] Scanned {len(rows)} entries in context_entries.")
    print(f"[REMAP] Found {len(modified_rows)} rows carrying target tags.")

    for row_id, status, old_tags, new_tags in modified_rows:
        print(f"  Entry #{row_id} [{status}]:")
        print(f"    - Before: {old_tags}")
        print(f"    + After:  {new_tags}")

    if not execute:
        print("\n[DRY RUN] No changes written. Run with --execute to apply.")
        conn.close()
        return 0

    print(f"\n[EXECUTE] Writing updates to {len(modified_rows)} rows in evelyn_memory.db...")
    now = time.time()
    for row_id, _status, _old_tags, new_tags in modified_rows:
        cursor.execute(
            "UPDATE context_entries SET tags = ?, updated_at = ? WHERE id = ?",
            (new_tags, now, row_id),
        )
    conn.commit()
    conn.close()
    print("[EXECUTE] Successfully updated context_entries in evelyn_memory.db.")

    # Re-verify against master_tag_taxonomy in evelyn_vault.db
    if VAULT_DB_PATH.exists():
        v_conn = sqlite3.connect(VAULT_DB_PATH)
        auth_tags = {
            r[0] for r in v_conn.execute("SELECT tag FROM master_tag_taxonomy").fetchall()
        }
        v_conn.close()

        m_conn = sqlite3.connect(MEMORY_DB_PATH)
        unregistered: dict[str, int] = {}
        for (tags_str,) in m_conn.execute(
            "SELECT tags FROM context_entries WHERE status = 'live' AND tags IS NOT NULL AND tags != ''"
        ):
            for t in [x.strip() for x in tags_str.split(",") if x.strip()]:
                if t.startswith(("event/", "motif/", "setting/")):
                    continue
                if t not in auth_tags:
                    unregistered[t] = unregistered.get(t, 0) + 1
        m_conn.close()

        print("\n=== Post-Migration Verification ===")
        print(f"Unregistered tag forms remaining in live memory: {len(unregistered)}")
        for tag, cnt in unregistered.items():
            print(f"  {tag}: {cnt}")
        if len(unregistered) == 0:
            print("✔ 100.0% zero-unvetted compliance achieved across evelyn_memory.db!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
