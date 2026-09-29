# test_vault_tag_admission.py
# date created: 2026-09-24
# date modified: 2026-09-24 19:10:10
# tags: #taxonomy, #librarian, #admission, #testing

"""The vault audit must send its unregistered terms to review, and never a container word.

Two defects, found together while reviewing the first librarian cycle.

**C5** — `audit_document_tags()` reconciles a document's subjects against the registry and holds
back the ones it cannot match, as `details["proposals"]`. That list went to a `logger.info` the
audit subprocess does not surface, and into a returned dict the backlog drainer discards;
`propose_tag_admission` was never called from `tag_librarian` at all. The pass audited 766
documents and proposed nothing, and the empty admission queue read as a clean pipeline when it
was the largest producer writing nowhere.

**The container words** — every tag-producing prompt tells the model not to name the folder
heading, and 17 of the 127 unregistered terms found on memory facts were exactly those words.
A prompt is advice. The filter now sits where terms are held back for review, which is the
point they could otherwise enter the queue.
"""

import pytest

import evelyn_config as cfg
from Evelyn.tools import memory_db, tag_librarian, taxonomy_db


@pytest.fixture
def vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    taxonomy_db.upsert_master_tag("floodplain", category="nature-science")
    monkeypatch.setattr(tag_librarian, "index_master_tag_in_chroma", lambda *a, **k: None)
    monkeypatch.setattr(cfg, "TAG_ADMISSION_MIN_SOURCES", 1)
    monkeypatch.setattr(cfg, "TAG_ADMISSION_AUTO_ADMIT", False)


def _pending() -> set[str]:
    return {
        (p.get("topic") or "").strip()
        for p in memory_db.get_pending_proposals(tag_librarian.TAG_ADMISSION_PROPOSAL)
    }


class TestContainerWords:
    @pytest.mark.parametrize("term", ["work", "home", "pets", "tools", "technology", "Environment"])
    def test_a_container_is_not_a_subject(self, term: str) -> None:
        assert tag_librarian.is_umbrella_term(term) is True

    @pytest.mark.parametrize(
        "term", ["firewall", "cat", "thermostat", "information-retrieval", "home-decor"]
    )
    def test_a_real_subject_passes(self, term: str) -> None:
        """Exact matches only — a compound term that merely starts with a container is fine."""
        assert tag_librarian.is_umbrella_term(term) is False

    def test_the_list_is_configured_not_hardcoded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cfg, "TAXONOMY_CONTAINER_TERMS", {"widget"})
        assert tag_librarian.is_umbrella_term("widget") is True
        assert tag_librarian.is_umbrella_term("work") is False

    def test_a_container_is_never_proposed(self, vocabulary: None) -> None:
        """It reaches the proposal branch like any unmatched phrase, and is dropped there."""
        _applied, proposals = tag_librarian.reconcile_subjects(
            ["work", "wellness", "levee inspection"], existing=[]
        )

        assert "levee-inspection" in proposals
        assert "work" not in proposals
        assert "wellness" not in proposals


class TestTheAuditProposesWhatItCannotMatch:
    def test_unmatched_subjects_reach_the_review_queue(
        self, vocabulary: None, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """The C5 defect: these were computed, logged to nowhere, and discarded."""
        note = tmp_path / "note.md"
        note.write_text("---\ntags: [floodplain]\n---\n\nBody.\n", encoding="utf-8")
        monkeypatch.setattr(
            tag_librarian, "audit_document_tags",
            lambda **kw: (False, "", {"final_tags": ["floodplain"], "previous_tags": ["floodplain"],
                                      "proposals": ["levee-inspection", "culvert-sizing"]}),
        )

        tag_librarian.audit_single_document_semantic(
            doc_path="note.md", vault_root=str(tmp_path)
        )

        assert _pending() == {"levee-inspection", "culvert-sizing"}

    def test_a_dry_run_proposes_nothing(
        self, vocabulary: None, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """A rehearsal that fills the reviewer's queue is not a rehearsal."""
        note = tmp_path / "note.md"
        note.write_text("---\ntags: [floodplain]\n---\n\nBody.\n", encoding="utf-8")
        monkeypatch.setattr(
            tag_librarian, "audit_document_tags",
            lambda **kw: (False, "", {"final_tags": ["floodplain"], "previous_tags": ["floodplain"],
                                      "proposals": ["levee-inspection"]}),
        )

        tag_librarian.audit_single_document_semantic(
            doc_path="note.md", vault_root=str(tmp_path), dry_run=True
        )

        assert _pending() == set()

    def test_the_proposal_records_which_note_wanted_it(
        self, vocabulary: None, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """A bare term is not reviewable; the reviewer needs the document to judge it in context."""
        note = tmp_path / "Reference/note.md"
        note.parent.mkdir(parents=True)
        note.write_text("---\ntags: [floodplain]\n---\n\nBody.\n", encoding="utf-8")
        monkeypatch.setattr(
            tag_librarian, "audit_document_tags",
            lambda **kw: (False, "", {"final_tags": ["floodplain"], "previous_tags": ["floodplain"],
                                      "proposals": ["levee-inspection"]}),
        )

        tag_librarian.audit_single_document_semantic(
            doc_path="Reference/note.md", vault_root=str(tmp_path)
        )

        proposals = memory_db.get_pending_proposals(tag_librarian.TAG_ADMISSION_PROPOSAL)
        # `origin` is stored in merged_observation, which is the field the review card shows.
        assert proposals[0]["merged_observation"] == "vault note (Reference/note.md)"

    def test_unregistered_term_deferred_when_under_quorum(
        self, vocabulary: None, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """With quorum requirement >= 2, single-source candidate is deferred."""
        monkeypatch.setattr(cfg, "TAG_ADMISSION_MIN_SOURCES", 2)
        monkeypatch.setattr(cfg, "TAG_ADMISSION_AUTO_ADMIT", False)

        note = tmp_path / "Reference/note.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("---\ntags: [floodplain]\n---\n\nBody.\n", encoding="utf-8")
        monkeypatch.setattr(
            tag_librarian, "audit_document_tags",
            lambda **kw: (False, "", {"final_tags": ["floodplain"], "previous_tags": ["floodplain"],
                                      "proposals": ["rare-solitary-term"]}),
        )

        tag_librarian.audit_single_document_semantic(
            doc_path="Reference/note.md", vault_root=str(tmp_path)
        )

        assert _pending() == set()
        deferred = {
            (p.get("topic") or "").strip()
            for p in memory_db.get_proposals_by_status(
                tag_librarian.TAG_ADMISSION_PROPOSAL, memory_db.DEFERRED_STATUS
            )
        }
        assert "rare-solitary-term" in deferred

    def test_high_confidence_term_auto_admitted_when_quorum_met(
        self, vocabulary: None, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """A well-formed term with >= 2 sources is auto-admitted without manual review."""
        monkeypatch.setattr(cfg, "TAG_ADMISSION_MIN_SOURCES", 2)
        monkeypatch.setattr(cfg, "TAG_ADMISSION_AUTO_ADMIT", True)

        note_a = tmp_path / "Reference/note_a.md"
        note_b = tmp_path / "Reference/note_b.md"
        note_a.parent.mkdir(parents=True, exist_ok=True)
        note_a.write_text("---\ntags: []\n---\n\nBody.\n", encoding="utf-8")
        note_b.write_text("---\ntags: []\n---\n\nBody.\n", encoding="utf-8")

        # Propose from note_a (deferred)
        tag_librarian.propose_tag_admission(["river-hydrology"], origin="note_a", source_path="Reference/note_a.md")
        assert _pending() == set()
        admitted, _ = taxonomy_db.partition_by_admission(["river-hydrology"])
        assert "river-hydrology" not in admitted

        # Second source arrives from note_b: quorum met, high confidence -> auto-admit
        tag_librarian.propose_tag_admission(["river-hydrology"], origin="note_b", source_path="Reference/note_b.md")
        assert _pending() == set()
        admitted, _ = taxonomy_db.partition_by_admission(["river-hydrology"])
        assert "river-hydrology" in admitted

    def test_stub_note_skipped_from_audit(
        self, vocabulary: None, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Notes in Stubs/ or with type: stub are ignored during semantic tag audit."""
        stub = tmp_path / "Stubs/ghost.md"
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text("---\ntype: stub\ntags: []\n---\n\nStub note.\n", encoding="utf-8")

        audited = False
        def fake_audit(**kw):
            nonlocal audited
            audited = True
            return (False, "", {"final_tags": [], "previous_tags": [], "proposals": []})

        monkeypatch.setattr(tag_librarian, "audit_document_tags", fake_audit)

        res = tag_librarian.audit_single_document_semantic(
            doc_path="Stubs/ghost.md", vault_root=str(tmp_path)
        )
        assert not audited
        assert res.get("status") == "skipped"

