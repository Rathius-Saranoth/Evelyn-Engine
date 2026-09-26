# test_tag_withholding.py
# date created: 2026-09-24
# date modified: 2026-09-26 07:45:55
# tags: #taxonomy, #admission, #memory, #testing

"""An unregistered tag goes to review, not onto the fact — but is never lost (D2).

§6.1 makes an unregistered term a proposal rather than a silent addition. The vault side already
worked that way: `reconcile_subjects` applies only what it can match. The memory writers stored
the term on the fact *and* proposed it, so the corpus and the vocabulary drifted apart — 127
terms accumulated unnoticed and needed a hand curation pass (A3) to reconcile.

Withholding is only safe because approval can now put an admitted term back on the facts that
wanted it (`backfill_admitted_term`, v000.006.216). The guard that matters is the other
direction: a term is withheld only once *some* proposal row covers it, so its disappearance
from the fact is recoverable. An unreviewed tag is untidy; a vanished one is unrecoverable.

A full queue used to mean no row, so the tag stayed on the fact and the gate switched itself
off — at 158 of 200 pending on 2026-09-26, roughly three hours from doing exactly that. The
cap now defers the row instead of skipping it (v000.006.245): the queue stays reviewable, the
term stays recorded, and only a write that fails outright puts the tag back on the fact.
"""

import sqlite3

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


def _deferred() -> set[str]:
    return {
        (p.get("topic") or "").strip()
        for p in memory_db.get_proposals_by_status(
            tag_librarian.TAG_ADMISSION_PROPOSAL, memory_db.DEFERRED_STATUS
        )
    }


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


def test_a_full_queue_defers_the_row_and_keeps_withholding(
    registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap limits what the reviewer is handed, not what gets recorded.

    While a full queue wrote nothing, it also stopped withholding — the tag went onto the
    fact unreviewed, which is the drift the gate exists to prevent, arriving precisely when
    the vocabulary is under most pressure.
    """
    monkeypatch.setattr(tag_librarian, "TAG_ADMISSION_MAX_PENDING", 0)

    kept = tag_librarian.withhold_unregistered_tags(
        _entry(), ["coffee", "espresso"], origin="test", reason="test"
    )

    assert kept == ["coffee"]
    assert _pending() == set(), "A deferred term must not reach the reviewer's queue"
    assert _deferred() == {"espresso"}, "...but it must still be recorded"


def test_a_deferred_term_returns_to_the_queue_when_there_is_room(
    registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backlog nothing drains is a backlog that decided to lose the term slowly."""
    monkeypatch.setattr(tag_librarian, "TAG_ADMISSION_MAX_PENDING", 0)
    tag_librarian.withhold_unregistered_tags(
        _entry(), ["espresso"], origin="test", reason="test"
    )
    assert _deferred() == {"espresso"}

    monkeypatch.setattr(tag_librarian, "TAG_ADMISSION_MAX_PENDING", 10)
    assert tag_librarian.release_deferred_admissions() == 1

    assert "espresso" in _pending()
    assert _deferred() == set()


def test_the_release_takes_only_what_the_queue_has_room_for(
    registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Releasing the whole backlog into a full queue would just undo the cap."""
    monkeypatch.setattr(tag_librarian, "TAG_ADMISSION_MAX_PENDING", 0)
    tag_librarian.propose_tag_admission(["zzz-one", "zzz-two", "zzz-three"], origin="test")
    assert len(_deferred()) == 3

    monkeypatch.setattr(tag_librarian, "TAG_ADMISSION_MAX_PENDING", 2)

    assert tag_librarian.release_deferred_admissions() == 2
    assert len(_pending()) == 2
    assert len(_deferred()) == 1
    assert tag_librarian.release_deferred_admissions() == 0, "The queue is full again"


def test_the_release_does_not_depend_on_a_new_term_arriving(
    registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backlog's release must follow the queue having room, and nothing else.

    Hanging it off `propose_tag_admission` looked equivalent and is not: that function
    returns early whenever a pass finds nothing unregistered, which is the normal case, so
    the backlog would drain only when some unrelated new term happened to show up.
    """
    monkeypatch.setattr(tag_librarian, "TAG_ADMISSION_MAX_PENDING", 0)
    tag_librarian.withhold_unregistered_tags(
        _entry(), ["espresso"], origin="test", reason="test"
    )

    monkeypatch.setattr(tag_librarian, "TAG_ADMISSION_MAX_PENDING", 10)
    # A pass where every term is already registered: the early-return path.
    assert tag_librarian.propose_tag_admission(["coffee"], origin="a quiet pass") == []
    assert _deferred() == {"espresso"}, "Guard only — the quiet pass must not be the releaser"

    assert tag_librarian.release_deferred_admissions() == 1
    assert _pending() == {"espresso"}


def test_a_term_that_could_not_be_recorded_at_all_is_kept(
    registry: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The surviving guard. No row means nothing proves the term was ever wanted."""
    def _explode(**kwargs: object) -> int:
        raise sqlite3.OperationalError("disk is full")

    monkeypatch.setattr(memory_db, "insert_proposal", _explode)

    kept = tag_librarian.withhold_unregistered_tags(
        _entry(), ["coffee", "espresso"], origin="test", reason="test"
    )

    assert kept == ["coffee", "espresso"]
    assert _pending() == set()
    assert _deferred() == set()


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


def test_the_scheduled_pass_releases_the_backlog() -> None:
    """The release must have a caller on the schedule, or the backlog is a one-way door.

    Six defects this week were the same shape: code written, correct, and reached by nobody
    (G1, G2, G4, G5, F7a, G6). Deferral without a scheduled release would have been the
    seventh — every term past the cap recorded, withheld, and never shown to anyone.
    """
    import ast
    import inspect

    import evelyn_server

    tree = ast.parse(inspect.getsource(evelyn_server.run_tag_librarian_task).lstrip())
    # Attributes, not calls: the scheduler hands the function to `asyncio.to_thread`, so it
    # is referenced rather than invoked at this site.
    referenced = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "release_deferred_admissions" in referenced, (
        "Nothing on the schedule releases deferred tag admissions; the backlog cannot drain"
    )
