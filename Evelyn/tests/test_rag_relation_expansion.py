# test_rag_relation_expansion.py
# date created: 2026-09-26 20:30:47
# date modified: 2026-09-26 20:31:41
# tags:

"""Tests for the query-time relation re-rank (vault-tag-taxonomy §6.4).

The feature is **off by default** and the measurement says to leave it that way for now;
these tests pin the mechanism's behaviour so the decision can be revisited on evidence
rather than re-derived from scratch.
"""

import pytest

from Evelyn.tools import chroma_rag

# --------------------------------------------------------------------------------------
# _chunk_tags
# --------------------------------------------------------------------------------------


def test_tags_parse_from_a_stringified_list():
    """Chroma metadata values are scalars, so a tag list arrives as its string repr."""
    chunk = {"metadata": {"tags": "['type/profile', 'archetype', 'lore']"}}
    assert chroma_rag._chunk_tags(chunk) == {"type/profile", "archetype", "lore"}


def test_tags_parse_from_a_plain_comma_string():
    chunk = {"metadata": {"tags": "rag, llm, information-retrieval"}}
    assert chroma_rag._chunk_tags(chunk) == {"rag", "llm", "information-retrieval"}


def test_tags_parse_from_a_real_list():
    chunk = {"metadata": {"tags": ["Rag", "LLM"]}}
    assert chroma_rag._chunk_tags(chunk) == {"rag", "llm"}


@pytest.mark.parametrize("meta", [{}, {"tags": ""}, {"tags": None}, None])
def test_untagged_chunks_yield_nothing(meta):
    assert chroma_rag._chunk_tags({"metadata": meta}) == set()


def test_missing_metadata_key_entirely():
    assert chroma_rag._chunk_tags({}) == set()


# --------------------------------------------------------------------------------------
# _expand_tags_by_relation
# --------------------------------------------------------------------------------------


@pytest.fixture
def one_seed(monkeypatch):
    """Seed from the single closest chunk only.

    The default is the top 3, which in a short fixture list makes every chunk a seed — and
    a seed is excluded from its own expansion, so nothing can be boosted. Narrowing it here
    keeps each test about the boost rather than about the seed window.
    """
    monkeypatch.setattr(chroma_rag.cfg, "RAG_RELATION_SEED_K", 1, raising=False)


@pytest.fixture
def fake_relations(monkeypatch):
    graph = {
        "sleep":  [{"tag": "dream", "weight": 0.4}, {"tag": "rest", "weight": 0.6}],
        "dream":  [{"tag": "sleep", "weight": 0.4}],
        "coffee": [{"tag": "rest", "weight": 0.2}],
    }
    from Evelyn.tools import taxonomy_db
    monkeypatch.setattr(taxonomy_db, "get_related_terms", lambda t: graph.get(t, []))
    return graph


def test_expansion_returns_related_terms_with_weights(fake_relations):
    assert chroma_rag._expand_tags_by_relation({"sleep"}) == {"dream": 0.4, "rest": 0.6}


def test_seeds_are_excluded_from_their_own_expansion(fake_relations):
    """`sleep` and `dream` relate to each other; neither should boost itself."""
    out = chroma_rag._expand_tags_by_relation({"sleep", "dream"})
    assert "sleep" not in out and "dream" not in out
    assert out == {"rest": 0.6}


def test_a_term_reachable_from_two_seeds_keeps_the_strongest_path(fake_relations):
    out = chroma_rag._expand_tags_by_relation({"sleep", "coffee"})
    assert out["rest"] == 0.6  # not 0.2, and not summed


def test_no_seeds_is_a_no_op(fake_relations):
    assert chroma_rag._expand_tags_by_relation(set()) == {}


def test_unknown_seed_expands_to_nothing(fake_relations):
    assert chroma_rag._expand_tags_by_relation({"nonexistent"}) == {}


# --------------------------------------------------------------------------------------
# _apply_relation_boost
# --------------------------------------------------------------------------------------


def _chunk(source, dist, tags=""):
    return {"source": source, "distance": dist, "metadata": {"tags": tags}}


def test_untagged_top_results_make_the_pass_a_no_op(fake_relations):
    """Measured on the live corpus: this is the common case, not the edge case.

    Context entries carry no tags in Chroma metadata and dominate the top of the ranking,
    so the seed set is usually empty and the whole pass does nothing.
    """
    chunks = [_chunk("sqlite::context_entry::1", 0.10), _chunk("sqlite::context_entry::2", 0.20)]
    out, stats = chroma_rag._apply_relation_boost(chunks)
    assert stats == {}
    assert [c["distance"] for c in out] == [0.10, 0.20]


def test_a_related_chunk_is_moved_closer(one_seed, fake_relations):
    chunks = [_chunk("a.md", 0.10, "sleep"), _chunk("b.md", 0.20, "dream")]
    out, stats = chroma_rag._apply_relation_boost(chunks)
    assert stats["boosted"] == 1
    moved = next(c for c in out if c["source"] == "b.md")
    assert moved["distance"] < 0.20
    assert moved["relation_matched"] == ["dream"]


def test_an_unrelated_chunk_is_untouched(one_seed, fake_relations):
    chunks = [_chunk("a.md", 0.10, "sleep"), _chunk("b.md", 0.20, "taxidermy")]
    out, _ = chroma_rag._apply_relation_boost(chunks)
    assert next(c for c in out if c["source"] == "b.md")["distance"] == 0.20


def test_the_boost_is_capped(one_seed, fake_relations, monkeypatch):
    """Many matching tags must not let tag count outrank semantic distance."""
    monkeypatch.setattr(chroma_rag.cfg, "RAG_RELATION_BOOST_CAP", 0.15, raising=False)
    chunks = [_chunk("a.md", 0.10, "sleep"), _chunk("b.md", 0.90, "dream, rest")]
    out, _ = chroma_rag._apply_relation_boost(chunks)
    b = next(c for c in out if c["source"] == "b.md")
    assert b["distance"] == pytest.approx(0.90 * 0.85)
    assert b["relation_boost"] == pytest.approx(0.15)


def test_results_come_back_sorted_by_distance(one_seed, fake_relations):
    chunks = [_chunk("a.md", 0.10, "sleep"), _chunk("far.md", 0.80, "dream"), _chunk("mid.md", 0.30)]
    out, _ = chroma_rag._apply_relation_boost(chunks)
    assert [c["distance"] for c in out] == sorted(c["distance"] for c in out)


def test_stats_report_whether_anything_moved(one_seed, fake_relations):
    chunks = [_chunk("a.md", 0.10, "sleep"), _chunk("b.md", 0.20, "taxidermy")]
    _, stats = chroma_rag._apply_relation_boost(chunks)
    assert stats["seeds"] == ["sleep"]
    assert stats["reordered"] is False


def test_the_feature_is_off_by_default():
    """Measured: it promotes a bibliography page over the note that answers the question."""
    import evelyn_config as cfg
    assert cfg.RAG_RELATION_EXPANSION_ENABLED is False
