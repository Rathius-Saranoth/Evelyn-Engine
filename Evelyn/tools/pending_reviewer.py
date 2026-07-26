# pending_reviewer.py
# date created: 2026-05-07 07:18:08
# date modified: 2026-05-25 19:50:51
# tags: #triage, #consolidation, #review, #terminal, #interactive

"""
pending_reviewer.py — Interactive reviewer for Evelyn's consolidation/recategorization proposals.

Handles two proposal types from SQLite `proposals` table:
  merge/supersede  — merge N source CEs into one new CE
  recategorize     — change category of a CE

Run from the project root:
    python Evelyn\tools\pending_reviewer.py
"""

import os
import sys
import time

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import evelyn_config as cfg
import Evelyn.tools.memory_db as memory_db

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
        import tty, termios
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
        print(f"    {DIM}[{cat}]{RESET} {obs}")
    print()

    if prop["type"] in ("merge", "supersede", "procedure_merge"):
        print(f"  {BOLD}Proposed Action: {p_type}{RESET}")
        print(f"  {DIM}Target Category:{RESET} {prop.get('suggested_category')}")
        print(f"  {DIM}Merged Summary / Procedure:{RESET}")
        print(f"  {GREEN}> {prop.get('merged_observation')}{RESET}\n")
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
                p_item = memory_db.get_procedure(eid)
                if p_item:
                    source_entries.append({"category": "procedures", "observation": f"[{p_item['trigger_pattern']}] {p_item['steps'][:100]}..."})
            else:
                entry = memory_db.get_entry(eid)
                if entry:
                    source_entries.append(entry)

        _display_proposal(prop, source_entries, idx, len(proposals))

        print(f"{DIM}{_DIV}{RESET}")
        print(
            f"  {GREEN}[A]{RESET} Approve   "
            f"{YELLOW}[D]{RESET} Deny / skip   "
            f"{DIM}[Q]{RESET} Quit"
        )
        print(f"{DIM}{_DIV}{RESET}")
        sys.stdout.flush()

        ch = _getch()

        if ch == "q":
            print(f"\n  {DIM}Quitting — {len(proposals) - idx} proposal(s) remaining.{RESET}")
            break

        if ch == "a":
            try:
                if prop["type"] == "recategorize":
                    # Apply recategorization
                    for entry in source_entries:
                        memory_db.update_entry(entry["id"], category=prop["suggested_category"])
                    memory_db.apply_proposal(prop["id"])
                    
                elif prop["type"] == "procedure_merge":
                    # Apply procedure_merge
                    import yaml
                    # Archive source procedures
                    for eid in source_ids:
                        memory_db.delete_procedure(eid)
                    # Insert merged procedure
                    try:
                        parsed_proc = yaml.safe_load(prop["merged_observation"])
                    except Exception:
                        parsed_proc = {}
                    if isinstance(parsed_proc, dict) and "trigger_pattern" in parsed_proc:
                        memory_db.insert_procedure(
                            trigger_pattern=parsed_proc["trigger_pattern"],
                            steps=parsed_proc.get("steps", ""),
                            pitfalls=parsed_proc.get("pitfalls"),
                            verification=parsed_proc.get("verification"),
                            source="consolidated",
                            status="live",
                            tags=parsed_proc.get("tags")
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
            except Exception as e:
                print(f"\n  {RED}✗ Error applying proposal: {e}{RESET}")
                errors += 1
            time.sleep(0.5)
            idx += 1

        elif ch == "d":
            try:
                memory_db.reject_proposal(prop["id"])
                denied += 1
                print(f"\n  {YELLOW}→ Denied/Skipped.{RESET}")
            except Exception as e:
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
            refresh_script = os.path.join(r"C:\Projects\LocalAI", "Evelyn", "tools", "refresh_memory.py")
            if os.path.exists(refresh_script):
                print(f"  {CYAN}Triggering background memory refresh for {approved} approved proposal(s)...{RESET}\n")
                subprocess.Popen([sys.executable, "-u", refresh_script], cwd=r"C:\Projects\LocalAI")
        except Exception as r_err:
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
