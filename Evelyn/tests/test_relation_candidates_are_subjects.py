# test_relation_candidates_are_subjects.py
# date created: 2026-09-25
# date modified: 2026-09-25 18:24:27
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

from Evelyn.tools import taxonomy_db
from Evelyn.tools.tag_librarian import FACET_PREFIXES, is_subject_term
from scripts import curate_tag_relations


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry is production data; the corpus below is the whole input under test.

    `get_master_tags` is stubbed for the category split (F6) rather than left to read the
    live taxonomy — the bucket a pair lands in is irrelevant here, but reading production to
    decide it is not hermetic.
    """
    monkeypatch.setattr(taxonomy_db, "get_related_terms", lambda _t: [])
    monkeypatch.setattr(taxonomy_db, "get_master_tags", lambda: [])


def _corpus_of(pair: tuple[str, str], times: int = 12) -> list[tuple[str, set[str]]]:
    """A pair that co-occurs everywhere it appears, and filler so lift has a baseline."""
    docs: list[tuple[str, set[str]]] = [
        (f"area{i % 4}", {pair[0], pair[1]}) for i in range(times)
    ]
    docs += [(f"area{i % 4}", {"unrelated", f"filler{i}"}) for i in range(200)]
    return docs


def _candidates(capsys, monkeypatch: pytest.MonkeyPatch, docs) -> list[str]:
    monkeypatch.setattr(curate_tag_relations, "_corpus", lambda: docs)
    curate_tag_relations.report(min_docs=3, min_areas=2, min_lift=3.0, limit=100)
    out = capsys.readouterr().out
    rows = [line.split() for line in out.splitlines() if line.strip()]
    return [
        r[3] for r in rows
        if len(r) >= 4 and r[0].replace(".", "").isdigit()
        and r[1].isdigit() and r[2].isdigit() and ":" in r[3]
    ]


def test_a_subject_pair_is_still_proposed(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard must not be a blanket filter — this is the case it has to let through."""
    assert _candidates(capsys, monkeypatch, _corpus_of(("hvac", "thermostat"))) == [
        "hvac:thermostat"
    ]


@pytest.mark.parametrize("facet", [f"{p}example" for p in FACET_PREFIXES])
def test_a_facet_paired_with_a_subject_is_not_proposed(
    facet, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _candidates(capsys, monkeypatch, _corpus_of(("ttrpg", facet))) == []


def test_the_exclusion_is_reported_rather_than_silent(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A filter that drops rows without saying so reads as a corpus that has no such pairs."""
    monkeypatch.setattr(curate_tag_relations, "_corpus", lambda: _corpus_of(("ttrpg", "type/profile")))
    curate_tag_relations.report(min_docs=3, min_areas=2, min_lift=3.0, limit=100)

    assert "1 facet pair(s) excluded" in capsys.readouterr().out


def test_the_generator_uses_the_subject_pass_predicate() -> None:
    """Reuse, not a second prefix list — the two must never be able to disagree."""
    import inspect

    src = inspect.getsource(curate_tag_relations)
    assert "is_subject_term" in src
    for prefix in FACET_PREFIXES:
        assert f'"{prefix}"' not in src, f"{prefix} restated instead of reusing the predicate"


def test_the_predicate_itself_still_answers_both_ways() -> None:
    assert is_subject_term("thermostat")
    assert not is_subject_term("type/profile")
