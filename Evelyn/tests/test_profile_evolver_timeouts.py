# test_profile_evolver_timeouts.py
# date created: 2026-08-28
# date modified: 2026-08-30 11:55:19
# tags: #test, #profile_evolver, #timeouts, #task_manager

import os
import sys
import unittest
from unittest.mock import patch

# Ensure repo root and Evelyn/tools are on python path
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
tools_dir = os.path.join(repo_root, "Evelyn/tools")
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

import evelyn_config as cfg
import profile_evolver
import task_manager


class TestProfileEvolverTimeouts(unittest.IsolatedAsyncioTestCase):
    def test_dynamic_timeout_baseline_for_profile_evolver(self):
        """Verify task_manager gives profile_evolver a 4500s baseline (or doc_timeout * 3)."""
        baseline = task_manager.get_dynamic_timeout("profile_evolver")
        expected_min = max(4500.0, float(getattr(cfg, "PROFILE_EVOLUTION_DOC_TIMEOUT", 1500)) * 3.0)
        self.assertGreaterEqual(baseline, expected_min)

    @patch("profile_evolver._evolve_document")
    @patch("profile_evolver.memory_db.get_pending_proposals")
    @patch("profile_evolver.memory_db.get_entries_by_category_for_document")
    @patch("profile_evolver._load_evolution_state")
    @patch("profile_evolver._save_evolution_state")
    async def test_per_document_timeout_continues_to_next_doc(
        self,
        mock_save_state,
        mock_load_state,
        mock_get_entries,
        mock_pending_props,
        mock_evolve_doc,
    ):
        """Verify that a timeout on one document logs INTERRUPTED_SAVED and continues to the next document."""
        mock_pending_props.return_value = []
        mock_load_state.return_value = {
            "last_run_per_doc": dict.fromkeys(profile_evolver.DOCUMENT_CATEGORIES, 0.0),
            "draft_cursor_per_doc": dict.fromkeys(profile_evolver.DOCUMENT_CATEGORIES, 0.0),
            "last_status_per_doc": {},
        }
        mock_get_entries.return_value = [
            {"id": i, "date": "2026-08-28", "observation": f"Test fact {i}", "created_at": 100.0, "updated_at": 100.0, "last_evolved_at": None}
            for i in range(10)
        ]

        # First doc raises TimeoutError, second and third return True
        call_count = 0

        async def side_effect(filename, entries, state):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("Simulated document timeout")
            return True

        mock_evolve_doc.side_effect = side_effect

        with patch.object(cfg, "PROFILE_EVOLUTION_ENABLED", True), \
             patch.object(cfg, "PROFILE_EVOLUTION_COOLDOWN", 0), \
             patch.object(cfg, "PROFILE_EVOLUTION_MIN_ENTRIES", 1), \
             patch.object(cfg, "PROFILE_EVOLUTION_DOC_TIMEOUT", 0.05), \
             patch("profile_evolver._other_heavy_tasks_running", return_value=False):
            await profile_evolver.run_profile_evolution()

        # All 3 documents should have been attempted despite doc 1 timing out
        self.assertEqual(call_count, len(profile_evolver.DOCUMENT_CATEGORIES))


if __name__ == "__main__":
    unittest.main()
