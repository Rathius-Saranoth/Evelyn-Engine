# test_relation_candidates_are_ranked.py
# date created: 2026-09-25
# date modified: 2026-09-25 18:57:38
# tags: #taxonomy, #relations, #curation, #testing

"""Candidates are split by curated category co-membership before review (F6).

Lift rediscovers association statistically and has no judgement about what belongs with what.
The 603 category groupings from Pass 1 do — a person made them — and they are already paid
for. Applying them as a prior separates `cpap:sleep` from `hydration:rest`: similar
co-occurrence, one a relation and the other two things that land in the same daily note.

Measured on the 153 clean candidates the split is 102 same-category / 51 cross. It is a prior
and not a verdict — reviewed in full, 42 of the 51 cross-category pairs turned out real, and
`cat:sleep` was the spurious example here until the vault's owner said the cat does in fact
decide how the night goes. Reviewing 153 undifferentiated pairs is the expensive part of C3;
this is the cheap thing that makes it cheaper.

The fixtures below keep `cat`/`sleep` as synthetic cross-category data, which is all they ever
were — the pair is filed in two categories, which is the only thing these tests assert.

No UI, no schema change — the categories are read, not written.
"""

import pytest

from Evelyn.tools import taxonomy_db
from scripts import curate_tag_relations

CATEGORIES = {
    "cpap": "sleep-rest",
    "sleep": "sleep-rest",
    "cat": "domestic-life",
    # Registered but never categorised — Pass 1 left one term blank, and a naive
    # `ca == cb` would call two such terms co-members of the same category.
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


def _sections(capsys, monkeypatch: pytest.MonkeyPatch, docs) -> dict[str, list[str]]:
    """Parse the report back into {bucket: [candidates]}."""
    monkeypatch.setattr(curate_tag_relations, "_corpus", lambda: docs)
    curate_tag_relations.report(min_docs=3, min_areas=2, min_lift=3.0, limit=100)

    out: dict[str, list[str]] = {"SAME CATEGORY": [], "CROSS CATEGORY": []}
    bucket = None
    for line in capsys.readouterr().out.splitlines():
        for name in out:
            if line.startswith(name):
                bucket = name
        fields = line.split()
        if (
            bucket and len(fields) >= 4
            and fields[0].replace(".", "").isdigit()
            and fields[1].isdigit() and fields[2].isdigit() and ":" in fields[3]
        ):
            out[bucket].append(fields[3])
    return out


def test_the_same_category_pair_and_the_cross_one_are_separated(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identical co-occurrence, different verdicts — this is the whole point of the split."""
    sections = _sections(capsys, monkeypatch, _corpus(("cpap", "sleep"), ("cat", "sleep")))

    assert sections["SAME CATEGORY"] == ["cpap:sleep"]
    assert sections["CROSS CATEGORY"] == ["cat:sleep"]


def test_a_blank_category_is_not_treated_as_a_match(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two terms nobody categorised share nothing. `"" == ""` would claim they do.

    The pair must clear the lift filter to reach the split at all — an earlier version of
    this test used terms that did not, so it passed against the bug it was written for.
    """
    sections = _sections(capsys, monkeypatch, _corpus(("widget", "gadget")))

    assert sections["CROSS CATEGORY"] == ["gadget:widget"], "the pair must reach the split"
    assert sections["SAME CATEGORY"] == []


def test_nothing_is_dropped_by_the_split(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ranking must re-order, never filter — C3 still has to see all of them."""
    docs = _corpus(("cpap", "sleep"), ("cat", "sleep"))
    sections = _sections(capsys, monkeypatch, docs)

    monkeypatch.setattr(curate_tag_relations, "_corpus", lambda: docs)
    curate_tag_relations.report(min_docs=3, min_areas=2, min_lift=3.0, limit=100)
    total = capsys.readouterr().out

    found = len(sections["SAME CATEGORY"]) + len(sections["CROSS CATEGORY"])
    assert f"{found} candidates ({found - 1} same-category, 1 cross)" in total


def test_both_buckets_are_printed_even_when_empty(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent heading reads as "no such candidates"; an empty one says which it is."""
    monkeypatch.setattr(curate_tag_relations, "_corpus", lambda: _corpus(("cpap", "sleep")))
    curate_tag_relations.report(min_docs=3, min_areas=2, min_lift=3.0, limit=100)
    out = capsys.readouterr().out

    assert "SAME CATEGORY — 1" in out
    assert "CROSS CATEGORY — 0" in out
    assert "(none)" in out


def test_the_category_that_placed_a_pair_is_shown(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reviewer disagreeing with the bucket needs to see what put it there."""
    monkeypatch.setattr(curate_tag_relations, "_corpus", lambda: _corpus(("cat", "sleep")))
    curate_tag_relations.report(min_docs=3, min_areas=2, min_lift=3.0, limit=100)
    out = capsys.readouterr().out

    assert "domestic-life | sleep-rest" in out


def test_an_aliased_surface_form_is_counted_as_its_canonical_term(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `UF` alias means the two terms are one concept (§6.2), so they cannot be a pair.

    Recording `romance` as an alias of `intimacy` left 31 documents still carrying the literal
    `romance` — correct for retrieval, which is what the pointer is for, and wrong for this
    count. Unresolved, the generator proposed `intimacy:romance` again on the very next run,
    so the reviewer's decision decided nothing.
    """
    monkeypatch.setattr(taxonomy_db, "get_aliases", lambda: {"kitty": "cat"})
    monkeypatch.setattr(
        taxonomy_db, "get_master_tags",
        lambda: [{"tag": t, "category": c} for t, c in {**CATEGORIES, "kitty": ""}.items()],
    )

    sections = _sections(capsys, monkeypatch, _corpus(("kitty", "cat")))

    assert sections["SAME CATEGORY"] == []
    assert sections["CROSS CATEGORY"] == [], "an alias pair is one term, never a candidate"


def test_a_document_collapsing_to_one_term_leaves_the_corpus(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_corpus` already excludes single-tag documents; resolution must not smuggle them back.

    They carry no co-occurrence but do sit in the lift denominator, so keeping them quietly
    deflates every lift in the report.
    """
    monkeypatch.setattr(taxonomy_db, "get_aliases", lambda: {"kitty": "cat"})
    docs = [("area0", {"kitty", "cat"})] * 5 + [("area1", {"cpap", "sleep"})] * 5

    monkeypatch.setattr(curate_tag_relations, "_corpus", lambda: docs)
    curate_tag_relations.report(min_docs=3, min_areas=2, min_lift=3.0, limit=100)

    assert "Corpus: 5 tagged documents" in capsys.readouterr().out
