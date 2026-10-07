# test_tts_server.py
# date created: 2026-10-06 18:21:00
# date modified: 2026-10-06 18:50:12
# tags: #tests, #tts, #speech, #audio, #rtf, #chatterbox

"""Unit tests for Evelyn's TTS server chunk planner, RTF tracker, and ratio scaling."""

import unittest

from services.tts.tts_server import RTFTracker, calculate_chunk_plan


class TestTTSServer(unittest.TestCase):
    def test_rtf_tracker_initial_and_ema_update(self):
        tracker = RTFTracker(initial_rtf=2.0)
        self.assertAlmostEqual(tracker.value, 2.0)

        # Record chunk with same RTF (10s gen / 5s audio = 2.0x)
        ema = tracker.record(gen_seconds=10.0, audio_seconds=5.0)
        self.assertAlmostEqual(ema, 2.0)

        # Record faster chunk (2s gen / 4s audio = 0.5x)
        # alpha = 0.35: new EMA = 0.35 * 0.5 + 0.65 * 2.0 = 0.175 + 1.30 = 1.475
        ema = tracker.record(gen_seconds=2.0, audio_seconds=4.0)
        self.assertAlmostEqual(ema, 1.475, places=3)
        self.assertAlmostEqual(tracker.value, 1.475, places=3)

        # Microscopic audio duration ignored
        unchanged = tracker.record(gen_seconds=1.0, audio_seconds=0.05)
        self.assertAlmostEqual(unchanged, 1.475, places=3)

    def test_calculate_chunk_plan_short_text(self):
        # Empty / whitespace
        self.assertEqual(calculate_chunk_plan("", 2.0), [""])
        self.assertEqual(calculate_chunk_plan("   ", 2.0), [""])

        # Single sentence
        single = "This is a single standalone sentence."
        self.assertEqual(calculate_chunk_plan(single, 2.0), [single])

    def test_calculate_chunk_plan_cpu_asymmetric(self):
        text = (
            "I would be glad to help with that project today. "
            "First, let us examine the core architecture to see how everything fits together cleanly. "
            "Then, we can review the configuration files and test the network endpoints. "
            "Finally, we can deploy the updated services and verify the logs."
        )
        # On CPU (RTF ~2.09), Chunk 0 must be 1 sentence for fast dispatch
        chunks = calculate_chunk_plan(text, rtf_ema=2.09)
        self.assertGreaterEqual(len(chunks), 2)
        # Chunk 0 should be exactly the first sentence
        self.assertEqual(chunks[0], "I would be glad to help with that project today.")
        # Total text preserved
        reconstructed = " ".join(chunks)
        for s in ["First", "examine", "configuration", "deploy"]:
            self.assertIn(s, reconstructed)

    def test_calculate_chunk_plan_merges_short_opening_greeting(self):
        # When opening sentence is < 35 chars, it should merge forward with sentence 1
        text = "Yes. That is a wonderful priority to have for our evening."
        chunks = calculate_chunk_plan(text, rtf_ema=2.09)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "Yes. That is a wonderful priority to have for our evening.")

    def test_calculate_chunk_plan_cuda_fine_grained(self):
        text = (
            "The initial setup step has completed successfully. "
            "Now let's proceed with the secondary evaluation. "
            "Each part is verified sequentially. "
            "The final step concludes our run."
        )
        # On CUDA (RTF ~0.25), chunks remain fine-grained
        chunks = calculate_chunk_plan(text, rtf_ema=0.25)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual(chunks[0], "The initial setup step has completed successfully.")

    def test_calculate_chunk_plan_preserves_paralinguistic_tags(self):
        text = (
            "Well... [sigh] I suppose we could try that approach. "
            "[chuckle] Just make sure to double check the configuration first. "
            "Otherwise things might behave unexpectedly."
        )
        chunks = calculate_chunk_plan(text, rtf_ema=2.0)
        self.assertIn("[sigh]", chunks[0])
        combined = " ".join(chunks)
        self.assertIn("[chuckle]", combined)

    def test_rtf_tracker_disk_persistence(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            cal_file = Path(tmpdir) / "cal.json"

            # Fresh tracker with default
            tracker1 = RTFTracker(initial_rtf=2.0, device="cpu", calibration_file=cal_file)
            self.assertAlmostEqual(tracker1.value, 2.0)

            # Record a measured chunk (gen 14.5s / 10s audio = 1.45x)
            tracker1.record(gen_seconds=14.5, audio_seconds=10.0)

            # Verify saved to disk
            self.assertTrue(cal_file.exists())
            with open(cal_file, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertIn("cpu", saved)

            # New tracker instance reloads persisted value
            tracker2 = RTFTracker(device="cpu", calibration_file=cal_file)
            self.assertAlmostEqual(tracker2.value, tracker1.value, places=2)

    def test_calculate_chunk_plan_cpu_stepping_stone(self):
        text = (
            "Sentence one is our opening statement here. "
            "Sentence two is the stepping stone for CPU playback continuity. "
            "Sentence three continues the detailed discussion at length. "
            "Sentence four concludes the section."
        )
        chunks = calculate_chunk_plan(text, rtf_ema=1.45)
        # On CPU (RTF 1.45 >= 1.0), Chunk 0 and Chunk 1 are single stepping stone sentences
        self.assertGreaterEqual(len(chunks), 3)
        self.assertEqual(chunks[0], "Sentence one is our opening statement here.")
        self.assertEqual(chunks[1], "Sentence two is the stepping stone for CPU playback continuity.")
