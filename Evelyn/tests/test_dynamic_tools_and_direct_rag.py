# test_dynamic_tools_and_direct_rag.py
# date created: 2026-09-01
# date modified: 2026-09-01
# tags: #test, #tools, #dynamic_tools, #rag, #query_reformulation

"""Unit tests for Dynamic Tool Surfacing, Intent Heuristics, and Direct Vector RAG."""

import os
import sys
import unittest

# Ensure repo root and Evelyn/tools are on python path
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
tools_dir = os.path.join(repo_root, "Evelyn/tools")
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

import evelyn_config as cfg
from Evelyn.tools.evelyn_tools import (
    CORE_TOOL_DEFINITIONS,
    MODEL_TOOL_DEFINITIONS,
    get_active_tools,
)
from Evelyn.tools.query_reformulator import clean_conversational_query, reformulate_query


class TestDynamicToolsAndDirectRAG(unittest.TestCase):
    def test_core_tools_default(self):
        """Verify default tool selection returns only core tools when no specialist triggers exist."""
        active = get_active_tools(user_message="Hello Evelyn, how are you tonight?")
        active_names = [t["function"]["name"] for t in active]

        self.assertEqual(len(active), len(cfg.CORE_TOOL_NAMES))
        for core_name in cfg.CORE_TOOL_NAMES:
            self.assertIn(core_name, active_names)

        # Specialist tools should not be in default conversational set
        self.assertNotIn("run_command", active_names)
        self.assertNotIn("manage_vault_list", active_names)
        self.assertNotIn("start_research", active_names)

    def test_intent_heuristic_vault_list(self):
        """Verify intent pattern activates manage_vault_list for grocery/todo list modifications."""
        msg = "Can you add sourdough bread and almond milk to my grocery list?"
        active = get_active_tools(user_message=msg)
        active_names = [t["function"]["name"] for t in active]

        self.assertIn("manage_vault_list", active_names)
        # Core tools still present
        self.assertIn("write_journal_entry", active_names)

    def test_intent_heuristic_run_command(self):
        """Verify intent pattern activates run_command when user requests terminal/shell execution."""
        msg = "Please run a bash script to test the camera connection"
        active = get_active_tools(user_message=msg)
        active_names = [t["function"]["name"] for t in active]

        self.assertIn("run_command", active_names)

    def test_intent_heuristic_calendar(self):
        """Verify intent pattern activates calendar tools when scheduling meetings."""
        msg = "Schedule a doctor appointment for next Thursday at 2pm"
        active = get_active_tools(user_message=msg)
        active_names = [t["function"]["name"] for t in active]

        self.assertIn("create_calendar_event", active_names)

    def test_procedure_coupled_tool_surfacing(self):
        """Verify tools declared in retrieved Procedure metadata or content are dynamically surfaced."""
        mock_procedures = [
            {
                "content": "## Deep Research Workflow\nUse `guide_research` to steer the subagents.",
                "metadata": {"tools": "start_research, inspect_research_task"},
            }
        ]
        active = get_active_tools(
            user_message="Let's study quantum dot displays",
            retrieved_procedures=mock_procedures,
        )
        active_names = [t["function"]["name"] for t in active]

        self.assertIn("start_research", active_names)
        self.assertIn("inspect_research_task", active_names)
        self.assertIn("guide_research", active_names)

    def test_clean_conversational_query_preamble_stripping(self):
        """Verify conversational filler and questions are cleanly stripped for vector search."""
        query1 = "Hey Evelyn, what were we planning for the solar battery and power inverter?"
        cleaned1 = clean_conversational_query(query1)
        self.assertEqual(cleaned1, "the solar battery and power inverter?")

        query2 = "Can you please check what tasks we have on our agenda for this week?"
        cleaned2 = clean_conversational_query(query2)
        self.assertEqual(cleaned2, "what tasks we have on our agenda for this week?")

        query3 = "Tell me about our trip and the passenger princess routine"
        cleaned3 = clean_conversational_query(query3)
        self.assertEqual(cleaned3, "our trip and the passenger princess routine")

    def test_reformulate_query_fast_path(self):
        """Verify reformulate_query bypasses LLM calls when RAG_REFORMULATE_ENABLED is False."""
        saved_flag = cfg.RAG_REFORMULATE_ENABLED
        try:
            cfg.RAG_REFORMULATE_ENABLED = False
            raw_msg = "Do you remember what we talked about regarding my sleep tracking and Oura ring stats?"
            result = reformulate_query(raw_msg)
            # Should return cleaned string without error
            self.assertIn("Oura ring stats", result)
            self.assertNotIn("Do you remember", result)
        finally:
            cfg.RAG_REFORMULATE_ENABLED = saved_flag


if __name__ == "__main__":
    unittest.main()
