# test_vault_ignore_layers.py
# date created: 2026-09-26
# date modified: 2026-09-26 17:37:34
# tags: #config, #vault, #privacy, #layering, #testing

"""One operator's folder names must not ship to everyone who clones this.

`TAG_LIBRARIAN_EXCLUDED_PREFIXES` held both kinds of fact at once: that `Templates/` is not a
source of subjects, which is true of any vault built on this engine, and that `Reference
Library/` is not either, which is a statement about exactly one person's vault. The tracked
file cannot carry the second kind — it is the same split as the taxonomy's base and local
layers, for the same reason.
"""

import pytest

import evelyn_config as cfg


def test_the_two_layers_combine() -> None:
    combined = cfg.TAG_LIBRARIAN_EXCLUDED_PREFIXES
    for entry in cfg.VAULT_STRUCTURAL_IGNORE:
        assert entry in combined
    for entry in cfg.VAULT_USER_IGNORE:
        assert entry in combined


def test_the_tracked_layer_names_nobody() -> None:
    """The invariant this split exists for, enforced rather than trusted.

    A structural entry may *interpolate* an identity — the assistant's own context folder is
    a real structural fact — but the tracked source must not contain the configured names as
    literals, because that is what reaches version control (§4).
    """
    import inspect
    import re

    source = inspect.getsource(cfg)
    block = re.search(r"VAULT_STRUCTURAL_IGNORE = \[(.*?)\]", source, re.S)
    assert block, "VAULT_STRUCTURAL_IGNORE is not a literal list any more"
    literal = block.group(1)

    for name in (cfg.USER_NAME, *getattr(cfg, "USER_LEGACY_ALIASES", [])):
        if not name or name.lower() in ("user", ""):
            continue
        assert name not in literal, (
            f"'{name}' is written into the tracked structural ignore list; "
            "personal paths belong in EVELYN_VAULT_USER_IGNORE"
        )


def test_a_user_entry_interpolates_its_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path can name its owner without that name entering version control."""
    import importlib

    monkeypatch.setenv("EVELYN_VAULT_USER_IGNORE", "{USER_NAME}/Private/, Imported/")
    reloaded = importlib.reload(cfg)
    try:
        assert f"{reloaded.USER_NAME}/Private/" in reloaded.VAULT_USER_IGNORE
        assert "Imported/" in reloaded.VAULT_USER_IGNORE
    finally:
        monkeypatch.delenv("EVELYN_VAULT_USER_IGNORE", raising=False)
        importlib.reload(cfg)


def test_an_empty_user_layer_is_not_an_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """A blank env var must not become a prefix that matches every path."""
    import importlib

    monkeypatch.setenv("EVELYN_VAULT_USER_IGNORE", "")
    reloaded = importlib.reload(cfg)
    try:
        assert reloaded.VAULT_USER_IGNORE == []
        assert "" not in reloaded.TAG_LIBRARIAN_EXCLUDED_PREFIXES
    finally:
        monkeypatch.delenv("EVELYN_VAULT_USER_IGNORE", raising=False)
        importlib.reload(cfg)


def test_the_audit_queue_honours_both_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lists are only worth anything where the audit actually reads them."""
    from Evelyn.tools import vault_db

    monkeypatch.setattr(
        cfg, "TAG_LIBRARIAN_EXCLUDED_PREFIXES", ["Templates/", "Imported/"]
    )
    vault_db.init_db()
    for path in ("Notes/Real.md", "Templates/Daily.md", "Imported/Vendor.md"):
        vault_db.upsert_document(path=path, title=path, mtime=1.0, gist="",
                                 rag_priority="normal", rag_pinned=False, tags="", aliases="")

    queued = {d["path"] for d in vault_db.fetch_next_documents_for_semantic_tag_audit(batch_size=50)}

    assert "Notes/Real.md" in queued
    assert "Templates/Daily.md" not in queued, "structural layer not applied"
    assert "Imported/Vendor.md" not in queued, "user layer not applied"
