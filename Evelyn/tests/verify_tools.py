# verify_tools.py
# date created: 2026-05-18
# date modified: 2026-05-25 19:50:50
# tags: #test, #verification, #tools, #testing, #assertion

import sys
import os
import unittest
from unittest.mock import patch, mock_open

# Add tools to path
sys.path.append(r"C:\Projects\LocalAI\Evelyn\tools")
import journal_manager
import context_manager


class TestEvelynTools(unittest.TestCase):
    @patch("builtins.open", new_callable=mock_open)
    @patch("os.path.exists")
    @patch("os.makedirs")
    def test_journal_creation(self, mock_makedirs, mock_exists, mock_file):
        mock_exists.return_value = False

        result = journal_manager.create_journal_entry(
            vibe_check="Feeling great",
            narrative="Test Content",
            message_in_a_bottle="Closing thought",
            mood="Happy",
            tags=["#test"]
        )

        self.assertIn("Created new", result)
        handle = mock_file()
        handle.write.assert_called()
        written_content = handle.write.call_args[0][0]
        self.assertIn("mood: Happy", written_content)
        self.assertIn("CY-", written_content)

    @patch("memory_db.insert_entry")
    def test_context_log(self, mock_insert):
        mock_insert.return_value = 123

        result = context_manager.append_context_log("Cat01-R", "Test Summary")

        self.assertIn("Created Context Entry (ID: 123)", result)
        mock_insert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
