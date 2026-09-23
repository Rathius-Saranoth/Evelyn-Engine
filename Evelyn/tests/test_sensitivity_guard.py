# test_sensitivity_guard.py
# date created: 2026-09-23 18:00:00
# date modified: 2026-09-23 17:40:39
# tags: #test, #privacy, #rag, #sensitivity

"""The `sensitivity:` property governs retrieval and tool access (v000.006.211).

Nothing read the key before this release: 46 of the 49 notes marked private or secret were
present in the RAG index, including tax records, medical records and child-support filings.

The two levels have deliberately different scope. `private` leaves automatic retrieval but
stays readable when the user asks for it, because medical and financial context is useful on
request and inappropriate injected unbidden. `secret` — credentials, recovery codes — is
withheld from retrieval and tools alike.
"""

import pytest

import evelyn_config as cfg
from Evelyn.tools.chroma_rag import is_rag_excluded_source
from Evelyn.tools.frontmatter_utils import is_tool_denied, read_sensitivity
from Evelyn.tools.ingest_obsidian_knowledge import parse_rag_frontmatter


def _note(level):
    body = f"sensitivity: {level}\n" if level is not None else ""
    return f"---\ntitle: A Note\n{body}---\n\nSome content.\n"


class TestRagExclusion:
    @pytest.mark.parametrize("level", ["private", "secret"])
    def test_withheld_levels_are_excluded_from_indexing(self, level):
        assert parse_rag_frontmatter(_note(level))["rag_exclude"] is True

    @pytest.mark.parametrize("level", ["public", "internal", None])
    def test_other_levels_are_indexed(self, level):
        assert parse_rag_frontmatter(_note(level))["rag_exclude"] is False

    def test_the_level_is_recorded_on_the_chunk(self):
        """The retrieval guard reads chunk metadata, so a document indexed before the rule
        existed can be withheld without re-reading it from disk."""
        assert parse_rag_frontmatter(_note("private"))["sensitivity"] == "private"

    @pytest.mark.parametrize("level", ["private", "secret"])
    def test_retrieval_withholds_an_already_indexed_chunk(self, level):
        assert is_rag_excluded_source("Personal/Medical/x.md", {"sensitivity": level}) is True

    def test_retrieval_allows_an_unmarked_chunk(self):
        assert is_rag_excluded_source("Notes/x.md", {"sensitivity": ""}) is False

    def test_case_and_whitespace_do_not_defeat_the_check(self):
        assert parse_rag_frontmatter(_note("  Secret  "))["rag_exclude"] is True
        assert is_rag_excluded_source("x.md", {"sensitivity": " SECRET "}) is True


class TestToolAccess:
    def test_secret_is_denied_to_tools(self, tmp_path):
        p = tmp_path / "creds.md"
        p.write_text(_note("secret"), encoding="utf-8")

        assert read_sensitivity(str(p)) == "secret"
        assert is_tool_denied(str(p)) is True

    def test_private_stays_readable_by_tools(self, tmp_path):
        """The whole point of the two levels: private leaves RAG but is still discoverable."""
        p = tmp_path / "medical.md"
        p.write_text(_note("private"), encoding="utf-8")

        assert parse_rag_frontmatter(p.read_text())["rag_exclude"] is True
        assert is_tool_denied(str(p)) is False

    def test_an_unmarked_note_is_readable(self, tmp_path):
        p = tmp_path / "plain.md"
        p.write_text(_note(None), encoding="utf-8")

        assert is_tool_denied(str(p)) is False

    def test_a_non_markdown_file_is_not_treated_as_a_note(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text('{"sensitivity": "secret"}', encoding="utf-8")

        assert is_tool_denied(str(p)) is False

    def test_a_missing_file_denies_nothing_and_does_not_raise(self, tmp_path):
        assert is_tool_denied(str(tmp_path / "nope.md")) is False

    def test_the_denied_set_is_configurable(self, tmp_path, monkeypatch):
        p = tmp_path / "medical.md"
        p.write_text(_note("private"), encoding="utf-8")
        monkeypatch.setattr(cfg, "SENSITIVITY_TOOL_DENIED", {"private", "secret"})

        assert is_tool_denied(str(p)) is True


class TestDeadReferenceBranchRemoved:
    def test_reference_chapters_are_still_excluded(self):
        assert is_rag_excluded_source("Reference Library/x.md", {"type": "reference-chapter"}) is True

    def test_the_reference_library_tag_no_longer_decides_anything(self):
        """That branch was dead — the tag was on 0 of 2,838 library notes and is not a
        registered term, so nothing could ever set it."""
        assert is_rag_excluded_source("Notes/x.md", {"tags": "reference-library"}) is False


class TestWriteAccess:
    """A read guard alone would still let a tool destroy the content it refuses to show."""

    @staticmethod
    def _secret_note(tmp_path):
        p = tmp_path / "creds.md"
        p.write_text("---\ntitle: Creds\nsensitivity: secret\n---\n\nRECOVERY-CODE-1\n", encoding="utf-8")
        return p

    def test_overwriting_a_secret_note_is_refused(self, tmp_path, monkeypatch):
        from Evelyn.tools import terminal_agent

        p = self._secret_note(tmp_path)
        monkeypatch.setattr(terminal_agent, "is_path_allowed", lambda _p: True)

        res = terminal_agent.write_file(str(p), "clobbered")

        assert "sensitivity: secret" in res and "cannot" in res
        assert "RECOVERY-CODE-1" in p.read_text(), "the note must be untouched"

    def test_appending_to_a_secret_note_is_refused(self, tmp_path, monkeypatch):
        from Evelyn.tools import terminal_agent

        p = self._secret_note(tmp_path)
        monkeypatch.setattr(terminal_agent, "is_path_allowed", lambda _p: True)

        res = terminal_agent.write_file(str(p), "extra", mode="append")

        assert "cannot" in res
        assert "extra" not in p.read_text()

    def test_a_private_note_may_still_be_written(self, tmp_path, monkeypatch):
        """`private` withholds from retrieval, not from deliberate action."""
        from Evelyn.tools import terminal_agent

        p = tmp_path / "medical.md"
        p.write_text("---\ntitle: M\nsensitivity: private\n---\n\nbody\n", encoding="utf-8")
        monkeypatch.setattr(terminal_agent, "is_path_allowed", lambda _p: True)

        res = terminal_agent.write_file(str(p), "new body")

        assert "sensitivity: secret" not in res

    def test_reading_a_secret_note_is_refused(self, tmp_path, monkeypatch):
        from Evelyn.tools import terminal_agent

        p = self._secret_note(tmp_path)
        monkeypatch.setattr(terminal_agent, "is_path_allowed", lambda _p: True)

        res = terminal_agent.read_file(str(p))

        assert "cannot" in res
        assert "RECOVERY-CODE-1" not in res
