"""
scratch/db_recent.py — Show the last N messages from evelyn_chat.db

Usage:
    python scratch/db_recent.py          # last 20 messages
    python scratch/db_recent.py 40       # last 40 messages

Output is written to scratch/out/db_recent.txt for clean reading.
"""

# db_recent.py

import sqlite3
import datetime
import sys
import os

DB_PATH = r"C:\Projects\LocalAI\evelyn_chat.db"
OUT_PATH = r"C:\Projects\LocalAI\scratch\out\db_recent.txt"
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20

con = sqlite3.connect(DB_PATH)
rows = con.execute(
    "SELECT id, role, ts, content, thinking, tools_used FROM messages ORDER BY id DESC LIMIT ?",
    (limit,)
).fetchall()
con.close()

lines = []
lines.append(f"=== Last {limit} messages (oldest first) ===")
lines.append(f"DB: {DB_PATH}")
lines.append("")

for r in reversed(rows):
    id_, role, ts, content, thinking, tools_used = r
    dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    header = f"[{dt}] [{role.upper()}] id={id_}"
    if tools_used:
        header += f" (tools: {tools_used})"
    lines.append(header)
    lines.append(content if content else "(empty)")
    if thinking:
        lines.append(f"  --- THINKING ---")
        lines.append(thinking[:400])
    lines.append("---")

with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines))

print(f"Written {len(rows)} messages to: {OUT_PATH}")
