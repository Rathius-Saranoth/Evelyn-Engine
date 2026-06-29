# trigger_profile_evolution.py
# date created: 2026-06-29
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

import asyncio
import json
import os
import sys
import time

# Avoid CP1252 character mapping crashes on Windows console
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(errors='replace')
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Path setup — mirror what the server does
# ---------------------------------------------------------------------------
ROOT_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for p in (ROOT_DIR, TOOLS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import evelyn_config as cfg
import memory_db
from profile_evolver import (
    _evolve_document,
    _load_evolution_state,
    _save_evolution_state,
    _draft_path,
)

# ---------------------------------------------------------------------------
# Same category map as profile_evolver.py
# ---------------------------------------------------------------------------
DOCUMENT_CATEGORIES = {
    "Evelyn_Narrative_Persona.md": [
        "Cat01-E", "Cat02-E", "Cat03-E", "Cat04-E", "Cat10-E",
    ],
    "Ricky_Narrative_Profile.md": [
        "Cat01-R", "Cat03-R", "Cat04-R", "Cat06-R", "Cat09-R", "Cat12-R",
    ],
    "System_Directives.md": [
        "Cat14-E", "Cat16-E", "Cat16-R",
    ],
}

MIN_ENTRIES = getattr(cfg, "PROFILE_EVOLUTION_MIN_ENTRIES", 5)


async def main() -> None:
    """Run profile evolution for all documents that have enough new entries."""
    state = _load_evolution_state()
    now   = time.time()

    print(f"[TRIGGER] Starting manual profile evolution — {len(DOCUMENT_CATEGORIES)} document(s) to check.\n")

    for filename, categories in DOCUMENT_CATEGORIES.items():
        last_run     = state["last_run_per_doc"].get(filename, 0.0)
        draft_exists = os.path.exists(_draft_path(filename))

        # Collect entries changed since the last completed evolution run
        changed_entries: list[dict] = []
        for cat in categories:
            entries = memory_db.get_entries_by_category(cat, status="live")
            for entry in entries:
                created_at   = entry.get("created_at", 0.0) or 0.0
                updated_at   = entry.get("updated_at", 0.0)  or 0.0
                last_touched = max(created_at, updated_at)
                if last_touched > last_run:
                    changed_entries.append(entry)

        resume_note = " (draft on disk — will resume)" if draft_exists else ""
        print(f"[TRIGGER] {filename}: {len(changed_entries)} qualifying entries (need {MIN_ENTRIES}){resume_note}.")

        # Allow resume even if below min_entries — draft already did the heavy lifting
        if len(changed_entries) < MIN_ENTRIES and not draft_exists:
            print(f"[TRIGGER] Skipping — below minimum threshold.\n")
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
