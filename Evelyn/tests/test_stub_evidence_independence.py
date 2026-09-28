# test_stub_evidence_independence.py
# date created: 2026-09-26 20:06:04
# date modified: 2026-09-27 21:28:55
# tags:

"""Tests that a stub is not counted as evidence for another stub.

A stub's `Context & Mentions` section is made of excerpts harvested from elsewhere. Quoting
it counts one original source twice, and quoting a stub that itself quoted a stub compounds
— which is how 307 excerpt-truncation artefacts propagated through 134 notes before `.257`.

Every stub got its evidence from outside the stub tree, so that is where it should be read.
"""

from unittest.mock import patch

import pytest

import evelyn_config as cfg
from Evelyn.tools import link_librarian, master_librarian

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


@pytest.mark.parametrize("path", [
    "Stubs/Google Sites.md",
    "stubs/gaming/Valheim.md",
    "/home/user/vault/Stubs/Valheim.md",
    "Stubs/Entities/Person.md",
    "stubs/tool.md",
])
def test_stub_path_is_recognised(path):
    assert link_librarian._is_stub_note(None, path=path) is True


@pytest.mark.parametrize("path", [
    "Notes/stubs.md",
    "Notes/Google Workspace.md",
    "Journal/2026-09-27.md",
    "stubs_index.md",
])
def test_non_stub_paths_are_not_recognised_as_stubs(path):
    assert link_librarian._is_stub_note(None, path=path) is False


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


# --------------------------------------------------------------------------------------
# create_ghost_link_stub caller stub isolation
# --------------------------------------------------------------------------------------


def test_caller_stub_does_not_inflate_count(tmp_path):
    """A stub passing itself as source_path must not be injected to meet min_refs."""
    root = _vault(tmp_path, {
        "Notes/Workspace.md": _note(
            "type/reference",
            "Here we discuss tools including [[Google Tasks]] in detail for team productivity."
        ),
        "Stubs/Google Sites.md": _note(
            "type/stub",
            "Mentioning [[Google Tasks]] inside a stub context."
        ),
    })

    res = link_librarian.create_ghost_link_stub(
        target_name="Google Tasks",
        source_path="Stubs/Google Sites.md",
        context_excerpt="Mentioning [[Google Tasks]] inside a stub context.",
        vault_root=root,
        min_refs=2,
    )

    # Must stay below threshold because only 1 legitimate source exists
    assert res["status"] == "below_threshold"
    assert res["ref_count"] == 1


def test_caller_stub_excluded_when_real_notes_meet_threshold(tmp_path):
    """When real notes meet min_refs, caller stub must still be excluded from sources."""
    root = _vault(tmp_path, {
        "Notes/One.md": _note("type/reference", "First reference to [[Concept X]] with extensive context characters."),
        "Notes/Two.md": _note("type/reference", "Second reference to [[Concept X]] with additional substantive description."),
        "Stubs/StubNote.md": _note("type/stub", "A stub quoting [[Concept X]]."),
    })

    with patch.object(cfg, "MASTER_LIBRARIAN_AUTO_STUBS", True):
        res = link_librarian.create_ghost_link_stub(
            target_name="Concept X",
            source_path="Stubs/StubNote.md",
            context_excerpt="A stub quoting [[Concept X]].",
            vault_root=root,
            min_refs=2,
            min_context_chars=50,
            min_snippet_chars=10,
        )

    assert res["status"] == "created_stub"
    # Ensure Stubs/StubNote.md was NOT included in the created stub's context
    stub_file = tmp_path / "Stubs" / "Concept X.md"
    assert stub_file.exists()
    content = stub_file.read_text(encoding="utf-8")
    assert "StubNote" not in content
    assert "Notes/One.md" in content or "One" in content
    assert "Notes/Two.md" in content or "Two" in content


# --------------------------------------------------------------------------------------
# master_librarian audit bypass for stub documents
# --------------------------------------------------------------------------------------


def test_master_librarian_bypasses_ghost_stub_creation_for_stubs(tmp_path):
    """Auditing a stub note must never initiate ghost link stub synthesis."""
    root = _vault(tmp_path, {
        "Stubs/SourceStub.md": (
            "---\ntitle: SourceStub\ntags: []\ntype: [stub]\n---\n\n"
            "# 🏛️ SourceStub\n\n> [!ABSTRACT]\n> An entity.\n\n"
            "## 🧭 Context & Mentions\n- Quoting [[TargetGhostLink]] here.\n"
        ),
    })

    res = master_librarian.audit_single_document(
        doc_path="Stubs/SourceStub.md",
        vault_root=root,
        dry_run=False,
    )

    # Must not create or propose stubs from within a stub document
    assert "synthesized_stubs" not in " ".join(res.get("actions", []))
    assert "proposed_stubs" not in " ".join(res.get("actions", []))
