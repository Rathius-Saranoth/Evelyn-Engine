# test_category_attribution.py
# date created: 2026-09-06 08:58:00
# date modified: 2026-10-16 00:13:20
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
from Evelyn.tools import chroma_rag, fact_categorizer, fact_extractor, profile_evolver


class TestCategoryAttributionAndTemporalGrounding(unittest.TestCase):
    """Test suite verifying perspective ownership decoupling, temporal grounding, and RAG envelope anchoring."""

    def test_category_normalization_preserves_perspective(self):
        """Verify validate_and_normalize_category enforces Cat##-[U|A] format while preserving owner code."""
        # Standard user fact
        self.assertEqual(fact_categorizer.validate_and_normalize_category("Cat01-U", subject="Alex"), "Cat01-U")
        # Standard assistant fact
        self.assertEqual(fact_categorizer.validate_and_normalize_category("Cat01-A", subject="Evelyn"), "Cat01-A")
        # Base category normalization with subject
        self.assertEqual(fact_categorizer.validate_and_normalize_category("Cat06", subject="Alex"), "Cat06-U")
        self.assertEqual(fact_categorizer.validate_and_normalize_category("Cat06", subject="Evelyn"), "Cat06-A")

    def test_cross_perspective_attribution_yaml_parsing(self):
        """Verify _parse_facts_yaml honors cross-entity perspective canon."""
        yaml_content = f"""
facts:
  - subject: "{cfg.USER_NAME}"
    summary: "Evelyn expresses deep appreciation for {cfg.USER_NAME}'s patience."
    confidence: "high"
    tags: "relationship, appreciation"
  - subject: "{cfg.ASSISTANT_NAME}"
    summary: "{cfg.USER_NAME} validates {cfg.ASSISTANT_NAME}'s unique cognitive presence."
    confidence: "high"
    tags: "relationship, validation"
  - subject: "Biscuit"
    summary: "Biscuit prefers napping on the sunny side of the rug."
    confidence: "high"
    tags: "cat, habits"
"""
        facts = fact_extractor._parse_facts_yaml(yaml_content, fallback_date="2026-09-06")
        self.assertEqual(len(facts), 3)

        # Fact 1: Evelyn canon about User
        self.assertEqual(facts[0]["subject"], cfg.USER_NAME)
        self.assertEqual(facts[0]["confidence"], "high")

        # Fact 2: User canon about Assistant
        self.assertEqual(facts[1]["subject"], cfg.ASSISTANT_NAME)
        self.assertEqual(facts[1]["confidence"], "high")

        # Fact 3: User canon about pet
        self.assertEqual(facts[2]["subject"], "Biscuit")
        self.assertEqual(facts[2]["confidence"], "high")

    def test_extraction_prompt_contains_perspective_and_temporal_rules(self):
        """Verify extraction prompt contains strict entity attribution and temporal grounding directives."""
        messages = [
            {"role": "user", "content": "I'm setting up a new 3D printer today."},
            {"role": "assistant", "content": "That sounds great! I love calibrated machinery."},
        ]
        prompt = fact_extractor._build_extraction_prompt(messages)

        # Entity attribution rules present
        self.assertIn("ENTITY ATTRIBUTION & SUBJECT GROUNDING", prompt)
        self.assertIn(f"Facts about {cfg.USER_NAME} have subject: '{cfg.USER_NAME}'", prompt)
        self.assertIn(f"Facts about {cfg.ASSISTANT_NAME} have subject: '{cfg.ASSISTANT_NAME}'", prompt)

        # Strict temporal grounding rules present
        self.assertIn("TEMPORAL GROUNDING & HISTORICAL ANCHORING", prompt)
        self.assertIn("NEVER use unanchored floating temporal adverbs", prompt)
        self.assertIn("DO NOT restate the current conversation's timestamp inside the observation body", prompt)
        self.assertIn("DO PRESERVE specific calendar dates", prompt)

    def test_chroma_rag_xml_envelope_attributes(self):
        """Verify build_enhanced_context_chunk renders category, subject, and date on memory_entry tags."""
        mock_chunks = [
            {
                "source": "sqlite::context_entry::101",
                "content": "Evelyn admires Alex's thoughtful approach to engineering.",
                "distance": 0.1,
                "metadata": {"type": "context_entry"},
            }
        ]

        mock_db_row = {
            "id": 101,
            "category": "Cat06-A",
            "subject": cfg.USER_NAME,
            "observation": "Evelyn admires Alex's thoughtful approach to engineering.",
            "date": "2026-05-15",
            "tags": "engineering, software-development",
        }

        mock_conn = MagicMock()

        def mock_execute(sql, *args, **kwargs):
            cur = MagicMock()
            if "procedures" in sql:
                cur.fetchall.return_value = []
            else:
                cur.fetchall.return_value = [mock_db_row]
            return cur

        mock_conn.execute.side_effect = mock_execute

        with patch("Evelyn.tools.chroma_rag.query_collection", return_value=mock_chunks), \
             patch("Evelyn.tools.chroma_rag.log_rag_retrieval"), \
             patch("Evelyn.tools.memory_db.get_db", return_value=mock_conn), \
             patch("memory_db.get_db", return_value=mock_conn, create=True):
            xml_output = chroma_rag.build_rag_context("engineering background")

            self.assertIn('<memory_entry id="101"', xml_output)
            self.assertIn(f'subject="{cfg.USER_NAME}"', xml_output)
            self.assertIn('date="2026-05-15"', xml_output)
            self.assertIn('tags="engineering, software-development"', xml_output)

    def test_profile_evolver_evidence_formatting(self):
        """Verify _cluster_entries_by_theme formats evidence lines with subject."""
        entries = [
            {
                "id": 42,
                "subject": "Alex",
                "observation": "Evelyn articulated deep trust in Alex's judgment.",
                "created_at": 1700000000.0,
            }
        ]
        batches = profile_evolver._cluster_entries_by_theme(cfg.PERSONA_FILE_USER, entries)
        self.assertTrue(len(batches) > 0)
        found = False
        for b in batches:
            evidence_text = b.get("evidence_text", "")
            if "[Subject: Alex]" in evidence_text:
                found = True
        self.assertTrue(found, "Evidence line should include subject")

    def test_deterministic_temporal_anchoring_regex(self):
        """Verify Tier A regex patterns correctly anchor transient phrasing while preserving statives."""
        date_str = "2026-06-09"
        obs1 = "Alex is currently printing a radius gauge for mounts."
        obs2 = "Robin is visiting Alex's location to do laundry."
        obs3 = "Currently troubleshooting a monitor outage."
        obs4 = "Alex is willing to provide feedback whenever requested."

        # Pattern 1
        m1 = re.match(r"^([A-Z][a-zA-Z0-9_\']+)\s+is\s+currently\s+([a-z]+ing\b.*)", obs1, re.IGNORECASE)
        self.assertIsNotNone(m1)
        res1 = f"As of {date_str}, {m1.group(1)} was {m1.group(2)}"
        self.assertEqual(res1, "As of 2026-06-09, Alex was printing a radius gauge for mounts.")

        # Pattern 2
        m2 = re.match(r"^([A-Z][a-zA-Z0-9_\']+)\s+is\s+([a-z]+ing\b.*)", obs2)
        self.assertIsNotNone(m2)
        res2 = f"As of {date_str}, {m2.group(1)} was {m2.group(2)}"
        self.assertEqual(res2, "As of 2026-06-09, Robin was visiting Alex's location to do laundry.")

        # Pattern 3
        m3 = re.match(r"^Currently\s+([a-z]+ing\b.*)", obs3, re.IGNORECASE)
        self.assertIsNotNone(m3)
        res3 = f"As of {date_str}, Alex was {m3.group(1)}"
        self.assertEqual(res3, "As of 2026-06-09, Alex was troubleshooting a monitor outage.")

        # Stative exclusion
        m4 = re.match(r"^([A-Z][a-zA-Z0-9_\']+)\s+is\s+([a-z]+ing\b.*)", obs4)
        self.assertIsNotNone(m4)
        self.assertEqual(m4.group(2).split()[0], "willing")
        statives = {"willing", "caring", "understanding", "pleasing"}
        self.assertIn(m4.group(2).split()[0], statives)

    def test_normalize_subject_attribution_aliases_and_third_parties(self):
        """Verify memory_db.normalize_subject_attribution normalizes aliases and reconciles third parties."""
        from Evelyn.tools import memory_db

        with patch.object(cfg, "PRIVATE_IDENTITY_NAMES", ["Jordan", "Biscuit"]):
            # Operator alias normalization
            self.assertEqual(memory_db.normalize_subject_attribution(cfg.USER_NAME.lower()), cfg.USER_NAME)
            # Assistant casing
            self.assertEqual(memory_db.normalize_subject_attribution(cfg.ASSISTANT_NAME.lower()), cfg.ASSISTANT_NAME)
            # Third-party casing/alias
            self.assertEqual(memory_db.normalize_subject_attribution("biscuit the cat"), "Biscuit")
            self.assertEqual(memory_db.normalize_subject_attribution("jordan"), "Jordan")

            # Reconciliation from observation when subject defaulted to user
            obs_pet = "Biscuit prefers napping by the sunny window in the living room."
            self.assertEqual(memory_db.normalize_subject_attribution(cfg.USER_NAME, obs_pet), "Biscuit")

            obs_third = "Jordan is acclimating to online classes and personal routines."
            self.assertEqual(memory_db.normalize_subject_attribution(cfg.USER_NAME, obs_third), "Jordan")

            # Preserves user when user is the actor
            obs_user_actor = f"{cfg.USER_NAME} helped Jordan downstairs after school."
            self.assertEqual(memory_db.normalize_subject_attribution(cfg.USER_NAME, obs_user_actor), cfg.USER_NAME)

    def test_subject_attribution_for_third_party_entities(self):
        """Verify _parse_facts_yaml preserves explicit third-party subjects."""
        yaml_content = """
facts:
  - subject: "Jordan"
    summary: "Jordan is acclimating to having greater independence."
    confidence: "high"
    tags: "family, routine"
  - subject: "Biscuit"
    summary: "Biscuit the cat prefers sleeping on warm blankets."
    confidence: "high"
    tags: "cat, habits"
"""
        with patch.object(cfg, "PRIVATE_IDENTITY_NAMES", ["Jordan", "Biscuit"]):
            facts = fact_extractor._parse_facts_yaml(yaml_content, fallback_date="2026-09-06")
            self.assertEqual(len(facts), 2)
            self.assertEqual(facts[0]["subject"], "Jordan")
            self.assertEqual(facts[1]["subject"], "Biscuit")

    def test_find_similar_entries_scoped_by_subject(self):
        """Verify memory_db.find_similar_entries prevents cross-entity suppression when subject is supplied."""
        from Evelyn.tools import memory_db

        e1 = memory_db.insert_entry(
            category=f"Cat05-{cfg.SUBJECT_CODE_USER}",
            subject=cfg.USER_NAME,
            observation="Takes prescribed evening medication with a large glass of water.",
            status="live",
        )
        e2 = memory_db.insert_entry(
            category=f"Cat05-{cfg.SUBJECT_CODE_USER}",
            subject="Jordan",
            observation="Takes prescribed evening medication before bed.",
            status="live",
        )

        # Without subject filter, e1 matches e2
        all_matches = memory_db.find_similar_entries(
            category=f"Cat05-{cfg.SUBJECT_CODE_USER}",
            observation_text="Takes prescribed evening medication with water.",
            min_overlap=0.80,
            status="live",
        )
        matched_ids = [m["id"] for m in all_matches]
        self.assertIn(e1, matched_ids)
        self.assertIn(e2, matched_ids)

        # With subject filter for Jordan, e1 is NOT returned
        scoped_matches = memory_db.find_similar_entries(
            category=f"Cat05-{cfg.SUBJECT_CODE_USER}",
            subject="Jordan",
            observation_text="Takes prescribed evening medication with water.",
            min_overlap=0.80,
            status="live",
        )
        scoped_ids = [m["id"] for m in scoped_matches]
        self.assertIn(e2, scoped_ids)
        self.assertNotIn(e1, scoped_ids)


if __name__ == "__main__":
    unittest.main()
