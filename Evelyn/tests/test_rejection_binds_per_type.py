# test_rejection_binds_per_type.py
# date created: 2026-09-25
# date modified: 2026-09-25 18:03:45
# tags: #proposals, #review, #g1, #testing

"""What a rejection means differs by type, and each producer has to honour its own answer (G1).

`.231` made `tag_admission` rejections bind. The other four types still deduplicated against
*pending* proposals alone, so a rejection removed a row from the queue and decided nothing —
`procedure_merge` has already made the full round trip in production.

The four answers are not the same, which is the point:

* **`profile_update` already bound** and needed nothing. Denying stamps
  `entry_document_evolution`, and the evolver re-opens an entry only when `updated_at` moves past
  that stamp. It is the best-shaped of the four and the model the others copy.
* **`procedure_merge` — permanent, for that exact set.** "These are genuinely distinct" is a
  durable statement about those procedures.
* **`split` — until the fact is edited.** "Atomic enough" stays true while the text does.
* **`ghost_link_stub` — until the evidence grows.** A target cited twice may deserve a note at
  twenty; a permanent block would silence the growth that should change the answer.

The scoping is where this goes wrong quietly. A `procedure_merge` rejection must not bar its
members from *every* future merge, and a `ghost_link_stub` rejection must not outlive the
citation count it was a judgement about.
"""

import time

import pytest

import evelyn_config as cfg
from Evelyn.tools import fact_splitter, memory_db, procedure_consolidator


def _reject(type_: str, **kw) -> int:
    pid = memory_db.insert_proposal(type=type_, **kw)
    assert memory_db.reject_proposal(pid)
    return pid


# --------------------------------------------------------------------------- split


def _fact(text: str = "A long compound observation about several unrelated subjects.") -> dict:
    eid = memory_db.insert_entry(category="Cat05-U", subject="Tester", observation=text)
    return memory_db.get_entry(eid)


def test_split_rejection_holds_while_the_fact_is_unchanged() -> None:
    entry = _fact()
    _reject("split", source_ids=[int(entry["id"])], topic=f"Split Compound Fact #{entry['id']}")

    assert fact_splitter._split_was_rejected_and_unchanged(memory_db.get_entry(entry["id"]))


def test_split_reopens_once_the_fact_is_edited() -> None:
    """What the reviewer judged no longer exists, so the question is a fair one again.

    Goes through `update_entry` rather than setting `updated_at` by hand: the whole policy rests
    on that write actually stamping the column, and a test that fakes the stamp would pass even
    if it stopped.
    """
    entry = _fact()
    _reject("split", source_ids=[int(entry["id"])], topic=f"Split Compound Fact #{entry['id']}")
    assert fact_splitter._split_was_rejected_and_unchanged(memory_db.get_entry(entry["id"]))

    time.sleep(0.02)
    memory_db.update_entry(int(entry["id"]), observation="Rewritten into something else entirely.")

    edited = memory_db.get_entry(entry["id"])
    assert edited["updated_at"], "update_entry must stamp updated_at or the policy cannot work"
    assert not fact_splitter._split_was_rejected_and_unchanged(edited)


def test_split_rejection_does_not_leak_to_other_facts() -> None:
    a, b = _fact(), _fact("A different compound observation entirely.")
    _reject("split", source_ids=[int(a["id"])], topic=f"Split Compound Fact #{a['id']}")

    assert not fact_splitter._split_was_rejected_and_unchanged(memory_db.get_entry(b["id"]))


# ----------------------------------------------------------------- procedure_merge


def _proc(n: int) -> dict:
    return {"id": n, "trigger_pattern": f"trigger {n}", "suggested_tools": None}


def test_rejected_cluster_is_not_proposed_again(monkeypatch: pytest.MonkeyPatch) -> None:
    _reject("procedure_merge", source_ids=[901, 902], topic="Merge 901+902")

    monkeypatch.setattr(procedure_consolidator, "calculate_procedure_similarity",
                        lambda *a, **k: 0.9)
    monkeypatch.setattr(memory_db, "get_all_procedures", lambda **k: [_proc(901), _proc(902)])

    assert procedure_consolidator.find_procedure_clusters() == []


def test_a_rejected_merge_does_not_bar_its_members_from_other_merges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scoping trap: suppress the set, never the procedures in it."""
    _reject("procedure_merge", source_ids=[901, 902], topic="Merge 901+902")

    monkeypatch.setattr(procedure_consolidator, "calculate_procedure_similarity",
                        lambda *a, **k: 0.9)
    monkeypatch.setattr(memory_db, "get_all_procedures", lambda **k: [_proc(901), _proc(903)])

    clusters = procedure_consolidator.find_procedure_clusters()
    assert len(clusters) == 1, "901 must still be mergeable with a procedure it never met"
    assert {p["id"] for p in clusters[0]} == {901, 903}


# ---------------------------------------------------------------- ghost_link_stub


def test_ghost_stub_threshold_is_configured_not_hardcoded() -> None:
    assert getattr(cfg, "GHOST_STUB_REEVIDENCE_FACTOR", None), "the threshold must be tunable"


def test_ghost_stub_evidence_is_stored_as_a_number() -> None:
    """It used to exist only as prose inside `reason`, which is not comparable."""
    pid = memory_db.insert_proposal(
        type="ghost_link_stub", source_ids=[], topic="Some Target",
        reason="Ghost link [[Some Target]] cited in 3 notes (400 context chars).",
        evidence=3,
    )
    assert memory_db.reject_proposal(pid)

    rejected = [p for p in memory_db.get_rejected_proposals("ghost_link_stub")
                if p["topic"] == "Some Target"]
    assert rejected and rejected[0]["evidence"] == 3
