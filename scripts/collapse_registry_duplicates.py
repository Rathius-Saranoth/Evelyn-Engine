#!/usr/bin/env python3
# collapse_registry_duplicates.py
# date created: 2026-09-23 18:30:00
# date modified: 2026-09-23 18:26:59
# tags: #taxonomy, #registry, #migration, #vocabulary

"""Collapse the five surviving near-duplicate pairs in the controlled vocabulary.

Each pair is one concept recorded twice. The survivor is the form actually in use, except
where §6.3.2 decides it — number form is singular by rule, which settles `symptom`/`symptoms`
where usage is nearly even.

Retirement records a `UF` alias rather than erasing (§6.2), so a document or query phrased the
retired way still resolves and the collapse is not undone by the next extraction.

Note on `usage_count`: the registry column is stale, because the pass that maintains it runs
under TAG_LIBRARIAN_ENABLED, which is off. Survivors were chosen from live counts in the vault
index and memory store, not from that column.

Usage:
    python scripts/collapse_registry_duplicates.py            # report only
    python scripts/collapse_registry_duplicates.py --execute
"""

import argparse
import os
import re
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evelyn_config as cfg
from Evelyn.tools import tag_librarian

# retired -> preferred
COLLAPSES = {
    "family-history": "genealogy",
    "artifacts": "artifact",
    "napping": "nap",
    "relationships": "relationship",
    "symptoms": "symptom",
}

TAGS_LINE = re.compile(r"^tags:[ \t]*(.*)$", re.MULTILINE)


def _tags_of(text: str) -> list[str]:
    """The note's tag list, as written."""
    m = TAGS_LINE.search(text)
    if not m:
        return []
    inner = m.group(1).strip()
    bare = inner[1:-1] if inner.startswith("[") and inner.endswith("]") else inner
    return [t.strip() for t in bare.split(",") if t.strip()]


def _rewrite_tags_line(text: str) -> tuple[str, bool]:
    """Swap retired forms for their preferred term in a note's tags line."""
    m = TAGS_LINE.search(text)
    if not m:
        return text, False
    inner = m.group(1).strip()
    bare = inner[1:-1] if inner.startswith("[") and inner.endswith("]") else inner
    tags = [t.strip() for t in bare.split(",") if t.strip()]
    out, changed = [], False
    for t in tags:
        repl = COLLAPSES.get(t, t)
        if repl != t:
            changed = True
        if repl not in out:
            out.append(repl)
    if not changed:
        return text, False
    return TAGS_LINE.sub(lambda _m: f"tags: [{', '.join(out)}]", text, count=1), True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    vault = getattr(cfg, "VAULT_BASE_DIR", os.path.expanduser("~/obsidian_vault"))
    mem_path = getattr(cfg, "MEMORY_DB_PATH", "/home/rathius/evelyn/data/evelyn_memory.db")

    notes = []
    for root, _dirs, files in os.walk(vault):
        for name in files:
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue
            new_text, changed = _rewrite_tags_line(text)
            if changed:
                notes.append((path, new_text))

    con = sqlite3.connect(mem_path)
    entries = []
    for eid, tags in con.execute(
        "SELECT id, tags FROM context_entries WHERE status='live' AND tags IS NOT NULL AND TRIM(tags)!=''"
    ):
        parts = [t.strip() for t in str(tags).split(",") if t.strip()]
        out, changed = [], False
        for t in parts:
            repl = COLLAPSES.get(t, t)
            if repl != t:
                changed = True
            if repl not in out:
                out.append(repl)
        if changed:
            entries.append((eid, ", ".join(out)))

    print("Registry duplicate collapse\n")
    for old, new in COLLAPSES.items():
        print(f"  {old:16} -> {new}")
    print(f"\n  vault notes to rewrite : {len(notes)}")
    print(f"  memory entries to fix  : {len(entries)}")
    for p, _ in notes:
        print(f"    note  {os.path.relpath(p, vault)}")
    for eid, t in entries:
        print(f"    entry #{eid} -> {t}")

    if not args.execute:
        con.close()
        print("\nReport only. Re-run with --execute.")
        return 0

    backup = f"/home/rathius/evelyn/scratch/collapse_backup-{time.strftime('%Y%m%d-%H%M%S')}"
    os.makedirs(backup, exist_ok=True)
    vcon = sqlite3.connect(getattr(cfg, "VAULT_DB_PATH", "/home/rathius/evelyn/data/evelyn_vault.db"))
    for path, new_text in notes:
        rel = os.path.relpath(path, vault)
        dest = os.path.join(backup, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(path, dest)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
        # Keep the index in step so the librarian does not still see the retired form. A
        # direct column update rather than `upsert_document`, which requires title and mtime
        # and would rewrite fields this change has no business touching.
        vcon.execute(
            "UPDATE vault_documents SET tags = ? WHERE path = ?",
            (", ".join(_tags_of(new_text)), rel),
        )

    vcon.commit()
    vcon.close()

    now = time.time()
    for eid, tags in entries:
        con.execute("UPDATE context_entries SET tags = ?, updated_at = ? WHERE id = ?", (tags, now, eid))
    con.commit()
    con.close()

    for old, new in COLLAPSES.items():
        tag_librarian.retire_term(old, new)

    print(f"\nRewrote {len(notes)} notes (originals in {backup}) and {len(entries)} entries.")
    print(f"Retired {len(COLLAPSES)} terms with UF aliases recorded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
