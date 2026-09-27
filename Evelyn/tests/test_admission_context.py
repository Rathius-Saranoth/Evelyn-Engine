# test_admission_context.py
# date created: 2026-09-26
# date modified: 2026-09-26 18:07:00
# tags: #taxonomy, #review, #context, #testing

"""A reviewer needs the sentence, not the origin label.

A term reached the queue labelled `split fact (Cat05-U)` and nothing else. That label names a
category, not a sentence. The same word can be a clothing retailer in one corpus, an orchid
genus in a published authority, and a novel everywhere else; only the line it was extracted
from says which, and no amount of measurement gets there.

The fixtures below are invented notes, not real ones - section 4 keeps the operator's own
text out of tracked files, which is also why the real example is described rather than quoted.
"""


import pytest

import evelyn_config as cfg
from Evelyn.tools import tag_librarian


@pytest.fixture
def note(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cfg, "VAULT_BASE_DIR", str(tmp_path))
    tag_librarian._EXCERPT_CACHE.clear()

    def _write(rel: str, body: str) -> str:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return rel

    return _write


def test_it_returns_the_line_that_mentions_the_term(note) -> None:
    rel = note("Notes/shop.md", (
        "---\ntags: [clothing]\n---\n\n"
        "# Shopping\n\n"
        "I have been looking at boots for the festival this autumn.\n"
        "Both Dracula Clothing and another shop have limited options in this size.\n"
    ))

    excerpt = tag_librarian.excerpt_for_term("dracula", rel)

    assert "Dracula Clothing" in excerpt
    assert "boots for the festival" not in excerpt


def test_it_matches_prose_rather_than_the_normalized_form(note) -> None:
    """`color-code-theory` never appears as a string; its words do."""
    rel = note("Notes/assessment.md",
               "The Color Code Personality Assessment uses motive as its central idea.\n")

    excerpt = tag_librarian.excerpt_for_term("color-code-theory", rel)

    assert "Color Code Personality Assessment" in excerpt


def test_it_skips_the_note_furniture(note) -> None:
    """A reviewer shown an `[!abstract]` banner learns nothing the card did not already say."""
    rel = note("Notes/furnished.md", (
        "> [!abstract] [[Some Index|A long callout banner that says nothing useful at all]]\n"
        "| a table row | that is long enough to pass the length filter here |\n"
        "## A heading that is long enough to pass the length filter\n"
        "The actual prose about braiding rope for the rigging project.\n"
    ))

    excerpt = tag_librarian.excerpt_for_term("braiding", rel)

    assert "braiding rope" in excerpt
    assert "abstract" not in excerpt
    assert "table row" not in excerpt


def test_a_path_escaping_the_vault_is_refused(note, tmp_path) -> None:
    """Same guard as the backfill: review context must not become a file-read primitive."""
    outside = tmp_path.parent / "outside-secret.md"
    outside.write_text("A secret about dracula that is well outside the vault root.\n",
                       encoding="utf-8")

    assert tag_librarian.excerpt_for_term("dracula", f"../{outside.name}") == ""


def test_a_missing_note_is_empty_not_an_error(note) -> None:
    assert tag_librarian.excerpt_for_term("dracula", "Notes/gone.md") == ""


def test_the_excerpt_is_cached_on_mtime(note, monkeypatch: pytest.MonkeyPatch) -> None:
    """A queue of 200 must cost one pass over the notes, not one per render."""
    rel = note("Notes/cached.md", "A line about braiding that is long enough to be kept.\n")
    first = tag_librarian.excerpt_for_term("braiding", rel)

    reads = {"n": 0}
    real_open = open

    def counting_open(*args, **kwargs):
        reads["n"] += 1
        return real_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", counting_open)
    second = tag_librarian.excerpt_for_term("braiding", rel)

    assert second == first
    assert reads["n"] == 0, "The second read should have come from the cache"


def test_a_note_without_the_term_still_gives_something(note) -> None:
    """Some context beats none: the reviewer can still see what kind of note asked."""
    rel = note("Notes/unrelated.md",
               "This note is entirely about the migration of waterfowl in autumn.\n")

    excerpt = tag_librarian.excerpt_for_term("zzz-absent-term", rel)

    assert "waterfowl" in excerpt


def test_the_review_payload_carries_the_excerpt() -> None:
    """The helper has to be reached from the builder the review UI actually reads."""
    import ast
    import inspect

    import evelyn_server

    src = inspect.getsource(evelyn_server.get_unified_review)
    called = {
        n.func.id for n in ast.walk(ast.parse(src.lstrip()))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_attach_admission_context" in called, (
        "The unified queue does not attach admission context; the cards stay contextless"
    )


def test_an_attachment_wrapper_shows_what_it_is(note) -> None:
    """A note whose whole body is a heading and a PDF embed still has to say so.

    72 of 82 vault-sourced proposals produced nothing under the prose filters, because the
    notes are index stubs and attachment wrappers with no prose at all. Silence there is the
    worst answer: that the term was inferred from a table of chapter numbers rather than from
    any content is the decisive fact about it.
    """
    rel = note("Genealogy/Nobility.md", (
        "# The Nobility of Croatia and Slavonia\n\n"
        "## Document Viewer\n"
        "![[Attachments/Source Material/Nobility.pdf]]\n"
    ))

    excerpt = tag_librarian.excerpt_for_term("croatia", rel)

    assert excerpt, "An attachment wrapper must not render as an empty card"
    assert "Nobility of Croatia" in excerpt
    assert ".pdf" in excerpt, "Seeing that the content is in a PDF is the point"


def test_every_vault_sourced_proposal_can_produce_something(note) -> None:
    """The guarantee the card depends on: no admission card renders contextless."""
    for body in ("# Only a heading\n", "- a\n- b\n", "> [!note] callout only\n", "\n\n\n"):
        rel = note(f"N/{abs(hash(body))}.md", body)
        excerpt = tag_librarian.excerpt_for_term("anything", rel)
        assert excerpt or not body.strip(), f"No context at all from: {body!r}"


def test_an_admission_reports_a_note_it_could_not_update(monkeypatch: pytest.MonkeyPatch) -> None:
    """A decision can succeed and still leave something undone.

    A proposal stores the path its note had when it was raised, and a vault is a live
    filesystem. On 2026-09-26 seven admissions reached nothing because the notes were being
    refiled between the proposal and the decision — and every one reported `ok`. The term is
    genuinely admitted either way, so failing the approval would be wrong; saying nothing is
    what made it invisible.
    """
    import asyncio

    import evelyn_server
    from Evelyn.tools import memory_db, tag_librarian, taxonomy_db

    memory_db.init_db()
    taxonomy_db.init_db()
    monkeypatch.setattr(tag_librarian, "index_master_tag_in_chroma", lambda *a, **k: None)
    monkeypatch.setattr(evelyn_server, "start_refresh_memory_internal", lambda: None)

    pid = memory_db.insert_proposal(
        type="tag_admission", source_ids=[], topic="zzz-orphaned",
        reason="test", merged_observation="vault note", source_path="Stubs/Moved Away.md",
    )
    req = evelyn_server.ProposalActionRequest(modified_text="zzz-orphaned", category="general")

    result = asyncio.run(evelyn_server._apply_proposal_action(pid, "approve", req, refresh=False))

    assert "zzz-orphaned" in {t["tag"] for t in taxonomy_db.get_master_tags()}, "still admitted"
    assert result.get("warnings"), "the unreachable note must be reported, not swallowed"
    assert "Stubs/Moved Away.md" in result["warnings"][0]
