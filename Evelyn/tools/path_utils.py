# path_utils.py
# date created: 2026-08-28 12:25:00
# date modified: 2026-08-28 12:25:00
# tags: #utils, #paths, #vault, #posix, #security

"""
path_utils.py — Canonical Obsidian Vault Path Transformations & Security.

Exports:
    to_vault_relpath()      — Converts absolute or relative paths to POSIX vault-relative strings.
    to_vault_abspath()      — Resolves vault-relative path safely with traversal attack protection.
    normalize_vault_path()  — Returns normalized lower-case POSIX path for comparisons.
    is_vault_excluded()     — Checks if a path falls within configured ignore lists or hidden folders.

Key config: evelyn_config.py (VAULT_BASE_DIR, VAULT_READ_IGNORE)
See also: reference/engine_architecture.md
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Anchoring paths before importing evelyn_config
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
for _d in (ROOT_DIR, TOOLS_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import evelyn_config as cfg

VAULT_ROOT = Path(getattr(cfg, "VAULT_BASE_DIR", "/home/rathius/obsidian_vault")).resolve()

DEFAULT_EXCLUDE_DIRS = {
    d.lower() for d in getattr(cfg, "VAULT_READ_IGNORE", ["Archived", "Pending_Approvals", "Extracted", "Pending"])
} | {
    ".obsidian",
    "attachments",
    "bases",
    "templates",
}


def to_vault_relpath(path: str | Path) -> str:
    """Convert an absolute or relative path to a vault-relative POSIX string.

    Guarantees forward slashes ('/') for database indexing and Obsidian WikiLinks.

    Args:
        path: Path object or string.

    Returns:
        POSIX-compliant relative path string (e.g. 'Personal/Journal/Note.md').
    """
    p = Path(path)
    if p.is_absolute():
        try:
            rel = p.relative_to(VAULT_ROOT)
        except ValueError:
            # Fallback if path is outside vault root
            rel = p
    else:
        rel = p
    return rel.as_posix()


def to_vault_abspath(rel_path: str | Path) -> Path:
    """Convert a vault-relative path into an absolute Path object with traversal guards.

    Defends against path traversal attacks (e.g. '../../etc/passwd').

    Args:
        rel_path: Vault-relative path string or Path object.

    Returns:
        Absolute Path object inside the Obsidian Vault.

    Raises:
        ValueError: If the resolved path escapes the vault boundary.
    """
    p_str = str(rel_path).strip().lstrip("/\\")
    target_path = (VAULT_ROOT / p_str).resolve()

    # Verify target stays inside VAULT_ROOT
    try:
        target_path.relative_to(VAULT_ROOT)
    except ValueError:
        raise ValueError(f"Path traversal detected: '{rel_path}' resolves outside vault root '{VAULT_ROOT}'") from None

    return target_path


def normalize_vault_path(path: str | Path) -> str:
    """Return a normalized lower-case POSIX path string for safe comparisons.

    Args:
        path: Absolute or relative filesystem path.

    Returns:
        Normalized lower-case POSIX string.
    """
    return to_vault_relpath(path).lower()


def is_vault_excluded(path: str | Path, custom_excludes: set[str] | None = None) -> bool:
    """Determine whether a path is excluded from vault ingestion or indexing.

    Args:
        path: Target file or directory path (relative or absolute).
        custom_excludes: Optional set of lowercase directory names to ignore.

    Returns:
        True if the path or any parent component is excluded, False otherwise.
    """
    rel_posix = to_vault_relpath(path)
    parts = rel_posix.split("/")

    excludes = DEFAULT_EXCLUDE_DIRS if custom_excludes is None else (DEFAULT_EXCLUDE_DIRS | {c.lower() for c in custom_excludes})

    # Check for hidden files or dot directories (e.g. .trash, .obsidian, .git)
    for part in parts:
        if part.startswith(".") and part not in (".", ".."):
            return True
        if part.lower() in excludes:
            return True

    return False
