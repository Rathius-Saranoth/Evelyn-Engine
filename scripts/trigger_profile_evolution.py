# trigger_profile_evolution.py
# date created: 2026-06-29
# date modified: 2026-09-18 18:51:33
# tags: #persona, #evolution, #manual, #utility

"""Standalone manual trigger for Evelyn's profile evolution pipeline.

Bypasses the idle-time threshold and heavy-task mutex (those are server-loop
concerns) and calls _evolve_document() directly for each document that has
enough qualifying entries. Safe to run while the server is up — Ollama is
shared but the evolver uses a separate httpx client and does not touch any
server-side state files.

Usage:
    python scripts/trigger_profile_evolution.py
"""

import argparse
import asyncio
import contextlib
import os
import sys
import time

# Avoid CP1252 character mapping crashes on Windows console by forcing UTF-8 output
if hasattr(sys.stdout, 'reconfigure'):
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    with contextlib.suppress(Exception):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ---------------------------------------------------------------------------
# Path setup — mirror what the server does
# ---------------------------------------------------------------------------
ROOT_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for p in (ROOT_DIR, TOOLS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import memory_db
import task_manager
from profile_evolver import (
    DOCUMENT_CATEGORIES,
    DOCUMENT_EVOLUTION_ORDER,
    _draft_path,
    _evolve_document,
    _load_evolution_state,
    _save_evolution_state,
)

import evelyn_config as cfg

MIN_ENTRIES = getattr(cfg, "PROFILE_EVOLUTION_MIN_ENTRIES", 5)
DEFAULT_MAX_ENTRIES = getattr(cfg, "PROFILE_EVOLUTION_MAX_ENTRIES_PER_RUN", 30)


async def main() -> None:
    """Run profile evolution for all documents that have enough new entries."""
    parser = argparse.ArgumentParser(description="Manual trigger for Evelyn's profile evolution.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force evolution even if there is a pending profile update proposal.",
    )
    parser.add_argument(
        "--doc",
        "--document",
        dest="doc",
        type=str,
        default=None,
        help="Target a specific document filename (e.g. System_Directives.md).",
    )
    parser.add_argument(
        "--limit",
        dest="limit",
        type=int,
        default=DEFAULT_MAX_ENTRIES,
        help=f"Maximum entries to evaluate per document (default: {DEFAULT_MAX_ENTRIES}).",
    )
    args = parser.parse_args()

    # Check for pending profile updates
    pending_props = memory_db.get_pending_proposals("profile_update")
    pending_files = {p["suggested_category"] for p in pending_props}

    ordered_docs = [doc for doc in DOCUMENT_EVOLUTION_ORDER if doc in DOCUMENT_CATEGORIES]
    if args.doc:
        ordered_docs = [doc for doc in ordered_docs if args.doc.lower() in doc.lower()]
        if not ordered_docs:
            print(f"[TRIGGER] Unknown document: {args.doc}. Available: {ordered_docs}")
            return

    print(f"[TRIGGER] Starting manual profile evolution — {len(ordered_docs)} document(s) to check.\n")
    task_manager.set_running("profile_evolver", phase="Manual Profile Evolution")

    try:
        for filename in ordered_docs:
            categories = DOCUMENT_CATEGORIES.get(filename, [])
            if filename in pending_files and not args.force:
                print(f"[TRIGGER] {filename}: Has a pending profile update. Skipping (use --force to bypass).\n")
                continue

            state = _load_evolution_state()
            state["last_run_per_doc"].get(filename, 0.0)
            draft_exists = await asyncio.to_thread(os.path.exists, _draft_path(filename))

            # Collect entries qualifying for this specific document.
            # Mirrors the selection logic in profile_evolver.run_profile_evolution().
            changed_entries: list[dict] = []
            for cat in categories:
                entries = memory_db.get_entries_by_category_for_document(cat, document_name=filename, status="live")
                changed_entries.extend(entries)

            # Sort chronologically (oldest-first)
            changed_entries.sort(key=lambda e: (e.get("date") or "", e.get("id") or 0))

            if args.limit > 0 and len(changed_entries) > args.limit:
                print(f"[TRIGGER] {filename}: Backlog capped from {len(changed_entries)} to {args.limit} entries.")
                changed_entries = changed_entries[:args.limit]

            resume_note = " (draft on disk — will resume)" if draft_exists else ""
            print(f"[TRIGGER] {filename}: {len(changed_entries)} qualifying entries (need {MIN_ENTRIES}){resume_note}.")

            # Allow resume even if below min_entries — draft already did the heavy lifting
            if len(changed_entries) < MIN_ENTRIES and not draft_exists:
                print("[TRIGGER] Skipping — below minimum threshold.\n")
                continue

            print(f"[TRIGGER] Evolving {filename}...")
            now = time.time()
            success = await _evolve_document(filename, changed_entries, state)

            if success:
                # Reload fresh state in case another event resolved concurrently
                fresh_state = _load_evolution_state()
                fresh_state["last_run_per_doc"][filename] = now
                _save_evolution_state(fresh_state)
                task_manager.save_last_run_ts("profile_evolver", now)
                print(f"[TRIGGER] OK  Proposal created for {filename}.\n")
            else:
                print(f"[TRIGGER] FAIL  No proposal generated for {filename} (no changes detected or model error).\n")
    finally:
        task_manager.clear_running("profile_evolver", status="idle")
        task_manager.save_last_run_ts("profile_evolver")

    print("[TRIGGER] Done.")


if __name__ == "__main__":
    asyncio.run(main())
