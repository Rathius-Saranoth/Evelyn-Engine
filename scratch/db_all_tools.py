"""
scratch/db_all_tools.py — Full breakdown of all tools ever recorded in tools_used column
"""

import sqlite3
import datetime
import os
from collections import Counter

DB_PATH = r"C:\Projects\LocalAI\evelyn_chat.db"
OUT_PATH = r"C:\Projects\LocalAI\scratch\out\db_all_tools.txt"
os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

con = sqlite3.connect(DB_PATH)
rows = con.execute(
    "SELECT id, ts, content, tools_used FROM messages "
    "WHERE tools_used IS NOT NULL AND tools_used != '' ORDER BY id ASC"
).fetchall()
con.close()

tool_counter = Counter()
lines = []
lines.append("=== All Tool Calls Ever Recorded ===")
lines.append(f"Total assistant messages with tools_used: {len(rows)}")
lines.append("")

for id_, ts, content, tools_used in rows:
    dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    for t in tools_used.split(","):
        tool_counter[t.strip()] += 1
    lines.append(f"[{dt}] id={id_} | tools: {tools_used}")
    lines.append(content[:200] if content else "(empty)")
    lines.append("---")

lines.append("")
lines.append("=== Tool Usage Frequency ===")
for tool, count in tool_counter.most_common():
    lines.append(f"  {tool}: {count}")

with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(lines))

print(f"Written to: {OUT_PATH}")
