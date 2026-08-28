# pending_reviewer.py
# date created: 2026-05-07 07:18:08
# date modified: 2026-08-28 07:33:33
# tags: #triage, #consolidation, #review, #terminal, #interactive

"""
pending_reviewer.py — Interactive reviewer for Evelyn's consolidation/recategorization proposals.

Handles two proposal types from SQLite `proposals` table:
  merge/supersede  — merge N source CEs into one new CE
  recategorize     — change category of a CE

Run from the project root:
    python Evelyn\tools\\pending_reviewer.py
"""

import os
import sqlite3
import sys
import time
from pathlib import Path

import yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import evelyn_config as cfg
from Evelyn.tools import memory_db

# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
MAGENTA = "\033[95m"

_BAR = "═" * 68
_DIV = "─" * 68


def _clr():
    """Clear the terminal screen (cross-platform)."""
    os.system("cls" if os.name == "nt" else "clear")


def _getch():
    """Read a single keypress without requiring Enter (cross-platform)."""
    try:
        import msvcrt
        ch = msvcrt.getch()
        return (ch.decode("utf-8", errors="replace") if isinstance(ch, bytes) else ch).lower()
    except ImportError:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1).lower()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _display_proposal(prop: dict, source_entries: list[dict], idx: int, total: int):
    """Render a proposal and its source entries.

    Args:
        prop:           Proposal dictionary from memory_db.
        source_entries: List of context_entry dictionaries.
        idx:            Current proposal index (0-based).
        total:          Total proposals to review.
    """
    _clr()
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"{BOLD}{CYAN}  Evelyn — Pending Proposal Reviewer{RESET}")
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")

    p_type = prop["type"].upper()
    print(f"  Proposal {BOLD}{idx + 1}{RESET} of {BOLD}{total}{RESET}  |  Type: {MAGENTA}{p_type}{RESET}")
    print(f"  {DIM}Topic:{RESET} {CYAN}{prop.get('topic', 'N/A')}{RESET}")
    print(f"{DIM}{_DIV}{RESET}\n")

    print(f"  {BOLD}Reasoning:{RESET}")
    print(f"  {DIM}{prop.get('reason', 'N/A')}{RESET}\n")

    print(f"  {BOLD}Source Entries:{RESET}")
    for entry in source_entries:
        cat = entry.get("category", "")
        obs = entry.get("observation", "")
        tags_str = f"  {DIM}(Tags: #{entry['tags']}){RESET}" if entry.get("tags") else ""
        print(f"    {DIM}[{cat}]{RESET} {obs}{tags_str}")
    if prop["type"] in ("merge", "supersede", "procedure_merge"):
        print(f"  {BOLD}Proposed Action: {p_type}{RESET}")
        print(f"  {DIM}Target Category:{RESET} {prop.get('suggested_category')}")
        if prop.get("merged_tags"):
            print(f"  {DIM}Merged Domain Tags:{RESET} {CYAN}#{prop.get('merged_tags')}{RESET}")
        print(f"  {DIM}Merged Summary / Procedure:{RESET}")
        print(f"  {GREEN}> {prop.get('merged_observation')}{RESET}\n")
    elif prop["type"] == "split":
        print(f"  {BOLD}Proposed Action: SPLIT COMPOUND ENTRY{RESET}")
        print(f"  {DIM}Decomposed Atomic Facts to Create:{RESET}")
        try:
            p_data = yaml.safe_load(prop.get("merged_observation", ""))
            child_list = p_data.get("entries", []) if isinstance(p_data, dict) else (p_data if isinstance(p_data, list) else [])
        except (yaml.YAMLError, ValueError, TypeError):
            child_list = []
        for i, ce in enumerate(child_list, 1):
            c_cat = ce.get("category", "")
            c_tags = f"  {CYAN}(Tags: #{ce['tags']}){RESET}" if ce.get("tags") else ""
            c_obs = ce.get("observation", "")
            print(f"    {GREEN}{i}. [{c_cat}]{RESET} {c_obs}{c_tags}")
        print()
    elif prop["type"] == "recategorize":
        print(f"  {BOLD}Proposed Action: RECATEGORIZE{RESET}")
        print(f"  {DIM}Suggested Category:{RESET} {GREEN}{prop.get('suggested_category')}{RESET}\n")



# ---------------------------------------------------------------------------
# Review Loop
# ---------------------------------------------------------------------------


def run_review():
    """Main interactive loop for reviewing pending proposals."""
    proposals = memory_db.get_pending_proposals()
    if not proposals:
        print(f"\n  {YELLOW}No pending proposals found in DB — nothing to review.{RESET}\n")
        return

    approved = denied = errors = 0
    idx = 0

    while idx < len(proposals):
        prop = proposals[idx]
        source_ids = prop.get("source_ids", [])
        source_entries = []
        for eid in source_ids:
            if prop["type"] == "procedure_merge":
                proc = memory_db.get_procedure(eid)
                if proc:
                    source_entries.append({
                        "category": "procedure",
                        "observation": f"[{proc['trigger_pattern']}] {proc['steps'][:120]}...",
                        "tags": proc.get("tags")
                    })
            else:
                entry = memory_db.get_entry(eid)
                if entry:
                    source_entries.append(entry)

        _display_proposal(prop, source_entries, idx, len(proposals))

        print(f"  [{GREEN}a{RESET}]pprove  [{RED}d{RESET}]eny  [{YELLOW}s{RESET}]kip  [{DIM}q{RESET}]uit")
        try:
            ch = input("  > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print(f"\n\n  {DIM}Session interrupted. Exiting.{RESET}\n")
            break

        if ch == "q":
            print(f"\n  {DIM}Quitting — {len(proposals) - idx} proposal(s) remaining.{RESET}")
            break

        if ch == "a":
            try:
                if prop["type"] == "split":
                    try:
                        p_data = yaml.safe_load(prop.get("merged_observation", ""))
                        child_list = p_data.get("entries", []) if isinstance(p_data, dict) else (p_data if isinstance(p_data, list) else [])
                    except (yaml.YAMLError, ValueError, TypeError):
                        child_list = []
                    if source_ids and child_list:
                        memory_db.split_entry(source_ids[0], child_list)
                    memory_db.apply_proposal(prop["id"])
                elif prop["type"] == "recategorize":
                    # Apply recategorization
                    for entry in source_entries:
                        memory_db.update_entry(entry["id"], category=prop["suggested_category"])
                    memory_db.apply_proposal(prop["id"])

                elif prop["type"] == "procedure_merge":
                    # Apply procedure_merge
                    import yaml
                    source_tags_set = set()
                    for eid in source_ids:
                        p_old = memory_db.get_procedure(eid)
                        if p_old and p_old.get("tags"):
                            for t in str(p_old["tags"]).split(","):
                                cleaned_t = t.strip()
                                if cleaned_t and cleaned_t.lower() not in ("procedure", "merged", "merge", "consolidated", "none"):
                                    source_tags_set.add(cleaned_t)
                        memory_db.delete_procedure(eid)
                    # Insert merged procedure
                    try:
                        parsed_proc = yaml.safe_load(prop["merged_observation"])
                    except (yaml.YAMLError, ValueError, TypeError):
                        parsed_proc = {}
                    if isinstance(parsed_proc, dict) and "trigger_pattern" in parsed_proc:
                        proc_tags = parsed_proc.get("tags")
                        if isinstance(proc_tags, list):
                            proc_tags_str = ", ".join([str(t).strip() for t in proc_tags if str(t).strip()])
                        else:
                            proc_tags_str = str(proc_tags).strip() if proc_tags is not None else ""

                        parsed_tags_set = {t.strip().lower() for t in proc_tags_str.split(",") if t.strip()}
                        if not proc_tags_str or parsed_tags_set.issubset({"procedure", "merged", "merge", "consolidated", "none"}):
                            final_tags = ", ".join(sorted(source_tags_set)) if source_tags_set else (proc_tags_str or "procedure")
                        else:
                            combined = {t.strip() for t in proc_tags_str.split(",") if t.strip()}
                            combined.update(source_tags_set)
                            if len(combined) > 1:
                                combined = {t for t in combined if t.lower() not in ("procedure", "merged", "merge", "consolidated", "none")}
                            final_tags = ", ".join(sorted(combined)) if combined else "procedure"

                        memory_db.insert_procedure(
                            trigger_pattern=parsed_proc["trigger_pattern"],
                            steps=parsed_proc.get("steps", ""),
                            pitfalls=parsed_proc.get("pitfalls"),
                            verification=parsed_proc.get("verification"),
                            source="consolidated",
                            status="live",
                            tags=final_tags,
                            suggested_tools=parsed_proc.get("suggested_tools"),
                        )
                    memory_db.apply_proposal(prop["id"])

                elif prop["type"] == "procedure_split":
                    # Apply procedure_split
                    import yaml
                    for eid in source_ids:
                        memory_db.delete_procedure(eid)
                    try:
                        parsed_data = yaml.safe_load(prop["merged_observation"])
                        child_procs = parsed_data.get("procedures", []) if isinstance(parsed_data, dict) else (parsed_data if isinstance(parsed_data, list) else [])
                    except (yaml.YAMLError, ValueError, TypeError):
                        child_procs = []
                    for cp in child_procs:
                        if isinstance(cp, dict) and "trigger_pattern" in cp:
                            memory_db.insert_procedure(
                                trigger_pattern=cp["trigger_pattern"],
                                steps=cp.get("steps", ""),
                                pitfalls=cp.get("pitfalls"),
                                verification=cp.get("verification"),
                                source="split",
                                status="live",
                                tags=cp.get("tags"),
                                suggested_tools=cp.get("suggested_tools"),
                            )
                    memory_db.apply_proposal(prop["id"])

                elif prop["type"] in ("merge", "supersede"):
                    # Apply merge/supersede
                    # Delete source entries
                    for entry in source_entries:
                        memory_db.delete_entry(entry["id"])
                    # Insert merged entry
                    # Use subject from the first source entry, default to 'R' if unknown
                    subject = source_entries[0]["subject"] if source_entries else "R"
                    date = source_entries[0]["date"] if source_entries else None

                    # Use LLM-generated merged tags if available, else fallback to union
                    if prop.get("merged_tags"):
                        merged_tags = prop["merged_tags"]
                    else:
                        merged_tags_set = set()
                        for entry in source_entries:
                            if entry.get("tags"):
                                for t in entry["tags"].split(","):
                                    if t.strip():
                                        merged_tags_set.add(t.strip())
                        merged_tags = ", ".join(sorted(merged_tags_set)) if merged_tags_set else None

                    memory_db.insert_entry(
                        category=prop["suggested_category"],
                        subject=subject,
                        observation=prop["merged_observation"],
                        source="consolidated",
                        date=date,
                        tags=merged_tags
                    )
                    memory_db.apply_proposal(prop["id"])

                approved += 1
                print(f"\n  {GREEN}✓ Approved{RESET}")
            except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
                print(f"\n  {RED}✗ Error applying proposal: {e}{RESET}")
                errors += 1
            time.sleep(0.5)
            idx += 1

        elif ch == "d":
            try:
                memory_db.reject_proposal(prop["id"])
                denied += 1
                print(f"\n  {YELLOW}→ Denied/Skipped.{RESET}")
            except (sqlite3.Error, OSError, ValueError) as e:
                print(f"\n  {RED}✗ Error rejecting proposal: {e}{RESET}")
                errors += 1
            time.sleep(0.4)
            idx += 1

        # Any other key: redisplay without advancing

    _clr()
    remaining = len(memory_db.get_pending_proposals())
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"{BOLD}{CYAN}  Review Complete{RESET}")
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"  {GREEN}Approved : {approved}{RESET}")
    print(f"  {YELLOW}Denied   : {denied}{RESET}")
    if errors:
        print(f"  {RED}Errors   : {errors}{RESET}")
    print(f"  Remaining pending : {remaining}")
    print()

    if approved > 0:
        try:
            import subprocess
            base_dir = getattr(cfg, "BASE_DIR", r"/home/rathius/evelyn")
            refresh_script = os.path.join(base_dir, "Evelyn", "tools", "refresh_memory.py")
            if os.path.exists(refresh_script):
                print(f"  {CYAN}Triggering background memory refresh for {approved} approved proposal(s)...{RESET}\n")
                subprocess.Popen([sys.executable, "-u", refresh_script], cwd=base_dir)
        except (OSError, subprocess.SubprocessError) as r_err:
            print(f"  {RED}Warning: Could not trigger memory refresh: {r_err}{RESET}\n")



def main():
    """Print the count of pending proposals and prompt the user to start the interactive review loop."""
    _clr()
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"{BOLD}{CYAN}  Evelyn — Pending Proposal Reviewer{RESET}")
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print()

    proposals = memory_db.get_pending_proposals()
    print(f"  Found {BOLD}{len(proposals)}{RESET} proposal(s) to review.")
    print()

    if not proposals:
        return

    print(f"  Press {BOLD}Enter{RESET} to start review, or {DIM}Ctrl+C{RESET} to cancel.\n")
    try:
        input()
    except KeyboardInterrupt:
        return

    try:
        run_review()
    except KeyboardInterrupt:
        print(f"\n\n  {DIM}Interrupted.{RESET}\n")


if __name__ == "__main__":
    main()
