# context_reviewer.py
# date created: 2026-05-04 17:28:42
# date modified: 2026-06-07 10:28:24
# tags: #context, #reviewer, #queue, #ui, #interface

"""
context_reviewer.py — Interactive terminal reviewer for Evelyn's auto-extracted context facts.

Phase 1 (implemented): Review extracted facts from the SQLite database.
  [A] Approve  — promotes to 'live' status
  [E] Edit     — (not supported in terminal DB view yet, but you can approve and edit DB directly)
  [D] Deny     — skip; fact stays in 'extracted' status
  [X] Delete   — permanently removes the fact from the database
  [Q] Quit     — exits; remaining facts are untouched

Run from the project root:
    python Evelyn\tools\context_reviewer.py

Google-style docstrings throughout for AI tool inspection.
"""

import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap — allows standalone execution from any working directory
# ---------------------------------------------------------------------------
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


def _clr() -> None:
    """Clear the terminal screen (cross-platform).

    Returns:
        None
    """
    os.system("cls" if os.name == "nt" else "clear")


def _getch() -> str:
    """Read a single keypress without requiring Enter.

    Returns:
        str: The lowercase decoded character of the pressed key.
    """
    try:
        import msvcrt  # Windows
        ch = msvcrt.getch()
        return (ch.decode("utf-8", errors="replace") if isinstance(ch, bytes) else ch).lower()
    except ImportError:
        import tty, termios  # Unix
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1).lower()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

_BAR = "═" * 68
_DIV = "─" * 68


def _display_entry(entry: dict, idx: int, total: int) -> None:
    """Render the current entry review screen.

    Args:
        entry: The context entry dictionary to display.
        idx: The 0-based index of the entry.
        total: The total count of entries.
    """
    _clr()
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"{BOLD}{CYAN}  Evelyn — Context Entry Reviewer  {DIM}Phase 1: Extracted Facts{RESET}")
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"  Entry {BOLD}{idx + 1}{RESET} of {BOLD}{total}{RESET}  |  ID: {entry['id']}")
    print(f"  {DIM}Category:{RESET} {CYAN}{entry['category']}{RESET}  {DIM}Date:{RESET} {entry['date']}")
    print(f"{DIM}{_DIV}{RESET}\n")

    print(f"  {entry['observation']}\n")

    print(f"{DIM}{_DIV}{RESET}")
    print(
        f"  {GREEN}[A]{RESET} Approve   "
        f"{YELLOW}[D]{RESET} Deny / skip   "
        f"{RED}[X]{RESET} Delete   "
        f"{DIM}[Q]{RESET} Quit"
    )
    print(f"{DIM}{_DIV}{RESET}")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Phase 1 — Extracted fact review
# ---------------------------------------------------------------------------


def _collect_extracted() -> list[dict]:
    """Retrieve the list of extracted entries from SQLite.

    Returns:
        list[dict]: A list of message entries with 'extracted' status.
    """
    return memory_db.get_all_entries(statuses=["extracted"])


def run_phase1() -> None:
    """Execute the interactive review loop for extracted entries in SQLite.

    Returns:
        None
    """
    entries = _collect_extracted()
    if not entries:
        print(f"\n  {YELLOW}No extracted entries found in DB — nothing to review.{RESET}\n")
        return

    approved = denied = deleted = errors = 0
    idx = 0

    while idx < len(entries):
        entry = entries[idx]

        _display_entry(entry, idx, len(entries))
        ch = _getch()

        if ch == "q":
            print(f"\n  {DIM}Quitting — {len(entries) - idx} entry/ies remaining.{RESET}")
            break

        if ch == "a":
            try:
                memory_db.update_entry(entry["id"], status="live")
                approved += 1
                print(f"\n  {GREEN}✓ Approved{RESET}")
            except Exception as e:
                print(f"\n  {RED}✗ Error: {e}{RESET}")
                errors += 1
            time.sleep(0.5)
            idx += 1

        elif ch == "d":
            denied += 1
            print(f"\n  {YELLOW}→ Skipped.{RESET}")
            time.sleep(0.4)
            idx += 1

        elif ch == "x":
            try:
                memory_db.delete_entry(entry["id"])
                deleted += 1
                print(f"\n  {RED}✗ Deleted.{RESET}")
            except Exception as e:
                print(f"\n  {RED}✗ Error deleting: {e}{RESET}")
                errors += 1
            time.sleep(0.4)
            idx += 1

        # Any other key: redisplay without advancing

    _clr()
    remaining = len(_collect_extracted())
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"{BOLD}{CYAN}  Review Complete{RESET}")
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"  {GREEN}Approved : {approved}{RESET}")
    print(f"  {YELLOW}Denied   : {denied}{RESET}")
    print(f"  {RED}Deleted  : {deleted}{RESET}")
    if errors:
        print(f"  {RED}Errors   : {errors}{RESET}")
    print(f"  Remaining extracted : {remaining}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the main entry point for the reviewer CLI.

    Returns:
        None
    """
    _clr()
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"{BOLD}{CYAN}  Evelyn — Context Entry Reviewer{RESET}")
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print()

    entries = _collect_extracted()
    print(f"  Found {BOLD}{len(entries)}{RESET} extracted entry/ies to review.")
    print()

    if not entries:
        return

    print(f"  Press {BOLD}Enter{RESET} to start Phase 1 review, or {DIM}Ctrl+C{RESET} to cancel.\n")
    try:
        input()
    except KeyboardInterrupt:
        return

    try:
        run_phase1()
    except KeyboardInterrupt:
        print(f"\n\n  {DIM}Interrupted.{RESET}\n")


if __name__ == "__main__":
    main()
