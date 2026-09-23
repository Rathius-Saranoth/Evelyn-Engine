# test_tag_retirement.py
# date created: 2026-09-22 21:10:00
# date modified: 2026-09-22 19:55:33
# tags: #test, #tags, #taxonomy, #retirement, #vocabulary

"""Cover for retirement-by-review replacing zero-usage pruning (v000.006.202).

Maintenance used to delete every term with zero usage. That conflates the authority file —
the terms judged legitimate — with the index, which is only what happens to be tagged now,
so deleting a note could delete vocabulary and restoring it days later would not bring the
terms back. Maintenance now proposes; a human decides; and an approved retirement records
an equivalence rather than erasing the record.
"""

import time

import pytest

import evelyn_config as cfg
from Evelyn.tools import memory_db, tag_librarian, taxonomy_db, vault_db


@pytest.fixture
def stores(monkeypatch, tmp_path):
    monkeypatch.setattr(vault_db, "DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setattr(cfg, "MEMORY_DB_PATH", str(tmp_path / "memory.db"))
    vault_db.init_db()
    memory_db.init_db()
    taxonomy_db.invalidate_alias_cache()
    monkeypatch.setattr(tag_librarian, "index_master_tag_in_chroma", lambda *a, **k: None)
    monkeypatch.setattr(tag_librarian, "delete_tag_from_chroma", lambda *a, **k: None)
    return tag_librarian


def _register(tag, *, protected=0, age_days=0.0):
    taxonomy_db.upsert_master_tag(tag, category=tag.split("/")[0] if "/" in tag else "general")
    con = vault_db.get_db()
    try:
        con.execute(
            "UPDATE master_tag_taxonomy SET protected = ?, created_at = ? WHERE tag = ?",
            (protected, time.time() - age_days * 86400, tag),
        )
        con.commit()
    finally:
        con.close()


def _rows():
    return taxonomy_db.get_master_tags()


def _pending():
    return memory_db.get_pending_proposals(tag_librarian.TAG_RETIREMENT_PROPOSAL)


class TestMaintenanceNeverDeletes:
    def test_an_unused_term_survives_maintenance(self, stores, monkeypatch):
        """The behaviour the old pruner got wrong: zero usage is not grounds for deletion."""
        _register("zzz-unused", age_days=365)
        monkeypatch.setattr(vault_db, "get_all_documents",
                            lambda: [{"tags": "zzz-in-use"}])
        _register("zzz-in-use")

        result = stores.maintain_master_taxonomy()

        assert result["removed_master_tags"] == 0
        assert "zzz-unused" in {r["tag"] for r in _rows()}


class TestRetirementProposals:
    def test_a_long_unused_term_is_proposed(self, stores):
        _register("zzz-ancient", age_days=365)
        proposed = stores.propose_tag_retirement(_rows(), ["zzz-ancient"])
        assert proposed == ["zzz-ancient"]
        assert _pending()[0]["topic"] == "zzz-ancient"

    def test_a_term_inside_its_grace_period_is_not_proposed(self, stores):
        """A reserved term legitimately has no uses yet."""
        _register("zzz-brand-new", age_days=1)
        assert stores.propose_tag_retirement(_rows(), ["zzz-brand-new"]) == []
        assert _pending() == []

    def test_curated_terms_are_never_proposed(self, stores):
        """Protected terms came from the reviewed vocabulary; absence from the index proves nothing."""
        _register("type/media/dataset", protected=1, age_days=365)
        assert stores.propose_tag_retirement(_rows(), ["type/media/dataset"]) == []

    def test_a_term_is_proposed_only_once(self, stores):
        _register("zzz-ancient", age_days=365)
        stores.propose_tag_retirement(_rows(), ["zzz-ancient"])
        assert stores.propose_tag_retirement(_rows(), ["zzz-ancient"]) == []
        assert len(_pending()) == 1


class TestRetiringATerm:
    def test_retiring_with_a_replacement_records_an_equivalence(self, stores):
        """A pointer is what stops the variant being re-minted later (§6.2)."""
        _register("family-history")
        _register("genealogy")

        assert stores.retire_term("family-history", "genealogy") is True

        assert taxonomy_db.get_aliases().get("family-history") == "genealogy"
        assert "family-history" not in {r["tag"] for r in _rows()}
        # And the equivalence resolves, so documents phrased the old way still land right.
        assert taxonomy_db.canonicalize_tags(["family-history"]) == ["genealogy"]

    def test_retiring_without_a_replacement_just_removes_the_row(self, stores):
        _register("zzz-obsolete")
        assert stores.retire_term("zzz-obsolete") is True
        assert "zzz-obsolete" not in {r["tag"] for r in _rows()}
        assert taxonomy_db.get_aliases().get("zzz-obsolete") is None

    def test_a_blank_term_is_refused(self, stores):
        assert stores.retire_term("  ") is False

    def test_a_term_cannot_alias_to_itself(self, stores):
        _register("zzz-self")
        stores.retire_term("zzz-self", "zzz-self")
        assert taxonomy_db.get_aliases().get("zzz-self") is None
