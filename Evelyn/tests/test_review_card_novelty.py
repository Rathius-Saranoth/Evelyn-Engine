# test_review_card_novelty.py
# date created: 2026-09-24
# date modified: 2026-09-24 18:14:27
# tags: #review, #taxonomy, #novelty, #testing

"""The review card answers "is this term new?" by lookup, not by cosine distance (B2).

The card used to embed the whole observation, take the nearest term's distance, and cut it
into `Aligned` / `Related` / `Novel` at 0.40 and 0.55. Measured over 144 labelled facts that
distance separated "every tag already registered" from "introduces a new term" by 0.005 — the
best split anywhere on the range scored 66.7% against a 62.5% majority baseline, and the whole
p10–p90 of the distribution straddled both cut points. No query shape rescued it; the question
simply was not a vector question, because the extraction already carries its proposed tags and
membership of a controlled vocabulary is a set.

The vector keeps the job it is good at and that §6.1 asks for: given an unregistered term,
name the nearest registered one so approval is a comparison rather than an act of recall.
"""

import pytest

import evelyn_server
from Evelyn.tools import tag_librarian, taxonomy_db


@pytest.fixture
def vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    """A small registry, and a vector lookup that answers without touching Chroma."""
    for term in ("audiobook", "coffee", "routine"):
        taxonomy_db.upsert_master_tag(term, category="media-music")
    taxonomy_db.record_alias("audio-book", "audiobook", tier="reviewed")
    taxonomy_db.invalidate_alias_cache()

    nearest = {"dungeon-crawler-carl": ("audiobook", 0.31), "espresso": ("coffee", 0.12)}
    monkeypatch.setattr(
        tag_librarian, "nearest_registered_term",
        lambda phrase: (*nearest.get(phrase, (None, 1.0)), 0.1),
    )
    monkeypatch.setattr(
        "Evelyn.tools.chroma_rag.query_collection",
        lambda *a, **k: [
            {"metadata": {"tag": "audiobook"}, "distance": 0.22},
            {"metadata": {"tag": "coffee"}, "distance": 0.38},
        ],
    )


def _item(**over) -> dict:
    base = {"id": 101, "observation": "Alex enjoys the Dungeon Crawler Carl audiobook series."}
    return {**base, **over}


def test_a_fact_whose_tags_are_all_registered_is_aligned(vocabulary: None) -> None:
    out = evelyn_server._enrich_extraction_with_taxonomy(_item(tags="audiobook, routine"))

    assert out["alignment_label"] == "Aligned"
    assert out["novelty_score"] == 0.0
    assert out["unregistered_tags"] == []


def test_an_unregistered_tag_makes_the_fact_novel(vocabulary: None) -> None:
    """And it names which tag, and what the vocabulary has instead."""
    out = evelyn_server._enrich_extraction_with_taxonomy(
        _item(tags="dungeon-crawler-carl, audiobook")
    )

    assert out["alignment_label"] == "Novel"
    assert out["unregistered_tags"] == [
        {"tag": "dungeon-crawler-carl", "nearest": "audiobook", "distance": 0.31}
    ]
    assert out["novelty_score"] == 0.31


def test_the_score_reports_the_most_novel_term(vocabulary: None) -> None:
    """One term far from everything is the reviewer's problem, not the average."""
    out = evelyn_server._enrich_extraction_with_taxonomy(
        _item(tags="espresso, dungeon-crawler-carl")
    )

    assert out["novelty_score"] == 0.31
    assert {u["tag"] for u in out["unregistered_tags"]} == {"espresso", "dungeon-crawler-carl"}


def test_a_tag_that_is_only_an_alias_is_not_new(vocabulary: None) -> None:
    """A fact phrased the retired way resolves to a registered term, so nothing is minted."""
    out = evelyn_server._enrich_extraction_with_taxonomy(_item(tags="audio-book"))

    assert out["alignment_label"] == "Aligned"
    assert out["unregistered_tags"] == []


def test_an_untagged_fact_is_labelled_as_such(vocabulary: None) -> None:
    """Nothing was proposed, so there is nothing to call aligned or novel."""
    out = evelyn_server._enrich_extraction_with_taxonomy(_item(tags=""))

    assert out["alignment_label"] == "Untagged"
    assert out["unregistered_tags"] == []


def test_suggestions_exclude_tags_the_fact_already_carries(vocabulary: None) -> None:
    """The card asks what else the vocabulary holds; echoing back its own tags answers nothing."""
    out = evelyn_server._enrich_extraction_with_taxonomy(_item(tags="audiobook"))

    assert "audiobook" not in out["suggested_tags"]
    assert out["suggested_tags"] == ["coffee"]


def test_a_failed_lookup_does_not_invent_a_verdict(vocabulary: None) -> None:
    """Better to say nothing is known than to label every fact novel because a query broke."""
    import Evelyn.tools.chroma_rag as cr

    def _explode(*a, **k):
        raise RuntimeError("collection unavailable")

    original = cr.query_collection
    cr.query_collection = _explode
    try:
        out = evelyn_server._enrich_extraction_with_taxonomy(_item(tags="audiobook"))
    finally:
        cr.query_collection = original

    assert out["alignment_label"] == "Untagged"
    assert out["suggested_tags"] == []
    assert out["unregistered_tags"] == []
