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

import evelyn_config as cfg


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
                cfg.PERSONA_FILE_ASSISTANT: 1000.0,
                cfg.PERSONA_FILE_USER: 1000.0,
                cfg.PERSONA_FILE_DIRECTIVES: 1000.0,
            },
            "draft_cursor_per_doc": {},
            "last_status_per_doc": {
                cfg.PERSONA_FILE_ASSISTANT: {
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

        self.assertIn(cfg.PERSONA_FILE_ASSISTANT, statuses)
        asst_status = statuses[cfg.PERSONA_FILE_ASSISTANT]
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
                cfg.PERSONA_FILE_ASSISTANT: 1000.0,
                cfg.PERSONA_FILE_USER: 1000.0,
                cfg.PERSONA_FILE_DIRECTIVES: 1000.0,
            },
            "draft_cursor_per_doc": {},
            "last_status_per_doc": {},
        }

        mock_con = MagicMock()
        mock_cur = MagicMock()
        mock_get_db.return_value.__enter__.return_value = mock_con
        mock_con.cursor.return_value = mock_cur

        # Pending proposal exists for User_Profile.md
        mock_cur.fetchall.return_value = [{"suggested_category": cfg.PERSONA_FILE_USER}]
        mock_cur.fetchone.return_value = None

        statuses = profile_evolver.get_profile_evolution_statuses()

        self.assertIn(cfg.PERSONA_FILE_USER, statuses)
        self.assertEqual(statuses[cfg.PERSONA_FILE_USER]["code"], "PENDING_EXISTS")
        self.assertEqual(statuses[cfg.PERSONA_FILE_USER]["label"], "Skipped — Proposal Pending")

    @patch("profile_evolver._save_evolution_state")
    @patch("profile_evolver.memory_db.get_db")
    def test_prunes_legacy_system_directives_and_prevents_duplicate_statuses(
        self, mock_get_db, mock_save_state
    ):
        """Verify that get_profile_evolution_statuses strictly prunes legacy keys like System_Directives.md."""
        mock_con = MagicMock()
        mock_cur = MagicMock()
        mock_get_db.return_value.__enter__.return_value = mock_con
        mock_con.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = []
        mock_cur.fetchone.return_value = None

        with patch("profile_evolver._load_evolution_state") as mock_load_state:
            mock_load_state.return_value = {
                "last_run_per_doc": {
                    "Assistant_Profile.md": 1000.0,
                    "User_Profile.md": 1000.0,
                    "System_Directives.md": 1000.0,
                    "Assistant_Directives.md": 1000.0,
                },
                "draft_cursor_per_doc": {},
                "last_status_per_doc": {
                    "Assistant_Profile.md": {
                        "code": "COOLDOWN_ACTIVE",
                        "label": "Skipped — Cooldown Active",
                        "timestamp": 1000.0,
                    },
                    "User_Profile.md": {
                        "code": "COOLDOWN_ACTIVE",
                        "label": "Skipped — Cooldown Active",
                        "timestamp": 1000.0,
                    },
                    "System_Directives.md": {
                        "code": "COOLDOWN_ACTIVE",
                        "label": "Skipped — Cooldown Active",
                        "timestamp": 900.0,
                    },
                    "Assistant_Directives.md": {
                        "code": "COOLDOWN_ACTIVE",
                        "label": "Skipped — Cooldown Active",
                        "timestamp": 1000.0,
                    },
                },
            }

            statuses = profile_evolver.get_profile_evolution_statuses()

            self.assertNotIn("System_Directives.md", statuses)
            self.assertIn("Assistant_Directives.md", statuses)
            self.assertEqual(
                list(statuses.keys()),
                [
                    cfg.PERSONA_FILE_ASSISTANT,
                    cfg.PERSONA_FILE_USER,
                    cfg.PERSONA_FILE_DIRECTIVES,
                ],
            )
            self.assertTrue(mock_save_state.called)

    def test_load_evolution_state_migrates_legacy_keys_and_prunes_unknown(self):
        """Verify that _load_evolution_state migrates System_Directives.md to Assistant_Directives.md and strips unmanaged keys."""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_state_file = os.path.join(tmp_dir, "test_evolution_state.json")
            legacy_data = {
                "last_run_per_doc": {
                    "Assistant_Profile.md": 1000.0,
                    "User_Profile.md": 1000.0,
                    "System_Directives.md": 1500.0,
                    "Unknown_Document.md": 2000.0,
                },
                "draft_cursor_per_doc": {
                    "System_Directives.md": 500.0,
                },
                "last_status_per_doc": {
                    "System_Directives.md": {
                        "code": "COOLDOWN_ACTIVE",
                        "label": "Skipped — Cooldown Active",
                        "timestamp": 1500.0,
                        "details": "Legacy status",
                    },
                    "Unknown_Document.md": {
                        "code": "NEVER_RUN",
                        "label": "Never Run",
                        "timestamp": 0.0,
                    },
                },
            }
            with open(tmp_state_file, "w", encoding="utf-8") as f:
                json.dump(legacy_data, f)

            with patch.object(profile_evolver, "_STATE_FILE", tmp_state_file):
                loaded = profile_evolver._load_evolution_state()

                self.assertNotIn("System_Directives.md", loaded["last_run_per_doc"])
                self.assertNotIn("Unknown_Document.md", loaded["last_run_per_doc"])
                self.assertIn(cfg.PERSONA_FILE_DIRECTIVES, loaded["last_run_per_doc"])
                self.assertEqual(loaded["last_run_per_doc"][cfg.PERSONA_FILE_DIRECTIVES], 1500.0)

                self.assertNotIn("System_Directives.md", loaded["draft_cursor_per_doc"])
                self.assertEqual(loaded["draft_cursor_per_doc"][cfg.PERSONA_FILE_DIRECTIVES], 500.0)

                self.assertNotIn("System_Directives.md", loaded["last_status_per_doc"])
                self.assertNotIn("Unknown_Document.md", loaded["last_status_per_doc"])
                self.assertIn(cfg.PERSONA_FILE_DIRECTIVES, loaded["last_status_per_doc"])
                self.assertEqual(
                    loaded["last_status_per_doc"][cfg.PERSONA_FILE_DIRECTIVES]["details"],
                    "Legacy status",
                )


if __name__ == "__main__":
    unittest.main()
