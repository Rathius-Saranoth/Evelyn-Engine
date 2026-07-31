# remediate_profile_evolution_backlog.py
# date created: 2026-07-30
"""
remediate_profile_evolution_backlog.py — Clear evolution backlog and initialize bookkeeping schema.

1. Calls memory_db.init_db() to run schema migrations.
2. Rejects all pending 'profile_update' proposals to establish a clean starting baseline.
3. Sets last_evolved_at = created_at for existing live context_entries to prevent historical churn.
4. Initializes first_observed, last_observed, and observed_count for existing context entries.
5. Clears stale evolution draft files and resets draft cursors in evelyn_evolution_state.json.
"""

import glob
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Evelyn", "tools"))

import evelyn_config as cfg
import memory_db

def remediate_backlog():
    print("[REMEDIATION] Initializing memory_db schema...", flush=True)
    memory_db.init_db()

    con = memory_db.get_db()

    # 1. Reject pending profile_update proposals
    cur = con.execute("UPDATE proposals SET status = 'rejected' WHERE type = 'profile_update' AND status = 'pending'")
    rejected_count = cur.rowcount
    print(f"[REMEDIATION] Rejected {rejected_count} pending profile_update proposal(s).", flush=True)

    # 2. Set last_evolved_at = created_at for live context entries where last_evolved_at is NULL
    cur = con.execute(
        "UPDATE context_entries SET last_evolved_at = created_at WHERE last_evolved_at IS NULL AND status = 'live'"
    )
    evolved_set_count = cur.rowcount
    print(f"[REMEDIATION] Set baseline last_evolved_at on {evolved_set_count} live context entry(ies).", flush=True)

    # 3. Populate first_observed, last_observed, and observed_count for legacy entries
    cur = con.execute(
        """
        UPDATE context_entries
        SET first_observed = COALESCE(first_observed, created_at),
            last_observed  = COALESCE(last_observed, created_at),
            observed_count = COALESCE(observed_count, 1)
        WHERE first_observed IS NULL OR last_observed IS NULL OR observed_count IS NULL
        """
    )
    bookkeeping_count = cur.rowcount
    print(f"[REMEDIATION] Populated observation bookkeeping fields on {bookkeeping_count} entry(ies).", flush=True)

    con.commit()
    con.close()

    # 4. Clean evolution draft files and state
    data_dir = os.path.dirname(os.path.abspath(cfg.CHAT_DB_PATH))
    draft_files = glob.glob(os.path.join(data_dir, "evelyn_evolution_draft_*.md"))
    for df in draft_files:
        try:
            os.remove(df)
            print(f"[REMEDIATION] Removed stale draft file: {os.path.basename(df)}", flush=True)
        except Exception as e:
            print(f"[REMEDIATION] Warning: could not remove draft {df}: {e}", flush=True)

    state_file = os.path.join(data_dir, "evelyn_evolution_state.json")
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state_data = json.load(f)
            if "draft_cursor_per_doc" in state_data:
                for k in state_data["draft_cursor_per_doc"]:
                    state_data["draft_cursor_per_doc"][k] = 0.0
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)
            print(f"[REMEDIATION] Reset draft cursors in {os.path.basename(state_file)}.", flush=True)
        except Exception as e:
            print(f"[REMEDIATION] Warning: could not reset state file {state_file}: {e}", flush=True)

    print("[REMEDIATION] Remediation complete!", flush=True)

if __name__ == "__main__":
    remediate_backlog()
