# tag_entities.py
# date created: 2026-09-26 17:16:45
# date modified: 2026-09-26 17:20:01
# tags: #taxonomy, #authority, #names, #entities

"""The name register: the third answer to a tag admission proposal.

A proposed term that turns out to be a name had no correct action. **Admitting** it puts a
shop, a product or a person into a controlled vocabulary of subjects, where the classifier
will then apply it to unrelated notes. **Rejecting** it is permanent since `000.006.231`, and
a rejection is scoped to the *word* - so turning down a clothing retailer called `Historical`
would also mean the ordinary adjective could never be admitted.

Authority control has always kept these apart for exactly this reason: LCSH beside LCNAF, and
four of FAST's nine facets are name facets. Two registers, two namespaces, and the collision
stops being a conflict - a name and a subject may share a string because they are not
competing for the same slot.

A registration is **not** a rejection. It is reversible (`forget_entity`), it records what the
corpus keeps asking for rather than going quiet, and it carries a `label` because the term is
often lossy - one shop reached the queue as `historical`, its second word dropped by the split
that extracted it.

This is the one taxonomy table that can hold personal names, so it is local and gitignored and
must never reach the tracked base vocabulary (§4).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from Evelyn.tools.vault_db import get_db, init_db

logger = logging.getLogger("evelyn.tag_entities")

ENTITY_KINDS = ("organization", "product", "work", "person", "place", "other")


def record_entity(term: str, label: str = "", kind: str = "other", note: str = "") -> bool:
    """Register a term as a name rather than a subject.

    Args:
        term:  The proposed term, in canonical §5 form.
        label: The full name, when the term has lost part of it.
        kind:  One of `ENTITY_KINDS`; anything else is stored as `other`.
        note:  Free text from the reviewer.

    Returns:
        bool: True when the register holds the term afterwards.
    """
    clean = (term or "").strip().lower()
    if not clean:
        return False
    if kind not in ENTITY_KINDS:
        kind = "other"

    init_db()
    con = get_db()
    try:
        with con:
            con.execute(
                "INSERT INTO tag_entities (term, label, kind, note, request_count, recorded_at) "
                "VALUES (?, ?, ?, ?, 1, ?) "
                "ON CONFLICT(term) DO UPDATE SET "
                # An empty field means "leave it alone", the convention `upsert_master_tag`
                # already uses: re-recording a name must not blank a label someone typed.
                "  label = CASE WHEN excluded.label <> '' THEN excluded.label ELSE tag_entities.label END,"
                "  kind = excluded.kind,"
                "  note = CASE WHEN excluded.note <> '' THEN excluded.note ELSE tag_entities.note END",
                (clean, (label or "").strip(), kind, (note or "").strip(), time.time()),
            )
    except sqlite3.Error as exc:
        logger.warning("[TAG ENTITIES] Could not record '%s': %s", clean, exc)
        return False
    logger.info("[TAG ENTITIES] Recorded '%s' as a name (%s).", clean, kind)
    return True


def is_entity(term: str) -> bool:
    """Whether a term is registered as a name."""
    clean = (term or "").strip().lower()
    if not clean:
        return False
    init_db()
    con = get_db()
    try:
        row = con.execute("SELECT 1 FROM tag_entities WHERE term = ?", (clean,)).fetchone()
    except sqlite3.Error:
        # Cannot prove it is a name, so suppress nothing: one extra proposal is a review,
        # a wrongly suppressed term is invisible.
        return False
    finally:
        con.close()
    return row is not None


def get_entities() -> list[dict[str, Any]]:
    """Every registered name, most recently recorded first."""
    init_db()
    con = get_db()
    try:
        rows = con.execute(
            "SELECT * FROM tag_entities ORDER BY recorded_at DESC, term ASC"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    return [dict(r) for r in rows]


def entity_terms() -> set[str]:
    """Every registered name as a lookup set, for the admission filter."""
    init_db()
    con = get_db()
    try:
        rows = con.execute("SELECT term FROM tag_entities").fetchall()
    except sqlite3.Error:
        return set()
    finally:
        con.close()
    return {str(r["term"]) for r in rows}


def record_entity_request(term: str) -> int:
    """Count one more request for a term already registered as a name.

    The counterpart to `record_rejected_request`. A name the corpus keeps nominating is
    either a name it keeps mentioning - which is fine and expected - or a word that also has
    a subject sense the vocabulary is missing. The number is what tells those apart, and
    going quiet loses the distinction.

    Args:
        term: The registered name being requested again.

    Returns:
        int: The request count afterwards, or 0 if no row matched.
    """
    clean = (term or "").strip().lower()
    if not clean:
        return 0
    init_db()
    con = get_db()
    try:
        with con:
            con.execute(
                "UPDATE tag_entities SET request_count = request_count + 1 WHERE term = ?",
                (clean,),
            )
        row = con.execute(
            "SELECT request_count AS n FROM tag_entities WHERE term = ?", (clean,)
        ).fetchone()
    except sqlite3.Error:
        return 0
    finally:
        con.close()
    return int(row["n"]) if row else 0


def forget_entity(term: str) -> bool:
    """Remove a name from the register, so the word can be proposed as a subject again.

    What makes this different from a rejection: it is meant to be undone. A reviewer who
    marked a word as a name and later wants the ordinary sense should not have to edit the
    database by hand.

    Args:
        term: The registered name to remove.

    Returns:
        bool: True if a row was removed.
    """
    clean = (term or "").strip().lower()
    if not clean:
        return False
    init_db()
    con = get_db()
    try:
        with con:
            cur = con.execute("DELETE FROM tag_entities WHERE term = ?", (clean,))
        return cur.rowcount > 0
    except sqlite3.Error as exc:
        logger.warning("[TAG ENTITIES] Could not forget '%s': %s", clean, exc)
        return False
    finally:
        con.close()
