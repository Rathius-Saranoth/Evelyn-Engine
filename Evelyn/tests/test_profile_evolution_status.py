# test_profile_evolution_status.py
# date created: 2026-09-07
# tags: #test, #profile_evolver, #status, #reconciliation

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
tools_dir = os.path.join(repo_root, "Evelyn/tools")
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

import profile_evolver


class TestProfileEvolutionStatusReconciliation(unittest.TestCase):
    @patch("profile_evolver._save_evolution_state")
    @patch("profile_evolver._load_evolution_state")
    @patch("profile_evolver.memory_db.get_db")
    def test_reconciles_stale_staged_status_when_proposal_applied(
        self, mock_get_db, mock_load_state, mock_save_state
    ):
        """When state says PROPOSAL_STAGED but DB has no pending and latest proposal is applied,

        get_profile_evolution_statuses must heal the record to APPROVED.
        """
        mock_load_state.return_value = {
            "last_run_per_doc": {
                "Assistant_Profile.md": 1000.0,
                "User_Profile.md": 1000.0,
                "System_Directives.md": 1000.0,
            },
            "draft_cursor_per_doc": {},
            "last_status_per_doc": {
                "Assistant_Profile.md": {
                    "code": "PROPOSAL_STAGED",
                    "label": "Proposal Pending Approval",
                    "timestamp": 1050.0,
                    "details": "Proposal staged (50 entries)",
                }
            },
        }

        # Mock DB connection
        mock_con = MagicMock()
        mock_cur = MagicMock()
        mock_get_db.return_value.__enter__.return_value = mock_con
        mock_con.cursor.return_value = mock_cur

        # 1. No pending proposals in DB
        mock_cur.fetchall.return_value = []

        # 2. Latest proposal for Assistant_Profile.md is applied
        mock_cur.fetchone.side_effect = [
            {"status": "applied", "reviewed_at": 1200.0, "created_at": 1050.0},
            None,
            None,
        ]

        statuses = profile_evolver.get_profile_evolution_statuses()

        self.assertIn("Assistant_Profile.md", statuses)
        asst_status = statuses["Assistant_Profile.md"]
        self.assertEqual(asst_status["code"], "APPROVED")
        self.assertEqual(asst_status["label"], "Profile Updated & Applied")
        self.assertEqual(asst_status["timestamp"], 1200.0)
        self.assertTrue(mock_save_state.called)

    @patch("profile_evolver._save_evolution_state")
    @patch("profile_evolver._load_evolution_state")
    @patch("profile_evolver.memory_db.get_db")
    def test_reconciles_active_pending_proposal(
        self, mock_get_db, mock_load_state, mock_save_state
    ):
        """When DB has a pending proposal, status is reported as PENDING_EXISTS."""
        mock_load_state.return_value = {
            "last_run_per_doc": {
                "Assistant_Profile.md": 1000.0,
                "User_Profile.md": 1000.0,
                "System_Directives.md": 1000.0,
            },
            "draft_cursor_per_doc": {},
            "last_status_per_doc": {},
        }

        mock_con = MagicMock()
        mock_cur = MagicMock()
        mock_get_db.return_value.__enter__.return_value = mock_con
        mock_con.cursor.return_value = mock_cur

        # Pending proposal exists for User_Profile.md
        mock_cur.fetchall.return_value = [{"suggested_category": "User_Profile.md"}]
        mock_cur.fetchone.return_value = None

        statuses = profile_evolver.get_profile_evolution_statuses()

        self.assertIn("User_Profile.md", statuses)
        self.assertEqual(statuses["User_Profile.md"]["code"], "PENDING_EXISTS")
        self.assertEqual(statuses["User_Profile.md"]["label"], "Skipped — Proposal Pending")


if __name__ == "__main__":
    unittest.main()
