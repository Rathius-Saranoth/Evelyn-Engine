#!/usr/bin/env python3
# check_privacy_boundary.py
# date created: 2026-09-20 00:00:00
# date modified: 2026-09-20 07:41:52
# tags: #hygiene, #privacy, #identity, #gate

"""
check_privacy_boundary.py — Deterministic gate for the privacy boundary (AGENTS.md §4).

Tracked files are backed up to a public remote. Real identities and machine-specific
absolute paths must never reach version control. That rule previously relied on an
agent remembering it while composing prose, which failed repeatedly across sessions:
the leak path is never a copied file, it is a changelog entry or comment quoting the
real example the author was looking at.

This makes the rule mechanical. Protected values are read from the gitignored .env via
evelyn_config, so the repository never contains the terms it is being scanned for.

Two severity tiers:

  BLOCKING — real identities: the operator name, legacy aliases, and the private names
  listed in EVELYN_PRIVATE_NAMES. These have no legitimate reason to be committed and
  exit 1.

  ADVISORY — machine-specific absolute paths (home directory, vault root). Reported but
  non-blocking, because AGENTS.md deliberately specifies absolute interpreter and
  database paths in its operational instructions. Pass --strict-paths to enforce them.

Run standalone or as a stage of check_code_hygiene.py.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import evelyn_config as cfg

# Files that legitimately describe the boundary itself.
EXEMPT_PATHS = {".env.example", "scripts/check_privacy_boundary.py"}

SCAN_SUFFIXES = (
    ".py", ".md", ".html", ".js", ".css", ".json", ".yaml", ".yml",
    ".toml", ".txt", ".sh", ".ps1", ".http", ".service",
)


def protected_terms() -> list[tuple[str, str]]:
    """Collect (label, term) pairs to scan for, sourced from the gitignored .env.

    Returns:
        list[tuple[str, str]]: Non-empty, de-duplicated protected terms.
    """
    terms: list[tuple[str, str]] = []

    user_name = (getattr(cfg, "USER_NAME", "") or "").strip()
    # "User" is the generic fallback shipped in the repo, not a real identity.
    if user_name and user_name.lower() not in {"user", ""}:
        terms.append(("operator name", user_name))

    terms.extend(
        ("legacy alias", alias.strip())
        for alias in getattr(cfg, "USER_LEGACY_ALIASES", []) or []
        if alias.strip()
    )
    terms.extend(
        ("private name", name.strip())
        for name in getattr(cfg, "PRIVATE_IDENTITY_NAMES", []) or []
        if name.strip()
    )

    vault = (getattr(cfg, "VAULT_BASE_DIR", "") or "").strip()
    if vault and vault.startswith("/"):
        terms.append(("vault abspath", vault))

    home = os.path.expanduser("~")
    if home and home.startswith("/") and home != "/":
        terms.append(("home abspath", home))

    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for label, term in terms:
        if term.lower() not in seen:
            seen.add(term.lower())
            unique.append((label, term))
    return unique


def tracked_files() -> list[str]:
    """List files tracked by git, limited to scannable text types."""
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=_PROJECT_ROOT,
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    return [f for f in out.split("\n") if f.strip() and f.endswith(SCAN_SUFFIXES)]


def scan() -> list[tuple[str, int, str, str, str]]:
    """Scan tracked files for protected terms.

    Returns:
        list: (path, line_no, label, term, line_text) for each violation.
    """
    terms = protected_terms()
    if not terms:
        return []

    patterns = [
        (label, term, re.compile(rf"(?<![\w-]){re.escape(term)}(?![\w-])", re.IGNORECASE))
        for label, term in terms
    ]
    violations: list[tuple[str, int, str, str, str]] = []

    for rel in tracked_files():
        if rel in EXEMPT_PATHS:
            continue
        path = os.path.join(_PROJECT_ROOT, rel)
        try:
            with open(path, encoding="utf-8", errors="ignore") as handle:
                lines = handle.readlines()
        except OSError:
            continue
        for idx, line in enumerate(lines, start=1):
            for label, term, pattern in patterns:
                if pattern.search(line):
                    violations.append((rel, idx, label, term, line.rstrip()[:120]))
    return violations


BLOCKING_LABELS = {"operator name", "legacy alias", "private name"}


def main() -> int:
    strict_paths = "--strict-paths" in sys.argv
    terms = protected_terms()
    if not terms:
        print("⚠  No protected terms configured — set EVELYN_USER_NAME / "
              "EVELYN_PRIVATE_NAMES in .env to enable this gate.")
        return 0

    print(f"Scanning tracked files for {len(terms)} protected term(s)")
    violations = scan()
    blocking = [v for v in violations if v[2] in BLOCKING_LABELS]
    advisory = [v for v in violations if v[2] not in BLOCKING_LABELS]

    if advisory:
        files = len({v[0] for v in advisory})
        note = "ENFORCED" if strict_paths else "advisory"
        print(f"\n·  {len(advisory)} machine-specific path reference(s) across {files} "
              f"file(s) [{note}]")
        if strict_paths:
            for rel, line_no, label, term, _text in advisory[:40]:
                print(f"  {rel}:{line_no}  [{label}: {term}]")

    if not blocking:
        print("\n✔ Privacy boundary clean — no real identity reaches version control.")
        return 1 if (strict_paths and advisory) else 0

    by_file: dict[str, int] = {}
    for rel, _, _, _, _ in blocking:
        by_file[rel] = by_file.get(rel, 0) + 1

    print(f"\n✖ {len(blocking)} identity violation(s) across {len(by_file)} tracked file(s):\n")
    for rel, line_no, label, term, text in blocking[:60]:
        print(f"  {rel}:{line_no}  [{label}: {term}]")
        print(f"      {text}")
    if len(blocking) > 60:
        print(f"  ... and {len(blocking) - 60} more")
    print("\nProtected values live in the gitignored .env. Replace literals with "
          "cfg.USER_NAME, or use a neutral placeholder.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
