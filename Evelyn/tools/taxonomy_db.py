# taxonomy_db.py
# date created: 2026-09-19 00:00:00
# date modified: 2026-09-19 21:05:42
# tags: #taxonomy, #tags, #vocabulary, #authority-control, #sqlite

"""taxonomy_db.py — Master Tag Taxonomy registry (shared controlled vocabulary).

The taxonomy is **not vault data and not memory data** — it is the controlled
vocabulary that governs both (`.agents/rules/vault-tag-taxonomy.md` §0). Every
consumer reads and proposes against this one registry:

    tag_librarian.py    — vault note classification
    fact_extractor.py   — memory fact tagging
    visual_indexer.py   — media tag alignment
    evelyn_server.py    — taxonomy telemetry

Exports:
    get_master_tags()     — All registered terms, highest usage first.
    upsert_master_tag()   — Register or update a term.
    delete_master_tag()   — Remove a term from the registry.
    get_aliases()         — All recorded UF equivalences.
    canonicalize_tags()   — Map a tag list through the alias table (cached) to preferred forms.

Storage note: the `master_tag_taxonomy` table physically lives in the vault store
and its DDL remains in `vault_db.init_db()` alongside that store's other tables.
Only the access API lives here — ownership is expressed in code rather than by
relocating the table, which would add a fourth database to serve a naming concern.
Connection handling is reused from `vault_db` rather than duplicated.

See also: .agents/rules/vault-tag-taxonomy.md §6 (Vocabulary Control)
"""

import sqlite3
import time
from typing import Any

from Evelyn.tools.vault_db import get_db, init_db


def get_master_tags() -> list[dict[str, Any]]:
    """Return every registered term with its category, description, and usage count.

    Ordered by usage_count DESC so high-frequency terms surface first.

    Returns:
        list[dict[str, Any]]: Registered taxonomy terms.
    """
    init_db()
    con = get_db()
    try:
        rows = con.execute(
            "SELECT * FROM master_tag_taxonomy ORDER BY usage_count DESC, category ASC, tag ASC"
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def upsert_master_tag(tag: str, category: str = "", description: str = "", usage_count: int = 0) -> None:
    """Register a term in the controlled vocabulary, or update an existing one.

    An existing description is preserved when the incoming one is empty, so a
    usage-count refresh never blanks curated scope text.

    Args:
        tag: The term, already in §5 format (e.g. 'tech/python').
        category: Top-level category (e.g. 'tech').
        description: Short scope statement.
        usage_count: Current number of resources using this term.
    """
    init_db()
    con = get_db()
    now = time.time()
    try:
        con.execute("""
            INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(tag) DO UPDATE SET
                category = excluded.category,
                description = CASE WHEN excluded.description != '' THEN excluded.description ELSE master_tag_taxonomy.description END,
                usage_count = excluded.usage_count,
                updated_at = excluded.updated_at
        """, (tag, category, description, usage_count, now, now))
        con.commit()
    finally:
        con.close()


def delete_master_tag(tag: str) -> None:
    """Remove a term from the controlled vocabulary.

    Args:
        tag: The term to remove.
    """
    init_db()
    con = get_db()
    try:
        con.execute("DELETE FROM master_tag_taxonomy WHERE tag = ?", (tag,))
        con.commit()
    finally:
        con.close()


def get_aliases() -> dict[str, str]:
    """Return every recorded UF equivalence.

    Returns:
        dict[str, str]: alias -> canonical.
    """
    init_db()
    con = get_db()
    try:
        rows = con.execute("SELECT alias, canonical FROM master_tag_aliases").fetchall()
    finally:
        con.close()
    return {r["alias"]: r["canonical"] for r in rows}


# Alias lookups sit on the hot path for every tag written anywhere in the engine, so the
# map is cached in-process and invalidated whenever an alias is recorded.
_ALIAS_CACHE: dict[str, str] | None = None


def invalidate_alias_cache() -> None:
    """Drop the cached alias map so the next lookup re-reads the registry."""
    global _ALIAS_CACHE
    _ALIAS_CACHE = None


def canonicalize_tags(tags: list[str]) -> list[str]:
    """Map tags through recorded `UF` equivalences to their preferred forms.

    This is what makes a collapse permanent. Recording an alias without consulting it on
    write only renames existing data — the next extraction re-mints the retired variant
    and the vocabulary drifts back (taxonomy §6.2).

    Args:
        tags: Tag strings, already normalized.

    Returns:
        list[str]: Canonical forms, de-duplicated, order preserved.
    """
    global _ALIAS_CACHE
    if _ALIAS_CACHE is None:
        try:
            _ALIAS_CACHE = get_aliases()
        except sqlite3.Error:
            _ALIAS_CACHE = {}

    out: list[str] = []
    for tag in tags:
        mapped = _ALIAS_CACHE.get(tag, tag)
        if mapped and mapped not in out:
            out.append(mapped)
    return out
