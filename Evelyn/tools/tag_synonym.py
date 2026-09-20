# tag_synonym.py
# date created: 2026-09-19 00:00:00
# date modified: 2026-09-19 21:05:42
# tags: #taxonomy, #synonyms, #vocabulary, #uf, #clustering

"""tag_synonym.py — Equivalence detection for the controlled vocabulary (taxonomy §6.2).

Implements the `UF` ("Used For") relation of ISO 25964: many colloquial variants map
onto one preferred term. The mapping is **star-shaped, never transitive** — a variant
attaches to exactly one canonical. Transitive (single-link) clustering chains unrelated
terms together through intermediates; measured on this corpus it merged 4,069 unrelated
terms into one group.

Tiers, by how much judgement each requires:
    T1A  — identical ignoring separators, same hierarchy depth ('3dprinting' / '3d-printing').
           Mechanical: the same term typed differently.
    T1B  — identical ignoring separators, DIFFERENT depth ('personal-growth' / 'personal/growth').
           A structural question: one compound concept, or a two-level hierarchy? Auto-resolvable
           only when the deeper form is also the more used; otherwise it needs a human.
    T2   — identical after singularization, same depth ('habits' / 'habit').
    T3   — semantically close but lexically distinct ('additive-manufacturing' / '3d-printing').
           Requires embeddings and always requires review.

Exports:
    skeleton()              — Separator-free lowercase form of a tag.
    singularize()           — Crude English singularization of a skeleton.
    build_corpus()          — Combined term->usage counts across vault and memory.
    lexical_equivalences()  — Tiered T1A/T1B/T2 groups, computed without embeddings.

See also: .agents/rules/vault-tag-taxonomy.md §6.2
"""

import collections
import re
import sqlite3
from typing import Any

import evelyn_config as cfg
from Evelyn.tools.tag_librarian import is_excluded_tag


def skeleton(tag: str) -> str:
    """Return the separator-free lowercase form used for equivalence testing.

    Args:
        tag: Any tag string.

    Returns:
        str: Alphanumeric-only lowercase form (e.g. '3d-printing' -> '3dprinting').
    """
    return re.sub(r"[^a-z0-9]", "", tag.lower())


def singularize(word: str) -> str:
    """Crudely singularize an English skeleton form.

    Deliberately conservative: it strips a trailing 's' only when not preceded by
    s/x/z, and maps 'ies' to 'y'. Over-aggressive stemming would merge genuinely
    distinct terms.

    Args:
        word: A skeleton form.

    Returns:
        str: Singularized skeleton.
    """
    return re.sub(r"ies$", "y", re.sub(r"(?<![sxz])s$", "", word))


def build_corpus() -> collections.Counter:
    """Collect every tag in use across both substrates with its total usage count.

    The vault and memory are one structure (§0), so equivalence detection runs over
    their union — clustering either alone would shape the vocabulary around half of it.

    Returns:
        collections.Counter: tag -> combined usage count, excluding protected namespaces.
    """
    counts: collections.Counter = collections.Counter()

    vault_path = getattr(cfg, "VAULT_DB_PATH", "")
    if vault_path:
        con = sqlite3.connect(vault_path, timeout=30.0)
        try:
            for tag, usage in con.execute("SELECT tag, usage_count FROM master_tag_taxonomy"):
                if tag and not is_excluded_tag(tag):
                    counts[tag] += usage or 0
        finally:
            con.close()

    memory_path = getattr(cfg, "MEMORY_DB_PATH", "")
    if memory_path:
        con = sqlite3.connect(memory_path, timeout=30.0)
        try:
            for table in ("context_entries", "procedures"):
                try:
                    rows = con.execute(
                        f"SELECT tags FROM {table} WHERE tags IS NOT NULL AND tags != ''"
                    ).fetchall()
                except sqlite3.OperationalError:
                    continue
                for (raw,) in rows:
                    for tag in (t.strip() for t in raw.split(",") if t.strip()):
                        if not is_excluded_tag(tag):
                            counts[tag] += 1
        finally:
            con.close()

    return counts


def _canonical_by_depth_then_usage(members: list[str], counts: collections.Counter) -> str:
    """Pick the preferred term: deepest hierarchy first, then highest usage."""
    return max(members, key=lambda t: (t.count("/"), counts[t], -len(t)))


def lexical_equivalences(counts: collections.Counter) -> dict[str, Any]:
    """Group terms that are lexically equivalent, tiered by required judgement.

    Args:
        counts: Corpus term -> usage count, from build_corpus().

    Returns:
        dict[str, Any]: {
            'auto':     list[(canonical, variant)] safe to apply without review,
            'deferred': list[list[str]] groups whose canonical is a human decision,
        }
    """
    by_skeleton: dict[str, list[str]] = collections.defaultdict(list)
    for tag in counts:
        by_skeleton[skeleton(tag)].append(tag)

    auto: list[tuple[str, str]] = []
    deferred: list[list[str]] = []
    resolved: set[str] = set()

    for members in by_skeleton.values():
        if len(members) < 2:
            continue
        resolved.update(members)
        depths = {t.count("/") for t in members}
        if len(depths) == 1:
            # T1A — pure word-separator difference; usage decides.
            canon = max(members, key=lambda t: (counts[t], -len(t)))
            auto.extend((canon, t) for t in members if t != canon)
            continue
        # T1B — differing depth is a structural claim. Only safe when the deeper form
        # is also the more used; otherwise a rare variant would rename a dominant term.
        deepest = _canonical_by_depth_then_usage(members, counts)
        most_used = max(members, key=lambda t: counts[t])
        if deepest == most_used:
            auto.extend((deepest, t) for t in members if t != deepest)
        else:
            deferred.append(sorted(members, key=lambda t: -counts[t]))

    # T2 — singular/plural at equal depth, ignoring pairs already settled above.
    by_singular: dict[str, list[str]] = collections.defaultdict(list)
    for tag in counts:
        by_singular[singularize(skeleton(tag))].append(tag)
    for members in by_singular.values():
        if len(members) < 2 or len({skeleton(t) for t in members}) < 2:
            continue
        if len({t.count("/") for t in members}) != 1:
            continue
        if any(t in resolved for t in members):
            continue
        canon = max(members, key=lambda t: (counts[t], -len(t)))
        auto.extend((canon, t) for t in members if t != canon)

    return {"auto": auto, "deferred": deferred}
