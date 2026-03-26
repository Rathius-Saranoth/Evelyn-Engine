---
description: How to inspect Evelyn's chat history and debug conversation issues
---

# Evelyn Chat Debug Workflow

When troubleshooting Evelyn's responses, conversation history, or tool calls, **go directly to the SQLite database**. Do NOT rely on `evelyn_server.log` or console output — they are often garbled or incomplete.

## Querying the Chat Database

The chat history lives at: `C:\Projects\LocalAI\evelyn_chat.db`

// turbo
1. Run a PowerShell query to inspect recent messages:

```powershell
& 'C:\Users\ricky\AppData\Local\Programs\Python\Python311\python.exe' -c "
import sqlite3, json
con = sqlite3.connect(r'C:\Projects\LocalAI\evelyn_chat.db')
rows = con.execute('SELECT role, content, thinking, ts FROM messages ORDER BY id DESC LIMIT 20').fetchall()
for r in reversed(rows):
    role, content, thinking, ts = r
    print(f'[{role.upper()}] {content[:300]}')
    if thinking:
        print(f'  <think> {thinking[:200]}')
    print('---')
con.close()
"
```

## Useful Queries

**Count all messages:**
```sql
SELECT COUNT(*) FROM messages;
```

**See last N messages with timestamps:**
```sql
SELECT id, role, substr(content,1,100), ts FROM messages ORDER BY id DESC LIMIT 10;
```

**Find messages containing a keyword:**
```sql
SELECT id, role, content FROM messages WHERE content LIKE '%<keyword>%' ORDER BY id DESC LIMIT 5;
```

**Clear all history (use with caution):**
```sql
DELETE FROM messages;
```

You can run these with the Python sqlite3 module or any SQLite browser tool (e.g. DB Browser for SQLite).

## Toggling Debug Logging

To enable verbose server-side logging (RAG chunks, tool calls, thinking content):

1. Open `C:\Projects\LocalAI\evelyn_config.py`
2. Set `DEBUG_LOGGING = True`
3. No restart required — the server reads this value per-request.

> **Note:** The Debug button was removed from the chat UI. It only showed an alert pointing here anyway.
