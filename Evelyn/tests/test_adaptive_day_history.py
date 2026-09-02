"""Unit test to verify adaptive day-bound history loading, token budgeting, and pruning."""

import pathlib
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime, time as dtime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

import evelyn_server


def test_full_day_history_loading_without_40_msg_cap():
    """Verify that load_history loads more than 40 messages from today if within token budget."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = pathlib.Path(tmp_dir) / "test_chat.db"
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        con.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                thinking TEXT,
                ts REAL NOT NULL,
                tools_used TEXT,
                tool_metadata TEXT,
                channel_id TEXT DEFAULT 'main'
            )
        """)

        # Generate 60 messages for today (30 user/assistant pairs)
        now_dt = datetime.now(UTC).astimezone()
        today_midnight = datetime.combine(now_dt.date(), dtime.min).replace(tzinfo=UTC).astimezone().timestamp()

        for i in range(60):
            role = "user" if i % 2 == 0 else "assistant"
            content = f"Message turn {i} with brief content."
            msg_ts = today_midnight + 3600 + (i * 60)
            con.execute("INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)", (role, content, msg_ts))

        con.commit()
        con.close()

        orig_get_db = evelyn_server.get_db
        try:
            def mock_get_db():
                c = sqlite3.connect(db_path)
                c.row_factory = sqlite3.Row
                return c

            evelyn_server.get_db = mock_get_db
            history = evelyn_server.load_history()

            # Should load all 60 messages since token budget allows it
            assert len(history) == 60, f"Expected 60 messages loaded, got {len(history)}"
            assert history[0]["content"].endswith("Message turn 0 with brief content.")
            assert history[-1]["content"].endswith("Message turn 59 with brief content.")
        finally:
            evelyn_server.get_db = orig_get_db


def test_history_token_budget_pruning_and_turn_integrity():
    """Verify that when history exceeds safe token budget, it prunes older messages while preserving turn integrity."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = pathlib.Path(tmp_dir) / "test_chat.db"
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        con.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                thinking TEXT,
                ts REAL NOT NULL,
                tools_used TEXT,
                tool_metadata TEXT,
                channel_id TEXT DEFAULT 'main'
            )
        """)

        now_dt = datetime.now(UTC).astimezone()
        today_midnight = datetime.combine(now_dt.date(), dtime.min).replace(tzinfo=UTC).astimezone().timestamp()

        # Insert 10 very large messages that will exceed the safe token budget
        # Safe history budget is ~16,000 tokens (~48,000 chars)
        large_block = "A" * 25000  # ~8,300 tokens per message
        for i in range(10):
            role = "user" if i % 2 == 0 else "assistant"
            content = f"Turn {i}: {large_block}"
            msg_ts = today_midnight + 3600 + (i * 60)
            con.execute("INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)", (role, content, msg_ts))

        con.commit()
        con.close()

        orig_get_db = evelyn_server.get_db
        try:
            def mock_get_db():
                c = sqlite3.connect(db_path)
                c.row_factory = sqlite3.Row
                return c

            evelyn_server.get_db = mock_get_db
            history = evelyn_server.load_history()

            # Should be pruned to stay under safe budget
            assert len(history) < 10, f"Expected history to be pruned, got {len(history)}"
            # Should end with an assistant message
            assert history[-1]["role"] == "assistant"
            # Should start with a user or system message
            assert history[0]["role"] in ("user", "system")
        finally:
            evelyn_server.get_db = orig_get_db
