# test_pdf_staging_worker.py
# date created: 2026-08-22
# tags: #tests, #pdf, #staging, #task_manager, #sidecar

"""
Unit tests for Evelyn's automated PDF Staging Worker and Ingestion Pipeline.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz  # PyMuPDF

import Evelyn.tools.pdf_staging_worker as staging_worker
from Evelyn.tools import task_manager


class TestPdfStagingWorker(unittest.TestCase):
    """Test suite for PDF staging queues, task supervision, and domain dispatch."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="evelyn_test_staging_")
        self.vault_root = Path(self.test_dir) / "vault"
        self.vault_root.mkdir(parents=True, exist_ok=True)

        self.staging_dir = self.vault_root / "Attachments" / "Staging"
        self.full_staging = self.staging_dir / "Full_Extraction"
        self.card_staging = self.staging_dir / "Sidecar_Only"
        self.source_root = self.vault_root / "Attachments" / "Source Material"

        self.full_staging.mkdir(parents=True, exist_ok=True)
        self.card_staging.mkdir(parents=True, exist_ok=True)
        self.source_root.mkdir(parents=True, exist_ok=True)

        # Patch module paths
        self.patch_vault = patch.object(staging_worker, "VAULT_ROOT", self.vault_root)
        self.patch_full = patch.object(staging_worker, "FULL_EXTRACTION_STAGING", self.full_staging)
        self.patch_card = patch.object(staging_worker, "SIDECAR_ONLY_STAGING", self.card_staging)
        self.patch_source = patch.object(staging_worker, "ATTACHMENTS_SOURCE_ROOT", self.source_root)

        self.patch_vault.start()
        self.patch_full.start()
        self.patch_card.start()
        self.patch_source.start()

    def tearDown(self):
        self.patch_source.stop()
        self.patch_card.stop()
        self.patch_full.stop()
        self.patch_vault.stop()
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _create_sample_pdf(self, path: Path, title: str, pages: int = 1) -> Path:
        """Create a valid mock PDF file with selectable text."""
        doc = fitz.open()
        for i in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_text((50, 50), f"# {title}\nChapter {i+1}\nThis is sample content for testing.", fontsize=12)
        doc.save(str(path))
        doc.close()
        return path

    def test_get_available_domains(self):
        """Verify available domains list is populated with standard vault paths."""
        domains = staging_worker.get_available_domains()
        self.assertIsInstance(domains, list)
        self.assertGreater(len(domains), 0)
        labels = [d["label"] for d in domains]
        self.assertTrue(any("Reference Library" in l for l in labels))
        self.assertTrue(any("Medical" in l for l in labels))

    def test_process_sidecar_only_staging(self):
        """Verify sidecar-only staging moves PDF to Attachments and generates sidecar card."""
        pdf_path = self.card_staging / "Sample Medical Record.pdf"
        self._create_sample_pdf(pdf_path, "Sample Medical Record")

        # Create meta file specifying target domain
        meta_path = self.card_staging / "Sample Medical Record.pdf.meta.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write('{"target_path": "Ricky/Medical", "domain": "Medical"}')

        result = staging_worker.process_staging_item(pdf_path, mode="card")
        self.assertEqual(result["status"], "success")

        # Check sidecar created in Ricky/Medical
        expected_note = self.vault_root / "Ricky" / "Medical" / "Sample Medical Record" / "Sample Medical Record_index.md"
        self.assertTrue(expected_note.exists())

        # Check source PDF moved to Attachments/Source Material/Medical/
        expected_attachment = self.source_root / "Medical" / "Sample Medical Record.pdf"
        self.assertTrue(expected_attachment.exists())

        # Check original in staging removed
        self.assertFalse(pdf_path.exists())
        self.assertFalse(meta_path.exists())

    def test_process_staging_queue_respects_task_mutex(self):
        """Verify staging ingestion defers execution when another heavy task is active."""
        pdf_path = self.card_staging / "Test Document.pdf"
        self._create_sample_pdf(pdf_path, "Test Document")

        # Simulate another running heavy task
        task_manager.set_running("sync")
        try:
            results = staging_worker.process_staging_queue()
            self.assertEqual(len(results), 0)
            self.assertTrue(pdf_path.exists())  # Remains in staging
        finally:
            task_manager.clear_running("sync")

        # Now when mutex is free, processing runs
        results = staging_worker.process_staging_queue()
        self.assertEqual(len(results), 1)
        self.assertFalse(pdf_path.exists())



    def test_process_full_extraction_staging(self):
        """Verify full extraction mode creates chapter notes, index, and moves source."""
        pdf_path = self.full_staging / "Sample Book Guide.pdf"
        self._create_sample_pdf(pdf_path, "Sample Book Guide", pages=2)

        meta_path = self.full_staging / "Sample Book Guide.pdf.meta.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write('{"target_path": "Reference Library", "domain": "AI"}')

        result = staging_worker.process_staging_item(pdf_path, mode="full")
        self.assertEqual(result["status"], "success")

        # Book folder should exist in Reference Library
        book_dir = self.vault_root / "Reference Library" / "Sample Book Guide"
        self.assertTrue(book_dir.exists())

        # Index note should exist
        index_note = book_dir / "Sample Book Guide_index.md"
        self.assertTrue(index_note.exists())

        # Source PDF moved to Attachments/Source Material/AI/
        attachment_pdf = self.source_root / "AI" / "Sample Book Guide.pdf"
        self.assertTrue(attachment_pdf.exists())


if __name__ == "__main__":
    unittest.main()

