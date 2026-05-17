"""
scratch/db_tool_calls.py — Show all messages where tools were actually fired

Queries by tools_used column and by role='tool' (if applicable).
Output is written to scratch/out/db_tool_calls.txt for clean reading.
"""

# db_tool_calls.py

import sqlite3
import datetime
import os

DB_PATH = r"C:\Projects\LocalAI\evelyn_chat.db"
OUT_PATH = r"C:\Projects\LocalAI\scratch\out\db_tool_calls.txt"
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

con = sqlite3.connect(DB_PATH)

# Messages where tools_used is populated
tool_rows = con.execute(
    "SELECT id, role, ts, content, tools_used FROM messages "
    "WHERE tools_used IS NOT NULL AND tools_used != '' ORDER BY id ASC"
).fetchall()

# All distinct roles (to confirm if 'tool' role ever appears)
roles = [r[0] for r in con.execute("SELECT DISTINCT role FROM messages").fetchall()]

# DB summary
total = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
mn, mx = con.execute("SELECT MIN(ts), MAX(ts) FROM messages").fetchone()

con.close()

lines = []
lines.append("=== Tool Call Audit ===")
lines.append(f"DB: {DB_PATH}")
lines.append(f"Total messages: {total}")
lines.append(f"Earliest: {datetime.datetime.fromtimestamp(mn).strftime('%Y-%m-%d %H:%M:%S')}")
lines.append(f"Latest:   {datetime.datetime.fromtimestamp(mx).strftime('%Y-%m-%d %H:%M:%S')}")
lines.append(f"Distinct roles: {roles}")
lines.append(f"Messages with tools_used populated: {len(tool_rows)}")
lines.append("")

if not tool_rows:
    lines.append("*** NO TOOL CALLS RECORDED — the model has never successfully fired a tool. ***")
else:
    lines.append("--- Tool-firing messages ---")
    for id_, role, ts, content, tools_used in tool_rows:
        dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"[{dt}] [{role.upper()}] id={id_} | tools: {tools_used}")
        lines.append(content[:500] if content else "(empty)")
        lines.append("---")

with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines))

print(f"Written to: {OUT_PATH}")
