# test_context_extractor_taxonomy_rag.py
# date created: 2026-08-19
# tags: #tests, #taxonomy, #rag, #extractor, #novelty

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import evelyn_config as cfg
import evelyn_server
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
            {"metadata": {"tag": "Tech/Python/FastAPI", "category": "Tech", "description": "Python web framework"}, "distance": 0.25},
            {"metadata": {"tag": "Tech/Backend/API", "category": "Tech", "description": "Backend API development"}, "distance": 0.35},
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
            self.assertEqual(tags[0]["tag"], "Tech/Python/FastAPI")
            self.assertAlmostEqual(min_dist, 0.25, places=2)
            self.assertIn("TAXONOMY MATCH CONFIDENCE: HIGH", guidance)
            self.assertIn("Strictly adhere to existing parent domain", guidance)

    def test_retrieve_candidate_taxonomy_and_clusters_novel_domain(self):
        """Verify distant/unmatched topics result in Novel Domain directive."""
        mock_messages = [
            {"role": "user", "content": "Exploring quantum topological braiding in Majorana fermions."}
        ]

        mock_tag_results = [
            {"metadata": {"tag": "Tech/Software", "category": "Tech", "description": "General software"}, "distance": 0.78},
        ]

        def mock_query_col(query, col_name, n_results=10):
            if "taxonomy" in col_name or "tag" in col_name:
                return mock_tag_results
            return []

        with patch("Evelyn.tools.chroma_rag.query_collection", side_effect=mock_query_col):
            _tags, _facts, min_dist, guidance = fact_extractor.retrieve_candidate_taxonomy_and_clusters(mock_messages)

            self.assertAlmostEqual(min_dist, 0.78, places=2)
            self.assertIn("TAXONOMY MATCH CONFIDENCE: LOW / NOVEL DOMAIN", guidance)
            self.assertIn("mint new domain-level tag hierarchies", guidance)

    def test_build_extraction_prompt_structure(self):
        """Verify extraction prompt contains category reference, tag taxonomy, memory clusters, and substance rules."""
        mock_messages = [{"role": "user", "content": "I like dark roast coffee."}]
        taxonomy_candidates = [
            {"tag": "Home/Coffee/Espresso", "description": "Coffee preparation", "distance": 0.2}
        ]
        memory_candidates = [
            {"content": "Enjoys morning pour-over coffee.", "distance": 0.3}
        ]
        guidance = "TAXONOMY MATCH CONFIDENCE: HIGH"

        prompt = fact_extractor._build_extraction_prompt(
            messages=mock_messages,
            cat00=f"### Cat05-{cfg.SUBJECT_CODE_USER}: Lifestyle & Preferences",
            taxonomy_candidates=taxonomy_candidates,
            memory_candidates=memory_candidates,
            novelty_guidance=guidance
        )

        self.assertIn("RELEVANT MASTER TAXONOMY DOMAINS & TAGS", prompt)
        self.assertIn("#Home/Coffee/Espresso", prompt)
        self.assertIn("RELEVANT EXISTING KNOWLEDGE CLUSTERS", prompt)
        self.assertIn("Enjoys morning pour-over coffee.", prompt)
        self.assertIn("CRITICAL SUBSTANCE & OBSERVATION RULES", prompt)
        self.assertIn("WRITE DEEP, SUBSTANTIVE OBSERVATIONS", prompt)
        self.assertIn("MULTI-TIER DOMAIN TAXONOMY", prompt)

    def test_parse_facts_yaml_with_hierarchical_tags(self):
        """Verify YAML facts block parsing normalizes multi-tier domain tags and TitleCase entities."""
        raw_yaml = f"""
```facts
facts:
  - subject: {cfg.USER_NAME}
    category: Cat05-{cfg.SUBJECT_CODE_USER}
    tags: "tech/python/fastapi, Ricky_Sekulich, 3d-printing/slicing"
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
        # Verify normalization
        self.assertEqual(fact["tags"], "tech/python/fastapi, Ricky_Sekulich, 3d-printing/slicing")
        self.assertEqual(fact["confidence"], "high")



    def test_server_enrich_extraction_with_taxonomy(self):
        """Verify evelyn_server._enrich_extraction_with_taxonomy adds suggestions and novelty score."""
        mock_item = {
            "id": 101,
            "category": f"Cat05-{cfg.SUBJECT_CODE_USER}",
            "observation": "Enjoys reading Dungeon Crawler Carl audiobook series.",
            "subject": "Ricky"
        }

        mock_tag_results = [
            {"metadata": {"tag": "Lore/Dungeon_Crawler_Carl", "category": "Lore"}, "distance": 0.22},
            {"metadata": {"tag": "Entertainment/Audiobooks", "category": "Entertainment"}, "distance": 0.38}
        ]

        with patch("Evelyn.tools.chroma_rag.query_collection", return_value=mock_tag_results):
            enriched = evelyn_server._enrich_extraction_with_taxonomy(dict(mock_item))
            self.assertIn("suggested_tags", enriched)
            self.assertEqual(enriched["suggested_tags"][0], "Lore/Dungeon_Crawler_Carl")
            self.assertEqual(enriched["alignment_label"], "Aligned")
            self.assertAlmostEqual(enriched["novelty_score"], 0.22, places=2)

    def test_memory_db_vad_column_migration(self):
        """Verify memory_db.init_db includes the optional vad column migration."""
        memory_db.init_db()
        con = memory_db.get_db()
        cols = [r[1] for r in con.execute("PRAGMA table_info(context_entries)").fetchall()]
        con.close()
        self.assertIn("vad", cols)


if __name__ == "__main__":
    unittest.main()
