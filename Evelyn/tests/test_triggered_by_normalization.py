# test_triggered_by_normalization.py
# date created: 2026-07-17
# tags: #test, #verification, #research

import sys
import os
import unittest
import asyncio
from unittest.mock import patch, mock_open, MagicMock

# Add directories to path
sys.path.append(r"/home/rathius/evelyn")
sys.path.append(r"/home/rathius/evelyn/Evelyn/tools")
sys.path.append(r"/home/rathius/evelyn/scripts")
sys.path.append(r"/home/rathius/evelyn/scripts/archive")

class TestTriggeredByNormalization(unittest.TestCase):
    @patch("builtins.open", new_callable=mock_open)
    @patch("os.path.exists")
    @patch("os.makedirs")
    @patch("subprocess.Popen")
    @patch("research_engine.get_task_dir")
    @patch("research_engine.call_ollama")
    @patch("research_engine.datetime")
    def test_research_engine_triggered_by_evelyn(self, mock_datetime, mock_call_ollama, mock_get_task_dir, mock_popen, mock_makedirs, mock_exists, mock_open_file):
        import research_engine

        # Mock datetime to return a static date
        mock_dt = MagicMock()
        mock_dt.strftime.return_value = "2026-07-17 12:00:00"
        mock_datetime.datetime.now.return_value = mock_dt

        mock_get_task_dir.return_value = "dummy_dir"
        mock_exists.return_value = False
        
        # Mock call_ollama to return a dummy report with yaml frontmatter
        mock_call_ollama.return_value = "---\nconfidence: 50%\nshort_title: Dummy Report\ntopic_tags: [test]\n---\nReport body content."

        state = {
            "query": "dummy query",
            "scope": "standard",
            "confidence": 50,
            "total_sources": 5,
            "triggered_by": "evelyn",
            "sources_registry": [],
            "plan": {"sub_questions": []},
            "ollama_calls": 0,
            "confidence_threshold": 80,
        }

        # Run step_synthesize
        asyncio.run(research_engine.step_synthesize("dummy_task_id", state))

        # Check what was written to file
        handle = mock_open_file()
        handle.write.assert_called()
        written_content = ""
        for call in handle.write.call_args_list:
            written_content += call[0][0]

        # Verify that triggered_by is "Evelyn" (proper case)
        self.assertIn("triggered_by: Evelyn", written_content)
        self.assertNotIn("triggered_by: evelyn\n", written_content)

    @patch("migrate_research_headers.parse_existing_file")
    def test_migrate_research_headers_triggered_by_evelyn(self, mock_parse):
        import migrate_research_headers

        metadata = {
            "title": "Test Report",
            "aliases": ["test-report"],
            "date created": "2026-07-17 12:00:00",
            "research_task_id": "task_123",
            "scope": "standard",
            "source_count": 3,
            "confidence": 90,
            "triggered_by": "evelyn",
            "tags": ["test"]
        }
        mock_parse.return_value = (metadata, "Report Body")

        with patch("builtins.open", new_callable=mock_open) as mock_file:
            asyncio.run(migrate_research_headers.migrate_file("dummy_path.md"))
            
            handle = mock_file()
            handle.write.assert_called()
            written_content = ""
            for call in handle.write.call_args_list:
                written_content += call[0][0]
                
            self.assertIn("triggered_by: Evelyn", written_content)
            self.assertNotIn("triggered_by: evelyn\n", written_content)

    @patch("migrate_research_filenames.parse_existing_file")
    def test_migrate_research_filenames_triggered_by_evelyn(self, mock_parse):
        import migrate_research_filenames

        metadata = {
            "title": "Test Report",
            "research_query": "Test Query",
            "date created": "2026-07-17 12:00:00",
            "research_task_id": "task_123",
            "scope": "standard",
            "source_count": 3,
            "confidence": 90,
            "triggered_by": "evelyn",
            "tags": ["test"]
        }
        mock_parse.return_value = (metadata, "Report Body")

        with patch("builtins.open", new_callable=mock_open) as mock_file:
            with patch("os.rename") as mock_rename:
                with patch("os.path.exists", return_value=False):
                    migrate_research_filenames.migrate_file("dummy_path.md")
                    
                    handle = mock_file()
                    handle.write.assert_called()
                    written_content = ""
                    for call in handle.write.call_args_list:
                        written_content += call[0][0]
                        
                    self.assertIn("triggered_by: \"Evelyn\"", written_content)
                    self.assertNotIn("triggered_by: \"evelyn\"", written_content)

if __name__ == "__main__":
    unittest.main()
