# version.py
# date created: 2026-08-22 15:53:23
# date modified: 2026-09-04 06:56:26
# tags: #versioning, #release

"""
Evelyn Engine Version System.

Provides the single source of truth for engine versioning, zero-padded formatting
(000.000.000), parsing, and comparison logic.
"""

from __future__ import annotations

import re

# Zero-padded 3-digit semantic version: MAJOR.MINOR.PATCH
__version__ = "000.006.060"
__version_info__ = (0, 6, 60)
__version_name__ = "DevUI & ChatUI Button Handler Hardening, Dead Code Cleanup & Instant Split Wiring"
VERSION_NAME = __version_name__

VERSION_PATTERN = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def format_version(major: int, minor: int, patch: int) -> str:
    """Format integer major, minor, patch into zero-padded string (e.g. '000.004.000')."""
    return f"{major:03d}.{minor:03d}.{patch:03d}"


def parse_version(v_str: str) -> tuple[int, int, int]:
    """
    Parse a version string (either padded '000.004.000' or standard '0.4.0')
    into a tuple of integers (major, minor, patch).
    Raises ValueError/TypeError if string does not match version format.
    """
    if not isinstance(v_str, str):
        raise TypeError(f"Version must be a string, got {type(v_str).__name__}")

    clean_str = v_str.strip().lstrip("v")
    match = VERSION_PATTERN.match(clean_str)
    if not match:
        raise ValueError(f"Invalid version format: '{v_str}'. Expected '000.000.000' or 'X.Y.Z'")

    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_valid_version(v_str: str) -> bool:
    """Check whether a version string matches standard or padded version formatting."""
    try:
        parse_version(v_str)
        return True
    except (ValueError, TypeError):
        return False


def compare_versions(v1: str, v2: str) -> int:
    """
    Compare two version strings.
    Returns:
        -1 if v1 < v2
         0 if v1 == v2
         1 if v1 > v2
    """
    t1 = parse_version(v1)
    t2 = parse_version(v2)
    if t1 < t2:
        return -1
    elif t1 > t2:
        return 1
    return 0


def normalize_version(v_str: str) -> str:
    """Normalize any valid version string to the strict 3-digit zero-padded format."""
    major, minor, patch = parse_version(v_str)
    return format_version(major, minor, patch)
