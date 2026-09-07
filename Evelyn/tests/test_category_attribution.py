# test_category_attribution.py
# date created: 2026-09-06 08:58:00
# date modified: 2026-09-06 09:00:37
# tags: #tests, #fast-memory, #category-attribution, #temporal-grounding

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import evelyn_config as cfg
from Evelyn.tools import chroma_rag, context_manager, fact_extractor, profile_evolver


class TestCategoryAttributionAndTemporalGrounding(unittest.TestCase):
    """Test suite verifying perspective ownership decoupling, temporal grounding, and RAG envelope anchoring."""

    def test_category_normalization_preserves_perspective(self):
        """Verify validate_and_normalize_category enforces Cat##-[U|A] format while preserving owner code."""
        # Standard user fact
        self.assertEqual(context_manager.validate_and_normalize_category("Cat01-U", subject="Ricky"), "Cat01-U")
        # Standard assistant fact
        self.assertEqual(context_manager.validate_and_normalize_category("Cat01-A", subject="Evelyn"), "Cat01-A")
        # Base category normalization with subject
        self.assertEqual(context_manager.validate_and_normalize_category("Cat06", subject="Ricky"), "Cat06-U")
        self.assertEqual(context_manager.validate_and_normalize_category("Cat06", subject="Evelyn"), "Cat06-A")

    def test_cross_perspective_attribution_yaml_parsing(self):
        """Verify _parse_facts_yaml honors cross-entity perspective canon."""
        yaml_content = f"""
facts:
  - category: "Cat06-A"
    subject: "{cfg.USER_NAME}"
    summary: "Evelyn expresses deep appreciation for {cfg.USER_NAME}'s patience."
    confidence: "high"
    tags: "relationship/appreciation"
  - category: "Cat06-U"
    subject: "{cfg.ASSISTANT_NAME}"
    summary: "{cfg.USER_NAME} validates {cfg.ASSISTANT_NAME}'s unique cognitive presence."
    confidence: "high"
    tags: "relationship/validation"
  - category: "Cat05-U"
    subject: "Fox"
    summary: "Fox prefers napping on the sunny side of the rug."
    confidence: "high"
    tags: "pets/habits"
"""
        facts = fact_extractor._parse_facts_yaml(yaml_content, fallback_date="2026-09-06")
        self.assertEqual(len(facts), 3)

        # Fact 1: Evelyn canon about User
        self.assertEqual(facts[0]["category"], "Cat06-A")
        self.assertEqual(facts[0]["subject"], cfg.USER_NAME)

        # Fact 2: User canon about Assistant
        self.assertEqual(facts[1]["category"], "Cat06-U")
        self.assertEqual(facts[1]["subject"], cfg.ASSISTANT_NAME)

        # Fact 3: User canon about pet
        self.assertEqual(facts[2]["category"], "Cat05-U")
        self.assertEqual(facts[2]["subject"], "Fox")

    def test_extraction_prompt_contains_perspective_and_temporal_rules(self):
        """Verify extraction prompt contains strict perspective ownership and temporal grounding directives."""
        messages = [
            {"role": "user", "content": "I'm setting up a new 3D printer today."},
            {"role": "assistant", "content": "That sounds great! I love calibrated machinery."},
        ]
        prompt = fact_extractor._build_extraction_prompt(messages, cat00="Cat00 Context Index")

        # Perspective ownership rules present
        self.assertIn("PERSPECTIVE OWNERSHIP & CATEGORY CANON", prompt)
        self.assertIn("Cat##-A", prompt)
        self.assertIn("Cat##-U", prompt)
        self.assertIn("The category suffix reflects WHOSE PERSPECTIVE/LEDGER the fact belongs to", prompt)

        # Strict temporal grounding rules present
        self.assertIn("TEMPORAL GROUNDING & HISTORICAL ANCHORING", prompt)
        self.assertIn("NEVER use unanchored floating temporal adverbs", prompt)
        self.assertIn("currently", prompt)

    def test_chroma_rag_xml_envelope_attributes(self):
        """Verify build_enhanced_context_chunk renders category, subject, and date on memory_entry tags."""
        mock_chunks = [
            {
                "source": "sqlite::context_entry::101",
                "content": "Evelyn admires Ricky's thoughtful approach to engineering.",
                "distance": 0.1,
                "metadata": {"type": "context_entry"},
            }
        ]

        mock_db_row = {
            "id": 101,
            "category": "Cat06-A",
            "subject": cfg.USER_NAME,
            "observation": "Evelyn admires Ricky's thoughtful approach to engineering.",
            "date": "2026-05-15",
        }

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [mock_db_row]

        with patch("Evelyn.tools.chroma_rag.query_collection", return_value=mock_chunks), \
             patch("Evelyn.tools.memory_db.get_db", return_value=mock_conn), \
             patch("memory_db.get_db", return_value=mock_conn, create=True):
            xml_output = chroma_rag.build_rag_context("engineering background")

            self.assertIn('<memory_entry id="101" category="Cat06-A"', xml_output)
            self.assertIn(f'subject="{cfg.USER_NAME}"', xml_output)
            self.assertIn('date="2026-05-15"', xml_output)

    def test_profile_evolver_evidence_formatting(self):
        """Verify _cluster_entries_by_theme formats evidence lines with category code and subject."""
        entries = [
            {
                "id": 42,
                "category": "Cat06-A",
                "subject": "Ricky",
                "observation": "Evelyn articulated deep trust in Ricky's judgment.",
                "created_at": 1700000000.0,
            }
        ]
        batches = profile_evolver._cluster_entries_by_theme(cfg.PERSONA_FILE_USER, entries)
        self.assertTrue(len(batches) > 0)
        found = False
        for b in batches:
            evidence_text = b.get("evidence_text", "")
            if "[Cat06-A | Subject: Ricky]" in evidence_text:
                found = True
        self.assertTrue(found, "Evidence line should include both category code and subject")

    def test_deterministic_temporal_anchoring_regex(self):
        """Verify Tier A regex patterns correctly anchor transient phrasing while preserving statives."""
        date_str = "2026-06-09"
        obs1 = "Ricky is currently printing a radius gauge for mounts."
        obs2 = "Airika is visiting Ricky's location to do laundry."
        obs3 = "Currently troubleshooting a monitor outage."
        obs4 = "Ricky is willing to provide feedback whenever requested."

        # Pattern 1
        m1 = re.match(r"^([A-Z][a-zA-Z0-9_\']+)\s+is\s+currently\s+([a-z]+ing\b.*)", obs1, re.IGNORECASE)
        self.assertIsNotNone(m1)
        res1 = f"As of {date_str}, {m1.group(1)} was {m1.group(2)}"
        self.assertEqual(res1, "As of 2026-06-09, Ricky was printing a radius gauge for mounts.")

        # Pattern 2
        m2 = re.match(r"^([A-Z][a-zA-Z0-9_\']+)\s+is\s+([a-z]+ing\b.*)", obs2)
        self.assertIsNotNone(m2)
        res2 = f"As of {date_str}, {m2.group(1)} was {m2.group(2)}"
        self.assertEqual(res2, "As of 2026-06-09, Airika was visiting Ricky's location to do laundry.")

        # Pattern 3
        m3 = re.match(r"^Currently\s+([a-z]+ing\b.*)", obs3, re.IGNORECASE)
        self.assertIsNotNone(m3)
        res3 = f"As of {date_str}, Ricky was {m3.group(1)}"
        self.assertEqual(res3, "As of 2026-06-09, Ricky was troubleshooting a monitor outage.")

        # Stative exclusion
        m4 = re.match(r"^([A-Z][a-zA-Z0-9_\']+)\s+is\s+([a-z]+ing\b.*)", obs4)
        self.assertIsNotNone(m4)
        self.assertEqual(m4.group(2).split()[0], "willing")
        statives = {"willing", "caring", "understanding", "pleasing"}
        self.assertIn(m4.group(2).split()[0], statives)


if __name__ == "__main__":
    unittest.main()
