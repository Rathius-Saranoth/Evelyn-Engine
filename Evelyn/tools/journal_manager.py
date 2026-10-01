# journal_manager.py
# date created: 2026-02-12 19:08:40
# date modified: 2026-10-01 17:55:24
# tags: #journal, #management, #entries, #logs, #protocols

"""
journal_manager.py — Journal entry creation and retrieval for Evelyn.

Manages Evelyn's personal journal, stored as dated markdown files inside
the Obsidian Vault structured year/month archive (JOURNAL_DIR/Journal Entries/YYYY/MM-ShortMonth/).

Journal entries are written directly to the structured archive. If an entry already exists
for the target date, new content is safely appended as a 'Supplemental Entry' section
to preserve multi-session reflections.

Key path constants:
  JOURNAL_DIR — Live journal base folder inside the Obsidian Vault.
  PENDING_DIR — Legacy quarantine folder checked as a fallback read path.

This module is imported and hot-reloaded by ``evelyn_tools.py``.
"""

import datetime
import importlib
import os
import sqlite3

import evelyn_config as cfg  # [[evelyn_config.py]]
from Evelyn.tools.frontmatter_utils import parse_frontmatter, render_frontmatter
from Evelyn.tools.tag_librarian import OCCURRED_PROPERTY, normalize_tag_format

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
    base_journal = getattr(
        cfg,
        "JOURNAL_DIR",
        os.path.join(
            getattr(cfg, "VAULT_BASE_DIR", os.path.expanduser("~/obsidian_vault")),
            getattr(cfg, "ASSISTANT_NAME", "Evelyn"),
            f"{getattr(cfg, 'ASSISTANT_NAME', 'Evelyn')}'s Journal",
        ),
    )

    # 1. Structured archive folder
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=datetime.UTC)
        year = dt.strftime("%Y")
        month_str = f"{dt.strftime('%m')}-{dt.strftime('%b')}"
        struct_path = os.path.join(base_journal, "Journal Entries", year, month_str, filename)
        if os.path.exists(struct_path):
            return struct_path
    except ValueError:
        pass

    # 2. Live vault root (legacy fallback)
    root_path = os.path.join(base_journal, filename)
    if os.path.exists(root_path):
        return root_path

    # 3. Pending quarantine folder
    pending_dir = getattr(cfg, "PENDING_DIR", PENDING_DIR)
    pending_path = os.path.join(pending_dir, filename)
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
    mode: str = "amend",
):
    """
    Writes or amends a journal entry markdown file in the structured Journal Entries archive.

    If a file for the target date already exists in the archive:
      - mode="amend" (default): Updates the entry in-place with the new reflection, merging
        new subject tags with existing frontmatter tags and preserving date properties.
      - mode="overwrite": Completely replaces the existing entry with the new content.
      - mode="append": Appends a legacy "Supplemental Entry" section.

    Tags are normalized to atomic lowercase subject terms (zero slashes,
    singular count nouns) and paired with the frontmatter properties
    (`type: [journal-entry]`, `occurred: YYYY-MM-DD`).

    Args:
        vibe_check: Brief intro capturing the emotional atmosphere of the entry.
        narrative: Core body text reflecting on events and emotions.
        message_in_a_bottle: A closing thought, wish, or intention for the future.
        mood: Single-word or short mood label (e.g. ``"Reflective"``). Written
            into the YAML frontmatter and Vibe Check section.
        tags: Optional list of atomic lowercase subject tags (zero slashes).
        date_str: Optional target date string in YYYY-MM-DD format (defaults to current date).
        mode: Write mode when entry exists ('amend', 'overwrite', or 'append'). Defaults to 'amend'.

    Returns:
        str: Confirmation message stating whether a new entry was created, amended, or overwritten.
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
    file_existed = bool(existing_filepath and os.path.exists(existing_filepath))
    if file_existed and existing_filepath:
        filepath = existing_filepath
    else:
        target_dir = _resolve_journal_dir(target_date)
        filepath = os.path.join(target_dir, filename)

    if tags is None:
        tags = []

    # Normalise to the canonical vault form (taxonomy §5) rather than only stripping '#',
    # which let model-supplied casing and underscores enter the vocabulary unchecked.
    clean_tags = [n for n in (normalize_tag_format(str(t)) for t in tags) if n]

    # The time axis is the `occurred` property, not a tag (taxonomy §3.8).
    occurred = target_date.strftime("%Y-%m-%d")

    direct_write = getattr(cfg, "JOURNAL_DIRECT_WRITE", True)

    if file_existed and mode == "amend":
        # Parse existing frontmatter to merge tags and retain metadata
        existing_fm = {}
        try:
            with open(filepath, encoding="utf-8") as f:
                existing_fm, _ = parse_frontmatter(f.read())
        except (OSError, ValueError):
            existing_fm = {}

        raw_existing_tags = existing_fm.get("tags") or []
        if isinstance(raw_existing_tags, str):
            raw_existing_tags = [t.strip() for t in raw_existing_tags.split(",") if t.strip()]
        existing_norm_tags = [n for n in (normalize_tag_format(str(t)) for t in raw_existing_tags) if n]
        merged_tags = list(dict.fromkeys([*existing_norm_tags, *clean_tags]))

        effective_mood = mood.strip() or str(existing_fm.get("mood", "Reflective"))
        fm_data = {
            "type": ["journal-entry"],
            "mood": effective_mood,
            "tags": merged_tags,
            OCCURRED_PROPERTY: occurred,
        }
        body = f"""# Journal Entry {target_date_str}

## Vibe Check
*Mood: {effective_mood}*
{vibe_check}

## The Narrative
{narrative}

## Message in a Bottle
*{message_in_a_bottle}*
"""
        file_content = render_frontmatter(fm_data, body=body)

        if direct_write:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(file_content)
            res = f"Journal entry for {target_date_str} successfully updated and amended in {filepath}"
        else:
            from Evelyn.tools import terminal_agent
            res = terminal_agent.write_file(filepath, file_content, mode="overwrite")

    elif file_existed and mode == "append":
        append_content = f"\n\n---\n\n## Supplemental Entry ({datetime.datetime.now(datetime.UTC).astimezone().strftime('%H:%M')})\n### Vibe Check\n*Mood: {mood}*\n{vibe_check}\n\n### The Narrative\n{narrative}\n\n### Message in a Bottle\n*{message_in_a_bottle}*\n"
        if direct_write:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(append_content)
            res = f"Supplemental entry successfully appended to {filepath}"
        else:
            from Evelyn.tools import terminal_agent
            res = terminal_agent.write_file(filepath, append_content, mode="append")

    else:
        # New entry or explicit overwrite
        body = f"""# Journal Entry {target_date_str}

## Vibe Check
*Mood: {mood}*
{vibe_check}

## The Narrative
{narrative}

## Message in a Bottle
*{message_in_a_bottle}*
"""
        file_content = render_frontmatter(
            {"type": ["journal-entry"], "mood": mood, "tags": clean_tags, OCCURRED_PROPERTY: occurred}, body=body
        )

        if direct_write:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(file_content)
            if file_existed:
                res = f"Journal entry for {target_date_str} successfully overwritten in {filepath}"
            else:
                res = f"Journal entry successfully written to {filepath}"
        else:
            from Evelyn.tools import terminal_agent
            res = terminal_agent.write_file(filepath, file_content, mode="overwrite")

    # Automatically mark unconsumed daytime ambient impressions as consumed for target date
    try:
        from Evelyn.tools import memory_db

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

