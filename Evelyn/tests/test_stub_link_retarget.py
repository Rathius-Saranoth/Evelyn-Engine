# test_stub_link_retarget.py
# date created: 2026-09-26 19:02:40
# date modified: 2026-09-26 19:05:18
# tags:

"""Tests for filename-safety detection and inbound wikilink retargeting.

A wikilink target may contain characters a filename may not (`[[Nier: Automata]]`).
The note is stored under a sanitised stem, and Obsidian resolves links by filename
only — a frontmatter alias never makes a bare `[[Alias]]` resolve. Unless the links
themselves are rewritten the note is an orphan and the links are ghosts forever.
"""

import os

import pytest

from Evelyn.tools import link_librarian, string_utils

# --------------------------------------------------------------------------------------
# is_filename_safe
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Mana Series",
        "Borderlands (game series)",
        "Snail's House",
        "Eve-online",
        "Sekulich Genealogy",
    ],
)
def test_safe_names_round_trip(name):
    assert string_utils.is_filename_safe(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "Nier: Automata",
        "Re:Zero",
        "9:00 PM Cutoff",
        "Lego: The Hobbit",
        "Super Smash Bros.",
        'Quote"Mark',
        "Slash/Target",
    ],
)
def test_unsafe_names_are_flagged(name):
    assert string_utils.is_filename_safe(name) is False


def test_empty_name_is_not_safe():
    assert string_utils.is_filename_safe("") is False


# --------------------------------------------------------------------------------------
# retarget_inbound_links
# --------------------------------------------------------------------------------------


def _vault(tmp_path, files: dict[str, str]) -> str:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return str(tmp_path)


def test_bare_link_keeps_the_written_words_as_display(tmp_path):
    root = _vault(tmp_path, {"a.md": "He replayed [[Nier: Automata]] last night.\n"})

    n, actions = link_librarian.retarget_inbound_links(
        "Nier: Automata", "Nier Automata", vault_root=root, sources=["a.md"]
    )

    assert n == 1
    assert actions == ["retargeted_links:a.md:1"]
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == (
        "He replayed [[Nier Automata|Nier: Automata]] last night.\n"
    )


def test_piped_link_keeps_the_authors_display_text(tmp_path):
    root = _vault(tmp_path, {"a.md": "the [[Nier: Automata|android game]] soundtrack\n"})

    link_librarian.retarget_inbound_links(
        "Nier: Automata", "Nier Automata", vault_root=root, sources=["a.md"]
    )

    assert (tmp_path / "a.md").read_text(encoding="utf-8") == (
        "the [[Nier Automata|android game]] soundtrack\n"
    )


def test_casing_variant_is_retargeted_and_keeps_its_own_spelling(tmp_path):
    """`[[NieR: Automata]]` is the same dead target; its spelling is the author's."""
    root = _vault(tmp_path, {"a.md": "elegies of [[NieR: Automata]] wove a tapestry\n"})

    link_librarian.retarget_inbound_links(
        "Nier: Automata", "Nier Automata", vault_root=root, sources=["a.md"]
    )

    assert "[[Nier Automata|NieR: Automata]]" in (tmp_path / "a.md").read_text(encoding="utf-8")


def test_subpath_is_preserved(tmp_path):
    root = _vault(tmp_path, {"a.md": "see [[Nier: Automata#Combat]] for detail\n"})

    link_librarian.retarget_inbound_links(
        "Nier: Automata", "Nier Automata", vault_root=root, sources=["a.md"]
    )

    assert "[[Nier Automata#Combat|Nier: Automata]]" in (
        tmp_path / "a.md"
    ).read_text(encoding="utf-8")


def test_links_inside_code_blocks_are_left_alone(tmp_path):
    body = (
        "Real link: [[Nier: Automata]]\n\n"
        "```python\n"
        "wikilink = '[[Nier: Automata]]'\n"
        "```\n\n"
        "Inline `[[Nier: Automata]]` too.\n"
    )
    root = _vault(tmp_path, {"a.md": body})

    link_librarian.retarget_inbound_links(
        "Nier: Automata", "Nier Automata", vault_root=root, sources=["a.md"]
    )

    out = (tmp_path / "a.md").read_text(encoding="utf-8")
    assert "Real link: [[Nier Automata|Nier: Automata]]" in out
    assert "wikilink = '[[Nier: Automata]]'" in out
    assert "Inline `[[Nier: Automata]]` too." in out


def test_unrelated_links_are_untouched(tmp_path):
    root = _vault(
        tmp_path, {"a.md": "[[Nier: Automata]] and [[Chrono Trigger]] and [[Nier Automata 2]]\n"}
    )

    link_librarian.retarget_inbound_links(
        "Nier: Automata", "Nier Automata", vault_root=root, sources=["a.md"]
    )

    out = (tmp_path / "a.md").read_text(encoding="utf-8")
    assert "[[Chrono Trigger]]" in out
    assert "[[Nier Automata 2]]" in out


def test_no_write_when_target_already_matches_the_stem(tmp_path):
    root = _vault(tmp_path, {"a.md": "[[Mana Series]]\n"})
    before = os.path.getmtime(tmp_path / "a.md")

    n, actions = link_librarian.retarget_inbound_links(
        "Mana Series", "Mana Series", vault_root=root, sources=["a.md"]
    )

    assert (n, actions) == (0, [])
    assert os.path.getmtime(tmp_path / "a.md") == before


def test_missing_source_file_is_skipped_not_fatal(tmp_path):
    root = _vault(tmp_path, {"a.md": "[[Nier: Automata]]\n"})

    n, _ = link_librarian.retarget_inbound_links(
        "Nier: Automata", "Nier Automata", vault_root=root, sources=["gone.md", "a.md"]
    )

    assert n == 1


def test_counts_every_occurrence_across_several_notes(tmp_path):
    root = _vault(
        tmp_path,
        {
            "a.md": "[[Nier: Automata]] twice: [[Nier: Automata|it]]\n",
            "b.md": "once [[Nier: Automata]]\n",
            "c.md": "no mention here\n",
        },
    )

    n, actions = link_librarian.retarget_inbound_links(
        "Nier: Automata", "Nier Automata", vault_root=root, sources=["a.md", "b.md", "c.md"]
    )

    assert n == 2
    assert actions == ["retargeted_links:a.md:2", "retargeted_links:b.md:1"]


def test_stub_relpath_and_retarget_agree_on_the_stem(tmp_path):
    """The stem the note is filed under must be the stem the links are pointed at."""
    relpath = link_librarian.stub_relpath("Nier: Automata", domain="Gaming")
    stem = os.path.splitext(os.path.basename(relpath))[0]

    root = _vault(tmp_path, {"a.md": "[[Nier: Automata]]\n"})
    link_librarian.retarget_inbound_links(
        "Nier: Automata", stem, vault_root=root, sources=["a.md"]
    )

    assert f"[[{stem}|Nier: Automata]]" in (tmp_path / "a.md").read_text(encoding="utf-8")
