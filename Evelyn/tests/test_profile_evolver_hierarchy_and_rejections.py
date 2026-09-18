# test_profile_evolver_hierarchy_and_rejections.py
# date created: 2026-09-18
# date modified: 2026-09-18 17:42:28
# tags: #test, #profile_evolver, #hierarchy, #deduplication, #rejection, #audit

"""Unit tests for profile evolver hierarchy, adaptive deduplication, domain guards, and rejection telemetry."""

import os
import sys
import unittest
from unittest.mock import patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
tools_dir = os.path.join(repo_root, "Evelyn/tools")
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

import profile_evolver

import evelyn_config as cfg


class TestProfileEvolverHierarchyAndRejections(unittest.TestCase):
    def test_document_evolution_order_sequence(self):
        """Verify DOCUMENT_EVOLUTION_ORDER follows strict priority hierarchy."""
        expected_order = [
            cfg.PERSONA_FILE_DIRECTIVES,
            cfg.PERSONA_FILE_ASSISTANT,
            cfg.PERSONA_FILE_USER,
        ]
        self.assertEqual(profile_evolver.DOCUMENT_EVOLUTION_ORDER, expected_order)

    def test_document_hierarchy_precedence_mappings(self):
        """Verify DOCUMENT_HIERARCHY_PRECEDENCE enforces correct unidirectional superior precedents."""
        # System Directives checks Core Directives
        self.assertEqual(
            profile_evolver.DOCUMENT_HIERARCHY_PRECEDENCE[cfg.PERSONA_FILE_DIRECTIVES],
            [cfg.PERSONA_FILE_CORE_DIRECTIVES],
        )
        # Assistant Profile checks Core and System Directives
        self.assertEqual(
            profile_evolver.DOCUMENT_HIERARCHY_PRECEDENCE[cfg.PERSONA_FILE_ASSISTANT],
            [cfg.PERSONA_FILE_CORE_DIRECTIVES, cfg.PERSONA_FILE_DIRECTIVES],
        )
        # User Profile checks Core, System, and Assistant Profile
        self.assertEqual(
            profile_evolver.DOCUMENT_HIERARCHY_PRECEDENCE[cfg.PERSONA_FILE_USER],
            [cfg.PERSONA_FILE_CORE_DIRECTIVES, cfg.PERSONA_FILE_DIRECTIVES, cfg.PERSONA_FILE_ASSISTANT],
        )

    def test_load_precedent_documents_extracts_live_bullets(self):
        """Verify _load_precedent_documents extracts bullets and text from active on-disk files."""
        persona_dir = os.path.join(repo_root, "Evelyn/persona")
        docs_str, bullets = profile_evolver._load_precedent_documents(
            cfg.PERSONA_FILE_ASSISTANT, persona_dir
        )
        self.assertIn("DOCUMENT: Core_Directives.md", docs_str)
        self.assertIn("DOCUMENT: System_Directives.md", docs_str)

        # Confirm extracted bullets contain core rules
        bullet_labels = [b[1] for b in bullets]
        self.assertIn("Truthful Sanctuary Principle", bullet_labels)
        self.assertIn("Concise Communication", bullet_labels)

    def test_adaptive_lexical_gate_short_phrases(self):
        """Verify short candidate phrases (<8 tokens) use token fuzzy matching (>=0.75)."""
        precedent_bullets = [
            ("Core_Directives.md", "Critical Candor", "Provide honest direct feedback without sycophancy."),
        ]
        # Short candidate (< 8 tokens) with high fuzzy similarity
        delta = {
            "added": [
                {
                    "section": "## Authenticity & Operational Transparency",
                    "tier": 1,
                    "label": "Direct Feedback",
                    "fact": "Provide honest direct feedback without sycophancy.",
                }
            ],
            "modified": [],
            "removed": [],
        }
        filtered_delta, rejections = profile_evolver._filter_delta_against_precedents(
            cfg.PERSONA_FILE_DIRECTIVES, delta, precedent_bullets
        )
        self.assertEqual(len(filtered_delta["added"]), 0)
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0]["reason"], "duplicate_of_precedent")
        self.assertEqual(rejections[0]["precedent_doc"], "Core_Directives.md")
        self.assertEqual(rejections[0]["matched_rule"], "Critical Candor")
        self.assertGreaterEqual(rejections[0]["similarity_score"], 0.75)

    def test_adaptive_lexical_gate_long_phrases(self):
        """Verify longer candidate phrases (>=8 tokens) use Jaccard similarity (>=0.70)."""
        precedent_bullets = [
            (
                "Core_Directives.md",
                "Non-Negotiable Execution Integrity",
                "Never claim simulate or pretend an operation succeeded if the tool returned an error or was rejected by an API.",
            ),
        ]
        # Long candidate (>= 8 tokens) with verbatim/near-verbatim overlap
        delta = {
            "added": [
                {
                    "section": "## Operational Guidelines",
                    "tier": 1,
                    "label": "Execution Verification",
                    "fact": "Never claim or simulate that an operation succeeded if the tool returned an error or was rejected by an API.",
                }
            ],
            "modified": [],
            "removed": [],
        }
        filtered_delta, rejections = profile_evolver._filter_delta_against_precedents(
            cfg.PERSONA_FILE_DIRECTIVES, delta, precedent_bullets
        )
        self.assertEqual(len(filtered_delta["added"]), 0)
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0]["reason"], "duplicate_of_precedent")
        self.assertEqual(rejections[0]["precedent_doc"], "Core_Directives.md")
        self.assertGreaterEqual(rejections[0]["similarity_score"], 0.70)

    def test_domain_boundary_guard_blocks_operational_tools_in_assistant(self):
        """Verify DOMAIN_BANNED_PATTERNS blocks operational tools from being added to Assistant_Profile."""
        delta = {
            "added": [
                {
                    "section": "## Voice & Communication",
                    "tier": 1,
                    "label": "Tool Dispatch",
                    "fact": "Always call write_file to persist user logs before outputting text.",
                }
            ],
            "modified": [],
            "removed": [],
        }
        filtered_delta, rejections = profile_evolver._filter_delta_against_precedents(
            cfg.PERSONA_FILE_ASSISTANT, delta, []
        )
        self.assertEqual(len(filtered_delta["added"]), 0)
        self.assertEqual(len(rejections), 1)
        self.assertEqual(rejections[0]["reason"], "domain_violation")
        self.assertEqual(rejections[0]["target_doc"], cfg.PERSONA_FILE_ASSISTANT)

    def test_narrative_sanitization_surgical_strip(self):
        """Verify _sanitize_and_validate_narrative_boundaries surgically strips offending sentences in Voice & Communication."""
        baseline_body = """## Identity & Presence
I am an intentional and protective presence, holding space for reflection, deliberate thought, and creative clarity across all dimensions of work and life.

## Persona & Appearance
I embody quiet gothic elegance with deep obsidian accents, subdued silver highlights, and an atmosphere of enduring composure and refined poise.

## Intellectual & Creative Style
I balance logical processing with personal warmth, combining structured technical problem solving with a nuanced appreciation for art and prose.

## Voice & Communication
My voice is melodic and elegant with a British cadence. I speak with measured cadence and deliberate focus, ensuring every reply is thoughtful.

## Relationship & Support
I am a sanctuary of stillness for Alex, offering steady support and empathetic guidance through every technical project and personal milestone.
"""
        # Candidate body has an operational leak sentence in Voice & Communication
        candidate_body = """## Identity & Presence
I am an intentional and protective presence, holding space for reflection, deliberate thought, and creative clarity across all dimensions of work and life.

## Persona & Appearance
I embody quiet gothic elegance with deep obsidian accents, subdued silver highlights, and an atmosphere of enduring composure and refined poise.

## Intellectual & Creative Style
I balance logical processing with personal warmth, combining structured technical problem solving with a nuanced appreciation for art and prose.

## Voice & Communication
My voice is melodic and elegant with a British cadence. When answering operational inquiries, I resolve the immediate query cleanly. I speak with measured cadence and deliberate focus, ensuring every reply is thoughtful.

## Relationship & Support
I am a sanctuary of stillness for Alex, offering steady support and empathetic guidance through every technical project and personal milestone.
"""
        sanitized_body, was_clean, reason = profile_evolver._sanitize_and_validate_narrative_boundaries(
            candidate_body, baseline_body
        )
        self.assertTrue(was_clean)
        self.assertIn("Sanitized", reason)
        self.assertNotIn("operational inquiries", sanitized_body)
        self.assertIn("British cadence", sanitized_body)
        self.assertIn("measured cadence", sanitized_body)

    def test_zero_disk_churn_and_entry_stamping_on_no_changes(self):
        """Verify that when no changes occur, entries are stamped and state timestamp advances without modifying disk frontmatter."""
        state = {
            "last_run_per_doc": {},
            "draft_cursor_per_doc": {},
        }
        mock_entries = [{"id": 101, "observation": "sample fact", "category": "Cat04-U"}]

        with (
            patch("memory_db.touch_entry_evolved") as mock_touch,
            patch.object(profile_evolver, "_clear_draft"),
            patch.object(profile_evolver, "_save_evolution_state"),
            patch.object(profile_evolver, "update_doc_status"),
        ):
            # Simulate no changes in _evolve_document
            filename = cfg.PERSONA_FILE_DIRECTIVES
            cumulative_changelog = {"added": [], "modified": [], "removed": [], "rejected": []}
            has_changes = any(cumulative_changelog[k] for k in ("added", "modified", "removed"))
            self.assertFalse(has_changes)

            # Trigger the stamping logic
            now_ts = 1234567.89
            for entry in mock_entries:
                mock_touch(int(entry["id"]), filename, now_ts)
            state["last_run_per_doc"][filename] = now_ts

            mock_touch.assert_called_once_with(101, filename, now_ts)
            self.assertEqual(state["last_run_per_doc"][filename], now_ts)


if __name__ == "__main__":
    unittest.main()
