# test_relation_candidates_are_subjects.py
# date created: 2026-09-25
# date modified: 2026-09-25 19:20:57
# tags: #taxonomy, #relations, #facets, #testing

"""A relation candidate must be two subjects, never a subject and a facet (C1).

The generator ranks pairs by co-occurrence lift, which is the right measure for association
and the wrong one for this: facet values co-occur with subjects *by construction*. Documents
about ttrpg really do tend to be profiles, so `ttrpg:type/profile` scores highly while
asserting nothing about an association between subjects — the only thing an `RT` relation is
allowed to mean (§6.4). Measured on the live corpus it was 14 of 167 candidates.

This is the same category error the subject pass guards in `v000.006.219`, in a second place,
so it reuses the same predicate rather than restating the prefix list.
"""

import pytest

from Evelyn.tools import tag_relations, taxonomy_db
from Evelyn.tools.tag_librarian import FACET_PREFIXES, is_subject_term


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry is production data; the corpus in each test is the whole input."""
    monkeypatch.setattr(taxonomy_db, "get_related_terms", lambda _t: [])
    monkeypatch.setattr(taxonomy_db, "get_master_tags", lambda: [])
    monkeypatch.setattr(taxonomy_db, "get_aliases", lambda: {})


def _corpus_of(pair: tuple[str, str], times: int = 12) -> list[tuple[str, set[str]]]:
    """A pair that co-occurs everywhere it appears, and filler so lift has a baseline."""
    docs = [(f"area{i % 4}", {pair[0], pair[1]}) for i in range(times)]
    docs += [(f"area{i % 4}", {"unrelated", f"filler{i}"}) for i in range(200)]
    return docs


def _pairs(docs) -> list[str]:
    found, _stats = tag_relations.find_relation_candidates(
        min_docs=3, min_areas=2, min_lift=3.0, docs=docs
    )
    return [c.pair for c in found]


def test_a_subject_pair_is_still_proposed() -> None:
    """The guard must not be a blanket filter — this is the case it has to let through."""
    assert _pairs(_corpus_of(("hvac", "thermostat"))) == ["hvac:thermostat"]


@pytest.mark.parametrize("facet", [f"{p}example" for p in FACET_PREFIXES])
def test_a_facet_paired_with_a_subject_is_not_proposed(facet) -> None:
    assert _pairs(_corpus_of(("ttrpg", facet))) == []


def test_the_exclusion_is_counted_rather_than_silent() -> None:
    """A filter that drops rows without saying so reads as a corpus that has no such pairs."""
    _found, stats = tag_relations.find_relation_candidates(
        min_docs=3, min_areas=2, min_lift=3.0, docs=_corpus_of(("ttrpg", "type/profile"))
    )
    assert stats["facet_pairs"] == 1


def test_the_generator_uses_the_subject_pass_predicate() -> None:
    """Reuse, not a second prefix list — the two must never be able to disagree."""
    import inspect

    src = inspect.getsource(tag_relations)
    assert "is_subject_term" in src
    for prefix in FACET_PREFIXES:
        assert f'"{prefix}"' not in src, f"{prefix} restated instead of reusing the predicate"


def test_the_predicate_itself_still_answers_both_ways() -> None:
    assert is_subject_term("thermostat")
    assert not is_subject_term("type/profile")
