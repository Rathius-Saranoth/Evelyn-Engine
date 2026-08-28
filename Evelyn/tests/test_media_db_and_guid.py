"""Unit tests for Evelyn's media_db.py module."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import evelyn_config as cfg
from Evelyn.tools import media_db


class TestMediaDbAndGuid(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_media.db")
        self.attachments_dir = os.path.join(self.test_dir, "attachments")

        self.cfg_patcher1 = patch.object(cfg, "MEDIA_DB_PATH", self.db_path)
        self.cfg_patcher2 = patch.object(cfg, "BASE_DIR", self.test_dir)
        self.cfg_patcher1.start()
        self.cfg_patcher2.start()

        media_db.init_media_db()

    def tearDown(self):
        self.cfg_patcher1.stop()
        self.cfg_patcher2.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_guid_generation_format(self):
        img_guid = media_db.generate_guid("image")
        aud_guid = media_db.generate_guid("audio")
        doc_guid = media_db.generate_guid("document")

        self.assertTrue(img_guid.startswith("med_img_"))
        self.assertTrue(aud_guid.startswith("med_aud_"))
        self.assertTrue(doc_guid.startswith("med_doc_"))
        self.assertEqual(len(img_guid), len("med_img_") + 32)

    def test_store_and_deduplicate_media_asset(self):
        dummy_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"

        # Turn 1: Store new image asset
        asset1 = media_db.store_or_get_media_asset(
            data=dummy_data,
            mime_type="image/png",
            source_msg_id=101,
            original_name="test.png",
        )
        self.assertTrue(asset1["is_new"])
        self.assertTrue(asset1["id"].startswith("med_img_"))
        self.assertTrue(os.path.exists(asset1["abs_file_path"]))

        # Turn 2: Re-upload the EXACT same image in a subsequent message turn (105)
        asset2 = media_db.store_or_get_media_asset(
            data=dummy_data,
            mime_type="image/png",
            source_msg_id=105,
            original_name="duplicate.png",
        )
        self.assertFalse(asset2["is_new"])
        self.assertEqual(asset1["id"], asset2["id"])
        self.assertEqual(asset1["file_hash"], asset2["file_hash"])

        # Check that both messages 101 and 105 link to this single media asset
        msg101_media = media_db.get_media_for_message(101)
        msg105_media = media_db.get_media_for_message(105)

        self.assertEqual(len(msg101_media), 1)
        self.assertEqual(len(msg105_media), 1)
        self.assertEqual(msg101_media[0]["id"], asset1["id"])
        self.assertEqual(msg105_media[0]["id"], asset1["id"])

    def test_update_media_metadata(self):
        dummy_data = b"sample audio bytes"
        asset = media_db.store_or_get_media_asset(
            data=dummy_data,
            mime_type="audio/wav",
            source_msg_id=200,
            media_type="audio",
        )

        success = media_db.update_media_metadata(
            guid=asset["id"],
            description="Short voice recording discussing project timeline.",
            extracted_text="Let's make sure the vision pipeline is done today.",
            tags=["#voice_memo", "#project/timeline"],
            taxonomy_domain="Tech/Projects/Evelyn",
        )
        self.assertTrue(success)

        retrieved = media_db.get_media_asset(asset["id"])
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["description"], "Short voice recording discussing project timeline.")
        self.assertEqual(retrieved["extracted_text"], "Let's make sure the vision pipeline is done today.")
        self.assertEqual(retrieved["tags"], ["#voice_memo", "#project/timeline"])
        self.assertEqual(retrieved["taxonomy_domain"], "Tech/Projects/Evelyn")


    def test_store_image_with_gps_and_exif_metadata(self):
        dummy_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
        client_meta = {
            "camera_make": "Apple",
            "camera_model": "iPhone 15 Pro",
            "datetime": "2026:08:21 14:30:00",
            "gps": {"latitude": 32.7767, "longitude": -96.7970, "altitude_m": 137.5},
        }

        asset = media_db.store_or_get_media_asset(
            data=dummy_data,
            mime_type="image/png",
            source_msg_id=300,
            original_name="dallas_trip.png",
            metadata=client_meta,
        )
        self.assertTrue(asset["is_new"])

        retrieved = media_db.get_media_asset(asset["id"])
        self.assertIsNotNone(retrieved)
        self.assertIsNotNone(retrieved["metadata"])
        self.assertEqual(retrieved["metadata"]["camera_make"], "Apple")
        self.assertEqual(retrieved["metadata"]["camera_model"], "iPhone 15 Pro")
        self.assertEqual(retrieved["metadata"]["gps"]["latitude"], 32.7767)
    def test_update_media_metadata_and_tags(self):
        dummy_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\x01"
        asset = media_db.store_or_get_media_asset(
            data=dummy_data,
            mime_type="image/png",
            source_msg_id=400,
            original_name="test_fox.png",
        )
        guid = asset["id"]

        success = media_db.update_media_metadata(
            guid=guid,
            description="Fox resting by the sunny window.",
            tags=["#Fox", "#cat", "#window"],
            taxonomy_domain="Pets/Fox",
        )
        self.assertTrue(success)

        retrieved = media_db.get_media_asset(guid)
        self.assertEqual(retrieved["description"], "Fox resting by the sunny window.")
        self.assertIn("#Fox", retrieved["tags"])
        self.assertEqual(retrieved["taxonomy_domain"], "Pets/Fox")


if __name__ == "__main__":
    unittest.main()

