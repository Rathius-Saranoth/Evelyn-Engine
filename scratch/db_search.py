"""
scratch/db_search.py — Search evelyn_chat.db for messages containing a keyword

Usage:
    python scratch/db_search.py journal
    python scratch/db_search.py "tool_calls"

Output is written to scratch/out/db_search.txt for clean reading.
"""

import sqlite3
import datetime
import sys
import os

DB_PATH = r"C:\Projects\LocalAI\evelyn_chat.db"
OUT_PATH = r"C:\Projects\LocalAI\scratch\out\db_search.txt"
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

if len(sys.argv) < 2:
    print("Usage: python scratch/db_search.py <keyword>")
    sys.exit(1)

keyword = sys.argv[1]

con = sqlite3.connect(DB_PATH)
rows = con.execute(
    "SELECT id, role, ts, content, tools_used FROM messages "
    "WHERE content LIKE ? ORDER BY id ASC",
    (f"%{keyword}%",)
).fetchall()

# Also check tools_used column
tool_rows = con.execute(
    "SELECT id, role, ts, content, tools_used FROM messages "
    "WHERE tools_used LIKE ? ORDER BY id ASC",
    (f"%{keyword}%",)
).fetchall()
con.close()

lines = []
lines.append(f"=== Search results for: '{keyword}' ===")
lines.append(f"DB: {DB_PATH}")
lines.append(f"Content matches: {len(rows)} | tools_used matches: {len(tool_rows)}")
lines.append("")

all_rows = {r[0]: r for r in rows + tool_rows}  # deduplicate by id
for id_ in sorted(all_rows.keys()):
    id_, role, ts, content, tools_used = all_rows[id_]
    dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    header = f"[{dt}] [{role.upper()}] id={id_}"
    if tools_used:
        header += f" (tools: {tools_used})"
    lines.append(header)
    lines.append(content if content else "(empty)")
    lines.append("---")

with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines))

print(f"Found {len(all_rows)} unique matches. Written to: {OUT_PATH}")
