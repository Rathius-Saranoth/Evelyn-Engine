import sqlite3
import datetime

con = sqlite3.connect(r'C:\Projects\LocalAI\evelyn_chat.db')

rows = con.execute(
    "SELECT id, role, ts, content FROM messages "
    "WHERE content LIKE '%journal%' OR content LIKE '%write_journal%' "
    "ORDER BY id ASC"
).fetchall()

lines = []
lines.append(f"Journal-related messages: {len(rows)}")
lines.append("=" * 60)
for r in rows:
    id_, role, ts, content = r
    dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"[{dt}] [{role.upper()}] id={id_}")
    lines.append(content[:1200])
    lines.append("---")

con.close()

with open(r'C:\Projects\LocalAI\scratch\journal_debug_out.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print("Done.")
