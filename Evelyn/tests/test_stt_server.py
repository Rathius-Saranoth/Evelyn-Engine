# test_stt_server.py
# date created: 2026-09-15 18:15:00
# date modified: 2026-09-15 18:16:17
# tags: #tests, #stt, #whisper, #audio, #media_db

"""Unit tests for Evelyn's Speech-to-Text (STT) microservice, media retention, and server proxy."""

import io
import math
import os
import shutil
import struct
import tempfile
import time
import unittest
import wave
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import evelyn_config as cfg
from Evelyn.tools import media_db
from services.stt.stt_server import app as stt_app
from services.stt.stt_server import decode_audio_to_pcm


def _generate_synthetic_wav(duration_s: float = 1.0, sample_rate: int = 16000, freq: float = 440.0) -> bytes:
    """Generate in-memory WAV audio bytes containing a pure sine tone."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        num_frames = int(duration_s * sample_rate)
        frames = bytearray()
        for i in range(num_frames):
            val = int(16000.0 * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
            frames.extend(struct.pack("<h", val))
        wf.writeframes(frames)
    return buf.getvalue()


class TestSTTServer(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(stt_app)

    def test_health_endpoint(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "evelyn-stt")
        self.assertIn("model_size", data)

    def test_decode_audio_to_pcm_synthetic_wav(self):
        wav_bytes = _generate_synthetic_wav(duration_s=1.2, sample_rate=16000)
        pcm_data, duration_s = decode_audio_to_pcm(wav_bytes)
        self.assertGreater(len(pcm_data), 0)
        self.assertAlmostEqual(duration_s, 1.2, delta=0.1)

    def test_decode_audio_to_pcm_empty_raises(self):
        with self.assertRaises(ValueError):
            decode_audio_to_pcm(b"")

    def test_transcribe_short_audio_rejection(self):
        # Generate 0.2s audio (< 0.5s minimum)
        short_wav = _generate_synthetic_wav(duration_s=0.2, sample_rate=16000)
        resp = self.client.post(
            "/v1/audio/transcriptions",
            files={"file": ("short.wav", short_wav, "audio/wav")},
            data={"language": "en"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["text"], "")
        self.assertLess(data["duration_s"], 0.5)

    @patch("services.stt.stt_server.get_model")
    def test_transcribe_valid_audio_mocked(self, mock_get_model):
        mock_segment = MagicMock()
        mock_segment.text = "Hello Evelyn this is a voice test"
        mock_info = MagicMock()
        mock_info.language = "en"

        mock_model = MagicMock()
        mock_model.transcribe.return_value = ([mock_segment], mock_info)
        mock_get_model.return_value = mock_model

        wav_bytes = _generate_synthetic_wav(duration_s=1.5, sample_rate=16000)
        resp = self.client.post(
            "/v1/audio/transcriptions",
            files={"file": ("test.wav", wav_bytes, "audio/wav")},
            data={"language": "en"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["text"], "Hello Evelyn this is a voice test")
        self.assertEqual(data["language"], "en")
        self.assertGreaterEqual(data["duration_s"], 1.4)

    def test_clean_whisper_text_hallucinations(self):
        from services.stt.stt_server import clean_whisper_text

        # Silence hallucinations should be completely stripped
        self.assertEqual(clean_whisper_text("For more information please visit www.f"), "")
        self.assertEqual(clean_whisper_text("For more information please visit www.example.com"), "")
        self.assertEqual(clean_whisper_text("Thank you for watching! Please subscribe."), "")
        self.assertEqual(clean_whisper_text("Subtitles by Amara.org"), "")
        self.assertEqual(clean_whisper_text("..."), "")
        self.assertEqual(clean_whisper_text("[silence]"), "")

        # Valid text should be preserved intact
        self.assertEqual(clean_whisper_text("Here is a test for deletion"), "Here is a test for deletion")
        self.assertEqual(clean_whisper_text("Hello Evelyn, this is real speech."), "Hello Evelyn, this is real speech.")


class TestMediaDbDeletionAndRetention(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_media.db")
        self.cfg_patcher1 = patch.object(cfg, "MEDIA_DB_PATH", self.db_path)
        self.cfg_patcher2 = patch.object(cfg, "BASE_DIR", self.test_dir)
        self.cfg_patcher1.start()
        self.cfg_patcher2.start()

        media_db.init_media_db()

    def tearDown(self):
        self.cfg_patcher1.stop()
        self.cfg_patcher2.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_delete_media_asset(self):
        dummy_audio = _generate_synthetic_wav(duration_s=1.0)
        asset = media_db.store_or_get_media_asset(
            data=dummy_audio,
            mime_type="audio/wav",
            media_type="audio",
            source_msg_id=1,
            original_name="clip.wav",
        )
        guid = asset["id"]
        abs_path = asset["abs_file_path"]
        self.assertTrue(os.path.isfile(abs_path))

        # Delete asset
        deleted = media_db.delete_media_asset(guid)
        self.assertTrue(deleted)
        self.assertFalse(os.path.isfile(abs_path))
        self.assertIsNone(media_db.get_media_asset(guid))

        # Second deletion should return False
        self.assertFalse(media_db.delete_media_asset(guid))

    def test_prune_expired_audio_assets(self):
        dummy_audio1 = _generate_synthetic_wav(duration_s=1.0, freq=300.0)
        dummy_audio2 = _generate_synthetic_wav(duration_s=1.0, freq=600.0)

        asset_old = media_db.store_or_get_media_asset(
            data=dummy_audio1,
            mime_type="audio/wav",
            media_type="audio",
            original_name="old.wav",
        )
        asset_new = media_db.store_or_get_media_asset(
            data=dummy_audio2,
            mime_type="audio/wav",
            media_type="audio",
            original_name="new.wav",
        )

        # Backdate asset_old created_ts to 45 days ago
        con = media_db.get_db()
        forty_five_days_ago = time.time() - (45 * 86400)
        with con:
            con.execute("UPDATE media_assets SET created_ts = ? WHERE id = ?", (forty_five_days_ago, asset_old["id"]))
        con.close()

        # When retention_days == 0 (disabled), no-op
        pruned_zero = media_db.prune_expired_audio_assets(retention_days=0)
        self.assertEqual(pruned_zero, 0)
        self.assertTrue(os.path.isfile(asset_old["abs_file_path"]))

        # When retention_days == 30, prunes asset_old (>30d) and leaves asset_new
        pruned_thirty = media_db.prune_expired_audio_assets(retention_days=30)
        self.assertEqual(pruned_thirty, 1)
        self.assertFalse(os.path.isfile(asset_old["abs_file_path"]))
        self.assertTrue(os.path.isfile(asset_new["abs_file_path"]))


if __name__ == "__main__":
    unittest.main()
