# test_see_reference_action.py
# date created: 2026-09-26
# date modified: 2026-09-26 08:45:56
# tags: #taxonomy, #review, #aliases, #equivalence, #testing

"""A reviewer can say "we already have a word for that" and have it recorded.

The admission card told reviewers to **Reject** when an existing term covered a proposal, and
rejecting wrote no `UF` row — so §6.2's own rule was broken by the UI citing it: *a deleted
synonym with no `UF` record will be re-minted by the next import*. Since `.231` the rejection
does stop the term coming back, so nothing was re-minted; the equivalence was simply lost, and
retrieval never learned that the two words mean the same thing.

`action = "alias"` is the third verdict. Reject still means "not a subject at all".
"""

import asyncio

import pytest

import evelyn_server
from Evelyn.tools import memory_db, taxonomy_db


def _act(pid: int, canonical: str):
    req = evelyn_server.ProposalActionRequest(modified_text=canonical)
    return asyncio.run(evelyn_server._apply_proposal_action(pid, "alias", req, refresh=False))


@pytest.fixture
def vocabulary(monkeypatch: pytest.MonkeyPatch) -> None:
    memory_db.init_db()
    taxonomy_db.init_db()
    for term in ("braid", "genealogy"):
        taxonomy_db.upsert_master_tag(term, category="craft")
    taxonomy_db.invalidate_alias_cache()
    monkeypatch.setattr(evelyn_server, "start_refresh_memory_internal", lambda: None)


def _admission(term: str, source_ids: list[int] | None = None, path: str = "") -> int:
    return memory_db.insert_proposal(
        type="tag_admission", source_ids=source_ids or [], topic=term,
        reason="test", merged_observation="test origin", source_path=path or None,
    )


def test_it_records_the_equivalence_and_closes_the_proposal(vocabulary: None) -> None:
    pid = _admission("braiding")

    _act(pid, "braid")

    assert taxonomy_db.get_aliases()["braiding"] == "braid"
    assert pid not in {p["id"] for p in memory_db.get_pending_proposals()}


def test_the_fact_that_asked_gets_the_preferred_term(vocabulary: None) -> None:
    """Recording the equivalence and leaving the fact untagged is the G5 gap again."""
    entry_id = memory_db.insert_entry(
        category="Cat05-U", subject="Tester",
        observation="A fact that asked for a word the vocabulary words differently.",
        tags="craft",
    )
    pid = _admission("braiding", source_ids=[entry_id])

    _act(pid, "braid")

    tags = [t.strip() for t in (memory_db.get_entry(entry_id).get("tags") or "").split(",")]
    assert "braid" in tags, "The preferred term must reach the fact that prompted the reference"
    assert "braiding" not in tags, "...and the retired one must not"


def test_the_target_must_be_in_the_vocabulary(vocabulary: None) -> None:
    """A reference to nothing resolves every tag onto an unregistered word."""
    pid = _admission("braiding")

    with pytest.raises(evelyn_server.HTTPException) as caught:
        _act(pid, "zzz-not-a-term")

    assert caught.value.status_code == 400
    assert "braiding" not in taxonomy_db.get_aliases()


def test_a_term_cannot_point_at_itself(vocabulary: None) -> None:
    pid = _admission("braid")

    with pytest.raises(evelyn_server.HTTPException) as caught:
        _act(pid, "braid")

    assert caught.value.status_code == 400


def test_the_target_is_resolved_through_existing_equivalences(vocabulary: None) -> None:
    """Pointing at a term that is itself retired would store a dead target."""
    taxonomy_db.record_alias("ancestry", "genealogy")
    taxonomy_db.invalidate_alias_cache()
    pid = _admission("lineage")

    _act(pid, "ancestry")

    assert taxonomy_db.get_aliases()["lineage"] == "genealogy", "Stored flat, not chained"


def test_a_registered_term_is_a_valid_target(vocabulary: None) -> None:
    """A registered term in the controlled vocabulary can be pointed at."""
    taxonomy_db.upsert_master_tag("leather", category="objects")
    pid = _admission("hides")

    _act(pid, "leather")

    assert taxonomy_db.get_aliases()["hides"] == "leather"


def test_only_an_admission_can_become_a_see_reference(vocabulary: None) -> None:
    pid = memory_db.insert_proposal(type="split", source_ids=[], topic="something")

    with pytest.raises(evelyn_server.HTTPException) as caught:
        _act(pid, "braid")

    assert caught.value.status_code == 400


def test_it_works_through_the_bulk_route(vocabulary: None) -> None:
    """A flood of near-duplicates is exactly when this verdict is needed most."""
    ids = [_admission(t) for t in ("braiding", "braider-style")]
    req = evelyn_server.BulkProposalActionRequest(decisions=[
        evelyn_server.BulkProposalDecision(id=i, action="alias", modified_text="braid")
        for i in ids
    ])

    result = asyncio.run(evelyn_server.action_proposals_bulk(req, None))

    assert result["applied"] == 2
    aliases = taxonomy_db.get_aliases()
    assert aliases["braiding"] == "braid"
    assert aliases["braider-style"] == "braid"
