# test_context_split.py
# date created: 2026-08-19
# date modified: 2026-10-16 00:13:20
# tags: #tests, #split, #context, #consolidation, #decomposition

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_TOOLS_DIR = _PROJECT_ROOT / "Evelyn" / "tools"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))


import evelyn_server
from Evelyn.tools import fact_consolidator, memory_db


class TestContextSplit(unittest.TestCase):
    """Test suite for Context Entry Splitting & Decomposition."""

    def setUp(self):
        memory_db.init_db()
        self.created_entry_ids = []
        self.created_proposal_ids = []

    def tearDown(self):
        import sqlite3

        import evelyn_config as cfg
        conn = sqlite3.connect(cfg.MEMORY_DB_PATH)
        cur = conn.cursor()
        if self.created_entry_ids:
            cur.execute(f"DELETE FROM context_entries WHERE id IN ({','.join(map(str, self.created_entry_ids))})")
        if self.created_proposal_ids:
            cur.execute(f"DELETE FROM proposals WHERE id IN ({','.join(map(str, self.created_proposal_ids))})")
        conn.commit()
        conn.close()

    def test_memory_db_split_entry(self):
        """Verify memory_db.split_entry atomically soft-deletes parent and inserts children."""
        parent_id = memory_db.insert_entry(
            category="Cat05-U",
            subject="Alex",
            observation="Likes espresso in the morning and maintains a HomeAssistant Zigbee server.",
            tags="Home/Coffee, Tech/HomeLab",
            status="live"
        )
        self.created_entry_ids.append(parent_id)

        child_entries = [
            {
                "category": "Cat05-U",
                "subject": "Alex",
                "observation": "Enjoys morning double-shot espresso with freshly ground beans.",
                "tags": "Home/Coffee/Espresso",
                "confidence": "high"
            },
            {
                "category": "Cat14-U",
                "subject": "Alex",
                "observation": "Maintains a self-hosted HomeAssistant server managing Zigbee smart devices.",
                "tags": "Tech/HomeLab/HomeAssistant, Tech/IoT/Zigbee",
                "confidence": "high"
            }
        ]

        new_ids = memory_db.split_entry(parent_id, child_entries)
        self.created_entry_ids.extend(new_ids)
        self.assertEqual(len(new_ids), 2)

        # Parent should be soft-deleted
        parent = memory_db.get_entry(parent_id)
        self.assertIsNotNone(parent)
        self.assertEqual(parent["status"], "deleted")

        # Children should be live
        child1 = memory_db.get_entry(new_ids[0])
        self.assertEqual(child1["observation"], child_entries[0]["observation"])
        self.assertEqual(child1["tags"], "Home/Coffee/Espresso")
        self.assertEqual(child1["source"], "split")

        child2 = memory_db.get_entry(new_ids[1])
        self.assertEqual(child2["observation"], child_entries[1]["observation"])
        self.assertEqual(child2["tags"], "Tech/HomeLab/HomeAssistant, Tech/IoT/Zigbee")

    def test_split_preview_and_apply_flow(self):
        """Verify preview_context_split generates structured child entries and apply_context_split saves them."""
        mock_llm_yaml = """
```yaml
entries:
  - category: Cat05-U
    subject: Alex
    tags: "coffee, routine"
    observation: "Prefers morning espresso."
  - category: Cat14-U
    subject: Alex
    tags: "homelab, smart-home-technology"
    observation: "Runs a HomeAssistant Zigbee server."
```
"""
        with patch("Evelyn.tools.tag_librarian.query_ollama", return_value=mock_llm_yaml):
            req = evelyn_server.SplitPreviewRequest(
                observation="Likes espresso and runs HomeAssistant.",
                category="Cat05-U",
                subject="Alex"
            )
            # Call preview route
            preview_result = asyncio_run(evelyn_server.preview_context_split(req, None))
            self.assertEqual(len(preview_result["splits"]), 2)
            # Flat controlled-vocabulary terms, not the pre-coordinate path the split prompt
            # taught until v000.006.204. This assertion previously expected `Home/Coffee/Espresso`
            # and had been failing since that release.
            self.assertEqual(preview_result["splits"][0]["tags"], "coffee, routine")

    def test_generate_split_proposal_in_consolidator(self):
        """Verify consolidator generates a split proposal for bloated entries."""
        test_source_id = memory_db.insert_entry(
            category="Cat05-U",
            subject="Alex",
            observation="Enjoys dark roast coffee every morning and builds mechanical keyboards with tactile switches and lubed stabilizers.",
            tags="coffee, hardware",
            status="live"
        )
        self.created_entry_ids.append(test_source_id)

        record = {
            "id": test_source_id,
            "category": "Cat05-U",
            "subject": "Alex",
            "summary": "Enjoys dark roast coffee every morning and builds mechanical keyboards with tactile switches and lubed stabilizers."
        }

        mock_llm_response = """
```yaml
verdict: split
reasoning: "Contains two distinct domain observations: coffee preferences and mechanical keyboard building."
entries:
  - category: Cat05-U
    subject: Alex
    tags: "Home/Coffee/Espresso"
    observation: "Enjoys drinking dark roast coffee every morning."
  - category: Cat05-U
    subject: Alex
    tags: "Tech/Hardware/Keyboards"
    observation: "Builds custom mechanical keyboards using tactile switches and lubed stabilizers."
```
"""
        with patch("Evelyn.tools.fact_consolidator._call_ollama", return_value=mock_llm_response):
            pid_str = asyncio_run(fact_consolidator.generate_split_proposal(record, "Cat00 Index"))
            self.assertIsNotNone(pid_str)
            self.created_proposal_ids.append(int(pid_str))

            proposals = memory_db.get_pending_proposals()
            split_prop = next((p for p in proposals if p["id"] == int(pid_str)), None)
            self.assertIsNotNone(split_prop)
            self.assertEqual(split_prop["type"], "split")
            self.assertEqual(split_prop["source_ids"], [test_source_id])

    def test_split_queue_operations(self):
        """Test enqueueing, listing, and dequeueing split review requests."""
        test_id = memory_db.insert_entry(
            category="Cat05-U",
            subject="Alex",
            observation="Test compound observation for split queue.",
            tags="Test",
            status="live"
        )
        self.created_entry_ids.append(test_id)

        # Enqueue
        success = memory_db.enqueue_split(test_id)
        self.assertTrue(success)

        queued_ids = memory_db.get_all_queued_split_entry_ids()
        self.assertIn(test_id, queued_ids)

        queue = memory_db.get_split_queue()
        matching = [item for item in queue if item["entry_id"] == test_id]
        self.assertEqual(len(matching), 1)

        # Dequeue
        dequeued = memory_db.dequeue_split(test_id)
        self.assertTrue(dequeued)
        queued_ids_after = memory_db.get_all_queued_split_entry_ids()
        self.assertNotIn(test_id, queued_ids_after)

    def test_queue_split_endpoint(self):
        """Test POST /api/context/{id}/queue_split endpoint."""
        from fastapi.testclient import TestClient
        client = TestClient(evelyn_server.app)

        test_id = memory_db.insert_entry(
            category="Cat05-U",
            subject="Alex",
            observation="Test observation for queue endpoint.",
            tags="Test",
            status="live"
        )
        self.created_entry_ids.append(test_id)

        with patch("evelyn_server.check_auth", return_value=None):
            resp = client.post(f"/api/context/{test_id}/queue_split")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data.get("status"), "ok")
            self.assertTrue(data.get("queued"))
            self.assertEqual(data.get("entry_id"), test_id)

            queued_ids = memory_db.get_all_queued_split_entry_ids()
            self.assertIn(test_id, queued_ids)

            # Cleanup queue
            memory_db.dequeue_split(test_id)

    def test_check_standalone_context(self):
        """Verify check_standalone_context detects dangling demonstratives and allows self-contained observations."""
        from Evelyn.tools import string_utils

        # Dangling demonstrative references
        bad_obs1 = "Alex expressed immediate love for these facial traits in Evelyn's generated imagery."
        ok, reason = string_utils.check_standalone_context(bad_obs1)
        self.assertFalse(ok)
        self.assertIn("these facial traits", reason)

        bad_obs2 = "Alex decided to support those changes after further review."
        ok, reason = string_utils.check_standalone_context(bad_obs2)
        self.assertFalse(ok)
        self.assertIn("those changes", reason)

        bad_obs3 = "As mentioned above, Alex prefers working late into the night."
        ok, reason = string_utils.check_standalone_context(bad_obs3)
        self.assertFalse(ok)
        self.assertIn("as mentioned above", reason.lower())

        # Standalone, fully self-contained observations
        good_obs1 = "Alex expressed immediate love for Evelyn's small nose and pointed ears in generated imagery."
        ok, _ = string_utils.check_standalone_context(good_obs1)
        self.assertTrue(ok)

        good_obs2 = "Evelyn incorporates delicately pointed ears and a small nose into her physical profile."
        ok, _ = string_utils.check_standalone_context(good_obs2)
        self.assertTrue(ok)

    def test_strip_redundant_date_anchors(self):
        """Verify strip_redundant_date_anchors cleanly removes mechanical date markers."""
        from Evelyn.tools import string_utils

        # Leading 'As of YYYY-MM-DD, '
        obs1 = "As of 2026-05-14, Alex was working on a tool called 'the consolidator'."
        cleaned1 = string_utils.strip_redundant_date_anchors(obs1)
        self.assertEqual(cleaned1, "Alex was working on a tool called 'the consolidator'.")

        # Leading 'On YYYY-MM-DD, '
        obs2 = "On 2025-10-25, Alex troubleshot a monitor outage."
        cleaned2 = string_utils.strip_redundant_date_anchors(obs2)
        self.assertEqual(cleaned2, "Alex troubleshot a monitor outage.")

        # Parenthetical '(as of YYYY-MM-DD)'
        obs3 = "Previously sought meticulous care and technical refinement (as of 2026-04-20), evolving over time."
        cleaned3 = string_utils.strip_redundant_date_anchors(obs3)
        self.assertEqual(cleaned3, "Previously sought meticulous care and technical refinement, evolving over time.")

        # Trailing 'as of YYYY-MM-DD'
        obs4 = "Alex has a pet, who has been described as demanding lately as of 2026-04-07."
        cleaned4 = string_utils.strip_redundant_date_anchors(obs4)
        self.assertEqual(cleaned4, "Alex has a pet, who has been described as demanding lately.")

        # Mid-sentence '; as of YYYY-MM-DD, '
        obs5 = "Alex's goal is to learn theory; as of 2026-04-13, he is pursuing a course."
        cleaned5 = string_utils.strip_redundant_date_anchors(obs5)
        self.assertEqual(cleaned5, "Alex's goal is to learn theory; he is pursuing a course.")

        # Trailing 'on YYYY-MM-DD.'
        obs6 = "Alex planned to mow the lawn on 2026-06-06."
        cleaned6 = string_utils.strip_redundant_date_anchors(obs6, target_date="2026-06-06")
        self.assertEqual(cleaned6, "Alex planned to mow the lawn.")

        # Mid-sentence 'on YYYY-MM-DD'
        obs7 = "Biscuit the cat woke Alex up 15 minutes before his alarm on 2026-09-30."
        cleaned7 = string_utils.strip_redundant_date_anchors(obs7, target_date="2026-09-30")
        self.assertEqual(cleaned7, "Biscuit the cat woke Alex up 15 minutes before his alarm.")

        # Time-of-day 'on the morning of YYYY-MM-DD'
        obs8 = "Alex experienced a headache on the morning of 2026-05-10."
        cleaned8 = string_utils.strip_redundant_date_anchors(obs8, target_date="2026-05-10")
        self.assertEqual(cleaned8, "Alex experienced a headache in the morning.")

        # SAFEGUARD: Intrinsic scheduled appointment date is PRESERVED
        obs9 = "Alex has a flu shot scheduled for approximately 10:40 AM on 2026-10-12."
        cleaned9 = string_utils.strip_redundant_date_anchors(obs9, target_date="2026-10-12")
        self.assertEqual(cleaned9, "Alex has a flu shot scheduled for approximately 10:40 AM on 2026-10-12.")

        # SAFEGUARD: Intrinsic birthday is PRESERVED
        obs10 = "Alex's birthday is on 2026-05-23."
        cleaned10 = string_utils.strip_redundant_date_anchors(obs10, target_date="2026-05-23")
        self.assertEqual(cleaned10, "Alex's birthday is on 2026-05-23.")

        # SAFEGUARD: Date differing from target_date is PRESERVED
        obs11 = "Alex planned a trip to the Renaissance Festival on 2026-10-10."
        cleaned11 = string_utils.strip_redundant_date_anchors(obs11, target_date="2026-10-09")
        self.assertEqual(cleaned11, "Alex planned a trip to the Renaissance Festival on 2026-10-10.")

    def test_split_proposal_rejects_dangling_anaphora(self):
        """Verify fact_splitter rejects proposals where a child observation fails the vacuum test."""
        from Evelyn.tools import fact_splitter

        test_source_id = memory_db.insert_entry(
            category="Cat05-U",
            subject="Alex",
            observation="Prefers pointed ears in generated imagery and decided to adopt them.",
            tags="aesthetic",
            status="live",
        )
        self.created_entry_ids.append(test_source_id)

        record = {
            "id": test_source_id,
            "category": "Cat05-U",
            "subject": "Alex",
            "summary": "Prefers pointed ears in generated imagery and decided to adopt them.",
        }

        mock_llm_response = """
```yaml
verdict: split
reasoning: "Decomposing into appearance and reaction."
entries:
  - category: Cat01-U
    subject: Evelyn
    tags: "appearance"
    observation: "Evelyn features delicately pointed ears in character descriptions."
  - category: Cat06-U
    subject: Alex
    tags: "feedback"
    observation: "Alex expressed immediate love for these facial traits in generated imagery."
```
"""
        with patch("Evelyn.tools.fact_consolidator._call_ollama", return_value=mock_llm_response):
            pid_str = asyncio_run(fact_splitter.generate_split_proposal(record, "Cat00 Index"))
            # Proposal should be rejected because child 2 has dangling anaphora 'these facial traits'
            self.assertIsNone(pid_str)



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
