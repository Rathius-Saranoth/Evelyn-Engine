# format_librarian.py
# date created: 2026-09-05 17:40:00
# date modified: 2026-09-27 12:46:42
# tags: #librarian, #format, #frontmatter, #schema, #visual-pkm, #vault

"""
format_librarian.py — Vault Frontmatter, Schema & Visual PKM Normalizer.

Exports:
    audit_document_format()     — Audits and normalizes YAML frontmatter, flow arrays, and schema.
    audit_document_properties() — Audits and enforces document properties (type, motif, setting, event, occurred).
    normalize_flow_array()      — Formats string list into a safe, quoted single-line YAML flow array.
    clean_icon_brackets()       — Converts bracketed icon links into clean attachment paths.
    get_document_class()        — Reads and validates document class from type: frontmatter.
    FACET_PROFILE               — Registry of document classes and their required/forbidden facets.
    DOCUMENT_CLASSES            — Sorted list of registered document classes.
    REQUIRED, OPTIONAL, FORBIDDEN — Facet requirement states.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from Evelyn.tools import frontmatter_utils, string_utils

logger = logging.getLogger("evelyn.format_librarian")

MANDATORY_KEYS = ("title", "aliases", "tags", "date created", "date modified")

REQUIRED, OPTIONAL, FORBIDDEN = "required", "optional", "forbidden"

FACET_PROFILE: dict[str, dict[str, str]] = {
    "reference":     {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "guide":         {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "manual":        {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "overview":      {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "moc":           {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "list":          {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "log":           {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": OPTIONAL,  "time": REQUIRED},
    "report":        {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": OPTIONAL,  "time": REQUIRED},
    "journal-entry": {"motif": OPTIONAL,  "setting": OPTIONAL,  "event": OPTIONAL,  "time": REQUIRED},
    "dream":         {"motif": REQUIRED,  "setting": REQUIRED,  "event": OPTIONAL,  "time": REQUIRED},
    "creative":      {"motif": REQUIRED,  "setting": OPTIONAL,  "event": OPTIONAL,  "time": OPTIONAL},
    "media":         {"motif": OPTIONAL,  "setting": OPTIONAL,  "event": OPTIONAL,  "time": OPTIONAL},
    "recipe":        {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "notes":         {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "profile":       {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
    "stub":          {"motif": FORBIDDEN, "setting": FORBIDDEN, "event": FORBIDDEN, "time": OPTIONAL},
}
DOCUMENT_CLASSES = sorted(FACET_PROFILE)

FLOW_ARRAY_PROPERTIES = ("aliases", "tags", "type", "motif", "setting", "event")


def get_document_class(content: str) -> str:
    """Read and validate the document class from the `type:` frontmatter property.

    Returns a valid class from DOCUMENT_CLASSES, or '' if missing or unrecognized.
    """
    meta, _ = frontmatter_utils.parse_frontmatter(content)
    raw = meta.get("type", [])
    if isinstance(raw, str):
        types = [t.strip().strip("'\"[]") for t in raw.split(",") if t.strip().strip("'\"[]")]
    elif isinstance(raw, (list, tuple, set)):
        types = [str(t).strip().strip("'\"[]") for t in raw if str(t).strip().strip("'\"[]")]
    else:
        types = []
    for t in types:
        clean = string_utils.slugify(t, delimiter="-").lower()
        if clean in FACET_PROFILE:
            return clean
    return ""


def audit_document_properties(content: str, path: str = "") -> tuple[bool, str, dict[str, Any]]:
    """Audit and enforce document frontmatter properties against FACET_PROFILE rules.

    Decouples document metadata properties (type, motif, setting, event, occurred)
    from subject tag extraction:
    1. Validates `type:` property (document class).
    2. Strips forbidden facet properties for this document class (e.g. `motif`, `setting`, `event` on reference notes).
    3. Detects gaps for required facets (e.g. `missing_required_occurred` on `journal-entry`, `missing_required_motif` on `dream`).
    4. Strips legacy prefix tags from property values (e.g. `motif: [motif/combat]` -> `motif: [combat]`).

    Args:
        content: Raw markdown text of note.
        path: Optional relative path of document.

    Returns:
        tuple[bool, str, dict[str, Any]]: (changed, updated_content, details_dict)
    """
    if not content:
        return False, content, {"status": "empty"}

    fm_dict, body = frontmatter_utils.parse_frontmatter(content)
    if not fm_dict and not content.startswith("---"):
        return False, content, {"status": "no_frontmatter"}

    changed = False
    details: dict[str, Any] = {
        "property_fixes": [],
        "property_gaps": [],
    }

    doc_class = get_document_class(content)
    details["document_class"] = doc_class

    # 1. Clean legacy prefix values inside facet properties (e.g. motif: [motif/flying] -> [flying])
    for facet in ("type", "motif", "setting", "event"):
        if facet in fm_dict:
            raw_val = fm_dict[facet]
            if isinstance(raw_val, str):
                val_items = [p.strip().strip("'\"[]") for p in raw_val.split(",") if p.strip().strip("'\"[]")]
            elif isinstance(raw_val, (list, tuple, set)):
                val_items = [str(p).strip().strip("'\"[]") for p in raw_val if str(p).strip().strip("'\"[]")]
            else:
                val_items = []

            cleaned_items = []
            for item in val_items:
                s = item
                if s.startswith(f"{facet}/"):
                    s = s[len(facet) + 1:]
                if s:
                    cleaned_items.append(s)

            if cleaned_items != val_items or not isinstance(raw_val, list):
                fm_dict[facet] = cleaned_items
                changed = True
                details["property_fixes"].append(f"cleaned_{facet}_prefixes")

    # 2. Enforce FACET_PROFILE rules if doc_class is known
    if doc_class and doc_class in FACET_PROFILE:
        profile = FACET_PROFILE[doc_class]
        for facet, rule in profile.items():
            if facet == "time":
                occurred_val = str(fm_dict.get("occurred", "")).strip()
                if rule == REQUIRED and not occurred_val:
                    details["property_gaps"].append("missing_required_occurred")
            elif facet in ("motif", "setting", "event"):
                if rule == FORBIDDEN:
                    if facet in fm_dict:
                        del fm_dict[facet]
                        changed = True
                        details["property_fixes"].append(f"stripped_forbidden_{facet}")
                elif rule == REQUIRED:
                    val = fm_dict.get(facet)
                    if not val or val == [] or val == "[]":
                        details["property_gaps"].append(f"missing_required_{facet}")

    if not changed:
        return False, content, details

    # Re-render frontmatter
    fm_raw = frontmatter_utils.render_frontmatter(fm_dict)
    lines = fm_raw.splitlines()
    new_lines = []
    for line in lines:
        matched = False
        for key in FLOW_ARRAY_PROPERTIES:
            if line.startswith(f"{key}:"):
                val = fm_dict.get(key, [])
                if isinstance(val, (list, tuple, set)):
                    rendered_flow = normalize_flow_array(list(val))
                elif isinstance(val, str) and (val.startswith("[") or "," in val):
                    rendered_flow = normalize_flow_array(val)
                else:
                    rendered_flow = normalize_flow_array([str(val)]) if val else "[]"
                new_lines.append(f"{key}: {rendered_flow}")
                matched = True
                break
        if not matched:
            new_lines.append(line)

    updated_fm = "\n".join(new_lines)
    updated_content = f"{updated_fm}\n{body}" if body else f"{updated_fm}\n"
    return True, updated_content, details


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

    # 1. Title sanitization, inference & notation healing
    current_title = str(fm_dict.get("title", "")).strip() if "title" in fm_dict else ""
    if current_title:
        cleaned_existing = string_utils.clean_title(current_title)
        if cleaned_existing != current_title:
            fm_dict["title"] = cleaned_existing
            current_title = cleaned_existing
            changed = True
            details["format_fixes"].append("cleaned_title_extension")

    is_leaked = string_utils.is_notation_leak(current_title) if current_title else False
    if not current_title or is_leaked:
        # Attempt intelligent title recovery from body headings
        recovered_title = ""
        subheadings = re.findall(r"(?m)^(?:#{2,4})\s+(.*)$", body)
        for sh in subheadings:
            candidate = sh.strip()
            if len(candidate) > 2 and not string_utils.is_notation_leak(candidate):
                if candidate.isupper():
                    candidate = candidate.title()
                recovered_title = string_utils.clean_title(candidate)
                break

        if not recovered_title and path:
            stem = os.path.splitext(os.path.basename(path))[0]
            if not string_utils.is_notation_leak(stem):
                recovered_title = string_utils.clean_title(stem)
            else:
                num_match = re.match(r"^(\d+)", stem)
                parent_dir = os.path.basename(os.path.dirname(path)) if "/" in path else ""
                if num_match:
                    num = int(num_match.group(1))
                    recovered_title = f"{parent_dir} — Exercise {num}" if parent_dir else f"Exercise {num}"
                elif parent_dir:
                    recovered_title = f"{parent_dir} Note"

        if not recovered_title:
            first_h1 = re.search(r"^#\s+(.*)$", body, re.MULTILINE)
            if first_h1 and not string_utils.is_notation_leak(first_h1.group(1).strip()):
                recovered_title = string_utils.clean_title(first_h1.group(1).strip())

        if recovered_title and recovered_title != current_title:
            fm_dict["title"] = recovered_title
            changed = True
            if is_leaked:
                details["format_fixes"].append("repaired_notation_title")
            else:
                details["format_fixes"].append("inferred_title")

    # Synchronize top H1 if it contains a notation leak
    first_h1_match = re.search(r"^#\s+(.*)$", body, re.MULTILINE)
    if first_h1_match:
        h1_text = first_h1_match.group(1).strip()
        if string_utils.is_notation_leak(h1_text) and fm_dict.get("title"):
            new_h1_line = f"# {fm_dict['title']}"
            body = body[:first_h1_match.start()] + new_h1_line + body[first_h1_match.end():]
            changed = True
            details["format_fixes"].append("synchronized_h1_title")

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

    # 3. Normalize aliases into list and filter out notation leaks
    raw_aliases = fm_dict.get("aliases")
    if raw_aliases is None:
        fm_dict["aliases"] = []
    else:
        if isinstance(raw_aliases, str):
            alias_list = [a.strip() for a in raw_aliases.split(",") if a.strip()]
        elif isinstance(raw_aliases, list):
            alias_list = [str(a).strip() for a in raw_aliases if str(a).strip()]
        else:
            alias_list = []

        cleaned_aliases = [a for a in alias_list if not string_utils.is_notation_leak(a)]
        if cleaned_aliases != raw_aliases:
            fm_dict["aliases"] = cleaned_aliases
            changed = True
            if len(cleaned_aliases) < len(alias_list):
                details["format_fixes"].append("purged_notation_aliases")
            else:
                details["format_fixes"].append("normalized_aliases_list")

    # 4. Normalize facet properties (type, motif, setting, event) and enforce forbidden profile rules
    for facet in ("type", "motif", "setting", "event"):
        if facet in fm_dict:
            raw_val = fm_dict[facet]
            if isinstance(raw_val, str):
                val_items = [p.strip().strip("'\"[]") for p in raw_val.split(",") if p.strip().strip("'\"[]")]
            elif isinstance(raw_val, (list, tuple, set)):
                val_items = [str(p).strip().strip("'\"[]") for p in raw_val if str(p).strip().strip("'\"[]")]
            else:
                val_items = []

            cleaned_items = []
            for item in val_items:
                s = item
                if s.startswith(f"{facet}/"):
                    s = s[len(facet) + 1:]
                if s:
                    cleaned_items.append(s)

            if cleaned_items != raw_val or not isinstance(raw_val, list):
                fm_dict[facet] = cleaned_items
                changed = True
                details["format_fixes"].append(f"normalized_{facet}_property")

    doc_class = get_document_class(content)
    if doc_class and doc_class in FACET_PROFILE:
        profile = FACET_PROFILE[doc_class]
        for facet, rule in profile.items():
            if rule == FORBIDDEN and facet in fm_dict:
                del fm_dict[facet]
                changed = True
                details["format_fixes"].append(f"stripped_forbidden_{facet}")

    # 5. Clean icon brackets in frontmatter
    fm_raw = frontmatter_utils.render_frontmatter(fm_dict)
    icon_changed, fm_raw = clean_icon_brackets(fm_raw)
    if icon_changed:
        changed = True
        details["format_fixes"].append("cleaned_icon_brackets")

    # 6. Format aliases, tags, and facet properties into single-line flow arrays
    lines = fm_raw.splitlines()
    new_lines = []
    for line in lines:
        matched_flow = False
        for key in FLOW_ARRAY_PROPERTIES:
            if line.startswith(f"{key}:"):
                rendered_flow = normalize_flow_array(fm_dict.get(key, []))
                new_line = f"{key}: {rendered_flow}"
                if new_line != line:
                    changed = True
                    details["format_fixes"].append(f"flow_array_{key}")
                new_lines.append(new_line)
                matched_flow = True
                break
        if not matched_flow:
            new_lines.append(line)

    if not changed:
        return False, content, details

    updated_fm = "\n".join(new_lines)
    updated_content = f"{updated_fm}\n{body}" if body else f"{updated_fm}\n"

    return True, updated_content, details
