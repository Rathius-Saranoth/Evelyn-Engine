#!/usr/bin/env python3
# audit_vault_tags.py
# date created: 2026-08-28
# date modified: 2026-08-28 11:44:52
# tags: #[tag, #librarian, #taxonomy, #audit, #cli, #maintenance, #evelyn]

"""audit_vault_tags.py — Standalone CLI runner for Obsidian Tag Librarian.

Runs incremental or batch taxonomy audits against vault documents, prioritizing:
1. Documents with no tags
2. Documents with multi-dash flat compound tags
3. Documents with simple flat tags (no hierarchy slashes)
4. Un-audited documents with existing nested tags
5. Routine rotation of oldest audited documents

Usage:
    python scripts/audit_vault_tags.py --limit 20
    python scripts/audit_vault_tags.py --limit 100 --verbose
    python scripts/audit_vault_tags.py --continuous
"""

import argparse
import signal
import time

from Evelyn.tools import tag_librarian, vault_db

_stop_requested = False


def _signal_handler(sig, frame):
    global _stop_requested
    print("\n[AUDIT] Stop requested. Finishing current document...", flush=True)
    _stop_requested = True


def main():
    parser = argparse.ArgumentParser(description="Audit and normalize Obsidian vault tags via Tag Librarian.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of documents to audit (0 for unlimited).")
    parser.add_argument("--continuous", action="store_true", help="Run continuously until stopped with Ctrl+C.")
    parser.add_argument("--verbose", action="store_true", help="Print detailed tag addition/removal breakdowns.")
    parser.add_argument("--sync-taxonomy", action="store_true", help="Run master taxonomy prune/sync after batch.")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    print("=" * 65)
    print(" 🏷️  Evelyn Tag Librarian — Batch Audit Runner")
    print("=" * 65)

    limit = 0 if args.continuous else args.limit
    audited_count = 0
    modified_count = 0
    skipped_count = 0
    start_time = time.time()

    print(f"Target: {'Continuous mode' if limit == 0 else f'{limit} documents'}")
    print("Press Ctrl+C at any time to gracefully stop.\n")

    while not _stop_requested:
        if limit > 0 and audited_count >= limit:
            break

        doc_info = vault_db.fetch_next_document_for_tag_audit()
        if not doc_info:
            print("[AUDIT] No more eligible documents found in vault.")
            break

        path = doc_info.get("path", "")

        t0 = time.time()
        res = tag_librarian.audit_single_document(path)
        elapsed = time.time() - t0

        status = res.get("status")
        if status == "skipped":
            skipped_count += 1
            if args.verbose:
                print(f"  [SKIPPED] {path}")
            continue
        elif status == "error":
            print(f"  [ERROR] {path}: {res.get('message')}")
            audited_count += 1
            continue

        audited_count += 1
        was_modified = res.get("modified", False)
        if was_modified:
            modified_count += 1

        prefix = "✏️ [MODIFIED]" if was_modified else "✓ [VERIFIED]"
        print(f"{prefix} ({audited_count}{f'/{limit}' if limit > 0 else ''}) {path} ({elapsed:.1f}s)")

        if args.verbose or was_modified:
            prev = res.get("previous_tags", [])
            final = res.get("final_tags", [])
            print(f"    - Before: {prev}")
            print(f"    - After : {final}")

        # Throttle slightly to keep GPU/LLM thermals balanced
        time.sleep(0.2)

    total_elapsed = time.time() - start_time
    print("\n" + "=" * 65)
    print(" 📊 Tag Audit Summary")
    print("=" * 65)
    print(f"  - Documents Audited  : {audited_count}")
    print(f"  - Documents Modified : {modified_count}")
    print(f"  - Documents Skipped  : {skipped_count}")
    print(f"  - Total Elapsed Time : {total_elapsed:.1f}s")
    if audited_count > 0:
        print(f"  - Average Pace       : {total_elapsed / audited_count:.1f}s / document")

    if args.sync_taxonomy or modified_count > 0:
        print("\n[TAXONOMY] Maintaining Master Tag Taxonomy in SQLite & Chroma...")
        m_res = tag_librarian.maintain_master_taxonomy()
        print(f"  - Active Master Tags : {m_res.get('total_master_tags', 0)}")
        print(f"  - Pruned Orphan Tags : {m_res.get('removed_master_tags', 0)}")

    print("\nDone.")


if __name__ == "__main__":
    main()
