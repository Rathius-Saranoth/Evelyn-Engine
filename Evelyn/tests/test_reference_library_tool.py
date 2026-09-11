# test_reference_library_tool.py
# date created: 2026-09-11 17:22:00
# tags: #tests, #reference-library, #chroma, #dual-collection

import unittest
from unittest.mock import patch

from Evelyn.tools.chroma_rag import is_rag_excluded_source
from Evelyn.tools.evelyn_tools import search_reference_library


class TestReferenceLibraryTool(unittest.TestCase):
    """Test suite for Reference Library separation, exclusion, and search tool."""

    def test_is_rag_excluded_source_checks(self):
        """Verify is_rag_excluded_source filters Reference Library paths and metadata."""
        # 1. By relative path
        self.assertTrue(is_rag_excluded_source("Reference Library/Learning Cello/Cello Method/01 - Lesson.md"))
        self.assertTrue(is_rag_excluded_source("/home/rathius/obsidian_vault/Reference Library/Owner's Manuals/Carrier.md"))

        # 2. By metadata type
        self.assertTrue(is_rag_excluded_source("custom_note.md", metadata={"type": "reference-chapter"}))

        # 3. By metadata tags
        self.assertTrue(is_rag_excluded_source("custom_note.md", metadata={"tags": ["reference-library", "tech"]}))
        self.assertTrue(is_rag_excluded_source("custom_note.md", metadata={"tags": "reference-library, tech"}))

        # 4. Standard personal notes should NOT be excluded
        self.assertFalse(is_rag_excluded_source("Notes/Projects/Idea.md", metadata={"type": "note"}))
        self.assertFalse(is_rag_excluded_source("Evelyn/Core Thoughts/Reflection.md"))

    def test_search_reference_library_empty_query(self):
        """Verify search_reference_library returns error for empty query."""
        res = search_reference_library("")
        self.assertIn("Error: search_reference_library called with an empty query.", res)
        res_spaces = search_reference_library("   ")
        self.assertIn("Error: search_reference_library called with an empty query.", res_spaces)

    @patch("chroma_rag.query_collection")
    def test_search_reference_library_success(self, mock_query):
        """Verify search_reference_library formats returned results cleanly."""
        mock_query.return_value = [
            {
                "content": "Turn gas valve to PILOT position and hold button.",
                "source": "/home/rathius/obsidian_vault/Reference Library/Owner's Manuals/Water Heater/01 - Lighting.md",
                "distance": 0.22,
                "metadata": {
                    "title": "Lighting Instructions",
                    "frontmatter_source": "Water Heater Manual",
                    "tags": ["reference-library", "manuals"],
                },
            }
        ]

        res = search_reference_library("water heater pilot light", limit=1)
        self.assertIn("### Reference Library Results for: 'water heater pilot light'", res)
        self.assertIn("Water Heater Manual — Lighting Instructions", res)
        self.assertIn("Turn gas valve to PILOT position", res)
        self.assertIn("Relevance: 78%", res)

    @patch("chroma_rag.query_collection")
    def test_search_reference_library_domain_filter(self, mock_query):
        """Verify domain filtering isolates matching topic."""
        mock_query.return_value = [
            {
                "content": "Cello bowing technique and bow hold.",
                "source": "/home/rathius/obsidian_vault/Reference Library/Learning Cello/Cello Method/01 - Bow.md",
                "distance": 0.20,
                "metadata": {
                    "title": "Bow Technique",
                    "tags": ["reference-library", "cello"],
                },
            },
            {
                "content": "Water heater troubleshooting steps.",
                "source": "/home/rathius/obsidian_vault/Reference Library/Owner's Manuals/Water Heater/02 - Steps.md",
                "distance": 0.25,
                "metadata": {
                    "title": "Water Heater",
                    "tags": ["reference-library", "manuals"],
                },
            },
        ]

        res_cello = search_reference_library("bow hold", limit=5, domain="cello")
        self.assertIn("Bow Technique", res_cello)
        self.assertNotIn("Water Heater", res_cello)


if __name__ == "__main__":
    unittest.main()
