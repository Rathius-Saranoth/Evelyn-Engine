# test_rejection_is_binding.py
# date created: 2026-09-25
# date modified: 2026-09-25 07:08:56
# tags: #taxonomy, #admission, #proposals, #testing

"""Rejecting a term has to decide something (G1).

`status='rejected'` was written by `reject_proposal()` and read by nothing — every consumer of
the proposals table filtered `status='pending'`. So a rejection removed a row from the queue and
changed nothing else: the next producer that wanted the term raised it again, and the reviewer's
only available action was to reject it a second time. 78 rejections across five proposal types
carried no effect, and `support` completed the round trip inside a day.

Two things make it bind, both the user's call on 2026-09-25:

- **Permanent, not expiring.** `propose_tag_admission` checks rejected topics alongside pending
  ones and does not re-raise.
- **Counted, not merely silenced.** Each further request increments `rejection_count` on the
  rejected row. A term at 1 was a one-off; a term at 30 is a subject the vocabulary is genuinely
  missing, and the count is the argument for reconsidering it. Silence could not say that.

The third case is the one suppression quietly created: withholding only withheld a tag once a
*pending* proposal covered it, so ceasing to re-propose rejected terms would have made
"rejected" the single verdict that lets a term onto a fact.
"""

import pytest

import evelyn_config as cfg
from Evelyn.tools import memory_db, tag_librarian, taxonomy_db

TYPE = tag_librarian.TAG_ADMISSION_PROPOSAL


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> None:
    taxonomy_db.upsert_master_tag("coffee", category="domestic-life")
    taxonomy_db.invalidate_alias_cache()
    monkeypatch.setattr(tag_librarian, "index_master_tag_in_chroma", lambda *a, **k: None)
    monkeypatch.setattr(cfg, "TAG_WITHHOLD_UNREGISTERED", True)


def _entry() -> int:
    return memory_db.insert_entry(
        category="Cat05-U", subject="Tester", observation="A drinks espresso each morning."
    )


def _pending() -> set[str]:
    return {(p.get("topic") or "").strip() for p in memory_db.get_pending_proposals(TYPE)}


def _reject(term: str) -> None:
    """Propose a term and have the reviewer turn it down."""
    tag_librarian.propose_tag_admission([term], origin="test")
    prop = next(p for p in memory_db.get_pending_proposals(TYPE) if p["topic"] == term)
    assert memory_db.reject_proposal(prop["id"])


def test_rejecting_seeds_the_count_at_one(registry: None) -> None:
    _reject("zzz-turned-down")
    assert memory_db.get_rejected_topics(TYPE)["zzz-turned-down"] == 1


def test_a_rejected_term_is_not_proposed_again(registry: None) -> None:
    _reject("zzz-turned-down")

    assert tag_librarian.propose_tag_admission(["zzz-turned-down"], origin="a later writer") == []
    assert "zzz-turned-down" not in _pending(), "rejecting must not be undone by the next writer"


def test_each_further_request_is_counted(registry: None) -> None:
    """The number is what distinguishes a one-off from a real gap in the vocabulary."""
    _reject("zzz-turned-down")

    for _ in range(3):
        tag_librarian.propose_tag_admission(["zzz-turned-down"], origin="a later writer")

    assert memory_db.get_rejected_topics(TYPE)["zzz-turned-down"] == 4


def test_a_rejected_term_is_withheld_from_the_fact(registry: None) -> None:
    """Otherwise 'rejected' becomes the one verdict that lets a term through."""
    _reject("zzz-turned-down")
    entry_id = _entry()

    kept = tag_librarian.withhold_unregistered_tags(
        entry_id=entry_id,
        tags=["coffee", "zzz-turned-down"],
        origin=f"memory fact #{entry_id}",
        reason="test",
    )

    assert kept == ["coffee"]


def test_an_unrelated_term_still_reaches_review(registry: None) -> None:
    """Suppression must be term-scoped, not a queue-wide mute."""
    _reject("zzz-turned-down")

    assert tag_librarian.propose_tag_admission(
        ["zzz-turned-down", "zzz-still-new"], origin="test"
    ) == ["zzz-still-new"]
    assert "zzz-still-new" in _pending()


def test_a_read_failure_suppresses_nothing(registry: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A term that slips through costs one review; one wrongly suppressed is invisible."""
    _reject("zzz-turned-down")

    def _boom(*_a: object, **_k: object) -> dict[str, int]:
        raise OSError("rejected set unavailable")

    monkeypatch.setattr(memory_db, "get_rejected_topics", _boom)
    assert tag_librarian.propose_tag_admission(["zzz-turned-down"], origin="test") == [
        "zzz-turned-down"
    ]
