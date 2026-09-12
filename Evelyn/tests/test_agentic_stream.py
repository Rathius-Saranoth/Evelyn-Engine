# test_agentic_stream.py
# date created: 2026-08-27 09:35:00
# date modified: 2026-09-12 11:58:29
# tags: #test, #streaming, #agentic, #unified_stream, #v000_006_000

import json
import unittest
from unittest.mock import patch

import evelyn_config as cfg
import evelyn_server as srv


class TestAgenticStream(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.orig_max_rounds = cfg.MAX_TOOL_ROUNDS
        cfg.MAX_TOOL_ROUNDS = 3

    async def asyncTearDown(self):
        cfg.MAX_TOOL_ROUNDS = self.orig_max_rounds

    async def test_01_single_pass_direct_conversation(self):
        """Verify conversational turns complete in 1 single streaming pass without duplicate thinking."""
        stream_chunks = [
            json.dumps({"message": {"thinking": "Checking if any tools are needed... No."}}),
            json.dumps({"message": {"content": "Hello there! How "}}),
            json.dumps({"message": {"content": "can I help you today?"}}),
            json.dumps({
                "message": {"content": ""},
                "done": True,
                "prompt_eval_count": 150,
                "eval_count": 42,
                "total_duration": 1200000000,
            }),
        ]

        async def mock_stream(msgs, tools=None, think_effort=None):
            for chunk in stream_chunks:
                yield chunk

        with patch("evelyn_server.call_ollama_stream", side_effect=mock_stream):
            events = [
                json.loads(evt_line[6:])
                async for evt_line in srv._agentic_stream_loop([{"role": "user", "content": "Hello"}], think_effort="medium")
                if evt_line.startswith("data: ")
            ]

            # Filter non-heartbeat events
            events = [e for e in events if e.get("type") != "heartbeat"]

            # Verify event sequence
            event_types = [e.get("type") for e in events]
            self.assertIn("thinking", event_types)
            self.assertIn("text", event_types)
            self.assertEqual(events[-1]["type"], "_state")

            state = events[-1]
            self.assertEqual(state["content"], "Hello there! How can I help you today?")
            self.assertIn("Checking if any tools are needed", state["thinking"])
            self.assertEqual(state["tools_used"], [])
            self.assertEqual(state["metrics"]["eval_count"], 42)
            self.assertEqual(state["metrics"]["prompt_eval_count"], 150)

    async def test_02_multi_round_tool_execution(self):
        """Verify intermediate tool calls execute cleanly and continue to next round for synthesis."""
        round_1_chunks = [
            json.dumps({"message": {"thinking": "User is asking for version info. I will read version.py."}}),
            json.dumps({
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "read_file",
                            "arguments": {"file_path": "Evelyn/version.py"}
                        }
                    }]
                },
                "done": True,
                "prompt_eval_count": 200,
                "eval_count": 30,
            }),
        ]

        round_2_chunks = [
            json.dumps({"message": {"thinking": "Now synthesizing response with version data."}}),
            json.dumps({"message": {"content": "The engine version is 000.006.000."}}),
            json.dumps({
                "message": {"content": ""},
                "done": True,
                "prompt_eval_count": 350,
                "eval_count": 25,
            }),
        ]

        call_count = 0

        async def mock_stream(msgs, tools=None, think_effort=None):
            nonlocal call_count
            call_count += 1
            chunks = round_1_chunks if call_count == 1 else round_2_chunks
            for c in chunks:
                yield c

        with patch("evelyn_server.call_ollama_stream", side_effect=mock_stream), \
             patch("evelyn_server.dispatch_tool", return_value="__version__ = '000.006.000'"):
            test_msgs = [{"role": "user", "content": "What version are you on?"}]
            events = [
                json.loads(evt_line[6:])
                async for evt_line in srv._agentic_stream_loop(test_msgs, think_effort="medium")
                if evt_line.startswith("data: ")
            ]

            events = [e for e in events if e.get("type") != "heartbeat"]
            event_types = [e.get("type") for e in events]

            self.assertIn("tool_start", event_types)
            self.assertIn("tool_end", event_types)
            self.assertIn("text", event_types)

            state = events[-1]
            self.assertEqual(state["type"], "_state")
            self.assertEqual(state["content"], "The engine version is 000.006.000.")
            self.assertEqual(state["tools_used"], ["read_file"])
            self.assertEqual(state["metrics"]["eval_count"], 55)  # 30 + 25
            self.assertEqual(state["metrics"]["prompt_eval_count"], 550)  # 200 + 350

    async def test_03_preamble_quarantining(self):
        """Verify pre-tool conversational preamble text in Round 1 is quarantined."""
        round_1_chunks = [
            json.dumps({"message": {"content": "Checking the system files for you now..."}}),
            json.dumps({
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {
                            "name": "search_vault",
                            "arguments": {"query": "test"}
                        }
                    }]
                },
                "done": True,
            }),
        ]

        round_2_chunks = [
            json.dumps({"message": {"content": "Found 1 matching note in the vault."}}),
            json.dumps({"message": {"content": ""}, "done": True}),
        ]

        call_count = 0

        async def mock_stream(msgs, tools=None, think_effort=None):
            nonlocal call_count
            call_count += 1
            chunks = round_1_chunks if call_count == 1 else round_2_chunks
            for c in chunks:
                yield c

        with patch("evelyn_server.call_ollama_stream", side_effect=mock_stream), \
             patch("evelyn_server.dispatch_tool", return_value="Matching note: test.md"):
            events = [
                json.loads(evt_line[6:])
                async for evt_line in srv._agentic_stream_loop([{"role": "user", "content": "Search vault"}], think_effort="medium")
                if evt_line.startswith("data: ")
            ]

            events = [e for e in events if e.get("type") != "heartbeat"]
            quarantine_events = [e for e in events if e.get("type") == "quarantine_preamble"]
            self.assertTrue(len(quarantine_events) > 0)
            self.assertIn("Checking the system files", quarantine_events[0]["text"])

            state = events[-1]
            # Final content should ONLY be the synthesis from round 2
            self.assertEqual(state["content"], "Found 1 matching note in the vault.")

    async def test_04_tool_error_resilience(self):
        """Verify tool exceptions are safely caught and fed back to the model without crashing."""
        round_1_chunks = [
            json.dumps({
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "read_file",
                            "arguments": {"file_path": "non_existent.txt"}
                        }
                    }]
                },
                "done": True,
            }),
        ]

        round_2_chunks = [
            json.dumps({"message": {"content": "I encountered an error reading the file."}}),
            json.dumps({"message": {"content": ""}, "done": True}),
        ]

        call_count = 0

        async def mock_stream(msgs, tools=None, think_effort=None):
            nonlocal call_count
            call_count += 1
            chunks = round_1_chunks if call_count == 1 else round_2_chunks
            for c in chunks:
                yield c

        def mock_error_dispatch(fn, fa):
            raise FileNotFoundError("File not found on disk")

        with patch("evelyn_server.call_ollama_stream", side_effect=mock_stream), \
             patch("evelyn_server.dispatch_tool", side_effect=mock_error_dispatch):
            test_msgs = [{"role": "user", "content": "Read non_existent.txt"}]
            events = [
                json.loads(evt_line[6:])
                async for evt_line in srv._agentic_stream_loop(test_msgs, think_effort="medium")
                if evt_line.startswith("data: ")
            ]

            events = [e for e in events if e.get("type") != "heartbeat"]
            tool_ends = [e for e in events if e.get("type") == "tool_end"]
            self.assertEqual(len(tool_ends), 1)
            self.assertEqual(tool_ends[0]["status"], "error")

            state = events[-1]
            self.assertEqual(state["content"], "I encountered an error reading the file.")
            # Verify the tool error message was appended to messages for round 2
            tool_msg = next(m for m in test_msgs if m.get("role") == "tool")
            self.assertIn("Error executing read_file", tool_msg["content"])

    async def test_05_terminal_round_enforcement(self):
        """Verify when MAX_TOOL_ROUNDS is reached, tools=None is enforced on the final round."""
        cfg.MAX_TOOL_ROUNDS = 2
        recorded_tools = []

        async def mock_stream(msgs, tools=None, think_effort=None):
            recorded_tools.append(tools)
            if tools is not None:
                # Emit tool call
                yield json.dumps({
                    "message": {
                        "tool_calls": [{
                            "function": {"name": "search_vault", "arguments": {"query": "loop"}}
                        }]
                    },
                    "done": True,
                })
            else:
                # Terminal pass
                yield json.dumps({"message": {"content": "Final synthesized summary."}})
                yield json.dumps({"message": {"content": ""}, "done": True})

        with patch("evelyn_server.call_ollama_stream", side_effect=mock_stream), \
             patch("evelyn_server.dispatch_tool", return_value="result"):
            events = [
                json.loads(evt_line[6:])
                async for evt_line in srv._agentic_stream_loop([{"role": "user", "content": "Loop test"}], think_effort="medium")
                if evt_line.startswith("data: ")
            ]

            # Round 1: offered tools
            self.assertTrue(len(events) > 0)
            self.assertIsNotNone(recorded_tools[0])
            # Round 2 (terminal round since MAX_TOOL_ROUNDS=2): tools=None enforced
            self.assertIsNone(recorded_tools[1])

    async def test_06_tool_scratchpad_compaction_and_synthesis_anchoring(self):
        """Verify dynamic tool returns exceeding budget are compacted and synthesis directive is injected."""
        cfg.MAX_TOOL_ROUNDS = 3
        # Set dynamic budget low via small NUM_CTX to trigger compaction
        with patch.object(cfg, "NUM_CTX", 2000), patch.object(cfg, "TOOL_RETURN_RATIO", 0.35):
            call_count = 0
            captured_msgs_history = []

            async def mock_stream(msgs, tools=None, think_effort=None):
                nonlocal call_count
                call_count += 1
                captured_msgs_history.append([dict(m) for m in msgs])
                if call_count == 1:
                    yield json.dumps({
                        "message": {
                            "tool_calls": [{
                                "function": {"name": "read_file", "arguments": {"file_path": "doc1.md"}}
                            }]
                        },
                        "done": True,
                    })
                elif call_count == 2:
                    yield json.dumps({
                        "message": {
                            "tool_calls": [{
                                "function": {"name": "read_file", "arguments": {"file_path": "doc2.md"}}
                            }]
                        },
                        "done": True,
                    })
                else:
                    yield json.dumps({"message": {"content": "Final synthesis addressing user prompt."}})
                    yield json.dumps({"message": {"content": ""}, "done": True})

            def mock_dispatch(fn, fa):
                # Return long content (~1200 chars ~= 480 tokens)
                return "Long document section line content. " * 35

            with patch("evelyn_server.call_ollama_stream", side_effect=mock_stream), \
                 patch("evelyn_server.dispatch_tool", side_effect=mock_dispatch):
                test_msgs = [{"role": "user", "content": "Analyze documents"}]
                events = [
                    json.loads(evt_line[6:])
                    async for evt_line in srv._agentic_stream_loop(test_msgs, think_effort="medium")
                    if evt_line.startswith("data: ")
                ]
                self.assertTrue(len(events) > 0)

                # Check that round 3 received compacted earlier tool output and a synthesis directive
                round_3_msgs = captured_msgs_history[2]
                tool_msgs = [m for m in round_3_msgs if m.get("role") == "tool"]
                self.assertEqual(len(tool_msgs), 2)
                # First tool message should have been compacted
                self.assertIn("compacted in scratchpad", tool_msgs[0]["content"])

                # Synthesis directive should be present in round 3 messages
                synthesis_msgs = [m for m in round_3_msgs if "<tool_synthesis" in m.get("content", "")]
                self.assertTrue(len(synthesis_msgs) > 0)

    async def test_07_parallel_streaming_tool_call_accumulation(self):
        """Verify parallel tool calls delivered across distinct streaming chunks are accumulated and executed."""
        cfg.MAX_TOOL_ROUNDS = 3
        call_count = 0
        executed_tools = []

        # Round 1 emits two separate chunks, each containing one tool call
        round_1_chunks = [
            json.dumps({
                "message": {
                    "tool_calls": [{"id": "call_1", "function": {"index": 0, "name": "read_file", "arguments": {"file_path": "docA.md"}}}]
                }
            }),
            json.dumps({
                "message": {
                    "tool_calls": [{"id": "call_2", "function": {"index": 1, "name": "read_file", "arguments": {"file_path": "docB.md"}}}]
                },
                "done": True,
            }),
        ]

        # Round 2 emits final synthesized response
        round_2_chunks = [
            json.dumps({"message": {"content": "Successfully synthesized both docA and docB."}}),
            json.dumps({"message": {"content": ""}, "done": True}),
        ]

        async def mock_stream(msgs, tools=None, think_effort=None):
            nonlocal call_count
            call_count += 1
            chunks = round_1_chunks if call_count == 1 else round_2_chunks
            for c in chunks:
                yield c

        def mock_dispatch(fn, fa):
            executed_tools.append(fa.get("file_path"))
            return f"Content of {fa.get('file_path')}"

        with patch("evelyn_server.call_ollama_stream", side_effect=mock_stream), \
             patch("evelyn_server.dispatch_tool", side_effect=mock_dispatch):
            test_msgs = [{"role": "user", "content": "Read docA and docB"}]
            events = [
                json.loads(evt_line[6:])
                async for evt_line in srv._agentic_stream_loop(test_msgs, think_effort="medium")
                if evt_line.startswith("data: ")
            ]
            self.assertTrue(len(events) > 0)
            # Both tools should have executed in Round 1
            self.assertEqual(executed_tools, ["docA.md", "docB.md"])
            state = events[-1]
            self.assertEqual(state["content"], "Successfully synthesized both docA and docB.")


if __name__ == "__main__":
    unittest.main()


