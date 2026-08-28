"""Unit tests for multimodal vision ingestion, visual indexing, and vector sync."""

import base64
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import evelyn_config as cfg
from Evelyn.tools import chroma_rag, media_db, visual_indexer


class TestMultimodalVision(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_media.db")
        self.memory_db_path = os.path.join(self.test_dir, "test_memory.db")

        self.cfg_patcher1 = patch.object(cfg, "MEDIA_DB_PATH", self.db_path)
        self.cfg_patcher2 = patch.object(cfg, "MEMORY_DB_PATH", self.memory_db_path)
        self.cfg_patcher3 = patch.object(cfg, "BASE_DIR", self.test_dir)
        self.cfg_patcher1.start()
        self.cfg_patcher2.start()
        self.cfg_patcher3.start()

        from Evelyn.tools import memory_db
        memory_db.init_db()
        media_db.init_media_db()

    def tearDown(self):
        self.cfg_patcher1.stop()
        self.cfg_patcher2.stop()
        self.cfg_patcher3.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("Evelyn.tools.visual_indexer.httpx.AsyncClient")
    def test_visual_indexer_processing_and_chroma_queue(self, mock_client_cls):
        from unittest.mock import MagicMock
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {
                "content": '{"caption": "System architecture diagram with SQLite and Ollama.", "ocr_text": "FASTAPI ROUTER CHROMA_DB", "suggested_tags": ["#architecture", "#python"], "domain": "Tech/Architecture"}'
            }
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client_cls.return_value = mock_client

        dummy_img = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
        asset = media_db.store_or_get_media_asset(
            data=dummy_img,
            mime_type="image/png",
            source_msg_id=1,
            original_name="arch_diagram.png",
        )
        guid = asset["id"]
        b64 = base64.b64encode(dummy_img).decode("utf-8")

        # Run visual indexing
        import asyncio
        success = asyncio.run(
            visual_indexer.process_media_asset_indexing(guid=guid, base64_image=b64)
        )
        self.assertTrue(success)

        # Verify media_db updated
        updated_asset = media_db.get_media_asset(guid)
        self.assertEqual(
            updated_asset["description"],
            "System architecture diagram with SQLite and Ollama.",
        )
        self.assertEqual(updated_asset["extracted_text"], "FASTAPI ROUTER CHROMA_DB")
        self.assertIn("#architecture", updated_asset["tags"])
        self.assertEqual(updated_asset["taxonomy_domain"], "Tech/Architecture")

        # Verify Chroma sync queue has an upsert item for evelyn_media
        con = chroma_rag._get_queue_db()
        cur = con.cursor()
        cur.execute(
            "SELECT * FROM chroma_sync_queue WHERE collection_name = ? AND source_path = ?",
            (cfg.CHROMA_MEDIA_COLLECTION, f"media::{guid}"),
        )
        queue_row = cur.fetchone()
        con.close()

        self.assertIsNotNone(queue_row)
        self.assertEqual(queue_row["action"], "upsert")
        self.assertIn("System architecture diagram", queue_row["content"])
        self.assertIn("FASTAPI ROUTER", queue_row["content"])


if __name__ == "__main__":
    unittest.main()
