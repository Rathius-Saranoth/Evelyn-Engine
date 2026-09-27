# taxonomy_db.py
# date created: 2026-09-19 00:00:00
# date modified: 2026-09-26 08:38:51
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

import contextlib
import logging
import sqlite3
import time
from typing import Any

from Evelyn.tools.vault_db import get_db, init_db

logger = logging.getLogger("evelyn.taxonomy_db")


def get_master_tags() -> list[dict[str, Any]]:
    """Return every registered term from the controlled vocabulary in SQLite.

    Ordered by usage_count DESC so high-frequency terms surface first.

    Returns:
        list[dict[str, Any]]: Every term in the controlled taxonomy.
    """
    init_db()
    con = get_db()
    try:
        rows = [dict(r) for r in con.execute("SELECT * FROM master_tag_taxonomy")]
    finally:
        con.close()

    result: list[dict[str, Any]] = [
        {
            "tag": r["tag"],
            "category": r.get("category") or "",
            "description": r.get("description") or "",
            "usage_count": int(r.get("usage_count") or 0),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
            "protected": int(r.get("protected") or 0),
        }
        for r in rows
    ]

    result.sort(key=lambda m: (-m["usage_count"], m["category"] or "", m["tag"]))
    return result


def upsert_master_tag(tag: str, category: str = "", description: str = "",
                      usage_count: int | None = None) -> None:
    """Register a term in the controlled vocabulary, or update an existing one.

    **An empty field means "leave it alone", not "blank it".** Most callers here know a term
    and its usage count and nothing else — a census refresh, an admission — and passing the
    default for `category` and `description` must not erase what a reviewer wrote. The
    description was already guarded this way; `category` was not, and was overwritten
    unconditionally, so any caller omitting it silently cleared a curated categorisation. To
    change a field, pass the new value; there is deliberately no way to blank one from here.

    Args:
        tag: The term, already in §5 format (e.g. 'tech/python').
        category: Top-level category (e.g. 'tech'). Empty preserves the stored one.
        description: Short scope statement. Empty preserves the stored one.
        usage_count: Current number of resources using this term. `None` preserves the stored
            count — a caller registering a term does not know it, and zero is a real value a
            reserved term legitimately holds, so it cannot double as "unknown".
    """
    init_db()
    con = get_db()
    now = time.time()
    try:
        con.execute("""
            INSERT INTO master_tag_taxonomy (tag, category, description, usage_count, created_at, updated_at)
            VALUES (:tag, :category, :description, COALESCE(:usage_count, 0), :now, :now)
            ON CONFLICT(tag) DO UPDATE SET
                category = CASE WHEN excluded.category != '' THEN excluded.category ELSE master_tag_taxonomy.category END,
                description = CASE WHEN excluded.description != '' THEN excluded.description ELSE master_tag_taxonomy.description END,
                usage_count = CASE WHEN :usage_count IS NULL THEN master_tag_taxonomy.usage_count ELSE excluded.usage_count END,
                updated_at = excluded.updated_at
        """, {"tag": tag, "category": category, "description": description,
              "usage_count": usage_count, "now": now})
        con.commit()
    finally:
        con.close()


def delete_master_tag(tag: str) -> bool:
    """Remove a term from the controlled vocabulary.

    Args:
        tag: The term to remove.

    Returns:
        bool: True if a local row was removed; False if the term was not found.
    """
    init_db()
    con = get_db()
    try:
        cur = con.execute("DELETE FROM master_tag_taxonomy WHERE tag = ?", (tag,))
        con.commit()
        return cur.rowcount > 0
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


def record_alias(alias: str, canonical: str, tier: str = "reviewed") -> None:
    """Record a `UF` equivalence so a retired term keeps pointing at its preferred form.

    Retiring a term in a controlled vocabulary means deprecating it *with a pointer*, not
    erasing it: a document or query still phrased the retired way must reach the preferred
    term, and deleting the record throws away the knowledge that the variant existed
    (taxonomy §6.2). The in-process alias cache is invalidated here, so callers need not.

    Args:
        alias: The retired surface form.
        canonical: The preferred term it should resolve to.
        tier: Provenance of the decision ('reviewed', 'inferred').
    """
    if not alias or not canonical or alias == canonical:
        return
    init_db()
    con = get_db()
    try:
        con.execute(
            """INSERT INTO master_tag_aliases (alias, canonical, tier, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(alias) DO UPDATE SET canonical = excluded.canonical, tier = excluded.tier""",
            (alias, canonical, tier, time.time()),
        )
        con.commit()
    finally:
        con.close()
    invalidate_alias_cache()


def canonicalize_tags(tags: list[str]) -> list[str]:
    """Map tags through recorded `UF` equivalences to their preferred forms.

    **This is not an admission gate.** A term with no recorded equivalence is returned
    unchanged whether or not it is in the controlled vocabulary, so calling this does
    nothing to keep unregistered terms out. Use :func:`partition_by_admission` to ask
    whether a term is actually registered.

    This is what makes a collapse permanent. Recording an alias without consulting it on
    write only renames existing data — the next extraction re-mints the retired variant
    and the vocabulary drifts back (taxonomy §6.2).

    Resolution is transitive and cycle-guarded. Aliases accumulate across migration passes,
    so a term retired in one pass can point at a term a later pass also retired; a single
    hop would leave that dead target in place.

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
        # Resolve transitively. Successive migration passes chain: a step-5 merge can point
        # at a term a later root pass then retired, so one hop leaves a dead target behind.
        mapped, seen = tag, {tag}
        while mapped in _ALIAS_CACHE:
            nxt = _ALIAS_CACHE[mapped]
            if not nxt:
                mapped = ""  # removed outright
                break
            if nxt in seen:
                break  # cycle: two passes disagreed on direction; stop at the current form
            seen.add(nxt)
            mapped = nxt
        if mapped and mapped not in out:
            out.append(mapped)
    return out


def partition_by_admission(tags: list[str]) -> tuple[list[str], list[str]]:
    """Split tags into those the vocabulary admits and those it does not.

    The admission decision the registry can actually make, kept deliberately separate from
    :func:`canonicalize_tags`, which only rewrites aliases and passes unknown terms straight
    through. Conflating the two is why several writers believed they were validating.

    This reports; it does not decide what to do with a rejection. Callers either refuse the
    term or route it to review (taxonomy §6).

    Args:
        tags: Tag strings, already normalized and alias-resolved.

    Returns:
        tuple[list[str], list[str]]: (admitted, unregistered), order preserved.
    """
    known = _registered_surface_forms()
    admitted, unregistered = [], []
    for tag in tags:
        if not tag:
            continue
        (admitted if tag in known else unregistered).append(tag)
    return admitted, unregistered


def _registered_surface_forms() -> set[str]:
    """Return every term plus every recorded equivalence, as one lookup set."""
    forms = {row["tag"] for row in get_master_tags()}
    with contextlib.suppress(sqlite3.Error):
        forms.update(get_aliases().keys())
    return forms


# Relation provenance, strongest first. A hand-approved relation outranks an inferred one
# whatever their weights say, because the weight is a retrieval dial and the tier is a judgement.
_TIER_RANK = {"reviewed": 2, "inferred": 1, "candidate": 0}


def _normalise_pair(term_a: str, term_b: str, kind: str) -> tuple[str, str] | None:
    """Put a relation pair in storage order, or return None if it is not a relation.

    `related` is symmetric, so its pair is sorted and stored once. `narrower` is directional —
    `term_a` is a kind of `term_b` — and sorting it would silently reverse roughly half of all
    such pairs.

    Raises:
        ValueError: If `kind` is neither `related` nor `narrower`.
    """
    a, b = (term_a or "").strip(), (term_b or "").strip()
    if not a or not b or a == b:
        return None
    if kind not in ("related", "narrower"):
        raise ValueError(f"kind must be 'related' or 'narrower', got {kind!r}")
    return tuple(sorted((a, b))) if kind == "related" else (a, b)  # type: ignore[return-value]


def record_relation(term_a: str, term_b: str, kind: str = "related", weight: float = 0.4,
                    tier: str = "reviewed", note: str = "") -> None:
    """Record a relation between two terms (taxonomy §6.4).

    Two kinds live here. `related` is associative and symmetric, so the pair is sorted before
    storage and `(a, b)` and `(b, a)` are one row. `narrower` is **directional** — `term_a` is a
    kind of `term_b` — so its order is preserved exactly as given. Sorting a narrower pair would
    silently reverse roughly half of them: `lucid-dreaming` is a kind of `dream`, and
    alphabetically it comes second.

    Equivalence does not belong here. Two interchangeable terms are an alias (§6.2), recorded
    with `record_alias`.

    Args:
        term_a: One term; the narrower one when `kind` is `narrower`.
        term_b: The other; the broader one when `kind` is `narrower`.
        kind: `related` or `narrower`.
        weight: Retrieval multiplier for expansion through this relation, below a direct match.
        tier: `reviewed` for a human decision, `candidate` for something not yet approved.
        note: Optional rationale, kept for a later reviewer.
    """
    pair = _normalise_pair(term_a, term_b, kind)
    if pair is None:
        return
    a, b = pair

    init_db()
    con = get_db()
    try:
        # A pair already stored the other way round is the same pair; replace it rather than
        # letting both directions coexist.
        con.execute("DELETE FROM master_tag_related WHERE term_a = ? AND term_b = ?", (b, a))
        con.execute(
            """
            INSERT INTO master_tag_related (term_a, term_b, kind, weight, tier, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(term_a, term_b) DO UPDATE SET
                kind = excluded.kind, weight = excluded.weight,
                tier = excluded.tier, note = excluded.note
            """,
            (a, b, kind, weight, tier, note, time.time()),
        )
        con.commit()
    finally:
        con.close()


def delete_relations(term: str) -> int:
    """Remove every relation touching a term.

    Used when a term is retired outright, with no preferred form to inherit its relations. A
    relation whose endpoint no longer exists is not a weaker relation, it is a broken one:
    expansion follows it to a term the registry cannot describe.

    Args:
        term: The term being removed.

    Returns:
        int: Relations deleted.
    """
    clean = (term or "").strip()
    if not clean:
        return 0
    init_db()
    con = get_db()
    try:
        cur = con.execute(
            "DELETE FROM master_tag_related WHERE term_a = ? OR term_b = ?", (clean, clean)
        )
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def repoint_relations(old_term: str, new_term: str) -> dict[str, int]:
    """Move a retired term's relations onto the term it retires to.

    Retirement with a replacement is a rename plus a pointer, not a deletion: if `napping`
    retires to `nap`, then whatever `napping` was related to, `nap` is related to. Dropping
    those rows instead would lose curated judgements for a bookkeeping reason.

    Three cases need deciding rather than copying:

    * The relation already pointed at the new term (`napping` is a kind of `nap`). The alias
      now says the two are one term, and a term is not related to itself, so it is dropped.
    * The new term already holds the same pair. The stronger claim stands — a `reviewed`
      relation outranks an `inferred` one, and within a tier the heavier weight wins — because
      re-pointing must not quietly downgrade a decision made about the surviving term.
    * A symmetric pair can fall out of storage order once an endpoint is renamed, so every
      moved row is re-normalised rather than written back as it was read.

    Args:
        old_term: The term being retired.
        new_term: Its preferred form.

    Returns:
        dict[str, int]: Counts of relations `moved`, `merged` into an existing stronger row,
        and `dropped` as self-relations.
    """
    old = (old_term or "").strip()
    new = (new_term or "").strip()
    if not old or not new or old == new:
        return {"moved": 0, "merged": 0, "dropped": 0}

    init_db()
    con = get_db()
    try:
        rows = [
            dict(r) for r in con.execute(
                "SELECT * FROM master_tag_related WHERE term_a = ? OR term_b = ?", (old, old)
            ).fetchall()
        ]
        if not rows:
            return {"moved": 0, "merged": 0, "dropped": 0}
        con.execute("DELETE FROM master_tag_related WHERE term_a = ? OR term_b = ?", (old, old))

        moved = merged = dropped = 0
        for row in rows:
            pair = _normalise_pair(
                new if row["term_a"] == old else row["term_a"],
                new if row["term_b"] == old else row["term_b"],
                row["kind"],
            )
            if pair is None:
                dropped += 1
                continue
            a, b = pair
            existing = con.execute(
                "SELECT kind, weight, tier FROM master_tag_related "
                "WHERE (term_a = ? AND term_b = ?) OR (term_a = ? AND term_b = ?)",
                (a, b, b, a),
            ).fetchone()
            if existing is not None:
                merged += 1
                incoming_rank = (_TIER_RANK.get(row["tier"], 0), row["weight"])
                held_rank = (_TIER_RANK.get(existing["tier"], 0), existing["weight"])
                if incoming_rank <= held_rank:
                    continue
                con.execute(
                    "DELETE FROM master_tag_related WHERE term_a = ? AND term_b = ?", (b, a)
                )
            else:
                moved += 1
            con.execute(
                """
                INSERT INTO master_tag_related (term_a, term_b, kind, weight, tier, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(term_a, term_b) DO UPDATE SET
                    kind = excluded.kind, weight = excluded.weight,
                    tier = excluded.tier, note = excluded.note
                """,
                (a, b, row["kind"], row["weight"], row["tier"], row["note"], row["created_at"] or time.time()),
            )
        con.commit()
        return {"moved": moved, "merged": merged, "dropped": dropped}
    finally:
        con.close()


def get_related_terms(term: str) -> list[dict[str, Any]]:
    """Return the terms related to one term, with the direction spelled out.

    `relation` reads from the queried term's point of view: `broader` means the other term is
    what this one is a kind of, `narrower` the reverse, `related` that neither is.

    Args:
        term: The term to expand from.

    Returns:
        list[dict]: Each with `tag`, `relation`, `weight` and `tier`, heaviest first.
    """
    clean = (term or "").strip()
    if not clean:
        return []
    init_db()
    con = get_db()
    try:
        rows = con.execute(
            """
            SELECT term_b AS tag, kind, weight, tier, 1 AS forward
              FROM master_tag_related WHERE term_a = ?
            UNION ALL
            SELECT term_a AS tag, kind, weight, tier, 0 AS forward
              FROM master_tag_related WHERE term_b = ?
            ORDER BY weight DESC, tag ASC
            """,
            (clean, clean),
        ).fetchall()
    finally:
        con.close()

    out = []
    for r in rows:
        d = dict(r)
        forward = d.pop("forward")
        kind = d.pop("kind")
        # Stored as "term_a is a kind of term_b", so the label flips when read from term_b.
        d["relation"] = kind if kind == "related" else ("broader" if forward else "narrower")
        out.append(d)
    return out
