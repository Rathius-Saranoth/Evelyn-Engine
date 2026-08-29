# test_xml_envelopes.py
# date created: 2026-08-29 13:14:00
# date modified: 2026-08-29 13:14:00
# tags: #test, #xml, #telemetry, #envelopes, #string_utils

"""Unit tests for XML envelope generation, sanitization, pruning, and turn injection."""

import unittest

from Evelyn.tools.string_utils import (
    build_autonomous_trigger_envelope,
    build_context_retrieval_envelope,
    build_memory_context_envelope,
    build_system_event_envelope,
    build_temporal_envelope,
    escape_xml_attr,
    escape_xml_content,
    inject_envelope_to_turn,
    stack_envelopes,
    wrap_xml_envelope,
)


class TestXMLEnvelopes(unittest.TestCase):
    """Test suite for XML escaping, envelope builders, pruning, and stacking."""

    def test_escape_xml_content(self):
        """Verify XML content escaping handles special characters and edge cases."""
        self.assertEqual(escape_xml_content("Rock & Roll <5 >2"), "Rock &amp; Roll &lt;5 &gt;2")
        self.assertEqual(escape_xml_content(None), "")
        self.assertEqual(escape_xml_content(42), "42")
        self.assertEqual(escape_xml_content("Simple string"), "Simple string")

    def test_escape_xml_attr(self):
        """Verify XML attribute escaping escapes quotes, angle brackets, and ampersands."""
        self.assertEqual(
            escape_xml_attr('He said "Hello" & \'Goodbye\' <1 >0'),
            "He said &quot;Hello&quot; &amp; &apos;Goodbye&apos; &lt;1 &gt;0",
        )
        self.assertEqual(escape_xml_attr(None), "")
        self.assertEqual(escape_xml_attr(100), "100")

    def test_wrap_xml_envelope_pruning_and_self_closing(self):
        """Verify wrap_xml_envelope prunes empty envelopes and supports self-closing tags."""
        # 1. Pruning when empty body and no self_closing flag
        self.assertEqual(wrap_xml_envelope("context_retrieval"), "")
        self.assertEqual(wrap_xml_envelope("context_retrieval", body="", source="vault"), "")
        self.assertEqual(wrap_xml_envelope("context_retrieval", body=[]), "")

        # 2. Self-closing when flag is set and attributes exist
        self.assertEqual(
            wrap_xml_envelope("session_gap", self_closing_if_empty=True, status="active_flow"),
            '<session_gap status="active_flow" />',
        )

        # 3. Content wrapping
        wrapped = wrap_xml_envelope("summary", body="Task completed successfully.")
        self.assertEqual(wrapped, "<summary>\n  Task completed successfully.\n</summary>")

    def test_build_temporal_envelope(self):
        """Verify build_temporal_envelope outputs complete, well-formed XML metadata."""
        current_time = "Saturday, Aug 29, 2026, 1:15 PM CDT"
        session_gap = {
            "status": "resumed",
            "duration_str": "3h 15m",
            "last_interaction_ts": "2026-08-29 10:00 AM",
        }
        calendar_events = [
            {"title": "Sync Meeting", "start_str": "2:00 PM", "status": "upcoming"}
        ]
        task_events = [
            {"title": "Run Server Backup", "due_str": "5:00 PM", "status": "needsAction"}
        ]

        xml = build_temporal_envelope(
            current_time=current_time,
            session_gap=session_gap,
            calendar_events=calendar_events,
            task_events=task_events,
        )

        self.assertTrue(xml.startswith("<temporal_context>"))
        self.assertTrue(xml.endswith("</temporal_context>"))
        self.assertIn(f"<current_time>{current_time}</current_time>", xml)
        self.assertIn('status="resumed"', xml)
        self.assertIn('break_duration="3h 15m"', xml)
        self.assertIn('<event title="Sync Meeting" time="2:00 PM" status="upcoming" />', xml)
        self.assertIn('<task title="Run Server Backup" time="5:00 PM" status="needsAction" />', xml)

    def test_build_context_retrieval_envelope_pruning_and_formatting(self):
        """Verify context retrieval prunes when empty and formats documents correctly."""
        # Pruning check
        self.assertEqual(build_context_retrieval_envelope(source="vault", query="solar", items=[]), "")

        # Formatted documents
        items = [
            {
                "id": "Hardware/Solar.md",
                "title": "Solar Specs",
                "score": 0.8912,
                "content": "24V nominal output.",
            },
            "<protocol name='backup'>\n  1. Run dump\n</protocol>",
        ]
        xml = build_context_retrieval_envelope(source="vault", query="solar power", items=items)

        self.assertTrue(xml.startswith('<context_retrieval source="vault" query="solar power" match_count="2">'))
        self.assertTrue(xml.endswith("</context_retrieval>"))
        self.assertIn('id="Hardware/Solar.md"', xml)
        self.assertIn('title="Solar Specs"', xml)
        self.assertIn('score="0.89"', xml)
        self.assertIn("24V nominal output.", xml)
        self.assertIn("<protocol name='backup'>", xml)

    def test_build_autonomous_trigger_envelope(self):
        """Verify autonomous trigger formats type, severity, summary, and directive."""
        xml = build_autonomous_trigger_envelope(
            trigger_type="task_overdue",
            entity_id="task_441",
            severity="high",
            summary="Server backup failed verification.",
            directive="Evaluate severity and alert operator immediately.",
        )

        self.assertTrue(xml.startswith('<autonomous_trigger type="task_overdue" entity_id="task_441" severity="high">'))
        self.assertIn("<summary>Server backup failed verification.</summary>", xml)
        self.assertIn("<directive>Evaluate severity and alert operator immediately.</directive>", xml)
        self.assertTrue(xml.endswith("</autonomous_trigger>"))

    def test_build_system_event_envelope(self):
        """Verify system event formats telemetry with status and description."""
        # Self closing
        self.assertEqual(
            build_system_event_envelope(event="daemon_ping", status="alive"),
            '<system_event event="daemon_ping" status="alive" />',
        )

        # With description
        xml = build_system_event_envelope(
            event="research_ready",
            timestamp="2026-08-29 13:15:00",
            status="completed",
            description="Topic 'Solid State Batteries' synthesis is complete.",
        )
        self.assertTrue(xml.startswith('<system_event event="research_ready" timestamp="2026-08-29 13:15:00" status="completed">'))
        self.assertIn("Topic 'Solid State Batteries' synthesis is complete.", xml)
        self.assertTrue(xml.endswith("</system_event>"))

    def test_build_memory_context_envelope(self):
        """Verify fast memory envelope formatting and empty pruning."""
        self.assertEqual(build_memory_context_envelope(category="Cat01-U", subject="Ricky", observation=""), "")

        xml = build_memory_context_envelope(
            category="Cat01-U",
            subject="Ricky",
            observation="Prefers concise command line tools & SQLite.",
        )
        self.assertTrue(xml.startswith('<memory_context category="Cat01-U" subject="Ricky">'))
        self.assertIn("Prefers concise command line tools &amp; SQLite.", xml)
        self.assertTrue(xml.endswith("</memory_context>"))

    def test_stack_envelopes_canonical_ordering(self):
        """Verify stacking orders envelopes deterministically: temporal -> system -> retrieval."""
        temporal = "<temporal_context>\n  <current_time>Now</current_time>\n</temporal_context>"
        retrieval = '<context_retrieval source="vault" query="q">\n  <doc />\n</context_retrieval>'
        trigger = '<autonomous_trigger type="alert">\n  <summary>Alert</summary>\n</autonomous_trigger>'

        # Pass in reverse order
        stacked = stack_envelopes(retrieval, temporal, trigger, "", None)
        expected = f"{temporal}\n\n{trigger}\n\n{retrieval}"
        self.assertEqual(stacked, expected)

    def test_inject_envelope_to_turn(self):
        """Verify inject_envelope_to_turn places envelopes at the top with double newlines."""
        env = "<temporal_context>\n  <current_time>Now</current_time>\n</temporal_context>"
        user_msg = "What is our schedule today?"

        turn = inject_envelope_to_turn(user_msg, env)
        self.assertEqual(turn, f"{env}\n\n{user_msg}")

        # Empty envelope case
        self.assertEqual(inject_envelope_to_turn(user_msg, ""), user_msg)
        self.assertEqual(inject_envelope_to_turn(user_msg, None), user_msg)

        # Empty user message case
        self.assertEqual(inject_envelope_to_turn("", env), env)


if __name__ == "__main__":
    unittest.main()
