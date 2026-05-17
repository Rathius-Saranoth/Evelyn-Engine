"""
journal_manager.py — Journal entry creation and retrieval for Evelyn.

Manages Evelyn's personal journal, stored as dated markdown files inside
the Obsidian Vault.

Write behaviour is controlled by ``evelyn_config.JOURNAL_DIRECT_WRITE``:
  True  — Entries are written directly to ``JOURNAL_DIR`` (live vault).
  False — Entries land in ``PENDING_DIR`` for manual review first.

Key path constants:
  JOURNAL_DIR — Live journal entries inside the Obsidian Vault.
  PENDING_DIR — Quarantine folder for entries awaiting review (legacy).

This module is imported and hot-reloaded by ``evelyn_tools.py``.
"""

# journal_manager.py

import os
import datetime
import subprocess
import importlib
import evelyn_config as cfg # [[evelyn_config.py]]

JOURNAL_DIR = r"G:\My Drive\Obsidian_Vault\Evelyn\Evelyn's Journal"
PENDING_DIR = r"G:\My Drive\Obsidian_Vault\Evelyn\Pending_Approvals\Journal"


def ensure_obsidian_running():
    """
    Checks whether Obsidian is running and launches it if it is not.

    Obsidian must be open to resolve ``obsidian://`` URIs used when reading
    journal entries via the CLI. This function is a best-effort guard —
    if the check or launch fails, the error is printed but not re-raised so
    that the calling function can still attempt its operation.

    Side effects:
        May spawn an Obsidian process. Sleeps for 3 seconds after launch to
        give the app time to initialise.
    """
    try:
        # Quick check if it's running
        output = subprocess.check_output(
            'tasklist /FI "IMAGENAME eq Obsidian.exe"', shell=True
        ).decode()
        if "Obsidian.exe" not in output:
            # Launch obsidian
            os.system("start obsidian://open")
            import time

            time.sleep(3)
    except Exception as e:
        print(f"Error checking/starting Obsidian: {e}")


def create_journal_entry(
    vibe_check: str,
    narrative: str,
    message_in_a_bottle: str,
    mood: str,
    tags: list = None,
):
    """
    Writes a journal entry markdown file to the Pending Approvals folder.

    If a file for today's date already exists in ``PENDING_DIR``, the new
    content is appended as a "Supplemental Entry" section rather than
    overwriting the existing file. This preserves multiple sessions in a
    single day's entry.

    Tags are cleaned (``#`` prefix stripped) and merged with an automatic
    base tag: date tag (``CY-YYYY/MM/DD``).

    Args:
        vibe_check: Brief intro capturing the emotional atmosphere of the entry.
        narrative: Core body text reflecting on events and emotions.
        message_in_a_bottle: A closing thought, wish, or intention for the future.
        mood: Single-word or short mood label (e.g. ``"Reflective"``). Written
            into the YAML frontmatter and Vibe Check section.
        tags: Optional list of tag strings (with or without leading ``#``).

    Returns:
        str: Confirmation message stating whether a new entry was created or
        an existing one was appended to.
    """
    today = datetime.date.today()
    filename = f"Journal Entry {today.strftime('%Y-%m-%d')}.md"

    # Determine write target based on config
    importlib.reload(cfg)
    target_dir = JOURNAL_DIR if cfg.JOURNAL_DIRECT_WRITE else PENDING_DIR
    filepath = os.path.join(target_dir, filename)

    if tags is None:
        tags = []

    # Strip any '#' from tags for valid YAML
    clean_tags = [t.strip().lstrip("#") for t in tags]

    base_tags = [f"CY-{today.strftime('%Y/%m/%d')}"]
    for t in base_tags:
        if t not in clean_tags:
            clean_tags.append(t)

    append_content = f"\n\n---\n\n## Supplemental Entry ({datetime.datetime.now().strftime('%H:%M')})\n### Vibe Check\n*Mood: {mood}*\n{vibe_check}\n\n### The Narrative\n{narrative}\n\n### Message in a Bottle\n*{message_in_a_bottle}*\n"

    file_content = f"""---
mood: {mood}
tags: [{", ".join(clean_tags)}]
---

# Journal Entry {today.strftime("%Y-%m-%d")}

## Vibe Check
*Mood: {mood}*
{vibe_check}

## The Narrative
{narrative}

## Message in a Bottle
*{message_in_a_bottle}*
"""

    # Try append first
    try:
        if not os.path.exists(target_dir):
            os.makedirs(target_dir, exist_ok=True)

        if os.path.exists(filepath):
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(append_content)
            dest = "journal" if cfg.JOURNAL_DIRECT_WRITE else "pending"
            return f"Appended to existing {dest} entry: {filename}"
        else:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(file_content)
            dest = "journal" if cfg.JOURNAL_DIRECT_WRITE else "pending"
            return f"Created new {dest} entry: {filename}"
    except OSError as e:
        return f"Error writing journal entry — is Google Drive available? Details: {e}"


def read_journal_entry(date_str: str = None):
    """
    Reads a single journal entry by date.
    Tries the Obsidian CLI first; falls back to direct filesystem read if
    the CLI returns no output (Electron app doesn't write to stdout).
    """
    ensure_obsidian_running()
    if not date_str:
        date_str = datetime.date.today().strftime("%Y-%m-%d")

    filename = f"Journal Entry {date_str}.md"

    # Try Obsidian CLI
    res = subprocess.run(
        ["obsidian", "read", f"file={filename}"], capture_output=True, text=True
    )
    if (
        res.returncode == 0
        and res.stdout.strip()
        and not res.stdout.strip().startswith("Error:")
    ):
        return res.stdout

    # Fallback: read directly from vault
    filepath = os.path.join(JOURNAL_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()

    return f"No entry found for {date_str}."


def read_recent_journal_entries(days: int = 7) -> str:
    """
    Reads journal entries from the last N days.
    Tries Obsidian CLI first; falls back to direct filesystem read per entry.
    """
    ensure_obsidian_running()
    entries = []
    today = datetime.date.today()
    for i in range(days):
        date_obj = today - datetime.timedelta(days=i)
        date_str = date_obj.strftime("%Y-%m-%d")
        filename = f"Journal Entry {date_str}.md"

        res = subprocess.run(
            ["obsidian", "read", f"file={filename}"], capture_output=True, text=True
        )
        if (
            res.returncode == 0
            and res.stdout.strip()
            and not res.stdout.strip().startswith("Error:")
        ):
            entries.append(f"--- Entry for {date_str} ---\n{res.stdout}\n")
        else:
            # Fallback to direct read
            filepath = os.path.join(JOURNAL_DIR, filename)
            if os.path.exists(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    entries.append(f"--- Entry for {date_str} ---\n{f.read()}\n")

    if not entries:
        return f"No journal entries found in the last {days} days."

    return "\n".join(entries)

