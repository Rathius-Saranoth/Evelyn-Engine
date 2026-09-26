# test_vault_admission_backfill.py
# date created: 2026-09-25
# date modified: 2026-09-25 19:46:32
# tags: #taxonomy, #admission, #vault, #backfill, #testing

"""An admitted term must reach whatever asked for it — including a vault note (G5).

`admit_proposed_term` registers the word; `backfill_admitted_term` puts it on the facts that
wanted it. A vault-sourced proposal carries `source_ids = []`, because that column holds
`context_entries` ids and a note is not one, so the second call was a no-op and the note that
demonstrably concerns the subject stayed unindexed for it.

Measured on the three terms admitted 2026-09-25: the two raised by extracted facts landed on
those facts; the one raised by a journal note landed on nothing. **G1's shape one field
along** — approve has a consumer for one substrate and not the other, and the queue empties
either way, so it reads as finished.

The write preserves mtime deliberately, which means the vault watcher will not see it. The
index therefore has to be told directly, exactly as the audit tells it at the end of a pass —
that is asserted here, because a silent index divergence is the failure this would otherwise
produce.
"""

import os
from pathlib import Path

import pytest

import evelyn_config as cfg
from Evelyn.tools import memory_db, tag_librarian, vault_db

NOTE = "Notes/Backfill Target.md"
BODY = """---
title: Backfill Target
tags: [journaling]
---

# Backfill Target

Some prose about a subject the vocabulary did not yet hold.
"""


@pytest.fixture
def note() -> str:
    path = os.path.join(cfg.VAULT_BASE_DIR, NOTE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(BODY)
    vault_db.upsert_document(NOTE, title="Backfill Target", mtime=os.path.getmtime(path),
                             tags="journaling")
    return path


def _tags_on_disk(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return tag_librarian.parse_frontmatter_tags(fh.read())[0]


def test_the_note_gains_the_admitted_term(note: str) -> None:
    assert tag_librarian.backfill_admitted_term_to_note("wellbeing", NOTE) is True

    tags = _tags_on_disk(note)
    assert "wellbeing" in tags
    assert "journaling" in tags, "existing tags must survive"


def test_the_index_is_told_because_the_watcher_will_not_notice(note: str) -> None:
    """The write preserves mtime, so nothing else will reindex this note."""
    tag_librarian.backfill_admitted_term_to_note("wellbeing", NOTE)

    row = vault_db.get_document(NOTE)
    assert row is not None
    assert "wellbeing" in str(row.get("tags") or ""), "the index would silently diverge"


def test_a_term_already_present_is_not_added_twice(note: str) -> None:
    assert tag_librarian.backfill_admitted_term_to_note("journaling", NOTE) is False
    assert _tags_on_disk(note).count("journaling") == 1


def test_a_missing_note_is_refused_rather_than_created(note: str) -> None:
    assert tag_librarian.backfill_admitted_term_to_note("wellbeing", "Notes/Not Here.md") is False
    assert not os.path.exists(os.path.join(cfg.VAULT_BASE_DIR, "Notes/Not Here.md"))


@pytest.mark.parametrize("bad", ["", "   "])
def test_an_empty_path_is_a_no_op(bad, note: str) -> None:
    """Every memory-sourced proposal has one, so this is the common case, not an edge."""
    assert tag_librarian.backfill_admitted_term_to_note("wellbeing", bad) is False


def test_a_proposal_records_the_note_that_wanted_the_term() -> None:
    """Without this the approval has nothing to act on — the whole defect in one line."""
    pid = memory_db.insert_proposal(
        type="tag_admission", source_ids=[], topic="wellbeing",
        merged_observation=f"vault note ({NOTE})", source_path=NOTE,
    )
    stored = next(p for p in memory_db.get_pending_proposals("tag_admission") if p["id"] == pid)

    assert stored["source_path"] == NOTE
    assert stored["source_ids"] == [], "a note is not a context entry; the columns are distinct"


def test_the_vault_audit_passes_the_path_it_already_knows() -> None:
    """The producer half: the audit holds `doc_path` and must hand it over."""
    import inspect

    src = inspect.getsource(tag_librarian.audit_single_document_semantic)
    assert "source_path=doc_path" in src


def test_approval_calls_the_vault_backfill() -> None:
    """The consumer half. A backfill nothing calls is the defect it was written to fix."""
    server = Path(__file__).resolve().parents[2] / "evelyn_server.py"
    assert "backfill_admitted_term_to_note" in server.read_text(encoding="utf-8")


def test_a_path_escaping_the_vault_is_refused(note: str) -> None:
    """Resolving against a live root means re-proving the traversal guard here."""
    outside = os.path.join(cfg.VAULT_BASE_DIR, "..", "escaped.md")
    with open(outside, "w", encoding="utf-8") as fh:
        fh.write(BODY)
    try:
        assert tag_librarian.backfill_admitted_term_to_note("wellbeing", "../escaped.md") is False
        with open(outside, encoding="utf-8") as fh:
            assert "wellbeing" not in fh.read()
    finally:
        os.remove(outside)
