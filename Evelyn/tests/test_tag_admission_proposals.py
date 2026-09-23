# test_tag_admission_proposals.py
# date created: 2026-09-22 20:20:00
# date modified: 2026-09-22 19:31:21
# tags: #test, #tags, #taxonomy, #proposals, #admission, #review

"""Cover for the tag-admission quarantine route (v000.006.201).

An unregistered term used to enter the vocabulary silently, because the only check on the
write path — `canonicalize_tags()` — maps aliases and passes unknown terms through. Terms
the registry does not hold now raise a proposal into the same review queue that already
carries merges and stubs, and approving one registers it.
"""

import pytest

import evelyn_config as cfg
from Evelyn.tools import memory_db, tag_librarian, taxonomy_db, vault_db


@pytest.fixture
def stores(monkeypatch, tmp_path):
    """Hermetic registry + proposal store; Chroma indexing is stubbed out."""
    monkeypatch.setattr(vault_db, "DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setattr(cfg, "MEMORY_DB_PATH", str(tmp_path / "memory.db"))
    vault_db.init_db()
    memory_db.init_db()
    taxonomy_db.invalidate_alias_cache()
    monkeypatch.setattr(tag_librarian, "index_master_tag_in_chroma", lambda *a, **k: None)
    taxonomy_db.upsert_master_tag("genealogy", category="genealogy", description="")
    return tag_librarian


def _pending():
    return memory_db.get_pending_proposals(tag_librarian.TAG_ADMISSION_PROPOSAL)


def test_unregistered_terms_are_proposed(stores):
    proposed = stores.propose_tag_admission(["zzz-new-term"], origin="unit test")

    assert proposed == ["zzz-new-term"]
    rows = _pending()
    assert len(rows) == 1
    assert rows[0]["topic"] == "zzz-new-term"
    assert rows[0]["status"] == "pending"


def test_registered_terms_are_not_proposed(stores):
    assert stores.propose_tag_admission(["genealogy"]) == []
    assert _pending() == []


def test_a_term_is_proposed_only_once(stores):
    """Fifty facts wanting the same term must not yield fifty review items."""
    stores.propose_tag_admission(["zzz-new-term"], origin="first")
    again = stores.propose_tag_admission(["zzz-new-term"], origin="second")

    assert again == []
    assert len(_pending()) == 1


def test_duplicates_within_one_call_collapse(stores):
    proposed = stores.propose_tag_admission(["zzz-dup", "zzz-dup", "zzz-dup"])
    assert proposed == ["zzz-dup"]
    assert len(_pending()) == 1


def test_date_anchors_are_never_proposed(stores):
    """CY- anchors are exempt from the vocabulary (§3.8), not candidates for it."""
    assert stores.propose_tag_admission(["CY-2026/09/22"]) == []
    assert _pending() == []


def test_terms_are_normalised_before_the_admission_check(stores):
    """'Genealogy' is the registered term, not a new one."""
    assert stores.propose_tag_admission(["Genealogy"]) == []


def test_the_queue_is_capped(stores, monkeypatch):
    """A misbehaving writer must not be able to bury the review UI."""
    monkeypatch.setattr(tag_librarian, "TAG_ADMISSION_MAX_PENDING", 3)
    proposed = stores.propose_tag_admission([f"zzz-term-{i}" for i in range(10)])

    assert len(proposed) == 3
    assert len(_pending()) == 3


def test_facet_is_recorded_for_the_reviewer(stores):
    stores.propose_tag_admission(["motif/zzz-unknown"])
    assert _pending()[0]["suggested_category"] == "motif"


class TestApproval:
    def test_admitting_registers_the_term(self, stores):
        assert stores.admit_proposed_term("zzz-approved-term") is True
        assert "zzz-approved-term" in {r["tag"] for r in taxonomy_db.get_master_tags()}

    def test_admitted_terms_are_unprotected(self, stores):
        """A term that entered by inference stays prunable; only curated terms are protected."""
        stores.admit_proposed_term("zzz-approved-term")
        row = next(r for r in taxonomy_db.get_master_tags() if r["tag"] == "zzz-approved-term")
        assert not row.get("protected")

    def test_admission_closes_the_proposal_loop(self, stores):
        """Once admitted, the term no longer reads as unregistered."""
        stores.propose_tag_admission(["zzz-round-trip"])
        stores.admit_proposed_term("zzz-round-trip")
        admitted, unregistered = taxonomy_db.partition_by_admission(["zzz-round-trip"])
        assert admitted == ["zzz-round-trip"] and unregistered == []

    def test_a_blank_term_is_refused(self, stores):
        assert stores.admit_proposed_term("   ") is False
