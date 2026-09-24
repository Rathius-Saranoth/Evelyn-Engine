# test_context_extractor_taxonomy_rag.py
# date created: 2026-08-19
# date modified: 2026-09-24 18:14:27
# tags: #tests, #taxonomy, #rag, #extractor, #novelty

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import evelyn_config as cfg
from Evelyn.tools import fact_extractor, memory_db


class TestContextExtractorTaxonomyRAG(unittest.TestCase):
    """Test suite for Context Extractor & Reviewer Semantic Taxonomy & Vector RAG Alignment."""

    def test_retrieve_candidate_taxonomy_and_clusters_aligned(self):
        """Verify vector retrieval returns candidate tags and computes high alignment score."""
        mock_messages = [
            {"role": "user", "content": "I am working on setting up FastAPI endpoints for my Python service."},
            {"role": "assistant", "content": "FastAPI with Pydantic is a great choice."}
        ]

        mock_tag_results = [
            {"metadata": {"tag": "fastapi", "category": "ai-engineering", "description": "Python web framework"}, "distance": 0.25},
            {"metadata": {"tag": "backend", "category": "ai-engineering", "description": "Backend API development"}, "distance": 0.35},
        ]
        mock_mem_results = [
            {"document": "Prefers writing async backend services in Python FastAPI.", "distance": 0.28, "metadata": {"source": "memory"}}
        ]

        def mock_query_col(query, col_name, n_results=10):
            if "taxonomy" in col_name or "tag" in col_name:
                return mock_tag_results
            return mock_mem_results

        with patch("Evelyn.tools.chroma_rag.query_collection", side_effect=mock_query_col):
            tags, _facts, min_dist, guidance = fact_extractor.retrieve_candidate_taxonomy_and_clusters(mock_messages)

            self.assertTrue(len(tags) > 0)
            self.assertEqual(tags[0]["tag"], "fastapi")
            self.assertAlmostEqual(min_dist, 0.25, places=2)
            # One directive, whatever the distance. It used to branch three ways on `min_dist`,
            # which v000.006.224 measured at roughly chance for the question it was asked.
            self.assertIn("Prefer one of them over a word of your own", guidance)
            self.assertNotIn("MATCH CONFIDENCE", guidance)
            self.assertNotIn("hierarch", guidance.lower())

    def test_a_distant_conversation_gets_the_same_directive(self):
        """A far nearest-match is not evidence of novelty, so it may not change the instruction.

        This case previously asserted the opposite: that a distance of 0.78 made the prompt
        say "EXPLICITLY ENCOURAGED to mint new domain-level tag hierarchies". The test was
        pinning the retired model in place while `test_prompts_teach_the_standard.py` swept
        for it three files away.
        """
        mock_messages = [
            {"role": "user", "content": "Exploring quantum topological braiding in Majorana fermions."}
        ]

        mock_tag_results = [
            {"metadata": {"tag": "software", "category": "ai-engineering", "description": "General software"}, "distance": 0.78},
        ]

        def mock_query_col(query, col_name, n_results=10):
            if "taxonomy" in col_name or "tag" in col_name:
                return mock_tag_results
            return []

        with patch("Evelyn.tools.chroma_rag.query_collection", side_effect=mock_query_col):
            _tags, _facts, min_dist, guidance = fact_extractor.retrieve_candidate_taxonomy_and_clusters(mock_messages)

            self.assertAlmostEqual(min_dist, 0.78, places=2)
            self.assertIn("Prefer one of them over a word of your own", guidance)
            self.assertNotIn("mint", guidance.lower())
            self.assertNotIn("hierarch", guidance.lower())

    def test_build_extraction_prompt_structure(self):
        """Verify extraction prompt contains category reference, tag taxonomy, memory clusters, and substance rules."""
        mock_messages = [{"role": "user", "content": "I like dark roast coffee."}]
        taxonomy_candidates = [
            {"tag": "coffee", "description": "Coffee preparation", "distance": 0.2}
        ]
        memory_candidates = [
            {"content": "Enjoys morning pour-over coffee.", "distance": 0.3}
        ]
        guidance = "TAG VOCABULARY: prefer a listed term."

        prompt = fact_extractor._build_extraction_prompt(
            messages=mock_messages,
            cat00=f"### Cat05-{cfg.SUBJECT_CODE_USER}: Lifestyle & Preferences",
            taxonomy_candidates=taxonomy_candidates,
            memory_candidates=memory_candidates,
            novelty_guidance=guidance
        )

        self.assertIn("REGISTERED VOCABULARY NEAREST THIS CONVERSATION", prompt)
        # Bare, as the `tags` field stores them: a leading `#` is Obsidian note syntax and
        # taught a surface form no memory fact uses.
        self.assertIn("- coffee", prompt)
        self.assertNotIn("#coffee", prompt)
        self.assertIn("RELEVANT EXISTING KNOWLEDGE CLUSTERS", prompt)
        self.assertIn("Enjoys morning pour-over coffee.", prompt)
        self.assertIn("CRITICAL SUBSTANCE & OBSERVATION RULES", prompt)
        self.assertIn("WRITE DEEP, SUBSTANTIVE OBSERVATIONS", prompt)
        # Renamed in v000.006.204: the rule now teaches flat controlled-vocabulary terms
        # instead of the TitleCase hierarchy that §5 abolished.
        self.assertIn("CONTROLLED VOCABULARY TAGS", prompt)
        self.assertNotIn("TitleCase with underscores", prompt)

    def test_parse_facts_yaml_with_hierarchical_tags(self):
        """Verify YAML facts block parsing normalizes multi-tier domain tags and TitleCase entities."""
        raw_yaml = f"""
```facts
facts:
  - subject: {cfg.USER_NAME}
    category: Cat05-{cfg.SUBJECT_CODE_USER}
    tags: "tech/python/fastapi, Test_Operator, 3d-printing/slicing"
    summary: "Configured multi-tier domain taxonomies for memory extraction."
    confidence: high
    date: "2026-08-19"
```
"""
        parsed = fact_extractor._parse_facts_yaml(raw_yaml, fallback_date="2026-08-19")
        self.assertEqual(len(parsed), 1)
        fact = parsed[0]
        self.assertEqual(fact["subject"], cfg.USER_NAME)
        self.assertEqual(fact["category"], f"Cat05-{cfg.SUBJECT_CODE_USER}")
        # Verify normalization. `Test_Operator` becomes `test-operator`: §5 gives proper
        # nouns the same rule as concepts — no underscores, no TitleCase — which is what
        # removes any way for one term to fork into two. This assertion previously expected
        # the raw form and contradicted test_tag_format_standard.py::test_no_entity_branch_survives.
        self.assertEqual(fact["tags"], "tech/python/fastapi, test-operator, 3d-printing/slicing")
        self.assertEqual(fact["confidence"], "high")

    def test_parse_facts_yaml_unclosed_fence(self):
        """Verify YAML facts block parsing works when stop sequence cuts closing fence."""
        raw_yaml = f"""```facts
facts:
  - subject: {cfg.USER_NAME}
    category: Cat05-{cfg.SUBJECT_CODE_USER}
    tags: "Tech/Python/FastAPI"
    summary: "Built an API service."
    confidence: high
    date: "2026-08-29"
"""
        parsed = fact_extractor._parse_facts_yaml(raw_yaml, fallback_date="2026-08-29")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["summary"], "Built an API service.")
        self.assertEqual(parsed[0]["category"], f"Cat05-{cfg.SUBJECT_CODE_USER}")



    # `_enrich_extraction_with_taxonomy` moved to Evelyn/tests/test_review_card_novelty.py
    # in v000.006.224, when it stopped being a distance band and became a registry lookup.

    def test_memory_db_vad_column_migration(self):
        """Verify memory_db.init_db includes the optional vad column migration."""
        memory_db.init_db()
        con = memory_db.get_db()
        cols = [r[1] for r in con.execute("PRAGMA table_info(context_entries)").fetchall()]
        con.close()
        self.assertIn("vad", cols)


if __name__ == "__main__":
    unittest.main()
