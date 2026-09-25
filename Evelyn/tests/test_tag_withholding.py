# test_tag_withholding.py
# date created: 2026-09-24
# date modified: 2026-09-24 19:45:25
# tags: #taxonomy, #admission, #memory, #testing

"""An unregistered tag goes to review, not onto the fact — but is never lost (D2).

§6.1 makes an unregistered term a proposal rather than a silent addition. The vault side already
worked that way: `reconcile_subjects` applies only what it can match. The memory writers stored
the term on the fact *and* proposed it, so the corpus and the vocabulary drifted apart — 127
terms accumulated unnoticed and needed a hand curation pass (A3) to reconcile.

Withholding is only safe because approval can now put an admitted term back on the facts that
wanted it (`backfill_admitted_term`, v000.006.216). The guard that matters is the other
direction: a term is withheld only once a pending proposal covers it. If the queue is full or
the proposal could not be written, the tag stays on the fact — an unreviewed tag is untidy, a
vanished one is unrecoverable.
"""

import pytest

import evelyn_config as cfg
from Evelyn.tools import memory_db, tag_librarian, taxonomy_db


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> None:
    for term in ("coffee", "routine"):
        taxonomy_db.upsert_master_tag(term, category="domestic-life")
    taxonomy_db.invalidate_alias_cache()
    monkeypatch.setattr(tag_librarian, "index_master_tag_in_chroma", lambda *a, **k: None)


def _entry() -> int:
    return memory_db.insert_entry(
        category="Cat05-U", subject="Tester", observation="A drinks espresso each morning."
    )


def _pending() -> set[str]:
    return {
        (p.get("topic") or "").strip()
        for p in memory_db.get_pending_proposals(tag_librarian.TAG_ADMISSION_PROPOSAL)
    }


def test_a_registered_tag_is_kept(registry: None) -> None:
    kept = tag_librarian.withhold_unregistered_tags(
        _entry(), ["coffee", "routine"], origin="test", reason="test"
    )

    assert kept == ["coffee", "routine"]
    assert _pending() == set()


def test_an_unregistered_tag_is_withheld_and_proposed(registry: None) -> None:
    """The A3 failure, prevented: the term goes to review instead of onto the fact."""
    kept = tag_librarian.withhold_unregistered_tags(
        _entry(), ["coffee", "espresso"], origin="test", reason="test"
    )

    assert kept == ["coffee"]
    assert _pending() == {"espresso"}


def test_the_proposal_records_the_entry_that_wanted_it(registry: None) -> None:
    """Without the trail, approval has no way to put the term back."""
    entry_id = _entry()

    tag_librarian.withhold_unregistered_tags(entry_id, ["espresso"], origin="test", reason="test")

    proposal = memory_db.get_pending_proposals(tag_librarian.TAG_ADMISSION_PROPOSAL)[0]
    assert proposal["source_ids"] == [entry_id]


def test_a_term_a_second_fact_wants_is_still_withheld(registry: None) -> None:
    """The proposal already exists, so it covers this fact too — and widens to include it."""
    first = _entry()
    tag_librarian.withhold_unregistered_tags(first, ["espresso"], origin="test", reason="test")
    second = _entry()

    kept = tag_librarian.withhold_unregistered_tags(
        second, ["espresso", "coffee"], origin="test", reason="test"
    )

    assert kept == ["coffee"]
    assert len(memory_db.get_pending_proposals(tag_librarian.TAG_ADMISSION_PROPOSAL)) == 1
    proposal = memory_db.get_pending_proposals(tag_librarian.TAG_ADMISSION_PROPOSAL)[0]
    assert set(proposal["source_ids"]) == {first, second}


def test_a_term_the_queue_could_not_accept_is_kept(
    registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard. A full queue must not turn withholding into deletion."""
    monkeypatch.setattr(tag_librarian, "TAG_ADMISSION_MAX_PENDING", 0)

    kept = tag_librarian.withhold_unregistered_tags(
        _entry(), ["coffee", "espresso"], origin="test", reason="test"
    )

    assert kept == ["coffee", "espresso"]
    assert _pending() == set()


def test_withholding_can_be_switched_off(registry: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Off, the term is still proposed — it is just also stored, as before v000.006.227."""
    monkeypatch.setattr(cfg, "TAG_WITHHOLD_UNREGISTERED", False)

    kept = tag_librarian.withhold_unregistered_tags(
        _entry(), ["coffee", "espresso"], origin="test", reason="test"
    )

    assert kept == ["coffee", "espresso"]
    assert _pending() == {"espresso"}


def test_a_container_word_is_neither_stored_nor_proposed(registry: None) -> None:
    """It is not a subject, so there is nothing to review and nothing worth keeping.

    The "never drop" guard protects information. A container word carries none — that is what
    puts it on the list — so dropping it loses nothing, and keeping it is how `work`, `home` and
    `pets` reached 17 memory facts.
    """
    kept = tag_librarian.withhold_unregistered_tags(
        _entry(), ["coffee", "work"], origin="test", reason="test"
    )

    assert kept == ["coffee"]
    assert _pending() == set()


def test_a_container_is_never_proposed_by_any_producer(registry: None) -> None:
    """The filter sits at the shared choke point, so no writer can route around it."""
    assert tag_librarian.propose_tag_admission(
        ["work", "pets", "espresso"], origin="test", reason="test"
    ) == ["espresso"]
