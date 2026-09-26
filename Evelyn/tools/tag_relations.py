# tag_relations.py
# date created: 2026-09-25
# date modified: 2026-09-25 19:20:57
# tags: #taxonomy, #relations, #candidates, #vocabulary

"""Relation candidate generation for the controlled vocabulary (taxonomy §6.4).

Flattening the hierarchy deleted relational information: `health/sleep` stated that sleep
belongs with health, and `health` + `sleep` states nothing. §6.4 is where that knowledge now
lives, and it is explicit that relations are never inferred and activated automatically.

This module *measures*; it never records. Everything here produces candidates for a person to
rule on, and `taxonomy_db.record_relation` is the only thing that writes.

**It lives in `Evelyn/tools/` because the engine has to be able to call it.** The computation
was written inside `scripts/curate_tag_relations.py`, which meant the only way to see a
candidate was to run a command by hand — so nothing ever reached the review queue, and the
first 155 relations were curated entirely outside the system that exists to curate them. The
script is now a thin CLI over this module.

Three filters, each for a different reason:

* **Support** — a pair must appear on at least `min_docs` documents and across `min_areas`
  independent parts of the corpus. Without the second, a single ten-tag appliance manual
  produces forty-five "relations" that are really one document's tag list.
* **Lift** — association above what the two terms' individual frequencies would predict.
* **Subjecthood** — both sides must be subjects, not facet values. A facet co-occurs with
  subjects *by construction*, so lift ranks `<subject>:type/profile` highly while asserting
  nothing an `RT` relation may mean (§3.4, §6.4).

Aliased surface forms are resolved first: a `UF` alias means the two terms *are* one concept
(§6.2), so counting them separately both splits a term's co-occurrence and keeps re-proposing
the aliased pair itself.
"""

import contextlib
import itertools
import logging
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

import evelyn_config as cfg
from Evelyn.tools import taxonomy_db
from Evelyn.tools.tag_librarian import is_subject_term

# Defaults, tunable from config. These are the values the C3 review was run at.
MIN_DOCS = getattr(cfg, "TAG_RELATION_MIN_DOCS", 5)
MIN_AREAS = getattr(cfg, "TAG_RELATION_MIN_AREAS", 3)
MIN_LIFT = getattr(cfg, "TAG_RELATION_MIN_LIFT", 3.0)

RELATION_PROPOSAL = "tag_relation"
# The queue is reviewed by one person. A cap is what stops a producer filling it faster
# than it drains — the admission queue reached 200/200 in an afternoon and silently
# refused every other producer with it (G3), because the cap is global.
MAX_PENDING = getattr(cfg, "TAG_RELATION_MAX_PENDING", 25)
# A rejected pair re-opens when the evidence for it has grown by this factor, the same
# shape as a ghost stub (`.234`): "these two aren't related" is a judgement about the
# documents that existed when it was made, and twice as many is a different question.
REEVIDENCE_FACTOR = getattr(cfg, "TAG_RELATION_REEVIDENCE_FACTOR", 2.0)

logger = logging.getLogger(__name__)


@dataclass
class RelationCandidate:
    """One pair a person has to rule on, with everything the decision needs.

    `same_category` is a prior, not a verdict. Two terms someone deliberately filed together
    are likely related and two filed apart need the argument made — but reviewed in full, 42
    of C3's 51 cross-category pairs turned out real, so it sorts the queue and decides nothing.
    """

    term_a: str
    term_b: str
    lift: float
    documents: int
    areas: int
    category_a: str
    category_b: str

    @property
    def pair(self) -> str:
        """The `a:b` form used as a proposal topic and by the `--record` CLI."""
        return f"{self.term_a}:{self.term_b}"

    @property
    def same_category(self) -> bool:
        """True when both terms sit in the same curated category, blanks excluded.

        Two terms nobody categorised share nothing; `"" == ""` would claim they do.
        """
        return bool(self.category_a) and self.category_a == self.category_b


def load_corpus() -> list[tuple[str, set[str]]]:
    """Tag sets from both substrates, each labelled with the area it came from.

    Read-only on both databases. Documents carrying one tag or more than twelve are excluded:
    one tag states no co-occurrence, and a very long tag list is a manifest rather than a
    statement about a document's subjects.

    Returns:
        list[tuple[str, set[str]]]: (area, tags) for every usable document and memory fact.
    """
    out: list[tuple[str, set[str]]] = []

    vault = sqlite3.connect(f"file:{cfg.VAULT_DB_PATH}?mode=ro", uri=True)
    try:
        for path, tags in vault.execute(
            "SELECT path, tags FROM vault_documents WHERE tags IS NOT NULL AND tags != ''"
        ):
            s = {t.strip() for t in str(tags).split(",") if t.strip()}
            if 1 < len(s) <= 12:
                out.append((str(path).split("/")[0], s))
    finally:
        vault.close()

    mem = sqlite3.connect(f"file:{cfg.MEMORY_DB_PATH}?mode=ro", uri=True)
    try:
        for (tags,) in mem.execute(
            "SELECT tags FROM context_entries "
            "WHERE status='live' AND tags IS NOT NULL AND TRIM(tags) != ''"
        ):
            s = {t.strip() for t in str(tags).split(",") if t.strip()}
            if 1 < len(s) <= 12:
                out.append(("memory", s))
    finally:
        mem.close()

    return out


def resolve_aliases(docs: list[tuple[str, set[str]]]) -> list[tuple[str, set[str]]]:
    """Collapse aliased surface forms onto their canonical term before counting.

    A `UF` alias means the two terms *are* one concept (§6.2), so counting them separately
    splits a term's co-occurrence in two and keeps proposing the aliased pair itself as a
    candidate forever — the reviewer decides, and the decision does not stick. Recording
    `romance` as an alias of `intimacy` left 31 documents still carrying the literal
    `romance`: correct for retrieval, which is what the pointer is for, and wrong for this
    count. A document whose tags collapse to a single term drops out, as one tag carries no
    co-occurrence.

    Args:
        docs: (area, tag set) pairs straight from the substrates.

    Returns:
        list[tuple[str, set[str]]]: The same documents with canonical terms.
    """
    aliases = taxonomy_db.get_aliases()
    alias_map = dict(aliases.items() if isinstance(aliases, dict) else aliases)
    if not alias_map:
        return docs
    out = []
    for area, tags in docs:
        resolved = {alias_map.get(t, t) for t in tags}
        if len(resolved) > 1:
            out.append((area, resolved))
    return out


def find_relation_candidates(
    min_docs: int = MIN_DOCS,
    min_areas: int = MIN_AREAS,
    min_lift: float = MIN_LIFT,
    docs: list[tuple[str, set[str]]] | None = None,
) -> tuple[list[RelationCandidate], dict[str, int]]:
    """Rank co-occurring subject pairs the vocabulary does not already relate.

    Args:
        min_docs: Documents a pair must appear on.
        min_areas: Independent corpus areas it must span.
        min_lift: Association above what the terms' own frequencies predict.
        docs: Pre-loaded corpus, for tests and for callers that already have one.

    Returns:
        tuple: (candidates heaviest-lift first, counters for what was excluded and why).
    """
    corpus = resolve_aliases(load_corpus() if docs is None else docs)
    n = len(corpus)
    stats = {"corpus": n, "facet_pairs": 0, "already_related": 0}
    if not n:
        return [], stats

    single: Counter = Counter()
    pair: Counter = Counter()
    areas: dict[tuple[str, str], set[str]] = defaultdict(set)
    for area, tags in corpus:
        single.update(tags)
        for a, b in itertools.combinations(sorted(tags), 2):
            pair[(a, b)] += 1
            areas[(a, b)].add(area)

    existing = {
        tuple(sorted((term, rel["tag"])))
        for term in single
        for rel in taxonomy_db.get_related_terms(term)
    }
    categories = {
        str(t["tag"]): str(t.get("category") or "").strip() for t in taxonomy_db.get_master_tags()
    }

    out: list[RelationCandidate] = []
    for (a, b), count in pair.items():
        if count < min_docs or len(areas[(a, b)]) < min_areas:
            continue
        lift = (count / n) / ((single[a] / n) * (single[b] / n))
        if lift < min_lift:
            continue
        if (a, b) in existing:
            stats["already_related"] += 1
            continue
        if not (is_subject_term(a) and is_subject_term(b)):
            # A statement about document class, not about subjects. Counted after the lift
            # filter so the number reports what was removed from the list, not every facet
            # pair in the corpus.
            stats["facet_pairs"] += 1
            continue
        out.append(
            RelationCandidate(
                term_a=a,
                term_b=b,
                lift=lift,
                documents=count,
                areas=len(areas[(a, b)]),
                category_a=categories.get(a, ""),
                category_b=categories.get(b, ""),
            )
        )

    out.sort(key=lambda c: (-c.lift, -c.documents, c.term_a, c.term_b))
    return out, stats


def _rejected_pairs() -> dict[str, dict[str, Any]]:
    """Pairs a reviewer has already turned down, keyed by `a:b`.

    Returns:
        dict[str, dict]: The rejected proposal row for each pair, empty if unreadable.
    """
    from Evelyn.tools import memory_db

    try:
        return {
            (p.get("topic") or "").strip(): p
            for p in memory_db.get_rejected_proposals(RELATION_PROPOSAL)
            if (p.get("topic") or "").strip()
        }
    except (sqlite3.Error, OSError) as exc:
        # Cannot prove anything was rejected, so suppress nothing: a pair that slips through
        # is one more review, a pair wrongly suppressed is invisible.
        logger.warning("[TAG RELATIONS] Could not read rejected relation proposals: %s", exc)
        return {}


def propose_tag_relations(limit: int | None = None) -> list[str]:
    """Raise review proposals for the strongest relation candidates.

    **This is what makes the candidates reachable.** The measurement has existed since
    `000.006.219`'s era as a script nobody called, so the first 155 relations were curated
    over a terminal and a conversation — outside the review system that exists for exactly
    this. A candidate nothing surfaces is a candidate nobody decides.

    Three things are never re-proposed: a pair the vocabulary already relates, a pair already
    pending, and a pair the reviewer rejected — unless its evidence has since grown by
    `REEVIDENCE_FACTOR`, which is the one thing that makes the old answer a different
    question. Each suppressed request increments `rejection_count`, so a pair the corpus keeps
    insisting on arrives at reconsideration with the number that argues for it.

    Args:
        limit: Maximum proposals to raise in one pass. Defaults to the room left under
            `MAX_PENDING`.

    Returns:
        list[str]: The `a:b` pairs newly proposed.
    """
    from Evelyn.tools import memory_db

    # Room first. Counting co-occurrence means reading every tagged document and fact in both
    # substrates, and this runs on the idle dispatcher's schedule — there is no sense paying
    # for the measurement to discover the queue is already full.
    try:
        pending = memory_db.get_pending_proposals(RELATION_PROPOSAL)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("[TAG RELATIONS] Could not read pending relation proposals: %s", exc)
        return []

    room = MAX_PENDING - len(pending)
    if limit is not None:
        room = min(room, limit)
    if room <= 0:
        logger.info(
            "[TAG RELATIONS] %d relation proposals already pending; not adding more.", len(pending)
        )
        return []

    candidates, _stats = find_relation_candidates()
    if not candidates:
        return []

    already = {(p.get("topic") or "").strip() for p in pending}
    rejected = _rejected_pairs()

    proposed: list[str] = []
    for candidate in candidates:
        if len(proposed) >= room:
            break
        pair = candidate.pair
        if pair in already:
            continue

        prior = rejected.get(pair)
        if prior is not None:
            threshold = float(prior.get("evidence") or 0) * REEVIDENCE_FACTOR
            if candidate.documents < threshold:
                with contextlib.suppress(sqlite3.Error, OSError):
                    count = memory_db.record_rejected_request(RELATION_PROPOSAL, pair)
                    logger.info(
                        "[TAG RELATIONS] '%s' was rejected at %s documents; now %d, below the "
                        "%.0f needed to re-open (asked %d time(s)).",
                        pair, prior.get("evidence"), candidate.documents, threshold, count,
                    )
                continue

        placement = (
            f"both filed under '{candidate.category_a}'" if candidate.same_category
            else f"filed apart: '{candidate.category_a or 'uncategorised'}' vs "
                 f"'{candidate.category_b or 'uncategorised'}'"
        )
        try:
            memory_db.insert_proposal(
                type=RELATION_PROPOSAL,
                source_ids=[],
                topic=pair,
                # The reviewer chooses the kind and its direction; the generator measures
                # association and cannot tell `related` from `narrower` (§6.4.1 question 3).
                suggested_category="related",
                reason=(
                    f"'{candidate.term_a}' and '{candidate.term_b}' appear together on "
                    f"{candidate.documents} documents across {candidate.areas} areas — "
                    f"{candidate.lift:.1f}x more often than their own frequencies predict, and "
                    f"{placement}."
                ),
                merged_observation="co-occurrence across the vault and memory",
                confidence="low",
                evidence=candidate.documents,
            )
        except (sqlite3.Error, OSError) as exc:
            logger.warning("[TAG RELATIONS] Could not propose '%s': %s", pair, exc)
            continue
        proposed.append(pair)

    if proposed:
        logger.info(
            "[TAG RELATIONS] Proposed %d relation(s) for review: %s",
            len(proposed), ", ".join(proposed),
        )
    return proposed


def apply_relation_decision(pair: str, kind: str) -> None:
    """Act on a reviewed relation candidate. The three outcomes are different decisions.

    * `related` — associative, symmetric, the common case (135 of the first 155).
    * `narrower` — `a` is a kind of `b`, and the order given *is* the claim. Sorting it would
      silently reverse about half: `lucid-dreaming` is a kind of `dream` and comes second
      alphabetically.
    * `alias` — not a relation at all. The two are one concept under two names (§6.2), so `a`
      is retired onto `b` and its relations move with it. **This one removes a term from the
      vocabulary**, which is why it is named rather than inferred.

    The generator measures association and cannot tell these apart (§6.4.1 question 3), so the
    reviewer supplies both the kind and, for the directional two, the order.

    Args:
        pair: `a:b`, ordered as the reviewer left it.
        kind: `related`, `narrower` or `alias`.

    Raises:
        ValueError: The pair is not two distinct terms, or the kind is not one of the three.
    """
    from Evelyn.tools import tag_librarian

    if ":" not in (pair or ""):
        raise ValueError("Relation decision needs a pair in 'a:b' form")
    term_a, term_b = (part.strip() for part in pair.split(":", 1))
    if not term_a or not term_b or term_a == term_b:
        raise ValueError(f"'{pair}' is not two distinct terms")

    if kind == "alias":
        if not tag_librarian.retire_term(term_a, term_b):
            raise ValueError(f"Term '{term_a}' could not be retired onto '{term_b}'")
    elif kind in ("related", "narrower"):
        taxonomy_db.record_relation(
            term_a, term_b, kind=kind, tier="reviewed",
            note="Reviewed from a co-occurrence candidate.",
        )
    else:
        raise ValueError(f"'{kind}' is not one of: related, narrower, alias")
