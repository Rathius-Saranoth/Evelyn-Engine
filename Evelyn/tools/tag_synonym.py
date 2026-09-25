# tag_synonym.py
# date created: 2026-09-19 00:00:00
# date modified: 2026-09-25 18:15:22
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
    root_census()           — Terms and uses sitting under each top-level root.
    root_inflection_merges() — Merge roots that differ only by inflection (§6.3.2: singular wins).
    literary_warrant()      — How often each root occurs as a phrase in the vault's own text.
    weak_root_resolution()  — Re-nest or dismantle sparse roots, judged by warrant not population.
    decompose_to_atoms()    — Split a hierarchical term into post-coordinate atoms (§3.3).
    decompose_tag_csv()     — Apply decomposition across a comma-separated tag string.
    flat_compound_decomposition() — Split unwarranted hyphenated compounds into atoms (§3.3).
    apply_decomposition_to_csv() — Apply a decomposition plan across a tag string.
    one_off_phrase_tags()   — Flat multi-word descriptors used once or twice (§6.3.4).
    singularize()           — Crude English singularization of a skeleton.
    build_corpus()          — Combined term->usage counts across vault and memory.
    lexical_equivalences()  — Tiered T1A/T1B/T2 groups, computed without embeddings.

See also: .agents/rules/vault-tag-taxonomy.md §6.2
"""

import collections
import re
import sqlite3
from collections.abc import Callable, Iterable
from typing import Any

import evelyn_config as cfg
from Evelyn.tools.tag_librarian import FACET_PREFIXES, is_excluded_tag


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

    Deliberately conservative: it maps 'ies' to 'y', then strips a trailing 's' only when
    not preceded by s/x/z. Over-aggressive stemming would merge genuinely distinct terms.

    **Order matters, and asymmetry is the failure mode.** Stripping the trailing 's' first
    destroyed the 'ies' pattern the next rule looks for, so `memories` became `memorie` while
    `memory` stayed `memory` — different keys, so the plural could never resolve to its own
    singular. Sibilant plurals had the same defect: `boxes` became `boxe` against `box`.

    Linguistic accuracy is not the goal; *symmetry* is. `analysis` stems to `analysi`, which
    is wrong as English and harmless here, because both sides of a comparison pass through
    this same function. A rule only does damage when it maps two forms of one word to two
    different keys.

    Args:
        word: A skeleton form.

    Returns:
        str: Singularized skeleton.
    """
    word = re.sub(r"ies$", "y", word)
    word = re.sub(r"(s|x|z|ch|sh)es$", r"\1", word)
    return re.sub(r"(?<![sxz])s$", "", word)


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


def root_census(counts: collections.Counter) -> dict[str, dict[str, int]]:
    """Count the terms and uses sitting under each top-level root.

    Args:
        counts: Corpus term -> usage count.

    Returns:
        dict[str, dict[str, int]]: root -> {'terms': n, 'uses': n}.
    """
    census: dict[str, dict[str, int]] = {}
    for tag, uses in counts.items():
        if "/" not in tag:
            continue
        root = tag.split("/", 1)[0]
        entry = census.setdefault(root, {"terms": 0, "uses": 0})
        entry["terms"] += 1
        entry["uses"] += uses
    return census


def root_inflection_merges(counts: collections.Counter) -> dict[str, str]:
    """Merge roots that are inflections of one word.

    Step 5 merged whole terms, so it could not see this: `preference/food` and
    `preferences/drink` share no lexical pair, yet the roots are one concept. Consolidating
    at the root level rewrites every term beneath it.

    The singular form wins, per §6.3.2 — which sometimes means the smaller root absorbs the
    larger one. That is the convention working, not a bug: correctness of form outranks
    incumbency.

    Args:
        counts: Corpus term -> usage count.

    Returns:
        dict[str, str]: variant root -> canonical root.
    """
    census = root_census(counts)
    families: dict[str, list[str]] = collections.defaultdict(list)
    for root in census:
        families[singularize(skeleton(root))].append(root)

    merges: dict[str, str] = {}
    for members in families.values():
        if len(members) < 2:
            continue
        # Shortest skeleton is the singular; break ties on established size.
        canonical = min(members, key=lambda r: (len(skeleton(r)), -census[r]["terms"]))
        for variant in members:
            if variant != canonical:
                merges[variant] = canonical
    return merges


def literary_warrant(roots: list[str], vault_root: str, entities: set[str] | None = None) -> dict[str, int]:
    """Count how often each root occurs as a phrase in the vault's own prose.

    This is *literary warrant* in the sense ANSI/NISO Z39.19 §6.5.1.1 uses it: a term earns
    its place in a controlled vocabulary by appearing in the literature of the domain, not
    by how much has already been filed under it. A root tagged once but written 800 times
    is an obvious category whose material simply has not been classified yet — and
    dismantling it would delete exactly the categories that are about to fill.

    Compounds are matched as phrases. Searching for `mental-state` as a hyphenated token
    finds nothing, because prose says "mental state"; treating that zero as evidence would
    condemn every multi-word root by construction.

    Args:
        roots: Root names to score.
        vault_root: Absolute path to the vault.
        entities: Names that are individuals, not categories (§2). Scored -1 so they can
            never qualify as domains however often they appear.

    Returns:
        dict[str, int]: root -> occurrence count, or -1 for entities.
    """
    import os
    import re

    entities = {e.lower() for e in (entities or set()) if e}
    chunks: list[str] = []
    for current, _dirs, files in os.walk(vault_root):
        for name in files:
            if not name.lower().endswith(".md"):
                continue
            try:
                with open(os.path.join(current, name), encoding="utf-8", errors="replace") as fh:
                    raw = fh.read()
            except OSError:
                continue
            chunks.append(re.sub(r"^---.*?\n---", "", raw, flags=re.DOTALL).lower())

    text = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s-]", " ", " ".join(chunks)))

    scores: dict[str, int] = {}
    for root in roots:
        if root in entities:
            scores[root] = -1
            continue
        words = root.split("-")
        pattern = r"\b" + r"[\s-]+".join(re.escape(w) for w in words) + r"s?\b"
        scores[root] = len(re.findall(pattern, text))
    return scores


def weak_root_resolution(
    counts: collections.Counter,
    min_children: int = 2,
    strong_children: int = 5,
    warrant: dict[str, int] | None = None,
    min_warrant: int = 3,
    head_aliases: dict[str, str] | None = None,
) -> dict[str, str]:
    """Resolve sparse roots, judged by literary warrant rather than by population.

    Child count measures how much of a category has been *classified so far*. Judging a
    namespace by it destroys the categories that are about to fill up, and does so silently,
    because the evidence they were real is the material nobody has tagged.

    Order of judgement for a sparse root:

    1. **Compound whose head is an established root** — a missed nesting, not a sparse
       category: `ai-behavior` becomes `ai/behavior` where `ai` already holds 128 terms.
    2. **Warrant at or above the floor** — the word is written throughout the vault, so the
       namespace stands however little sits under it. `architecture`, tagged once, appears
       1,697 times.
    3. **Below the floor** — nobody writes it, so nobody would search it. Dismantled.
       `pet-name`, `social-relations` and `food-prep` occur zero times in the prose.

    Dismantling never produces a long flat compound: only two-segment terms collapse, and
    deeper ones drop the junk root and keep their real structure.

    Args:
        counts: Corpus term -> usage count, already inflection-merged.
        min_children: Below this many children a root is considered sparse.
        strong_children: Children required for a root to count as an established parent.
        warrant: root -> occurrence count from literary_warrant(). Absent, every sparse
            root is kept, since dismantling without evidence is the failure mode.
        min_warrant: Occurrences required for a sparse root to keep its namespace.
        head_aliases: Inflection merges from root_inflection_merges(), so a compound head
            is tested in its canonical form. Without it `relationships-dynamics` fails the
            head check — `relationships` merged into `relationship` — and gets dismantled
            into a flat tag despite having a perfectly good parent.

    Returns:
        dict[str, str]: old term -> new term.
    """
    census = root_census(counts)
    sparse = {r for r, stats in census.items() if stats["terms"] < min_children}
    strong = {r for r, stats in census.items() if stats["terms"] >= strong_children}
    warrant = warrant or {}

    rewritten: dict[str, str] = {}
    for tag in counts:
        if "/" not in tag:
            continue
        root, rest = tag.split("/", 1)
        if root not in sparse:
            continue

        if "-" in root:
            head, tail = root.split("-", 1)
            head = (head_aliases or {}).get(head, head)
            if head in strong:
                rewritten[tag] = f"{head}/{tail}/{rest}"
                continue

        score = warrant.get(root, min_warrant)  # unknown roots are kept, not destroyed
        if score >= min_warrant:
            continue

        rewritten[tag] = tag.replace("/", "-") if tag.count("/") == 1 else rest

    return rewritten


def adopt_flat_compounds(counts: collections.Counter, strong_children: int = 5) -> dict[str, str]:
    """Nest flat compounds whose leading word is already an established level.

    This is the sibling test (§6.3.1) applied to terms that have no nested twin to compare
    against. `productivity-tips` is flat only because nothing ever nested it; `productivity`
    demonstrably holds terms, so the hyphen was a missed slash all along.

    The same evidential bar applies as everywhere else — the head must be a *real* level,
    proven by what already lives under it. A compound whose head names nothing is left
    alone rather than nested speculatively.

    Converges in one pass on this corpus: adopting a term makes its parent larger, but not
    in a way that promotes new heads, so a second pass finds nothing.

    Args:
        counts: Corpus term -> usage count.
        strong_children: Terms required under a head for it to count as a real level.

    Returns:
        dict[str, str]: flat term -> nested term.
    """
    census = root_census(counts)
    strong = {r for r, stats in census.items() if stats["terms"] >= strong_children}

    adopted: dict[str, str] = {}
    for tag in counts:
        if "/" in tag or "-" not in tag:
            continue
        head, tail = tag.split("-", 1)
        if head in strong:
            adopted[tag] = f"{head}/{tail}"
    return adopted


def one_off_phrase_tags(
    counts: collections.Counter, min_words: int = 3, max_uses: int = 2
) -> list[str]:
    """Find flat multi-word descriptors that occur once or twice.

    A tag is a retrieval handle. `cat-care-supplies` or `heartwarming-animal-encounters`
    is a sentence fragment that happens to be hyphenated — nobody searches it, nothing
    else shares it, and it contributes a vocabulary entry for a single document.

    Two conditions together, because either alone is wrong. Length alone would condemn
    legitimate compound terms; low use alone would condemn correct structure that is merely
    young (§6.3.3). It is the **combination** — verbose *and* unshared — that marks a label
    generated for one document rather than a category.

    Nested terms are excluded regardless of length: a slash means something placed it in the
    tree, and that structure is the expensive part to rebuild.

    Args:
        counts: Corpus term -> usage count.
        min_words: Hyphen-separated words required to count as verbose.
        max_uses: Highest usage count still considered unshared.

    Returns:
        list[str]: Terms to retire, sorted.
    """
    return sorted(
        tag for tag, uses in counts.items()
        if "/" not in tag
        and not is_excluded_tag(tag)
        and tag.count("-") >= min_words - 1
        and uses <= max_uses
    )


def decompose_to_atoms(tag: str) -> list[str]:
    """Split a hierarchical term into the atoms a post-coordinate vocabulary holds.

    `work/routine/morning` is three concepts glued together by an indexer guessing which
    combination a future query would want. Guesses multiply — `journaling` appeared under 31
    parents here — so the concepts are separated and the query recombines them (§0.0).

    Three things are left alone: protected date anchors, administrative namespaces, and the
    single level a facet prefix carries. A facet term deeper than that loses its middle,
    because `setting/biome/tropical` is categorising *within* an axis, which is the
    hierarchy this removes.

    Args:
        tag: A term, possibly hierarchical.

    Returns:
        list[str]: The atoms it becomes. Never empty for a non-empty input.
    """
    if not tag or is_excluded_tag(tag):
        return [tag] if tag else []

    for prefix in FACET_PREFIXES:
        if tag.startswith(prefix):
            leaf = tag.split("/")[-1]
            return [f"{prefix}{leaf}"] if leaf else [tag]

    return [part for part in tag.split("/") if part] or [tag]


def decompose_tag_csv(raw: str | None) -> tuple[str, bool]:
    """Rewrite a comma-separated tag string into atoms, de-duplicating.

    Args:
        raw: Comma-separated tags.

    Returns:
        tuple[str, bool]: (rewritten CSV, whether it changed).
    """
    if not raw:
        return "", False
    current = [t.strip() for t in raw.split(",") if t.strip()]
    out: list[str] = []
    for tag in current:
        for atom in decompose_to_atoms(tag):
            if atom and atom not in out:
                out.append(atom)
    return ", ".join(out), out != current


# Words that carry no subject on their own. A compound decomposes into concepts, and a
# preposition or article is not one: `about-superpowers` is about superpowers, not about
# `about`. Kept deliberately small — this removes glue, not vocabulary.
FUNCTION_WORDS = frozenset({
    "a", "about", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "i",
    "if", "in", "is", "it", "its", "my", "of", "on", "or", "so", "that", "the", "this",
    "to", "was", "were", "with",
})


def flat_compound_decomposition(
    terms: Iterable[str], vault_root: str, canonicalize: Callable[[str], str] | None = None
) -> dict[str, list[str]]:
    """Decompose hyphenated compounds the vault never actually writes as a phrase.

    Decomposition to atoms (§3.3) split `a/b/c` and stopped at the slash, because §5 gives
    the hyphen a legitimate job: joining the words *inside* one term. But the same
    pre-coordination that filled the vault with slashes also filled it with hyphens.
    Measured here, hyphenated compounds outnumbered atoms three to one, and 1,403 of them
    appear nowhere in the vault's prose — `tracking-worries`, `insomnia-struggles`,
    `cozy-gaming-night`. They are the indexer's guesses wearing a different separator.

    Literary warrant separates the two cases, exactly as Z39.19 §6.5.1.1 intends and as
    `literary_warrant()` already measures elsewhere: a compound the vault writes as a phrase
    is a bound term and survives whole (`machine-learning`, 1,796 occurrences;
    `chain-of-thought`, 240). A compound nobody has ever written is a label someone
    assembled at filing time, and it decomposes.

    The bar is deliberately the lowest one that works — *ever written, even once*. A higher
    floor would be easy to justify in aggregate and would take real categories with it; the
    admission floor (§6.3) is the mechanism for thinning weak terms, and it applies to atoms
    after this runs rather than to compounds before it.

    Args:
        terms: Candidate terms; anything nested, protected, or excluded is skipped.
        vault_root: Absolute path to the vault, scanned for phrase occurrences.
        canonicalize: Optional resolver applied to each produced atom, so decomposition
            lands on established preferred terms instead of minting inflected twins
            (`dreams` -> `dream`). A resolution that is itself compound or nested is
            discarded: aliases recorded before decomposition still point at pre-coordinate
            targets, and following one would rebuild the compound this function just took
            apart.

    Returns:
        dict[str, list[str]]: compound -> atoms, for compounds that decompose. Terms that
        survive whole are absent, as are compounds that would yield nothing but glue.
    """
    candidates = [
        t for t in terms
        if t and "/" not in t and "-" in t and not is_excluded_tag(t)
    ]
    warrant = literary_warrant(candidates, vault_root)

    plan: dict[str, list[str]] = {}
    for term in candidates:
        if warrant.get(term, 0) > 0:
            continue  # written as a phrase: a bound term, kept whole
        atoms: list[str] = []
        for part in term.split("-"):
            if not part or part in FUNCTION_WORDS or part.isdigit() or len(part) == 1:
                continue
            resolved = canonicalize(part) if canonicalize else part
            if not resolved or ("-" in resolved or "/" in resolved):
                resolved = part  # alias target is pre-coordinate; keep the atom
            if resolved not in atoms:
                atoms.append(resolved)
        if atoms:
            plan[term] = atoms
    return plan


def apply_decomposition_to_csv(raw: str | None, plan: dict[str, list[str]]) -> tuple[str, bool]:
    """Rewrite a comma-separated tag string through a decomposition plan.

    Args:
        raw: Comma-separated tags.
        plan: compound -> atoms, from `flat_compound_decomposition()`.

    Returns:
        tuple[str, bool]: (rewritten CSV, whether it changed).
    """
    if not raw:
        return "", False
    current = [t.strip() for t in raw.split(",") if t.strip()]
    out: list[str] = []
    for tag in current:
        for atom in plan.get(tag, [tag]):
            if atom and atom not in out:
                out.append(atom)
    return ", ".join(out), out != current
