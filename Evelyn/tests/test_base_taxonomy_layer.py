# test_base_taxonomy_layer.py
# date created: 2026-09-26
# date modified: 2026-09-26 08:38:51
# tags: #taxonomy, #authority, #layering, #vocabulary, #testing

"""The shared base vocabulary sits under the local one and cannot be written by the engine.

Three kinds of fact shared one `master_tag_taxonomy` row: what a term means, who says so, and
how often this corpus uses it. The consequences were measurable - a fresh clone got an empty
table, because nothing in the repository seeds a term; and the nightly census rewrites counts
in the same row as the definition (164 of them on 2026-09-26), so definitions could never live
in a tracked file without a background task dirtying it nightly.

These tests hold the invariant that makes the split worth having: **nothing the engine does can
purge the base.**
"""

import json

import pytest

from Evelyn.tools import tag_librarian, taxonomy_base, taxonomy_db


@pytest.fixture
def base_file(tmp_path) -> str:
    doc = {
        "version": "test.1",
        "terms": [
            {"term": "leather", "category": "objects", "description": "A material.",
             "scheme": "fast", "authorized_label": "Leather"},
            {"term": "lethargy", "category": "health-body", "scheme": "mesh",
             "authorized_label": "Lethargy"},
        ],
        "aliases": [{"alias": "hides", "canonical": "leather", "scheme": "fast"}],
    }
    path = tmp_path / "base.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def test_a_fresh_clone_gets_a_working_vocabulary(base_file: str) -> None:
    """The point of tracking the base: an empty local table is no longer an empty vocabulary."""
    assert taxonomy_db.get_master_tags() == []

    taxonomy_base.sync_base_taxonomy(base_file)

    terms = {t["tag"] for t in taxonomy_db.get_master_tags()}
    assert terms == {"leather", "lethargy"}


def test_base_terms_are_admitted_without_a_proposal(base_file: str) -> None:
    """Admission reads one surface set, so the base has to reach it or it buys nothing."""
    taxonomy_base.sync_base_taxonomy(base_file)

    admitted, unregistered = taxonomy_db.partition_by_admission(["leather", "airthread"])

    assert admitted == ["leather"]
    assert unregistered == ["airthread"], "A personal term must still go to review"


def test_the_census_cannot_shadow_a_base_definition(base_file: str) -> None:
    """The census writes a count and knows nothing about categories.

    Merging per row rather than per field would let that write replace `objects` with the
    empty string it passes, so the tracked definition would be shadowed by a background task.
    """
    taxonomy_base.sync_base_taxonomy(base_file)
    taxonomy_db.upsert_master_tag("leather", usage_count=17)

    row = next(t for t in taxonomy_db.get_master_tags() if t["tag"] == "leather")
    assert row["usage_count"] == 17, "The count is local"
    assert row["category"] == "objects", "The definition is still the base's"
    assert row["description"] == "A material."
    assert row["source"] == "both"


def test_a_local_decision_overrides_the_base(base_file: str) -> None:
    """An override is the point of a layer; a reviewer outranks a cataloguer here."""
    taxonomy_base.sync_base_taxonomy(base_file)
    taxonomy_db.upsert_master_tag("leather", category="craft")

    row = next(t for t in taxonomy_db.get_master_tags() if t["tag"] == "leather")
    assert row["category"] == "craft"
    assert row["description"] == "A material.", "...but only the field actually overridden"


def test_a_base_term_cannot_be_deleted(base_file: str) -> None:
    taxonomy_base.sync_base_taxonomy(base_file)

    assert taxonomy_db.delete_master_tag("leather") is False
    assert taxonomy_base.is_base_term("leather")


def test_a_base_term_cannot_be_retired(base_file: str, monkeypatch) -> None:
    """The guard belongs before the first write, not after the last one.

    `retire_term` records the alias and re-points relations *before* it deletes, and it
    ignored the delete's result - so a refused delete would have left an alias pointing at a
    term that is still live, and returned True.
    """
    taxonomy_base.sync_base_taxonomy(base_file)
    monkeypatch.setattr(tag_librarian, "delete_tag_from_chroma", lambda *a, **k: None)

    assert tag_librarian.retire_term("leather", "lethargy") is False
    assert taxonomy_base.is_base_term("leather")
    assert "leather" not in taxonomy_db.get_aliases(), "Nothing may be written on a refusal"


def test_base_terms_are_protected_from_retirement_proposals(base_file: str) -> None:
    """Unused is the normal state of a shared starter term, not evidence against it."""
    taxonomy_base.sync_base_taxonomy(base_file)

    assert all(t["protected"] for t in taxonomy_db.get_master_tags())


def test_aliases_merge_with_the_local_layer_winning(base_file: str) -> None:
    taxonomy_base.sync_base_taxonomy(base_file)
    assert taxonomy_db.get_aliases()["hides"] == "leather"

    taxonomy_db.record_alias("hides", "lethargy")

    assert taxonomy_db.get_aliases()["hides"] == "lethargy"


def test_syncing_twice_is_a_no_op(base_file: str) -> None:
    assert taxonomy_base.sync_base_taxonomy(base_file)["status"] == "loaded"
    assert taxonomy_base.sync_base_taxonomy(base_file)["status"] == "current"


def test_a_new_version_replaces_rather_than_merges(base_file: str, tmp_path) -> None:
    """A term dropped from the file must leave the table, or the file stops being the truth."""
    taxonomy_base.sync_base_taxonomy(base_file)

    newer = tmp_path / "base2.json"
    newer.write_text(json.dumps({
        "version": "test.2",
        "terms": [{"term": "lethargy", "category": "health-body", "scheme": "mesh"}],
        "aliases": [],
    }), encoding="utf-8")
    taxonomy_base.sync_base_taxonomy(str(newer))

    assert not taxonomy_base.is_base_term("leather")
    assert taxonomy_base.is_base_term("lethargy")


def test_a_missing_base_file_leaves_a_working_vocabulary(tmp_path) -> None:
    """The base is an addition. Its absence is a smaller vocabulary, never a broken one."""
    taxonomy_db.upsert_master_tag("airthread", category="personal")

    result = taxonomy_base.sync_base_taxonomy(str(tmp_path / "nothing.json"))

    assert result["status"] == "absent"
    assert [t["tag"] for t in taxonomy_db.get_master_tags()] == ["airthread"]


def test_a_malformed_base_file_does_not_take_the_vocabulary_down(tmp_path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json at all", encoding="utf-8")
    taxonomy_db.upsert_master_tag("airthread", category="personal")

    assert taxonomy_base.sync_base_taxonomy(str(broken))["status"] == "absent"
    assert [t["tag"] for t in taxonomy_db.get_master_tags()] == ["airthread"]


def test_the_boot_sequence_loads_the_base() -> None:
    """A loader nothing calls is a tracked file nobody reads.

    Admission, withholding and the census all ask "does the vocabulary hold this term?" and
    would answer it wrongly for the whole of a boot that skipped this, so the call has to be
    in the startup path and not merely available.
    """
    import ast
    import inspect

    import evelyn_server

    tree = ast.parse(inspect.getsource(evelyn_server.lifespan).lstrip())
    referenced = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "sync_base_taxonomy" in referenced, (
        "Nothing on boot materialises taxonomy/base.json; the shared vocabulary never loads"
    )
