# test_taxonomy_seed_guard.py
# date created: 2026-09-24
# date modified: 2026-09-24 17:52:21
# tags: #taxonomy, #vocabulary, #seeding, #testing

"""Seeding must not be able to flatten a curated vocabulary (A2).

`seed_master_taxonomy_from_vault()` upserted every vault tag with `category = "general"` and a
boilerplate description, and `upsert_master_tag` overwrote `category` unconditionally. One run
of a CLI flag whose name sounds harmless would have replaced 675 curated categories with a
single shelf label. It had never run — verified at the time: no row carried that description —
which is the only reason there was nothing to repair.

Two guards are pinned here: the seeder refuses a populated registry, and an omitted field on
`upsert_master_tag` means "leave it alone" rather than "blank it".
"""

import pytest

from Evelyn.tools import tag_librarian, taxonomy_db, vault_db


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the seeder off the vector store; these tests are about the registry rows."""
    monkeypatch.setattr(tag_librarian, "index_master_tag_in_chroma", lambda *a, **k: None)


def _vault(*tag_sets: str) -> None:
    for i, tags in enumerate(tag_sets):
        vault_db.upsert_document(path=f"Notes/n{i}.md", title=f"n{i}", mtime=1.0, tags=tags)


def _rows() -> dict[str, dict]:
    return {m["tag"]: m for m in taxonomy_db.get_master_tags()}


class TestUpsertPreservesWhatItIsNotTold:
    def test_an_omitted_category_does_not_blank_a_curated_one(self, registry: None) -> None:
        """The defect behind A2: any caller omitting `category` cleared the reviewer's answer."""
        taxonomy_db.upsert_master_tag("cat", category="domestic-life", description="Pets.")

        taxonomy_db.upsert_master_tag("cat", usage_count=7)

        row = _rows()["cat"]
        assert row["category"] == "domestic-life"
        assert row["description"] == "Pets."
        assert row["usage_count"] == 7

    def test_an_omitted_usage_count_does_not_reset_it(self, registry: None) -> None:
        """Zero is a real count a reserved term holds, so it cannot also mean 'unknown'."""
        taxonomy_db.upsert_master_tag("cat", category="domestic-life", usage_count=7)

        taxonomy_db.upsert_master_tag("cat", description="Pets.")

        assert _rows()["cat"]["usage_count"] == 7

    def test_an_explicit_value_still_changes_the_field(self, registry: None) -> None:
        """Preserving on empty must not make a field unwritable."""
        taxonomy_db.upsert_master_tag("cat", category="domestic-life")

        taxonomy_db.upsert_master_tag("cat", category="relationships", usage_count=0)

        row = _rows()["cat"]
        assert row["category"] == "relationships"
        assert row["usage_count"] == 0


class TestSeedingRefusesACuratedRegistry:
    def test_it_seeds_an_empty_registry(self, registry: None) -> None:
        """The one situation seeding is for: the vault is the only record of what was used."""
        _vault("cat, pet", "cat, weather")

        result = tag_librarian.seed_master_taxonomy_from_vault()

        assert result["status"] == "seeded"
        assert result["registered"] == 3
        assert set(_rows()) == {"cat", "pet", "weather"}
        assert _rows()["cat"]["usage_count"] == 2

    def test_a_seeded_term_carries_no_invented_category_or_description(self, registry: None) -> None:
        """Boilerplate is indistinguishable from a curated answer once it is written."""
        _vault("cat")

        tag_librarian.seed_master_taxonomy_from_vault()

        row = _rows()["cat"]
        assert (row["category"] or "") == ""
        assert (row["description"] or "") == ""

    def test_it_refuses_once_the_registry_holds_terms(self, registry: None) -> None:
        """An unregistered term is then an admission question, not a bulk insert."""
        _vault("cat, pet")
        taxonomy_db.upsert_master_tag("cat", category="domestic-life", description="Pets.")

        result = tag_librarian.seed_master_taxonomy_from_vault()

        assert result["status"] == "refused"
        assert result["reason"] == "registry_populated"
        assert result["registered"] == 0
        assert set(_rows()) == {"cat"}, "it must not register the unregistered term either"
        assert _rows()["cat"]["category"] == "domestic-life"

    def test_a_forced_restore_still_does_not_rewrite_an_existing_row(self, registry: None) -> None:
        """The override is for a restore; it is not a licence to overwrite a review."""
        _vault("cat, pet")
        taxonomy_db.upsert_master_tag(
            "cat", category="domestic-life", description="Pets.", usage_count=99
        )

        result = tag_librarian.seed_master_taxonomy_from_vault(allow_populated=True)

        assert result["status"] == "seeded"
        assert result["registered"] == 1, "only the term the registry lacked"
        row = _rows()["cat"]
        assert row["category"] == "domestic-life"
        assert row["description"] == "Pets."
        assert row["usage_count"] == 99, "counts on existing rows are maintenance's job"
        assert "pet" in _rows()
