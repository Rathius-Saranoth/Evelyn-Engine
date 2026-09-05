#!/usr/bin/env python3
# master_librarian.py
# date created: 2026-09-05
# date modified: 2026-09-05 17:42:44
# tags: #[librarian, #master-librarian, #cli, #maintenance, #vault, #evelyn]

"""master_librarian.py — Standalone CLI runner for Evelyn Master Librarian.

Runs autonomous single-pass audits across vault documents:
- Link & ghost resolution (code block protection, spurious array wrapping, bare attachments, parent breadcrumbs)
- Formatting normalization (single-line flow arrays, unnested icon brackets)
- Table of Contents synchronization (_index.md)
- Activity logging and ambient reflection grounding

Usage:
    python scripts/master_librarian.py --limit 5 --dry-run
    python scripts/master_librarian.py --limit 20
    python scripts/master_librarian.py --path "Slipbox/My Note.md" --dry-run
    python scripts/master_librarian.py --all
"""

import argparse
import signal
import time

from Evelyn.tools import master_librarian, vault_db

_stop_requested = False


def _signal_handler(sig, frame):
    global _stop_requested
    print("\n[LIBRARIAN] Stop requested. Finishing current document...", flush=True)
    _stop_requested = True


def main():
    parser = argparse.ArgumentParser(
        description="Audit and curate Obsidian vault notes via Master Librarian."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of documents to audit (default: 5, 0 for unlimited).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Audit all eligible notes in the vault queue.",
    )
    parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="Audit a single target document path (relative to vault root).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Documents per batch pass (default: 5).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate transformations without writing to disk or updating database.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose transformation and link details.",
    )
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    print("=" * 68)
    print(" 📚 Evelyn Master Librarian — Vault Curation Runner")
    print("=" * 68)

    if args.dry_run:
        print("  ⚠️  DRY RUN MODE ENABLED — No files or databases will be modified.")

    start_time = time.time()
    audited_count = 0
    modified_count = 0
    clean_count = 0
    error_count = 0
    total_ghosts_resolved = 0
    total_flow_normalized = 0

    # Single path audit mode
    if args.path:
        print(f"\nTarget Document: {args.path}\n")
        res = master_librarian.audit_single_document(args.path, dry_run=args.dry_run)
        audited_count = 1
        status = res.get("status")
        changed = res.get("changed", False)
        elapsed = res.get("elapsed_ms", 0)

        if status == "error":
            print(f"❌ [ERROR] {args.path}: {res.get('error')}")
            error_count = 1
        elif changed:
            modified_count = 1
            print(f"✏️  [MODIFIED] {args.path} ({elapsed}ms)")
            actions = res.get("actions", [])
            for act in actions:
                print(f"    - {act}")
            if args.verbose:
                print(f"    Link Details: {res.get('link_details')}")
        else:
            clean_count = 1
            print(f"✓ [CLEAN] {args.path} ({elapsed}ms)")

    else:
        limit = 0 if args.all else args.limit
        print(f"Target: {'All eligible notes' if limit == 0 else f'{limit} documents'}")
        print(f"Batch Size: {args.batch_size}")
        print("Press Ctrl+C at any time to gracefully stop.\n")

        while not _stop_requested:
            if limit > 0 and audited_count >= limit:
                break

            remaining = limit - audited_count if limit > 0 else args.batch_size
            fetch_limit = min(args.batch_size, remaining) if limit > 0 else args.batch_size

            docs = vault_db.fetch_next_document_for_librarian_audit(fetch_limit)
            if not docs:
                print("[LIBRARIAN] No more eligible documents in vault queue.")
                break

            for doc in docs:
                if _stop_requested:
                    break
                if limit > 0 and audited_count >= limit:
                    break

                doc_path = doc.get("path", "")
                res = master_librarian.audit_single_document(doc_path, dry_run=args.dry_run)
                audited_count += 1
                status = res.get("status")
                changed = res.get("changed", False)
                elapsed = res.get("elapsed_ms", 0)

                if status == "error":
                    error_count += 1
                    print(f"❌ [ERROR] ({audited_count}) {doc_path}: {res.get('error')}")
                    continue

                if changed:
                    modified_count += 1
                    prefix = "✏️  [MODIFIED]"
                else:
                    clean_count += 1
                    prefix = "✓ [CLEAN]"

                print(
                    f"{prefix} ({audited_count}{f'/{limit}' if limit > 0 else ''}) {doc_path} ({elapsed}ms)"
                )

                actions = res.get("actions", [])
                if actions:
                    for act in actions:
                        print(f"    - {act}")
                        if "ghost" in act.lower():
                            total_ghosts_resolved += 1
                        if "flow array" in act.lower():
                            total_flow_normalized += 1

                if args.verbose and res.get("link_details"):
                    ld = res["link_details"]
                    if any(ld.values()):
                        print(f"    Details: {ld}")

    total_elapsed = time.time() - start_time
    print("\n" + "=" * 68)
    print(" 📊 Master Librarian Run Summary")
    print("=" * 68)
    print(f"  - Total Audited      : {audited_count}")
    print(f"  - Notes Modified     : {modified_count}")
    print(f"  - Notes Clean        : {clean_count}")
    print(f"  - Errors             : {error_count}")
    print(f"  - Total Elapsed Time : {total_elapsed:.2f}s")
    if audited_count > 0:
        print(f"  - Average Pace       : {(total_elapsed / audited_count) * 1000:.1f}ms / document")

    # Show vault-wide summary
    with_stats = vault_db.get_librarian_status_summary()
    print("\n 🏛️  Vault Database Status")
    print(f"  - Total Notes in Vault  : {with_stats['total_notes']:,}")
    print(
        f"  - Fully Audited Notes   : {with_stats['audited_notes']:,} ({with_stats['audit_pct']}%)"
    )
    print(f"  - Remaining Ghost Links : {with_stats['ghost_links']:,}")
    print(f"  - Recorded Curations    : {with_stats['total_activities']:,}")
    print("=" * 68 + "\n")


if __name__ == "__main__":
    main()
