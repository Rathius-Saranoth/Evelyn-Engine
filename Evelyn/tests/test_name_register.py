# test_name_register.py
# date created: 2026-09-26
# date modified: 2026-09-26 17:20:01
# tags: #taxonomy, #names, #entities, #review, #testing

"""A name is not a subject, and the queue needs a third answer for one.

Admitting a shop or a product puts it into a controlled vocabulary of subjects, where the
classifier then applies it to unrelated notes. Rejecting is permanent since `.231` and is
scoped to the *word*, so turning down a retailer whose name is an ordinary adjective would
spend that adjective for good.

Authority control has always kept the two apart - LCSH beside LCNAF, four of FAST's nine
facets being name facets - because separate namespaces are what let a name and a subject
share a string without competing.
"""

import asyncio

import pytest

import evelyn_server
from Evelyn.tools import memory_db, tag_entities, tag_librarian, taxonomy_db


def _name(pid: int, label: str = "", kind: str = "other"):
    req = evelyn_server.ProposalActionRequest(modified_text=label, kind=kind)
    return asyncio.run(evelyn_server._apply_proposal_action(pid, "name", req, refresh=False))


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> None:
    memory_db.init_db()
    taxonomy_db.init_db()
    monkeypatch.setattr(tag_librarian, "index_master_tag_in_chroma", lambda *a, **k: None)


def _admission(term: str) -> int:
    return memory_db.insert_proposal(
        type="tag_admission", source_ids=[], topic=term,
        reason="test", merged_observation="test origin",
    )


def test_it_registers_the_name_and_closes_the_proposal(queue: None) -> None:
    pid = _admission("airthread")

    _name(pid, label="Airthread", kind="organization")

    assert tag_entities.is_entity("airthread")
    assert pid not in {p["id"] for p in memory_db.get_pending_proposals()}


def test_a_name_does_not_enter_the_subject_vocabulary(queue: None) -> None:
    """The whole reason this is not an approval."""
    pid = _admission("airthread")

    _name(pid, kind="organization")

    assert "airthread" not in {t["tag"] for t in taxonomy_db.get_master_tags()}


def test_a_registered_name_is_not_proposed_again(queue: None) -> None:
    pid = _admission("airthread")
    _name(pid, kind="organization")

    assert tag_librarian.propose_tag_admission(["airthread"], origin="a later pass") == []


def test_further_requests_are_counted_rather_than_dropped(queue: None) -> None:
    """A name the corpus keeps nominating may be a subject sense the vocabulary lacks.

    Only the number tells that apart from a name it simply keeps mentioning, and going quiet
    loses the distinction - the same reasoning as `record_rejected_request`.
    """
    pid = _admission("airthread")
    _name(pid, kind="organization")

    tag_librarian.propose_tag_admission(["airthread"], origin="one")
    tag_librarian.propose_tag_admission(["airthread"], origin="two")

    entity = next(e for e in tag_entities.get_entities() if e["term"] == "airthread")
    assert entity["request_count"] == 3


def test_the_word_survives_being_marked_a_name(queue: None) -> None:
    """The failure this exists to avoid: rejecting a shop spends the ordinary word.

    After forgetting the name, the term must be proposable as a subject again - which a
    rejection would never allow, being permanent and scoped to the word.
    """
    pid = _admission("historical")
    _name(pid, label="Historical Emporium", kind="organization")
    assert tag_librarian.propose_tag_admission(["historical"], origin="x") == []

    assert tag_entities.forget_entity("historical")

    assert tag_librarian.propose_tag_admission(["historical"], origin="y") == ["historical"]


def test_the_full_name_is_kept(queue: None) -> None:
    """The term is often lossy; the register is where the rest of the name goes back."""
    pid = _admission("historical")

    _name(pid, label="Historical Emporium", kind="organization")

    entity = next(e for e in tag_entities.get_entities() if e["term"] == "historical")
    assert entity["label"] == "Historical Emporium"
    assert entity["kind"] == "organization"


def test_re_recording_does_not_blank_a_label(queue: None) -> None:
    """An empty field means "leave it alone", the convention the registry already uses."""
    tag_entities.record_entity("historical", label="Historical Emporium", kind="organization")

    tag_entities.record_entity("historical", label="", kind="organization")

    entity = next(e for e in tag_entities.get_entities() if e["term"] == "historical")
    assert entity["label"] == "Historical Emporium"


def test_only_an_admission_can_be_recorded_as_a_name(queue: None) -> None:
    pid = memory_db.insert_proposal(type="split", source_ids=[], topic="something")

    with pytest.raises(evelyn_server.HTTPException) as caught:
        _name(pid)

    assert caught.value.status_code == 400


def test_it_works_through_the_bulk_route(queue: None) -> None:
    """The entity cluster arrives together - shops, platforms, products in one batch."""
    ids = [_admission(t) for t in ("airthread", "crunchyroll")]
    req = evelyn_server.BulkProposalActionRequest(decisions=[
        evelyn_server.BulkProposalDecision(id=i, action="name", kind="organization")
        for i in ids
    ])

    result = asyncio.run(evelyn_server.action_proposals_bulk(req, None))

    assert result["applied"] == 2
    assert tag_entities.is_entity("airthread")
    assert tag_entities.is_entity("crunchyroll")
