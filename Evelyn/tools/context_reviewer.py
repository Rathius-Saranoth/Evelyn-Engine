"""
context_reviewer.py — Interactive terminal reviewer for Evelyn's auto-extracted context facts.

Phase 1 (implemented): Review EX_*.md files from the Extracted/ staging folder.
  [A] Approve  — strips extraction metadata, renames EX_ → CE_, moves to live category folder
  [D] Deny     — skip; file stays in Extracted/ for modification and re-review
  [X] Delete   — permanently removes the file
  [Q] Quit     — exits; remaining files are untouched

Phase 2 (future): Review CONSOLIDATION_*.md proposal files from Pending/.

Run from the project root:
    python Evelyn\\tools\\context_reviewer.py

Google-style docstrings throughout for AI tool inspection.
"""

import os
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Path bootstrap — allows standalone execution from any working directory
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import evelyn_config as cfg  # noqa: E402  (after path fix)

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
    """Clear the terminal screen (cross-platform)."""
    os.system("cls" if os.name == "nt" else "clear")


def _getch() -> str:
    """Read a single keypress without requiring Enter (cross-platform).

    Returns:
        Lowercase single character.
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
# Regex patterns
# ---------------------------------------------------------------------------

_CAT_CODE_RE    = re.compile(r"(Cat\d{2}-[ER])", re.IGNORECASE)
_DATE_RE        = re.compile(r"(\d{4}-\d{2}-\d{2})")
_TAGS_LINE_RE   = re.compile(r"^(tags:\s*\[)(.*?)(\])$", re.MULTILINE)
_NOTE_RE        = re.compile(r"^> \[!NOTE\].*$", re.MULTILINE)
_HEADING_EX_RE  = re.compile(r"^(# )(EX_)", re.MULTILINE)


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def _parse_category(filename: str) -> str | None:
    """Extract the Cat##-R/E category code from a filename.

    Args:
        filename: Basename like ``EX_2024-04-25_Cat05-R.md``.

    Returns:
        Category string (e.g. ``"Cat05-R"``) or None.
    """
    m = _CAT_CODE_RE.search(filename)
    return m.group(1) if m else None


def _parse_date(filename: str) -> str | None:
    """Extract the YYYY-MM-DD date from a filename.

    Args:
        filename: Basename like ``EX_2024-04-25_Cat05-R.md``.

    Returns:
        Date string or None.
    """
    m = _DATE_RE.search(filename)
    return m.group(1) if m else None


def _make_ce_filename(date: str, category: str, existing: set[str]) -> str:
    """Return a unique CE_ filename that does not collide with existing files.

    Args:
        date:     ``YYYY-MM-DD`` string.
        category: Category code like ``Cat05-R``.
        existing: Set of filenames already in the target directory.

    Returns:
        Unique filename like ``CE_2024-04-25_Cat05-R.md``.
    """
    base = f"CE_{date}_{category}.md"
    if base not in existing:
        return base
    i = 1
    while True:
        candidate = f"CE_{date}_{category} ({i}).md"
        if candidate not in existing:
            return candidate
        i += 1


def _clean_content(content: str, old_name: str, new_name: str) -> str:
    """Strip extraction-specific metadata from file content on approval.

    Changes made:
    - Removes ``extracted`` from the ``tags:`` frontmatter line.
    - Updates the ``# EX_`` heading to ``# CE_``.
    - Removes the ``> [!NOTE] Auto-extracted...`` footer line.

    Args:
        content:  Raw file content.
        old_name: Source filename (EX_*.md).
        new_name: Target filename (CE_*.md).

    Returns:
        Cleaned content string ready to write.
    """
    # 1. Remove 'extracted' from tags list
    def _fix_tags(m: re.Match) -> str:
        prefix, tags_str, suffix = m.group(1), m.group(2), m.group(3)
        tags = [t.strip() for t in tags_str.split(",") if t.strip().lower() != "extracted"]
        return prefix + ", ".join(tags) + suffix

    content = _TAGS_LINE_RE.sub(_fix_tags, content)

    # 2. Rename heading EX_ → CE_
    content = _HEADING_EX_RE.sub(r"\1CE_", content)

    # 3. Remove auto-extracted footer note
    content = _NOTE_RE.sub("", content).rstrip() + "\n"

    return content


def _compute_target(filename: str) -> tuple[Path, str] | None:
    """Compute the target directory and CE_ filename for an approved EX_ file.

    Args:
        filename: Basename of the EX_ file.

    Returns:
        Tuple of (target_dir Path, ce_filename str) or None if unparseable.
    """
    category = _parse_category(filename)
    date     = _parse_date(filename)
    if not category or not date:
        return None

    cat_num    = category[:5]  # "Cat05"
    target_dir = Path(cfg.CONTEXT_ENTRIES_DIR) / cat_num / category
    existing   = {f.name for f in target_dir.glob("*.md")} if target_dir.exists() else set()
    ce_name    = _make_ce_filename(date, category, existing)
    return target_dir, ce_name


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

_BAR = "═" * 68
_DIV = "─" * 68


def _display_file(path: Path, idx: int, total: int, target_dir: Path | None, ce_name: str | None) -> None:
    """Render the current file review screen.

    Args:
        path:       Absolute path to the EX_ file.
        idx:        0-based index in the file list.
        total:      Total number of files to review.
        target_dir: Where the file would move on approve (or None).
        ce_name:    New filename on approve (or None).
    """
    _clr()
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"{BOLD}{CYAN}  Evelyn — Context Entry Reviewer  {DIM}Phase 1: Extracted Facts{RESET}")
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"  File {BOLD}{idx + 1}{RESET} of {BOLD}{total}{RESET}:  {YELLOW}{path.name}{RESET}")
    if target_dir and ce_name:
        print(f"  {DIM}→ {target_dir / ce_name}{RESET}")
    else:
        print(f"  {RED}  ⚠ Cannot determine target path{RESET}")
    print(f"{DIM}{_DIV}{RESET}\n")

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        lines = [f"[ERROR reading file: {e}]"]

    for line in lines[:50]:
        if line.startswith("---"):
            print(f"  {DIM}{line}{RESET}")
        elif line.startswith("#"):
            print(f"  {BOLD}{line}{RESET}")
        elif line.startswith("**") and ":" in line:
            key, _, val = line.partition(":")
            print(f"  {CYAN}{key}:{RESET}{val}")
        elif line.startswith("> [!NOTE]"):
            print(f"  {DIM}{line}{RESET}")
        else:
            print(f"  {line}")

    if len(lines) > 50:
        print(f"\n  {DIM}... {len(lines) - 50} more lines not shown{RESET}")

    print(f"\n{DIM}{_DIV}{RESET}")
    print(
        f"  {GREEN}[A]{RESET} Approve & promote   "
        f"{YELLOW}[D]{RESET} Deny / skip   "
        f"{RED}[X]{RESET} Delete   "
        f"{DIM}[Q]{RESET} Quit"
    )
    print(f"{DIM}{_DIV}{RESET}")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Phase 1 — Extracted fact review
# ---------------------------------------------------------------------------


def _collect_extracted() -> list[Path]:
    """Return sorted list of EX_*.md files from EXTRACTED_DIR.

    Excludes hidden files and the _README.md placeholder.

    Returns:
        List of Path objects sorted alphabetically.
    """
    d = Path(cfg.EXTRACTED_DIR)
    if not d.exists():
        return []
    return sorted(f for f in d.glob("EX_*.md") if not f.name.startswith("_"))


def run_phase1() -> None:
    """Interactive review loop for Extracted/ EX_ files.

    Presents each file one at a time and processes single-key commands:
    A → approve and promote, D → deny/skip, X → delete, Q → quit.
    Prints a summary on exit.
    """
    files = _collect_extracted()
    if not files:
        print(f"\n  {YELLOW}No EX_*.md files found in Extracted/ — nothing to review.{RESET}\n")
        return

    approved = denied = deleted = errors = 0
    idx = 0

    while idx < len(files):
        path = files[idx]

        if not path.exists():
            idx += 1
            continue

        result = _compute_target(path.name)
        target_dir, ce_name = result if result else (None, None)

        _display_file(path, idx, len(files), target_dir, ce_name)
        ch = _getch()

        if ch == "q":
            print(f"\n  {DIM}Quitting — {len(files) - idx} file(s) remaining.{RESET}")
            break

        if ch == "a":
            if not result:
                print(f"\n  {RED}✗ Cannot parse category/date from filename. Skipping.{RESET}")
                errors += 1
                time.sleep(1.2)
                idx += 1
                continue
            try:
                content  = path.read_text(encoding="utf-8")
                cleaned  = _clean_content(content, path.name, ce_name)
                target_dir.mkdir(parents=True, exist_ok=True)
                (target_dir / ce_name).write_text(cleaned, encoding="utf-8")
                path.unlink()
                approved += 1
                print(f"\n  {GREEN}✓ Approved → {ce_name}{RESET}")
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
                path.unlink()
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
    print(f"  Remaining in Extracted/ : {remaining}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Main entry point.  Prints counts then starts Phase 1 review."""
    _clr()
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"{BOLD}{CYAN}  Evelyn — Context Entry Reviewer{RESET}")
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"  Extracted folder : {cfg.EXTRACTED_DIR}")
    print(f"  Pending folder   : {cfg.PENDING_DIR}")
    print(f"  Entries root     : {cfg.CONTEXT_ENTRIES_DIR}")
    print()

    files = _collect_extracted()
    print(f"  Found {BOLD}{len(files)}{RESET} extracted file(s) to review.")
    print()

    if not files:
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
