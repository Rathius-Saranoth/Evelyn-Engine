# dream_manager.py
# date created: 2026-08-29 07:45:00
# date modified: 2026-08-29 07:47:43
# tags: #dreams, #management, #entries, #vault, #protocols

"""
dream_manager.py — Dream entry creation and retrieval for Evelyn.

Manages Ricky's dream records, stored as structured markdown files inside
the Obsidian Vault (Dream Entries archive).

Preserves raw user descriptions intact, captures initial feelings/thoughts,
tags, and handles multiple dreams per calendar date by appending structured
sections with updated frontmatter.
"""

from __future__ import annotations

import datetime
import importlib
import os

import evelyn_config as cfg  # [[evelyn_config.py]]
from Evelyn.tools.frontmatter_utils import (
    parse_frontmatter,
    render_frontmatter,
    write_file_with_frontmatter,
)


def _resolve_dream_dir() -> str:
    """Find or create the canonical dream entries directory in the vault."""
    if not os.environ.get("PYTEST_CURRENT_TEST") and not getattr(cfg, "DISABLE_HOT_RELOAD", False):
        importlib.reload(cfg)
    vault_base = getattr(cfg, "VAULT_BASE_DIR", os.path.expanduser("~/obsidian_vault"))

    # Check for Dream Entries or Dream Journal/Dream Entries
    candidates = [
        os.path.join(vault_base, "Dream Journal", "Dream Entries"),
        os.path.join(vault_base, "Dream Entries"),
        os.path.join(vault_base, "Dream Journal"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c

    # Default fallback: Dream Journal/Dream Entries
    default_dir = os.path.join(vault_base, "Dream Journal", "Dream Entries")
    os.makedirs(default_dir, exist_ok=True)
    return default_dir


def _resolve_dream_filepath(date_str: str) -> str | None:
    """Find the filepath of a dream entry by date."""
    target_dir = _resolve_dream_dir()
    filename = f"Dream Entry {date_str}.md"
    filepath = os.path.join(target_dir, filename)
    if os.path.exists(filepath):
        return filepath

    # Also search parent or alternative candidate dirs
    vault_base = getattr(cfg, "VAULT_BASE_DIR", os.path.expanduser("~/obsidian_vault"))
    for alt_dir in [
        os.path.join(vault_base, "Dream Entries"),
        os.path.join(vault_base, "Dream Journal"),
        os.path.join(vault_base, "Dream Journal", "Dream Entries"),
    ]:
        alt_path = os.path.join(alt_dir, filename)
        if os.path.exists(alt_path):
            return alt_path

    return None


def create_dream_entry(
    title: str,
    description: str,
    date_str: str = "",
    feelings: str = "",
    tags: list[str] | str | None = None,
    analysis: str = "",
) -> str:
    """Compose and save a structured Dream Entry note for Ricky in the Obsidian vault.

    Args:
        title: Descriptive title for this specific dream scene/narrative.
        description: Raw, untouched dream description from Ricky.
        date_str: Optional date string (YYYY-MM-DD). Defaults to current date.
        feelings: Optional initial feelings, immediate waking thoughts, or mood.
        tags: Optional tag list or comma-separated tag string.
        analysis: Optional thematic or cross-referencing analysis notes.

    Returns:
        str: Confirmation message with the destination note filepath.
    """
    if not title.strip() and not description.strip():
        return "Error: write_dream_entry called with empty title and description. Aborted."

    now = datetime.datetime.now(datetime.UTC).astimezone()
    target_date_str = date_str.strip() if date_str and date_str.strip() else now.strftime("%Y-%m-%d")

    title_clean = title.strip() or "Untitled Dream"
    description_clean = description.strip()
    feelings_clean = feelings.strip()
    analysis_clean = analysis.strip()

    # Parse and clean tags
    clean_tags: list[str] = []
    if tags:
        if isinstance(tags, str):
            clean_tags = [t.strip().lstrip("#") for t in tags.split(",") if t.strip().lstrip("#")]
        elif isinstance(tags, (list, tuple, set)):
            clean_tags = [t.strip().lstrip("#") for t in tags if isinstance(t, str) and t.strip().lstrip("#")]

    # Automatic base date tag
    try:
        dt_parsed = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").replace(tzinfo=datetime.UTC)
        cy_tag = f"CY-{dt_parsed.strftime('%Y/%m/%d')}"
    except ValueError:
        cy_tag = f"CY-{now.strftime('%Y/%m/%d')}"

    if cy_tag not in clean_tags:
        clean_tags.insert(0, cy_tag)
    if "dream" not in [t.lower() for t in clean_tags]:
        clean_tags.append("dream")

    target_dir = _resolve_dream_dir()
    filename = f"Dream Entry {target_date_str}.md"
    filepath = os.path.join(target_dir, filename)

    now_timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

    # Construct the section block for this dream
    section_lines = [
        f"## Dream Title: {title_clean}",
        "",
        f"Dream Description: {description_clean}",
        "",
        f"Initial Feelings/Thoughts: {feelings_clean}",
    ]
    if analysis_clean:
        section_lines.extend(["", f"Analytical Notes: {analysis_clean}"])
    dream_section = "\n".join(section_lines)

    if os.path.exists(filepath):
        try:
            with open(filepath, encoding="utf-8") as f:
                existing_text = f.read()

            existing_meta, existing_body = parse_frontmatter(existing_text)
            existing_tags_raw = existing_meta.get("tags", [])
            if isinstance(existing_tags_raw, str):
                existing_tags = [t.strip().lstrip("#") for t in existing_tags_raw.split(",") if t.strip().lstrip("#")]
            elif isinstance(existing_tags_raw, list):
                existing_tags = [str(t).strip().lstrip("#") for t in existing_tags_raw if str(t).strip().lstrip("#")]
            else:
                existing_tags = []

            # Merge tags
            for t in clean_tags:
                if t not in existing_tags:
                    existing_tags.append(t)

            existing_meta["tags"] = existing_tags
            existing_meta["date modified"] = now_timestamp

            # Append new section to existing body
            new_body = existing_body.rstrip() + "\n\n" + dream_section + "\n"
            rendered_content = render_frontmatter(existing_meta, body=new_body)
            write_file_with_frontmatter(filepath, rendered_content)
            return f"Successfully appended dream '{title_clean}' to existing note: {filepath}"
        except OSError as e:
            return f"Error updating existing dream entry note: {e}"
    else:
        # Create new note
        new_meta = {
            "title": f"Dream Entry {target_date_str}",
            "aliases": [],
            "tags": clean_tags,
            "icon": [],
            "date created": now_timestamp,
            "date modified": now_timestamp,
        }
        new_body = f"# Dream Entry {target_date_str}\n\n{dream_section}\n"
        try:
            rendered_content = render_frontmatter(new_meta, body=new_body)
            write_file_with_frontmatter(filepath, rendered_content)
            return f"Successfully created new dream entry '{title_clean}': {filepath}"
        except OSError as e:
            return f"Error creating dream entry note: {e}"


def read_dream_entry(date_str: str = "") -> str:
    """Read a single dream entry note by date."""
    if not date_str or not date_str.strip():
        date_str = datetime.datetime.now(datetime.UTC).astimezone().strftime("%Y-%m-%d")

    filepath = _resolve_dream_filepath(date_str.strip())
    if filepath and os.path.exists(filepath):
        try:
            with open(filepath, encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            return f"Error reading dream entry file: {e}"

    return f"No dream entry note found for date {date_str}."
