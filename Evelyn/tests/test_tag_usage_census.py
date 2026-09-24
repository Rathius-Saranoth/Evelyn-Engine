# test_tag_usage_census.py
# date created: 2026-09-24
# date modified: 2026-09-24 17:26:08
# tags: #taxonomy, #census, #retirement, #testing

"""The usage census must read the whole corpus, not the vault alone.

§0 of `.agents/rules/vault-tag-taxonomy.md` treats vault notes and memory facts as one
corpus governed by one vocabulary. The census behind `maintain_master_taxonomy` counted
vault documents only, so a term used exclusively by memory facts reported zero uses — and
zero uses is what eventually proposes a term for retirement. Seven registered terms were
in that position, five of them unprotected and therefore on the clock.

These tests pin the substrates the census reads, so narrowing it again fails here rather
than silently in ninety days.
"""

import time

import pytest

import evelyn_config as cfg
from Evelyn.tools import memory_db, tag_librarian, taxonomy_db, vault_db


@pytest.fixture
def no_chroma(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep maintenance off the vector store; this suite is about the count, not the index."""
    monkeypatch.setattr(tag_librarian, "index_master_tag_in_chroma", lambda *a, **k: None)
    monkeypatch.setattr(tag_librarian, "delete_tag_from_chroma", lambda *a, **k: None)


def _seed_corpus() -> None:
    """One tagged record in each substrate, each with a term unique to it."""
    vault_db.upsert_document(
        path="Notes/census-vault.md", title="Census Vault", mtime=time.time(), tags="vault-only-term"
    )
    memory_db.insert_entry(
        category=f"Cat05-{getattr(cfg, 'SUBJECT_CODE_USER', 'U')}",
        subject=getattr(cfg, "USER_NAME", "Tester"),
        observation="A memory fact carrying a term no vault note uses.",
        tags="memory-only-term",
    )
    memory_db.insert_procedure(
        trigger_pattern="when the census is under test",
        steps="1. Count the procedure's tags.",
        tags="procedure-only-term",
    )


def test_census_counts_every_substrate(no_chroma: None) -> None:
    """A term is counted wherever it lives: vault note, memory fact, or procedure."""
    _seed_corpus()

    counts, sizes = tag_librarian.census_tag_usage()

    assert counts.get("vault-only-term") == 1
    assert counts.get("memory-only-term") == 1, "memory facts are part of the corpus (§0)"
    assert counts.get("procedure-only-term") == 1, "procedures carry controlled-vocabulary tags"
    assert sizes == {"vault": 1, "memory": 1, "procedures": 1}


def test_census_ignores_records_that_are_not_live(no_chroma: None) -> None:
    """A deleted fact is not corpus. Counting it would keep a dead term alive."""
    memory_db.insert_entry(
        category=f"Cat05-{getattr(cfg, 'SUBJECT_CODE_USER', 'U')}",
        subject=getattr(cfg, "USER_NAME", "Tester"),
        observation="A fact that was removed.",
        status="deleted",
        tags="deleted-fact-term",
    )

    counts, sizes = tag_librarian.census_tag_usage()

    assert "deleted-fact-term" not in counts
    assert sizes["memory"] == 0


def test_memory_only_term_survives_maintenance(no_chroma: None) -> None:
    """The regression itself: a term used only in memory must not be retired as unused."""
    _seed_corpus()
    long_ago = time.time() - (getattr(cfg, "TAG_RETIREMENT_GRACE_DAYS", 90) + 30) * 86400

    for term in ("vault-only-term", "memory-only-term", "procedure-only-term", "orphan-term"):
        taxonomy_db.upsert_master_tag(term, category="general", description=f"{term} scope")
    # Registration date drives the grace period, so age every row past it.
    con = vault_db.get_db()
    con.execute("UPDATE master_tag_taxonomy SET created_at = ?", (long_ago,))
    con.commit()
    con.close()

    result = tag_librarian.maintain_master_taxonomy()

    assert result["status"] == "success"
    proposed = {
        (p.get("topic") or "").strip()
        for p in memory_db.get_pending_proposals(tag_librarian.TAG_RETIREMENT_PROPOSAL)
    }
    assert proposed == {"orphan-term"}, (
        "only the term nothing uses may be proposed; the others are in active use"
    )

    registry = {m["tag"]: m for m in taxonomy_db.get_master_tags()}
    assert registry["memory-only-term"]["usage_count"] == 1
    assert registry["procedure-only-term"]["usage_count"] == 1


def test_maintenance_aborts_when_the_vault_reads_empty(no_chroma: None) -> None:
    """Memory alone must not satisfy the circuit breaker: an unread vault zeroes its terms."""
    memory_db.insert_entry(
        category=f"Cat05-{getattr(cfg, 'SUBJECT_CODE_USER', 'U')}",
        subject=getattr(cfg, "USER_NAME", "Tester"),
        observation="The only record in the corpus.",
        tags="memory-only-term",
    )
    taxonomy_db.upsert_master_tag("vault-only-term", category="general")

    result = tag_librarian.maintain_master_taxonomy()

    assert result["status"] == "aborted"
    assert result["reason"] == "empty_vault_documents"
    assert not memory_db.get_pending_proposals(tag_librarian.TAG_RETIREMENT_PROPOSAL)
