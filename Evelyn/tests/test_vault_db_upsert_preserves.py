# test_vault_db_upsert_preserves.py
# date created: 2026-09-22 12:30:00
# date modified: 2026-09-22 07:29:57
# tags: #test, #vault, #sqlite, #upsert, #tags, #regression

"""Regression cover for vault_db.upsert_document metadata preservation.

A caller that knows only a document's path and mtime — an editor save, say — must not
silently discard the tags, aliases, gist and RAG priority the indexer computed. Before
v000.006.196 the ON CONFLICT clause assigned every column unconditionally, so the
parameter defaults blanked them on every API and UI note save.
"""

import time

from Evelyn.tools import vault_db


def _seed(monkeypatch, tmp_path, name):
    monkeypatch.setattr(vault_db, "DB_PATH", str(tmp_path / name))
    vault_db.init_db()
    vault_db.upsert_document(
        path="Slipbox/Note.md",
        title="Note",
        mtime=time.time(),
        gist="An indexed gist.",
        rag_priority="high",
        rag_pinned=True,
        tags="type/notes,tech/python",
        aliases="Alt Name",
    )


def test_partial_upsert_preserves_indexed_metadata(monkeypatch, tmp_path):
    """A path/title/mtime-only upsert must leave indexed metadata intact."""
    _seed(monkeypatch, tmp_path, "preserve.db")

    vault_db.upsert_document(path="Slipbox/Note.md", title="Note", mtime=time.time() + 10)

    doc = vault_db.get_document("Slipbox/Note.md")
    assert doc is not None
    assert doc["tags"] == "type/notes,tech/python"
    assert doc["aliases"] == "Alt Name"
    assert doc["gist"] == "An indexed gist."
    assert doc["rag_priority"] == "high"
    assert doc["rag_pinned"] == 1


def test_explicit_values_still_overwrite(monkeypatch, tmp_path):
    """Preservation must not block a caller that genuinely re-computes the metadata."""
    _seed(monkeypatch, tmp_path, "overwrite.db")

    vault_db.upsert_document(
        path="Slipbox/Note.md",
        title="Note",
        mtime=time.time() + 10,
        gist="",
        rag_priority="low",
        rag_pinned=False,
        tags="type/journal-entry",
        aliases="",
    )

    doc = vault_db.get_document("Slipbox/Note.md")
    assert doc is not None
    assert doc["tags"] == "type/journal-entry"
    assert doc["aliases"] == ""
    assert doc["gist"] == ""
    assert doc["rag_priority"] == "low"
    assert doc["rag_pinned"] == 0


def test_fresh_insert_uses_historical_defaults(monkeypatch, tmp_path):
    """A brand-new row with omitted metadata falls back to the documented defaults."""
    monkeypatch.setattr(vault_db, "DB_PATH", str(tmp_path / "fresh.db"))
    vault_db.init_db()

    vault_db.upsert_document(path="Slipbox/New.md", title="New", mtime=time.time())

    doc = vault_db.get_document("Slipbox/New.md")
    assert doc is not None
    assert doc["tags"] == ""
    assert doc["aliases"] == ""
    assert doc["gist"] == ""
    assert doc["rag_priority"] == "normal"
    assert doc["rag_pinned"] == 0
