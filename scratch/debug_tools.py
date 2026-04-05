import sqlite3
import datetime

con = sqlite3.connect(r'C:\Projects\LocalAI\evelyn_chat.db')

# Check for any tool-call related content
rows = con.execute(
    "SELECT id, role, ts, content FROM messages "
    "WHERE content LIKE '%tool%' OR content LIKE '%write_journal_entry%' OR role = 'tool' "
    "ORDER BY id ASC"
).fetchall()

lines = []
lines.append(f"Tool-related messages: {len(rows)}")
lines.append("=" * 60)
for r in rows:
    id_, role, ts, content = r
    dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"[{dt}] [{role.upper()}] id={id_}")
    lines.append(content[:1500])
    lines.append("---")

# Also check all distinct roles in the DB
roles = con.execute("SELECT DISTINCT role FROM messages").fetchall()
lines.append("")
lines.append(f"All distinct roles in DB: {[r[0] for r in roles]}")

# Also look at the message right after the journal requests on Apr 3 and Apr 4
lines.append("")
lines.append("Messages around Apr 3-4 journal request (ids 428-445):")
lines.append("=" * 60)
rows2 = con.execute(
    "SELECT id, role, ts, content FROM messages WHERE id BETWEEN 428 AND 445 ORDER BY id ASC"
).fetchall()
for r in rows2:
    id_, role, ts, content = r
    dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"[{dt}] [{role.upper()}] id={id_}")
    lines.append(content[:1500])
    lines.append("---")

con.close()

with open(r'C:\Projects\LocalAI\scratch\tool_debug_out.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print("Done.")
