# test_admission_refuses_hierarchy.py
# date created: 2026-09-25
# date modified: 2026-09-25 20:15:28
# tags: #taxonomy, #admission, #hierarchy, #testing

"""A pre-coordinate compound may not become a proposal.

Post-coordination left exactly one use for `/`: naming which axis a facet value sits on
(§3.3). `lore/campaign-narrative` is `lore` and `campaign-narrative` glued together by an
indexer guessing which combination a future query would want — the structure this vocabulary
removed.

Nothing checked. `normalize_tag_format` preserves the slash and `is_excluded_tag` ignores it,
so two such terms reached the review queue on 2026-09-23. They were rejected, and because
`.231` made a rejection permanent, that rejection became the only thing stopping them
returning — **a format rule delegated to a human decision**, which also meant the rows could
not be tidied away without reopening the hole.

The gate belongs at admission, so the rejection rows are free to be deleted as the errors they
look like.
"""

import pytest

from Evelyn.tools import memory_db, tag_librarian


@pytest.mark.parametrize("term", ["lore/campaign-narrative", "gaming/dn-d-characters",
                                  "work/routine/morning", "health/sleep"])
def test_a_hierarchical_compound_is_never_proposed(term) -> None:
    assert tag_librarian.propose_tag_admission([term], origin="test") == []
    assert not [
        p for p in memory_db.get_pending_proposals(tag_librarian.TAG_ADMISSION_PROPOSAL)
        if p["topic"] == term
    ]


@pytest.mark.parametrize("term", ["type/media/text", "setting/forest", "motif/storm",
                                  "event/move"])
def test_a_facet_value_is_still_welcome(term) -> None:
    """The guard must not eat the one legitimate use of a slash, including DCMI sub-typing."""
    assert tag_librarian.is_wellformed_term(term)


def test_a_flat_term_is_unaffected() -> None:
    assert tag_librarian.propose_tag_admission(["kintsugi"], origin="test") == ["kintsugi"]


def test_the_predicate_answers_both_ways() -> None:
    assert tag_librarian.is_wellformed_term("budgeting")
    assert not tag_librarian.is_wellformed_term("lore/campaign-narrative")
