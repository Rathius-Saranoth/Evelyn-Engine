# format_librarian.py
# date created: 2026-09-05 17:40:00
# date modified: 2026-09-05 17:36:52
# tags: #librarian, #format, #frontmatter, #schema, #visual-pkm, #vault

"""
format_librarian.py — Vault Frontmatter, Schema & Visual PKM Normalizer.

Exports:
    audit_document_format()     — Audits and normalizes YAML frontmatter, flow arrays, and schema.
    normalize_flow_array()      — Formats string list into a safe, quoted single-line YAML flow array.
    clean_icon_brackets()       — Converts bracketed icon links into clean attachment paths.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from Evelyn.tools import frontmatter_utils, string_utils

logger = logging.getLogger("evelyn.format_librarian")

MANDATORY_KEYS = ("title", "aliases", "tags", "date created", "date modified")


def normalize_flow_array(items: list[str] | str) -> str:
    """Render a list of strings as a clean, single-line YAML flow array.

    Safely quotes elements that contain colons, commas, brackets, or spaces.
    Example: ['hello', 'world: part 2', 'foo, bar'] -> ['hello', "world: part 2", "foo, bar"]

    Args:
        items: List of string elements, or raw string.

    Returns:
        str: Single-line YAML flow array string, e.g. '["a", "b", "c"]'.
    """
    if isinstance(items, str):
        # If already bracketed flow array, parse elements
        clean = items.strip()
        if clean.startswith("[") and clean.endswith("]"):
            inner = clean[1:-1].strip()
            if not inner:
                return "[]"
            # Simple comma split respecting quotes
            parts = [p.strip().strip("'\"") for p in re.split(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", inner)]
            items = [p for p in parts if p]
        else:
            items = [p.strip() for p in items.split(",") if p.strip()]

    if not items:
        return "[]"

    formatted_elements = []
    for item in items:
        s = str(item).strip()
        if not s:
            continue
        # Check if quoting is needed (contains colon, comma, quote, hashtag, brackets)
        needs_quotes = any(c in s for c in (':', ',', '"', "'", '[', ']', '{', '}', '#')) or s.startswith('-')
        if needs_quotes:
            escaped = s.replace('"', '\\"')
            formatted_elements.append(f'"{escaped}"')
        else:
            # Check if alphanumeric or dashed/underscored
            if re.match(r"^[A-Za-z0-9/_-]+$", s):
                formatted_elements.append(s)
            else:
                escaped = s.replace('"', '\\"')
                formatted_elements.append(f'"{escaped}"')

    return "[" + ", ".join(formatted_elements) + "]"


def clean_icon_brackets(frontmatter_text: str) -> tuple[bool, str]:
    """Clean bracketed icon wikilinks in frontmatter into unbracketed file paths.

    Example: `icon: ["[[icon.png]]"]` -> `icon: "Attachments/Icons/icon.png"`

    Args:
        frontmatter_text: Raw YAML frontmatter string.

    Returns:
        tuple[bool, str]: (changed, updated_frontmatter)
    """
    if not frontmatter_text or "icon:" not in frontmatter_text:
        return False, frontmatter_text

    pattern = re.compile(r'icon:\s*\[?["\']?\[\[(.*?)\]\]["\']?\]?')

    def _repl(m: re.Match) -> str:
        icon_path = m.group(1).strip()
        if not icon_path.startswith("Attachments/"):
            icon_path = f"Attachments/Icons/{os.path.basename(icon_path)}"
        return f'icon: "{icon_path}"'

    new_fm, count = pattern.subn(_repl, frontmatter_text)
    return count > 0, new_fm


def audit_document_format(content: str, path: str = "") -> tuple[bool, str, dict[str, Any]]:
    """Audit and normalize YAML frontmatter, flow arrays, and Visual PKM schema.

    Args:
        content: Raw markdown text of note.
        path: Optional relative path of document for title inference.

    Returns:
        tuple[bool, str, dict[str, Any]]: (changed, updated_content, details_dict)
    """
    if not content:
        return False, content, {"status": "empty"}

    fm_dict, body = frontmatter_utils.parse_frontmatter(content)
    changed = False
    details: dict[str, Any] = {"format_fixes": []}

    # 1. Title inference if missing
    if "title" not in fm_dict or not str(fm_dict["title"]).strip():
        inferred_title = ""
        if path:
            stem = os.path.splitext(os.path.basename(path))[0]
            inferred_title = string_utils.clean_title(stem)
        else:
            first_h1 = re.search(r"^#\s+(.*)$", body, re.MULTILINE)
            if first_h1:
                inferred_title = first_h1.group(1).strip()
        if inferred_title:
            fm_dict["title"] = inferred_title
            changed = True
            details["format_fixes"].append("inferred_title")

    # 2. Normalize tags into list
    raw_tags = fm_dict.get("tags")
    if raw_tags is None:
        fm_dict["tags"] = []
    elif isinstance(raw_tags, str):
        parsed_tags = [t.strip().lstrip("#") for t in raw_tags.split(",") if t.strip()]
        if parsed_tags != raw_tags:
            fm_dict["tags"] = parsed_tags
            changed = True
            details["format_fixes"].append("normalized_tags_list")

    # 3. Normalize aliases into list
    raw_aliases = fm_dict.get("aliases")
    if raw_aliases is None:
        fm_dict["aliases"] = []
    elif isinstance(raw_aliases, str):
        parsed_aliases = [a.strip() for a in raw_aliases.split(",") if a.strip()]
        if parsed_aliases != raw_aliases:
            fm_dict["aliases"] = parsed_aliases
            changed = True
            details["format_fixes"].append("normalized_aliases_list")

    # 4. Clean icon brackets in frontmatter
    fm_raw = frontmatter_utils.render_frontmatter(fm_dict)
    icon_changed, fm_raw = clean_icon_brackets(fm_raw)
    if icon_changed:
        changed = True
        details["format_fixes"].append("cleaned_icon_brackets")

    # 5. Format aliases and tags into single-line flow arrays
    lines = fm_raw.splitlines()
    new_lines = []
    for line in lines:
        if line.startswith("tags:"):
            rendered_flow = normalize_flow_array(fm_dict.get("tags", []))
            new_line = f"tags: {rendered_flow}"
            if new_line != line:
                changed = True
                details["format_fixes"].append("flow_array_tags")
            new_lines.append(new_line)
        elif line.startswith("aliases:"):
            rendered_flow = normalize_flow_array(fm_dict.get("aliases", []))
            new_line = f"aliases: {rendered_flow}"
            if new_line != line:
                changed = True
                details["format_fixes"].append("flow_array_aliases")
            new_lines.append(new_line)
        else:
            new_lines.append(line)

    if not changed:
        return False, content, details

    updated_fm = "\n".join(new_lines)
    updated_content = f"{updated_fm}\n{body}" if body else f"{updated_fm}\n"

    return True, updated_content, details
