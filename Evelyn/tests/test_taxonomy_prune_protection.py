# test_taxonomy_prune_protection.py
# date created: 2026-09-22 12:35:00
# date modified: 2026-09-22 07:29:57
# tags: #test, #taxonomy, #tags, #vocabulary, #pruning, #regression

"""Regression cover for curated-vocabulary protection in taxonomy maintenance.

`maintain_master_taxonomy()` prunes terms with zero usage. That rule suits a folksonomy
grown from documents but is wrong for a controlled vocabulary, where terms are registered
deliberately and may be reserved before anything uses them. Before v000.006.196 it stood to
delete 54 curated terms — including DCMI `type/media` sub-types registered one release
earlier — while staying under the 15% circuit breaker.
"""

import time

import pytest

from Evelyn.tools import tag_librarian, taxonomy_db, vault_db


@pytest.fixture
def registry(monkeypatch, tmp_path):
    """Hermetic vault store holding both the document index and the taxonomy table."""
    monkeypatch.setattr(vault_db, "DB_PATH", str(tmp_path / "test_vault.db"))
    vault_db.init_db()
    # Chroma is a derived cache; the pruning decision is what is under test.
    monkeypatch.setattr(tag_librarian, "delete_tag_from_chroma", lambda *a, **k: None)
    monkeypatch.setattr(tag_librarian, "index_master_tag_in_chroma", lambda *a, **k: None)

    # One indexed document keeps a single term in use.
    vault_db.upsert_document(
        path="Slipbox/In Use.md", title="In Use", mtime=time.time(), tags="tech/python"
    )
    return vault_db


def _register(tag, protected):
    taxonomy_db.upsert_master_tag(tag, category=tag.split("/")[0], description="")
    con = vault_db.get_db()
    try:
        con.execute("UPDATE master_tag_taxonomy SET protected = ? WHERE tag = ?", (protected, tag))
        con.commit()
    finally:
        con.close()


def test_protected_terms_survive_zero_usage(registry):
    """A reserved curated term with no documents must not be pruned."""
    _register("tech/python", 1)
    _register("type/media/dataset", 1)   # reserved, nothing uses it yet
    _register("event/surgery", 1)        # reserved, nothing uses it yet

    result = tag_librarian.maintain_master_taxonomy()

    assert result["status"] == "success"
    assert result["removed_master_tags"] == 0
    assert result["protected_retained"] == 2
    surviving = {m["tag"] for m in taxonomy_db.get_master_tags()}
    assert {"type/media/dataset", "event/surgery"} <= surviving


def test_unprotected_terms_are_still_pruned(registry):
    """Terms that entered by inference remain prunable on zero usage."""
    _register("tech/python", 1)
    _register("stray-inferred-term", 0)

    result = tag_librarian.maintain_master_taxonomy()

    assert result["removed_master_tags"] == 1
    surviving = {m["tag"] for m in taxonomy_db.get_master_tags()}
    assert "stray-inferred-term" not in surviving
    assert "tech/python" in surviving
