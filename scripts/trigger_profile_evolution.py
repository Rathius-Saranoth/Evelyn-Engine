# trigger_profile_evolution.py
# date created: 2026-06-29
# date modified: 2026-07-03 10:26:36
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
from profile_evolver import (
    DOCUMENT_CATEGORIES,
    _draft_path,
    _evolve_document,
    _load_evolution_state,
    _save_evolution_state,
)

import evelyn_config as cfg

MIN_ENTRIES = getattr(cfg, "PROFILE_EVOLUTION_MIN_ENTRIES", 5)


async def main() -> None:
    """Run profile evolution for all documents that have enough new entries."""
    parser = argparse.ArgumentParser(description="Manual trigger for Evelyn's profile evolution.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force evolution even if there is a pending profile update proposal."
    )
    args = parser.parse_args()

    state = _load_evolution_state()
    now   = time.time()

    # Check for pending profile updates
    pending_props = memory_db.get_pending_proposals("profile_update")
    pending_files = {p["suggested_category"] for p in pending_props}

    print(f"[TRIGGER] Starting manual profile evolution — {len(DOCUMENT_CATEGORIES)} document(s) to check.\n")

    for filename, categories in DOCUMENT_CATEGORIES.items():
        if filename in pending_files and not args.force:
            print(f"[TRIGGER] {filename}: Has a pending profile update. Skipping (use --force to bypass).\n")
            continue

        state["last_run_per_doc"].get(filename, 0.0)
        draft_exists = await asyncio.to_thread(os.path.exists, _draft_path(filename))

        # Collect entries not yet evolved OR whose observation has changed since last evolution.
        # Mirrors the selection logic in profile_evolver.run_profile_evolution().
        changed_entries: list[dict] = []
        for cat in categories:
            entries = memory_db.get_entries_by_category(cat, status="live")
            for entry in entries:
                updated_at      = entry.get("updated_at", 0.0) or 0.0
                last_evolved_at = entry.get("last_evolved_at")
                if last_evolved_at is None or updated_at > last_evolved_at:
                    changed_entries.append(entry)

        resume_note = " (draft on disk — will resume)" if draft_exists else ""
        print(f"[TRIGGER] {filename}: {len(changed_entries)} qualifying entries (need {MIN_ENTRIES}){resume_note}.")

        # Allow resume even if below min_entries — draft already did the heavy lifting
        if len(changed_entries) < MIN_ENTRIES and not draft_exists:
            print("[TRIGGER] Skipping — below minimum threshold.\n")
            continue

        print(f"[TRIGGER] Evolving {filename}...")
        success = await _evolve_document(filename, changed_entries, state)

        if success:
            state["last_run_per_doc"][filename] = now
            _save_evolution_state(state)
            print(f"[TRIGGER] OK  Proposal created for {filename}.\n")
        else:
            print(f"[TRIGGER] FAIL  No proposal generated for {filename} (no changes detected or model error).\n")

    print("[TRIGGER] Done.")


if __name__ == "__main__":
    asyncio.run(main())
