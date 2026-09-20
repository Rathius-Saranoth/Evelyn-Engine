# tag_synonym.py
# date created: 2026-09-19 00:00:00
# date modified: 2026-09-20 10:03:00
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
    namespace_children()    — How many terms live under each path prefix in the corpus.
    resolve_structural_nesting() — Settle flat-vs-nested pairs via the sibling test (§6.3.1).
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


def namespace_children(counts: collections.Counter) -> collections.Counter:
    """Count how many corpus terms live beneath each path prefix.

    Args:
        counts: Corpus terms.

    Returns:
        collections.Counter: path prefix -> number of terms nested under it.
    """
    parents: collections.Counter = collections.Counter()
    for tag in counts:
        parts = tag.split("/")
        for i in range(1, len(parts)):
            parents["/".join(parts[:i])] += 1
    return parents


def resolve_structural_nesting(
    deferred: list[list[str]], counts: collections.Counter, min_siblings: int = 2
) -> list[tuple[str, str]]:
    """Settle flat-vs-nested pairs using the sibling test (taxonomy §6.3.1).

    A hierarchy level must have siblings. If the leading token of a compound already acts
    as a namespace elsewhere, the term nests; if nothing lives under it, the compound is a
    single term of art and stays flat. This is what distinguishes `work-stress` (nest —
    'work' has hundreds of children) from `me-cfs` (keep — 'me' is not a category, and
    ME/CFS is one disease name).

    Args:
        deferred: Groups of competing forms, from lexical_equivalences()['deferred'].
        counts: Corpus term counts, for the sibling census.
        min_siblings: Terms required under a prefix for it to count as a real level.

    Returns:
        list[tuple[str, str]]: (canonical, variant) pairs.
    """
    parents = namespace_children(counts)
    pairs: list[tuple[str, str]] = []

    for members in deferred:
        flat = min(members, key=lambda t: t.count("/"))
        nested = max(members, key=lambda t: t.count("/"))
        fparts, nparts = flat.split("/"), nested.split("/")

        # Find where the two forms diverge — the hyphen under question is rarely in the
        # leaf. 'home-maintenance/chores' vs 'home/maintenance/chores' asks about
        # 'home-maintenance', and testing only the leaf answers the wrong question.
        idx = next((i for i, (a, b) in enumerate(zip(fparts, nparts, strict=False)) if a != b), None)
        if idx is None or "-" not in fparts[idx]:
            canonical = max(members, key=lambda t: counts[t])
        else:
            # Test the level the NESTED form actually proposes, not the flat form's first
            # hyphen. 'health/self-care/routine' proposes 'health/self-care' as the level —
            # splitting the flat form on its first hyphen would test 'health/self', which
            # neither form is claiming exists.
            level = "/".join([*fparts[:idx], nparts[idx]])
            canonical = nested if parents.get(level, 0) >= min_siblings else flat
        pairs.extend((canonical, m) for m in members if m != canonical)

    return pairs
