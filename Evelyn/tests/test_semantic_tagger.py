# test_semantic_tagger.py
# date created: 2026-09-18 19:25:00
# date modified: 2026-09-18 19:33:58
# tags: #test, #semantic_tagger, #tag_librarian, #vault_db, #unit_test

"""Hermetic unit tests for the dedicated semantic tagging subsystem and Tag RAG drainer."""

import asyncio
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from Evelyn.tools import tag_librarian, vault_db


class TestSemanticTaggingSubsystem(unittest.TestCase):
    """Hermetic unit tests for vault_db semantic tag audit queue and tag_librarian engine."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_vault.db")
        self.orig_db_path = vault_db.DB_PATH
        self.orig_vault_root = tag_librarian.VAULT_ROOT
        vault_db.DB_PATH = self.db_path
        tag_librarian.VAULT_ROOT = self.temp_dir.name
        vault_db.init_db()

    def tearDown(self):
        vault_db.DB_PATH = self.orig_db_path
        tag_librarian.VAULT_ROOT = self.orig_vault_root
        self.temp_dir.cleanup()

    def test_fetch_next_documents_for_semantic_tag_audit_urgency_tiers(self):
        """Verify 5-tier prioritization queue ordering and exclusion filtering."""
        now = time.time()

        # Doc 1: Tier 1 - Never audited, no tags
        vault_db.upsert_document(
            path="Notes/Untagged_Fresh.md",
            title="Untagged Fresh",
            mtime=now,
            tags="",
            last_semantic_tag_audit=0.0,
        )

        # Doc 2: Tier 2 - Never audited, existing flat tags
        vault_db.upsert_document(
            path="Notes/Tagged_Fresh.md",
            title="Tagged Fresh",
            mtime=now,
            tags="tech, ai",
            last_semantic_tag_audit=0.0,
        )

        # Doc 3: Tier 3 - Audited previously, but modified since audit
        vault_db.upsert_document(
            path="Notes/Modified_Doc.md",
            title="Modified Doc",
            mtime=now - 50,
            tags="ai/llm",
            last_semantic_tag_audit=now - 500,
        )

        # Doc 4: Tier 4 - Cooled down (audited long ago, not modified)
        vault_db.upsert_document(
            path="Notes/Cooled_Doc.md",
            title="Cooled Doc",
            mtime=now - 100000,
            tags="reading/books",
            last_semantic_tag_audit=now - 90000,
        )

        # Doc 5: Excluded directory (Templates)
        vault_db.upsert_document(
            path="Templates/Excluded_Template.md",
            title="Excluded Template",
            mtime=now,
            tags="",
            last_semantic_tag_audit=0.0,
        )

        # Fetch with batch_size=5, cooldown=86400
        docs = vault_db.fetch_next_documents_for_semantic_tag_audit(batch_size=5, cooldown_seconds=86400)
        fetched_paths = [d["path"] for d in docs]

        # Templates note must be excluded
        self.assertNotIn("Templates/Excluded_Template.md", fetched_paths)

        # Order must strictly follow Tier 1 -> Tier 2 -> Tier 3 -> Tier 4
        self.assertIn("Notes/Untagged_Fresh.md", fetched_paths)
        self.assertIn("Notes/Tagged_Fresh.md", fetched_paths)
        self.assertIn("Notes/Modified_Doc.md", fetched_paths)
        self.assertIn("Notes/Cooled_Doc.md", fetched_paths)

        self.assertEqual(fetched_paths[0], "Notes/Untagged_Fresh.md")
        self.assertEqual(fetched_paths[1], "Notes/Tagged_Fresh.md")
        self.assertEqual(fetched_paths[2], "Notes/Modified_Doc.md")
        self.assertEqual(fetched_paths[3], "Notes/Cooled_Doc.md")

    def test_update_document_semantic_tag_audit(self):
        """Verify update_document_semantic_tag_audit updates audit timestamp and tags."""
        now = time.time()
        vault_db.upsert_document(
            path="Notes/TestDoc.md",
            title="Test Doc",
            mtime=now,
            tags="old/tag",
            last_semantic_tag_audit=0.0,
        )

        doc_before = vault_db.get_document("Notes/TestDoc.md")
        self.assertIsNotNone(doc_before)
        assert doc_before is not None
        self.assertEqual(doc_before["last_semantic_tag_audit"], 0.0)

        # Update audit timestamp and new tags
        vault_db.update_document_semantic_tag_audit("Notes/TestDoc.md", tags="new/domain, new/sub")

        doc_after = vault_db.get_document("Notes/TestDoc.md")
        self.assertIsNotNone(doc_after)
        assert doc_after is not None
        self.assertGreater(doc_after["last_semantic_tag_audit"], now - 10)
        self.assertEqual(doc_after["tags"], "new/domain, new/sub")

    @patch("Evelyn.tools.tag_librarian.chroma_rag.ingest_markdown_file")
    @patch("Evelyn.tools.tag_librarian.query_ollama")
    @patch("Evelyn.tools.tag_librarian.retrieve_candidate_tags_for_document")
    def test_audit_single_document_semantic_flow(
        self,
        mock_candidates,
        mock_query_ollama,
        mock_chroma_ingest,
    ):
        """Verify full single-document semantic audit flow with Tag RAG and LLM mock."""
        mock_candidates.return_value = (
            [
                {"tag": "AI/LLM/Inference", "category": "AI", "description": "LLM inference techniques", "distance": 0.12},
                {"tag": "AI/RAG/Evaluation", "category": "AI", "description": "RAG evaluation frameworks", "distance": 0.25},
            ],
            0.12,
            "TAXONOMY MATCH CONFIDENCE: HIGH",
        )
        mock_query_ollama.return_value = """{
            "tags_to_keep": [],
            "tags_to_add": ["AI/LLM/Inference", "AI/RAG/Evaluation"],
            "tags_to_remove": ["ai", "rag"],
            "new_master_tags": []
        }"""

        # Create physical test document in temp vault
        doc_rel = "Notes/Neural_Search.md"
        doc_abs = os.path.join(self.temp_dir.name, doc_rel)
        os.makedirs(os.path.dirname(doc_abs), exist_ok=True)
        raw_content = """---
title: Neural Search Architectures
tags: [ai, rag]
---
# Neural Search Architectures
Vector retrieval with embedding rerankers and Chroma stores.
"""
        with open(doc_abs, "w", encoding="utf-8") as f:
            f.write(raw_content)

        vault_db.upsert_document(
            path=doc_rel,
            title="Neural Search Architectures",
            mtime=os.path.getmtime(doc_abs),
            tags="ai, rag",
            last_semantic_tag_audit=0.0,
        )

        # Run semantic audit
        result = tag_librarian.audit_single_document_semantic(
            doc_path=doc_rel,
            vault_root=self.temp_dir.name,
            dry_run=False,
        )

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["modified"])
        self.assertEqual(result["final_tags"], ["Ai/Llm/Inference", "Ai/Rag/Evaluation"])

        # Verify disk file updated
        with open(doc_abs, encoding="utf-8") as f:
            updated_content = f.read()
        self.assertIn("tags: [Ai/Llm/Inference, Ai/Rag/Evaluation]", updated_content)

        # Verify vault_db updated
        doc_in_db = vault_db.get_document(doc_rel)
        self.assertIsNotNone(doc_in_db)
        assert doc_in_db is not None
        self.assertGreater(doc_in_db["last_semantic_tag_audit"], 0)
        self.assertIn("Ai/Llm/Inference", doc_in_db["tags"])

    @patch("Evelyn.tools.tag_librarian.audit_single_document_semantic")
    @patch("Evelyn.tools.vault_db.fetch_next_documents_for_semantic_tag_audit")
    def test_run_semantic_tag_audit_async_drainer(
        self,
        mock_fetch,
        mock_audit_single,
    ):
        """Verify backlog drainer integration in run_semantic_tag_audit_async."""
        # Batch 1 returns 2 docs; Batch 2 returns empty (queue drained)
        mock_fetch.side_effect = [
            [{"path": "Notes/Doc1.md"}, {"path": "Notes/Doc2.md"}],
            [],
        ]
        mock_audit_single.return_value = {
            "status": "success",
            "modified": True,
            "final_tags": ["Tech/Python"],
        }

        result = asyncio.run(
            tag_librarian.run_semantic_tag_audit_async(
                batch_size=2,
                max_batches=1,
                delay_between_items=0.0,
                auto_re_enqueue=False,
            )
        )

        self.assertEqual(result.items_processed, 2)
        self.assertEqual(result.batches_completed, 1)
        self.assertFalse(result.yielded)
        self.assertEqual(mock_audit_single.call_count, 2)


if __name__ == "__main__":
    unittest.main()
