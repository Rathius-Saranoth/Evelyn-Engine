"""
pending_reviewer.py — Interactive reviewer for Evelyn's consolidation/recategorization proposals.

Handles two proposal types from PENDING_DIR:
  CONSOLIDATION_*.md  — merge N source CEs into one new CE
  RECATEGORIZE_*.md   — move a CE file to a new category

Run from the project root:
    python Evelyn\\tools\\pending_reviewer.py
"""

import os
import re
import sys
import time
import shutil
import datetime
from pathlib import Path

import yaml
from dataclasses import dataclass, field

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import evelyn_config as cfg

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
    """Read a single keypress without requiring Enter (cross-platform).

    Returns:
        Lowercase single character.
    """
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


def _open_in_editor(path: Path):
    """Open *path* in the system default .md editor (Antigravity / Notepad).

    Blocks until the user presses a key to return, then re-displays the
    current file from disk.

    Args:
        path: File to open.
    """
    try:
        os.startfile(str(path))
        print(f"\n  {CYAN}→ Opened in editor. Press any key when done...{RESET}")
        sys.stdout.flush()
        _getch()
    except Exception as e:
        print(f"\n  {RED}✗ Could not open editor: {e}{RESET}")
        time.sleep(1.2)


def _render_file_lines(path: Path, max_lines: int = 40):
    """Print the contents of *path* to the terminal with basic syntax highlighting.

    Args:
        path:      File to display.
        max_lines: Maximum number of lines to print before truncating.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        print(f"  {RED}[ERROR reading file: {e}]{RESET}")
        return
    for line in lines[:max_lines]:
        if line.startswith("---"):
            print(f"  {DIM}{line}{RESET}")
        elif line.startswith("#"):
            print(f"  {BOLD}{line}{RESET}")
        elif line.startswith("**") and ":" in line:
            key, _, val = line.partition(":")
            print(f"  {CYAN}{key}:{RESET}{val}")
        elif line.startswith("> "):
            print(f"  {MAGENTA}{line}{RESET}")
        elif line.startswith("> [!"):
            print(f"  {DIM}{line}{RESET}")
        else:
            print(f"  {line}")
    if len(lines) > max_lines:
        print(f"\n  {DIM}... {len(lines) - max_lines} more lines not shown{RESET}")


# ---------------------------------------------------------------------------
# Category name lookup
# ---------------------------------------------------------------------------

_CAT_NAMES = {
    "Cat01-R": "Core Identity & Self-Perception",
    "Cat01-E": "Core Identity & Self-Perception (Evelyn)",
    "Cat02-R": "Core Values & Beliefs",
    "Cat02-E": "Core Values & Beliefs (Evelyn)",
    "Cat03-R": "Cognitive & Decision-Making Style",
    "Cat03-E": "Cognitive & Decision-Making Style (Evelyn)",
    "Cat04-R": "Emotional Landscape",
    "Cat04-E": "Emotional Landscape (Evelyn)",
    "Cat05-R": "Relationships & Social Dynamics",
    "Cat05-E": "Relationships & Social Dynamics (Evelyn)",
    "Cat06-R": "Life History & Key Events",
    "Cat06-E": "Life History & Key Events (Evelyn)",
    "Cat07-R": "Professional & Creative Life",
    "Cat07-E": "Professional & Creative Life (Evelyn)",
    "Cat08-R": "Health & Physical Well-Being",
    "Cat08-E": "Health & Physical Well-Being (Evelyn)",
    "Cat09-R": "Interests, Hobbies & Passions",
    "Cat09-E": "Interests, Hobbies & Passions (Evelyn)",
    "Cat10-R": "Communication Style & Preferences",
    "Cat10-E": "Communication Style & Preferences (Evelyn)",
    "Cat11-R": "Goals, Aspirations & Fears",
    "Cat11-E": "Goals, Aspirations & Fears (Evelyn)",
    "Cat12-R": "Emotional States & Responses",
    "Cat12-E": "Emotional States & Responses (Evelyn)",
    "Cat13-R": "Recurring Themes & Patterns",
    "Cat13-E": "Recurring Themes & Patterns (Evelyn)",
    "Cat14-R": "Humor, Play & Lightness",
    "Cat14-E": "Humor, Play & Lightness (Evelyn)",
    "Cat15-R": "Spirituality & Philosophy",
    "Cat15-E": "Spirituality & Philosophy (Evelyn)",
    "Cat16-R": "Shared Memories & Milestones",
    "Cat16-E": "Shared Memories & Milestones (Evelyn)",
}

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Shared
_CAT_CODE_RE   = re.compile(r"(Cat\d{2}-[ER])", re.IGNORECASE)
_DATE_RE       = re.compile(r"(\d{4}-\d{2}-\d{2})")

# CE file tag replacement (used in approve actions)
_PRIMARY_TAG_RE  = re.compile(
    r"(\*\*Primary:\*\*\s+\[\[)(Cat\d{2}-[ER])(\]\])(\s*\([^)]*\))?"
)

# CONSOLIDATION body-section patterns (still needed — body format unchanged)
_SOURCE_ENTRY_RE = re.compile(r"^-\s+`([^`]+)`", re.MULTILINE)
_MERGED_SUM_RE   = re.compile(
    r"## Proposed Summary\s*\n((?:[ \t]*>.*\n?)+)", re.MULTILINE
)
# Reason sub-patterns from ## Reasoning body section
_FLAGGED_RE      = re.compile(r"\*\*Flagged:\*\*\s*(.+)")
_ANALYSIS_RE     = re.compile(r"\*\*Analysis:\*\*\s*(.+)")
# Recategorize body: ## Reasoning contains the full reason text
_RECAT_REASON_RE = re.compile(r"## Reasoning\s*\n\s*(.+)")

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RecatProposal:
    path: Path
    entry_name: str
    current_cat: str
    suggested_cat: str
    reason: str
    current_path: Path
    suggested_path: Path
    topic: str = ""


@dataclass
class ConsolProposal:
    path: Path
    source_date: str
    target_cat: str
    merged_summary: str
    source_entries: list = field(default_factory=list)
    topic: str = ""
    verdict: str = "merge"


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Normalise garbled unicode artefacts in LLM-generated proposal text."""
    return text.replace("\ufffd", "—").replace("\x00", "").strip()


def _read_frontmatter(path: Path) -> dict:
    """Read YAML frontmatter from a markdown file.

    Args:
        path: Path to the markdown file.

    Returns:
        Dict of frontmatter fields, or empty dict on failure.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[3:end]) or {}
    except Exception:
        return {}


def parse_recat(path: Path):
    """Parse a RECATEGORIZE_*.md proposal file into a RecatProposal.

    Reads structured fields from YAML frontmatter and the reasoning
    text from the ``## Reasoning`` body section.

    Args:
        path: Absolute path to the proposal file.

    Returns:
        RecatProposal on success, or None if the file is missing/unparseable.
    """
    fm = _read_frontmatter(path)
    if not fm:
        return None

    current_cat  = fm.get("current_cat", "")
    suggest_cat  = fm.get("suggested_cat", "")
    current_path = fm.get("source_path", "")
    suggest_path = fm.get("suggested_path", "")
    topic        = str(fm.get("topic", "")).strip()

    if not all([current_cat, suggest_cat, current_path, suggest_path]):
        return None

    # Extract entry filename from the ## Entry body section
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    entry_m = re.search(r"## Entry\s*\n\s*`([^`]+)`", raw)
    entry_name = entry_m.group(1).strip() if entry_m else Path(current_path).name

    # Extract reason from ## Reasoning body section
    reason_m = _RECAT_REASON_RE.search(raw)
    reason = _norm(reason_m.group(1)) if reason_m else ""

    return RecatProposal(
        path=path,
        entry_name=entry_name,
        current_cat=current_cat,
        suggested_cat=suggest_cat,
        reason=reason,
        current_path=Path(current_path),
        suggested_path=Path(suggest_path),
        topic=topic,
    )


def parse_consol(path: Path):
    """Parse a CONSOLIDATION_*.md proposal file into a ConsolProposal.

    Reads structured fields from YAML frontmatter and body sections
    (source entries list, merged summary blockquote) via targeted regex.
    Re-reads the file from disk each time, so edits made via [E]dit are
    reflected immediately on re-parse.

    Args:
        path: Absolute path to the proposal file.

    Returns:
        ConsolProposal on success, or None if the file is missing/unparseable.
    """
    fm = _read_frontmatter(path)
    if not fm:
        return None

    target_cat  = fm.get("category", "")
    source_date = str(fm.get("source_date", "2026-01-01"))
    verdict     = str(fm.get("verdict", "merge")).strip().lower()
    topic       = str(fm.get("topic", "")).strip()

    if not target_cat:
        return None

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    entries = [e.strip() for e in _SOURCE_ENTRY_RE.findall(raw)]
    sm = _MERGED_SUM_RE.search(raw)

    # keep_both verdicts have no merged summary — that's valid
    if verdict != "keep_both" and not sm:
        return None

    merged = ""
    if sm:
        lines = [l.lstrip().lstrip(">").strip() for l in sm.group(1).splitlines()]
        merged = _norm(" ".join(l for l in lines if l))

    return ConsolProposal(
        path=path,
        source_date=source_date,
        target_cat=target_cat,
        merged_summary=merged,
        source_entries=entries,
        topic=topic,
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def collect_recats() -> list:
    """Return deduplicated, sorted list of RECATEGORIZE_*.md files from PENDING_DIR.

    Deduplication: Multiple passes of the consolidator may flag the same
    source entry repeatedly.  This groups proposals by ``source_path``
    (from frontmatter) and keeps only the most recent proposal per unique
    source entry, deleting the stale duplicates from disk.

    Returns:
        List of Paths, sorted by filename (chronological).
    """
    d = Path(cfg.PENDING_DIR)
    if not d.exists():
        return []

    all_files = sorted(
        (f for f in d.glob("RECATEGORIZE_*.md") if not f.name.startswith("_")),
    )

    # Group by source_path — keep only the newest proposal per source
    by_source: dict[str, list[Path]] = {}
    no_source: list[Path] = []  # fallback for files with no parseable frontmatter

    for f in all_files:
        fm = _read_frontmatter(f)
        src = fm.get("source_path", "")
        if src:
            by_source.setdefault(src, []).append(f)
        else:
            no_source.append(f)

    kept: list[Path] = list(no_source)
    stale_count = 0
    for src, paths in by_source.items():
        # Paths are already sorted chronologically; keep the last (newest)
        kept.append(paths[-1])
        for stale in paths[:-1]:
            try:
                stale.unlink()
                stale_count += 1
            except OSError:
                pass

    if stale_count:
        print(
            f"  {DIM}Deduplicated: removed {stale_count} stale "
            f"recategorization proposal(s).{RESET}"
        )

    return sorted(kept)


def collect_consols() -> list:
    """Return sorted list of CONSOLIDATION_*.md files from PENDING_DIR."""
    d = Path(cfg.PENDING_DIR)
    if not d.exists():
        return []
    return sorted(f for f in d.glob("CONSOLIDATION_*.md") if not f.name.startswith("_"))


# ---------------------------------------------------------------------------
# Source CE resolver
# ---------------------------------------------------------------------------

def _resolve_source(cat: str, filename: str) -> Path | None:
    """Resolve a source CE filename to its absolute path under CONTEXT_ENTRIES_DIR.

    Args:
        cat:      Category code string (e.g. ``"Cat02-R"``).
        filename: Basename of the CE file.

    Returns:
        Path if the file exists, otherwise None.
    """
    m = _CAT_CODE_RE.search(cat)
    if not m:
        return None
    cat_code = m.group(1)
    cat_num  = cat_code[:5]
    target   = Path(cfg.CONTEXT_ENTRIES_DIR) / cat_num / cat_code / filename
    return target if target.exists() else None


def _make_ce_filename(date: str, category: str, existing: set) -> str:
    """Return a unique CE_ filename that does not collide with *existing* files.

    Args:
        date:     ``YYYY-MM-DD`` string.
        category: Category code like ``Cat05-R``.
        existing: Set of filenames already in the target directory.

    Returns:
        Collision-safe filename like ``CE_2024-04-25_Cat05-R.md``.
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


# ---------------------------------------------------------------------------
# CE builder
# ---------------------------------------------------------------------------

def _build_ce_content(proposal: ConsolProposal) -> str:
    """Construct the full markdown content for a new consolidated CE file.

    The resulting file follows the standard CE_ format: YAML frontmatter,
    heading, Primary link, and Summary.  ``kw/`` tags are intentionally omitted
    — Evelyn will add them on her next extraction pass.

    Args:
        proposal: A parsed ConsolProposal with target_cat, source_date, and
                  merged_summary populated.

    Returns:
        Complete UTF-8 markdown string ready to write.
    """
    date   = proposal.source_date
    cat    = proposal.target_cat
    now    = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    cy_tag = "CY-" + date.replace("-", "/")[:7]  # CY-YYYY/MM
    cat_name = _CAT_NAMES.get(cat, cat)

    return (
        f"---\n"
        f"aliases: []\n"
        f"tags: [{cy_tag}]\n"
        f"icon:\n"
        f"date created: {now}\n"
        f"date modified: {now}\n"
        f"---\n\n"
        f"# CE_{date}_{cat}\n\n"
        f"#{cy_tag}\n\n"
        f"**Primary:** [[{cat}]] ({cat_name})\n\n"
        f"**Summary:** {proposal.merged_summary}\n"
    )


# ---------------------------------------------------------------------------
# Display: Recategorize
# ---------------------------------------------------------------------------

def _display_recat(proposal: RecatProposal, idx: int, total: int):
    """Render the recategorization review screen for one proposal.

    Args:
        proposal: Parsed RecatProposal to display.
        idx:      0-based index in the file list.
        total:    Total number of recategorization proposals.
    """
    _clr()
    cur_name = _CAT_NAMES.get(proposal.current_cat, "")
    sug_name = _CAT_NAMES.get(proposal.suggested_cat, "")
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"{BOLD}{CYAN}  Evelyn — Pending Reviewer       Phase A: Recategorizations{RESET}")
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"  File {BOLD}{idx + 1}{RESET} of {BOLD}{total}{RESET}:  {YELLOW}{proposal.path.name}{RESET}")
    if proposal.topic:
        print(f"  Topic: {BOLD}{proposal.topic}{RESET}")
    print(f"  {BOLD}{proposal.current_cat}{RESET} ({cur_name})  →  {GREEN}{proposal.suggested_cat}{RESET} ({sug_name})")
    if proposal.reason:
        print(f"  {DIM}Reason: {proposal.reason}{RESET}")
    print(f"{DIM}{_DIV}{RESET}\n")

    src = proposal.current_path
    exists = src.exists()
    status = f"{GREEN}✓ EXISTS{RESET}" if exists else f"{RED}✗ MISSING{RESET}"
    print(f"  SOURCE ENTRY  [{BOLD}{proposal.entry_name}{RESET}  {status}]")

    if exists:
        print()
        _render_file_lines(src)
    else:
        print(f"\n  {DIM}(File not found — may have been moved or deleted.){RESET}")

    print(f"\n{DIM}{_DIV}{RESET}")
    print(
        f"  {GREEN}[A]{RESET} Approve & move   "
        f"{MAGENTA}[+]{RESET} Add Secondary   "
        f"{CYAN}[E]{RESET} Edit source   "
        f"{YELLOW}[D]{RESET} Deny   "
        f"{DIM}[S]{RESET} Skip   "
        f"{RED}[Q]{RESET} Quit"
    )
    print(f"{DIM}{_DIV}{RESET}")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Display: Consolidation
# ---------------------------------------------------------------------------

def _display_consol(proposal: ConsolProposal, idx: int, total: int):
    """Render the consolidation review screen for one proposal.

    Args:
        proposal: Parsed ConsolProposal to display.
        idx:      0-based index in the file list.
        total:    Total number of consolidation proposals.
    """
    _clr()
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"{BOLD}{CYAN}  Evelyn — Pending Reviewer        Phase B: Consolidations{RESET}")
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"  File {BOLD}{idx + 1}{RESET} of {BOLD}{total}{RESET}:  {YELLOW}{proposal.path.name}{RESET}")
    n = len(proposal.source_entries)
    verb = proposal.verdict.capitalize()
    if proposal.topic:
        print(f"  Topic: {BOLD}{proposal.topic}{RESET}")
    print(f"  {verb} {BOLD}{n}{RESET} entr{'y' if n == 1 else 'ies'}  →  {GREEN}{proposal.target_cat}{RESET}")
    print(f"{DIM}{_DIV}{RESET}\n")

    if proposal.verdict == "keep_both":
        print(f"  {YELLOW}VERDICT: Keep Both — entries are distinct.{RESET}\n")
    else:
        print(f"  {BOLD}PROPOSED MERGED SUMMARY:{RESET}")
        for line in _wrap(proposal.merged_summary, 64):
            print(f"  {MAGENTA}> {line}{RESET}")

    print(f"\n  {BOLD}SOURCE ENTRIES ({n}):{RESET}")
    for entry in proposal.source_entries:
        cat_m = _CAT_CODE_RE.search(entry)
        found = _resolve_source(cat_m.group(1) if cat_m else proposal.target_cat, entry)
        mark = f"{GREEN}✓{RESET}" if found else f"{RED}✗{RESET}"
        print(f"    [{mark}]  {entry}")

    print(f"\n{DIM}{_DIV}{RESET}")
    print(
        f"  {GREEN}[A]{RESET} Approve   "
        f"{CYAN}[E]{RESET} Edit proposal   "
        f"{YELLOW}[V]{RESET} View sources   "
        f"{YELLOW}[D]{RESET} Deny   "
        f"{DIM}[S]{RESET} Skip   "
        f"{RED}[Q]{RESET} Quit"
    )
    print(f"{DIM}{_DIV}{RESET}")
    sys.stdout.flush()


def _wrap(text: str, width: int) -> list:
    """Word-wrap *text* to *width* characters, returning a list of lines."""
    words = text.split()
    lines, current = [], []
    length = 0
    for w in words:
        if length + len(w) + bool(current) > width:
            lines.append(" ".join(current))
            current, length = [w], len(w)
        else:
            current.append(w)
            length += len(w) + bool(current)
    if current:
        lines.append(" ".join(current))
    return lines or [""]


# ---------------------------------------------------------------------------
# Actions: Recategorize
# ---------------------------------------------------------------------------

def _approve_recat(proposal: RecatProposal) -> bool:
    """Execute the approve action for a recategorization proposal.

    Reads the source CE, updates its ``**Primary:**`` tag to the suggested
    category, renames the file to reflect the new category code, writes it
    to the suggested directory, deletes the source, and deletes the proposal.

    Args:
        proposal: The parsed RecatProposal to action.

    Returns:
        True on success, False if the source was missing or an error occurred.
    """
    src = proposal.current_path
    if not src.exists():
        print(f"\n  {RED}✗ Source file not found — cannot move.{RESET}")
        time.sleep(1.5)
        return False

    try:
        content = src.read_text(encoding="utf-8")

        # Update Primary tag: replace cat code and parenthetical name (first match only).
        # count=1 prevents accidental replacement of **Primary:** mentions in body text.
        new_cat = proposal.suggested_cat
        cat_name = _CAT_NAMES.get(new_cat, new_cat)
        def _replace_primary(m: re.Match) -> str:
            return m.group(1) + new_cat + "]]" + f" ({cat_name})"
        content = _PRIMARY_TAG_RE.sub(_replace_primary, content, count=1)

        # Preserve original filename date; only swap the category code portion.
        # Falls back to date extraction if the name format is non-standard.
        old_name = src.name  # e.g. CE_2025-07-25_Cat03-R.md
        new_name = old_name.replace(proposal.current_cat, new_cat)
        if new_name == old_name:
            # category code not found in name — reconstruct safely
            date_m = _DATE_RE.search(old_name)
            date   = date_m.group(1) if date_m else datetime.date.today().isoformat()
            new_name = f"CE_{date}_{new_cat}.md"

        cat_num    = new_cat[:5]
        target_dir = Path(cfg.CONTEXT_ENTRIES_DIR) / cat_num / new_cat
        target_dir.mkdir(parents=True, exist_ok=True)

        # Collision avoidance
        if (target_dir / new_name).exists():
            stem = Path(new_name).stem
            i = 1
            while (target_dir / f"{stem} ({i}).md").exists():
                i += 1
            new_name = f"{stem} ({i}).md"

        dest = target_dir / new_name

        # Write updated content, then copy OS timestamps from source.
        dest.write_text(content, encoding="utf-8")
        src_stat = src.stat()
        os.utime(dest, (src_stat.st_atime, src_stat.st_mtime))

        src.unlink()
        proposal.path.unlink()

        print(f"\n  {GREEN}✓ Moved → {dest}{RESET}")
        time.sleep(0.6)
        return True

    except Exception as e:
        print(f"\n  {RED}✗ Error: {e}{RESET}")
        time.sleep(1.5)
        return False


def _deny_proposal(path: Path):
    """Delete the proposal file, leaving the source CE(s) untouched.

    Args:
        path: Path to the proposal ``.md`` file.
    """
    try:
        path.unlink()
        print(f"\n  {YELLOW}→ Proposal deleted. Source entry untouched.{RESET}")
    except Exception as e:
        print(f"\n  {RED}✗ Error deleting proposal: {e}{RESET}")
    time.sleep(0.5)


def _add_secondary(proposal: RecatProposal) -> bool:
    """Add the suggested category as a **Secondary:** link in the source CE.

    Instead of moving the file (full recategorization), this keeps the entry
    in its current category but adds a cross-reference link to the suggested
    category.  The original file timestamps are preserved.

    Args:
        proposal: The parsed RecatProposal to action.

    Returns:
        True on success, False if the source was missing or an error occurred.
    """
    src = proposal.current_path
    if not src.exists():
        print(f"\n  {RED}✗ Source file not found — cannot update.{RESET}")
        time.sleep(1.5)
        return False

    try:
        content = src.read_text(encoding="utf-8")
        new_cat = proposal.suggested_cat
        cat_name = _CAT_NAMES.get(new_cat, new_cat)
        secondary_line = f"**Secondary:** [[{new_cat}]] ({cat_name})"

        # Replace existing Secondary line, or insert after Primary line
        secondary_re = re.compile(r"^\*\*Secondary:\*\*.*$", re.MULTILINE)
        if secondary_re.search(content):
            content = secondary_re.sub(secondary_line, content, count=1)
        else:
            # Insert after the **Primary:** line
            primary_re = re.compile(r"(^\*\*Primary:\*\*.+$)", re.MULTILINE)
            m = primary_re.search(content)
            if m:
                insert_pos = m.end()
                content = content[:insert_pos] + "\n" + secondary_line + content[insert_pos:]
            else:
                # No Primary found — append before Summary as fallback
                content = content.rstrip() + "\n\n" + secondary_line + "\n"

        # Write back, preserving original timestamps
        src_stat = src.stat()
        src.write_text(content, encoding="utf-8")
        os.utime(src, (src_stat.st_atime, src_stat.st_mtime))

        proposal.path.unlink()

        print(f"\n  {GREEN}✓ Added Secondary: [[{new_cat}]] — proposal resolved.{RESET}")
        time.sleep(0.6)
        return True

    except Exception as e:
        print(f"\n  {RED}✗ Error: {e}{RESET}")
        time.sleep(1.5)
        return False


# ---------------------------------------------------------------------------
# Actions: Consolidation
# ---------------------------------------------------------------------------

def _approve_consol(proposal: ConsolProposal) -> bool:
    """Execute the approve action for a consolidation proposal.

    Creates a new CE file from the merged summary, deletes each resolved
    source CE, and deletes the proposal file.

    Args:
        proposal: The parsed ConsolProposal to action.

    Returns:
        True on success, False if an error occurred.
    """
    try:
        cat     = proposal.target_cat
        cat_num = cat[:5]
        target_dir = Path(cfg.CONTEXT_ENTRIES_DIR) / cat_num / cat
        target_dir.mkdir(parents=True, exist_ok=True)
        existing = {f.name for f in target_dir.glob("*.md")}
        new_name = _make_ce_filename(proposal.source_date, cat, existing)
        dest     = target_dir / new_name

        content = _build_ce_content(proposal)
        dest.write_text(content, encoding="utf-8")

        deleted = 0
        for entry in proposal.source_entries:
            cat_m = _CAT_CODE_RE.search(entry)
            src   = _resolve_source(cat_m.group(1) if cat_m else cat, entry)
            if src and src.exists():
                src.unlink()
                deleted += 1

        proposal.path.unlink()

        print(f"\n  {GREEN}✓ Created {new_name}  |  Deleted {deleted} source(s){RESET}")
        time.sleep(0.7)
        return True

    except Exception as e:
        print(f"\n  {RED}✗ Error: {e}{RESET}")
        time.sleep(1.5)
        return False


def _view_sources(proposal: ConsolProposal):
    """Interactively display each source CE referenced by a consolidation proposal.

    Cycles through resolved source files one at a time.  Any key advances;
    ``Q`` returns to the proposal display.

    Args:
        proposal: The ConsolProposal whose source entries to display.
    """
    sources = []
    for entry in proposal.source_entries:
        cat_m = _CAT_CODE_RE.search(entry)
        p = _resolve_source(cat_m.group(1) if cat_m else proposal.target_cat, entry)
        sources.append((entry, p))

    for i, (name, path) in enumerate(sources):
        _clr()
        print(f"{BOLD}{CYAN}{_BAR}{RESET}")
        exists = path and path.exists()
        status = f"{GREEN}✓{RESET}" if exists else f"{RED}MISSING{RESET}"
        print(f"  Source {i + 1} of {len(sources)}:  {YELLOW}{name}{RESET}  [{status}]")
        print(f"{DIM}{_DIV}{RESET}\n")
        if exists:
            _render_file_lines(path, max_lines=50)
        else:
            print(f"  {DIM}(File not found){RESET}")
        print(f"\n{DIM}{_DIV}{RESET}")
        print(f"  Any key → next source   {RED}[Q]{RESET} back to proposal")
        print(f"{DIM}{_DIV}{RESET}")
        sys.stdout.flush()
        ch = _getch()
        if ch == "q":
            break


# ---------------------------------------------------------------------------
# Review runners
# ---------------------------------------------------------------------------

def run_recats():
    """Interactive review loop for RECATEGORIZE proposals.

    Presents each proposal one at a time with single-key commands:
    A → approve & move, + → add as secondary category, E → edit source,
    D → deny/delete proposal, S → skip, Q → quit.
    Prints a summary on exit.
    """
    files = collect_recats()
    if not files:
        print(f"\n  {YELLOW}No RECATEGORIZE_*.md files found.{RESET}\n")
        return

    approved = denied = skipped = errors = secondary = 0
    auto_removed = 0
    idx = 0

    while idx < len(files):
        path = files[idx]
        if not path.exists():
            idx += 1
            continue

        proposal = parse_recat(path)
        if not proposal:
            _clr()
            print(f"\n  {RED}⚠ Cannot parse: {path.name} — skipping.{RESET}\n")
            time.sleep(1.2)
            idx += 1
            errors += 1
            continue

        # Mid-session orphan check: source vanished since startup.
        if not proposal.current_path.exists():
            try:
                path.unlink()
            except OSError:
                pass
            auto_removed += 1
            idx += 1
            continue

        _display_recat(proposal, idx, len(files))
        ch = _getch()

        if ch == "q":
            print(f"\n  {DIM}Quitting — {len(files) - idx} file(s) remaining.{RESET}")
            break
        elif ch == "a":
            if _approve_recat(proposal):
                approved += 1
            else:
                errors += 1
            idx += 1
        elif ch == "+":
            if _add_secondary(proposal):
                secondary += 1
            else:
                errors += 1
            idx += 1
        elif ch == "e":
            _open_in_editor(proposal.current_path if proposal.current_path.exists() else proposal.path)
        elif ch == "d":
            _deny_proposal(proposal.path)
            denied += 1
            idx += 1
        elif ch == "s":
            skipped += 1
            idx += 1

    _print_summary("Recategorizations", approved, denied, skipped, errors, secondary, auto_removed)


def run_consols():
    """Interactive review loop for CONSOLIDATION proposals.

    Presents each proposal one at a time with single-key commands:
    A → approve, E → edit proposal, V → view sources, D → deny,
    S → skip, Q → quit.  Prints a summary on exit.
    """
    files = collect_consols()
    if not files:
        print(f"\n  {YELLOW}No CONSOLIDATION_*.md files found.{RESET}\n")
        return

    approved = denied = skipped = errors = 0
    auto_removed = 0
    idx = 0

    while idx < len(files):
        path = files[idx]
        if not path.exists():
            idx += 1
            continue

        proposal = parse_consol(path)
        if not proposal:
            _clr()
            print(f"\n  {RED}⚠ Cannot parse: {path.name} — skipping.{RESET}\n")
            time.sleep(1.2)
            idx += 1
            errors += 1
            continue

        # Mid-session orphan check: purge if ANY source is missing since
        # a partial proposal cannot be properly reviewed.
        any_source_missing = any(
            _resolve_source(
                (_CAT_CODE_RE.search(e).group(1) if _CAT_CODE_RE.search(e) else proposal.target_cat),
                e,
            ) is None
            for e in proposal.source_entries
        ) if proposal.source_entries else False

        if any_source_missing:
            try:
                path.unlink()
            except OSError:
                pass
            auto_removed += 1
            idx += 1
            continue

        _display_consol(proposal, idx, len(files))
        ch = _getch()

        if ch == "q":
            print(f"\n  {DIM}Quitting — {len(files) - idx} file(s) remaining.{RESET}")
            break
        elif ch == "a":
            if _approve_consol(proposal):
                approved += 1
            else:
                errors += 1
            idx += 1
        elif ch == "e":
            _open_in_editor(proposal.path)
            # Re-parse after edit
            updated = parse_consol(path)
            if updated:
                files[idx] = path  # path unchanged; re-display re-parses
        elif ch == "v":
            _view_sources(proposal)
        elif ch == "d":
            _deny_proposal(proposal.path)
            denied += 1
            idx += 1
        elif ch == "s":
            skipped += 1
            idx += 1

    _print_summary("Consolidations", approved, denied, skipped, errors, auto_removed=auto_removed)


def _print_summary(
    label: str, approved: int, denied: int, skipped: int, errors: int,
    secondary: int = 0,
    auto_removed: int = 0,
):
    """Print the end-of-phase review summary screen."""
    _clr()
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"{BOLD}{CYAN}  Review Complete — {label}{RESET}")
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"  {GREEN}Approved    : {approved}{RESET}")
    if secondary:
        print(f"  {MAGENTA}Secondary   : {secondary}{RESET}")
    print(f"  {YELLOW}Denied      : {denied}{RESET}")
    print(f"  {DIM}Skipped     : {skipped}{RESET}")
    if auto_removed:
        print(f"  {DIM}Auto-removed: {auto_removed} (missing sources){RESET}")
    if errors:
        print(f"  {RED}Errors      : {errors}{RESET}")
    print()


# ---------------------------------------------------------------------------
# Orphan purge — auto-remove proposals whose source files are gone
# ---------------------------------------------------------------------------

def _purge_orphaned_proposals() -> tuple[int, int]:
    """Scan all pending proposals and delete those with no surviving source files.

    Runs once at startup before the menu is shown.  A proposal is considered
    orphaned when ANY source CE it references is missing from disk:

    - RECATEGORIZE_*.md: the single ``current_path`` no longer exists.
    - CONSOLIDATION_*.md: *any* source entry is unresolvable.  A partial
      proposal cannot be properly reviewed; the consolidator will re-evaluate
      once the remaining entries are processed.

    Returns:
        Tuple of (recat_purged, consol_purged) counts.
    """
    recat_purged = consol_purged = 0

    # --- Recategorizations ---
    d = Path(cfg.PENDING_DIR)
    if d.exists():
        for f in sorted(d.glob("RECATEGORIZE_*.md")):
            if f.name.startswith("_"):
                continue
            proposal = parse_recat(f)
            if proposal is None:
                continue  # unparseable — leave for the error path in the runner
            if not proposal.current_path.exists():
                try:
                    f.unlink()
                    recat_purged += 1
                except OSError:
                    pass

        # --- Consolidations ---
        for f in sorted(d.glob("CONSOLIDATION_*.md")):
            if f.name.startswith("_"):
                continue
            proposal = parse_consol(f)
            if proposal is None:
                continue
            # Purge if ANY source is missing — a partial proposal cannot be
            # properly reviewed and the consolidator will re-evaluate cleanly
            # once remaining entries are processed.
            any_missing = any(
                _resolve_source(
                    (_CAT_CODE_RE.search(e).group(1)
                     if _CAT_CODE_RE.search(e) else proposal.target_cat),
                    e,
                ) is None
                for e in proposal.source_entries
            )
            if any_missing and proposal.source_entries:
                try:
                    f.unlink()
                    consol_purged += 1
                except OSError:
                    pass

    return recat_purged, consol_purged


# ---------------------------------------------------------------------------
# Main — startup menu
# ---------------------------------------------------------------------------

def main():
    """Entry point.  Purges orphaned proposals then shows the startup menu."""
    _clr()

    # Purge proposals whose source files have already been removed by a
    # previous review or consolidation pass, before counting or displaying.
    recat_purged, consol_purged = _purge_orphaned_proposals()
    total_purged = recat_purged + consol_purged
    if total_purged:
        print(f"{BOLD}{CYAN}{_BAR}{RESET}")
        print(f"{BOLD}{CYAN}  Evelyn — Startup: Orphan Purge{RESET}")
        print(f"{BOLD}{CYAN}{_BAR}{RESET}")
        if recat_purged:
            print(f"  {DIM}Auto-removed {recat_purged} recat proposal(s) — source file(s) missing.{RESET}")
        if consol_purged:
            print(f"  {DIM}Auto-removed {consol_purged} consolidation proposal(s) — all source files missing.{RESET}")
        print()
        time.sleep(1.5)
        _clr()

    recats  = collect_recats()
    consols = collect_consols()

    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"{BOLD}{CYAN}  Evelyn — Pending Proposal Reviewer{RESET}")
    print(f"{BOLD}{CYAN}{_BAR}{RESET}")
    print(f"  Pending folder: {cfg.PENDING_DIR}")
    print()
    print(f"  Found {BOLD}{len(recats)}{RESET} recategorization proposal(s).")
    print(f"  Found {BOLD}{len(consols)}{RESET} consolidation proposal(s).")
    if total_purged:
        print(f"  {DIM}({total_purged} orphaned proposal(s) auto-removed at startup.){RESET}")
    print()
    print(f"  {BOLD}[1]{RESET} Review Recategorizations  ({len(recats)})")
    print(f"  {BOLD}[2]{RESET} Review Consolidations     ({len(consols)})")
    print(f"  {RED}[Q]{RESET} Quit")
    print(f"{DIM}{_DIV}{RESET}")
    sys.stdout.flush()

    ch = _getch()
    if ch == "1":
        try:
            run_recats()
        except KeyboardInterrupt:
            print(f"\n\n  {DIM}Interrupted.{RESET}\n")
    elif ch == "2":
        try:
            run_consols()
        except KeyboardInterrupt:
            print(f"\n\n  {DIM}Interrupted.{RESET}\n")


if __name__ == "__main__":
    main()
