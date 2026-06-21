# evelyn_server.py
# date created: 2026-03-23 15:43:21
# date modified: 2026-06-21 07:25:33
# tags: #server, #fastAPI, #RAG, #async, #backend

"""
evelyn_server.py — Custom Evelyn backend server.

FastAPI app providing:
  - POST /chat       — Streaming chat with tool loop, RAG injection, inline think-tag parsing
  - GET  /history    — Chat history
  - DELETE /history  — Clear chat history
  - GET  /status     — Health check
  - GET  /           — Serve the chat UI

Auth: X-Evelyn-Key header checked against EVELYN_API_KEY env var.
Run: python evelyn_server.py
"""

import asyncio
import json
import importlib
import sqlite3
import sys
import time
import httpx
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
TOOLS_DIR = BASE_DIR / "Evelyn" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
PERSONA_DIR = BASE_DIR / "Evelyn" / "persona"

import evelyn_config as cfg
from evelyn_tools import MODEL_TOOL_DEFINITIONS, TOOL_FUNCTIONS
from chroma_rag import build_rag_context
from context_summarizer import (
    build_conversation_summary,
    trigger_summary_update,
    invalidate_summary_cache,
    cancel_pending_summary,
)
from fact_consolidator import run_consolidation, cancel_pending_consolidation
from fact_extractor import run_extraction, cancel_pending_extraction

# ---------------------------------------------------------------------------
# Console colors (ANSI — native on Windows Terminal, VS Code, etc.)
# ---------------------------------------------------------------------------
_RST = "\033[0m"
_BLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[91m"
_GRN = "\033[92m"
_YEL = "\033[93m"
_CYN = "\033[96m"
_MAG = "\033[95m"

# ---------------------------------------------------------------------------
# Activity tracking for idle-time consolidation
# ---------------------------------------------------------------------------

# Updated at the top of every chat_stream() call. The consolidation loop
# checks this to decide whether the server is idle enough to run.
_last_activity_ts: float = time.time()
_last_self_initiate_ts: float = 0.0
_active_research_processes = {}


def terminate_research_process(task_id: str):
    """Immediately terminate the active background subprocess for a research task if running."""
    proc = _active_research_processes.pop(task_id, None)
    if proc:
        try:
            print(f"[RESEARCH TERMINATE] Terminating active subprocess for task {task_id}", flush=True)
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        except Exception as e:
            print(f"[RESEARCH TERMINATE ERROR] Failed to terminate subprocess {task_id}: {e}", flush=True)


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------


def dlog(*args):
    """Debug-only log. Reads cfg.DEBUG_LOGGING per-call so toggling takes effect live."""
    if cfg.DEBUG_LOGGING:
        print(f"{_DIM}[DEBUG]", *args, end=f"{_RST}\n")


# ---------------------------------------------------------------------------
# System prompt loader
# ---------------------------------------------------------------------------


def get_research_context() -> str:
    """Assemble a context block of recently completed, stalled, or quarantined research tasks.

    Returns:
        str: A formatted context block of task notifications and warnings,
            or an empty string if no tasks exist.
    """
    import os
    import json
    import re
    research_dir = cfg.RESEARCH_DATA_DIR
    if not os.path.exists(research_dir):
        return ""
        
    stalled_tasks = []
    unnotified_count = 0
    
    for d in os.listdir(research_dir):
        task_dir = os.path.join(research_dir, d)
        if os.path.isdir(task_dir):
            state_file = os.path.join(task_dir, "state.json")
            if os.path.exists(state_file):
                try:
                    with open(state_file, "r", encoding="utf-8") as f:
                        state = json.load(f)
                        status = state.get("status")
                        if status == "done" and not state.get("quarantined"):
                            if not state.get("notified", False):
                                unnotified_count += 1
                        elif status == "needs_guidance" or state.get("quarantined"):
                            stalled_tasks.append(state)
                except Exception:
                    pass
                    
    lines = []
    if stalled_tasks:
        lines.append("\n=== STALLED / QUARANTINED RESEARCH TASKS ===")
        lines.append("You have active research tasks that are struggling to find relevant information or have been quarantined due to low confidence.")
        lines.append("You should mention these to Ricky so he can provide guidance, or you can use the 'guide_research' tool to adjust the search terms yourself.")
        for t in stalled_tasks:
            query = t.get("query", "Unknown Topic")
            task_id = t.get("task_id", "")
            status = "NEEDS GUIDANCE" if t.get("status") == "needs_guidance" else "QUARANTINED"
            idx = t.get("current_sq_idx", 0)
            plan = t.get("plan", {})
            sqs = plan.get("sub_questions", [])
            sq_query = sqs[idx].get("query", "") if 0 <= idx < len(sqs) else ""
            lines.append(f"- Topic: {query}\n  Task ID: {task_id}\n  Status: {status}\n  Stuck on Sub-Question: {sq_query}\n")

    if unnotified_count > 0:
        lines.append(f"\nSystem Notification: You have {unnotified_count} newly completed deep research task(s). Use the 'check_new_research' tool to review them.")
            
    return "\n".join(lines)


def get_upcoming_agenda_prompt_context() -> str:
    """Fetch a high-level summary notification of upcoming agenda items to inject into the system prompt.

    Avoids token bloat by notifying about counts and only listing urgent pending reminders.
    """
    try:
        import sys
        TOOLS_DIR = r"C:\Projects\LocalAI\Evelyn\tools"
        if TOOLS_DIR not in sys.path:
            sys.path.append(TOOLS_DIR)
        import reminders
        
        # 1. Fetch upcoming agenda items for the next 24 hours
        items = reminders.get_unified_agenda(days=1)
        gcal_count = sum(1 for x in items if x["type"] == "calendar_event")
        
        # 2. Fetch all pending reminders
        pending_reminders = reminders.list_reminders(status="pending")
        
        lines = []
        if gcal_count > 0:
            lines.append(f"System Notification: You have {gcal_count} upcoming calendar event(s) in the next 24 hours. Use the 'get_agenda' tool to view them.")
        
        if pending_reminders:
            reminder_lines = []
            import datetime
            now = datetime.datetime.now()
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            # We check relative to local timezone if possible, or UTC. Simple string comparison:
            soon_threshold = (now + datetime.timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
            
            for r in pending_reminders:
                due_dt = r["due_at"]
                # Highlight if due/past due or due soon (within 12 hours)
                if due_dt <= soon_threshold:
                    status_lbl = "PAST DUE" if due_dt < now_str else "DUE SOON"
                    reminder_lines.append(f"  - [{status_lbl}] ID {r['id']}: \"{r['title']}\" (due {r['due_at']})")
            
            if reminder_lines:
                lines.append("System Notification: Pending reminders requiring attention:")
                lines.extend(reminder_lines)
                lines.append("Use the 'complete_reminder' tool to mark them completed when done.")
        
        if lines:
            return "\n" + "\n".join(lines)
        return ""
    except Exception as e:
        return f"\n[Agenda Error] Failed to load agenda notification: {e}"



def load_system_prompt() -> str:
    """Assemble the system prompt from narrative persona files and direct instructions.

    Returns:
        str: The combined and formatted system prompt.
    """
    import re
    # Matches YAML frontmatter with either LF or CRLF line endings (Windows files use CRLF)
    _FRONTMATTER_RE = re.compile(r"^---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
    parts = []
    date_str = datetime.now().strftime("%A, %B %d, %Y")
    time_str = datetime.now().strftime("%I:%M %p")
    parts.append(f"The current date and time is {date_str} - {time_str}.")
    parts.append(
        "Before responding, briefly verify any facts about people, relationships, or past events "
        "from your knowledge. Use <think> tags for this verification step. For complex questions "
        "requiring multi-step logic, use <think> tags for full reasoning. Keep thinking concise -- "
        "you don't need lengthy chains for casual conversation."
    )
    for fname in [
        "Evelyn_Narrative_Persona.md",
        "Ricky_Narrative_Profile.md",
        "System_Directives.md",
    ]:
        fpath = PERSONA_DIR / fname
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8")
            content = _FRONTMATTER_RE.sub("", content)
            parts.append(content)
            
    # Inject research context if present (dynamic per-request, not cacheable)
    # Agenda context is injected as a user-turn prefix in _process_chat_background()
    # to avoid KV-cache staleness (see Tweak 2 — 2026-06-21).

    return "\n\n".join(parts)



# ---------------------------------------------------------------------------
# Time-gap awareness
# ---------------------------------------------------------------------------


def get_time_gap_context() -> str | None:
    """Return a time-gap annotation if enough time has passed since the last message.

    Returns:
        str | None: A bracketed explanation of elapsed time if exceeding
            5 minutes, otherwise None.
    """
    con = get_db()
    row = con.execute(
        "SELECT ts FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    if not row:
        return None  # First message ever

    from datetime import timedelta as _td

    last_ts = datetime.fromtimestamp(row["ts"])
    now = datetime.now()
    delta = now - last_ts

    if delta < _td(minutes=5):
        return None  # Continuous conversation, no annotation needed

    time_str = now.strftime("%I:%M %p").lstrip("0")

    if delta < _td(hours=1):
        mins = int(delta.total_seconds() // 60)
        return f"[About {mins} minutes have passed since the last message. Current time: {time_str}.]"
    elif delta < _td(hours=6):
        hrs = delta.total_seconds() / 3600
        label = f"{hrs:.1f}".rstrip("0").rstrip(".")
        return f"[About {label} hours have passed since the last message. Current time: {time_str}.]"
    else:
        days = delta.days
        hrs = delta.seconds // 3600
        parts = []
        if days:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hrs:
            parts.append(f"{hrs} hour{'s' if hrs != 1 else ''}")
        gap = " and ".join(parts) if parts else "a long time"
        return f"[{gap} have passed since the last message. Current time: {time_str}.]"


# ---------------------------------------------------------------------------
# SQLite chat history
# ---------------------------------------------------------------------------


def get_db():
    """Return a new SQLite connection to the chat history DB with row_factory set."""
    con = sqlite3.connect(cfg.CHAT_DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _init_fts5_index(con: sqlite3.Connection) -> None:
    """Create the FTS5 full-text search index and sync triggers for chat history.

    Uses a content-table FTS5 virtual table mirroring the messages.content column.
    Content tables store no data — the FTS5 index is a pure pointer layer. Three
    triggers keep the index in sync with the messages table at zero extra overhead
    per INSERT/UPDATE/DELETE. A one-time rebuild populates the index from any
    existing rows. Subsequent runs are no-ops because CREATE VIRTUAL TABLE uses
    IF NOT EXISTS.

    Args:
        con: An open SQLite connection to evelyn_chat.db.
    """
    # FTS5 virtual table — content= points at the actual data table
    con.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
        USING fts5(content, role UNINDEXED, content='messages', content_rowid='id')
    """)
    # INSERT trigger: index new messages as they arrive
    con.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_fts_insert
        AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content, role)
            VALUES (new.id, new.content, new.role);
        END
    """)
    # DELETE trigger: remove from FTS index when message is deleted
    con.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_fts_delete
        AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content, role)
            VALUES ('delete', old.id, old.content, old.role);
        END
    """)
    # UPDATE trigger: remove old entry, insert new entry
    con.execute("""
        CREATE TRIGGER IF NOT EXISTS messages_fts_update
        AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, content, role)
            VALUES ('delete', old.id, old.content, old.role);
            INSERT INTO messages_fts(rowid, content, role)
            VALUES (new.id, new.content, new.role);
        END
    """)
    # Rebuild index from any rows that existed before the FTS table was created.
    # This is idempotent — FTS5 rebuild is a full replace, safe to call once.
    try:
        fts_count = con.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
        msg_count = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        if fts_count == 0 and msg_count > 0:
            con.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
            print(f"[FTS5] Rebuilt search index for {msg_count} existing messages.", flush=True)
    except Exception as e:
        print(f"[FTS5] Index rebuild skipped: {e}", flush=True)


def init_db():
    """Create all chat DB tables if they do not exist; migrate existing DBs; build FTS5 index."""
    con = get_db()
    con.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            role        TEXT NOT NULL,
            content     TEXT NOT NULL,
            thinking    TEXT,
            tools_used  TEXT,
            ts          REAL NOT NULL
        )
    """)
    # Migrate: add tools_used column if missing (existing DBs)
    try:
        con.execute("ALTER TABLE messages ADD COLUMN tools_used TEXT")
    except Exception:
        pass  # Column already exists

    con.execute("""
        CREATE TABLE IF NOT EXISTS message_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL,
            prompt_eval_count INTEGER,
            prompt_eval_duration REAL,
            eval_count INTEGER,
            eval_duration REAL,
            total_duration REAL,
            load_duration REAL,
            FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
        )
    """)

    # Reminders and Calendar cache tables (Hermes Tier 2 #7)
    con.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            description TEXT,
            due_at      TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  TEXT NOT NULL,
            notified    INTEGER DEFAULT 0,
            recurrence_rule TEXT
        )
    """)
    # Migrate: add recurrence_rule column if missing (existing DBs)
    try:
        con.execute("ALTER TABLE reminders ADD COLUMN recurrence_rule TEXT")
    except Exception:
        pass  # Column already exists

    con.execute("""
        CREATE TABLE IF NOT EXISTS calendar_events (
            id          TEXT PRIMARY KEY,
            summary     TEXT NOT NULL,
            description TEXT,
            start_at    TEXT NOT NULL,
            end_at      TEXT NOT NULL,
            location    TEXT,
            source      TEXT NOT NULL DEFAULT 'google',
            last_sync   TEXT NOT NULL
        )
    """)

    # FTS5 full-text search index (Hermes Tier 2 #6)
    _init_fts5_index(con)

    con.commit()
    con.close()



PLACEHOLDER_MARKER = "[Response interrupted"
THREAD_BREAK_MARKER = "[THREAD_BREAK]"

# Gemma 4 intermittently leaks non-standard internal token strings into the
# content stream after heavy tool calls (journal, multi-step reasoning, etc.).
# These are stripped silently in _stream_content() as a safety net.
# Add new patterns here as model quirks are discovered.
_LEAKED_MODEL_TOKENS = [
    "thought\n",
    "<channel|>",
    "lania_thought\n",
    "<tool_call|>",
]


def _time_of_day_label(ts: float | None) -> str:
    """Convert a unix timestamp to a 'Day Mon DD \u00b7 period' label.

    Returns a bracketed label like '[Mon Jun 09 \u00b7 afternoon] ' for use as a
    transcript prefix. Returns an empty string if ts is absent or invalid.
    """
    if not ts:
        return ""
    try:
        d = datetime.fromtimestamp(ts)
        hour = d.hour
        if 5 <= hour < 12:
            period = "morning"
        elif 12 <= hour < 17:
            period = "afternoon"
        elif 17 <= hour < 21:
            period = "evening"
        else:
            period = "night"
        return f"[{d.strftime('%a %b %d')} \u00b7 {period}] "
    except (OSError, OverflowError, ValueError):
        return ""


def load_history() -> list[dict]:
    """Load recent chat history for the model, bounded by thread breaks and caps.

    Filters out empty, placeholder, and thread-break messages, and ensures
    no orphaned trailing user messages confuse the model. Prepends a temporal
    time-of-day label to each message content string to give the model historical
    ordering and chronological context.

    Returns:
        list[dict]: A list of message dictionaries with "role" and "content".
    """
    con = get_db()
    # Find the latest thread-break marker (if any)
    brk = con.execute(
        "SELECT id FROM messages WHERE content = ? ORDER BY id DESC LIMIT 1",
        (THREAD_BREAK_MARKER,),
    ).fetchone()
    after_id = brk["id"] if brk else 0

    limit = cfg.MAX_HISTORY_MESSAGES
    rows = con.execute(
        "SELECT role, content, ts FROM messages WHERE id > ? ORDER BY id DESC LIMIT ?",
        (after_id, limit),
    ).fetchall()
    con.close()

    # Rows come back newest-first; reverse to chronological order
    rows = list(reversed(rows))

    # Skip empty-content rows, placeholder messages, and thread-break markers.
    # Placeholders must NOT be sent to the model -- they confuse magistral
    # and cause it to produce empty responses on every subsequent request.
    messages = [
        {
            "role": r["role"],
            "content": (
                f"{_time_of_day_label(r['ts'])}{r['content']}"
                if r["role"] == "user"
                else r["content"]
            ),
        }
        for r in rows
        if r["content"].strip()
        and not r["content"].startswith(PLACEHOLDER_MARKER)
        and r["content"] != THREAD_BREAK_MARKER
    ]
    # Strip orphaned trailing user messages (no assistant response yet).
    # These form double-user-message chains that confuse the model.
    while messages and messages[-1]["role"] == "user":
        messages.pop()


    if brk:
        dlog(f"History: thread-break at id={after_id}, returning {len(messages)} msgs (limit {limit})")
    elif len(rows) >= limit:
        dlog(f"History: capped at {limit} msgs (oldest trimmed)")
    else:
        dlog(f"History: {len(messages)} msgs (no cap hit)")

    return messages


def save_message(role: str, content: str, thinking: str = None, tools_used: str = None) -> None:
    """Insert a message row into the chat history DB (fire and forget — no return value).

    Args:
        role: The role of the sender (e.g., "user", "assistant").
        content: The text content of the message.
        thinking: Optional thinking/reasoning process text.
        tools_used: Optional comma-separated list of tool names invoked.
    """
    con = get_db()
    con.execute(
        "INSERT INTO messages (role, content, thinking, tools_used, ts) VALUES (?, ?, ?, ?, ?)",
        (role, content, thinking, tools_used, time.time()),
    )
    con.commit()
    con.close()


def save_message_get_id(role: str, content: str, thinking: str = None, tools_used: str = None) -> int:
    """Insert a message row into the chat history database and return its row ID.

    Args:
        role: The role of the sender (e.g., "user", "assistant").
        content: The text content of the message.
        thinking: Optional thinking/reasoning process text.
        tools_used: Optional comma-separated list of tool names invoked.

    Returns:
        int: The auto-incremented database row ID of the inserted message.
    """
    con = get_db()
    cur = con.execute(
        "INSERT INTO messages (role, content, thinking, tools_used, ts) VALUES (?, ?, ?, ?, ?)",
        (role, content, thinking, tools_used, time.time()),
    )
    row_id = cur.lastrowid
    con.commit()
    con.close()
    return row_id


def update_message(row_id: int, content: str, thinking: str = None, tools_used: str = None, tool_metadata: str = None):
    """Update an existing message row in the chat history database.

    Args:
        row_id: The database row ID of the message to update.
        content: The new text content.
        thinking: Optional thinking/reasoning process text to save.
        tools_used: Optional comma-separated list of tools used.
        tool_metadata: Optional JSON-serialized metadata for tools.
    """
    con = get_db()
    con.execute(
        "UPDATE messages SET content = ?, thinking = ?, tools_used = ?, tool_metadata = ? WHERE id = ?",
        (content, thinking, tools_used, tool_metadata, row_id),
    )
    con.commit()
    con.close()


def save_message_metrics(message_id: int, metrics: dict):
    """Insert API call metrics for a given message into the database.

    Args:
        message_id: The ID of the message associated with these metrics.
        metrics: A dictionary containing metrics like prompt_eval_count,
            eval_count, total_duration, etc.
    """
    if not metrics:
        return
    con = get_db()
    con.execute(
        """INSERT INTO message_metrics 
           (message_id, prompt_eval_count, prompt_eval_duration, eval_count, eval_duration, total_duration, load_duration)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            message_id,
            metrics.get("prompt_eval_count"),
            metrics.get("prompt_eval_duration"),
            metrics.get("eval_count"),
            metrics.get("eval_duration"),
            metrics.get("total_duration"),
            metrics.get("load_duration")
        )
    )
    con.commit()
    con.close()


def clear_history():
    """Delete all rows from the messages table (full conversation reset)."""
    con = get_db()
    con.execute("DELETE FROM messages")
    con.commit()
    con.close()


def delete_last_assistant_message() -> str | None:
    """Delete the last assistant message and retrieve the prior user query.

    Returns:
        str | None: The content of the last user message, or None if none exists.
    """
    con = get_db()
    # Find and delete the last assistant row
    last_asst = con.execute(
        "SELECT id FROM messages WHERE role = 'assistant' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if last_asst:
        con.execute("DELETE FROM messages WHERE id = ?", (last_asst["id"],))
        con.commit()
    # Retrieve the last user message (should now be the tail)
    last_user = con.execute(
        "SELECT content FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    con.close()
    return last_user["content"] if last_user else None


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def check_auth(request: Request):
    """Raise HTTP 401 if the request is missing or has a wrong X-Evelyn-Key header.

    No-ops when cfg.API_KEY is unset (local-only mode).
    """
    if not cfg.API_KEY:
        return  # No key configured = open (local-only use)
    key = request.headers.get("X-Evelyn-Key", "")
    if key != cfg.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ---------------------------------------------------------------------------
# Ollama interaction
# ---------------------------------------------------------------------------


async def call_ollama_stream(messages: list[dict], tools: list[dict] = None):
    """Stream a chat request to Ollama.

    Note that streaming combined with think=True silently swallows tool_call
    tokens in Ollama. Thus, this is only used for follow-up content passes.

    Args:
        messages: A list of message objects mapping to conversation history.
        tools: Optional list of tool definitions.

    Yields:
        str: Raw JSON response lines from the Ollama server.
    """
    use_think = cfg.THINK
    options = {"num_ctx": cfg.NUM_CTX}
    for key, val in {
        "temperature": cfg.TEMPERATURE,
        "min_p": cfg.MIN_P,
        "top_k": cfg.TOP_K,
        "top_p": cfg.TOP_P,
        "repeat_penalty": cfg.REPEAT_PENALTY,
        "repeat_last_n": cfg.REPEAT_LAST_N,
        "seed": cfg.SEED,
        "num_predict": cfg.NUM_PREDICT,
    }.items():
        if val is not None:
            options[key] = val
    if cfg.STOP_SEQUENCES:
        options["stop"] = cfg.STOP_SEQUENCES
    payload = {
        "model": cfg.MODEL_NAME,
        "messages": messages,
        "stream": True,
        "options": options,
        "think": use_think,
    }
    if tools:
        payload["tools"] = tools

    dlog(
        "Streaming to Ollama. think:",
        use_think,
        "Roles:",
        [m["role"] for m in messages],
    )

    async with httpx.AsyncClient(timeout=600) as client:
        async with client.stream(
            "POST", f"{cfg.OLLAMA_URL}/api/chat", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.strip():
                    yield line


async def call_ollama_full(messages: list[dict], tools: list[dict] = None) -> dict:
    """Perform a non-streaming Ollama call, primarily used for tool detection.

    Args:
        messages: A list of message objects representing the prompt history.
        tools: Optional list of tool definitions available to the model.

    Returns:
        dict: The full parsed JSON response dictionary from the Ollama API.
    """
    options = {"num_ctx": cfg.NUM_CTX}
    for key, val in {
        "temperature": cfg.TEMPERATURE,
        "min_p": cfg.MIN_P,
        "top_k": cfg.TOP_K,
        "top_p": cfg.TOP_P,
        "repeat_penalty": cfg.REPEAT_PENALTY,
        "repeat_last_n": cfg.REPEAT_LAST_N,
        "seed": cfg.SEED,
        "num_predict": cfg.NUM_PREDICT,
    }.items():
        if val is not None:
            options[key] = val
    if cfg.STOP_SEQUENCES:
        options["stop"] = cfg.STOP_SEQUENCES
    payload = {
        "model": cfg.MODEL_NAME,
        "messages": messages,
        "stream": False,
        "options": options,
        "think": cfg.THINK,
        "tools": tools or [],
    }
    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(f"{cfg.OLLAMA_URL}/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


def dispatch_tool(name: str, args: dict) -> str:
    """Execute a registered tool by name with the provided arguments.

    Args:
        name: The name of the tool function to run.
        args: A dictionary of keyword arguments passed to the tool.

    Returns:
        str: The text output or result of the tool execution, or an error message.
    """
    fn = TOOL_FUNCTIONS.get(name)
    if not fn:
        return f"Error: unknown tool '{name}'"
    try:
        dlog(f"Tool call: {name}({args})")
        result = fn(**args)
        dlog(f"Tool result preview: {str(result)[:200]}")
        return result
    except Exception as e:
        import traceback

        print(f"\n{_RED}[TOOL ERROR]{_RST} Exception in '{name}':", flush=True)
        traceback.print_exc()
        return f"Tool '{name}' raised an error: {e}"


# ---------------------------------------------------------------------------
# Streaming helper: parse & emit a content-only Ollama stream
# ---------------------------------------------------------------------------


async def _stream_content(msgs: list[dict]):
    """
    Stream the content follow-up pass (no tool definitions).
    Handles native think field + inline <think> tag parsing.
    Yields SSE data strings.
    Returns final (content_buf, thinking_buf) via a _state sentinel event.
    """
    thinking_buf = ""
    content_buf = ""
    parse_buf = ""
    metrics_dict = {}
    in_think = False
    OPEN_TAG = "<think>"
    CLOSE_TAG = "</think>"

    print(f"{_CYN}[PASS2]{_RST} Streaming content. Roles:", [m["role"] for m in msgs], flush=True)

    _SENTINEL = object()
    queue: asyncio.Queue = asyncio.Queue()

    async def _feed():
        """Feed Ollama stream lines into the queue for the outer consumer."""
        try:
            async for line in call_ollama_stream(msgs, tools=None):
                await queue.put(("line", line))
        except BaseException as exc:
            await queue.put(("error", exc))
            return
        await queue.put(("done", _SENTINEL))

    feeder = asyncio.create_task(_feed())
    try:
        while True:
            try:
                kind, item = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                yield 'data: {"type":"heartbeat"}\n\n'
                continue

            if kind == "error":
                print(f"{_RED}[PASS2 ERROR]{_RST} {type(item).__name__}: {item}", flush=True)
                raise item
            if kind == "done":
                break

            try:
                chunk = json.loads(item)
            except Exception:
                continue

            msg = chunk.get("message", {})

            # Native thinking field
            native_think = msg.get("thinking", "")
            if native_think:
                thinking_buf += native_think
                yield f"data: {json.dumps({'type': 'thinking', 'delta': native_think})}\n\n"

            # Content field -- strip leaked model tokens, then route through inline-tag parser
            text_delta = msg.get("content", "")
            if text_delta:
                for _tok in _LEAKED_MODEL_TOKENS:
                    text_delta = text_delta.replace(_tok, "")
                parse_buf += text_delta
                while parse_buf:
                    if in_think:
                        ct_idx = parse_buf.find(CLOSE_TAG)
                        if ct_idx == -1:
                            safe = len(parse_buf) - len(CLOSE_TAG)
                            if safe > 0:
                                out = parse_buf[:safe]
                                thinking_buf += out
                                yield f"data: {json.dumps({'type': 'thinking', 'delta': out})}\n\n"
                                parse_buf = parse_buf[safe:]
                            break
                        else:
                            if ct_idx > 0:
                                out = parse_buf[:ct_idx]
                                thinking_buf += out
                                yield f"data: {json.dumps({'type': 'thinking', 'delta': out})}\n\n"
                            parse_buf = parse_buf[ct_idx + len(CLOSE_TAG) :]
                            in_think = False
                    else:
                        ot_idx = parse_buf.find(OPEN_TAG)
                        if ot_idx == -1:
                            found_partial = False
                            for plen in range(len(OPEN_TAG) - 1, 0, -1):
                                if parse_buf.endswith(OPEN_TAG[:plen]):
                                    safe = len(parse_buf) - plen
                                    if safe > 0:
                                        out = parse_buf[:safe]
                                        content_buf += out
                                        yield f"data: {json.dumps({'type': 'text', 'delta': out})}\n\n"
                                        parse_buf = parse_buf[safe:]
                                    found_partial = True
                                    break
                            if not found_partial:
                                content_buf += parse_buf
                                yield f"data: {json.dumps({'type': 'text', 'delta': parse_buf})}\n\n"
                                parse_buf = ""
                            break
                        else:
                            if ot_idx > 0:
                                out = parse_buf[:ot_idx]
                                content_buf += out
                                yield f"data: {json.dumps({'type': 'text', 'delta': out})}\n\n"
                            parse_buf = parse_buf[ot_idx + len(OPEN_TAG) :]
                            in_think = True

            if chunk.get("done"):
                metrics_dict = {
                    "prompt_eval_count": chunk.get("prompt_eval_count"),
                    "prompt_eval_duration": chunk.get("prompt_eval_duration"),
                    "eval_count": chunk.get("eval_count"),
                    "eval_duration": chunk.get("eval_duration"),
                    "total_duration": chunk.get("total_duration"),
                    "load_duration": chunk.get("load_duration"),
                }
                if parse_buf:
                    if in_think:
                        thinking_buf += parse_buf
                        yield f"data: {json.dumps({'type': 'thinking', 'delta': parse_buf})}\n\n"
                    else:
                        content_buf += parse_buf
                        yield f"data: {json.dumps({'type': 'text', 'delta': parse_buf})}\n\n"
                break

    except BaseException as exc:
        print(f"{_RED}[PASS2 STREAM ERROR]{_RST} {type(exc).__name__}: {exc}", flush=True)
        feeder.cancel()
        raise
    finally:
        if not feeder.done():
            feeder.cancel()

    yield f"data: {json.dumps({'type': '_state', 'content': content_buf, 'thinking': thinking_buf, 'metrics': metrics_dict})}\n\n"


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Pydantic model representing an incoming chat request from the user."""
    message: str


async def _process_chat_background(
    user_message: str,
    is_regenerate: bool,
    time_ctx: str | None,
    assistant_row_id: int,
    queue: asyncio.Queue,
):
    """Run the background chat processing worker.

    Executes independently of the client's SSE connection state to guarantee
    the model's response is fully generated and committed to the database.

    Args:
        user_message: The text of the user's incoming chat message.
        is_regenerate: True if regenerating the last assistant response.
        time_ctx: Optional time-gap context string to prefix to user message.
        assistant_row_id: The database message ID reserved for the response.
        queue: The queue used to forward SSE stream event dictionaries.
    """
    content_buf = ""
    thinking_buf = ""
    metrics_dict = {}
    tools_used_list = []
    tool_metadata_list = []

    async def put(type_: str, **kw):
        """Enqueue a serialized SSE event dictionary.

        Args:
            type_: The event type string.
            **kw: Additional fields to serialize into the event payload.
        """
        await queue.put("data: " + json.dumps({"type": type_, **kw}) + "\n\n")

    async def drain_stream(stream):
        """Iterate _stream_content, buffer state, and forward events to queue."""
        nonlocal content_buf, thinking_buf, metrics_dict
        async for event in stream:
            if event.startswith("data: "):
                try:
                    d = json.loads(event[6:])
                    if d.get("type") == "_state":
                        content_buf = d["content"]
                        thinking_buf = d.get("thinking", "")
                        metrics_dict = d.get("metrics", {})
                        if metrics_dict:
                            await put("metrics", **metrics_dict)
                        continue          # _state is internal bookkeeping only
                    if d.get("type") == "text":
                        content_buf += d.get("delta", "")
                    elif d.get("type") == "thinking":
                        thinking_buf += d.get("delta", "")
                except Exception:
                    pass
            await queue.put(event)

    try:
        await put("status", msg="Processing...")

        # RAG + system prompt + history (fast synchronous work)
        rag_context = build_rag_context(user_message)
        system = load_system_prompt()
        if rag_context:
            system += f"\n\n{rag_context}"
            chunk_count = rag_context.count("\n[")
            pinned_count = rag_context.count("[primary source]")
            dlog(f"RAG injected: chars={len(rag_context)} chunks={chunk_count} pinned={pinned_count}")

        conv_summary = build_conversation_summary()
        if conv_summary:
            system += f"\n\n--- Conversation Summary (older messages) ---\n{conv_summary}\n--- End Summary ---"
            dlog("Summary injected:", conv_summary[:200])

        history = load_history()

        # --- Tweak 2: Agenda as dynamic user-turn prefix (2026-06-21) ---
        # Injected here rather than in load_system_prompt() so it is not
        # frozen into the Gemma 4 KV-cache prefill. This refreshes on every
        # request and costs tokens only when there is something to report.
        agenda_prefix = get_upcoming_agenda_prompt_context()

        user_msg_for_model = f"{time_ctx}\n{user_message}" if time_ctx else user_message
        if agenda_prefix:
            user_msg_for_model = f"{agenda_prefix}\n\n{user_msg_for_model}"
        
        messages = [{"role": "system", "content": system}] + history
        
        research_ctx = get_research_context()
        if research_ctx:
            messages.append({"role": "system", "content": research_ctx})
            
        messages.append({"role": "user", "content": user_msg_for_model})

        await put("status", msg="Querying model...")

        # ------------------------------------------------------------------
        # Pass 1: Non-streaming tool detection
        # ------------------------------------------------------------------
        print(
            f"{_CYN}[PASS1]{_RST} Non-streaming tool-detection. Roles:",
            [m["role"] for m in messages],
            flush=True,
        )

        pass1_task = asyncio.ensure_future(
            call_ollama_full(messages, tools=MODEL_TOOL_DEFINITIONS)
        )
        while not pass1_task.done():
            await queue.put('data: {"type":"heartbeat"}\n\n')
            await asyncio.sleep(1.0)

        try:
            pass1_resp = pass1_task.result()
        except Exception as exc:
            print(f"{_RED}[PASS1 ERROR]{_RST} {type(exc).__name__}: {exc}", flush=True)
            # finally block will log the empty response and update DB
            return

        pass1_msg = pass1_resp.get("message", {})
        tool_calls = pass1_msg.get("tool_calls") or []
        pass1_content = pass1_msg.get("content") or ""
        pass1_thinking = pass1_msg.get("thinking") or ""
        dlog(
            f"Pass1 -- content: {len(pass1_content)} chars, "
            f"thinking: {len(pass1_thinking)} chars, tools: {len(tool_calls)}"
        )

        if not tool_calls:
            await drain_stream(_stream_content(messages))

        else:
            # ------------------------------------------------------------------
            # Agentic tool loop
            # ------------------------------------------------------------------
            loop = asyncio.get_running_loop()
            current_tool_calls = tool_calls
            current_content = pass1_content

            for tool_round in range(1, cfg.MAX_TOOL_ROUNDS + 1):
                dlog(f"Tool round {tool_round}/{cfg.MAX_TOOL_ROUNDS}: {len(current_tool_calls)} call(s)")
                await put("status", msg=f"Tool round {tool_round}...")

                messages.append({
                    "role": "assistant",
                    "content": "",  # Strip pre-tool reasoning — prevents Pass-2 echo of journal/tool content
                    "tool_calls": current_tool_calls,
                })

                for tc in current_tool_calls:
                    fn_name = tc["function"]["name"]
                    fn_args = tc["function"].get("arguments", {})
                    if isinstance(fn_args, str):
                        try:
                            fn_args = json.loads(fn_args)
                        except Exception:
                            fn_args = {}

                    await put("tool", name=fn_name)
                    dlog(f"Dispatching tool: {fn_name}({fn_args})")

                    tool_task = loop.run_in_executor(
                        None, lambda fn=fn_name, fa=fn_args: dispatch_tool(fn, fa)
                    )
                    while not tool_task.done():
                        await queue.put('data: {"type":"heartbeat"}\n\n')
                        await asyncio.sleep(1.0)
                    result = tool_task.result()
                    
                    tool_entry = fn_name
                    meta_entry = {"name": fn_name, "data": None}
                    
                    if fn_name == "generate_image":
                        import re
                        m = re.search(r'(/images/[^\s\)]+)', result)
                        if m:
                            tool_entry = f"{fn_name}[{m.group(1)}]"
                            meta_entry["data"] = {"path": m.group(1)}
                            await put("tool_data", name=fn_name, data=m.group(1))
                    elif fn_name == "write_journal_entry":
                        import re
                        m = re.search(r'entry: (Journal Entry [^\.]+\.md)', result)
                        if m:
                            tool_entry = f"{fn_name}[{m.group(1)}]"
                            meta_entry["data"] = {"id": m.group(1)}
                            await put("tool_data", name=fn_name, data=m.group(1))
                            
                    tools_used_list.append(tool_entry)
                    tool_metadata_list.append(meta_entry)
                    if cfg.DEBUG_TOOL_FULL:
                        print(
                            f"{_YEL}[TOOL RESULT]{_RST} {fn_name}\n"
                            f"{'─' * 60}\n{result}\n{'─' * 60}",
                            flush=True,
                        )
                    else:
                        dlog(f"Tool result preview: {str(result)[:200]}")
                    messages.append({"role": "tool", "content": result, "name": fn_name})

                if tool_round >= cfg.MAX_TOOL_ROUNDS:
                    print(
                        f"{_YEL}[TOOL LOOP]{_RST} Hit MAX_TOOL_ROUNDS ({cfg.MAX_TOOL_ROUNDS}). Forcing final response.",
                        flush=True,
                    )
                    await put("status", msg="Generating response...")
                    break

                await put("status", msg="Thinking...")
                print(
                    f"{_YEL}[TOOL LOOP]{_RST} Round {tool_round} complete. Re-querying model. Roles:",
                    [m["role"] for m in messages],
                    flush=True,
                )

                followup_task = asyncio.ensure_future(
                    call_ollama_full(messages, tools=MODEL_TOOL_DEFINITIONS)
                )
                while not followup_task.done():
                    await queue.put('data: {"type":"heartbeat"}\n\n')
                    await asyncio.sleep(1.0)

                followup_resp = followup_task.result()
                followup_msg = followup_resp.get("message", {})
                current_tool_calls = followup_msg.get("tool_calls") or []
                current_content = followup_msg.get("content") or ""

                dlog(
                    f"Round {tool_round} follow-up: content={len(current_content)} chars, "
                    f"tools={len(current_tool_calls)}"
                )

                if not current_tool_calls:
                    dlog("Model produced no more tool calls. Exiting tool loop.")
                    await put("status", msg="Generating response...")
                    break

            # Final streaming response after tool loop
            await drain_stream(_stream_content(messages))

    finally:
        # Always commit to DB — independent of whether SSE pipe is alive
        final_content = content_buf.strip()
        tools_str = ",".join(tools_used_list) if tools_used_list else None
        tools_meta_str = json.dumps(tool_metadata_list) if tool_metadata_list else None
        if final_content:
            update_message(
                assistant_row_id,
                final_content,
                thinking=thinking_buf if thinking_buf else None,
                tools_used=tools_str,
                tool_metadata=tools_meta_str
            )
            save_message_metrics(assistant_row_id, metrics_dict)
        else:
            update_message(
                assistant_row_id, "[Response interrupted -- please try again.]"
            )
            dlog(
                "WARNING: empty assistant response. thinking len:",
                len(thinking_buf),
                "tools fired:",
                bool(tool_calls),
            )

        dlog(f"Done -- content: {len(content_buf)} chars, thinking: {len(thinking_buf)} chars")

        # Signal SSE pipe to close cleanly
        await queue.put(f"data: {json.dumps({'type': 'done'})}\n\n")
        await queue.put(None)  # sentinel

        # Trigger summary update
        if final_content:
            import context_summarizer
            context_summarizer._summary_task = asyncio.create_task(trigger_summary_update())


def pause_all_active_research():
    """Immediately pause any currently running background research tasks to prevent Ollama blockage."""
    global _background_tasks
    paused_any = False
    for tid, task in list(_background_tasks.items()):
        if tid.startswith("task_") and task.get("status") in ("running", "searching", "synthesizing"):
            print(f"[IMMEDIATE RESEARCH PAUSE] Pausing active research task {tid} due to incoming user chat activity.", flush=True)
            from research_engine import load_state, save_state
            try:
                state = load_state(tid)
                if state and state["status"] in ("running", "searching", "synthesizing"):
                    state["status"] = "paused"
                    save_state(tid, state)
                    _background_tasks[tid]["status"] = "paused"
                    terminate_research_process(tid)
                    paused_any = True
            except Exception as e:
                print(f"[IMMEDIATE RESEARCH PAUSE ERROR] Failed to pause task {tid}: {e}", flush=True)
    return paused_any


async def chat_stream(user_message: str, is_regenerate: bool = False):
    """Open an SSE connection to stream the generated chat response.

    Args:
        user_message: The text of the user's incoming chat message.
        is_regenerate: True if regenerating the last assistant response.

    Yields:
        str: Server-Sent Events formatted data blocks.
    """
    global _last_activity_ts
    _last_activity_ts = time.time()
    importlib.reload(cfg)

    # Immediately pause any active deep research to unblock Ollama
    pause_all_active_research()

    cancel_pending_summary()
    cancel_pending_consolidation()
    cancel_pending_extraction()

    if not is_regenerate:
        time_ctx = get_time_gap_context()
        if time_ctx:
            dlog("Time-gap annotation:", time_ctx)
        save_message("user", user_message)
    else:
        time_ctx = None
        dlog("Regenerating last response")

    # Reserve DB row and spawn the background task synchronously.
    # From this point the task owns all processing — client can disconnect freely.
    assistant_row_id = save_message_get_id("assistant", "")
    event_queue: asyncio.Queue = asyncio.Queue()

    asyncio.create_task(
        _process_chat_background(
            user_message, is_regenerate, time_ctx, assistant_row_id, event_queue
        )
    )
    print(f"{_CYN}[CHAT]{_RST} Background task started — SSE pipe open", flush=True)

    # Drain queue and forward to client.
    # GeneratorExit/CancelledError = client disconnected — log and exit cleanly.
    # The background task is completely unaffected.
    try:
        while True:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                yield 'data: {"type":"heartbeat"}\n\n'
                continue
            if event is None:  # sentinel from _process_chat_background
                break
            yield event
    except (GeneratorExit, asyncio.CancelledError):
        print(
            f"{_CYN}[CHAT]{_RST} SSE pipe closed (client disconnected) — "
            f"background task continues independently",
            flush=True,
        )







# ---------------------------------------------------------------------------
# Background task tracking
# ---------------------------------------------------------------------------
# Simple in-memory dict for tracking background process status.
# The UI polls GET /task_status/{name} to know when a process finishes.
#
# IMPORTANT: Idle-time tasks (fact_consolidator, fact_extractor) monitor this
# dictionary. If ANY task in this dict has "status": "running", idle tasks
# will yield/defer to prevent overwhelming Ollama. Future heavy background tasks
# should track their status here to automatically benefit from mutual exclusion.
_background_tasks: dict[str, dict] = {}


def is_any_heavy_task_running(exclude_name: str = None) -> bool:
    """Check if any heavy background task is currently running in the system.

    Unified checker across all background tasks (sync, vault_map, refresh_memory,
    consolidator, extractor, and active research tasks) to guarantee complete
    mutual exclusion and prevent Ollama/CPU resource contention.

    Args:
        exclude_name: Optional task name to exclude from checking.

    Returns:
        bool: True if another heavy task is currently running, False otherwise.
    """
    for k, task in _background_tasks.items():
        if exclude_name and k == exclude_name:
            continue
        if k.startswith("task_"):
            if task.get("status") in ("running", "searching", "synthesizing"):
                return True
        elif task.get("status") == "running":
            return True
    return False


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context: initialise DB, install error handler, start background tasks.

    Args:
        app: The FastAPI application instance.
    """
    # Suppress noisy Windows ProactorEventLoop ConnectionResetError tracebacks.
    # These fire when browser clients (polling /task_status) disconnect mid-response.
    # WinError 10054 is harmless — the background task continues regardless.
    def _suppress_connection_reset(loop, context):
        """Swallow WinError 10054 (client disconnect) from the ProactorEventLoop exception handler.

        Args:
            loop: The event loop.
            context: The exception context.
        """
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError):
            return  # Swallow silently — expected on Windows with polling clients
        loop.default_exception_handler(context)

    asyncio.get_event_loop().set_exception_handler(_suppress_connection_reset)

    init_db()
    print(f"{_BLD}{_CYN}Evelyn server starting on {cfg.BIND_HOST}:{cfg.SERVER_PORT}{_RST}")
    print(f"  Model: {cfg.MODEL_NAME} | Context: {cfg.NUM_CTX} | Think: {cfg.THINK}")
    print(f"  History cap: {cfg.MAX_HISTORY_MESSAGES} msgs | Debug: {cfg.DEBUG_LOGGING}")

    # Rebuild conversation summary cache in background (covers mid-day restarts)
    import context_summarizer
    context_summarizer._summary_task = asyncio.create_task(trigger_summary_update())
    print(f"  {_GRN}Summarizer:{_RST} background rebuild started")

    # Idle-time consolidation loop — wakes every CONSOLIDATION_IDLE_CHECK_INTERVAL
    # seconds, checks inactivity, and runs run_consolidation() when idle long enough.
    async def _idle_consolidation_loop():
        """Background loop that triggers fact consolidation during idle periods."""
        while True:
            await asyncio.sleep(cfg.CONSOLIDATION_IDLE_CHECK_INTERVAL)
            importlib.reload(cfg)
            if not cfg.CONSOLIDATION_ENABLED:
                continue
            idle_seconds = time.time() - _last_activity_ts
            if idle_seconds >= cfg.CONSOLIDATION_IDLE_THRESHOLD:
                if is_any_heavy_task_running():
                    continue
                print(
                    f"{_MAG}[CONSOLIDATOR]{_RST} Idle for "
                    f"{idle_seconds / 60:.1f}m — starting consolidation pass.",
                    flush=True,
                )
                import fact_consolidator
                fact_consolidator._consolidation_task = asyncio.create_task(run_consolidation())

    asyncio.create_task(_idle_consolidation_loop())
    print(
        f"  {_GRN}Consolidator:{_RST} idle loop started "
        f"(threshold={cfg.CONSOLIDATION_IDLE_THRESHOLD // 60}m, "
        f"check={cfg.CONSOLIDATION_IDLE_CHECK_INTERVAL // 60}m)"
    )

    # Idle-time extraction loop — shorter threshold than consolidation.
    # Reads new messages directly from the DB; no summarizer dependency.
    async def _idle_extraction_loop():
        """Background loop that triggers fact extraction during idle periods."""
        while True:
            await asyncio.sleep(cfg.FACT_EXTRACTION_IDLE_CHECK_INTERVAL)
            importlib.reload(cfg)
            if not cfg.FACT_EXTRACTION_ENABLED:
                continue
            idle_seconds = time.time() - _last_activity_ts
            if idle_seconds >= cfg.FACT_EXTRACTION_IDLE_THRESHOLD:
                if is_any_heavy_task_running():
                    continue
                print(
                    f"{_CYN}[EXTRACTOR]{_RST} Idle for "
                    f"{idle_seconds / 60:.1f}m — starting extraction pass.",
                    flush=True,
                )
                import fact_extractor
                fact_extractor._extraction_task = asyncio.create_task(run_extraction())

    asyncio.create_task(_idle_extraction_loop())
    print(
        f"  {_GRN}Extractor:{_RST}   idle loop started "
        f"(threshold={cfg.FACT_EXTRACTION_IDLE_THRESHOLD // 60}m, "
        f"check={cfg.FACT_EXTRACTION_IDLE_CHECK_INTERVAL // 60}m)"
    )

    # Idle-time deep research loop
    async def _idle_research_loop():
        """Background loop for deep research management."""
        import os
        import json
        import subprocess
        import sys
        
        while True:
            await asyncio.sleep(10)
            importlib.reload(cfg)
            if not getattr(cfg, "RESEARCH_ENABLED", True):
                continue
                
            idle_seconds = time.time() - _last_activity_ts
            
            # 1. Topic generation
            global _last_self_initiate_ts
            if (
                getattr(cfg, "RESEARCH_SELF_INITIATE", True)
                and idle_seconds >= getattr(cfg, "RESEARCH_IDLE_THRESHOLD", 1800)
                and time.time() - _last_self_initiate_ts >= 3600
            ):
                try:
                    # Check if any heavy task is active
                    if not is_any_heavy_task_running():
                        _last_self_initiate_ts = time.time()
                        from research_engine import self_initiate_research_topics
                        await self_initiate_research_topics()
                except Exception as e:
                    print(f"[RESEARCH ERROR] Topic generation failed: {e}", flush=True)

            # 2. Build a unified view of unfinished tasks from memory and disk
            from research_engine import load_state, save_state
            
            unfinished_tasks = []
            active_task = None
            
            for tid, task in list(_background_tasks.items()):
                if tid.startswith("task_"):
                    # Check disk state as well to stay perfectly in sync
                    disk_state = load_state(tid)
                    status = disk_state.get("status") if disk_state else task.get("status")
                    if status in ("running", "paused", "error", "searching", "synthesizing", "pending"):
                        task_info = {
                            "task_id": tid,
                            "status": status,
                            "query": disk_state.get("query") if disk_state else task.get("query", ""),
                            "scope": disk_state.get("scope") if disk_state else task.get("scope", "standard"),
                            "created_at": disk_state.get("created_at") if disk_state else ""
                        }
                        unfinished_tasks.append(task_info)
                        if status in ("running", "searching", "synthesizing"):
                            active_task = task_info
                        # Sync memory status back to prevent drift
                        _background_tasks[tid]["status"] = status

            # 3. Handle active task pausing if user becomes active
            if active_task:
                tid = active_task["task_id"]
                state = load_state(tid)
                disk_status = state.get("status") if state else None
                
                # If finished or changed out-of-band on disk, sync it to memory
                if disk_status and disk_status not in ("running", "searching", "synthesizing"):
                    print(f"[RESEARCH SYNC] Task {tid} completed or changed status on disk to '{disk_status}' — updating server memory.", flush=True)
                    _background_tasks[tid]["status"] = disk_status
                    if disk_status in ("done", "error", "cancelled"):
                        _background_tasks[tid]["finished_at"] = time.time()
                    continue
                    
                if idle_seconds < 10:  # User active!
                    print(f"[RESEARCH INTERRUPT] User active (idle={idle_seconds:.1f}s) — pausing deep research task {tid}", flush=True)
                    if state and state["status"] in ("running", "searching", "synthesizing"):
                        state["status"] = "paused"
                        state["error"] = "Paused: Interrupted automatically due to active user chat session (to prioritize conversational response speed)."
                        save_state(tid, state)
                        _background_tasks[tid]["status"] = "paused"
                        terminate_research_process(tid)
                continue

            # Check if any heavy background task is currently running (e.g. refresh_memory, vault_map, sync)
            if is_any_heavy_task_running():
                continue

            # 4. Auto-resume / Auto-retry unfinished tasks if idle
            if unfinished_tasks:
                if idle_seconds >= 300:  # Server idle for 5 min
                    # Sort unfinished tasks by created_at ascending (oldest gets priority)
                    unfinished_tasks.sort(key=lambda x: x.get("created_at") or "")
                    target_task = unfinished_tasks[0]
                    
                    print(f"[RESEARCH AUTO-RECOVERY] Server idle for {idle_seconds:.1f}s — auto-resuming unfinished task {target_task['task_id']} (status: {target_task['status']})", flush=True)
                    from evelyn_tools import resume_research_task
                    resume_research_task(target_task['task_id'])
                    # Wait for subprocess thread to spin up and register
                    await asyncio.sleep(20)
                continue

            # 5. Process queued tasks
            if idle_seconds >= getattr(cfg, "RESEARCH_IDLE_THRESHOLD", 1800):
                # Double guard
                if unfinished_tasks:
                    continue
                    
                queue_file = os.path.join(cfg.RESEARCH_DATA_DIR, "queue.json")
                if os.path.exists(queue_file):
                    try:
                        with open(queue_file, "r", encoding="utf-8") as f:
                            queue = json.load(f)
                    except Exception:
                        queue = []
                        
                    if queue:
                        # Sort chronologically by created_at date
                        queue.sort(key=lambda x: x.get("created_at") or x.get("created_time") or "")
                        
                        next_task = queue.pop(0)
                        try:
                            with open(queue_file, "w", encoding="utf-8") as f:
                                json.dump(queue, f, indent=2)
                        except Exception:
                            pass
                            
                        print(f"[RESEARCH IDLE START] Starting queued task: '{next_task['query']}'", flush=True)
                        from evelyn_tools import start_research
                        start_research(next_task["query"], scope=next_task.get("scope", "standard"))
                        # Sleep long enough for the subprocess thread to register in
                        # _background_tasks before the next iteration's active-task check.
                        await asyncio.sleep(30)

    asyncio.create_task(_idle_research_loop())
    print(
        f"  {_GRN}Deep Research:{_RST} idle loop started "
        f"(threshold={getattr(cfg, 'RESEARCH_IDLE_THRESHOLD', 1800) // 60}m)"
    )

    # Idle-time memory refresh loop - runs during deep idle periods (45m+)
    async def _idle_memory_refresh_loop():
        """Background loop that triggers memory refresh during deep idle periods."""
        last_run_time = 0
        while True:
            await asyncio.sleep(300) # Check every 5 minutes
            importlib.reload(cfg)
            
            # Require at least 45 minutes of idle time
            idle_seconds = time.time() - _last_activity_ts
            if idle_seconds >= 2700:
                # Limit running to once every 2 hours max
                if time.time() - last_run_time >= 7200:
                    if not is_any_heavy_task_running():
                        print(f"{_GRN}[IDLE REFRESH]{_RST} Server idle for {idle_seconds / 60:.1f}m — triggering background memory refresh.", flush=True)
                        await start_refresh_memory_internal()
                        last_run_time = time.time()

    asyncio.create_task(_idle_memory_refresh_loop())
    print(f"  {_GRN}Mem Refresher:{_RST} idle loop started (threshold=45m, limit=2h)")

    # Periodic Google Calendar auto-sync loop (Hermes Tier 2 #7)
    async def _gcal_sync_loop():
        """Periodic background task that pulls events from Google Calendar and caches them.
        Runs on startup and then every 30 minutes.
        """
        await asyncio.sleep(10)  # Brief warm-up delay on startup
        while True:
            try:
                import gcal_sync
                result = gcal_sync.sync_gcal_events()
                if result.get("status") == "success":
                    print(f"{_GRN}[GCAL SYNC]{_RST} Auto-sync successful: {result['message']}", flush=True)
                elif result.get("status") == "offline":
                    # Only print if debug logging is enabled or on config change
                    if cfg.DEBUG_LOGGING:
                        print(f"{_GRN}[GCAL SYNC]{_RST} Auto-sync fallback to cache: {result['message']}", flush=True)
            except Exception as e:
                print(f"{_RED}[GCAL SYNC ERROR]{_RST} {e}", flush=True)
            
            # Run every 30 minutes
            await asyncio.sleep(1800)

    asyncio.create_task(_gcal_sync_loop())
    print(f"  {_GRN}GCal Syncer:{_RST} periodic loop started (interval=30m)")

    yield



app = FastAPI(title="Evelyn", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UI_DIR = BASE_DIR / "evelyn_ui"
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR), html=True), name="ui")

# Serve generated images directly via the main server
app.mount("/images", StaticFiles(directory=cfg.IMAGE_OUTPUT_DIR), name="images")

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/status")
async def status(_: None = Depends(check_auth)):
    """Return server health and active config (model, context size, think mode)."""
    return {
        "status": "ok",
        "model": cfg.MODEL_NAME,
        "think": cfg.THINK,
        "debug": cfg.DEBUG_LOGGING,
        "num_ctx": cfg.NUM_CTX,
    }


@app.post("/chat")
async def chat(req: ChatRequest, _: None = Depends(check_auth)):
    """Accept a user message and return a Server-Sent Events stream of the response.

    Args:
        req: The chat request object containing the user message.
        _: Authentication dependency placeholder.

    Returns:
        StreamingResponse: An SSE stream of the assistant's response.
    """
    return StreamingResponse(
        chat_stream(req.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/regenerate")
async def regenerate(_: None = Depends(check_auth)):
    """Delete the last assistant message and re-generate a response."""
    user_message = delete_last_assistant_message()
    if not user_message:
        raise HTTPException(
            status_code=400, detail="No user message to regenerate from."
        )
    return StreamingResponse(
        chat_stream(user_message, is_regenerate=True),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
@app.get("/latest_message_id")
async def get_latest_message_id(_: None = Depends(check_auth)):
    """Return the ID of the latest committed message."""
    con = get_db()
    row = con.execute("SELECT MAX(id) as max_id FROM messages WHERE content != ''").fetchone()
    con.close()
    return {"id": row["max_id"] or 0}


@app.get("/history")
async def get_history(
    _: None = Depends(check_auth),
    limit: int = 50,
    before: int | None = None,
):
    """Retrieve the chat message history, ordered chronologically.

    Args:
        _: Authorization dependency.
        limit: The maximum number of messages to return.
        before: Optional message ID to filter results (for cursor pagination).

    Returns:
        list[dict]: A list of message dictionaries.
    """
    con = get_db()
    if before:
        rows = con.execute(
            """
            SELECT m.id, m.role, m.content, m.thinking, m.tools_used, m.tool_metadata, m.ts, mm.prompt_eval_count, mm.eval_count
            FROM messages m
            LEFT JOIN message_metrics mm ON m.id = mm.message_id
            WHERE m.id < ? AND m.content != ''
            ORDER BY m.id DESC LIMIT ?
            """,
            (before, limit),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT m.id, m.role, m.content, m.thinking, m.tools_used, m.tool_metadata, m.ts, mm.prompt_eval_count, mm.eval_count
            FROM messages m
            LEFT JOIN message_metrics mm ON m.id = mm.message_id
            WHERE m.content != ''
            ORDER BY m.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    con.close()
    # Rows come back newest-first from DESC; reverse to chronological
    rows = list(reversed(rows))
    return [dict(r) for r in rows]


@app.delete("/history")
async def delete_history(_: None = Depends(check_auth)):
    """Clear all chat history and return a confirmation."""
    clear_history()
    return {"status": "cleared"}


@app.get("/artifact")
async def get_artifact(type: str, id: str, _: None = Depends(check_auth)):
    """Fetch raw markdown content for a named artifact (journal or research report).

    Args:
        type: Artifact kind — "journal" or "research".
        id:   Filename stem (journal) or task_id (research).

    Returns:
        Dict with "content" (markdown text) and "status" (approved/unfiled/pending/unknown).
    """
    if type == "journal":
        import os
        import re
        from Evelyn.tools.journal_manager import PENDING_DIR, JOURNAL_DIR

        filename = id if id.endswith(".md") else f"{id}.md"
        filename = os.path.basename(filename)

        # 1. Try structured vault folder: Journal Entries/YYYY/MM-ShortMonth/Journal Entry YYYY-MM-DD.md
        m = re.search(r'Journal Entry (\d{4})-(\d{2})-\d{2}\.md', filename)
        if m:
            year = m.group(1)
            month_num = m.group(2)
            import datetime
            try:
                month_dt = datetime.date(int(year), int(month_num), 1)
                month_name = month_dt.strftime("%b")
                struct_path = os.path.join(JOURNAL_DIR, "Journal Entries", year, f"{month_num}-{month_name}", filename)
                if os.path.exists(struct_path):
                    with open(struct_path, "r", encoding="utf-8") as f:
                        return {"content": f.read(), "status": "approved"}
            except Exception:
                pass

        # 2. Try vault root path — written directly (JOURNAL_DIRECT_WRITE=True) but not yet
        # filed into the structured subfolder.  Return "unfiled" so the modal shows the
        # approve/file button rather than the "already approved" badge.
        root_path = os.path.join(JOURNAL_DIR, filename)
        if os.path.exists(root_path):
            with open(root_path, "r", encoding="utf-8") as f:
                return {"content": f.read(), "status": "unfiled"}

        # 3. Try pending folder (legacy — JOURNAL_DIRECT_WRITE=False mode)
        pending_path = os.path.join(PENDING_DIR, filename)
        if os.path.exists(pending_path):
            with open(pending_path, "r", encoding="utf-8") as f:
                return {"content": f.read(), "status": "pending"}

        # Fallback to journal_manager read
        from Evelyn.tools.journal_manager import read_journal_entry
        m = re.search(r'Journal Entry ([0-9\-]+)\.md', filename)
        if m:
            content = read_journal_entry(m.group(1))
            return {"content": content, "status": "unknown"}
        else:
            raise HTTPException(status_code=400, detail="Invalid journal ID")
    elif type == "research":
        import os
        import evelyn_config as cfg
        import re
        safe_id = re.sub(r'[^a-zA-Z0-9_\-]+', '-', id).strip('-')
        report_path = os.path.join(cfg.RESEARCH_VAULT_DIR, f"{safe_id}.md")
        if os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                content = f.read()
            return {"content": content}
        else:
            # Fallback to research data dir
            if os.path.exists(cfg.RESEARCH_DATA_DIR):
                for d in os.listdir(cfg.RESEARCH_DATA_DIR):
                    d_path = os.path.join(cfg.RESEARCH_DATA_DIR, d)
                    if os.path.isdir(d_path):
                        rep_path = os.path.join(d_path, "report.md")
                        if os.path.exists(rep_path):
                            state_path = os.path.join(d_path, "state.json")
                            if os.path.exists(state_path):
                                try:
                                    import json
                                    with open(state_path, "r", encoding="utf-8") as sf:
                                        sdata = json.load(sf)
                                    if sdata.get("task_id") == id or re.sub(r'[^a-zA-Z0-9_\-]+', '-', sdata.get("query", "").lower()).strip('-') == safe_id:
                                        with open(rep_path, "r", encoding="utf-8") as rf:
                                            content = rf.read()
                                        return {"content": content}
                                except Exception:
                                    pass
            raise HTTPException(status_code=404, detail="Research report not found")
    else:
        raise HTTPException(status_code=400, detail="Unknown artifact type")


class ApproveJournalRequest(BaseModel):
    """Pydantic model representing a request to approve a pending journal entry."""
    id: str


@app.post("/journal/approve")
async def approve_journal(req: ApproveJournalRequest, _: None = Depends(check_auth)):
    """Move a pending journal entry into the structured vault folder hierarchy.

    Args:
        req: Request containing the journal entry ID/filename.
        _: Authorization dependency.

    Returns:
        dict: Confirmation of approval status and final destination path.
    """
    import os
    import re
    import shutil
    import datetime
    from Evelyn.tools.journal_manager import PENDING_DIR, JOURNAL_DIR

    filename = req.id if req.id.endswith(".md") else f"{req.id}.md"
    filename = os.path.basename(filename)

    # Resolve source: prefer PENDING_DIR (legacy mode), fall back to vault root
    # (JOURNAL_DIRECT_WRITE=True mode — entry written directly but not yet structured).
    source_path = os.path.join(PENDING_DIR, filename)
    if not os.path.exists(source_path):
        root_path = os.path.join(JOURNAL_DIR, filename)
        if os.path.exists(root_path):
            source_path = root_path
        else:
            # Neither source found — check if already filed in structured path
            m_check = re.search(r'Journal Entry (\d{4})-(\d{2})-\d{2}\.md', filename)
            if m_check:
                try:
                    chk_year = m_check.group(1)
                    chk_month = m_check.group(2)
                    chk_dt = datetime.date(int(chk_year), int(chk_month), 1)
                    chk_name = chk_dt.strftime("%b")
                    struct_path = os.path.join(JOURNAL_DIR, "Journal Entries", chk_year, f"{chk_month}-{chk_name}", filename)
                    if os.path.exists(struct_path):
                        return {"status": "already_approved", "destination": struct_path}
                except Exception:
                    pass
            raise HTTPException(status_code=404, detail="Journal entry file not found in pending or vault root")

    m = re.search(r'Journal Entry (\d{4})-(\d{2})-\d{2}\.md', filename)
    if not m:
        raise HTTPException(status_code=400, detail="Invalid journal filename format")

    year = m.group(1)
    month_num = m.group(2)
    month_dt = datetime.date(int(year), int(month_num), 1)
    month_name = month_dt.strftime("%b") # e.g. "May"
    
    target_dir = os.path.join(JOURNAL_DIR, "Journal Entries", year, f"{month_num}-{month_name}")
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, filename)

    try:
        shutil.move(source_path, target_path)
        print(f"[JOURNAL APPROVE] Moved {filename} to structured vault path: {target_path}", flush=True)
        await start_refresh_memory_internal()
        return {"status": "success", "destination": target_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to move journal file: {e}")


@app.post("/new_thread")
async def new_thread(_: None = Depends(check_auth)):
    """Insert a thread-break marker. History before this point won't be
    sent to the model, but remains in the DB for UI scrollback."""
    save_message("system", THREAD_BREAK_MARKER)
    invalidate_summary_cache()
    print(f"{_MAG}[THREAD]{_RST} New thread started", flush=True)
    return {"status": "new thread started"}


# background task tracking variables now located at the top of App setup


def _load_existing_research_tasks():
    """Scan the research data directory and register any paused, errored, or interrupted tasks."""
    try:
        import os
        import json
        research_dir = cfg.RESEARCH_DATA_DIR
        if not os.path.exists(research_dir):
            return
            
        for d in os.listdir(research_dir):
            if d.startswith("task_"):
                task_dir = os.path.join(research_dir, d)
                if os.path.isdir(task_dir):
                    state_file = os.path.join(task_dir, "state.json")
                    if os.path.exists(state_file):
                        try:
                            with open(state_file, "r", encoding="utf-8") as f:
                                state = json.load(f)
                            status = state.get("status")
                            
                            if status in ("paused", "running", "error"):
                                target_status = status
                                # If it was running, it is now paused because the server restarted
                                if status == "running":
                                    target_status = "paused"
                                    state["status"] = "paused"
                                    with open(state_file, "w", encoding="utf-8") as fw:
                                        json.dump(state, fw, indent=2)
                                        
                                _background_tasks[d] = {
                                    "status": target_status,
                                    "query": state.get("query", ""),
                                    "scope": state.get("scope", "standard"),
                                    "started_at": os.path.getmtime(state_file)
                                }
                                print(f"[RESEARCH RECOVERY] Registered {target_status} task {d} from disk.", flush=True)
                        except Exception:
                            pass
    except Exception as e:
        print(f"[RESEARCH RECOVERY ERROR] Failed to load existing tasks: {e}", flush=True)


_load_existing_research_tasks()


@app.get("/task_status/{task_name}")
async def task_status(task_name: str, _: None = Depends(check_auth)):
    """Retrieve the current status of a named background task.

    Args:
        task_name: The identifier of the background task.
        _: Authorization dependency.

    Returns:
        dict: The task status information.
    """
    task = _background_tasks.get(task_name)
    if not task:
        return {"status": "unknown", "task": task_name}
    return task


@app.post("/sync")
async def trigger_sync(_: None = Depends(check_auth)):
    """Trigger a background Chroma ingest directly (no chat turn required).

    Cancels any in-flight consolidation or extraction tasks before starting
    so that Ollama is not shared between the sync process and idle-time LLM
    calls simultaneously.
    """
    import threading
    from evelyn_tools import TOOL_FUNCTIONS

    # Free Ollama before a heavy background operation starts
    cancel_pending_consolidation()
    cancel_pending_extraction()

    _background_tasks["sync"] = {"status": "running", "started_at": time.time()}

    def _run():
        """Run sync_context_memory in a daemon thread and update the task registry."""
        try:
            print(f"{_GRN}[SYNC]{_RST} Manual sync triggered via /sync endpoint", flush=True)
            TOOL_FUNCTIONS["sync_context_memory"]()
            _background_tasks["sync"] = {"status": "done", "finished_at": time.time()}
            print(f"{_GRN}[SYNC]{_RST} Complete.", flush=True)
        except Exception as e:
            _background_tasks["sync"] = {"status": "error", "error": str(e), "finished_at": time.time()}
            print(f"{_RED}[SYNC ERROR]{_RST} {e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "sync started"}


@app.post("/vault_map")
async def trigger_vault_map(_: None = Depends(check_auth)):
    """Regenerate the Obsidian vault map in the background (no chat turn required).

    Cancels any in-flight consolidation or extraction tasks before starting.
    Vault map generation is CPU/IO heavy and runs Ollama for gist generation;
    sharing Ollama with idle-time tasks simultaneously causes timeouts.
    """
    import threading
    import subprocess
    import sys

    # Free Ollama before a heavy background operation starts
    cancel_pending_consolidation()
    cancel_pending_extraction()

    _background_tasks["vault_map"] = {"status": "running", "started_at": time.time()}

    def _run():
        """Run vault_indexer.py as a subprocess and update the task registry on completion."""
        try:
            script = str(BASE_DIR / "Evelyn" / "tools" / "vault_indexer.py")
            print(f"{_GRN}[VAULT MAP]{_RST} Regeneration triggered via /vault_map endpoint", flush=True)
            result = subprocess.run(
                [sys.executable, "-u", script],
                stdout=sys.stdout, stderr=sys.stderr,
                cwd=str(BASE_DIR),
            )
            if result.returncode == 0:
                _background_tasks["vault_map"] = {"status": "done", "finished_at": time.time()}
                print(f"{_GRN}[VAULT MAP]{_RST} Done.", flush=True)
            else:
                _background_tasks["vault_map"] = {"status": "error", "error": f"Exit code {result.returncode}", "finished_at": time.time()}
                print(f"{_RED}[VAULT MAP ERROR]{_RST} Process exited with code {result.returncode}", flush=True)
        except Exception as e:
            _background_tasks["vault_map"] = {"status": "error", "error": str(e), "finished_at": time.time()}
            print(f"{_RED}[VAULT MAP ERROR]{_RST} {e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "vault map generation started"}


# Phase display labels for the /refresh_memory endpoint's stdout parser.
# Module-level constant — no need to recreate on every request.
_REFRESH_PHASE_LABELS = {
    "vault_map":        "Mapping Obsidian Vault...",
    "ingest_knowledge": "Ingesting Core Knowledge...",
    "ingest_gists":     "Ingesting Gists into Chroma...",
}


async def start_refresh_memory_internal():
    """Trigger the unified Memory Refresh pipeline as an async background task.
    Safely ignores the run if another refresh is already running to avoid overlap.
    """
    if _background_tasks.get("refresh_memory", {}).get("status") == "running":
        print(f"{_GRN}[REFRESH]{_RST} Memory refresh is already running; skipping redundant trigger.", flush=True)
        return

    # Free VRAM before a heavy multi-phase Ollama operation starts.
    cancel_pending_consolidation()
    cancel_pending_extraction()

    _background_tasks["refresh_memory"] = {
        "status": "running",
        "phase": "Starting...",
        "started_at": time.time(),
    }

    async def _run_subprocess():
        """Run refresh_memory.py as an async subprocess, streaming phase updates to the registry."""
        try:
            import sys
            script_path = str(BASE_DIR / "Evelyn" / "tools" / "refresh_memory.py")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-u", script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(BASE_DIR),
            )

            while True:
                line_bytes = await proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                print(f"{_GRN}[REFRESH]{_RST} {line}", flush=True)

                if line.startswith("[PHASE_START:"):
                    key = line.split("[PHASE_START:")[1].split("]")[0]
                    _background_tasks["refresh_memory"]["phase"] = _REFRESH_PHASE_LABELS.get(key, f"Running {key}...")

                elif line.startswith("[PHASE_FAIL:"):
                    key = line.split("[PHASE_FAIL:")[1].split("]")[0]
                    raise RuntimeError(f"Phase '{key}' failed.")

            await proc.wait()

            if proc.returncode == 0:
                _background_tasks["refresh_memory"].update({
                    "status": "done",
                    "phase": "Completed successfully.",
                    "finished_at": time.time(),
                })
                print(f"{_GRN}[REFRESH]{_RST} All phases done.", flush=True)
            else:
                raise RuntimeError(f"Pipeline exited with code {proc.returncode}")

        except Exception as e:
            _background_tasks["refresh_memory"].update({
                "status": "error",
                "phase": "Failed.",
                "error": str(e),
                "finished_at": time.time(),
            })
            print(f"{_RED}[REFRESH ERROR]{_RST} {e}", flush=True)

    asyncio.create_task(_run_subprocess())


@app.post("/refresh_memory")
async def trigger_refresh_memory(_: None = Depends(check_auth)):
    """Trigger the unified Memory Refresh pipeline as an async subprocess.

    Sequentially runs:
      Phase 1 — Vault Map generation (vault_indexer.py)
      Phase 2 — Core Knowledge ingest (ingest_obsidian_knowledge.py)
      Phase 3 — Gist ingest (ingest_gists.py)

    Cancels in-flight consolidation/extraction tasks first to free VRAM.
    Returns 200 OK immediately; the pipeline runs in the background.
    The UI polls GET /task_status/refresh_memory for phase updates.
    """
    await start_refresh_memory_internal()
    return {"status": "refresh memory started"}


class ResearchStartRequest(BaseModel):
    """Pydantic model representing a request to start a new research task."""
    query: str
    scope: str = "standard"


@app.post("/research/start")
async def api_start_research(req: ResearchStartRequest, _: None = Depends(check_auth)):
    """Trigger a deep research task in the background.

    Args:
        req: Start request containing the query and scope.
        _: Authorization dependency.

    Returns:
        dict: A success message and metadata.
    """
    from evelyn_tools import start_research
    _demote_running_task_if_any("new_task")
    result = start_research(req.query, scope=req.scope, bypass_queue=True)
    return {"message": result}


@app.get("/research/status/{task_id}")
async def api_research_status(task_id: str, _: None = Depends(check_auth)):
    """Return the real-time status of a research task.

    Args:
        task_id: The ID of the research task.
        _: Authorization dependency.

    Returns:
        dict: The state dictionary of the task.
    """
    from research_engine import load_state
    state = load_state(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Research task not found")
    return state


@app.get("/research/report/{task_id}")
async def api_research_report(task_id: str, _: None = Depends(check_auth)):
    """Return the synthesized report of a research task.

    Args:
        task_id: The ID of the research task.
        _: Authorization dependency.

    Returns:
        dict: A dictionary containing the markdown report content.
    """
    import os
    from research_engine import get_task_dir
    task_dir = get_task_dir(task_id)
    report_file = os.path.join(task_dir, "report.md")
    if not os.path.exists(report_file):
        raise HTTPException(status_code=404, detail="Report not synthesized yet or task failed")
    with open(report_file, "r", encoding="utf-8") as f:
        return {"report": f.read()}


@app.get("/research/list")
async def api_research_list(_: None = Depends(check_auth)):
    """List all research tasks sorted by creation date, merging in-progress and queued items."""
    import os
    import json
    research_dir = cfg.RESEARCH_DATA_DIR
    if not os.path.exists(research_dir):
        return []
    tasks = []
    existing_queries = set()
    for d in os.listdir(research_dir):
        task_dir = os.path.join(research_dir, d)
        if os.path.isdir(task_dir):
            state_file = os.path.join(task_dir, "state.json")
            if os.path.exists(state_file):
                try:
                    with open(state_file, "r", encoding="utf-8") as f:
                        state = json.load(f)
                        tasks.append(state)
                        if state.get("query"):
                            existing_queries.add(state["query"])
                except Exception:
                    pass

    # Helper to clean/check duplicates
    def queries_are_duplicates(q1: str, q2: str) -> bool:
        """Check if two research queries are semantic duplicates.

        Args:
            q1: The first query string to compare.
            q2: The second query string to compare.

        Returns:
            bool: True if the queries exceed the duplication similarity thresholds.
        """
        import re
        if not q1 or not q2:
            return False
        words1 = set(w for w in re.findall(r"\w+", q1.lower()) if len(w) > 3)
        words2 = set(w for w in re.findall(r"\w+", q2.lower()) if len(w) > 3)
        if not words1 or not words2:
            return False
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        jaccard = len(intersection) / len(union)
        if jaccard >= 0.45:
            return True
        overlap_count = len(intersection)
        min_len = min(len(words1), len(words2))
        if overlap_count >= 4 and (overlap_count / min_len) >= 0.75:
            return True
        return False

    # 2. Process queue.json, filtering duplicates and adding queued items
    queue_file = os.path.join(research_dir, "queue.json")
    if os.path.exists(queue_file):
        try:
            with open(queue_file, "r", encoding="utf-8") as f:
                queue = json.load(f)
                
            filtered_queue = []
            queue_changed = False
            
            for item in queue:
                q = item.get("query", "")
                # Check if this queue item is already running/done
                is_duplicate = False
                for eq in existing_queries:
                    if queries_are_duplicates(q, eq):
                        is_duplicate = True
                        break
                        
                if is_duplicate:
                    print(f"[RESEARCH QUEUE] Automatically removing duplicate task from queue: '{q}'", flush=True)
                    queue_changed = True
                else:
                    filtered_queue.append(item)
                    
            # If we stripped out duplicates, write the sanitized queue back to disk
            if queue_changed:
                with open(queue_file, "w", encoding="utf-8") as f:
                    json.dump(filtered_queue, f, indent=2)
                queue = filtered_queue
                
            # Add remaining queued items to the tasks list
            for idx, item in enumerate(queue):
                temp_id = f"queued_{idx}"
                tasks.append({
                    "task_id": temp_id,
                    "query": item.get("query"),
                    "scope": item.get("scope", "standard"),
                    "status": "queued",
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("created_at"),
                    "triggered_by": item.get("source", "evelyn"),
                    "current_step": "queued",
                    "confidence": None,
                    "total_sources": 0,
                    "orchestrator_turns": 0
                })
        except Exception as e:
            print(f"[RESEARCH LIST ERROR] Failed to process queue.json: {e}", flush=True)

    tasks.sort(key=lambda t: t.get("created_at", "") or "", reverse=True)
    return tasks


@app.post("/research/cancel/{task_id}")
async def api_cancel_research(task_id: str, _: None = Depends(check_auth)):
    """Cancel an in-flight or queued research task.

    Args:
        task_id: The ID of the task to cancel.
        _: Authorization dependency.

    Returns:
        dict: Cancellation status indicator.
    """
    import os
    if task_id.startswith("queued_"):
        try:
            idx = int(task_id.split("_")[1])
            queue_file = os.path.join(cfg.RESEARCH_DATA_DIR, "queue.json")
            if os.path.exists(queue_file):
                with open(queue_file, "r", encoding="utf-8") as f:
                    queue = json.load(f)
                if 0 <= idx < len(queue):
                    removed = queue.pop(idx)
                    with open(queue_file, "w", encoding="utf-8") as f:
                        json.dump(queue, f, indent=2)
                    print(f"[RESEARCH QUEUE] Cancelled queued task: '{removed.get('query')}'", flush=True)
                    return {"status": "cancelled", "task_id": task_id}
            raise HTTPException(status_code=404, detail="Queue file not found or index invalid")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to cancel queued task: {e}")

    from research_engine import load_state, save_state
    state = load_state(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="Research task not found")
    state["status"] = "cancelled"
    state["termination_reason"] = "user_cancel"
    save_state(task_id, state)
    
    if task_id in _background_tasks:
        _background_tasks[task_id]["status"] = "cancelled"
        _background_tasks[task_id]["finished_at"] = time.time()
        
    terminate_research_process(task_id)
        
    return {"status": "cancelled", "task_id": task_id}


@app.post("/research/delete/{task_id}")
async def api_delete_research(task_id: str, _: None = Depends(check_auth)):
    """Permanently delete a research task directory from disk and server memory.

    Args:
        task_id: The ID of the task to delete.
        _: Authorization dependency.

    Returns:
        dict: Deletion status indicator.
    """
    import shutil
    import os
    
    # 1. Handle queued task ID (e.g. queued_N)
    if task_id.startswith("queued_"):
        try:
            idx = int(task_id.split("_")[1])
            queue_file = os.path.join(cfg.RESEARCH_DATA_DIR, "queue.json")
            if os.path.exists(queue_file):
                with open(queue_file, "r", encoding="utf-8") as f:
                    queue = json.load(f)
                if 0 <= idx < len(queue):
                    removed = queue.pop(idx)
                    with open(queue_file, "w", encoding="utf-8") as f:
                        json.dump(queue, f, indent=2)
                    print(f"[RESEARCH QUEUE] Deleted queued task: '{removed.get('query')}'", flush=True)
                    return {"status": "deleted", "task_id": task_id}
            raise HTTPException(status_code=404, detail="Queue item or file not found")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete queued task: {e}")

    # Terminate process immediately if active
    terminate_research_process(task_id)

    # 2. Handle actual task directories
    task_dir = os.path.join(cfg.RESEARCH_DATA_DIR, task_id)
    if not os.path.exists(task_dir):
        raise HTTPException(status_code=404, detail="Research task not found")
        
    # Delete the directory recursively with a retry loop to handle Windows file lock delays
    import time
    time.sleep(0.5) # Give the terminated process a moment to release handles
    delete_success = False
    for attempt in range(3):
        try:
            shutil.rmtree(task_dir)
            delete_success = True
            break
        except Exception:
            time.sleep(1.0)
            
    if not delete_success:
        # Last ditch effort ignoring errors
        shutil.rmtree(task_dir, ignore_errors=True)
        
    # 3. Clean up server background task tracking
    if task_id in _background_tasks:
        del _background_tasks[task_id]
        
    print(f"[RESEARCH DELETE] Permanently deleted task folder and tracking: {task_id}", flush=True)
    return {"status": "deleted", "task_id": task_id}


def _demote_running_task_if_any(promoting_task_id: str):
    """Automatically pause the currently running research task (if any) and mark it on disk
    with a contextual explanation that it was demoted due to a dashboard manual override.
    """
    running_task = next(
        (tid for tid, t in list(_background_tasks.items())
         if tid.startswith("task_") and t.get("status") == "running" and tid != promoting_task_id),
        None,
    )
    if running_task:
        from research_engine import load_state, save_state
        state = load_state(running_task)
        if state and state.get("status") == "running":
            state["status"] = "paused"
            state["error"] = f"Demoted: Suspended automatically because you manually promoted another research task ({promoting_task_id}) from the dashboard."
            save_state(running_task, state)
            _background_tasks[running_task]["status"] = "paused"
            _background_tasks[running_task]["finished_at"] = time.time()
            terminate_research_process(running_task)
            print(f"[RESEARCH DEMOTION] Auto-paused task {running_task} because task {promoting_task_id} was promoted by the user.", flush=True)


@app.post("/research/resume/{task_id}")
async def api_resume_research(task_id: str, _: None = Depends(check_auth)):
    """Resume a paused, cancelled, or failed research task.

    Args:
        task_id: The ID of the task to resume.
        _: Authorization dependency.

    Returns:
        dict: Status message confirming execution start.
    """
    _demote_running_task_if_any(task_id)
    from evelyn_tools import resume_research_task
    result = resume_research_task(task_id)
    return {"message": result}


class GuideRequest(BaseModel):
    """Pydantic model representing a request to inject guidance into a research task."""
    guidance: str

class SQRewriteRequest(BaseModel):
    """Pydantic model representing a request to rewrite a sub-question."""
    sq_id: str
    new_question: str

class FinalizeGuidanceRequest(BaseModel):
    """Pydantic model representing a request to finalize guidance on a research task."""
    pass

@app.post("/research/guide/{task_id}")
async def api_guide_research(task_id: str, request: GuideRequest, _: None = Depends(check_auth)):
    """Inject guidance into a struggling research task and resume it.

    Args:
        task_id: The ID of the task.
        request: Request containing the guidance text.
        _: Authorization dependency.

    Returns:
        dict: Status message confirming guidance injection.
    """
    _demote_running_task_if_any(task_id)
    from evelyn_tools import guide_research
    result = guide_research(task_id, request.guidance)
    return {"message": result}

@app.post("/research/guide/{task_id}/rewrite")
async def api_guide_research_rewrite(task_id: str, request: SQRewriteRequest, _: None = Depends(check_auth)):
    """Submit a single sub-question rewrite (does not resume the task).

    Args:
        task_id: The ID of the task.
        request: Request containing the sub-question ID and new text.
        _: Authorization dependency.

    Returns:
        dict: Status message confirming the rewrite.
    """
    from evelyn_tools import rewrite_sub_question
    result = rewrite_sub_question(task_id, request.sq_id, request.new_question)
    return {"message": result}

@app.post("/research/guide/{task_id}/finalize")
async def api_guide_research_finalize(task_id: str, _: None = Depends(check_auth)):
    """Finalize manual guidance edits and resume the task.

    Args:
        task_id: The ID of the task.
        _: Authorization dependency.

    Returns:
        dict: Status message confirming finalization and resumption.
    """
    _demote_running_task_if_any(task_id)
    from evelyn_tools import finalize_guidance
    result = finalize_guidance(task_id)
    return {"message": result}


@app.post("/research/start-now/{task_id}")
async def api_start_now_research(task_id: str, _: None = Depends(check_auth)):
    """Force-start a queued or paused research task immediately.

    Args:
        task_id: The ID of the task (or queued index) to start.
        _: Authorization dependency.

    Returns:
        dict: Status message confirming execution start.
    """
    import os

    _demote_running_task_if_any(task_id)

    if task_id.startswith("queued_"):
        try:
            idx = int(task_id.split("_")[1])
            queue_file = os.path.join(cfg.RESEARCH_DATA_DIR, "queue.json")
            if not os.path.exists(queue_file):
                raise HTTPException(status_code=404, detail="Queue file not found")

            with open(queue_file, "r", encoding="utf-8") as f:
                queue = json.load(f)

            if not (0 <= idx < len(queue)):
                raise HTTPException(status_code=404, detail="Queue index out of range")

            item = queue.pop(idx)
            with open(queue_file, "w", encoding="utf-8") as f:
                json.dump(queue, f, indent=2)

            query = item.get("query", "")
            scope = item.get("scope", "standard")
            print(f"[RESEARCH START-NOW] Dequeued and starting: '{query}' (scope={scope})", flush=True)

            from evelyn_tools import start_research
            result = start_research(query, scope=scope, bypass_queue=True)
            return {"message": result}

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to start queued task: {e}")

    # For real task IDs (paused / cancelled / error) — resume in-place
    from evelyn_tools import resume_research_task
    result = resume_research_task(task_id)
    return {"message": result}


@app.post("/tts")
async def tts_proxy(request: Request):
    """Proxy TTS requests to the local qwen_tts_server.
    Keeps the TTS server local-only while allowing Tailscale/mobile clients
    to reach it through evelyn_server (which is already on 0.0.0.0).
    """
    body = await request.body()
    async with httpx.AsyncClient(timeout=300) as client:
        try:
            resp = await client.post(
                f"{cfg.TTS_SERVER_URL}/v1/audio/speech",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="TTS server is not running")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=str(e))
    from fastapi.responses import Response

    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "audio/flac"),
    )


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the Evelyn UI index.html, or a fallback page if the UI files are missing."""
    index = UI_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h1>Evelyn Server Running</h1><p>Place UI files in evelyn_ui/</p>"
    )


# --- Prints System Prompt during Startup ---

if __name__ == "__main__":
    try:
        # We call the definition here
        final_prompt = load_system_prompt()

        print("--- START OF SYSTEM PROMPT ---")
        print(final_prompt)
        print("--- END OF SYSTEM PROMPT ---")

    except NameError as e:
        print(f"Error: It looks like something is missing in the script: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Developer Web UI Endpoints
# ---------------------------------------------------------------------------
class EditEntryRequest(BaseModel):
    """Pydantic model representing a request to edit a memory entry."""
    observation: str = None

@app.get("/api/review/extractions")
async def get_extractions(_: None = Depends(check_auth)):
    """Return all extracted (pending review) memory entries."""
    import Evelyn.tools.memory_db as memory_db
    return memory_db.get_all_entries(statuses=["extracted"])

@app.post("/api/review/extractions/{id}/{action}")
async def action_extraction(id: int, action: str, req: EditEntryRequest = None, _: None = Depends(check_auth)):
    """Approve, delete, or edit an extracted memory entry.

    Args:
        id:     SQLite row ID of the entry.
        action: "approve" | "delete" | "edit".
        req:    Required for "edit" — carries the updated observation text.
    """
    import Evelyn.tools.memory_db as memory_db
    if action == "approve":
        memory_db.update_entry(id, status="live")
        await start_refresh_memory_internal()
    elif action == "delete":
        memory_db.delete_entry(id)
    elif action == "edit" and req and req.observation:
        memory_db.update_entry(id, observation=req.observation, status="live")
        await start_refresh_memory_internal()
    else:
        raise HTTPException(status_code=400, detail="Invalid action")
    return {"status": "ok"}

@app.get("/api/review/proposals")
async def get_proposals(_: None = Depends(check_auth)):
    """Return all pending consolidation/recategorization proposals with their source entries."""
    import Evelyn.tools.memory_db as memory_db
    proposals = memory_db.get_pending_proposals()
    for p in proposals:
        source_entries = []
        for eid in p.get("source_ids", []):
            entry = memory_db.get_entry(eid)
            if entry:
                source_entries.append(entry)
        p["source_entries"] = source_entries
    return proposals

@app.post("/api/review/proposals/{id}/{action}")
async def action_proposal(id: int, action: str, _: None = Depends(check_auth)):
    """Approve or deny a consolidation/recategorization proposal.

    Args:
        id:     Proposal row ID.
        action: "approve" | "deny".
    """
    import Evelyn.tools.memory_db as memory_db
    if action == "deny":
        memory_db.reject_proposal(id)
        return {"status": "ok"}
    elif action == "approve":
        proposals = memory_db.get_pending_proposals()
        prop = next((p for p in proposals if p["id"] == id), None)
        if not prop:
            raise HTTPException(status_code=404, detail="Proposal not found")
            
        source_entries = []
        for eid in prop.get("source_ids", []):
            entry = memory_db.get_entry(eid)
            if entry:
                source_entries.append(entry)
        
        if prop["type"] == "recategorize":
            for entry in source_entries:
                memory_db.update_entry(entry["id"], category=prop["suggested_category"])
            memory_db.apply_proposal(id)
        elif prop["type"] in ("merge", "supersede"):
            for entry in source_entries:
                memory_db.delete_entry(entry["id"])
            subject = source_entries[0]["subject"] if source_entries else "R"
            date = source_entries[0]["date"] if source_entries else None
            
            if prop.get("merged_tags"):
                merged_tags = prop["merged_tags"]
            else:
                merged_tags_set = set()
                for entry in source_entries:
                    if entry.get("tags"):
                        for t in entry["tags"].split(","):
                            if t.strip():
                                merged_tags_set.add(t.strip())
                merged_tags = ", ".join(sorted(merged_tags_set)) if merged_tags_set else None
            
            memory_db.insert_entry(
                category=prop["suggested_category"],
                subject=subject,
                observation=prop["merged_observation"],
                source="consolidated",
                date=date,
                tags=merged_tags
            )
            memory_db.apply_proposal(id)
        await start_refresh_memory_internal()
        return {"status": "ok"}
    else:
        raise HTTPException(status_code=400, detail="Invalid action")


if __name__ == "__main__":
    import uvicorn
    import os

    SSL_KEY = "ricky-pc.tail0e161b.ts.net.key"
    SSL_CERT = "ricky-pc.tail0e161b.ts.net.crt"
    ssl_args = {}
    if os.path.exists(SSL_KEY) and os.path.exists(SSL_CERT):
        ssl_args = {"ssl_keyfile": SSL_KEY, "ssl_certfile": SSL_CERT}
        print("SSL certs found -- starting with HTTPS")
    else:
        print("No SSL certs found -- starting with plain HTTP (fine for Tailscale)")

    uvicorn.run(
        "evelyn_server:app",
        host=cfg.BIND_HOST,
        port=cfg.SERVER_PORT,
        reload=False,
        log_level="info",
        **ssl_args,
    )
