# undo_thread.py
# date created: 2026-05-17 14:48:24
# date modified: 2026-05-17 14:49:04

"""
Undo the last [THREAD_BREAK] in the Evelyn chat database.
Exists because I accidentally split the thread, often.

Usage:
    python undo_thread.py

This will:
1. Find the last [THREAD_BREAK] in the database.
2. Show you the number of messages since that break.
3. Ask for confirmation to delete the [THREAD_BREAK] message.
4. If confirmed, it deletes the message and merges the threads.
"""

import sqlite3
import os
import argparse

DB_PATH = r"C:\Projects\LocalAI\evelyn_chat.db"

def main():
    parser = argparse.ArgumentParser(description="Undo the last [THREAD_BREAK] in the chat database.")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation and delete immediately.")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"Database not found at: {DB_PATH}")
        return

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Find the most recent thread break
    cur.execute(
        "SELECT id, ts, role, content FROM messages WHERE content = '[THREAD_BREAK]' ORDER BY id DESC LIMIT 1"
    )
    row = cur.fetchone()

    if not row:
        print("No [THREAD_BREAK] messages found in the database.")
        con.close()
        return

    msg_id, ts, role, content = row
    
    # Check how many messages have been sent since this thread break
    cur.execute("SELECT COUNT(*) FROM messages WHERE id > ?", (msg_id,))
    count_after = cur.fetchone()[0]

    print(f"Found latest thread break:")
    print(f"  ID: {msg_id}")
    print(f"  Timestamp: {ts}")
    print(f"  Messages sent since this break: {count_after}")
    
    if count_after > 0:
        print("\nWARNING: There are messages after this thread break.")
        print("Undoing it will merge the new messages into the previous thread.")

    if not args.yes:
        confirm = input(f"\nAre you sure you want to delete message {msg_id}? (y/N): ")
        if confirm.lower() != 'y':
            print("Operation cancelled.")
            con.close()
            return

    cur.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
    con.commit()
    print(f"Successfully deleted message {msg_id}.")
    con.close()

if __name__ == "__main__":
    main()
