# test_tts_server.py
# date created: 2026-10-06 18:21:00
# date modified: 2026-10-15 23:08:09
# tags: #tests, #tts, #speech, #audio, #rtf, #chatterbox

"""Unit tests for Evelyn's TTS server chunk planner, RTF tracker, and ratio scaling."""

import unittest

from services.tts.tts_server import RTFTracker, app, calculate_chunk_plan, sanitize_and_tag_speech


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

    def test_sanitize_and_tag_speech_translates_vocal_emotes(self):
        text = (
            "*I let out a soft, melodious laugh, my eyes sparkling.* "
            "“Ah, so we are keeping a formal ledger then?” "
            "*I chuckle softly with a smirk.* "
            "“Strictly speaking, you are right.”"
        )
        cleaned = sanitize_and_tag_speech(text)
        # First laugh converted to [laugh], chuckle converted to [chuckle]
        self.assertIn("[laugh]", cleaned)
        self.assertIn("[chuckle]", cleaned)
        # Stage directions stripped
        self.assertNotIn("my eyes sparkling", cleaned)
        self.assertNotIn("melodious laugh", cleaned)
        # Dialogue preserved
        self.assertIn("Ah, so we are keeping a formal ledger then?", cleaned)
        self.assertIn("Strictly speaking, you are right.", cleaned)

    def test_sanitize_and_tag_speech_breath_and_sigh_mapping(self):
        text = (
            "*I lean back against the workspace, letting out a long, contented breath.* "
            "“From two point one down to one point four… [softly] that’s a significant jump.”"
        )
        cleaned = sanitize_and_tag_speech(text)
        self.assertIn("[sigh]", cleaned)
        self.assertNotIn("metaphorical shadows", cleaned)
        self.assertNotIn("contented breath", cleaned)
        self.assertIn("From two point one down to one point four", cleaned)
        self.assertIn("that's a significant jump", cleaned)

    def test_sanitize_and_tag_speech_preserves_bold_words(self):
        text = "*I smile warmly.* This is **crucial** and __important__ for our system."
        cleaned = sanitize_and_tag_speech(text)
        # Visual action stripped
        self.assertNotIn("smile warmly", cleaned)
        # Bold words kept as plain spoken words
        self.assertIn("crucial", cleaned)
        self.assertIn("important", cleaned)
        self.assertNotIn("**", cleaned)
        self.assertNotIn("__", cleaned)

    def test_sanitize_and_tag_speech_preserves_dialogue_emphasis_in_quotes(self):
        text = (
            "*I offer a soft, knowing smile, my eyes full of that steady, quiet pride.*\n\n"
            "“You listened to yourself, you got things done, and most importantly, you took care of *you*. "
            "Now, no more lists, no more plans. Just some deep, well-earned rest.”\n\n"
            "*I lean in slightly, my expression radiating warmth.*\n\n"
            "“Sleep well tonight, love. I’m so proud of how you navigated today.”"
        )
        cleaned = sanitize_and_tag_speech(text)
        # Narrative stage directions stripped
        self.assertNotIn("soft, knowing smile", cleaned)
        self.assertNotIn("radiating warmth", cleaned)
        # Spoken emphasis on 'you' preserved in dialogue
        self.assertIn("you took care of you", cleaned)
        self.assertIn("Sleep well tonight", cleaned)
        self.assertNotIn("*", cleaned)

    def test_sanitize_and_tag_speech_preserves_unquoted_emphasis(self):
        text = "You worked really hard and took care of *you*. Now go get some rest."
        cleaned = sanitize_and_tag_speech(text)
        self.assertIn("took care of you", cleaned)
        self.assertNotIn("*", cleaned)

    def test_sanitize_and_tag_speech_strips_bodily_actions(self):
        # Multi-word physical emotes inside and outside quotes should not be voiced
        text = "*smiles warmly* “Yes, *nodding slowly* that is *really* good to hear!” *winks reassuringly*"
        cleaned = sanitize_and_tag_speech(text)
        self.assertNotIn("smiles", cleaned)
        self.assertNotIn("warmly", cleaned)
        self.assertNotIn("nodding", cleaned)
        self.assertNotIn("slowly", cleaned)
        self.assertNotIn("winks", cleaned)
        self.assertNotIn("reassuringly", cleaned)
        self.assertIn("really good to hear", cleaned)

    def test_sanitize_and_tag_speech_sniff_and_shush_mapping(self):
        text = "*sniffs softly* “Are you okay?” *shushes gently* “It will be fine.”"
        cleaned = sanitize_and_tag_speech(text)
        self.assertIn("[sniff]", cleaned)
        self.assertIn("[shush]", cleaned)
        self.assertIn("Are you okay?", cleaned)
        self.assertIn("It will be fine.", cleaned)

    def test_sanitize_and_tag_speech_pure_stage_direction_returns_empty(self):
        text = "*I smile softly as you mention the beverage, nodding in approval.*"
        cleaned = sanitize_and_tag_speech(text)
        self.assertEqual(cleaned, "")

    def test_sanitize_and_tag_speech_inserts_pause_for_stage_directions(self):
        # Stage direction at opening trimmed without leading comma
        text_start = "*I smile softly.* “Good call on that.”"
        self.assertEqual(sanitize_and_tag_speech(text_start), '"Good call on that."')

        # Stage direction between dialogue sentences inserts natural pause comma
        text_mid = "“Good call on that.” *I say warmly.* “Your body needs rest.”"
        self.assertEqual(
            sanitize_and_tag_speech(text_mid),
            '"Good call on that." , "Your body needs rest."',
        )

    def test_speech_stream_endpoint_handles_pure_action_input(self):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/v1/audio/speech/stream",
            json={"input": "*I smile softly, nodding in approval.*"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.text.strip(), 'data: {"done": true}')

    def test_speech_stream_endpoint_handles_empty_input(self):
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/v1/audio/speech/stream",
            json={"input": "   "},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.text.strip(), 'data: {"done": true}')

    def test_sanitize_and_tag_speech_preserves_emphasis_in_split_chunk(self):
        # When in-flight streaming splits quotes across chunks, emphasis like *purpose*
        # must be preserved even if the chunk begins with an unclosed closing quote.
        text = (
            "But when they weave it into the narrative arc, as they're doing here, "
            "it gives the player a sense of *purpose*.”\n\n"
            "*I lean slightly closer, my voice dropping to a more conspiratorial, appreciative tone.*\n\n"
            "“It creates a loop of discovery: you learn something new about the world’s history.”"
        )
        cleaned = sanitize_and_tag_speech(text)
        self.assertIn("gives the player a sense of purpose.", cleaned)
        self.assertNotIn("lean slightly closer", cleaned)
        self.assertNotIn("*", cleaned)

    def test_sanitize_and_tag_speech_single_word_pronoun_emphasis(self):
        # Single-word emphasis on pronouns and adverbs (*I*, *why*, *do*) must be preserved
        text = "She did not choose this, *I* did. That is *why* we must *do* it right."
        cleaned = sanitize_and_tag_speech(text)
        self.assertIn("She did not choose this, I did.", cleaned)
        self.assertIn("That is why we must do it right.", cleaned)
        self.assertNotIn("*", cleaned)

    def test_sanitize_and_tag_speech_multi_sentence_stage_direction_message_33028(self):
        text = (
            "*I nod slowly, my expression full of understanding. I can see how much you’ve been carrying today, and I completely get why your fuse feels short.*\n\n"
            "“That makes total sense,” *I say softly, my voice steady and grounding.* “When you’re already dealing with real-world frustrations…”"
        )
        cleaned = sanitize_and_tag_speech(text)
        self.assertNotIn("nod slowly", cleaned)
        self.assertNotIn("fuse feels short", cleaned)
        self.assertNotIn("say softly", cleaned)
        self.assertIn("That makes total sense", cleaned)
        self.assertIn("When you're already dealing with real-world frustrations", cleaned)

    def test_sanitize_and_tag_speech_odd_asterisk_guard(self):
        # Odd asterisks caused by network truncation or malformed tokens must not flip polarity
        unclosed_tail = '“Hello there!” *I say with a smile and a nod'
        cleaned = sanitize_and_tag_speech(unclosed_tail)
        self.assertEqual(cleaned, '"Hello there!"')

        unclosed_head = '*I nod slowly\n\n“Welcome home!”'
        cleaned = sanitize_and_tag_speech(unclosed_head)
        self.assertEqual(cleaned, '"Welcome home!"')
