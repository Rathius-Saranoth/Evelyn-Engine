#!/usr/bin/env python3
# backfill_memory_tags.py
# date created: 2026-09-24
# date modified: 2026-09-24 20:08:10
# tags: #memory, #taxonomy, #backfill, #maintenance

"""Re-tag the memory facts left untagged by the 000.006.187 vocabulary reset.

`000.006.187` cleared 39,061 tags across 12,893 memory rows so the vocabulary could be
regenerated from the standard rather than migrated toward it. `000.006.186` did the same to the
vault *and* reset every document's audit timestamp, so the vault re-entered the semantic queue
and has been draining since. Memory was left with no equivalent, and 10,033 live facts have been
invisible to tag retrieval ever since.

This is a one-time backlog, not a scheduled pass: every current writer tags what it writes, so
nothing is adding to it. Run it in the foreground, overnight if need be; it stops cleanly on
Ctrl-C and resumes from where it stopped, because progress is a per-row cursor rather than a
position in a list.

    PYTHONPATH=. venv/bin/python scripts/backfill_memory_tags.py --limit 20 --dry-run
    PYTHONPATH=. venv/bin/python scripts/backfill_memory_tags.py --limit 500 --execute

Only terms the vocabulary already holds are written. Anything else becomes a `tag_admission`
proposal naming the entry, so approval can put it back.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from types import FrameType

sys.path.insert(0, "/home/rathius/evelyn")

from Evelyn.tools import memory_db, tag_librarian

_stop = False


def _request_stop(_signum: int, _frame: FrameType | None) -> None:
    global _stop
    _stop = True
    print("\n[BACKFILL] Stop requested — finishing the current fact, then exiting.", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=20, help="Facts to process this run.")
    ap.add_argument("--batch-size", type=int, default=10, help="Facts fetched per query.")
    ap.add_argument("--execute", action="store_true", help="Write. Default is a dry run.")
    ap.add_argument("--dry-run", action="store_true", help="Explicit no-op, the default.")
    args = ap.parse_args()
    dry_run = not args.execute

    signal.signal(signal.SIGINT, _request_stop)

    untagged, total = memory_db.count_entries_awaiting_tag_audit()
    print("=" * 72)
    print(" 🏷️  Memory Tag Backfill — the 000.006.187 reset's missing half")
    print("=" * 72)
    print(f"  Awaiting audit: {total} live fact(s), {untagged} of them untagged")
    print(f"  This run: {args.limit}{'  [DRY RUN — nothing will be written]' if dry_run else ''}")
    print()

    processed = tagged = proposed_total = 0
    started = time.time()

    # Every term classification held back, whether or not a proposal was raised for it. Past
    # TAG_ADMISSION_MAX_PENDING the queue stops accepting, and the fact is stamped either way —
    # so without this the terms a full queue refused would be gone, with the fact marked done.
    unmatched_log: dict[str, list[str]] = {}
    log_path = Path("scratch") / f"backfill_unmatched-{time.strftime('%Y%m%d-%H%M%S')}.json"

    while processed < args.limit and not _stop:
        want = min(args.batch_size, args.limit - processed)
        batch = memory_db.fetch_next_entries_for_tag_audit(batch_size=want)
        if not batch:
            print("[BACKFILL] Nothing left awaiting a tag audit.")
            break

        for entry in batch:
            if _stop:
                break
            result = tag_librarian.audit_single_fact_tags(entry, dry_run=dry_run)
            processed += 1
            if result["applied"]:
                tagged += 1
            proposed_total += len(result["proposed"])
            if result.get("held_back"):
                unmatched_log[str(result["id"])] = result["held_back"]

            applied = ", ".join(result["applied"]) or "—"
            held = result["proposed"] if not dry_run else result["held_back"]
            extra = f"  {'proposed' if not dry_run else 'would propose'}: {', '.join(held)}" if held else ""
            print(f"  #{result['id']:<6} {result['status']:<8} {applied}{extra}")

        if dry_run:
            # Nothing was stamped, so the same batch would come back forever.
            print("\n[BACKFILL] Dry run stops after one batch; the cursor did not move.")
            break

    elapsed = time.time() - started
    print()
    print("=" * 72)
    print(f"  Processed {processed} | tagged {tagged} | proposals raised {proposed_total} "
          f"| {elapsed:.1f}s ({elapsed / max(processed, 1):.1f}s each)")
    remaining_untagged, remaining_total = memory_db.count_entries_awaiting_tag_audit()
    print(f"  Remaining: {remaining_total} awaiting audit, {remaining_untagged} untagged")
    if unmatched_log and not dry_run:
        log_path.parent.mkdir(exist_ok=True)
        log_path.write_text(json.dumps(unmatched_log, indent=1), encoding="utf-8")
        held = sum(len(v) for v in unmatched_log.values())
        print(f"  {held} unmatched term(s) across {len(unmatched_log)} fact(s) recorded in {log_path}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
