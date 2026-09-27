# test_stub_evidence_independence.py
# date created: 2026-09-26 20:06:04
# date modified: 2026-09-26 20:07:11
# tags:

"""Tests that a stub is not counted as evidence for another stub.

A stub's `Context & Mentions` section is made of excerpts harvested from elsewhere. Quoting
it counts one original source twice, and quoting a stub that itself quoted a stub compounds
— which is how 307 excerpt-truncation artefacts propagated through 134 notes before `.257`.

Every stub got its evidence from outside the stub tree, so that is where it should be read.
"""

import pytest

from Evelyn.tools import link_librarian

# --------------------------------------------------------------------------------------
# _is_stub_note
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("tags", [
    ["type/stub"],
    ["journaling", "type/stub"],
    ["TYPE/STUB"],
    "type/stub",
    "gaming, type/stub",
])
def test_stub_tag_is_recognised(tags):
    assert link_librarian._is_stub_note({"tags": tags}) is True


@pytest.mark.parametrize("type_val", [
    ["stub"],
    ["STUB"],
    "stub",
    ["stub", "reference"],
])
def test_stub_type_property_is_recognised(type_val):
    assert link_librarian._is_stub_note({"type": type_val}) is True


@pytest.mark.parametrize("tags", [
    ["type/reference", "video-games"],
    ["type/profile"],
    ["stub"],              # the bare word is a subject, not the form facet
    ["type/stubbed-toe"],  # prefix collision
    [],
])
def test_non_stub_tags_are_not_recognised(tags):
    assert link_librarian._is_stub_note({"tags": tags}) is False


def test_missing_or_empty_frontmatter():
    assert link_librarian._is_stub_note(None) is False
    assert link_librarian._is_stub_note({}) is False


# --------------------------------------------------------------------------------------
# harvest_entity_references
# --------------------------------------------------------------------------------------


def _vault(tmp_path, files: dict[str, str]) -> str:
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return str(tmp_path)


def _note(tags: str, body: str) -> str:
    return f"---\ntitle: t\ntags: [{tags}]\n---\n\n{body}\n"


def test_a_stub_is_not_harvested_as_a_reference(tmp_path):
    root = _vault(tmp_path, {
        "Journal/Entry.md": _note("type/journal-entry", "We played [[Beat Saber]] on Friday."),
        "Stubs/Valheim.md": _note("type/stub", "Context: quoted from elsewhere, [[Beat Saber]]."),
    })

    refs = link_librarian.harvest_entity_references("Beat Saber", vault_root=root)
    sources = [r["source"] for r in refs]

    assert "Journal/Entry.md" in sources
    assert "Stubs/Valheim.md" not in sources


def test_a_target_cited_only_by_stubs_yields_no_evidence(tmp_path):
    """The case that mattered: circular citation producing a stub made of stubs."""
    root = _vault(tmp_path, {
        "Stubs/One.md": _note("type/stub", "mentions [[Phantom Thing]]"),
        "Stubs/Two.md": _note("type/stub", "also mentions [[Phantom Thing]]"),
        "Stubs/Three.md": _note("type/stub", "and [[Phantom Thing]] again"),
    })

    assert link_librarian.harvest_entity_references("Phantom Thing", vault_root=root) == []


def test_non_stub_notes_are_still_harvested_normally(tmp_path):
    root = _vault(tmp_path, {
        "Contacts/Person.md": _note("type/profile", "plays [[Valheim]] often"),
        "Notes/Games.md": _note("type/reference", "a note about [[Valheim]] and others"),
    })

    refs = link_librarian.harvest_entity_references("Valheim", vault_root=root)

    assert len(refs) == 2
    assert all(r["context"] for r in refs)


def test_the_stub_being_built_is_still_excluded_by_name(tmp_path):
    """The pre-existing self-reference guard must survive the new tag check."""
    root = _vault(tmp_path, {
        "Notes/Source.md": _note("type/reference", "about [[Valheim]] here"),
        "Valheim.md": _note("type/reference", "self-referencing [[Valheim]] mention"),
    })

    refs = link_librarian.harvest_entity_references("Valheim", vault_root=root)

    assert [r["source"] for r in refs] == ["Notes/Source.md"]
