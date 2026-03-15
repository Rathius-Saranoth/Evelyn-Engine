import sys
import os
import unittest
from unittest.mock import patch, mock_open

# Add tools to path
sys.path.append(r"C:\Projects\LocalAI\Evelyn\tools")
import journal_manager
import context_manager


class TestEvelynTools(unittest.TestCase):
    @patch("subprocess.run")
    @patch("subprocess.check_output")
    def test_journal_creation(self, mock_check_output, mock_run):
        mock_check_output.return_value = b"Obsidian.exe"  # Assume it's running

        # Configure mock_run to return a successful run object to simulate creation
        mock_result = unittest.mock.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Created new entry"
        mock_run.return_value = mock_result

        result = journal_manager.create_journal_entry(
            "Test Content", "Happy", ["#test"]
        )

        self.assertIn("Created new entry", result)
        # Check if subprocess.run was called with 'obsidian' and 'create'
        mock_run.assert_called()
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "obsidian")

        # The first call might be append and the second call might be create
        # But we assert that created new entry happened
        create_call = None
        for call in mock_run.call_args_list:
            if "create" in call[0][0]:
                create_call = call[0][0]
                break

        self.assertIsNotNone(create_call)
        self.assertTrue(any("mood: Happy" in str(arg) for arg in create_call))

    @patch("builtins.open", new_callable=mock_open)
    @patch("os.listdir")
    def test_context_log(self, mock_listdir, mock_file):
        mock_listdir.return_value = ["Cat01 - Core Identity.md"]

        result = context_manager.append_context_log("Cat01", "Test Summary")

        self.assertIn("Log appended to Cat01 - Core Identity.md", result)
        handle = mock_file()
        handle.write.assert_called()
        written_content = handle.write.call_args[0][0]
        self.assertIn("Primary: [[Cat01]]", written_content)
        self.assertIn("| Summary: Test Summary", written_content)


if __name__ == "__main__":
    unittest.main()
