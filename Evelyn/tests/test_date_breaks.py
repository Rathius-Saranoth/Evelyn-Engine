"""Unit test to verify dynamic date break injection in load_history."""

import time
import tempfile
import sys
import pathlib
import sqlite3
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

import evelyn_server


def test_date_break_injection_in_load_history():
    """Test that load_history dynamically injects system date breaks across midnight."""
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
                tool_metadata TEXT
            )
        """)

        # Day 1: Aug 4, 2026
        dt1 = datetime(2026, 8, 4, 20, 0, 0)
        ts1 = dt1.timestamp()
        dt2 = datetime(2026, 8, 4, 20, 5, 0)
        ts2 = dt2.timestamp()

        # Day 2: Aug 5, 2026
        dt3 = datetime(2026, 8, 5, 9, 0, 0)
        ts3 = dt3.timestamp()
        dt4 = datetime(2026, 8, 5, 9, 5, 0)
        ts4 = dt4.timestamp()

        con.execute("INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)", ("user", "Day 1 User Msg", ts1))
        con.execute("INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)", ("assistant", "Day 1 Assistant Reply", ts2))
        con.execute("INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)", ("user", "Day 2 User Msg", ts3))
        con.execute("INSERT INTO messages (role, content, ts) VALUES (?, ?, ?)", ("assistant", "Day 2 Assistant Reply", ts4))
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

            roles = [m["role"] for m in history]
            contents = [m["content"] for m in history]

            # Expect system date break between Day 1 and Day 2
            assert "system" in roles, f"Expected system role in {roles}"
            system_msgs = [m for m in history if m["role"] == "system"]
            assert len(system_msgs) == 1, f"Expected 1 system msg, got {len(system_msgs)}"
            assert "--- Date Changed: Wednesday, Aug 05, 2026 ---" in system_msgs[0]["content"], (
                f"Unexpected content: {system_msgs[0]['content']}"
            )

            print("PASS: test_date_break_injection_in_load_history passed successfully!")
        finally:
            evelyn_server.get_db = orig_get_db


if __name__ == "__main__":
    test_date_break_injection_in_load_history()
