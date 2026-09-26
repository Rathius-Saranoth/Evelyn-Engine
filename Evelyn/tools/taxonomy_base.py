# taxonomy_base.py
# date created: 2026-09-26 08:38:21
# date modified: 2026-09-26 08:38:51
# tags: #taxonomy, #authority, #vocabulary, #layering

"""The read-only base layer beneath the local controlled vocabulary.

Three kinds of fact were living in one `master_tag_taxonomy` row: what a term means, who says
so, and how often this corpus uses it. Separating them is what makes a shareable vocabulary
possible at all.

* **What a term means, and who says so** lives in `taxonomy/base.json`, tracked in git. Every
  entry cites the published authority it came from - FAST, MeSH, AAT, LCGFT - so a reviewer can
  tell a professional cataloguer's decision from a local one, and a fresh clone starts with a
  working vocabulary instead of an empty table.
* **How often this corpus uses it** stays in `master_tag_taxonomy`, which is gitignored. The
  nightly census rewrites those counts constantly - 164 of them on 2026-09-26 - and there is
  deliberately no column here for it to write into.

The base is never written by the engine. Admission, retirement and the census all address the
local table; a local row naming a base term is an *override*, not a duplicate, and the base
entry survives it. That is the whole invariant: the authoritative list cannot be purged by
anything the engine does, so it can always be checked against.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from typing import Any

import evelyn_config as cfg
from Evelyn.tools.vault_db import get_db, init_db

logger = logging.getLogger("evelyn.taxonomy_base")

BASE_FILE = os.path.join(getattr(cfg, "BASE_DIR", os.getcwd()), "taxonomy", "base.json")
_VERSION_KEY = "base_version"


def _read_base_file(path: str | None = None) -> dict[str, Any]:
    """Parse the tracked base vocabulary file, or return an empty one."""
    target = path or BASE_FILE
    if not os.path.isfile(target):
        return {"version": "", "terms": [], "aliases": []}
    try:
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        # A malformed base file must not take the vocabulary down with it: the local layer
        # is a complete working vocabulary on its own, and this one is an addition to it.
        logger.error("[TAXONOMY BASE] Could not read %s: %s", target, exc)
        return {"version": "", "terms": [], "aliases": []}
    if not isinstance(data, dict):
        logger.error("[TAXONOMY BASE] %s is not an object; ignoring.", target)
        return {"version": "", "terms": [], "aliases": []}
    return data


def loaded_version() -> str:
    """Return the base file version currently materialised in the database."""
    init_db()
    con = get_db()
    try:
        row = con.execute(
            "SELECT value FROM base_taxonomy_meta WHERE key = ?", (_VERSION_KEY,)
        ).fetchone()
    except sqlite3.Error:
        return ""
    finally:
        con.close()
    return str(row["value"]) if row else ""


def sync_base_taxonomy(path: str | None = None, force: bool = False) -> dict[str, Any]:
    """Materialise the tracked base file into its tables, if the version has moved.

    A full replace rather than a merge: the file is the source of truth, so a term removed
    from it must leave the table too, and a rebuild is cheap at this size. Nothing here reads
    or writes `master_tag_taxonomy`, so local decisions are untouched by a base update.

    Args:
        path:  Override the base file location (tests).
        force: Rebuild even when the version matches.

    Returns:
        dict[str, Any]: `status`, `version`, `terms`, `aliases`.
    """
    data = _read_base_file(path)
    version = str(data.get("version") or "")
    terms = data.get("terms") or []
    aliases = data.get("aliases") or []

    if not version:
        return {"status": "absent", "version": "", "terms": 0, "aliases": 0}

    if not force and version == loaded_version():
        return {"status": "current", "version": version,
                "terms": count_base_terms(), "aliases": 0}

    now = time.time()
    init_db()
    con = get_db()
    try:
        with con:
            con.execute("DELETE FROM base_tag_taxonomy")
            con.execute("DELETE FROM base_tag_aliases")
            con.executemany(
                "INSERT OR REPLACE INTO base_tag_taxonomy "
                "(term, category, description, scheme, scheme_id, authorized_label, loaded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        str(t.get("term") or "").strip(),
                        str(t.get("category") or ""),
                        str(t.get("description") or ""),
                        str(t.get("scheme") or ""),
                        str(t.get("scheme_id") or ""),
                        str(t.get("authorized_label") or ""),
                        now,
                    )
                    for t in terms
                    if str(t.get("term") or "").strip()
                ],
            )
            con.executemany(
                "INSERT OR REPLACE INTO base_tag_aliases (alias, canonical, scheme, loaded_at) "
                "VALUES (?, ?, ?, ?)",
                [
                    (
                        str(a.get("alias") or "").strip(),
                        str(a.get("canonical") or "").strip(),
                        str(a.get("scheme") or ""),
                        now,
                    )
                    for a in aliases
                    if str(a.get("alias") or "").strip() and str(a.get("canonical") or "").strip()
                ],
            )
            con.execute(
                "INSERT OR REPLACE INTO base_taxonomy_meta (key, value) VALUES (?, ?)",
                (_VERSION_KEY, version),
            )
    except sqlite3.Error as exc:
        logger.error("[TAXONOMY BASE] Could not materialise base vocabulary: %s", exc)
        return {"status": "failed", "version": version, "terms": 0, "aliases": 0}
    finally:
        con.close()

    logger.info(
        "[TAXONOMY BASE] Loaded base vocabulary %s: %d term(s), %d alias(es).",
        version, len(terms), len(aliases),
    )
    return {"status": "loaded", "version": version,
            "terms": len(terms), "aliases": len(aliases)}




def count_base_terms() -> int:
    """How many terms the base layer holds."""
    init_db()
    con = get_db()
    try:
        row = con.execute("SELECT COUNT(*) AS n FROM base_tag_taxonomy").fetchone()
    except sqlite3.Error:
        return 0
    finally:
        con.close()
    return int(row["n"]) if row else 0


def is_base_term(term: str) -> bool:
    """Whether a term comes from the base layer, and so cannot be deleted locally."""
    clean = (term or "").strip()
    if not clean:
        return False
    init_db()
    con = get_db()
    try:
        row = con.execute(
            "SELECT 1 FROM base_tag_taxonomy WHERE term = ?", (clean,)
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        con.close()
    return row is not None
