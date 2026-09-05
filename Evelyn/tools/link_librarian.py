# link_librarian.py
# date created: 2026-09-05 17:42:00
# date modified: 2026-09-05 17:38:06
# tags: #librarian, #links, #wikilinks, #ghost_links, #alias_hygiene, #attachments, #breadcrumbs

"""
link_librarian.py — Vault Link Integrity, Ghost Link Auditing & Alias Hygiene.

Exports:
    audit_document_links()          — Complete audit pass over markdown note links and frontmatter aliases.
    wrap_spurious_code_arrays()     — Wraps un-fenced NumPy arrays, tensors, and float lists in backticks.
    resolve_bare_attachments()      — Expands bare filename attachment links to full relative vault paths.
    prune_redundant_aliases()       — Cleans possessive ('s) and plural (s) aliases; converts doc types to tags.
    inject_parent_breadcrumbs()     — Injects upstream parent index callout into isolated chapter notes.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import evelyn_config as cfg
from Evelyn.tools import frontmatter_utils, string_utils

logger = logging.getLogger("evelyn.link_librarian")

DOC_TYPE_ALIAS_MAP = {
    "user manual": "user-manual",
    "specification sheet": "spec-sheet",
    "user guide": "user-guide",
    "datasheet": "datasheet",
    "spec sheet": "spec-sheet",
    "user manutial": "user-manual",
}


def wrap_spurious_code_arrays(text: str) -> tuple[bool, str]:
    """Wrap un-fenced floating-point and numeric arrays in code backticks.

    Matches numpy array outputs, torch tensors, and float lists:
        array([[0.33149648]], dtype=float32) -> `array([[0.33149648]], dtype=float32)`
        tensor([[-31893., ...]]) -> `tensor([[-31893., ...]])`
        [[0., 0., 0., 1., 0.]] -> `[[0., 0., 0., 1., 0.]]`

    Args:
        text: Markdown text (already masked by protect_code_blocks).

    Returns:
        tuple[bool, str]: (changed, updated_text)
    """
    changed = False

    # 0. Clean damaged backtick boundaries from prior ad-hoc runs: `array(`[[...]]`)` -> array([[...]])
    repaired_text, c0 = re.subn(
        r"`(array|tensor)\(`(\[\[[\s\S]*?\]\])`?\)`",
        r"\1(\2)",
        text,
    )
    if c0 > 0:
        changed = True
        text = repaired_text

    # 1. Matches array([[...]]) or tensor([[...]])
    arr_pattern = re.compile(
        r"(?<![`\w])((?:array|tensor)\s*\(\s*\[\[[\s\S]*?\]\](?:,\s*dtype=[\w\d]+)?\s*\))(?![`\w])",
        re.MULTILINE,
    )
    new_text, c1 = arr_pattern.subn(r"`\1`", text)
    if c1 > 0:
        changed = True
        text = new_text

    # 2. Protect newly created code backticks so float_pattern does not match inside them
    masked_text, local_placeholders = string_utils.protect_code_blocks(text)

    # 3. Matches bare numeric/float 2D lists: [[0. , 0.907, 0.093]] or [[-0.5, 1.2]] or [[1.5]]
    float_pattern = re.compile(
        r"(?<![`\w])(\[\[\s*[-+]?\d*\.?\d+(?:_?\d+)*(?:\s*,\s*[-+]?\d*\.?\d+(?:_?\d+)*)*\s*\]\])(?![`\w])",
        re.MULTILINE,
    )
    new_text, c2 = float_pattern.subn(r"`\1`", masked_text)
    if c2 > 0:
        changed = True
        masked_text = new_text

    text = string_utils.restore_code_blocks(masked_text, local_placeholders)
    return changed, text


def resolve_bare_attachments(text: str, vault_root: str | None = None) -> tuple[bool, str, int]:
    """Expand bare filename attachment links to full relative vault paths.

    Example: `[[Federal Tax 2024.pdf]]` -> `[[Attachments/Source Material/Financial/Federal Tax 2024.pdf]]`

    Args:
        text: Markdown text (already masked by protect_code_blocks).
        vault_root: Optional vault root directory.

    Returns:
        tuple[bool, str, int]: (changed, updated_text, resolved_count)
    """
    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
    attachments_dir = os.path.join(root, "Attachments")
    if not os.path.exists(attachments_dir):
        return False, text, 0

    # Build lookup map of bare attachment filenames -> relative vault paths
    attachment_exts = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp3", ".wav"}
    file_map: dict[str, str] = {}
    for dirpath, _, filenames in os.walk(attachments_dir):
        for f in filenames:
            ext = os.path.splitext(f)[1].lower()
            if ext in attachment_exts:
                abs_f = os.path.join(dirpath, f)
                rel_f = os.path.relpath(abs_f, root).replace("\\", "/")
                file_map[f.lower()] = rel_f

    if not file_map:
        return False, text, 0

    resolved_count = 0

    def _replace_att(m: re.Match) -> str:
        nonlocal resolved_count
        target = m.group(1).strip()
        alias_part = m.group(2) or ""

        # Skip if already path-qualified
        if "/" in target:
            return m.group(0)

        target_lower = target.lower()
        if target_lower in file_map:
            resolved_count += 1
            return f"[[{file_map[target_lower]}{alias_part}]]"

        return m.group(0)

    pattern = re.compile(r"\[\[([^|\]\n]+\.(?:pdf|png|jpg|jpeg|gif|webp|mp3|wav))(\|.*?)?\]\]", re.IGNORECASE)
    new_text = pattern.sub(_replace_att, text)
    return resolved_count > 0, new_text, resolved_count


def prune_redundant_aliases(
    aliases: list[str] | str,
    tags: list[str] | str,
    title: str = "",
) -> tuple[bool, list[str], list[str], list[str]]:
    """Prune redundant possessive ('s) and plural (s) aliases; migrate doc types to tags.

    Args:
        aliases: List of aliases or comma-separated string.
        tags: List of tags or comma-separated string.
        title: Document title.

    Returns:
        tuple[bool, list[str], list[str], list[str]]: (changed, clean_aliases, clean_tags, actions)
    """
    if isinstance(aliases, str):
        alias_list = [a.strip() for a in aliases.split(",") if a.strip()]
    else:
        alias_list = list(aliases or [])

    if isinstance(tags, str):
        tag_list = [t.strip().lstrip("#") for t in tags.split(",") if t.strip()]
    else:
        tag_list = list(tags or [])

    changed = False
    actions = []

    # 1. Base names that can be referenced natively by Obsidian link suffixes
    base_names = {title.strip().lower()} if title.strip() else set()
    for a in alias_list:
        base_names.add(a.strip().lower())

    final_aliases: list[str] = []
    for a in alias_list:
        clean_a = a.strip()
        lower_a = clean_a.lower()

        # Check doc-type migration
        if lower_a in DOC_TYPE_ALIAS_MAP:
            target_tag = DOC_TYPE_ALIAS_MAP[lower_a]
            if target_tag not in tag_list:
                tag_list.append(target_tag)
            changed = True
            actions.append(f"migrated_doc_type_alias:{clean_a}->#{target_tag}")
            continue

        # Check possessive 's or ’s pruning
        is_possessive = False
        for sfx in ("'s", "’s"):
            if lower_a.endswith(sfx):
                stem = lower_a[: -len(sfx)].strip()
                if stem in base_names:
                    is_possessive = True
                    break

        if is_possessive:
            changed = True
            actions.append(f"pruned_possessive_alias:{clean_a}")
            continue

        # Check redundant plural s pruning if stem is in base names
        if (
            len(lower_a) > 3
            and lower_a.endswith("s")
            and not lower_a.endswith("ss")
            and lower_a[:-1] in base_names
        ):
            changed = True
            actions.append(f"pruned_plural_alias:{clean_a}")
            continue

        final_aliases.append(clean_a)

    return changed, final_aliases, tag_list, actions


def inject_parent_breadcrumbs(
    body: str,
    path: str,
    vault_root: str | None = None,
) -> tuple[bool, str]:
    """Inject upstream parent index callouts into isolated chapter notes.

    Example: `> [!abstract] [[Book_index|📖 Book]]\n\n`

    Args:
        body: Markdown body of the note.
        path: Relative path of the document.
        vault_root: Vault root directory.

    Returns:
        tuple[bool, str]: (changed, updated_body)
    """
    if not path or "_index" in path.lower():
        return False, body

    dirpath = os.path.dirname(path)
    if not dirpath:
        return False, body

    folder_name = os.path.basename(dirpath)
    index_candidate_stem = f"{folder_name}_index"

    # Check if index candidate already referenced
    if index_candidate_stem.lower() in body.lower():
        return False, body

    # Check if index file exists on disk
    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
    possible_index_path = os.path.join(root, dirpath, f"{index_candidate_stem}.md")
    if not os.path.exists(possible_index_path):
        # Also check generic _index.md
        possible_index_path = os.path.join(root, dirpath, "_index.md")
        if not os.path.exists(possible_index_path):
            return False, body
        index_candidate_stem = "_index"

    breadcrumb = f"> [!abstract] [[{index_candidate_stem}|📖 {folder_name}]]\n\n"
    new_body = breadcrumb + body.lstrip()
    return True, new_body


def audit_document_links(
    content: str,
    path: str = "",
    vault_root: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Perform a complete link audit and normalization on a note.

    Args:
        content: Raw markdown text of the note.
        path: Relative path of the note.
        vault_root: Optional vault root directory.

    Returns:
        tuple[bool, str, dict[str, Any]]: (changed, updated_content, details)
    """
    if not content:
        return False, content, {"status": "empty"}

    fm_dict, body = frontmatter_utils.parse_frontmatter(content)
    changed = False
    details: dict[str, Any] = {
        "actions": [],
        "ghost_links_count": 0,
        "resolved_attachments": 0,
    }

    # 1. Alias & Doc-type hygiene
    title = str(fm_dict.get("title", ""))
    aliases = fm_dict.get("aliases", [])
    tags = fm_dict.get("tags", [])
    alias_changed, clean_aliases, clean_tags, alias_actions = prune_redundant_aliases(
        aliases=aliases,
        tags=tags,
        title=title,
    )
    if alias_changed:
        fm_dict["aliases"] = clean_aliases
        fm_dict["tags"] = clean_tags
        changed = True
        details["actions"].extend(alias_actions)

    # 2. Body processing: first repair fractured array backticks from prior unmasked scripts
    body, c_fix = re.subn(
        r"`(array|tensor)\(`\s*(\[\[[\s\S]*?\]\])\s*`\)`",
        r"`\1(\2)`",
        body,
    )
    if c_fix > 0:
        changed = True
        details["actions"].append("repaired_fractured_array_backticks")

    # Mask code blocks to protect authentic code
    masked_body, placeholders = string_utils.protect_code_blocks(body)
    try:
        # 2a. Wrap spurious code arrays (NumPy, tensor, float lists)
        c_arr, masked_body = wrap_spurious_code_arrays(masked_body)
        if c_arr:
            changed = True
            details["actions"].append("wrapped_spurious_arrays")

        # 2b. Resolve bare attachments
        c_att, masked_body, att_count = resolve_bare_attachments(masked_body, vault_root=vault_root)
        if c_att:
            changed = True
            details["resolved_attachments"] = att_count
            details["actions"].append(f"resolved_{att_count}_attachments")

        # 2c. Parent breadcrumbs for chapter notes
        c_crumb, masked_body = inject_parent_breadcrumbs(masked_body, path, vault_root=vault_root)
        if c_crumb:
            changed = True
            details["actions"].append("injected_parent_breadcrumb")

        # 2d. Count ghost links
        link_matches = re.findall(r"\[\[([^|\]\n#]+)(?:[|#][^\]\n]*)?\]\]", masked_body)
        root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
        ghost_count = 0
        for target in link_matches:
            target_clean = target.strip()
            if not target_clean or "/" in target_clean:
                continue
            # Simple heuristic check: if target doesn't exist on disk in current dir or vault
            note_path = os.path.join(root, f"{target_clean}.md")
            if not os.path.exists(note_path):
                ghost_count += 1
        details["ghost_links_count"] = ghost_count

    finally:
        restored_body = string_utils.restore_code_blocks(masked_body, placeholders)

    # Re-render frontmatter if changed
    if changed:
        from Evelyn.tools.format_librarian import normalize_flow_array

        rendered_fm = frontmatter_utils.render_frontmatter(fm_dict)
        # Enforce flow array rendering
        lines = rendered_fm.splitlines()
        final_lines = []
        for line in lines:
            if line.startswith("tags:"):
                final_lines.append(f"tags: {normalize_flow_array(fm_dict.get('tags', []))}")
            elif line.startswith("aliases:"):
                final_lines.append(f"aliases: {normalize_flow_array(fm_dict.get('aliases', []))}")
            else:
                final_lines.append(line)
        updated_fm = "\n".join(final_lines)
        updated_content = f"{updated_fm}\n{restored_body}" if restored_body else f"{updated_fm}\n"
    else:
        updated_content = content

    return changed, updated_content, details
