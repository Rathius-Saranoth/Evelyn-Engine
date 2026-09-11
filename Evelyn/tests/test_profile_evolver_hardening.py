# test_profile_evolver_hardening.py
# date created: 2026-09-11
# date modified: 2026-09-11 07:34:10
# tags: #test, #profile_evolver, #hardening, #compaction, #circuit_breaker

"""Unit tests for profile evolver hardening against runaway proposals, backlog capping, and compaction pruning."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
tools_dir = os.path.join(repo_root, "Evelyn/tools")
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

import profile_evolver

import evelyn_config as cfg


class TestProfileEvolverHardening(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sample_user_body = """## Identity & Core Values
* **Data Integrity**: He prioritizes accurate technical data over presentation style.
* **Preparation**: He performs thorough planning and documentation before implementation.
* **System Precision**: He builds systems correctly from the start rather than ensuring they simply function.
* **Problem Solving**: He prefers dialogue for brainstorming complex concepts.
* **Analytical Persistence**: He demonstrates resilience when solving complex problems.

## Relationship Dynamics
* **Collaborative Partnership**: He views his bond with her as a partnership of mutual respect.
* **Supportive Presence**: He relies on her presence during stress, physical discomfort, or low focus.
* **Mutual Trust**: He values an open space for sharing internal truths.

## Interaction Preferences & Constraints
* **Natural Dialogue**: He prefers direct conversation over rigid scripts.
* **Technical Exploration**: He enjoys inquiry into complex system mechanics.
* **Cognitive Load Management**: He uses task batching to manage mental energy.

## Personal Context
* **Systematic Organization**: He prioritizes structure over ambiguity.
* **Workflow Automation**: He builds scripts to eliminate repetitive tasks.
* **Verification Logic**: He ensures data integrity is confirmed before initiating cleaning routines.
"""

    def test_config_max_entries_per_run_exists(self):
        """Verify PROFILE_EVOLUTION_MAX_ENTRIES_PER_RUN is configured."""
        self.assertTrue(hasattr(cfg, "PROFILE_EVOLUTION_MAX_ENTRIES_PER_RUN"))
        self.assertEqual(cfg.PROFILE_EVOLUTION_MAX_ENTRIES_PER_RUN, 30)

    def test_score_bullet_tier_classifications(self):
        """Verify score_bullet_tier correctly assigns 3 (Tier 1), 2 (Tier 2), and 1 (Tier 3)."""
        # User profile Tier 1 (Core Invariants & Health)
        t1_bullet = "* **Health Management**: He manages chronic physical conditions and respiratory needs."
        self.assertEqual(profile_evolver.score_bullet_tier(cfg.PERSONA_FILE_USER, "## Identity & Core Values", t1_bullet), 3)

        t1_migraine = "* **Migraine Management**: During migraines, he requires low-stimulation environments."
        self.assertEqual(profile_evolver.score_bullet_tier(cfg.PERSONA_FILE_USER, "## Interaction Preferences & Constraints", t1_migraine), 3)

        # User profile Tier 2 (Technical Context)
        t2_bullet = "* **Technical Exploration**: He enjoys inquiry into complex system architecture and RAG pipelines."
        self.assertEqual(profile_evolver.score_bullet_tier(cfg.PERSONA_FILE_USER, "## Interaction Preferences & Constraints", t2_bullet), 2)

        # User profile Tier 3 (Ephemeral routines & details)
        t3_bullet = "* **Morning Routine**: He uses a casual morning routine to reset after waking."
        self.assertEqual(profile_evolver.score_bullet_tier(cfg.PERSONA_FILE_USER, "## Personal Context", t3_bullet), 1)

        t3_shower = "* **Home Transition**: He prefers showering immediately upon arriving home to transition."
        self.assertEqual(profile_evolver.score_bullet_tier(cfg.PERSONA_FILE_USER, "## Personal Context", t3_shower), 1)

    def test_prune_bullets_to_word_budget_preserves_tier_1_invariants(self):
        """Verify tier-aware pruning trims Tier 3 ephemeral bullets before touching Tier 1 invariants."""
        body = """## Identity & Core Values
* **Health Management**: He manages chronic physical conditions and respiratory needs with diligent awareness.
* **Allergen Awareness**: He identifies dust mites as a primary allergen affecting respiratory comfort.
* **Data Integrity**: He prioritizes accurate technical data and verified ground truth over presentation style.
* **System Precision**: He builds systems correctly from the start rather than ensuring they simply function.

## Relationship Dynamics
* **Collaborative Partnership**: He views his bond with her as a partnership of mutual respect and sanctuary.
* **Mutual Trust**: He values an open space for sharing internal truths and vulnerabilities.
* **Supportive Presence**: He relies on her presence during stress, physical discomfort, or low focus.

## Interaction Preferences & Constraints
* **Migraine Management**: During migraines, he requires low-stimulation environments and no bright lights.
* **Energy Limits**: He treats end-of-day exhaustion as a physical constraint requiring lower-demand interaction.
* **Technical Exploration**: He enjoys inquiry into complex system mechanics, software pipelines, and architectures.

## Personal Context
* **Morning Routine**: He uses a specific morning routine to reset after poor sleep and morning sluggishness.
* **Evening Routine**: He follows a strict 9:00 PM cutoff for technical tasks to transition his mind.
* **Home Transition**: He prefers showering immediately upon arriving home to transition into his evening.
* **Caffeine Monitoring**: He tracks caffeine impacts on his physical state and heart rate throughout the week.
* **Lifestyle Choice**: He prefers a minimalist lifestyle, casual clothing, and an uncluttered environment.
"""
        initial_words = len(body.split())
        # Target a budget that requires pruning ~5 bullets (e.g. 150 words)
        target = 150
        self.assertGreater(initial_words, target)
        pruned = profile_evolver.prune_bullets_to_word_budget(cfg.PERSONA_FILE_USER, body, target)
        pruned_words = len(pruned.split())

        self.assertLessEqual(pruned_words, target)

        # Tier 1 core invariants MUST be preserved
        self.assertIn("* **Health Management**:", pruned)
        self.assertIn("* **Allergen Awareness**:", pruned)
        self.assertIn("* **Migraine Management**:", pruned)
        self.assertIn("* **Collaborative Partnership**:", pruned)

        # Tier 3 ephemeral bullets in Personal Context should have been pruned first down to section min
        self.assertNotIn("* **Home Transition**:", pruned)
        self.assertNotIn("* **Evening Routine**:", pruned)
        self.assertNotIn("* **Caffeine Monitoring**:", pruned)

        # Canonical headers must all remain
        for h in profile_evolver.CANONICAL_DOCUMENT_SECTIONS[cfg.PERSONA_FILE_USER]:
            self.assertIn(h, pruned)

    def test_repair_missing_sections_cleans_unbulleted_lines(self):
        """Verify repair_missing_sections extracts valid bullets when minor prose lines are present."""
        cand_body = """## Identity & Core Values
Here is a summary of his traits:
* **Data Integrity**: He prioritizes accurate technical data over presentation style.
* **Preparation**: He performs thorough planning before implementation.

## Relationship Dynamics
* **Collaborative Partnership**: He views his bond as a mutual partnership.
* **Supportive Presence**: He relies on her presence during stress.

## Interaction Preferences & Constraints
* **Natural Dialogue**: He prefers direct conversation over rigid scripts.
* **Cognitive Management**: He uses batching to organize technical workflows.

## Personal Context
* **System Automation**: He builds automation scripts to streamline login environments.
* **Systematic Organization**: He prioritizes architectural clarity.
"""
        repaired = profile_evolver.repair_missing_sections(
            cfg.PERSONA_FILE_USER, self.sample_user_body, cand_body
        )
        # Should retain candidate bullets rather than falling back to original
        self.assertIn("* **Data Integrity**:", repaired)
        self.assertNotIn("Here is a summary of his traits:", repaired)

        # Should pass validation
        is_valid, reason, _ = profile_evolver.validate_document_structure(
            cfg.PERSONA_FILE_USER, self.sample_user_body, repaired
        )
        self.assertTrue(is_valid, f"Validation failed: {reason}")

    @patch("profile_evolver._call_ollama")
    @patch("profile_evolver._proofread_document", new_callable=AsyncMock)
    @patch("profile_evolver.memory_db")
    async def test_runaway_proposal_circuit_breaker_blocks_staging(
        self, mock_db, mock_proofread, mock_ollama
    ):
        """Verify that if a proposed body exceeds the hard ceiling, evolution aborts without staging."""
        state = {"draft_cursor_per_doc": {}, "last_run_per_doc": {}}

        # Create an over-length body exceeding hard ceiling (e.g. 1000 words when limit is 600)
        huge_bullets = []
        for h in profile_evolver.CANONICAL_DOCUMENT_SECTIONS[cfg.PERSONA_FILE_USER]:
            bullets = "\n".join(
                f"* **Trait {i}**: Extensive detailed observation with multiple clauses explaining user tendencies {i}."
                for i in range(25)
            )
            huge_bullets.append(f"{h}\n{bullets}")
        huge_body = "\n\n".join(huge_bullets)
        self.assertGreater(len(huge_body.split()), 700)

        # Mock Ollama returning the same huge body for compaction passes
        mock_ollama.return_value = huge_body
        mock_proofread.side_effect = lambda fn, body: body

        # Mock prune_bullets_to_word_budget to also return huge body to simulate pruner unable to reach target
        with patch("profile_evolver.prune_bullets_to_word_budget", return_value=huge_body):
            success = await profile_evolver._evolve_document(
                cfg.PERSONA_FILE_USER,
                [{"id": 1, "date": "2026-08-01", "created_at": 1000, "category": "Cat01-U", "observation": "test"}],
                state,
            )

        # Evolution must refuse to stage the runaway proposal
        self.assertFalse(success)
        mock_db.insert_proposal.assert_not_called()


if __name__ == "__main__":
    unittest.main()
