# test_terminal_agent.py
# date created: 2026-06-27 09:38:56
# date modified: 2026-06-30T01:18:00Z
# tags: #test, #verification, #terminal, #security

"""Unit tests for the Evelyn Terminal Agent safety, persistence, and execution logic.

Verifies path scoping, pattern blocking, approval gating, persistent storage,
approved execution, and status query functions for terminal commands and file system access.
"""

import sys
import os
import time
import tempfile
import shutil
import unittest
from unittest.mock import patch, mock_open

# Add Evelyn/tools and root to system path
sys.path.append(r"/home/rathius/evelyn")
sys.path.append(r"/home/rathius/evelyn/Evelyn/tools")

import terminal_agent


class TestTerminalAgent(unittest.TestCase):
    """Test suite for security verification and execution of terminal/file actions."""

    def setUp(self):
        """Set up a temporary directory and redirect the approvals storage path."""
        self.test_dir = tempfile.mkdtemp()
        self.test_approvals_file = os.path.join(self.test_dir, "test_approvals.json")
        self.original_approvals_file = terminal_agent.APPROVALS_FILE
        terminal_agent.APPROVALS_FILE = self.test_approvals_file

    def tearDown(self):
        """Clean up the temporary directory and restore the original storage path."""
        terminal_agent.APPROVALS_FILE = self.original_approvals_file
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("importlib.reload")
    @patch("terminal_agent.cfg")
    def test_path_scoping(self, mock_cfg, mock_reload):
        """Verify that paths outside the allowed list are blocked."""
        mock_cfg.TERMINAL_ALLOWED_PATHS = [
            r"/home/rathius/evelyn",
            r"/tmp",
        ]

        # Allowed paths
        self.assertTrue(terminal_agent.is_path_allowed(r"/home/rathius/evelyn"))
        self.assertTrue(terminal_agent.is_path_allowed(r"/home/rathius/evelyn/subfolder"))
        self.assertTrue(terminal_agent.is_path_allowed(r"/tmp/file.txt"))

        # Blocked paths
        self.assertFalse(terminal_agent.is_path_allowed(r"/etc"))
        self.assertFalse(terminal_agent.is_path_allowed(r"/home/otheruser/Documents"))
        # Path traversal checks
        self.assertFalse(terminal_agent.is_path_allowed(r"/home/rathius/evelyn/../../etc"))

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
        
        # Verify it was saved to the persistent test file
        pending = terminal_agent.get_pending_approvals()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["command"], cmd)
        self.assertEqual(pending[0]["status"], "pending")

    def test_auto_approval_override(self):
        """Verify that safe patterns override approval requirements."""
        cmd = "git status"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "On branch main"
            mock_run.return_value.stderr = ""
            mock_run.return_value.returncode = 0
            
            res = terminal_agent.run_command(cmd)
            self.assertNotIn("requires approval", res)
            self.assertEqual(len(terminal_agent.get_pending_approvals()), 0)

    @patch("builtins.open", new_callable=mock_open, read_data="line 1\nline 2")
    def test_read_file_allowed(self, mock_file):
        """Verify reading file inside allowed paths works."""
        # Patch is_path_allowed to return True for test file
        with patch("terminal_agent.is_path_allowed", return_value=True):
            res = terminal_agent.read_file("C:\\Projects\\LocalAI\\test.txt")
            self.assertIn("line 1", res)

    def test_read_file_blocked(self):
        """Verify reading file outside allowed paths is blocked."""
        res = terminal_agent.read_file("C:\\Windows\\system.ini")
        self.assertIn("outside allowed paths", res)

    def test_write_file_approval_staging(self):
        """Verify writing a file always stages for approval."""
        res = terminal_agent.write_file("C:\\Projects\\LocalAI\\new.py", "print('hello')", mode="overwrite")
        self.assertIn("File write requires approval", res)
        self.assertIn("Approval ID: write_", res)
        
        pending = terminal_agent.get_pending_approvals()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["type"], "write")

    def test_approve_and_execute_write(self):
        """Verify that approving a staged write writes the file and records success."""
        test_file = os.path.join(self.test_dir, "test_write.txt")
        # Ensure test_file path is allowed
        with patch("terminal_agent.is_path_allowed", return_value=True):
            res = terminal_agent.write_file(test_file, "Hello persistent approval!", mode="overwrite")
            approval_id = res.split("Approval ID: ")[1].split("\n")[0]
            
            # Execute approval
            approve_res = terminal_agent.approve_command(approval_id)
            self.assertIn("[Success] File written to", approve_res)
            
            # Verify file contents on disk
            with open(test_file, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertEqual(content, "Hello persistent approval!")
            
            # Verify status in DB
            status = terminal_agent.get_approval_status(approval_id)
            self.assertEqual(status["status"], "approved")

    def test_deny_command(self):
        """Verify that denying a command marks its status as denied."""
        res = terminal_agent.write_file("C:\\Projects\\LocalAI\\test.py", "print(1)", mode="overwrite")
        approval_id = res.split("Approval ID: ")[1].split("\n")[0]
        
        deny_res = terminal_agent.deny_command(approval_id)
        self.assertEqual(deny_res, "Command denied.")
        
        status = terminal_agent.get_approval_status(approval_id)
        self.assertEqual(status["status"], "denied")

    def test_cleanup_stale_approvals(self):
        """Verify stale approvals are expired and aged records are purged."""
        approvals = {
            "old_pending": {
                "type": "command",
                "command": "git push",
                "cwd": "C:\\Projects\\LocalAI",
                "timeout": 30,
                "created_at": time.time() - 700, # older than 10 mins
                "status": "pending"
            },
            "very_old_approved": {
                "type": "command",
                "command": "git log",
                "cwd": "C:\\Projects\\LocalAI",
                "timeout": 30,
                "created_at": time.time() - 8 * 86400, # older than 7 days
                "status": "approved"
            },
            "recent_pending": {
                "type": "command",
                "command": "git status",
                "cwd": "C:\\Projects\\LocalAI",
                "timeout": 30,
                "created_at": time.time() - 50, # recent
                "status": "pending"
            }
        }
        terminal_agent._save_approvals(approvals)
        
        terminal_agent.cleanup_stale_approvals()
        
        updated = terminal_agent._load_approvals()
        # "old_pending" should be expired
        self.assertEqual(updated["old_pending"]["status"], "expired")
        # "very_old_approved" should be deleted
        self.assertNotIn("very_old_approved", updated)
        # "recent_pending" should remain pending
        self.assertEqual(updated["recent_pending"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
