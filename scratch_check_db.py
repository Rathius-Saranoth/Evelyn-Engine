import sqlite3

con = sqlite3.connect(r'c:\Projects\LocalAI\data\evelyn_memory.db')
con.row_factory = sqlite3.Row

rows = con.execute("SELECT id, observation, created_at FROM context_entries WHERE status='extracted' AND confidence='high'").fetchall()
for r in rows:
    print(dict(r))
