# tag_entities.py
# date created: 2026-09-26 17:16:45
# date modified: 2026-10-16 01:55:18
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

ENTITY_KINDS = (
    "organization",
    "product",
    "work",
    "project",
    "person",
    "persona",
    "pet",
    "place",
    "other",
)


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


def update_entity(term: str, label: str = "", kind: str = "other", note: str = "") -> bool:
    """Directly update or register an entity with explicit label, kind, and note.

    Unlike `record_entity` which preserves existing labels when given empty strings,
    `update_entity` is used for explicit curation and overrides values.
    """
    clean = (term or "").strip().lower().replace(" ", "-").replace("_", "-")
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
                "  label = excluded.label, "
                "  kind = excluded.kind, "
                "  note = excluded.note",
                (clean, (label or "").strip(), kind, (note or "").strip(), time.time()),
            )
    except sqlite3.Error as exc:
        logger.warning("[TAG ENTITIES] Could not update '%s': %s", clean, exc)
        return False
    logger.info("[TAG ENTITIES] Updated entity '%s' (%s, label=%s).", clean, kind, label)
    return True


def is_entity(term: str) -> bool:
    """Whether a term is registered as a name (or alias of a name)."""
    clean = (term or "").strip().lower()
    if not clean:
        return False
    init_db()
    con = get_db()
    try:
        row = con.execute("SELECT 1 FROM tag_entities WHERE term = ?", (clean,)).fetchone()
        if not row:
            row = con.execute("SELECT 1 FROM tag_entity_aliases WHERE alias = ?", (clean,)).fetchone()
    except sqlite3.Error:
        # Cannot prove it is a name, so suppress nothing: one extra proposal is a review,
        # a wrongly suppressed term is invisible.
        return False
    finally:
        con.close()
    return row is not None


def get_entity(term_or_label: str) -> dict[str, Any] | None:
    """Find a registered entity by its canonical term, label, or alias."""
    clean = (term_or_label or "").strip()
    if not clean:
        return None
    clean_lower = clean.lower()
    clean_term = clean_lower.replace(" ", "-").replace("_", "-")
    init_db()
    con = get_db()
    try:
        row = con.execute(
            "SELECT * FROM tag_entities WHERE term = ? OR term = ? OR LOWER(label) = ? LIMIT 1",
            (clean_lower, clean_term, clean_lower),
        ).fetchone()
        if not row:
            alias_row = con.execute(
                "SELECT canonical FROM tag_entity_aliases WHERE alias = ? OR alias = ? LIMIT 1",
                (clean_lower, clean_term),
            ).fetchone()
            if alias_row:
                row = con.execute(
                    "SELECT * FROM tag_entities WHERE term = ? LIMIT 1",
                    (alias_row["canonical"],),
                ).fetchone()
        if not row:
            return None
        entity = dict(row)
        alias_rows = con.execute(
            "SELECT alias FROM tag_entity_aliases WHERE canonical = ? ORDER BY alias ASC",
            (entity["term"],),
        ).fetchall()
        entity["aliases"] = [str(r["alias"]) for r in alias_rows]
        return entity
    except sqlite3.Error:
        return None
    finally:
        con.close()


def resolve_entity_label(candidate: str) -> str | None:
    """Resolve a candidate name or term to its canonical display label if registered.

    Matches against term (e.g. 'evelyn-engine'), label (e.g. 'Evelyn Engine'), or alias.
    Returns the entity's formatted label (e.g. 'Evelyn Engine'), or candidate if label is blank.
    """
    entity = get_entity(candidate)
    if not entity:
        return None
    label = str(entity.get("label") or "").strip()
    if label:
        return label
    term = str(entity.get("term") or "").strip()
    return term.replace("-", " ").title()


def all_entity_labels() -> list[str]:
    """Return all unique canonical entity labels registered in the taxonomy."""
    init_db()
    con = get_db()
    try:
        rows = con.execute(
            "SELECT term, label FROM tag_entities ORDER BY request_count DESC, term ASC"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    labels: list[str] = []
    seen: set[str] = set()
    for r in rows:
        lbl = str(r["label"] or "").strip()
        if not lbl:
            lbl = str(r["term"] or "").strip().replace("-", " ").title()
        if lbl and lbl.lower() not in seen:
            seen.add(lbl.lower())
            labels.append(lbl)
    return labels


def get_entities() -> list[dict[str, Any]]:
    """Every registered name, most recently recorded first, with their aliases."""
    init_db()
    con = get_db()
    try:
        rows = con.execute(
            "SELECT * FROM tag_entities ORDER BY recorded_at DESC, term ASC"
        ).fetchall()
        alias_rows = con.execute(
            "SELECT alias, canonical FROM tag_entity_aliases ORDER BY alias ASC"
        ).fetchall()
        alias_map: dict[str, list[str]] = {}
        for ar in alias_rows:
            alias_map.setdefault(str(ar["canonical"]), []).append(str(ar["alias"]))
        entities = []
        for r in rows:
            ent = dict(r)
            ent["aliases"] = alias_map.get(ent["term"], [])
            entities.append(ent)
        return entities
    except sqlite3.Error:
        return []
    finally:
        con.close()


def entity_terms() -> set[str]:
    """Every registered name (and alias) as a lookup set, for the admission filter."""
    init_db()
    con = get_db()
    try:
        rows = con.execute("SELECT term FROM tag_entities").fetchall()
        alias_rows = con.execute("SELECT alias FROM tag_entity_aliases").fetchall()
    except sqlite3.Error:
        return set()
    finally:
        con.close()
    terms = {str(r["term"]) for r in rows}
    terms.update(str(r["alias"]) for r in alias_rows)
    return terms


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
            # If it's an alias, update the canonical entity's count
            alias_row = con.execute(
                "SELECT canonical FROM tag_entity_aliases WHERE alias = ?", (clean,)
            ).fetchone()
            target_term = alias_row["canonical"] if alias_row else clean

            con.execute(
                "UPDATE tag_entities SET request_count = request_count + 1 WHERE term = ?",
                (target_term,),
            )
        row = con.execute(
            "SELECT request_count AS n FROM tag_entities WHERE term = ?", (target_term,)
        ).fetchone()
    except sqlite3.Error:
        return 0
    finally:
        con.close()
    return int(row["n"]) if row else 0


def add_entity_alias(term: str, alias: str) -> bool:
    """Add an alternative spelling, abbreviation, or merged slug pointing to a canonical entity."""
    clean_term = (term or "").strip().lower().replace(" ", "-").replace("_", "-")
    clean_alias = (alias or "").strip().lower().replace(" ", "-").replace("_", "-")
    if not clean_term or not clean_alias or clean_term == clean_alias:
        return False
    init_db()
    con = get_db()
    try:
        with con:
            exists = con.execute("SELECT 1 FROM tag_entities WHERE term = ?", (clean_term,)).fetchone()
            if not exists:
                logger.warning("[TAG ENTITIES] Cannot add alias '%s': canonical entity '%s' not found.", clean_alias, clean_term)
                return False
            # If alias exists as its own entity, remove it so it becomes an alias
            con.execute("DELETE FROM tag_entities WHERE term = ?", (clean_alias,))
            con.execute(
                "INSERT INTO tag_entity_aliases (alias, canonical, created_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(alias) DO UPDATE SET canonical = excluded.canonical, created_at = excluded.created_at",
                (clean_alias, clean_term, time.time()),
            )
        logger.info("[TAG ENTITIES] Added alias '%s' -> '%s'.", clean_alias, clean_term)
        return True
    except sqlite3.Error as exc:
        logger.warning("[TAG ENTITIES] Could not add alias '%s' for '%s': %s", clean_alias, clean_term, exc)
        return False
    finally:
        con.close()


def remove_entity_alias(alias: str) -> bool:
    """Remove an alias mapping."""
    clean = (alias or "").strip().lower().replace(" ", "-").replace("_", "-")
    if not clean:
        return False
    init_db()
    con = get_db()
    try:
        with con:
            cur = con.execute("DELETE FROM tag_entity_aliases WHERE alias = ?", (clean,))
        return cur.rowcount > 0
    except sqlite3.Error as exc:
        logger.warning("[TAG ENTITIES] Could not remove alias '%s': %s", clean, exc)
        return False
    finally:
        con.close()


def merge_entities(source_term: str, target_term: str, retain_alias: bool = True) -> bool:
    """Merge a duplicate entity into a canonical entity, combining stats and context.

    Args:
        source_term: The duplicate entity term to absorb and remove.
        target_term: The canonical entity term that absorbs the duplicate.
        retain_alias: Whether to register source_term as a permanent alias of target_term.

    Returns:
        bool: True on successful merge.
    """
    src = (source_term or "").strip().lower().replace(" ", "-").replace("_", "-")
    tgt = (target_term or "").strip().lower().replace(" ", "-").replace("_", "-")
    if not src or not tgt or src == tgt:
        return False
    init_db()
    con = get_db()
    try:
        with con:
            src_row = con.execute("SELECT * FROM tag_entities WHERE term = ?", (src,)).fetchone()
            tgt_row = con.execute("SELECT * FROM tag_entities WHERE term = ?", (tgt,)).fetchone()
            if not src_row or not tgt_row:
                logger.warning("[TAG ENTITIES] Cannot merge '%s' into '%s': entity missing.", src, tgt)
                return False

            src_data = dict(src_row)
            tgt_data = dict(tgt_row)

            new_count = int(tgt_data.get("request_count") or 1) + int(src_data.get("request_count") or 1)

            # Kind resolution: prefer specific kind over 'other'
            new_kind = tgt_data.get("kind") or "other"
            if new_kind == "other" and src_data.get("kind") and src_data.get("kind") != "other":
                new_kind = src_data["kind"]

            # Label resolution: keep target label, or adopt source label if target blank
            new_label = (tgt_data.get("label") or "").strip()
            if not new_label and src_data.get("label"):
                new_label = src_data["label"].strip()

            # Note resolution
            src_note = (src_data.get("note") or "").strip()
            tgt_note = (tgt_data.get("note") or "").strip()
            if not tgt_note:
                new_note = src_note
            elif src_note and src_note not in tgt_note:
                new_note = f"{tgt_note} (Merged from {src}: {src_note})".strip()
            else:
                new_note = tgt_note

            # Update target entity
            con.execute(
                "UPDATE tag_entities SET label = ?, kind = ?, note = ?, request_count = ? WHERE term = ?",
                (new_label, new_kind, new_note, new_count, tgt),
            )

            # Re-point any existing aliases that pointed to src
            con.execute("UPDATE tag_entity_aliases SET canonical = ? WHERE canonical = ?", (tgt, src))

            # Delete source entity from tag_entities
            con.execute("DELETE FROM tag_entities WHERE term = ?", (src,))

            # Record source as alias if requested
            if retain_alias:
                con.execute(
                    "INSERT INTO tag_entity_aliases (alias, canonical, created_at) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT(alias) DO UPDATE SET canonical = excluded.canonical, created_at = excluded.created_at",
                    (src, tgt, time.time()),
                )
            else:
                con.execute("DELETE FROM tag_entity_aliases WHERE alias = ?", (src,))

        # Synchronize memory subject references if needed
        try:
            from Evelyn.tools import memory_db

            con_mem = memory_db.get_db()
            with con_mem:
                if src_data.get("label") and new_label and src_data["label"] != new_label:
                    con_mem.execute(
                        "UPDATE context_entries SET subject = ? WHERE subject = ?",
                        (new_label, src_data["label"]),
                    )
        except (sqlite3.Error, ImportError, AttributeError) as mem_err:
            logger.debug("[TAG ENTITIES] Memory sync during merge skipped: %s", mem_err)

        logger.info("[TAG ENTITIES] Successfully merged entity '%s' into '%s' (retain_alias=%s).", src, tgt, retain_alias)
        return True
    except sqlite3.Error as exc:
        logger.warning("[TAG ENTITIES] Merge failed for '%s' -> '%s': %s", src, tgt, exc)
        return False
    finally:
        con.close()


def forget_entity(term: str) -> bool:
    """Remove a name from the register, so the word can be proposed as a subject again.

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
            con.execute("DELETE FROM tag_entity_aliases WHERE canonical = ? OR alias = ?", (clean, clean))
            cur = con.execute("DELETE FROM tag_entities WHERE term = ?", (clean,))
        return cur.rowcount > 0
    except sqlite3.Error as exc:
        logger.warning("[TAG ENTITIES] Could not forget '%s': %s", clean, exc)
        return False
    finally:
        con.close()
