"""Unit tests to verify chat history bounding with before_id and channel isolation."""

import pathlib
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime, time as dtime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

import evelyn_server


def test_load_history_before_id_bounding():
    """Verify that load_history(before_id=...) excludes the active user message while preserving prior turns."""
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

        # Turn 1: User A & Assistant A
        con.execute("INSERT INTO messages (role, content, ts, channel_id) VALUES (?, ?, ?, ?)",
                    ("user", "Hello Evelyn", today_midnight + 10, "main"))
        con.execute("INSERT INTO messages (role, content, ts, channel_id) VALUES (?, ?, ?, ?)",
                    ("assistant", "Hello Ricky", today_midnight + 12, "main"))

        # Turn 2: User B (interrupted/failed turn)
        con.execute("INSERT INTO messages (role, content, ts, channel_id) VALUES (?, ?, ?, ?)",
                    ("user", "Interrupted query", today_midnight + 20, "main"))

        # Turn 3: User C (current prompt saved at id=4)
        cur = con.execute("INSERT INTO messages (role, content, ts, channel_id) VALUES (?, ?, ?, ?)",
                          ("user", "Current active prompt", today_midnight + 30, "main"))
        user_row_id = cur.lastrowid
        con.commit()
        con.close()

        orig_get_db = evelyn_server.get_db
        try:
            def mock_get_db():
                c = sqlite3.connect(db_path)
                c.row_factory = sqlite3.Row
                return c

            evelyn_server.get_db = mock_get_db

            # Query history with before_id=user_row_id (simulating chat processing)
            history = evelyn_server.load_history(before_id=user_row_id, channel_id="main")

            # Must NOT include Turn 3 (id=4)
            contents = [m["content"] for m in history]
            for c in contents:
                assert "Current active prompt" not in c, "Active prompt must be excluded from history"

            # Must include Turn 1 and Turn 2 (preserving interrupted turn when bounded)
            assert any("Hello Evelyn" in c for c in contents)
            assert any("Hello Ricky" in c for c in contents)
            assert any("Interrupted query" in c for c in contents)
            assert len(history) == 3
        finally:
            evelyn_server.get_db = orig_get_db


def test_load_history_channel_isolation():
    """Verify that messages from different channels are isolated in load_history."""
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

        con.execute("INSERT INTO messages (role, content, ts, channel_id) VALUES (?, ?, ?, ?)",
                    ("user", "Main channel message", today_midnight + 10, "main"))
        con.execute("INSERT INTO messages (role, content, ts, channel_id) VALUES (?, ?, ?, ?)",
                    ("assistant", "Main channel reply", today_midnight + 12, "main"))
        con.execute("INSERT INTO messages (role, content, ts, channel_id) VALUES (?, ?, ?, ?)",
                    ("user", "Sidecar channel message", today_midnight + 20, "sidecar"))
        con.execute("INSERT INTO messages (role, content, ts, channel_id) VALUES (?, ?, ?, ?)",
                    ("assistant", "Sidecar channel reply", today_midnight + 22, "sidecar"))
        con.commit()
        con.close()

        orig_get_db = evelyn_server.get_db
        try:
            def mock_get_db():
                c = sqlite3.connect(db_path)
                c.row_factory = sqlite3.Row
                return c

            evelyn_server.get_db = mock_get_db

            main_history = evelyn_server.load_history(channel_id="main")
            sidecar_history = evelyn_server.load_history(channel_id="sidecar")

            assert len(main_history) == 2
            assert all("Sidecar" not in m["content"] for m in main_history)

            assert len(sidecar_history) == 2
            assert all("Main" not in m["content"] for m in sidecar_history)
        finally:
            evelyn_server.get_db = orig_get_db


def test_load_history_max_messages_cap():
    """Verify that load_history truncates history to MAX_HISTORY_MESSAGES and begins with a user turn."""
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

        # Insert 50 alternating messages (25 user, 25 assistant)
        for i in range(50):
            role = "user" if i % 2 == 0 else "assistant"
            con.execute(
                "INSERT INTO messages (role, content, ts, channel_id) VALUES (?, ?, ?, ?)",
                (role, f"Message {i} ({role})", today_midnight + 10 + i, "main")
            )
        con.commit()
        con.close()

        orig_get_db = evelyn_server.get_db
        try:
            def mock_get_db():
                c = sqlite3.connect(db_path)
                c.row_factory = sqlite3.Row
                return c

            evelyn_server.get_db = mock_get_db

            # load_history with default cap (40)
            history = evelyn_server.load_history(channel_id="main")

            assert len(history) <= 40, f"Expected at most 40 messages, got {len(history)}"
            # First message must be a user turn to maintain dialog turn integrity
            assert history[0]["role"] == "user", f"Expected first message to be user, got {history[0]['role']}"
            # Verify newest messages are preserved
            assert history[-1]["content"] == "Message 49 (assistant)"
        finally:
            evelyn_server.get_db = orig_get_db

