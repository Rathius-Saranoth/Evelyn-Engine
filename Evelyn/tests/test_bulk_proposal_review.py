# test_bulk_proposal_review.py
# date created: 2026-09-26
# date modified: 2026-09-26 07:16:50
# tags: #tests, #review, #proposals, #bulk, #taxonomy

"""Bulk proposal review applies the same decision the single-card route applies.

Tag admission arrives in floods — 158 pending on 2026-09-26, each one its own POST and its
own confirm dialog — and the queue's own cap silently stops withholding once it fills. A
reviewer who cannot clear the queue faster than it fills never gets to make the decision at
all, so these tests hold the bulk path to the thing that makes it safe: it must be the *same*
code, with the same side effects, as approving one card at a time.
"""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import evelyn_server
from Evelyn.tools import memory_db, taxonomy_db


def _bulk(decisions: list[dict]):
    """Call the bulk route directly, standing in for the HTTP layer."""
    req = evelyn_server.BulkProposalActionRequest(
        decisions=[evelyn_server.BulkProposalDecision(**d) for d in decisions]
    )
    return asyncio.run(evelyn_server.action_proposals_bulk(req, None))


class TestBulkProposalReview(unittest.TestCase):
    """The bulk route is the single route, repeated — not a second implementation."""

    def setUp(self):
        memory_db.init_db()
        taxonomy_db.init_db()
        patcher = patch.object(evelyn_server, "start_refresh_memory_internal", new_callable=AsyncMock)
        self.refresh = patcher.start()
        self.addCleanup(patcher.stop)

    def _admission(self, term: str, source_ids: list[int] | None = None) -> int:
        return memory_db.insert_proposal(
            type="tag_admission",
            source_ids=source_ids or [],
            topic=term,
            reason="test",
            merged_observation="test origin",
        )

    def test_a_batch_applies_every_decision(self):
        """Three admissions submitted together are three admissions applied."""
        ids = [self._admission(t) for t in ("alpha-term", "beta-term", "gamma-term")]
        result = _bulk([{"id": i, "action": "approve", "modified_text": t, "category": "general"}
                        for i, t in zip(ids, ("alpha-term", "beta-term", "gamma-term"), strict=True)])

        self.assertEqual(result["applied"], 3)
        self.assertEqual(result["failed"], 0)
        registered = {m["tag"] for m in taxonomy_db.get_master_tags()}
        for term in ("alpha-term", "beta-term", "gamma-term"):
            self.assertIn(term, registered, f"'{term}' was submitted but never registered")

    def test_one_bad_row_does_not_end_the_batch(self):
        """A reviewer clearing 150 items cannot afford an abort on the first bad id."""
        good = self._admission("survivor-term")
        result = _bulk([
            {"id": 99999999, "action": "approve", "modified_text": "ghost", "category": "general"},
            {"id": good, "action": "approve", "modified_text": "survivor-term", "category": "general"},
        ])

        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertIn("survivor-term", {m["tag"] for m in taxonomy_db.get_master_tags()})
        failure = next(r for r in result["results"] if r["status"] == "error")
        self.assertEqual(failure["id"], 99999999)

    def test_approval_still_backfills_the_fact_that_asked(self):
        """The whole point of admitting a term is that it reaches the fact that wanted it."""
        entry_id = memory_db.insert_entry(
            category="Cat05-U",
            subject="Tester",
            observation="A fact that asked for a term the vocabulary did not hold.",
            tags="sleep",
        )
        prop = self._admission("backfilled-term", source_ids=[entry_id])

        _bulk([{"id": prop, "action": "approve", "modified_text": "backfilled-term",
                "category": "general"}])

        entry = memory_db.get_entry(entry_id)
        tags = [t.strip() for t in (entry.get("tags") or "").split(",")]
        self.assertIn("backfilled-term", tags,
                      "Bulk approval registered the term but never put it on the fact (G5)")

    def test_a_rejection_is_recorded_as_a_rejection(self):
        """Reject must be permanent here too, or the flood returns tomorrow."""
        prop = self._admission("rejected-term")
        result = _bulk([{"id": prop, "action": "deny"}])

        self.assertEqual(result["applied"], 1)
        self.assertIn("rejected-term", memory_db.get_rejected_topics("tag_admission"))

    def test_the_memory_refresh_runs_once_for_the_batch(self):
        """Once per item is 150 refreshes; the reason bulk exists is that this is expensive."""
        ids = [self._admission(t) for t in ("refresh-a", "refresh-b", "refresh-c")]
        _bulk([{"id": i, "action": "approve", "modified_text": t, "category": "general"}
               for i, t in zip(ids, ("refresh-a", "refresh-b", "refresh-c"), strict=True)])

        self.assertEqual(self.refresh.call_count, 1)

    def test_a_batch_of_rejections_does_not_refresh_at_all(self):
        """Nothing was written to memory, so there is nothing to refresh."""
        ids = [self._admission(t) for t in ("no-refresh-a", "no-refresh-b")]
        _bulk([{"id": i, "action": "deny"} for i in ids])

        self.assertEqual(self.refresh.call_count, 0)

    def test_denying_a_row_that_is_not_there_is_reported_as_a_failure(self):
        """The applied count is the reviewer's only feedback, so it must not count no-ops."""
        result = _bulk([{"id": 99999999, "action": "deny"}])

        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["failed"], 1)

    def test_removing_a_row_that_is_not_there_is_reported_as_a_failure(self):
        """Same for delete: `delete_proposal` returns whether it deleted anything."""
        result = _bulk([{"id": 99999999, "action": "delete"}])

        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["failed"], 1)

    def test_a_batch_submitted_twice_does_not_report_success_twice(self):
        """A double-click must not read as 2 successful rejections of 1 proposal."""
        prop = self._admission("double-submit-term")

        first = _bulk([{"id": prop, "action": "delete"}])
        second = _bulk([{"id": prop, "action": "delete"}])

        self.assertEqual(first["applied"], 1)
        self.assertEqual(second["applied"], 0)
        self.assertEqual(second["failed"], 1)

    def test_an_empty_batch_is_refused(self):
        """An empty submission is a UI bug, not a no-op worth pretending succeeded."""
        with self.assertRaises(evelyn_server.HTTPException) as caught:
            _bulk([])
        self.assertEqual(caught.exception.status_code, 400)

    def test_an_oversized_batch_is_refused(self):
        """The ceiling is a real limit, not a comment."""
        oversized = [{"id": i, "action": "deny"} for i in range(evelyn_server.BULK_PROPOSAL_MAX + 1)]
        with self.assertRaises(evelyn_server.HTTPException) as caught:
            _bulk(oversized)
        self.assertEqual(caught.exception.status_code, 400)

    def test_the_single_route_and_the_bulk_route_share_one_implementation(self):
        """Two implementations of approval is two sets of side effects to forget."""
        import ast
        import inspect

        source = inspect.getsource(evelyn_server.action_proposal)
        tree = ast.parse(source.lstrip())
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn(
            "_apply_proposal_action", called,
            "The single-proposal route no longer delegates; bulk and single can now drift apart",
        )


if __name__ == "__main__":
    unittest.main()
