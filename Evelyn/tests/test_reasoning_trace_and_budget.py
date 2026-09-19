# test_reasoning_trace_and_budget.py
# date created: 2026-09-19 00:00:00
# date modified: 2026-09-19 09:31:34
# tags: #test, #streaming, #reasoning_trace, #think_budget, #v000_006_140

"""Regression coverage for the native multi-channel reasoning pipeline.

Covers the structured per-round trace emitted on `_state` and the reasoning
budget that breaks self-prompting thought loops by re-issuing the round with
thinking disabled.
"""

import json
import unittest
from unittest.mock import patch

import evelyn_config as cfg
import evelyn_server as srv


async def _drain(msgs, **kw):
    """Run the agentic loop and return decoded non-heartbeat SSE events."""
    events = [
        json.loads(line[6:])
        async for line in srv._agentic_stream_loop(msgs, **kw)
        if line.startswith("data: ")
    ]
    return [e for e in events if e.get("type") != "heartbeat"]


class TestStructuredReasoningTrace(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig_rounds = cfg.MAX_TOOL_ROUNDS
        self._orig_budget = cfg.THINK_BUDGET_CHARS
        cfg.MAX_TOOL_ROUNDS = 3

    async def asyncTearDown(self):
        cfg.MAX_TOOL_ROUNDS = self._orig_rounds
        cfg.THINK_BUDGET_CHARS = self._orig_budget

    async def test_01_state_carries_structured_trace(self):
        """A plain conversational turn emits a one-round structured trace."""
        cfg.THINK_BUDGET_CHARS = 16000
        chunks = [
            json.dumps({"message": {"thinking": "Short and grounded."}}),
            json.dumps({"message": {"content": "Good morning."}}),
            json.dumps({"message": {"content": ""}, "done": True, "eval_count": 12}),
        ]

        async def mock_stream(msgs, tools=None, think_effort=None):
            for c in chunks:
                yield c

        with patch("evelyn_server.call_ollama_stream", side_effect=mock_stream):
            events = await _drain([{"role": "user", "content": "hi"}], think_effort="medium")

        state = events[-1]
        self.assertEqual(state["type"], "_state")
        trace = state["trace"]
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0]["round"], 1)
        self.assertEqual(trace[0]["thinking"], "Short and grounded.")
        self.assertEqual(trace[0]["tools"], [])
        self.assertFalse(trace[0]["budget_tripped"])

    async def test_02_think_tags_in_content_are_not_parsed(self):
        """Content is passed through verbatim — no legacy <think> state machine."""
        cfg.THINK_BUDGET_CHARS = 0
        literal = "Use the <think> tag in your template."
        chunks = [
            json.dumps({"message": {"content": literal}}),
            json.dumps({"message": {"content": ""}, "done": True}),
        ]

        async def mock_stream(msgs, tools=None, think_effort=None):
            for c in chunks:
                yield c

        with patch("evelyn_server.call_ollama_stream", side_effect=mock_stream):
            events = await _drain([{"role": "user", "content": "q"}], think_effort=False)

        state = events[-1]
        self.assertEqual(state["content"], literal)
        self.assertEqual(state["thinking"], "")


class TestReasoningBudget(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig_rounds = cfg.MAX_TOOL_ROUNDS
        self._orig_budget = cfg.THINK_BUDGET_CHARS
        cfg.MAX_TOOL_ROUNDS = 3

    async def asyncTearDown(self):
        cfg.MAX_TOOL_ROUNDS = self._orig_rounds
        cfg.THINK_BUDGET_CHARS = self._orig_budget

    async def test_01_runaway_thinking_trips_budget_and_answers(self):
        """A looping thought chain is cancelled and re-issued without thinking."""
        cfg.THINK_BUDGET_CHARS = 200
        self.attempts = []

        async def mock_stream(msgs, tools=None, think_effort=None):
            self.attempts.append(think_effort)
            if think_effort is not False:
                # Attempt 1: model loops on self-termination tokens forever.
                for _ in range(20):
                    yield json.dumps({"message": {"thinking": "Ready. Done. Perfect. Go. " * 4}})
                yield json.dumps({"message": {"content": ""}, "done": True})
            else:
                # Attempt 2: thinking disabled, real answer arrives.
                yield json.dumps({"message": {"content": "Here is the actual answer."}})
                yield json.dumps({"message": {"content": ""}, "done": True, "eval_count": 9})

        with patch("evelyn_server.call_ollama_stream", side_effect=mock_stream):
            events = await _drain([{"role": "user", "content": "deep question"}], think_effort="high")

        types = [e.get("type") for e in events]
        self.assertIn("think_budget_exceeded", types)

        # The round was retried exactly once, with thinking disabled.
        self.assertEqual(self.attempts, ["high", False])

        state = events[-1]
        self.assertEqual(state["type"], "_state")
        self.assertEqual(state["content"], "Here is the actual answer.")
        self.assertTrue(state["trace"][0]["budget_tripped"])
        self.assertEqual(state["metrics"]["think_budget_trips"], 1)

    async def test_02_budget_disabled_allows_long_reasoning(self):
        """A zero budget disables the guard entirely."""
        cfg.THINK_BUDGET_CHARS = 0

        async def mock_stream(msgs, tools=None, think_effort=None):
            for _ in range(10):
                yield json.dumps({"message": {"thinking": "x" * 500}})
            yield json.dumps({"message": {"content": "done thinking"}})
            yield json.dumps({"message": {"content": ""}, "done": True})

        with patch("evelyn_server.call_ollama_stream", side_effect=mock_stream):
            events = await _drain([{"role": "user", "content": "q"}], think_effort="max")

        types = [e.get("type") for e in events]
        self.assertNotIn("think_budget_exceeded", types)
        self.assertEqual(events[-1]["content"], "done thinking")


class TestToolStatusFidelity(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._orig_rounds = cfg.MAX_TOOL_ROUNDS
        self._orig_budget = cfg.THINK_BUDGET_CHARS
        cfg.MAX_TOOL_ROUNDS = 2
        cfg.THINK_BUDGET_CHARS = 0

    async def asyncTearDown(self):
        cfg.MAX_TOOL_ROUNDS = self._orig_rounds
        cfg.THINK_BUDGET_CHARS = self._orig_budget

    async def test_01_failed_tool_status_is_persisted_in_trace(self):
        """A failing tool is recorded as an error, not silently as success."""
        self.calls = 0

        async def mock_stream(msgs, tools=None, think_effort=None):
            self.calls += 1
            if self.calls == 1:
                yield json.dumps({
                    "message": {
                        "thinking": "I should read that file.",
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "read_file", "arguments": {"file_path": "/nope"}}}
                        ],
                    }
                })
                yield json.dumps({"message": {"content": ""}, "done": True})
            else:
                yield json.dumps({"message": {"content": "That file could not be read."}})
                yield json.dumps({"message": {"content": ""}, "done": True})

        with (
            patch("evelyn_server.call_ollama_stream", side_effect=mock_stream),
            patch("evelyn_server.dispatch_tool", return_value="Error: file not found"),
        ):
            events = await _drain([{"role": "user", "content": "read /nope"}], think_effort="low")

        state = events[-1]
        first_round = state["trace"][0]
        self.assertEqual(len(first_round["tools"]), 1)
        self.assertEqual(first_round["tools"][0]["name"], "read_file")
        self.assertEqual(first_round["tools"][0]["status"], "error")


if __name__ == "__main__":
    unittest.main()
