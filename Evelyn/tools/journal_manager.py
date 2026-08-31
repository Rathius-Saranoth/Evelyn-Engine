# journal_manager.py
# date created: 2026-02-12 19:08:40
# date modified: 2026-08-31 17:36:20
# tags: #journal, #management, #entries, #logs, #protocols

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

import datetime
import importlib
import os
import sqlite3

import evelyn_config as cfg  # [[evelyn_config.py]]
from Evelyn.tools.frontmatter_utils import render_frontmatter

JOURNAL_DIR = getattr(cfg, "JOURNAL_DIR", os.path.join(getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault"), getattr(cfg, "ASSISTANT_NAME", "Evelyn"), f"{getattr(cfg, 'ASSISTANT_NAME', 'Evelyn')}'s Journal"))
PENDING_DIR = os.path.join(getattr(cfg, "PENDING_DIR", os.path.join(getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault"), getattr(cfg, "ASSISTANT_NAME", "Evelyn"), "Pending_Approvals")), "Journal")


def _resolve_journal_dir(date_obj: datetime.date | None = None) -> str:
    """Resolve the structured target directory for journal entries:
    JOURNAL_DIR/Journal Entries/YYYY/MM-ShortMonth/
    """
    if not os.environ.get("PYTEST_CURRENT_TEST") and not getattr(cfg, "DISABLE_HOT_RELOAD", False):
        importlib.reload(cfg)
    base_journal = getattr(
        cfg,
        "JOURNAL_DIR",
        os.path.join(
            getattr(cfg, "VAULT_BASE_DIR", os.path.expanduser("~/obsidian_vault")),
            getattr(cfg, "ASSISTANT_NAME", "Evelyn"),
            f"{getattr(cfg, 'ASSISTANT_NAME', 'Evelyn')}'s Journal",
        ),
    )
    if date_obj is None:
        date_obj = datetime.datetime.now(datetime.UTC).astimezone().date()

    year = date_obj.strftime("%Y")
    month_str = f"{date_obj.strftime('%m')}-{date_obj.strftime('%b')}"
    target_dir = os.path.join(base_journal, "Journal Entries", year, month_str)
    os.makedirs(target_dir, exist_ok=True)
    return target_dir


def _resolve_journal_filepath(date_str: str) -> str | None:
    """Find the filepath of a journal entry by date.

    Searches across:
      1. Structured archive: JOURNAL_DIR/Journal Entries/YYYY/MM-ShortMonth/Journal Entry YYYY-MM-DD.md
      2. Live vault root (legacy fallback): JOURNAL_DIR/Journal Entry YYYY-MM-DD.md
      3. Pending quarantine: PENDING_DIR/Journal Entry YYYY-MM-DD.md

    Args:
        date_str: Date string formatted as YYYY-MM-DD.

    Returns:
        str | None: Absolute path to the journal entry markdown file if found, else None.
    """
    filename = f"Journal Entry {date_str}.md"

    # 1. Structured archive folder
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=datetime.UTC)
        year = dt.strftime("%Y")
        month_str = f"{dt.strftime('%m')}-{dt.strftime('%b')}"
        struct_path = os.path.join(JOURNAL_DIR, "Journal Entries", year, month_str, filename)
        if os.path.exists(struct_path):
            return struct_path
    except ValueError:
        pass

    # 2. Live vault root (legacy fallback)
    root_path = os.path.join(JOURNAL_DIR, filename)
    if os.path.exists(root_path):
        return root_path

    # 3. Pending quarantine folder
    pending_path = os.path.join(PENDING_DIR, filename)
    if os.path.exists(pending_path):
        return pending_path

    return None


def create_journal_entry(
    vibe_check: str,
    narrative: str,
    message_in_a_bottle: str,
    mood: str,
    tags: list | None = None,
    date_str: str | None = None,
):
    """
    Writes a journal entry markdown file to the structured Journal Entries archive.

    If a file for the target date already exists in the archive, the new
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
        date_str: Optional target date string in YYYY-MM-DD format (defaults to current date).

    Returns:
        str: Confirmation message stating whether a new entry was created or
        an existing one was appended to.
    """
    if date_str:
        try:
            target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=datetime.UTC).date()
        except ValueError:
            target_date = datetime.datetime.now(datetime.UTC).astimezone().date()
    else:
        target_date = datetime.datetime.now(datetime.UTC).astimezone().date()

    target_date_str = target_date.strftime("%Y-%m-%d")
    filename = f"Journal Entry {target_date_str}.md"

    # Determine write target: check existing archive file or create in structured year/month dir
    existing_filepath = _resolve_journal_filepath(target_date_str)
    if existing_filepath and os.path.exists(existing_filepath):
        filepath = existing_filepath
    else:
        target_dir = _resolve_journal_dir(target_date)
        filepath = os.path.join(target_dir, filename)

    if tags is None:
        tags = []

    # Strip any '#' from tags for valid YAML
    clean_tags = [t.strip().lstrip("#") for t in tags]

    base_tags = [f"CY-{target_date.strftime('%Y/%m/%d')}"]
    for t in base_tags:
        if t not in clean_tags:
            clean_tags.append(t)

    append_content = f"\n\n---\n\n## Supplemental Entry ({datetime.datetime.now(datetime.UTC).astimezone().strftime('%H:%M')})\n### Vibe Check\n*Mood: {mood}*\n{vibe_check}\n\n### The Narrative\n{narrative}\n\n### Message in a Bottle\n*{message_in_a_bottle}*\n"

    body = f"""# Journal Entry {target_date_str}

## Vibe Check
*Mood: {mood}*
{vibe_check}

## The Narrative
{narrative}

## Message in a Bottle
*{message_in_a_bottle}*
"""
    file_content = render_frontmatter({"mood": mood, "tags": clean_tags}, body=body)

    from Evelyn.tools import memory_db
    direct_write = getattr(cfg, "JOURNAL_DIRECT_WRITE", True)

    if direct_write:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        if os.path.exists(filepath):
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(append_content)
            res = f"Supplemental entry successfully appended to {filepath}"
        else:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(file_content)
            res = f"Journal entry successfully written to {filepath}"
    else:
        from Evelyn.tools import terminal_agent
        if os.path.exists(filepath):
            res = terminal_agent.write_file(filepath, append_content, mode="append")
        else:
            res = terminal_agent.write_file(filepath, file_content, mode="overwrite")

    # Automatically mark unconsumed daytime ambient impressions as consumed for target date
    try:
        unconsumed = memory_db.get_unconsumed_ambient_impressions(target_date_str)
        if unconsumed:
            consumed_ids = [imp["id"] for imp in unconsumed]
            memory_db.mark_ambient_impressions_consumed(consumed_ids)
    except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
        logger_name = "evelyn.journal_manager"
        import logging
        logging.getLogger(logger_name).warning(f"Error marking ambient impressions consumed: {e}")

    return res


def read_journal_entry(date_str: str | None = None) -> str:
    """Read a single journal entry by date.

    Args:
        date_str: Optional date string in YYYY-MM-DD format. Defaults to today.

    Returns:
        str: The content of the journal entry, or a message if not found.
    """
    if not date_str:
        date_str = datetime.datetime.now(datetime.UTC).astimezone().date().strftime("%Y-%m-%d")

    filepath = _resolve_journal_filepath(date_str)
    if filepath and os.path.exists(filepath):
        try:
            with open(filepath, encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            return f"Error reading journal entry file: {e}"

    return f"No entry found for {date_str}."


def read_recent_journal_entries(days: int = 7) -> str:
    """Read journal entries from the last N days.

    Args:
        days: The number of recent days to read.

    Returns:
        str: The concatenated journal entries text.
    """
    entries = []
    today = datetime.datetime.now(datetime.UTC).astimezone().date()
    for i in range(days):
        date_obj = today - datetime.timedelta(days=i)
        date_str = date_obj.strftime("%Y-%m-%d")

        filepath = _resolve_journal_filepath(date_str)
        if filepath and os.path.exists(filepath):
            try:
                with open(filepath, encoding="utf-8") as f:
                    entries.append(f"--- Entry for {date_str} ---\n{f.read()}\n")
            except OSError:
                pass

    if not entries:
        return f"No journal entries found in the last {days} days."

    return "\n".join(entries)

