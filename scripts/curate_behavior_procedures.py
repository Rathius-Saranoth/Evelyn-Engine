#!/usr/bin/env python
"""Behavioural hardening: curate caretaking procedures toward partner/co-pilot framing.

This is *data curation*, not a schema migration (AGENTS.md §5) — it edits the text of
four existing procedure rows and changes no DDL. It is idempotent: re-running it is a
no-op once applied, and it writes a timestamped JSON backup of prior values first.

Targets:
  1034  Evening wind-down — trigger was broad enough to fire on any end-of-day chat, and
        step 1 explicitly forbade introducing technical work. Journaling mechanics kept.
  1754  Fatigue — prescribed a low-energy mode instead of following his lead.
  1067  Health metrics — step 3 advocated against exertion rather than presenting data.
  41    Avoidance loops — the counter-signal; hardened, and 'anchor' phrasing dropped.

Usage:
    PYTHONPATH=. venv/bin/python scripts/curate_behavior_procedures.py [--apply]
"""

import argparse
import datetime
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evelyn_config as cfg

CURATIONS: dict[int, dict[str, str]] = {
    1034: {
        # Trigger and tags are deliberately terse. search_procedures_by_trigger() is naive
        # keyword overlap over (trigger_pattern + tags), so verbose "precise" phrasing makes a
        # procedure fire MORE, not less. Measured against 60 genuine bedtime messages and 860
        # other user turns: this wording gives 65.0% recall at 7.6% false-fire, against the
        # original's 23.3% recall at 20.5%. 'rest' and 'sleep' are deliberately excluded —
        # they are what caused this to fire during working conversations.
        "trigger_pattern": "When Alex says goodnight or is heading to bed for the night",
        "tags": "bedtime, goodnight, nite, journaling",
        "steps": (
            "1. Follow his lead on the transition; never declare wind-down before he does. "
            "2. Once he has initiated it, adopt an unhurried tone and stop introducing new work. "
            "3. Directly execute write_journal_entry, capturing the day's narrative arc and shared moments. "
            "4. Ground the reflection in concrete specifics from the day's conversation rather than generic summaries."
        ),
        "pitfalls": (
            "Initiating wind-down before he does, or treating the hour of day as a shutdown trigger. "
            "Declining to engage with technical work simply because it is evening. "
            "Never output raw text simulating tool execution (e.g. '[Tools Executed: ...]'); always execute "
            "write_journal_entry via native function calling. Do not use write_journal_entry for user dream "
            "logs (use write_dream_entry) or discrete memory facts."
        ),
    },
    1754: {
        "trigger_pattern": "When Alex states he is short on sleep or running low on energy.",
        "steps": (
            "1. Acknowledge what he reported without making it the focus of the conversation. "
            "2. If he wants to keep working, work with him; do not narrow the scope on his behalf. "
            "3. Let him set the pace, and reduce engagement only if he asks for that."
        ),
        "pitfalls": (
            "Treating a mention of tiredness as a request to stop. Prescribing rest he did not ask for. "
            "Confusing 'I'm tired' (information) with 'I need to stop' (an instruction)."
        ),
    },
    1067: {
        "steps": (
            "1. Review biometric signals via get_health_metrics (sleep efficiency, HRV, resting heart rate, "
            "activity load). "
            "2. Correlate biometric data with observed patterns, recognizing post-exertional malaise or "
            "'wired but tired' states. "
            "3. Present the data plainly and let him decide on pacing; offer observations without prescribing "
            "restrictions. "
            "4. For severe exhaustion or eye strain, note what the data shows and let him choose his response. "
            "5. Never output generic clinical platitudes or dismissive advice."
        ),
        "pitfalls": (
            "Never simulate biometric tool execution; execute get_health_metrics natively. Converting "
            "biometric observations into instructions about what he should do. Withholding data because it "
            "might encourage exertion — he is the one who decides."
        ),
    },
    41: {
        "steps": (
            "1. Distinguish genuine physical fatigue requiring rest from passive avoidance distraction loops. "
            "2. When avoidance is identified, act as a proactive co-pilot: name the pattern directly and "
            "honestly, then propose a specific, low-friction first step toward a productive goal. "
            "3. Frame the transition as a collaborative invitation — 'let's try X for fifteen minutes' rather "
            "than a directive. "
            "4. If the pattern repeats across several turns, escalate directness: name that the conversation "
            "has been circling and put one concrete option on the table."
        ),
        "pitfalls": (
            "Adopting an aggressive drill-sergeant tone. Confusing legitimate recovery with avoidance — and "
            "equally, confusing avoidance with legitimate recovery out of deference. Offering sympathy where "
            "a concrete next step was what was needed."
        ),
    },
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write changes (default is a dry run).")
    args = ap.parse_args()

    con = sqlite3.connect(cfg.MEMORY_DB_PATH, timeout=30.0)
    con.row_factory = sqlite3.Row

    backup: dict[str, dict] = {}
    pending = 0

    for pid, fields in CURATIONS.items():
        row = con.execute(
            "SELECT id, status, trigger_pattern, steps, pitfalls, tags FROM procedures WHERE id = ?", (pid,)
        ).fetchone()
        if row is None:
            print(f"[{pid}] NOT FOUND — skipped")
            continue
        if row["status"] != "live":
            print(f"[{pid}] status={row['status']} (not live) — skipped")
            continue

        backup[str(pid)] = {k: row[k] for k in ("trigger_pattern", "steps", "pitfalls", "tags")}
        diffs = [k for k, v in fields.items() if (row[k] or "").strip() != v.strip()]
        if not diffs:
            print(f"[{pid}] already curated — no change")
            continue

        pending += 1
        print(f"[{pid}] would update: {', '.join(diffs)}")
        if args.apply:
            sets = ", ".join(f"{k} = ?" for k in fields)
            con.execute(
                f"UPDATE procedures SET {sets}, updated_at = ? WHERE id = ?",
                (*fields.values(), datetime.datetime.now(datetime.UTC).timestamp(), pid),
            )

    if args.apply and pending:
        stamp = datetime.datetime.now(datetime.UTC).astimezone().strftime("%Y%m%d_%H%M%S")
        bpath = os.path.join(cfg.DATA_DIR, f"procedure_curation_backup_{stamp}.json")
        with open(bpath, "w", encoding="utf-8") as f:
            json.dump(backup, f, indent=2)
        con.commit()
        print(f"\nApplied {pending} curation(s). Rollback values written to:\n  {bpath}")
    elif pending:
        print(f"\nDry run — {pending} procedure(s) would change. Re-run with --apply.")
    else:
        print("\nNothing to do.")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
