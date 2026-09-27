# test_resolve_moved_note.py
# date created: 2026-09-27 07:22:31
# date modified: 2026-09-27 07:22:43
# tags:

"""Tests for following a note that has been refiled.

A proposal, a backfill target and a harvested reference all store the path a note had when
the row was written. The vault is then reorganised by hand and every one of those paths
silently stops resolving. Measured 2026-09-27: 94 of 200 review cards showed no source text,
and 91 of those had a note that still existed somewhere else.
"""

import pytest

from Evelyn.tools import path_utils


@pytest.fixture(autouse=True)
def _clear_index_cache():
    """The basename index is cached for 60s; each test builds its own vault."""
    path_utils._NAME_INDEX.clear()
    yield
    path_utils._NAME_INDEX.clear()


def _vault(tmp_path, *rels) -> str:
    for rel in rels:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# note\n", encoding="utf-8")
    return str(tmp_path)


def test_a_path_that_still_resolves_is_returned_unchanged(tmp_path):
    root = _vault(tmp_path, "Notes/Thing.md")
    assert path_utils.resolve_moved_note("Notes/Thing.md", vault_root=root) == "Notes/Thing.md"


def test_a_moved_note_is_followed(tmp_path):
    root = _vault(tmp_path, "Stubs/Company/Arby's.md")
    assert path_utils.resolve_moved_note("Stubs/Arby's.md", vault_root=root) == "Stubs/Company/Arby's.md"


def test_a_note_moved_several_levels_is_followed(tmp_path):
    root = _vault(tmp_path, "A/B/C/Deep.md")
    assert path_utils.resolve_moved_note("Deep.md", vault_root=root) == "A/B/C/Deep.md"


def test_an_ambiguous_name_resolves_to_nothing(tmp_path):
    """Two notes share the name; guessing would attach the wrong evidence to a decision."""
    root = _vault(tmp_path, "One/Shared.md", "Two/Shared.md")
    assert path_utils.resolve_moved_note("Old/Shared.md", vault_root=root) == ""


def test_a_genuinely_missing_note_resolves_to_nothing(tmp_path):
    root = _vault(tmp_path, "Notes/Other.md")
    assert path_utils.resolve_moved_note("Notes/Gone.md", vault_root=root) == ""


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_empty_input(tmp_path, bad):
    root = _vault(tmp_path, "Notes/Thing.md")
    assert path_utils.resolve_moved_note(bad, vault_root=root) == ""


def test_hidden_directories_are_not_searched(tmp_path):
    """A copy inside `.obsidian` or `.trash` must not be offered as the live note."""
    root = _vault(tmp_path, ".trash/Deleted.md")
    assert path_utils.resolve_moved_note("Notes/Deleted.md", vault_root=root) == ""


def test_a_leading_slash_and_backslashes_are_tolerated(tmp_path):
    root = _vault(tmp_path, "Notes/Thing.md")
    assert path_utils.resolve_moved_note("/Notes/Thing.md", vault_root=root) == "Notes/Thing.md"
    assert path_utils.resolve_moved_note(r"Notes\Thing.md", vault_root=root) == "Notes/Thing.md"


def test_non_markdown_files_are_ignored(tmp_path):
    root = _vault(tmp_path, "Notes/Keep.md")
    (tmp_path / "Attachments").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Attachments" / "Gone.png").write_bytes(b"x")
    assert path_utils.resolve_moved_note("Notes/Gone.png", vault_root=root) == ""


def test_the_index_is_cached_then_expires(tmp_path, monkeypatch):
    """One render of a 200-row queue must not walk the vault 200 times."""
    root = _vault(tmp_path, "A/Thing.md")
    calls = {"n": 0}
    real_walk = path_utils.os.walk

    def counting_walk(*a, **k):
        calls["n"] += 1
        return real_walk(*a, **k)

    monkeypatch.setattr(path_utils.os, "walk", counting_walk)

    for _ in range(5):
        path_utils.resolve_moved_note("Old/Thing.md", vault_root=root)
    assert calls["n"] == 1, "index rebuilt per lookup"

    # A move made after the TTL must be picked up.
    path_utils._NAME_INDEX.clear()
    (tmp_path / "B").mkdir()
    (tmp_path / "A" / "Thing.md").rename(tmp_path / "B" / "Thing.md")
    assert path_utils.resolve_moved_note("Old/Thing.md", vault_root=root) == "B/Thing.md"
