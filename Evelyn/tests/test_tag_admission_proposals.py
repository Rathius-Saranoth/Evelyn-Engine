# test_tag_admission_proposals.py
# date created: 2026-09-22 20:20:00
# date modified: 2026-09-24 19:52:48
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


def test_administrative_namespaces_are_never_proposed(stores):
    """Administrative axes are exempt from the vocabulary (§3.8), not candidates for it.
    Dates are not among them any more: the time axis is the `occurred` property, so a date
    never reaches the tag pipeline at all."""
    assert stores.propose_tag_admission(["status/active", "obsidian-graph/contact"]) == []
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


def test_a_flat_term_is_left_uncategorised_for_the_reviewer(stores):
    """`general` is not one of the registry's categories, so proposing one would put a
    meaningless value into the vocabulary the moment a reviewer clicked approve."""
    stores.propose_tag_admission(["zzz-flat-term"])

    assert _pending()[0]["suggested_category"] == ""


class TestApproval:
    def test_admitting_registers_the_term(self, stores):
        assert stores.admit_proposed_term("zzz-approved-term") is True
        assert "zzz-approved-term" in {r["tag"] for r in taxonomy_db.get_master_tags()}

    def test_a_flat_term_is_admitted_without_an_invented_category(self, stores):
        stores.admit_proposed_term("zzz-no-category")

        row = next(r for r in taxonomy_db.get_master_tags() if r["tag"] == "zzz-no-category")
        assert (row["category"] or "") == ""

    def test_the_reviewers_category_is_recorded(self, stores):
        stores.admit_proposed_term("zzz-chosen-cat", category="domestic-life")

        row = next(r for r in taxonomy_db.get_master_tags() if r["tag"] == "zzz-chosen-cat")
        assert row["category"] == "domestic-life"

    def test_the_vector_copy_carries_the_same_category_as_the_row(self, stores, monkeypatch):
        """The row and its embedding must agree on what was admitted; they were written
        from different values, so an inferred category reached one and not the other."""
        seen = {}
        monkeypatch.setattr(
            tag_librarian, "index_master_tag_in_chroma",
            lambda tag, category="", description="": seen.update(tag=tag, category=category),
        )
        stores.admit_proposed_term("motif/zzz-vector")

        row = next(r for r in taxonomy_db.get_master_tags() if r["tag"] == "motif/zzz-vector")
        # Empty on both sides since v000.006.228: admission no longer derives a category from the
        # prefix, because `motif/storm` does not need a category saying `motif`. What the test
        # guards is that the row and its embedding agree, whatever the value is.
        assert seen["category"] == (row["category"] or "") == ""

    def test_a_reviewer_supplied_category_reaches_both(self, stores, monkeypatch):
        """Agreement has to hold for a real value too, or the assertion above proves nothing."""
        seen = {}
        monkeypatch.setattr(
            tag_librarian, "index_master_tag_in_chroma",
            lambda tag, category="", description="": seen.update(tag=tag, category=category),
        )
        stores.admit_proposed_term("zzz-flat", category="mind-self")

        row = next(r for r in taxonomy_db.get_master_tags() if r["tag"] == "zzz-flat")
        assert seen["category"] == row["category"] == "mind-self"

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


class TestStubTagsFollowTheStandard:
    """A ghost stub carries its type and nothing else (v000.006.207).

    The generator defaulted to `stub, concept`. Neither is a registered term, and the §4
    class profile forbids a domain tag on the `stub` class outright — the registered form
    is the type facet `type/stub`, which is what the 124 stubs already in the vault carry.
    """

    def test_the_payload_default_is_the_registered_type_facet(self):
        from Evelyn.tools import link_librarian

        assert link_librarian.StubPayload(target_name="Anything").tags == ["type/stub"]

    def test_a_payload_with_no_tags_element_falls_back_to_the_type_facet(self):
        from Evelyn.tools import link_librarian

        parsed = link_librarian.parse_stub_xml(
            '<entity_stub target="Anything"><abstract>x</abstract></entity_stub>'
        )

        assert parsed.tags == ["type/stub"]


class TestStubFilenamesAreCrossPlatform:
    """A wikilink target may hold characters a filename may not (v000.006.208).

    `[[Nier: Automata]]` produced `Nier: Automata.md`, which Windows cannot represent, so
    Syncthing refused to sync it. Sanitising the stem alone would orphan every existing
    link to it, so the original name is carried as an alias.
    """

    def test_illegal_characters_are_stripped_from_the_path(self):
        from Evelyn.tools import link_librarian

        assert link_librarian.stub_relpath("Nier: Automata") == "Stubs/Nier Automata.md"
        assert link_librarian.stub_relpath('A<B>C|D?') == "Stubs/A B C D.md"

    def test_the_original_name_survives_as_title_and_alias(self):
        from Evelyn.tools import link_librarian

        md = link_librarian.render_stub_markdown(
            link_librarian.StubPayload(target_name="Nier: Automata", synthesized_abstract="x")
        )

        assert 'title: "Nier: Automata"' in md
        assert 'aliases: ["Nier: Automata"]' in md

    def test_a_clean_name_gains_no_redundant_alias(self):
        from Evelyn.tools import link_librarian

        md = link_librarian.render_stub_markdown(
            link_librarian.StubPayload(target_name="Valheim", synthesized_abstract="x")
        )

        assert "aliases: []" in md


class TestBackfillOnApproval:
    """Approval must reach the facts that asked for the term (v000.006.216).

    `admit_proposed_term` registered a term and stopped there, and proposals recorded no
    `source_ids` at all — so there was no trail back to the facts. Harmless while the writer
    stores the tag regardless; the moment unregistered tags are withheld instead, an approved
    term would never reach the fact that wanted it.
    """

    def test_the_requesting_entries_are_recorded(self, stores):
        stores.propose_tag_admission(["zzz-wanted"], origin="test", source_ids=[11, 22])

        assert _pending()[0]["source_ids"] == [11, 22]

    def test_a_second_requester_widens_the_existing_proposal(self, stores):
        stores.propose_tag_admission(["zzz-wanted"], source_ids=[11])
        stores.propose_tag_admission(["zzz-wanted"], source_ids=[22])

        pend = _pending()
        assert len(pend) == 1, "still one proposal per term"
        assert pend[0]["source_ids"] == [11, 22]

    def test_backfill_adds_the_term_to_live_entries(self, stores):
        a = memory_db.insert_entry(category="Cat05-U", subject="S", observation="o", tags="coffee")
        b = memory_db.insert_entry(category="Cat05-U", subject="S", observation="o", tags="")
        for eid in (a, b):
            memory_db.update_entry(eid, status="live")

        assert stores.backfill_admitted_term("zzz-approved", [a, b]) == 2
        assert "zzz-approved" in memory_db.get_entry(a)["tags"]
        assert "coffee" in memory_db.get_entry(a)["tags"], "existing tags are preserved"

    def test_backfill_does_not_duplicate_an_existing_tag(self, stores):
        eid = memory_db.insert_entry(category="Cat05-U", subject="S", observation="o", tags="zzz-dup")
        memory_db.update_entry(eid, status="live")

        assert stores.backfill_admitted_term("zzz-dup", [eid]) == 0
        assert memory_db.get_entry(eid)["tags"].count("zzz-dup") == 1

    def test_backfill_skips_entries_that_are_no_longer_live(self, stores):
        eid = memory_db.insert_entry(category="Cat05-U", subject="S", observation="o", tags="")
        memory_db.update_entry(eid, status="merged")

        assert stores.backfill_admitted_term("zzz-gone", [eid]) == 0

    def test_backfill_tolerates_an_empty_trail(self, stores):
        assert stores.backfill_admitted_term("zzz-none", []) == 0
