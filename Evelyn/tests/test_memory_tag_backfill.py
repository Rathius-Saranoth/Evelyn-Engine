# test_memory_tag_backfill.py
# date created: 2026-09-24
# date modified: 2026-09-26 07:45:55
# tags: #memory, #taxonomy, #backfill, #testing

"""The memory half of the 000.006.187 tag reset (B3).

`000.006.187` cleared 39,061 tags across 12,893 memory rows so the vocabulary could be
regenerated from the standard. `000.006.186` did the same to the vault **and reset every
document's audit timestamp**, so the vault re-entered the semantic queue and has been draining
since. Memory got no equivalent re-entry, and 10,033 live facts sat invisible to tag retrieval.

The cursor is its own column: `last_audited_at` already belongs to the grounding auditor and is
stamped by every merge, so sharing it would have the two passes resetting each other.
"""

import pytest

from Evelyn.tools import memory_db, tag_librarian, taxonomy_db


@pytest.fixture
def vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    for term in ("coffee", "routine"):
        taxonomy_db.upsert_master_tag(term, category="domestic-life")
    taxonomy_db.invalidate_alias_cache()
    monkeypatch.setattr(tag_librarian, "index_master_tag_in_chroma", lambda *a, **k: None)


def _fact(observation: str, tags: str | None = None) -> int:
    return memory_db.insert_entry(
        category="Cat05-U", subject="Tester", observation=observation, tags=tags
    )


class TestTheQueue:
    def test_untagged_facts_come_first(self, vocabulary: None) -> None:
        """The backlog is one pass over facts that say nothing, not a rotation."""
        tagged = _fact("A tagged fact.", tags="coffee")
        untagged = _fact("An untagged fact.")

        queue = [e["id"] for e in memory_db.fetch_next_entries_for_tag_audit(batch_size=10)]

        assert queue.index(untagged) < queue.index(tagged)

    def test_an_audited_fact_leaves_the_queue(self, vocabulary: None) -> None:
        entry_id = _fact("An untagged fact.")
        memory_db.mark_entry_tag_audited(entry_id, tags="coffee")

        assert entry_id not in {e["id"] for e in memory_db.fetch_next_entries_for_tag_audit(10)}

    def test_a_fact_that_matched_nothing_still_leaves_the_queue(self, vocabulary: None) -> None:
        """Otherwise it is retried forever, ahead of facts nobody has looked at."""
        entry_id = _fact("An untagged fact.")
        memory_db.mark_entry_tag_audited(entry_id)

        assert entry_id not in {e["id"] for e in memory_db.fetch_next_entries_for_tag_audit(10)}
        assert (memory_db.get_entry(entry_id)["tags"] or "") == ""

    def test_the_count_separates_untagged_from_total(self, vocabulary: None) -> None:
        _fact("A tagged fact.", tags="coffee")
        _fact("An untagged fact.")

        untagged, total = memory_db.count_entries_awaiting_tag_audit()

        assert (untagged, total) == (1, 2)


class TestTheAudit:
    def test_only_registered_terms_are_written(
        self, vocabulary: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unmatched subject becomes a proposal, not a tag — same contract as a new fact."""
        entry_id = _fact("A drinks espresso as part of a morning routine.")
        monkeypatch.setattr(
            tag_librarian, "classify_document_subjects",
            lambda **kw: (["coffee", "routine"], ["espresso"]),
        )

        result = tag_librarian.audit_single_fact_tags(memory_db.get_entry(entry_id))

        assert result["applied"] == ["coffee", "routine"]
        assert result["proposed"] == ["espresso"]
        assert memory_db.get_entry(entry_id)["tags"] == "coffee, routine"

    def test_the_proposal_names_the_fact_that_wanted_it(
        self, vocabulary: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entry_id = _fact("A drinks espresso.")
        monkeypatch.setattr(
            tag_librarian, "classify_document_subjects", lambda **kw: ([], ["espresso"])
        )

        tag_librarian.audit_single_fact_tags(memory_db.get_entry(entry_id))

        proposal = memory_db.get_pending_proposals(tag_librarian.TAG_ADMISSION_PROPOSAL)[0]
        assert proposal["source_ids"] == [entry_id]

    def test_held_back_terms_are_reported_even_when_not_proposed(
        self, vocabulary: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Past the pending cap nothing is *queued*, and the fact is stamped regardless.

        The row itself is written, deferred, so the term stays recorded and withheld — but
        the reviewer has not seen it, so it must still read as held back. Without this the
        terms a full queue set aside would look decided with the fact marked done, which is
        the silent loss the whole admission path exists to prevent.
        """
        entry_id = _fact("A drinks espresso.")
        monkeypatch.setattr(tag_librarian, "TAG_ADMISSION_MAX_PENDING", 0)
        monkeypatch.setattr(
            tag_librarian, "classify_document_subjects", lambda **kw: ([], ["espresso"])
        )

        result = tag_librarian.audit_single_fact_tags(memory_db.get_entry(entry_id))

        assert result["proposed"] == []
        assert result["held_back"] == ["espresso"]

    def test_existing_tags_are_kept(self, vocabulary: None, monkeypatch: pytest.MonkeyPatch) -> None:
        """The pass can only add: a fact says what it is about, never what it is not about."""
        entry_id = _fact("A drinks coffee in a routine.", tags="coffee")
        monkeypatch.setattr(
            tag_librarian, "classify_document_subjects", lambda **kw: (["routine"], [])
        )

        result = tag_librarian.audit_single_fact_tags(memory_db.get_entry(entry_id))

        assert result["applied"] == ["coffee", "routine"]

    def test_a_dry_run_writes_nothing(self, vocabulary: None, monkeypatch: pytest.MonkeyPatch) -> None:
        entry_id = _fact("A drinks espresso.")
        monkeypatch.setattr(
            tag_librarian, "classify_document_subjects", lambda **kw: (["coffee"], ["espresso"])
        )

        tag_librarian.audit_single_fact_tags(memory_db.get_entry(entry_id), dry_run=True)

        assert (memory_db.get_entry(entry_id)["tags"] or "") == ""
        assert memory_db.get_pending_proposals(tag_librarian.TAG_ADMISSION_PROPOSAL) == []
        assert entry_id in {e["id"] for e in memory_db.fetch_next_entries_for_tag_audit(10)}
