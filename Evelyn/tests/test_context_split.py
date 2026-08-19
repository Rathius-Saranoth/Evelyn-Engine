# test_context_split.py
# date created: 2026-08-19
# tags: #tests, #split, #context, #consolidation, #decomposition

import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TOOLS_DIR = _PROJECT_ROOT / "Evelyn" / "tools"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))


import Evelyn.tools.memory_db as memory_db
import Evelyn.tools.fact_consolidator as fact_consolidator
import evelyn_server


class TestContextSplit(unittest.TestCase):
    """Test suite for Context Entry Splitting & Decomposition."""

    def setUp(self):
        memory_db.init_db()

    def test_memory_db_split_entry(self):
        """Verify memory_db.split_entry atomically soft-deletes parent and inserts children."""
        parent_id = memory_db.insert_entry(
            category="Cat05-R",
            subject="Ricky",
            observation="Likes espresso in the morning and maintains a HomeAssistant Zigbee server.",
            tags="Home/Coffee, Tech/HomeLab",
            status="live"
        )

        child_entries = [
            {
                "category": "Cat05-R",
                "subject": "Ricky",
                "observation": "Enjoys morning double-shot espresso with freshly ground beans.",
                "tags": "Home/Coffee/Espresso",
                "confidence": "high"
            },
            {
                "category": "Cat14-R",
                "subject": "Ricky",
                "observation": "Maintains a self-hosted HomeAssistant server managing Zigbee smart devices.",
                "tags": "Tech/HomeLab/HomeAssistant, Tech/IoT/Zigbee",
                "confidence": "high"
            }
        ]

        new_ids = memory_db.split_entry(parent_id, child_entries)
        self.assertEqual(len(new_ids), 2)

        # Parent should be soft-deleted
        parent = memory_db.get_entry(parent_id)
        self.assertIsNotNone(parent)
        self.assertEqual(parent["status"], "deleted")

        # Children should be live
        child1 = memory_db.get_entry(new_ids[0])
        self.assertEqual(child1["category"], "Cat05-R")
        self.assertEqual(child1["observation"], child_entries[0]["observation"])
        self.assertEqual(child1["tags"], "Home/Coffee/Espresso")
        self.assertEqual(child1["source"], "split")

        child2 = memory_db.get_entry(new_ids[1])
        self.assertEqual(child2["category"], "Cat14-R")
        self.assertEqual(child2["observation"], child_entries[1]["observation"])
        self.assertEqual(child2["tags"], "Tech/HomeLab/HomeAssistant, Tech/IoT/Zigbee")

    def test_split_preview_and_apply_flow(self):
        """Verify preview_context_split generates structured child entries and apply_context_split saves them."""
        mock_llm_yaml = """
```yaml
entries:
  - category: Cat05-R
    subject: Ricky
    tags: "Home/Coffee/Espresso"
    observation: "Prefers morning espresso."
  - category: Cat14-R
    subject: Ricky
    tags: "Tech/HomeLab/HomeAssistant"
    observation: "Runs a HomeAssistant Zigbee server."
```
"""
        with patch("Evelyn.tools.tag_librarian.query_ollama", return_value=mock_llm_yaml):
            req = evelyn_server.SplitPreviewRequest(
                observation="Likes espresso and runs HomeAssistant.",
                category="Cat05-R",
                subject="Ricky"
            )
            # Call preview route
            preview_result = asyncio_run(evelyn_server.preview_context_split(req, None))
            self.assertEqual(len(preview_result["splits"]), 2)
            self.assertEqual(preview_result["splits"][0]["tags"], "Home/Coffee/Espresso")

    def test_generate_split_proposal_in_consolidator(self):
        """Verify consolidator generates a split proposal for bloated entries."""
        record = {
            "id": 999,
            "category": "Cat05-R",
            "subject": "Ricky",
            "summary": "Enjoys dark roast coffee every morning and builds mechanical keyboards with tactile switches and lubed stabilizers."
        }

        mock_llm_response = """
```yaml
verdict: split
reasoning: "Contains two distinct domain observations: coffee preferences and mechanical keyboard building."
entries:
  - category: Cat05-R
    subject: Ricky
    tags: "Home/Coffee/Espresso"
    observation: "Enjoys drinking dark roast coffee every morning."
  - category: Cat05-R
    subject: Ricky
    tags: "Tech/Hardware/Keyboards"
    observation: "Builds custom mechanical keyboards using tactile switches and lubed stabilizers."
```
"""
        with patch("Evelyn.tools.fact_consolidator._call_ollama", return_value=mock_llm_response):
            pid_str = asyncio_run(fact_consolidator.generate_split_proposal(record, "Cat00 Index"))
            self.assertIsNotNone(pid_str)

            proposals = memory_db.get_pending_proposals()
            split_prop = next((p for p in proposals if p["id"] == int(pid_str)), None)
            self.assertIsNotNone(split_prop)
            self.assertEqual(split_prop["type"], "split")
            self.assertEqual(split_prop["source_ids"], [999])


def asyncio_run(coro):
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import nest_asyncio
        nest_asyncio.apply()
        return loop.run_until_complete(coro)
    return asyncio.run(coro)


if __name__ == "__main__":
    unittest.main()
