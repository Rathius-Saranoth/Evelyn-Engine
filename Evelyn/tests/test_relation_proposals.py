# test_relation_proposals.py
# date created: 2026-09-25
# date modified: 2026-09-25 19:20:57
# tags: #taxonomy, #relations, #proposals, #review, #testing

"""Relation candidates reach the review queue, and both answers have a reader (F7).

The measurement existed as a script nobody called, so the only way to see a candidate was to
run a command by hand — and the first 155 relations were curated over a terminal and a
conversation, outside the review system that exists for exactly this. A producer that raises
nothing, or a queue whose answers nothing reads, is the defect this week keeps finding.

So both directions are asserted here:

* **approve** — `related`, `narrower` and `alias` are three different decisions, and `alias`
  is the one that removes a term from the vocabulary rather than adding a row.
* **deny** — a rejected pair is not re-proposed, re-opens only when its evidence grows
  (`.234`'s per-type policy: a relation candidate *is* an evidence claim), and each suppressed
  request counts against it so a pair the corpus keeps insisting on arrives with the number
  that argues for it.
"""

import pytest

from Evelyn.tools import memory_db, tag_relations, taxonomy_db

CATEGORIES = {"cpap": "sleep-rest", "sleep": "sleep-rest", "hvac": "", "thermostat": ""}


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(taxonomy_db, "get_related_terms", lambda _t: [])
    monkeypatch.setattr(
        taxonomy_db, "get_master_tags",
        lambda: [{"tag": t, "category": c} for t, c in CATEGORIES.items()],
    )
    monkeypatch.setattr(taxonomy_db, "get_aliases", lambda: {})


def _candidate(a: str, b: str, documents: int) -> tag_relations.RelationCandidate:
    return tag_relations.RelationCandidate(
        term_a=a, term_b=b, lift=12.0, documents=documents, areas=3,
        category_a=CATEGORIES.get(a, ""), category_b=CATEGORIES.get(b, ""),
    )


def _generator(monkeypatch: pytest.MonkeyPatch, *candidates) -> None:
    monkeypatch.setattr(
        tag_relations, "find_relation_candidates",
        lambda *a, **k: (list(candidates), {"corpus": 100, "facet_pairs": 0, "already_related": 0}),
    )


# ------------------------------------------------------------------ the producer


def test_a_candidate_becomes_a_reviewable_proposal(monkeypatch: pytest.MonkeyPatch) -> None:
    _generator(monkeypatch, _candidate("cpap", "sleep", 52))

    assert tag_relations.propose_tag_relations() == ["cpap:sleep"]

    pending = [
        p for p in memory_db.get_pending_proposals(tag_relations.RELATION_PROPOSAL)
        if p["topic"] == "cpap:sleep"
    ]
    assert pending, "the pair must actually be in the queue, not merely returned"
    assert pending[0]["evidence"] == 52, "the document count is the claim a rejection is about"
    assert "52 documents" in pending[0]["reason"], "the reviewer needs the evidence on the card"


def test_a_pending_pair_is_not_raised_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    _generator(monkeypatch, _candidate("hvac", "thermostat", 6))
    assert tag_relations.propose_tag_relations() == ["hvac:thermostat"]

    assert tag_relations.propose_tag_relations() == []


def test_the_queue_is_measured_before_the_corpus_is_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counting co-occurrence reads both substrates end to end, on the idle schedule.

    Paying for that to discover the queue is full is the kind of waste that only shows up as
    a slow machine, so the room check comes first.
    """
    called = []
    monkeypatch.setattr(
        tag_relations, "find_relation_candidates",
        lambda *a, **k: (called.append(1), ([], {}))[1],
    )
    monkeypatch.setattr(tag_relations, "MAX_PENDING", 0)

    assert tag_relations.propose_tag_relations() == []
    assert called == [], "the corpus must not be counted when there is no room"


def test_the_cap_bounds_one_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    _generator(
        monkeypatch,
        _candidate("cpap", "sleep", 52),
        _candidate("hvac", "thermostat", 6),
    )
    assert len(tag_relations.propose_tag_relations(limit=1)) == 1


# ------------------------------------------------------------------- the denial


def _reject(pair: str, evidence: int) -> int:
    pid = memory_db.insert_proposal(
        type=tag_relations.RELATION_PROPOSAL, source_ids=[], topic=pair,
        reason="…", evidence=evidence,
    )
    assert memory_db.reject_proposal(pid)
    return pid


def test_a_rejected_pair_is_not_re_proposed(monkeypatch: pytest.MonkeyPatch) -> None:
    _reject("cpap:sleep", 52)
    _generator(monkeypatch, _candidate("cpap", "sleep", 52))

    assert tag_relations.propose_tag_relations() == []


def test_a_rejected_pair_re_opens_once_the_evidence_grows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Not related" was a judgement about the documents that existed when it was made."""
    _reject("hvac:thermostat", 6)
    _generator(monkeypatch, _candidate("hvac", "thermostat", 11))
    assert tag_relations.propose_tag_relations() == [], "11 is below 2x the rejected 6"

    _generator(monkeypatch, _candidate("hvac", "thermostat", 12))
    assert tag_relations.propose_tag_relations() == ["hvac:thermostat"]


def test_each_suppressed_request_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pair the corpus keeps asking for should arrive with the number that argues for it."""
    pid = _reject("cpap:sleep", 52)
    _generator(monkeypatch, _candidate("cpap", "sleep", 52))

    tag_relations.propose_tag_relations()
    tag_relations.propose_tag_relations()

    row = next(
        p for p in memory_db.get_rejected_proposals(tag_relations.RELATION_PROPOSAL)
        if p["id"] == pid
    )
    assert row["rejection_count"] == 3, "1 seeded by the rejection, plus two suppressed asks"


# ------------------------------------------------------------------ the approval


def test_approving_as_related_records_a_symmetric_relation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded = {}
    monkeypatch.setattr(
        taxonomy_db, "record_relation",
        lambda a, b, **kw: recorded.update({"pair": (a, b), "kind": kw.get("kind")}),
    )

    tag_relations.apply_relation_decision("cpap:sleep", "related")

    assert recorded == {"pair": ("cpap", "sleep"), "kind": "related"}


def test_approving_as_narrower_keeps_the_order_the_reviewer_gave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Order *is* the claim. Sorting would reverse about half of them silently."""
    recorded = {}
    monkeypatch.setattr(
        taxonomy_db, "record_relation",
        lambda a, b, **kw: recorded.update({"pair": (a, b), "kind": kw.get("kind")}),
    )

    tag_relations.apply_relation_decision("lucid-dreaming:dream", "narrower")

    assert recorded == {"pair": ("lucid-dreaming", "dream"), "kind": "narrower"}


def test_approving_as_alias_retires_rather_than_relates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The outcome that removes a term, which is why the card must name it (§6.2)."""
    from Evelyn.tools import tag_librarian

    retired = {}
    monkeypatch.setattr(
        tag_librarian, "retire_term",
        lambda term, replacement="": (retired.update({"term": term, "onto": replacement}), True)[1],
    )
    monkeypatch.setattr(
        taxonomy_db, "record_relation",
        lambda *a, **k: pytest.fail("an alias is not a relation (§6.4)"),
    )

    tag_relations.apply_relation_decision("romance:intimacy", "alias")

    assert retired == {"term": "romance", "onto": "intimacy"}


@pytest.mark.parametrize("bad", ["cpap", "cpap:", ":sleep", "sleep:sleep"])
def test_a_malformed_pair_is_refused(bad) -> None:
    with pytest.raises(ValueError):
        tag_relations.apply_relation_decision(bad, "related")


def test_an_unknown_kind_is_refused() -> None:
    """Defaulting an unrecognised verdict to `related` would invent a decision."""
    with pytest.raises(ValueError, match="related, narrower, alias"):
        tag_relations.apply_relation_decision("cpap:sleep", "equivalent")
