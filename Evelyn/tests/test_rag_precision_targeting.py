# test_rag_precision_targeting.py
# date created: 2026-09-01
# date modified: 2026-09-01 20:33:06
# tags: #test, #rag, #precision_targeting, #abstract, #frontmatter

"""Unit tests for Pre-Chunk Sanitization, Abstract Metadata Anchoring, and Precision RAG Context Assembly."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure repo root and Evelyn/tools are on python path
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
tools_dir = os.path.join(repo_root, "Evelyn/tools")
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

import evelyn_config as cfg
from Evelyn.tools.chroma_rag import (
    clean_rag_chunk_content,
    extract_abstract_callout,
    preprocess_markdown_for_indexing,
)


class TestRAGPrecisionTargeting(unittest.TestCase):
    def test_extract_abstract_callout_multiline(self):
        """Verify multiline [!ABSTRACT] callout is cleanly extracted."""
        markdown = """# Note Title
> [!ABSTRACT]
> This is a high-level summary of the note concepts.
> It spans across two lines of text.

## Section 1
Detailed content goes here.
"""
        abstract = extract_abstract_callout(markdown)
        expected = "This is a high-level summary of the note concepts.\nIt spans across two lines of text."
        self.assertEqual(abstract, expected)

    def test_extract_abstract_callout_single_line(self):
        """Verify single-line [!ABSTRACT] callout is extracted."""
        markdown = "> [!ABSTRACT] Compact single line abstract summary."
        abstract = extract_abstract_callout(markdown)
        self.assertEqual(abstract, "Compact single line abstract summary.")

    def test_preprocess_markdown_for_indexing_full(self):
        """Verify full preprocessing: frontmatter stripped, abstract extracted, navigation and footers removed."""
        markdown = """---
title: System Architecture
tags: [spec, engine, core]
rag_priority: high
---
> Navigation: [[Home]] · [[Index]]
> [!NAV] Breadcrumb trail

# System Architecture
> [!ABSTRACT]
> High level architectural overview for the engine components.

## 🪐 Core Engine
The core engine manages conversation state and vector retrieval.

| Component | Function |
|---|---|
| RAG | Vector Search |
| Tools | Agentic Actions |

## 🔗 Related Notes
- [[Memory System]]
- [[Database Migrations]]
- [[FastAPI Endpoints]]
"""
        clean_body, meta = preprocess_markdown_for_indexing(markdown)

        # 1. Frontmatter metadata parsed
        self.assertEqual(meta.get("title"), "System Architecture")
        self.assertEqual(meta.get("rag_priority"), "high")
        self.assertIn("spec", meta.get("tags", []))

        # 2. Abstract extracted into metadata
        self.assertEqual(
            meta.get("abstract"),
            "High level architectural overview for the engine components.",
        )

        # 3. YAML frontmatter stripped from body
        self.assertNotIn("title: System Architecture", clean_body)
        self.assertNotIn("rag_priority: high", clean_body)

        # 4. Navigation lines stripped
        self.assertNotIn("> Navigation:", clean_body)
        self.assertNotIn("[!NAV]", clean_body)

        # 5. Emoji footer and related notes links stripped
        self.assertNotIn("## 🔗 Related Notes", clean_body)
        self.assertNotIn("[[Memory System]]", clean_body)

        # 6. Core content and tables preserved
        self.assertIn("## 🪐 Core Engine", clean_body)
        self.assertIn("| RAG | Vector Search |", clean_body)

    def test_preprocess_markdown_emoji_footer_variants(self):
        """Verify regex resilience against various emoji and wording variants for link footers."""
        test_cases = [
            "## 📌 Related Notes\n- [[Link 1]]",
            "## 🧭 Navigation\n- [[Link 2]]",
            "## 📚 See Also\n- [[Link 3]]",
            "## Footnotes\n1. Reference link",
        ]
        for footer in test_cases:
            doc = f"# Sample Title\nContent text here.\n\n{footer}"
            clean_body, _ = preprocess_markdown_for_indexing(doc)
            self.assertIn("Content text here.", clean_body)
            self.assertNotIn(footer, clean_body)

    def test_clean_rag_chunk_content_safety_net(self):
        """Verify downstream safety net cleans severed or un-synced chunks."""
        raw_chunk = """---
tags: [old, chunk]
---
> Navigation: [[Old_Index]]
Some important text from the middle of the document.
## 🔗 Related Notes
- [[Dead Link]]"""
        cleaned = clean_rag_chunk_content(raw_chunk)
        self.assertEqual(cleaned, "Some important text from the middle of the document.")

    def test_build_rag_context_abstract_anchoring(self):
        """Verify build_rag_context prepends [!ABSTRACT] when retrieving mid-document chunks."""
        from Evelyn.tools.chroma_rag import build_rag_context

        mock_chunk = {
            "source": os.path.join(cfg.VAULT_BASE_DIR, "Engineering/Engine_Design.md"),
            "content": "## ⚡ Performance Tuning\nDirect vector search runs in under 100ms.",
            "distance": 0.25,
            "metadata": {
                "chunk": 2,
                "total_chunks": 4,
                "title": "Engine Design",
                "tags": "spec, design",
                "abstract": "Architectural guidelines for Evelyn Engine high performance systems.",
            },
        }

        with patch("Evelyn.tools.chroma_rag.query_collection", return_value=[mock_chunk]), \
             patch("Evelyn.tools.chroma_rag._fetch_pinned_chunks", return_value=[]), \
             patch("Evelyn.tools.chroma_rag._apply_priority_boost", side_effect=lambda x: x), \
             patch("Evelyn.tools.chroma_rag.log_rag_retrieval"):

            envelope = build_rag_context("How fast is direct vector search?")

            self.assertIn('<document path="Engineering/Engine_Design.md" title="Engine Design" tags="spec, design">', envelope)
            # Abstract anchored before mid-document excerpt
            self.assertIn("[!ABSTRACT]", envelope)
            self.assertIn("Architectural guidelines for Evelyn Engine high performance systems.", envelope)
            self.assertIn("## ⚡ Performance Tuning", envelope)


if __name__ == "__main__":
    unittest.main()
