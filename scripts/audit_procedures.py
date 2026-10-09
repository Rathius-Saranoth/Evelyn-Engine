#!/usr/bin/env python3
# audit_procedures.py
# date created: 2026-09-29
# date modified: 2026-09-29 20:55:44
# tags: #[procedures, #taxonomy, #librarian, #audit, #maintenance]

"""Audit and canonicalize operational procedure tags against the Master Tag Taxonomy.

Uses the same deterministic classification and reconciliation cascade as vault documents
and memory facts:
  1. Extracts candidate subjects from trigger patterns, steps, pitfalls, and tools.
  2. Strips deprecated container prefixes (skill/, procedure/, protocol/, system/, etc.).
  3. Canonicalizes existing tags through registered aliases.
  4. Applies matching terms from master_tag_taxonomy.
  5. Raises tag_admission proposals for emerging, unmatched subjects (origin='procedure #X').

Usage:
    PYTHONPATH=. venv/bin/python scripts/audit_procedures.py --limit 10 --dry-run
    PYTHONPATH=. venv/bin/python scripts/audit_procedures.py --limit 50 --execute
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from types import FrameType

sys.path.insert(0, "/home/rathius/evelyn")

from Evelyn.tools import memory_db, tag_librarian

_stop = False


def _request_stop(_signum: int, _frame: FrameType | None) -> None:
    global _stop
    _stop = True
    print("\n[PROCEDURE AUDIT] Stop requested — finishing current procedure, then exiting.", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=20, help="Procedures to process this run (default: 20).")
    ap.add_argument("--batch-size", type=int, default=10, help="Batch size per fetch (default: 10).")
    ap.add_argument("--execute", action="store_true", help="Write changes to the database. Default is dry-run.")
    ap.add_argument("--dry-run", action="store_true", help="Explicit dry-run mode (default).")
    ap.add_argument("--all-statuses", action="store_true", help="Include archived/merged procedures as well as live/extracted.")
    args = ap.parse_args()

    dry_run = not args.execute
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    statuses = ["live", "extracted"] if not args.all_statuses else ["live", "extracted", "merged", "archived"]
    untagged, total = memory_db.count_procedures_awaiting_tag_audit(statuses=statuses)

    print("=" * 72)
    print(" 🛠️  Procedure Tag Librarian Audit")
    print("=" * 72)
    print(f"  Awaiting audit: {total} procedure(s) ({untagged} untagged) [statuses: {', '.join(statuses)}]")
    print(f"  This run: limit {args.limit}{'  [DRY RUN — nothing will be written]' if dry_run else '  [EXECUTE MODE]'}")
    print()

    processed = tagged = proposed_total = 0
    started = time.time()

    while processed < args.limit and not _stop:
        want = min(args.batch_size, args.limit - processed)
        batch = memory_db.fetch_next_procedures_for_tag_audit(batch_size=want, statuses=statuses)
        if not batch:
            print("[PROCEDURE AUDIT] No procedures left awaiting audit.")
            break

        for proc in batch:
            if _stop:
                break
            result = tag_librarian.audit_single_procedure_tags(proc, dry_run=dry_run)
            processed += 1
            if result["applied"]:
                tagged += 1
            proposed_total += len(result["proposed"])

            applied_str = ", ".join(result["applied"]) or "—"
            held = result["proposed"] if not dry_run else result["held_back"]
            extra = f"  {'proposed' if not dry_run else 'would propose'}: {', '.join(held)}" if held else ""
            print(f"  #{result['id']:<4} {result['status']:<8} {applied_str}{extra}")

        if dry_run:
            print("\n[PROCEDURE AUDIT] Dry run stops after first batch; database unmodified.")
            break

    elapsed = time.time() - started
    print()
    print("=" * 72)
    print(f"  Processed {processed} | tagged {tagged} | proposals raised {proposed_total} "
          f"| {elapsed:.1f}s ({elapsed / max(processed, 1):.2f}s each)")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
