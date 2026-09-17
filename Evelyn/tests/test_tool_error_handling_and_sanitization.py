# test_tool_error_handling_and_sanitization.py
# date created: 2026-09-17 18:15:00
# date modified: 2026-09-17 18:13:55
# tags: #[test, #tool-errors, #sanitization, #honesty, #core-directives]

"""Unit tests for tool error interception, input sanitization, and Core_Directives isolation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import evelyn_config as cfg
from Evelyn.tools.profile_evolver import CANONICAL_DOCUMENT_SECTIONS, DOCUMENT_CATEGORIES
from Evelyn.tools.string_utils import sanitize_tool_input_text
from evelyn_server import load_system_prompt


class TestToolInputSanitization:
    """Validate string sanitization against hallucinated code and malformed inputs."""

    def test_sanitize_html_and_runaway_code_block(self):
        malformed_title = (
            "Bring home coffee grounds</div></th></div></li>\n"
            "</body></html>\n"
            "<script src=\"https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js\"></script>\n"
            "```html\n<!DOCTYPE html>\n```"
        )
        cleaned = sanitize_tool_input_text(malformed_title, max_length=150, single_line=True)
        assert cleaned == "Bring home coffee grounds"
        assert "<" not in cleaned
        assert ">" not in cleaned
        assert "\n" not in cleaned

    def test_sanitize_markdown_fences_and_extra_whitespace(self):
        text = "   ```python\n   Fix the broken thermostat wiring   \n```   "
        cleaned = sanitize_tool_input_text(text, max_length=150, single_line=True)
        assert cleaned == "Fix the broken thermostat wiring"

    def test_sanitize_length_capping(self):
        long_title = "A" * 300
        cleaned = sanitize_tool_input_text(long_title, max_length=120, single_line=True)
        assert len(cleaned) == 120

    def test_sanitize_multiline_notes(self):
        notes = "<b>Line 1:</b> Important detail\n\n```json\n{\"test\": 1}\n```\nLine 2: Next detail."
        cleaned = sanitize_tool_input_text(notes, max_length=1000, single_line=False)
        assert "<b>" not in cleaned
        assert "```" not in cleaned
        assert "Line 1: Important detail" in cleaned
        assert "Line 2: Next detail." in cleaned

    def test_sanitize_empty_or_none(self):
        assert sanitize_tool_input_text(None) == ""
        assert sanitize_tool_input_text("") == ""
        assert sanitize_tool_input_text("   ") == ""


class TestServiceInputSanitizationWiring:
    """Verify tool wrappers sanitize inbound arguments before building payloads."""

    @patch("Evelyn.tools.gtasks_sync.get_gtasks_service")
    def test_gtasks_create_gtask_sanitizes_title(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_insert = MagicMock()
        mock_service.tasks().insert.return_value = mock_insert
        mock_insert.execute.return_value = {"id": "gtask_123"}

        from Evelyn.tools.gtasks_sync import create_gtask

        dirty_title = "Bring home coffee grounds</div></th>\nNext line"
        res = create_gtask(title=dirty_title, due="2026-09-18 15:00:00")
        assert res.get("status") == "success"

        call_args = mock_service.tasks().insert.call_args
        assert call_args is not None
        body = call_args[1]["body"]
        assert body["title"] == "Bring home coffee grounds"
        assert "\n" not in body["title"]
        assert "<" not in body["title"]

    @patch("Evelyn.tools.gcal_sync.get_gcal_service")
    def test_gcal_create_gcal_event_sanitizes_summary(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_insert = MagicMock()
        mock_service.events().insert.return_value = mock_insert
        mock_insert.execute.return_value = {"id": "event_123", "summary": "Clean Event"}

        from Evelyn.tools.gcal_sync import create_gcal_event

        dirty_summary = "Water Heater Inspection <script>alert(1)</script>\nSecond line"
        res = create_gcal_event(summary=dirty_summary, start_at="2026-09-18 10:00:00")
        assert res.get("status") == "success"

        call_args = mock_service.events().insert.call_args
        assert call_args is not None
        body = call_args[1]["body"]
        assert body["summary"] == "Water Heater Inspection alert(1)"
        assert "\n" not in body["summary"]


class TestCoreDirectivesAndEvolverIsolation:
    """Ensure Core_Directives.md is loaded in system prompt and strictly isolated from evolver."""

    def test_core_directives_loaded_in_system_prompt(self):
        prompt = load_system_prompt()
        assert "## Foundational Operational Honesty" in prompt
        assert "Truthful Sanctuary Principle" in prompt
        assert "Never claim, simulate, or pretend an operation" in prompt
        assert "Tool Execution Ground Truth" in prompt

    def test_core_directives_absent_from_profile_evolver(self):
        core_doc = getattr(cfg, "PERSONA_FILE_CORE_DIRECTIVES", "Core_Directives.md")
        assert core_doc not in DOCUMENT_CATEGORIES
        assert core_doc not in CANONICAL_DOCUMENT_SECTIONS

    def test_persona_files_order(self):
        persona_files = getattr(cfg, "PERSONA_FILES", [])
        assert len(persona_files) == 4
        assert persona_files[0] == "Core_Directives.md"
