# test_relation_candidates_are_ranked.py
# date created: 2026-09-25
# date modified: 2026-09-25 19:20:57
# tags: #taxonomy, #relations, #curation, #testing

"""Candidates carry the curated category co-membership that sorts them for review (F6).

Lift rediscovers association statistically and has no judgement about what belongs with what.
The 603 category groupings from Pass 1 do — a person made them — and they are already paid
for. Applying them as a prior separates `cpap:sleep` from `hydration:rest`: similar
co-occurrence, one a relation and the other two things that land in the same daily note.

Measured on the 153 clean candidates the split is 102 same-category / 51 cross. It is a prior
and **not a verdict** — reviewed in full, 42 of the 51 cross-category pairs turned out real,
and `cat:sleep` was the spurious example here until the vault's owner said the cat does in
fact decide how the night goes. That is why `same_category` is a field on the candidate and
never a filter: nothing is dropped by it.

`cat`/`sleep` below is synthetic cross-category data, which is all it ever was — the pair is
filed in two categories, which is the only thing these tests assert.
"""

import pytest

from Evelyn.tools import tag_relations, taxonomy_db

CATEGORIES = {
    "cpap": "sleep-rest",
    "sleep": "sleep-rest",
    "cat": "domestic-life",
    # Registered but never categorised — Pass 1 left one term blank, and a naive `ca == cb`
    # would call two such terms co-members of the same category.
    "widget": "",
    "gadget": "",
}


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry is production data; this fixture is the whole vocabulary under test."""
    monkeypatch.setattr(taxonomy_db, "get_related_terms", lambda _t: [])
    monkeypatch.setattr(
        taxonomy_db, "get_master_tags",
        lambda: [{"tag": t, "category": c} for t, c in CATEGORIES.items()],
    )
    monkeypatch.setattr(taxonomy_db, "get_aliases", lambda: {})


def _corpus(*pairs: tuple[str, str]) -> list[tuple[str, set[str]]]:
    docs: list[tuple[str, set[str]]] = []
    for pair in pairs:
        docs += [(f"area{i % 4}", {pair[0], pair[1]}) for i in range(12)]
    docs += [(f"area{i % 4}", {"unrelated", f"filler{i}"}) for i in range(200)]
    return docs


def _found(docs) -> list[tag_relations.RelationCandidate]:
    candidates, _stats = tag_relations.find_relation_candidates(
        min_docs=3, min_areas=2, min_lift=3.0, docs=docs
    )
    return candidates


def test_the_same_category_pair_and_the_cross_one_are_distinguished() -> None:
    """Identical co-occurrence, different buckets — this is the whole point of the split."""
    by_pair = {c.pair: c for c in _found(_corpus(("cpap", "sleep"), ("cat", "sleep")))}

    assert by_pair["cpap:sleep"].same_category
    assert not by_pair["cat:sleep"].same_category


def test_a_blank_category_is_not_treated_as_a_match() -> None:
    """Two terms nobody categorised share nothing. `"" == ""` would claim they do.

    The pair must clear the lift filter to reach the split at all — an earlier version of
    this test used terms that did not, so it passed against the bug it was written for.
    """
    found = _found(_corpus(("widget", "gadget")))

    assert [c.pair for c in found] == ["gadget:widget"], "the pair must reach the split"
    assert not found[0].same_category


def test_the_split_sorts_and_never_filters() -> None:
    """A ranking must re-order, never drop — C3 still has to see all of them.

    `cat:sleep` is the proof this matters: it was the canonical cross-category example and
    turned out to be real.
    """
    found = _found(_corpus(("cpap", "sleep"), ("cat", "sleep")))

    assert {c.pair for c in found} == {"cpap:sleep", "cat:sleep"}


def test_the_category_that_placed_a_pair_is_carried() -> None:
    """A reviewer disagreeing with the bucket needs to see what put it there."""
    candidate = next(c for c in _found(_corpus(("cat", "sleep"))) if c.pair == "cat:sleep")

    assert candidate.category_a == "domestic-life"
    assert candidate.category_b == "sleep-rest"


def test_an_aliased_surface_form_is_counted_as_its_canonical_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `UF` alias means the two terms are one concept (§6.2), so they cannot be a pair.

    Recording `romance` as an alias of `intimacy` left 31 documents still carrying the literal
    `romance` — correct for retrieval, which is what the pointer is for, and wrong for this
    count. Unresolved, the generator proposed `intimacy:romance` again on the very next run,
    so the reviewer's decision decided nothing.
    """
    monkeypatch.setattr(taxonomy_db, "get_aliases", lambda: {"kitty": "cat"})

    assert _found(_corpus(("kitty", "cat"))) == [], "an alias pair is one term, never a candidate"


def test_a_document_collapsing_to_one_term_leaves_the_corpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`load_corpus` already excludes single-tag documents; resolution must not smuggle them back.

    They carry no co-occurrence but do sit in the lift denominator, so keeping them quietly
    deflates every lift in the report.
    """
    monkeypatch.setattr(taxonomy_db, "get_aliases", lambda: {"kitty": "cat"})
    docs = [("area0", {"kitty", "cat"})] * 5 + [("area1", {"cpap", "sleep"})] * 5

    _candidates, stats = tag_relations.find_relation_candidates(
        min_docs=3, min_areas=2, min_lift=3.0, docs=docs
    )
    assert stats["corpus"] == 5


def test_a_pair_the_vocabulary_already_relates_is_not_re_proposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recording a relation is the one answer that must stop the pair coming back."""
    docs = _corpus(("cpap", "sleep"))
    assert [c.pair for c in _found(docs)] == ["cpap:sleep"]

    monkeypatch.setattr(
        taxonomy_db, "get_related_terms",
        lambda t: [{"tag": "sleep", "relation": "related"}] if t == "cpap" else [],
    )
    candidates, stats = tag_relations.find_relation_candidates(
        min_docs=3, min_areas=2, min_lift=3.0, docs=docs
    )

    assert candidates == []
    assert stats["already_related"] == 1, "the suppression must be counted, not silent"
