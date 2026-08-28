# test_telemetry_and_thinking_hardening.py
# date created: 2026-08-15 11:54:56
# date modified: 2026-08-15 11:54:56
# tags: #test, #telemetry, #hardening, #thinking, #task_manager

import unittest

from Evelyn.tools import task_manager


class TestTelemetryAndThinkingHardening(unittest.TestCase):
    def setUp(self):
        self.tasks_dict = {}
        self.orig_get_bg = task_manager._get_background_tasks
        self.orig_save_persist = task_manager.save_persistent_state
        self.orig_record_history = task_manager.record_task_history
        task_manager._get_background_tasks = lambda: self.tasks_dict
        task_manager.save_persistent_state = lambda: None
        task_manager.record_task_history = lambda **kwargs: None

    def tearDown(self):
        task_manager._get_background_tasks = self.orig_get_bg
        task_manager.save_persistent_state = self.orig_save_persist
        task_manager.record_task_history = self.orig_record_history

    def test_task_manager_error_sanitization(self):
        """Verify trailing colons and empty error strings are sanitized cleanly."""
        task_manager.set_running("test_task")
        task_manager.clear_running("test_task", status="error", error="ReadTimeout: ")

        status_data = self.tasks_dict.get("test_task")
        self.assertEqual(status_data["status"], "error")
        self.assertEqual(status_data["error"], "ReadTimeout")

        # Test empty error
        task_manager.set_running("test_task_empty")
        task_manager.clear_running("test_task_empty", status="error", error="   ")
        status_data_empty = self.tasks_dict.get("test_task_empty")
        self.assertIsNone(status_data_empty["error"])

    def test_task_manager_error_cleared_on_success(self):
        """Verify previously errored tasks clear their error upon subsequent successful completion."""
        task_manager.set_running("test_task_recover")
        task_manager.clear_running("test_task_recover", status="error", error="ReadTimeout")

        status_data = self.tasks_dict.get("test_task_recover")
        self.assertEqual(status_data["error"], "ReadTimeout")

        # Run again and succeed
        task_manager.set_running("test_task_recover")
        task_manager.clear_running("test_task_recover", status="idle", summary="Completed successfully")

        status_data_after = self.tasks_dict.get("test_task_recover")
        self.assertEqual(status_data_after["status"], "idle")
        self.assertIsNone(status_data_after["error"])
        self.assertEqual(status_data_after["summary"], "Completed successfully")

    def test_multi_phase_thinking_splitting(self):
        """Verify multi-phase thinking format parses into discrete sections."""
        combined_thinking = (
            "[Initial]\nI should analyze the user request.\n\n"
            "[Tool 1]\nAnalyzing results of tool call.\n\n"
            "[Response]\nFormulating the final answer."
        )
        import re
        sections = re.split(r"(?=\[(?:Initial|Tool \d+|Response)\]\n)", combined_thinking)
        clean_sections = [s for s in sections if s.strip()]
        self.assertEqual(len(clean_sections), 3)

        m0 = re.match(r"^\[(Initial|Tool \d+|Response)\]\n([\s\S]*)$", clean_sections[0])
        self.assertIsNotNone(m0)
        self.assertEqual(m0.group(1), "Initial")
        self.assertEqual(m0.group(2).strip(), "I should analyze the user request.")

        m1 = re.match(r"^\[(Initial|Tool \d+|Response)\]\n([\s\S]*)$", clean_sections[1])
        self.assertIsNotNone(m1)
        self.assertEqual(m1.group(1), "Tool 1")
        self.assertEqual(m1.group(2).strip(), "Analyzing results of tool call.")

        m2 = re.match(r"^\[(Initial|Tool \d+|Response)\]\n([\s\S]*)$", clean_sections[2])
        self.assertIsNotNone(m2)
        self.assertEqual(m2.group(1), "Response")
        self.assertEqual(m2.group(2).strip(), "Formulating the final answer.")

if __name__ == "__main__":
    unittest.main()
