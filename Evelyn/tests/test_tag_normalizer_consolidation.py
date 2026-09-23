# test_tag_normalizer_consolidation.py
# date created: 2026-09-22 19:40:00
# date modified: 2026-09-22 19:09:21
# tags: #test, #tags, #taxonomy, #normalization, #regression

"""Regression cover for tag-normaliser consolidation (v000.006.199).

Six writers each decided independently what a tag should look like, so the same input
entered the vault or the memory store in several incompatible forms. Every one now routes
through `tag_librarian.normalize_tag_format`. These tests pin the properties that were
actually being violated rather than re-testing the normaliser itself.
"""

import pathlib

import pytest

from Evelyn.tools import taxonomy_db, vault_db
from Evelyn.tools.tag_librarian import normalize_tag_format


@pytest.fixture
def registry(monkeypatch, tmp_path):
    """A hermetic controlled vocabulary holding exactly one known term."""
    monkeypatch.setattr(vault_db, "DB_PATH", str(tmp_path / "test_vault.db"))
    vault_db.init_db()
    taxonomy_db.invalidate_alias_cache()
    taxonomy_db.upsert_master_tag("genealogy", category="genealogy", description="")
    return taxonomy_db


class TestCanonicalFormProperties:
    """Properties the divergent normalisers broke."""

    def test_hierarchy_survives(self):
        """research_engine's slug stripped '/', collapsing an axis into one word."""
        assert normalize_tag_format("tech/python/fastapi") == "tech/python/fastapi"

    def test_underscores_become_hyphens(self):
        """'Test_Operator' must not persist as 'test_operator'."""
        assert normalize_tag_format("Test_Operator") == "test-operator"

    def test_multiword_is_one_term_not_a_hierarchy(self):
        """pdf_staging_worker turned a space into '/', inventing an axis."""
        assert normalize_tag_format("Machine Learning") == "machine-learning"
        assert "/" not in normalize_tag_format("Owner's Manuals")

    def test_apostrophes_do_not_reach_yaml(self):
        """'Owner's Manuals' leaked a quote into a YAML flow array."""
        assert "'" not in normalize_tag_format("Owner's Manuals")

    def test_date_anchors_are_preserved(self):
        """The old slug destroyed CY- anchors ('cy-20260922'); §3.8 exempts them."""
        assert normalize_tag_format("CY-2026/09/22") == "CY-2026/09/22"

    def test_normalisation_is_idempotent(self):
        """Re-normalising stored tags must be a no-op, or every write would churn."""
        for tag in ["tech/software/git", "machine-learning", "CY-2026/09/22", "type/media/text"]:
            assert normalize_tag_format(normalize_tag_format(tag)) == normalize_tag_format(tag)


class TestWritersUseTheCanonicalNormaliser:
    """Each consolidated writer must import the canonical function, not re-implement it."""

    @pytest.mark.parametrize("module_path", [
        "Evelyn/tools/research_engine.py",
        "Evelyn/tools/context_manager.py",
        "Evelyn/tools/dream_manager.py",
        "Evelyn/tools/journal_manager.py",
        "Evelyn/tools/pdf_staging_worker.py",
        "Evelyn/tools/vault_list_manager.py",
    ])
    def test_writer_imports_canonical_normaliser(self, module_path):
        source = pathlib.Path(module_path).read_text(encoding="utf-8")
        assert "normalize_tag_format" in source, f"{module_path} does not use the canonical normaliser"

    def test_no_writer_reimplements_the_slug(self):
        """The specific fork that flattened hierarchy must not come back."""
        source = pathlib.Path("Evelyn/tools/research_engine.py").read_text(encoding="utf-8")
        assert 're.sub(r"[^\\w\\s-]", "", tag.lower())' not in source

    def test_pdf_worker_no_longer_fabricates_hierarchy(self):
        source = pathlib.Path("Evelyn/tools/pdf_staging_worker.py").read_text(encoding="utf-8")
        assert "domain_name.lower().replace(' ', '/')" not in source


class TestAdmissionIsDistinctFromAliasMapping:
    """canonicalize_tags() is not a gate; partition_by_admission() is the gate."""

    def test_alias_mapping_passes_unknown_terms_through(self):
        """This is the behaviour that made writers believe they were validating."""
        assert taxonomy_db.canonicalize_tags(["definitely-not-a-registered-term"]) == [
            "definitely-not-a-registered-term"
        ]

    def test_admission_separates_registered_from_unknown(self, registry):
        admitted, unregistered = registry.partition_by_admission(
            ["genealogy", "definitely-not-a-registered-term"]
        )
        assert admitted == ["genealogy"]
        assert unregistered == ["definitely-not-a-registered-term"]

    def test_admission_preserves_order_and_drops_blanks(self, registry):
        admitted, unregistered = registry.partition_by_admission(["", "zzz-unknown-a", "zzz-unknown-b"])
        assert admitted == []
        assert unregistered == ["zzz-unknown-a", "zzz-unknown-b"]

    def test_aliases_count_as_admitted(self, registry):
        """A recorded equivalence is a valid surface form, not an unknown term."""
        admitted, unregistered = registry.partition_by_admission(["genealogy"])
        assert admitted == ["genealogy"] and unregistered == []
