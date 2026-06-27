# test_terminal_agent.py
# date created: 2026-06-27 09:38:56
# date modified: 2026-06-27 09:39:18
# tags: 

# Evelyn/tests/test_terminal_agent.py
# date created: 2026-06-27 16:00:00
# date modified: 2026-06-27 16:00:00
# tags: #test, #verification, #terminal, #security

"""Unit tests for the Evelyn Terminal Agent safety and execution logic.

Verifies path scoping, pattern blocking, approval gating, and execution matching
functions for terminal commands and file system access.
"""

import sys
import os
import unittest
from unittest.mock import patch, mock_open

# Add Evelyn/tools and root to system path
sys.path.append(r"C:\Projects\LocalAI")
sys.path.append(r"C:\Projects\LocalAI\Evelyn\tools")

import terminal_agent


class TestTerminalAgent(unittest.TestCase):
    """Test suite for security verification and execution of terminal/file actions."""

    def setUp(self):
        """Reset pending approvals before each test."""
        terminal_agent._pending_approvals.clear()

    @patch("importlib.reload")
    @patch("terminal_agent.cfg")
    def test_path_scoping(self, mock_cfg, mock_reload):
        """Verify that paths outside the allowed list are blocked."""
        mock_cfg.TERMINAL_ALLOWED_PATHS = [
            r"C:\Projects\LocalAI",
            r"C:\Temp",
        ]

        # Allowed paths
        self.assertTrue(terminal_agent.is_path_allowed(r"C:\Projects\LocalAI"))
        self.assertTrue(terminal_agent.is_path_allowed(r"C:\Projects\LocalAI\subfolder"))
        self.assertTrue(terminal_agent.is_path_allowed(r"C:\Temp\file.txt"))

        # Blocked paths
        self.assertFalse(terminal_agent.is_path_allowed(r"C:\Windows"))
        self.assertFalse(terminal_agent.is_path_allowed(r"C:\Users\ricky\Documents"))
        # Path traversal checks
        self.assertFalse(terminal_agent.is_path_allowed(r"C:\Projects\LocalAI\..\..\Windows"))

    def test_blocked_patterns(self):
        """Verify that dangerous blocked commands are instantly rejected."""
        # dangerous pattern
        cmd = "format c: /fs:NTFS"
        res = terminal_agent.run_command(cmd)
        self.assertIn("blocked by safety filter", res)

        # command injection del /s /q
        cmd = "del /s /q C:\\Projects\\LocalAI"
        res = terminal_agent.run_command(cmd)
        self.assertIn("blocked by safety filter", res)

    def test_approval_requirement(self):
        """Verify that command matching approval patterns returns approval warnings."""
        cmd = "pip install --user requests"
        res = terminal_agent.run_command(cmd)
        self.assertIn("requires approval before execution", res)
        self.assertIn("Approval ID: cmd_", res)
        self.assertEqual(len(terminal_agent._pending_approvals), 1)


    def test_auto_approval_override(self):
        """Verify that safe patterns override approval requirements."""
        cmd = "git status"
        # git status matches git (push|reset|rebase) for "git ", but matches safe pattern ^git (status|log|diff)
        # Mock subprocess.run to verify it tries to execute instead of staging for approval
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "On branch main"
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0
            
            res = terminal_agent.run_command(cmd)
            self.assertNotIn("requires approval", res)
            self.assertEqual(len(terminal_agent._pending_approvals), 0)

    @patch("builtins.open", new_callable=mock_open, read_data="line 1\nline 2")
    def test_read_file_allowed(self, mock_file):
        """Verify reading file inside allowed paths works."""
        res = terminal_agent.read_file("C:\\Projects\\LocalAI\\test.txt")
        self.assertIn("line 1", res)
        mock_file.assert_called_with("C:\\Projects\\LocalAI\\test.txt", "r", encoding="utf-8")

    def test_read_file_blocked(self):
        """Verify reading file outside allowed paths is blocked."""
        res = terminal_agent.read_file("C:\\Windows\\system.ini")
        self.assertIn("outside allowed paths", res)

    def test_write_file_approval_staging(self):
        """Verify writing a file always stages for approval."""
        res = terminal_agent.write_file("C:\\Projects\\LocalAI\\new.py", "print('hello')", mode="overwrite")
        self.assertIn("File write requires approval", res)
        self.assertIn("Approval ID: write_", res)
        self.assertEqual(len(terminal_agent._pending_approvals), 1)


if __name__ == "__main__":
    unittest.main()
