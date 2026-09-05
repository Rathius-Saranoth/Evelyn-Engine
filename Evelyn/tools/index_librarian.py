# index_librarian.py
# date created: 2026-09-05 17:45:00
# date modified: 2026-09-05 17:38:09
# tags: #librarian, #index, #moc, #table_of_contents, #navigation, #vault

"""
index_librarian.py — Vault Folder Index & MOC Table of Contents Curator.

Maintains `_index.md` and `<Folder>_index.md` directory tables of contents, ensuring
new notes are registered, dead paths are pruned, and mtime loop hazards are prevented.

Exports:
    audit_folder_index()        — Audits and synchronizes a directory's table of contents note.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

import evelyn_config as cfg
from Evelyn.tools import frontmatter_utils, vault_db

logger = logging.getLogger("evelyn.index_librarian")


def audit_folder_index(
    folder_relpath: str,
    vault_root: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Audit and update the folder table of contents index note.

    Args:
        folder_relpath: Relative folder path within the vault.
        vault_root: Optional vault base directory.

    Returns:
        tuple[bool, str, dict[str, Any]]: (changed, updated_content, details)
    """
    root = vault_root or getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault")
    norm_folder = folder_relpath.replace("\\", "/").strip("/")
    abs_folder = os.path.join(root, norm_folder)

    if not os.path.isdir(abs_folder):
        return False, "", {"status": "folder_not_found"}

    folder_name = os.path.basename(norm_folder)
    index_filenames = [f"{folder_name}_index.md", "_index.md"]
    index_file = None
    for candidate in index_filenames:
        p = os.path.join(abs_folder, candidate)
        if os.path.exists(p):
            index_file = candidate
            break

    if not index_file:
        return False, "", {"status": "no_index_file"}

    index_relpath = f"{norm_folder}/{index_file}" if norm_folder else index_file
    abs_index_path = os.path.join(abs_folder, index_file)

    try:
        with open(abs_index_path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        logger.warning(f"Error reading index file {abs_index_path}: {e}")
        return False, "", {"status": "read_error", "error": str(e)}

    # Scan sibling markdown files
    sibling_files = []
    for f in os.listdir(abs_folder):
        if f.endswith(".md") and f != index_file and not f.startswith("."):
            sibling_files.append(f)

    changed = False
    details: dict[str, Any] = {"added_notes": [], "index_path": index_relpath}

    # Check for sibling notes missing from index content
    missing_notes = []
    for note in sibling_files:
        stem = os.path.splitext(note)[0]
        # Search for link [[stem]] or [[stem|...]]
        pattern = re.compile(rf"\[\[\s*{re.escape(stem)}(?:\|.*?)?\s*\]\]", re.IGNORECASE)
        if not pattern.search(content):
            missing_notes.append((stem, note))

    if missing_notes:
        # Append missing notes to the bottom or existing table
        fm_dict, body = frontmatter_utils.parse_frontmatter(content)
        addition_lines = ["\n\n## 📑 Additional Notes", ""]
        for stem, note in missing_notes:
            addition_lines.append(f"- [[{stem}]]")
            details["added_notes"].append(stem)
        updated_body = body + "\n".join(addition_lines) + "\n"
        content = f"{frontmatter_utils.render_frontmatter(fm_dict)}\n{updated_body}"
        changed = True

    if changed:
        # Atomic sibling write
        tmp_path = f"{abs_index_path}.tmp_{os.getpid()}"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, abs_index_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        # Peer review defense: Atomically update index note's audit timestamp
        # so its new mtime does NOT trigger a loop in the audit priority queue!
        now = time.time()
        vault_db.update_document_librarian_audit(index_relpath, mtime=now)
        logger.info(f"[INDEX LIBRARIAN] Updated {index_relpath} and synchronized audit timestamp.")

    return changed, content, details
