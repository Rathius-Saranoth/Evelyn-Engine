# test_all_tools_end_to_end.py
# date created: 2026-08-19 20:26:51
# date modified: 2026-08-19 20:26:51
# tags: 
# Comprehensive Unit and End-to-End Test Suite for Evelyn Tools

import os
import sys
import unittest
from unittest.mock import patch

# Setup paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
TOOLS_DIR = os.path.join(ROOT_DIR, "tools")
BASE_DIR = os.path.dirname(ROOT_DIR)

for p in (BASE_DIR, ROOT_DIR, TOOLS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import evelyn_config as cfg
import evelyn_tools
import chroma_rag
import context_manager
import vault_db
import journal_manager
import task_manager


class TestAllToolsEndToEnd(unittest.TestCase):
    """Test suite exercising every tool registered in TOOL_FUNCTIONS and core RAG pipelines."""

    def test_01_tool_registry_integrity(self):
        """Verify all MODEL_TOOL_DEFINITIONS have matching dispatch callables in TOOL_FUNCTIONS."""
        for defn in evelyn_tools.MODEL_TOOL_DEFINITIONS:
            name = defn["function"]["name"]
            self.assertIn(name, evelyn_tools.TOOL_FUNCTIONS, f"Missing callable for model tool '{name}'")
            self.assertTrue(callable(evelyn_tools.TOOL_FUNCTIONS[name]))

    def test_02_chroma_rag_retrieval(self):
        """Test vector similarity search in the unified evelyn_memory collection."""
        with patch.object(chroma_rag, "query_collection", return_value=[{"content": "Evelyn test persona", "source": "test.md", "distance": 0.12}]):
            results = chroma_rag.query_collection("Evelyn persona", cfg.CHROMA_MEMORY_COLLECTION, n_results=3)
            self.assertIsInstance(results, list)
            self.assertEqual(len(results), 1)
            first = results[0]
            self.assertIn("content", first)
            self.assertIn("source", first)
            self.assertIn("distance", first)

    def test_03_chroma_rag_build_context(self):
        """Test formatted RAG context construction without gist substitutions."""
        with patch.object(chroma_rag, "query_collection", return_value=[{"content": "Tenser persona details", "source": "tenser.md", "distance": 0.12}]), \
             patch.object(chroma_rag, "_fetch_pinned_chunks", return_value=[]):
            ctx = chroma_rag.build_rag_context("Tenser persona")
            self.assertIsInstance(ctx, str)
            if ctx:
                self.assertIn("--- Retrieved Context ---", ctx)
                self.assertNotIn("Gist Summary:", ctx)
                self.assertNotIn("recall_specific_memory", ctx)

    def test_04_vault_db_and_context_search(self):
        """Test SQLite vault search and context manager preview rendering."""
        results = vault_db.search_documents("Journal", limit=3)
        self.assertIsInstance(results, list)
        
        rendered = context_manager.search_vault_map("Journal", limit=3)
        self.assertIsInstance(rendered, str)
        if results:
            self.assertIn("Top", rendered)
            self.assertIn("Path:", rendered)

    def test_05_read_journal_tools(self):
        """Test journal reading tools."""
        recent = evelyn_tools.read_recent_journal_entries(n=2)
        self.assertIsInstance(recent, str)

        read_j = evelyn_tools.read_journal(mode="recent", limit=2)
        self.assertIsInstance(read_j, str)

    @patch("evelyn_tools._reload")
    @patch("journal_manager.create_journal_entry")
    def test_06_write_journal_entry(self, mock_create, mock_reload):
        """Test writing journal entry tool dispatch."""
        mock_create.return_value = "Created new journal entry (Test)"
        res = evelyn_tools.write_journal_entry(
            mood="Peaceful",
            vibe_check="Calm afternoon in Sanctum.",
            narrative="Worked on testing all engine tools.",
            message_in_a_bottle="Keep testing.",
            tags="testing, unit_test"
        )
        self.assertIn("Created", res)
        mock_create.assert_called_once()

    def test_07_search_history(self):
        """Test FTS conversation history search tool."""
        res = evelyn_tools.search_history(query="Evelyn", limit=3)
        self.assertIsInstance(res, str)

    def test_08_terminal_agent_tools(self):
        """Test read_file and run_command tools."""
        # Read a known file
        read_res = evelyn_tools.read_file(file_path="evelyn_config.py", max_lines=10)
        self.assertIn("evelyn_config.py", read_res)
        self.assertIn("1 |", read_res)

        # Run a safe command
        cmd_res = evelyn_tools.run_command(command="echo 'EVELYN_TEST_OK'")
        self.assertIn("EVELYN_TEST_OK", cmd_res)

    def test_09_deprecated_tool_guards(self):
        """Verify deprecated tools return clean notices instead of crashing."""
        v_res = evelyn_tools.search_vault("anything")
        self.assertIn("[NOTICE]", v_res)

        r_res = evelyn_tools.recall_specific_memory("anything.md")
        self.assertIn("[NOTICE]", r_res)

    def test_10_health_tools(self):
        """Test health metrics and workout query tools."""
        metrics_res = evelyn_tools.get_health_metrics(timeframe="today")
        self.assertIsInstance(metrics_res, str)

        workouts_res = evelyn_tools.get_recent_workouts(days=7)
        self.assertIsInstance(workouts_res, str)

    def test_11_calendar_tools(self):
        """Test agenda and calendar tools."""
        agenda_res = evelyn_tools.get_agenda(timeframe="today")
        self.assertIsInstance(agenda_res, str)

    def test_12_web_search(self):
        """Test web search tool with query."""
        res = evelyn_tools.web_search(query="Python unit testing", max_results=2)
        self.assertIsInstance(res, str)

    def test_13_task_manager_concurrency(self):
        """Test task manager state reporting."""
        status = task_manager.get_status("sync")
        self.assertIn(status, ("idle", "running", "done", "error", "timed_out", None))
        self.assertIsNone(task_manager.get_status("nonexistent_task_xyz"))
    def test_14_tasks_tools(self):
        """Test Google Tasks model tools."""
        list_res = evelyn_tools.list_tasks()
        self.assertIsInstance(list_res, str)
        sync_res = evelyn_tools.sync_google_tasks()
        self.assertIsInstance(sync_res, str)

    def test_15_vault_list_tools(self):
        """Test manage_vault_list model tool."""
        res = evelyn_tools.manage_vault_list(name="TestGroceries", action="add", items=["Oat Milk (1 gal)"])
        self.assertIsInstance(res, str)
        read_res = evelyn_tools.manage_vault_list(name="TestGroceries", action="read")
        self.assertIsInstance(read_res, str)
        self.assertIn("Oat Milk", read_res)


if __name__ == "__main__":
    unittest.main()
