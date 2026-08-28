# frontmatter_utils.py
# date created: 2026-08-28 12:25:00
# date modified: 2026-08-28 12:25:00
# tags: #frontmatter, #yaml, #markdown, #visual-pkm, #utils

"""
frontmatter_utils.py — Canonical Markdown YAML Frontmatter Authority.

Exports:
    format_yaml_array()             — Formats sequence values into single-line flow arrays [a, b].
    parse_frontmatter()             — Parses YAML frontmatter strictly from line 1 into (metadata_dict, body).
    render_frontmatter()            — Renders metadata dictionary into Visual PKM standard frontmatter.
    update_frontmatter_field()      — Line-targeted, non-destructive frontmatter field updater.
    write_file_with_frontmatter()   — Writes file with optional os.utime mtime/atime preservation.

Key config: PyYAML, string_utils.py
See also: reference/engine_architecture.md, .agents/rules/vault-note-style.md
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import Any

import yaml


def format_yaml_array(items: Iterable[str] | str | None) -> str:
    """Format an iterable or string of items into a clean Obsidian flow array [item1, item2].

    Enforces Visual PKM standard single-line flow arrays.

    Args:
        items: List/set/tuple of tag/alias strings, or comma-separated string.

    Returns:
        Flow array string (e.g. '[tag1, tag2]' or '[]').
    """
    if items is None:
        return "[]"

    if isinstance(items, str):
        raw = items.strip()
        if raw.startswith("[") and raw.endswith("]"):
            raw = raw[1:-1]
        tokens = [t.strip().strip("'\"#") for t in raw.split(",") if t.strip().strip("'\"#")]
    else:
        tokens = []
        for it in items:
            s = str(it).strip().strip("'\"#")
            if s:
                tokens.append(s)

    # Deduplicate while preserving insertion order
    seen = set()
    cleaned = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            cleaned.append(t)

    if not cleaned:
        return "[]"

    # Quote items containing spaces, commas, colons, or special chars
    quoted_items = []
    for item in cleaned:
        if any(c in item for c in (",", ":", " ", "[", "]", "{", "}", "#", "'", '"')):
            safe = item.replace('"', '\\"')
            quoted_items.append(f'"{safe}"')
        else:
            quoted_items.append(item)

    return f"[{', '.join(quoted_items)}]"


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter strictly from index 0 into a dictionary and return (metadata, body).

    Leniently accepts both single-line flow arrays and multiline list blocks,
    returning structured Python dict types and preserving the exact body text.

    Args:
        content: Raw markdown note string.

    Returns:
        Tuple of (metadata_dict, body_content). If no frontmatter is found, returns ({}, content).
    """
    if not content:
        return {}, ""

    # Strip optional Unicode BOM
    clean_content = content.lstrip("\ufeff")

    # Strict frontmatter check: must start at index 0 with '---'
    if not clean_content.startswith("---"):
        return {}, content

    # Match opening and closing '---'
    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", clean_content, re.DOTALL)
    if not match:
        return {}, content

    fm_raw = match.group(1)
    body = clean_content[match.end():]

    try:
        data = yaml.safe_load(fm_raw)
        if not isinstance(data, dict):
            data = {}
    except (yaml.YAMLError, ValueError, TypeError):
        # Fallback manual line parser if YAML contains unquoted syntax
        data = {}
        for line in fm_raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip()
                v = v.strip()
                if v.startswith("[") and v.endswith("]"):
                    items = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
                    data[k] = items
                else:
                    data[k] = v

    return data, body


def render_frontmatter(metadata: dict[str, Any], body: str = "") -> str:
    """Render metadata dictionary into Visual PKM compliant YAML frontmatter.

    Enforces single-line flow arrays for tags, aliases, and list attributes.

    Args:
        metadata: Key-value dictionary of metadata fields.
        body: Markdown body content to append.

    Returns:
        Complete markdown string with frontmatter header.
    """
    if not metadata:
        return body

    lines = ["---"]

    # Priority key ordering for clean Visual PKM notes
    priority_order = [
        "title",
        "aliases",
        "tags",
        "date created",
        "date modified",
        "rag_priority",
        "rag_pinned",
        "rag_exclude",
        "mood",
    ]

    written_keys = set()

    for k in priority_order:
        if k in metadata:
            val = metadata[k]
            written_keys.add(k)
            lines.append(_format_field_line(k, val))

    # Append remaining custom keys
    for k, val in metadata.items():
        if k not in written_keys:
            lines.append(_format_field_line(k, val))

    lines.append("---")

    fm_block = "\n".join(lines)
    if body:
        if body.startswith("\n"):
            return f"{fm_block}{body}"
        else:
            return f"{fm_block}\n{body}"
    return f"{fm_block}\n"


def _format_field_line(key: str, value: Any) -> str:
    """Helper to format a single frontmatter key-value pair."""
    ARRAY_KEYS = {"tags", "aliases", "keywords", "categories", "collections", "related"}

    if key.lower() in ARRAY_KEYS or isinstance(value, (list, set, tuple)):
        return f"{key}: {format_yaml_array(value)}"
    elif isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    elif value is None:
        return f"{key}: "
    else:
        # String, int, float
        val_str = str(value).strip()
        # Quote string if it contains YAML-breaking colons or brackets
        if "\n" in val_str:
            # Multiline text
            return f"{key}: >-\n  " + "\n  ".join(val_str.splitlines())
        elif any(c in val_str for c in (": ", "#", "[", "]")) and not (val_str.startswith('"') and val_str.endswith('"')):
            safe = val_str.replace('"', '\\"')
            return f'{key}: "{safe}"'
        return f"{key}: {val_str}"


def update_frontmatter_field(content: str, key: str, value: Any) -> str:
    """Update or insert a single frontmatter key-value field without corrupting comments or other fields.

    Uses line-targeted replacement to avoid destructive full YAML dumping.

    Args:
        content: Raw markdown document content.
        key: Metadata property name (e.g. 'date modified', 'tags', 'rag_pinned').
        value: New value to set.

    Returns:
        Updated document string.
    """
    clean_content = content.lstrip("\ufeff")

    # If no frontmatter exists, create one
    if not clean_content.startswith("---"):
        metadata = {key: value}
        return render_frontmatter(metadata, body=content)

    match = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n?", clean_content, re.DOTALL)
    if not match:
        metadata = {key: value}
        return render_frontmatter(metadata, body=content)

    fm_raw = match.group(1)
    body = clean_content[match.end():]
    fm_lines = fm_raw.splitlines()

    new_field_line = _format_field_line(key, value)
    key_lower = key.strip().lower()

    # Search for existing key in frontmatter lines
    found_idx = -1
    i = 0
    while i < len(fm_lines):
        line = fm_lines[i]
        line_s = line.strip()
        if ":" in line_s and not line_s.startswith("#"):
            k_cand = line_s.split(":", 1)[0].strip().lower()
            if k_cand == key_lower:
                found_idx = i
                # Check if next lines are multiline list items (- item)
                j = i + 1
                while j < len(fm_lines):
                    next_s = fm_lines[j].strip()
                    if next_s.startswith("- ") or (next_s.startswith("  ") and not ":" in next_s):
                        j += 1
                    else:
                        break
                # Replace range [i:j] with new_field_line
                fm_lines[i:j] = [new_field_line]
                break
        i += 1

    if found_idx == -1:
        # Key not found; append right before closing delimiter
        fm_lines.append(new_field_line)

    new_fm_raw = "\n".join(fm_lines)
    return f"---\n{new_fm_raw}\n---\n{body}"


def write_file_with_frontmatter(
    filepath: str,
    content: str,
    preserve_mtime: bool = False,
    encoding: str = "utf-8"
) -> bool:
    """Write content to file with optional mtime/atime preservation.

    Args:
        filepath: Target file path.
        content: Text content to write.
        preserve_mtime: If True, preserves previous filesystem mtime/atime.
        encoding: File character encoding.

    Returns:
        True on successful write, False on error.
    """
    try:
        prev_times = None
        if preserve_mtime and os.path.exists(filepath):
            st = os.stat(filepath)
            prev_times = (st.st_atime, st.st_mtime)

        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", encoding=encoding, newline="\n") as f:
            f.write(content)

        if prev_times is not None:
            os.utime(filepath, prev_times)

        return True
    except OSError as e:
        print(f"[FRONTMATTER_UTILS] Error writing {filepath}: {e}")
        return False
