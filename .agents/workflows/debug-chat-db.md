---
description: How to inspect Evelyn's chat history and debug conversation issues
title: debug-chat-db.md
date created: 2026-03-25 19:11:00
date modified: 2026-04-04 20:05:10
tags: sqlite, debug, database, query, workflow
---

# Evelyn Chat Debug Workflow

When troubleshooting Evelyn's responses, conversation history, or tool calls, **go directly to the SQLite database**. Do NOT rely on `evelyn_server.log` or console output — they are often garbled or incomplete.

---

## ⚠️ Critical: Always Write Output to a File

**Never** try to read DB contents by printing to stdout in `run_command` — multi-line content with embedded newlines causes terminal output to overlap and become unreadable.

**The correct pattern:**
1. Run a script from `scratch/` that writes results to `scratch/out/<name>.txt`
2. Read the result with `view_file` on the output file

This is already implemented in all scripts below.

---

## Ready-Made Debug Scripts (`scratch/`)

All scripts are run from `C:\Projects\LocalAI` and write output to `scratch/out/`.

// turbo
### Show Recent Messages
```powershell
python scratch\db_recent.py           # last 20 messages
python scratch\db_recent.py 40        # last 40 messages
```
Then read: `view_file C:\Projects\LocalAI\scratch\out\db_recent.txt`

// turbo
### Search by Keyword
```powershell
python scratch\db_search.py journal
python scratch\db_search.py write_journal_entry
```
Then read: `view_file C:\Projects\LocalAI\scratch\out\db_search.txt`

// turbo
### Audit Tool Calls (Did tools actually fire?)
```powershell
python scratch\db_tool_calls.py
```
Then read: `view_file C:\Projects\LocalAI\scratch\out\db_tool_calls.txt`

This also shows all distinct roles in the DB and whether any `tools_used` entries exist.

// turbo
### Check Pending Approval Files
```powershell
python scratch\check_pending.py
```
Then read: `view_file C:\Projects\LocalAI\scratch\out\check_pending.txt`

---

## DB Location & Schema

The chat history lives at: `C:\Projects\LocalAI\evelyn_chat.db`

```
messages (
    id          INTEGER PRIMARY KEY,
    role        TEXT,          -- 'user', 'assistant', 'system'
    content     TEXT,
    thinking    TEXT,
    tools_used  TEXT,          -- comma-separated tool names if tool fired, else NULL
    ts          REAL           -- Unix timestamp float
)
```

> **Note:** There is no `role='tool'` in this DB. Tool results are passed in-memory to the model during Pass 2 but are not persisted. To confirm a tool actually fired, check the `tools_used` column on assistant messages.

---

## Timestamps

`ts` is a Unix float. Convert with Python:
```python
import datetime
datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
```

---

## Toggling Debug Logging

To enable verbose server-side logging (RAG chunks, tool calls, thinking content):

1. Open `C:\Projects\LocalAI\evelyn_config.py`
2. Set `DEBUG_LOGGING = True`
3. No restart required — the server reads this value per-request.

> **Note:** The Debug button was removed from the chat UI. It only showed an alert pointing here anyway.
