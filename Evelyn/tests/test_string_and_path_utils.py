# test_string_and_path_utils.py
# date created: 2026-08-28 12:25:00
# date modified: 2026-08-28 12:25:00
# tags: #tests, #string_utils, #path_utils

import pytest

from Evelyn.tools.path_utils import (
    VAULT_ROOT,
    is_vault_excluded,
    normalize_vault_path,
    to_vault_abspath,
    to_vault_relpath,
)
from Evelyn.tools.string_utils import (
    clean_llm_gist,
    clean_title,
    sanitize_filename,
    slugify,
    strip_thinking_tags,
)


class TestStringUtils:
    def test_strip_thinking_tags(self):
        raw = "<think>Analyzing query...\nDone.</think>Here is the actual answer."
        assert strip_thinking_tags(raw) == "Here is the actual answer."

        unclosed = "<think>Started thinking but cut off"
        assert strip_thinking_tags(unclosed) == ""

        summary_prefix = "**Summary:** This is a gist."
        assert strip_thinking_tags(summary_prefix) == "This is a gist."

        boxed = r"\boxed{42} Result is 42."
        assert strip_thinking_tags(boxed) == "Result is 42."

    def test_clean_llm_gist(self):
        raw = '"<think>Thinking</think>Key takeaways from chapter."'
        assert clean_llm_gist(raw) == "Key takeaways from chapter."

    def test_sanitize_filename(self):
        assert sanitize_filename('My: Illegal/File? Name*') == "My Illegal File Name"
        assert sanitize_filename("   ...trailing.  ") == "trailing"
        assert sanitize_filename("", default="backup") == "backup"

    def test_slugify(self):
        assert slugify("Groceries & Daily Supplies") == "groceries_daily_supplies"
        assert slugify("Clean My Room!", delimiter="-") == "clean-my-room"

    def test_clean_title(self):
        assert clean_title("01_Introduction_To_AI.pdf") == "01 Introduction To AI"
        assert clean_title("SEC_10K_2026.md") == "SEC_10K_2026"
        assert clean_title("Simple Title.markdown") == "Simple Title"


class TestPathUtils:
    def test_to_vault_relpath(self):
        abs_p = VAULT_ROOT / "Personal" / "Notes" / "Daily.md"
        rel = to_vault_relpath(abs_p)
        assert rel == "Personal/Notes/Daily.md"
        assert "\\" not in rel

    def test_to_vault_abspath_safe(self):
        target = to_vault_abspath("Personal/Notes/Daily.md")
        assert target == (VAULT_ROOT / "Personal" / "Notes" / "Daily.md").resolve()

    def test_to_vault_abspath_traversal_guard(self):
        with pytest.raises(ValueError, match="Path traversal detected"):
            to_vault_abspath("../../etc/passwd")

    def test_normalize_vault_path(self):
        p = "Personal/Notes/Daily.md"
        assert normalize_vault_path(p) == "personal/notes/daily.md"

    def test_is_vault_excluded(self):
        assert is_vault_excluded(".obsidian/workspace.json") is True
        assert is_vault_excluded(".trash/old.md") is True
        assert is_vault_excluded("Attachments/Source Material/doc.pdf") is True
        assert is_vault_excluded("Notes/General.md") is False
