# test_fact_consolidator_scan_state.py
# date created: 2026-08-28 19:16:00
# date modified: 2026-08-28 19:16:09
# tags: #[test, #fact_consolidator, #scan_state, #taxonomy, #categories]

"""Unit tests for Fact Consolidator category scan state sanitization."""

import json
import os
import tempfile
from unittest.mock import patch

from Evelyn.tools import fact_consolidator


def test_load_scan_state_sanitizes_legacy_and_invalid_categories() -> None:
    """Test that _load_scan_state prunes malformed keys and migrates legacy keys."""
    raw_data = {
        "Cat01-U": {"anchor": 0, "offset": 14, "n": 248},
        "Cat01-R": {"anchor": 0, "offset": 14, "n": 125},  # Legacy duplicate, Cat01-U already present
        "Cat02-R": {"anchor": 3, "offset": 0, "n": 7},    # Legacy -R, maps to Cat02-U
        "Cat02-E": {"anchor": 0, "offset": 14, "n": 36},   # Legacy -E, maps to Cat02-A
        "Cat00": {"anchor": 1, "offset": 0, "n": 2},      # Non-canonical / invalid
        "": {"anchor": 0, "offset": 0, "n": 2},            # Blank key
        "None": {"anchor": 1, "offset": 0, "n": 2},        # None string
        "Kate_Profile": {"anchor": 1, "offset": 0, "n": 2}, # Non-category
    }

    with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as tf:
        json.dump(raw_data, tf)
        tmp_path = tf.name

    try:
        with patch.object(fact_consolidator, "_SCAN_STATE_FILE", tmp_path):
            fact_consolidator._load_scan_state()
            state = fact_consolidator._category_scan_state

            # Verify canonical categories are present and invalid keys are removed
            assert "Cat01-U" in state
            assert state["Cat01-U"]["n"] == 248
            assert "Cat02-U" in state
            assert state["Cat02-U"]["n"] == 7
            assert "Cat02-A" in state
            assert state["Cat02-A"]["n"] == 36

            assert "Cat01-R" not in state
            assert "Cat02-R" not in state
            assert "Cat02-E" not in state
            assert "Cat00" not in state
            assert "" not in state
            assert "None" not in state
            assert "Kate_Profile" not in state

            # Check that file on disk was also updated/pruned
            with open(tmp_path, encoding="utf-8") as f:
                saved_data = json.load(f)
            assert set(saved_data.keys()) == {"Cat01-U", "Cat02-U", "Cat02-A"}
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
