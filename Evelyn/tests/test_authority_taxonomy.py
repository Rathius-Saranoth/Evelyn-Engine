# test_authority_taxonomy.py
# date created: 2026-09-29
# date modified: 2026-09-29 19:15:09
# tags: #taxonomy, #authority, #relevance, #testing

"""Targeted unit tests for institutional authority lookup, relevance ranking, and acronym handling."""

import json
from unittest.mock import patch

from scripts import lookup_authority_taxonomy as lat


def test_normalize_atom():
    """Verify atom normalization to controlled kebab-case."""
    assert lat.normalize_atom("Craft festivals") == "craft-festivals"
    assert lat.normalize_atom("Art, Fang") == "art-fang"
    assert lat.normalize_atom("FAST & LOC") == "fast-loc"


def test_score_authority_result_relevance_hierarchy():
    """Verify authority scoring prioritizes LOC, dual authority, and topical subjects over corporate noise."""
    query = "craft"
    query_atom = "craft"

    dual_topical = {
        "title": "Craft festivals",
        "canonical_tag": "craft-festivals",
        "source": "FAST + LOC",
        "marc_tag": "150",
        "matched_via": None,
    }
    loc_topical = {
        "title": "Craft sticks",
        "canonical_tag": "craft-sticks",
        "source": "LOC",
        "marc_tag": "150",
        "matched_via": None,
    }
    fast_topical = {
        "title": "Glass craft",
        "canonical_tag": "glass-craft",
        "source": "FAST",
        "marc_tag": "150",
        "matched_via": None,
    }
    fast_corporate_acronym = {
        "title": "Indiana University Center for Research into Anthropological Foundations of Technology",
        "canonical_tag": "indiana-university-center-for-research",
        "source": "FAST",
        "marc_tag": "110",
        "matched_via": "CRAFT",
    }

    score_dual = lat.score_authority_result(dual_topical, query, query_atom)
    score_loc = lat.score_authority_result(loc_topical, query, query_atom)
    score_fast = lat.score_authority_result(fast_topical, query, query_atom)
    score_corp = lat.score_authority_result(fast_corporate_acronym, query, query_atom)

    # Dual authority + prefix match should rank higher than single LOC
    assert score_dual > score_loc
    # LOC topical heading should rank higher than single FAST topical
    assert score_loc > score_fast
    # Topical concept must dramatically outrank corporate acronym noise
    assert score_fast > score_corp
    # Corporate acronym should have a negative or low score
    assert score_corp < 0


def test_score_authority_result_exact_matches():
    """Exact heading and atom matches receive top tier weighting."""
    query = "craft"
    query_atom = "craft"

    exact_atom = {
        "title": "Craft",
        "canonical_tag": "craft",
        "source": "FAST + LOC",
        "marc_tag": "150",
        "matched_via": None,
    }
    partial_match = {
        "title": "Craft malls",
        "canonical_tag": "craft-malls",
        "source": "FAST + LOC",
        "marc_tag": "150",
        "matched_via": None,
    }

    assert lat.score_authority_result(exact_atom, query, query_atom) > lat.score_authority_result(
        partial_match, query, query_atom
    )


def test_query_authority_diagnostic_ranking_and_merging():
    """Verify query_authority_diagnostic ranks and merges candidates properly without starving LOC."""
    mock_fast_results = [
        {
            "title": "Indiana University CRAFT",
            "canonical_tag": "indiana-university-craft",
            "url": "https://id.worldcat.org/fast/fst1",
            "source": "FAST",
            "marc_tag": "110",
            "match_type": "alt",
            "matched_via": "CRAFT",
            "variants": {"craft"},
        },
        {
            "title": "Craft festivals",
            "canonical_tag": "craft-festivals",
            "url": "https://id.worldcat.org/fast/fst2",
            "source": "FAST",
            "marc_tag": "150",
            "match_type": "alt",
            "matched_via": "Craft fairs",
            "variants": {"craft-fairs"},
        },
    ]

    mock_loc_results = [
        {
            "title": "Craft festivals",
            "canonical_tag": "craft-festivals",
            "url": "https://id.loc.gov/authorities/subjects/sh85033716",
            "source": "LOC",
            "marc_tag": "150",
            "match_type": "auth",
            "matched_via": None,
            "variants": set(),
        },
        {
            "title": "Craft sticks",
            "canonical_tag": "craft-sticks",
            "url": "https://id.loc.gov/authorities/subjects/sh2007004961",
            "source": "LOC",
            "marc_tag": "150",
            "match_type": "auth",
            "matched_via": None,
            "variants": set(),
        },
    ]

    lat._QUERY_CACHE.clear()
    test_term = "craft"

    mock_facets = {
        "uf": ["wooden-craft-sticks"],
        "bt": [{"title": "Festivals", "canonical_tag": "festivals", "url": "https://id.loc.gov/authorities/subjects/sh1"}],
        "nt": [{"title": "Local craft fairs", "canonical_tag": "local-craft-fairs", "url": "https://id.loc.gov/authorities/subjects/sh2"}],
        "rt": [{"title": "Folk art", "canonical_tag": "folk-art", "url": "https://id.loc.gov/authorities/subjects/sh3"}],
    }

    with (
        patch.object(lat, "query_fast", return_value=(mock_fast_results, None)),
        patch.object(lat, "query_loc", return_value=(mock_loc_results, None)),
        patch.object(lat, "fetch_loc_concept_facets", return_value=mock_facets),
    ):
        payload = lat.query_authority_diagnostic(test_term, max_results=5)

    results = payload["results"]
    assert len(results) == 3

    # Rank 1: Dual authority Craft festivals (merged FAST + LOC)
    assert results[0]["canonical_tag"] == "craft-festivals"
    assert results[0]["source"] == "FAST + LOC"
    assert results[0]["matched_via"] is None
    # 4 Facets must be populated
    assert "festivals" in [b["canonical_tag"] for b in results[0]["bt"]]
    assert "local-craft-fairs" in [n["canonical_tag"] for n in results[0]["nt"]]
    assert "folk-art" in [p["canonical_tag"] for p in results[0]["rt"]]
    assert "craft-fairs" in results[0]["uf"]

    # Rank 2: LOC Craft sticks
    assert results[1]["canonical_tag"] == "craft-sticks"
    assert results[1]["source"] == "LOC"

    # Rank 3: Corporate acronym at the bottom
    assert results[2]["canonical_tag"] == "indiana-university-craft"
    assert results[2]["matched_via"] == "CRAFT"


def test_fetch_loc_concept_facets_parser():
    """Verify parsing of JSON-LD graph into 4 thesaurus facets (UF, BT, NT, RT)."""
    mock_json_ld = [
        {
            "@id": "http://id.loc.gov/authorities/subjects/sh_main",
            "@type": ["http://www.loc.gov/mads/rdf/v1#Topic", "http://www.w3.org/2004/02/skos/core#Concept"],
            "http://www.loc.gov/mads/rdf/v1#authoritativeLabel": [{"@value": "Three-dimensional printing"}],
            "http://www.w3.org/2004/02/skos/core#altLabel": [
                {"@value": "3D printing"},
                {"@value": "3-D printing"},
            ],
            "http://www.w3.org/2004/02/skos/core#broader": [
                {"@id": "http://id.loc.gov/authorities/subjects/sh_broader"},
            ],
            "http://www.w3.org/2004/02/skos/core#narrower": [
                {"@id": "http://id.loc.gov/authorities/subjects/sh_narrower"},
            ],
            "http://www.w3.org/2004/02/skos/core#related": [
                {"@id": "http://id.loc.gov/authorities/subjects/sh_related"},
            ],
        },
        {
            "@id": "http://id.loc.gov/authorities/subjects/sh_broader",
            "http://www.loc.gov/mads/rdf/v1#authoritativeLabel": [{"@value": "Additive manufacturing"}],
        },
        {
            "@id": "http://id.loc.gov/authorities/subjects/sh_narrower",
            "http://www.loc.gov/mads/rdf/v1#authoritativeLabel": [{"@value": "Solid freeform fabrication"}],
        },
        {
            "@id": "http://id.loc.gov/authorities/subjects/sh_related",
            "http://www.loc.gov/mads/rdf/v1#authoritativeLabel": [{"@value": "Prototyping"}],
        },
    ]

    with patch("urllib.request.urlopen") as mock_open:
        mock_resp = mock_open.return_value.__enter__.return_value
        mock_resp.read.return_value = json.dumps(mock_json_ld).encode("utf-8")
        with patch("json.load", return_value=mock_json_ld):
            facets = lat.fetch_loc_concept_facets("https://id.loc.gov/authorities/subjects/sh_main", "three-dimensional-printing")

    assert facets["uf"] == ["3-d-printing", "3d-printing"]
    assert len(facets["bt"]) == 1
    assert facets["bt"][0]["canonical_tag"] == "additive-manufacturing"
    assert len(facets["nt"]) == 1
    assert facets["nt"][0]["canonical_tag"] == "solid-freeform-fabrication"
    assert len(facets["rt"]) == 1
    assert facets["rt"][0]["canonical_tag"] == "prototyping"


def test_register_taxonomy_alias_absorption_and_repointing(monkeypatch, tmp_path):
    """Verify alias registration ensures canonical existence, repoints relations, prunes standalone rows, and invalidates cache."""
    import evelyn_config as cfg
    from Evelyn.tools import tag_librarian, taxonomy_db, vault_db
    from evelyn_server import _register_taxonomy_alias

    monkeypatch.setattr(vault_db, "DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setattr(cfg, "VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setattr(tag_librarian, "delete_tag_from_chroma", lambda *a, **k: None)

    vault_db.init_db()
    taxonomy_db.invalidate_alias_cache()

    # Step 1: Register three-dimensional-printing as alias of 3d-printing
    _moves, deleted = _register_taxonomy_alias("three-dimensional-printing", "3d-printing")
    assert deleted is False  # Wasn't a standalone tag prior
    assert "3d-printing" in [t["tag"] for t in taxonomy_db.get_master_tags()]
    assert taxonomy_db.canonicalize_tags(["three-dimensional-printing"]) == ["3d-printing"]

    # Step 2: Now test absorption of an existing tag with prior relations
    taxonomy_db.upsert_master_tag("sla-printing", category="tech", description="Stereolithography")
    taxonomy_db.record_relation("sla-printing", "manufacturing", kind="narrower")

    moves, deleted = _register_taxonomy_alias("sla-printing", "3d-printing")
    assert deleted is True  # Standalone master_tag row was pruned
    assert moves["moved"] >= 1
    assert "sla-printing" not in [t["tag"] for t in taxonomy_db.get_master_tags()]

    # Relation should have repointed from sla-printing -> 3d-printing
    related_to_3d = taxonomy_db.get_related_terms("3d-printing")
    assert any(r["tag"] == "manufacturing" for r in related_to_3d)

    # Alias cache must resolve immediately
    assert taxonomy_db.canonicalize_tags(["sla-printing", "craft"]) == ["3d-printing", "craft"]

