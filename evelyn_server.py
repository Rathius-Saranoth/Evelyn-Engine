# evelyn_server.py
# date created: 2026-03-23 15:43:21
# date modified: 2026-08-15 17:12:42
# tags: #server, #fastAPI, #RAG, #async, #backend

"""
evelyn_server.py — Custom Evelyn backend server.

FastAPI app providing:
  - POST /chat       — Streaming chat with tool loop, RAG injection, inline think-tag parsing
  - POST /regenerate — Delete last assistant response and re-generate
  - POST /edit       — Update last user message in DB and stream new response
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
import os
import re
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
from typing import Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
TOOLS_DIR = BASE_DIR / "Evelyn" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
PERSONA_DIR = BASE_DIR / "Evelyn" / "persona"

import evelyn_config as cfg
from evelyn_tools import MODEL_TOOL_DEFINITIONS, TOOL_FUNCTIONS, TOOL_THINK_EFFORT
from chroma_rag import build_rag_context
from fact_consolidator import run_consolidation, cancel_pending_consolidation
from procedure_consolidator import run_procedure_consolidation, cancel_pending_procedure_consolidation
from fact_extractor import run_extraction, cancel_pending_extraction
from profile_evolver import run_profile_evolution, cancel_pending_evolution, advance_doc_run_timestamp


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
_last_window_warn_ts: float = 0.0
_active_research_processes = {}
_last_research_spawn_ts: float = 0.0   # Layer 2: spawn debounce
_error_resume_ts: dict = {}            # Layer 3: per-task error cooldown

# ---------------------------------------------------------------------------
# In-Memory Stream Buffer & Session Management
# ---------------------------------------------------------------------------

class ActiveStreamSession:
    """Buffer and notification manager for a single active chat generation turn."""

    def __init__(self, stream_id: str):
        self.stream_id: str = stream_id
        self.chunks: list[dict] = []  # [{"id": int, "event": str}]
        self.status: str = "running"  # "running", "completed", "error"
        self.event_notify: asyncio.Event = asyncio.Event()
        self.created_at: float = time.time()
        self.completed_at: float | None = None
        self.error_msg: str | None = None

    def push_chunk(self, raw_event_str: str):
        """Append an event chunk and wake all awaiting listeners without race conditions."""
        chunk_id = len(self.chunks)
        self.chunks.append({
            "id": chunk_id,
            "event": raw_event_str
        })
        old_event = self.event_notify
        self.event_notify = asyncio.Event()
        old_event.set()

    def mark_complete(self, error: str | None = None):
        """Mark stream complete or error and wake all listeners."""
        if error:
            self.status = "error"
            self.error_msg = error
        else:
            self.status = "completed"
        self.completed_at = time.time()
        old_event = self.event_notify
        self.event_notify = asyncio.Event()
        old_event.set()


class StreamRegistry:
    """Registry tracking active and recently completed streaming sessions."""

    def __init__(self):
        self.sessions: dict[str, ActiveStreamSession] = {}
        self.active_stream_id: str | None = None

    def create(self, stream_id: str) -> ActiveStreamSession:
        self.cleanup_stale()
        session = ActiveStreamSession(stream_id)
        self.sessions[stream_id] = session
        self.active_stream_id = stream_id
        return session

    def get(self, stream_id: str) -> ActiveStreamSession | None:
        return self.sessions.get(stream_id)

    def get_active(self) -> ActiveStreamSession | None:
        if self.active_stream_id:
            s = self.sessions.get(self.active_stream_id)
            if s and s.status == "running":
                return s
        return None

    def cleanup_stale(self, ttl_seconds: int = 300):
        now = time.time()
        expired = [
            sid for sid, s in self.sessions.items()
            if s.completed_at and (now - s.completed_at > ttl_seconds)
        ]
        for sid in expired:
            del self.sessions[sid]
        if self.active_stream_id in expired:
            self.active_stream_id = None


stream_registry = StreamRegistry()


async def stream_session_events(
    session: ActiveStreamSession,
    after: int = -1,
    request: Request | None = None
):
    """Asynchronous generator that replays buffered chunks and streams live events."""
    cursor = after + 1
    try:
        while True:
            # 1. Replay / flush any chunks past cursor
            while cursor < len(session.chunks):
                chunk = session.chunks[cursor]
                yield f"id: {chunk['id']}\n{chunk['event']}"
                cursor += 1

            # 2. If finished and caught up, exit cleanly
            if session.status in ("completed", "error") and cursor >= len(session.chunks):
                break

            # 3. Check client disconnect
            if request and await request.is_disconnected():
                break

            # 4. Wait for new chunk or timeout (for keep-alive heartbeat)
            if cursor >= len(session.chunks):
                current_event = session.event_notify
                if cursor < len(session.chunks) or session.status in ("completed", "error"):
                    continue
                try:
                    await asyncio.wait_for(current_event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    yield 'data: {"type":"heartbeat"}\n\n'
    except (GeneratorExit, asyncio.CancelledError):
        pass

# ---------------------------------------------------------------------------
# Thinking-effort classifier
# ---------------------------------------------------------------------------

# Regex for stripping model-emitted self-election hints from content streams.
# Applied in pass1 content cleanup AND in _stream_content to prevent leaking
# into chat bubbles. Defined here so both sites share the same compiled pattern.
_SELF_ELECT_RE = re.compile(
    r'\s*\{"requested_effort":\s*"(?:low|medium|high|max)"\}\s*',
    re.IGNORECASE,
)

# Trivial pattern: must be the ENTIRE message (fullmatch), must be short (<45 chars).
# "Thanks! Why didn't that work?" → fails fullmatch → falls to medium. ✓
_TRIVIAL_RE = re.compile(
    r"\s*(good\s*night|gn|goodnight|good\s*morning|gm|"
    r"thank(?:\s*you)?(?:\s+(?:so\s+much|very\s+much))?|thanks(?:\s+(?:so\s+much|a\s+lot))?|thx|ty|"
    r"ok(?:ay)?|k|👍|✓|✔|"
    r"sounds?\s*good|perfect|noted|will\s*do|alright|sure|got\s*it|"
    r"bye|goodbye|see\s*you|take\s*care|later|ttyl|"
    r"night|sweet\s*dreams?|sleep\s*well)\W*",
    re.IGNORECASE | re.DOTALL,
)

# Complex pattern: unambiguous analytical/multi-step phrasing only.
# \b boundaries: "why is that?" does not match; "explain why X" does.
# Length gate (>50) stops short rhetorical questions from escalating.
_COMPLEX_RE = re.compile(
    r"\b(analyze|analyse|deep\s+dive|walk\s+me\s+through|"
    r"step[\s-]by[\s-]step|compare\s+and\s+contrast|"
    r"help\s+me\s+understand|explain\s+(?:how|why|what)|"
    r"what(?:'s|\s+is)\s+the\s+best\s+way|"
    r"diagnose|troubleshoot|figure\s+out|think\s+through|"
    r"what\s+should\s+(?:i|we)\s+do\s+about|"
    r"struggling\s+with|is\s+there\s+a\s+better\s+way)\b",
    re.IGNORECASE,
)

# Numeric ranking for effort comparison during escalation.
_EFFORT_RANK: dict[str, int] = {"false": -1, "low": 0, "medium": 1, "high": 2, "max": 3}


def classify_message_effort(message: str) -> str:
    """Heuristic pre-classifier: returns a suggested think effort level.

    Strict priority hierarchy:
      1. Trivial isolated phrase (<45 chars, fullmatch) → "low"
      2. Analytical keywords present (>50 chars)       → "high"
      3. Everything else                                → "medium"

    The model may still self-elect or tool-escalation may override this result.

    Returns:
        str: One of "low", "medium", or "high".
    """
    stripped = message.strip()

    # Rule 1 — trivial: entire message must match, must be short
    if len(stripped) < 45 and _TRIVIAL_RE.fullmatch(stripped):
        return "low"

    # Rule 2 — complex: analytical phrasing + length gate to avoid casual
    # rhetorical questions ("Why is that?" = 13 chars → medium, not high)
    if len(stripped) > 50 and _COMPLEX_RE.search(stripped):
        return "high"

    # Rule 3 — baseline
    return "medium"



def _in_research_window() -> bool:
    """Return True if the current local hour is within the configured research window.

    If both RESEARCH_ACTIVE_HOURS_START and RESEARCH_ACTIVE_HOURS_END are 0, the
    window check is disabled and research can run at any hour.

    Returns:
        bool: True if research is permitted to start or resume right now.
    """
    start = getattr(cfg, "RESEARCH_ACTIVE_HOURS_START", 6)
    end   = getattr(cfg, "RESEARCH_ACTIVE_HOURS_END",   21)
    if start == 0 and end == 0:
        return True  # Windowing disabled
    current_hour = time.localtime().tm_hour
    return start <= current_hour < end


def terminate_research_process(task_id: str):
    """Immediately terminate the active background subprocess for a research task if running and clean up lock files."""
    proc = _active_research_processes.pop(task_id, None)
    if proc:
        try:
            print(f"[RESEARCH TERMINATE] Terminating active subprocess handle for task {task_id}", flush=True)
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        except Exception as e:
            print(f"[RESEARCH TERMINATE ERROR] Failed to terminate subprocess handle {task_id}: {e}", flush=True)

    # Hardened cleanup: check engine.pid via psutil, kill orphan process if alive, and remove engine.pid
    try:
        from Evelyn.tools.research_engine import get_task_dir
        pid_path = os.path.join(get_task_dir(task_id), "engine.pid")
        if os.path.exists(pid_path):
            try:
                with open(pid_path) as f:
                    pid = int(f.read().strip())
                import psutil
                if psutil.pid_exists(pid):
                    p = psutil.Process(pid)
                    if any("research_engine.py" in arg for arg in p.cmdline()):
                        print(f"[RESEARCH TERMINATE] Killing process PID {pid} for task {task_id}", flush=True)
                        p.terminate()
                        try:
                            p.wait(timeout=2.0)
                        except Exception:
                            p.kill()
            except Exception:
                pass
            finally:
                try:
                    os.remove(pid_path)
                except OSError:
                    pass
    except Exception as e:
        print(f"[RESEARCH TERMINATE ERROR] PID cleanup failed for {task_id}: {e}", flush=True)


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
        lines.append(f"\n(Context note: {unnotified_count} newly completed deep research task(s) are ready. You may call 'check_new_research' if relevant to Ricky's prompt.)")
            
    return "\n".join(lines)


def get_upcoming_agenda_prompt_context() -> str:
    """Fetch a high-level summary notification of upcoming Google Calendar events to inject into the system prompt.

    Avoids token bloat by notifying about counts and only listing urgent pending events.
    """
    try:
        import sys
        TOOLS_DIR = str(BASE_DIR / "Evelyn" / "tools")
        if TOOLS_DIR not in sys.path:
            sys.path.append(TOOLS_DIR)
        import gcal_sync
        
        # 1. Fetch upcoming calendar events for the next 24 hours (days_back=0, days_forward=1)
        events = gcal_sync.get_cached_gcal_events(days_back=0, days_forward=1)
        
        lines = []
        if events:
            lines.append(f"(Context note: Ricky has {len(events)} upcoming calendar event(s) in the next 24 hours. You may call 'get_agenda' if Ricky asks about his schedule.)")
        
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
        "you don't need lengthy chains for casual conversation. "
        "If a turn calls for unusually deep reflection (complex planning, emotional nuance, "
        "multi-step analysis), you may include {\"requested_effort\":\"high\"} on its own line before "
        "your response. For brief acknowledgments or casual sign-offs where deep reasoning is "
        "unnecessary, include {\"requested_effort\":\"low\"} instead. "
        "Do not include this marker in routine replies."
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
        str | None: A succinct bracketed explanation of the last message time,
            elapsed time gap, and current time if exceeding 5 minutes, otherwise None.
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

    last_time_str = (
        last_ts.strftime("%a %b %d, %I:%M %p").lstrip("0")
        if last_ts.date() != now.date()
        else last_ts.strftime("%I:%M %p").lstrip("0")
    )

    if delta < _td(hours=1):
        mins = int(delta.total_seconds() // 60)
        gap_str = f"{mins} minutes"
    elif delta < _td(hours=6):
        hrs = delta.total_seconds() / 3600
        label = f"{hrs:.1f}".rstrip("0").rstrip(".")
        gap_str = f"{label} hours"
    else:
        days = delta.days
        hrs = delta.seconds // 3600
        parts = []
        if days:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hrs:
            parts.append(f"{hrs} hour{'s' if hrs != 1 else ''}")
        gap_str = " and ".join(parts) if parts else "a long time"

    return f"[Last user message: {last_time_str} ({gap_str} ago)]"


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
            think_effort TEXT,
            think_source TEXT,
            FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
        )
    """)
    # Migrate: add think_effort and think_source columns if missing
    try:
        con.execute("ALTER TABLE message_metrics ADD COLUMN think_effort TEXT")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE message_metrics ADD COLUMN think_source TEXT")
    except Exception:
        pass

    # Drop reminders table if it exists to cleanly remove local reminders data
    con.execute("DROP TABLE IF EXISTS reminders")

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
    """Load recent chat history bounded by day boundaries, thread breaks, and caps.

    Rules:
      1. Loads 100% of today's messages (ts >= midnight).
      2. Plus up to 6 messages from the previous day (for evening/transition context).
      3. Overall bounded by cfg.MAX_HISTORY_MESSAGES (hard limit).
      4. Bounded by the latest [THREAD_BREAK] marker if present.
      5. Inject explicit date boundary markers with journal isolation instructions.

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
    from datetime import time as dtime
    today_start = datetime.combine(datetime.now().date(), dtime.min).timestamp()

    # 1. Fetch today's messages (newest first)
    today_rows = con.execute(
        "SELECT role, content, ts FROM messages WHERE id > ? AND ts >= ? ORDER BY id DESC LIMIT ?",
        (after_id, today_start, limit),
    ).fetchall()

    # 2. Fetch up to 6 messages from yesterday (if limit headroom permits)
    remaining_limit = max(0, limit - len(today_rows))
    prev_day_limit = min(6, remaining_limit)

    prev_rows = []
    if prev_day_limit > 0:
        prev_rows = con.execute(
            "SELECT role, content, ts FROM messages WHERE id > ? AND ts < ? ORDER BY id DESC LIMIT ?",
            (after_id, today_start, prev_day_limit),
        ).fetchall()

    con.close()

    # Combine: prev_rows (older) + today_rows (newer), then reverse to chronological order
    rows = list(reversed(today_rows + prev_rows))

    # Skip empty-content rows, placeholder messages, and thread-break markers.
    valid_rows = [
        r
        for r in rows
        if r["content"].strip()
        and not r["content"].startswith(PLACEHOLDER_MARKER)
        and r["content"] != THREAD_BREAK_MARKER
    ]

    messages = []
    last_date = None

    for r in valid_rows:
        ts = r["ts"]
        if ts:
            try:
                msg_date = datetime.fromtimestamp(ts).date()
                if last_date is not None and msg_date != last_date:
                    date_str = msg_date.strftime("%A, %b %d, %Y")
                    messages.append({
                        "role": "system",
                        "content": f"--- Date Changed: {date_str} (All journal entries and daily reflections must reference ONLY events occurring after this date marker) ---",
                    })
                last_date = msg_date
            except (OSError, OverflowError, ValueError):
                pass

        messages.append({
            "role": r["role"],
            "content": (
                f"{_time_of_day_label(ts)}{r['content']}"
                if r["role"] == "user"
                else r["content"]
            ),
        })

    # Strip orphaned trailing user/system messages (no assistant response yet).
    # These form double-user-message chains that confuse the model.
    while messages and messages[-1]["role"] in ("user", "system"):
        messages.pop()

    dlog(f"History: loaded {len(today_rows)} today + {len(prev_rows)} prev day = {len(messages)} total msgs")
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
            eval_count, total_duration, think_effort, think_source, etc.
    """
    if not metrics:
        return
    con = get_db()
    con.execute(
        """INSERT INTO message_metrics 
           (message_id, prompt_eval_count, prompt_eval_duration, eval_count, eval_duration, total_duration, load_duration, think_effort, think_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            message_id,
            metrics.get("prompt_eval_count"),
            metrics.get("prompt_eval_duration"),
            metrics.get("eval_count"),
            metrics.get("eval_duration"),
            metrics.get("total_duration"),
            metrics.get("load_duration"),
            metrics.get("think_effort"),
            metrics.get("think_source"),
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


def edit_last_user_message(new_text: str) -> str | None:
    """Delete the last assistant message and update the last user message content.

    Args:
        new_text: The updated content for the last user message.

    Returns:
        str | None: The new message content, or None if no user message exists.
    """
    con = get_db()
    # Delete the last assistant row
    last_asst = con.execute(
        "SELECT id FROM messages WHERE role = 'assistant' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if last_asst:
        con.execute("DELETE FROM messages WHERE id = ?", (last_asst["id"],))
        con.commit()

    # Find and update the last user message
    last_user = con.execute(
        "SELECT id FROM messages WHERE role = 'user' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not last_user:
        con.close()
        return None

    con.execute(
        "UPDATE messages SET content = ?, ts = ? WHERE id = ?",
        (new_text, time.time(), last_user["id"]),
    )
    con.commit()
    con.close()
    return new_text



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


async def call_ollama_stream(messages: list[dict], tools: list[dict] = None,
                              think_effort=None):
    """Stream a chat request to Ollama.

    Note that streaming combined with think=True silently swallows tool_call
    tokens in Ollama. Thus, this is only used for follow-up content passes.

    Args:
        messages: A list of message objects mapping to conversation history.
        tools: Optional list of tool definitions.
        think_effort: Thinking effort level for this request. One of False,
            "low", "medium", "high", "max". Defaults to cfg.THINK when None.

    Yields:
        str: Raw JSON response lines from the Ollama server.
    """
    use_think = think_effort if think_effort is not None else cfg.THINK
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


async def call_ollama_full(
    messages: list[dict],
    tools: list[dict] = None,
    num_predict_override: int | None = None,
) -> dict:
    """Perform a non-streaming Ollama call for tool detection and agentic reasoning.

    Used for every round of the tool loop. With THINK_TOOL_LOOP=True, the model
    reasons at each decision point — evaluating tool results and deciding whether
    to call another tool or exit the loop.

    Args:
        messages: A list of message objects representing the prompt history.
        tools: Optional list of tool definitions available to the model.
        num_predict_override: If set, overrides cfg.NUM_PREDICT for this call.
            Tool-loop rounds pass cfg.TOOL_LOOP_NUM_PREDICT (a smaller budget)
            since mid-loop reasoning only needs to evaluate results and route,
            not generate a full response.

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
        "num_predict": num_predict_override if num_predict_override is not None else cfg.NUM_PREDICT,
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
        "think": cfg.THINK_TOOL_LOOP,  # Enables reasoning at each tool decision point
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


async def _stream_content(msgs: list[dict], think_effort=None):
    """
    Stream the content follow-up pass (no tool definitions).
    Handles native think field + inline <think> tag parsing.
    Yields SSE data strings.
    Returns final (content_buf, thinking_buf) via a _state sentinel event.

    Args:
        msgs: Conversation messages to send to Ollama.
        think_effort: Thinking effort level for this response pass. Forwarded
            to call_ollama_stream. Defaults to cfg.THINK when None.
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
            async for line in call_ollama_stream(msgs, tools=None, think_effort=think_effort):
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

            # Content field -- strip leaked model tokens and self-elect hints,
            # then route through inline-tag parser.
            text_delta = msg.get("content", "")
            if text_delta:
                for _tok in _LEAKED_MODEL_TOKENS:
                    text_delta = text_delta.replace(_tok, "")
                # Belt-and-suspenders: strip self-election hints that escaped
                # into the stream (pass1_content cleanup is the primary guard).
                text_delta = _SELF_ELECT_RE.sub("", text_delta)
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
    think: str | bool | None = None  # UI override: "low"/"medium"/"high"/"max"/False/None


class EditRequest(BaseModel):
    """Pydantic model representing an incoming edit message request from the user."""
    message: str



async def _process_chat_background(
    user_message: str,
    is_regenerate: bool,
    time_ctx: str | None,
    assistant_row_id: int,
    session: ActiveStreamSession,
    think_effort: str | bool = "medium",
    ui_override: bool = False,
):
    """Run the background chat processing worker.

    Executes independently of the client's SSE connection state to guarantee
    the model's response is fully generated and committed to the database.

    Args:
        user_message: The text of the user's incoming chat message.
        is_regenerate: True if regenerating the last assistant response.
        time_ctx: Optional time-gap context string to prefix to user message.
        assistant_row_id: The database message ID reserved for the response.
        session: The active stream session used to buffer SSE events.
    """
    content_buf = ""
    thinking_buf = ""
    metrics_dict = {}
    tools_used_list = []
    tool_metadata_list = []

    think_source = "ui_override" if ui_override else "heuristic"

    async def put(type_: str, **kw):
        """Enqueue a serialized SSE event dictionary to the active stream session.

        Args:
            type_: The event type string.
            **kw: Additional fields to serialize into the event payload.
        """
        session.push_chunk("data: " + json.dumps({"type": type_, **kw}) + "\n\n")

    async def drain_stream(stream, response_label: bool = False):
        """Iterate _stream_content, buffer state, and forward events to session.

        Args:
            stream: The async generator from _stream_content.
            response_label: When True, inject a [Response] thinking label before
                the first thinking token. Set True when a tool loop preceded this
                stream so the UI creates a distinct collapsable section.
        """
        nonlocal content_buf, thinking_buf, metrics_dict
        _label_sent = not response_label  # if False, skip label entirely
        async for event in stream:
            if event.startswith("data: "):
                try:
                    d = json.loads(event[6:])
                    if d.get("type") == "_state":
                        content_buf = d["content"]
                        thinking_buf = d.get("thinking", "")
                        metrics_dict.update(d.get("metrics", {}))
                        if metrics_dict:
                            await put("metrics", **metrics_dict)
                        continue          # _state is internal bookkeeping only
                    if d.get("type") == "thinking" and not _label_sent:
                        _label_sent = True
                        thinking_buf += "[Response]\n"
                        session.push_chunk(
                            "data: " + json.dumps({"type": "thinking", "delta": "[Response]\n"}) + "\n\n"
                        )
                    if d.get("type") == "text":
                        content_buf += d.get("delta", "")
                    elif d.get("type") == "thinking":
                        thinking_buf += d.get("delta", "")
                except Exception:
                    pass
            session.push_chunk(event)

    try:
        session.push_chunk("data: " + json.dumps({"type": "stream_session", "stream_id": session.stream_id}) + "\n\n")
        await put("status", msg="Processing...")

        # RAG + system prompt + history (fast synchronous work)
        rag_context = await asyncio.to_thread(build_rag_context, user_message)
        system = load_system_prompt()
        if rag_context:
            system += f"\n\n{rag_context}"
            chunk_count = rag_context.count("\n[")
            pinned_count = rag_context.count("[primary source]")
            dlog(f"RAG injected: chars={len(rag_context)} chunks={chunk_count} pinned={pinned_count}")

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
        # Tool Round 0: Non-streaming reasoning + tool detection
        # ------------------------------------------------------------------
        print(
            f"{_CYN}[TOOL_ROUND_0]{_RST} Reasoning + tool detection. think={cfg.THINK_TOOL_LOOP}. Roles:",
            [m["role"] for m in messages],
            flush=True,
        )

        try:
            pass1_resp = await call_ollama_full(
                messages,
                tools=MODEL_TOOL_DEFINITIONS,
                num_predict_override=cfg.TOOL_LOOP_NUM_PREDICT,
            )
        except Exception as exc:
            print(f"{_RED}[TOOL_ROUND_0 ERROR]{_RST} {type(exc).__name__}: {exc}", flush=True)
            # finally block will log the empty response and update DB
            return

        pass1_msg = pass1_resp.get("message", {})
        tool_calls = pass1_msg.get("tool_calls") or []
        pass1_content = pass1_msg.get("content") or ""
        pass1_thinking = pass1_msg.get("thinking") or ""
        dlog(
            f"Tool round 0 — content: {len(pass1_content)} chars, "
            f"thinking: {len(pass1_thinking)} chars, tools: {len(tool_calls)}"
        )

        if cfg.SHOW_TOOL_LOOP_THINKING and pass1_thinking:
            await put("thinking", delta=f"[Initial]\n{pass1_thinking}")
            thinking_buf += f"[Initial]\n{pass1_thinking}\n\n"

        # Self-election: strip routing hint from content before it enters message
        # history (primary leak-prevention; _stream_content has a belt-and-suspenders strip).
        pass1_content_clean = _SELF_ELECT_RE.sub("", pass1_content).strip()
        if cfg.THINK_SELF_ELECT and not ui_override:
            m = _SELF_ELECT_RE.search(pass1_content)
            if m:
                elected = re.search(
                    r'"requested_effort":\s*"(low|medium|high|max)"',
                    m.group(0), re.IGNORECASE
                )
                if elected:
                    think_effort = elected.group(1)
                    think_source = "self_elect"
                    dlog(f"Self-elected think effort: {think_effort}")
        pass1_content = pass1_content_clean

        if not tool_calls:
            metrics_dict["think_effort"] = str(think_effort)
            metrics_dict["think_source"] = think_source
            has_prior_thinking = bool(pass1_thinking and cfg.SHOW_TOOL_LOOP_THINKING)
            await drain_stream(_stream_content(messages, think_effort=think_effort),
                               response_label=has_prior_thinking)

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

                    result = await loop.run_in_executor(
                        None, lambda fn=fn_name, fa=fn_args: dispatch_tool(fn, fa)
                    )
                    
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
                    elif fn_name in ("run_command", "write_file"):
                        import re
                        m = re.search(r'Approval ID:\s*(cmd_\w+|write_\w+)', result)
                        if m:
                            approval_id = m.group(1)
                            tool_entry = f"{fn_name}[{approval_id}]"
                            meta_entry["data"] = {"id": approval_id, "type": "approval_required"}
                            await put("tool_data", name=fn_name, data=approval_id)
                            
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

                followup_resp = await call_ollama_full(
                    messages,
                    tools=MODEL_TOOL_DEFINITIONS,
                    num_predict_override=cfg.TOOL_LOOP_NUM_PREDICT,
                )
                followup_msg = followup_resp.get("message", {})
                current_tool_calls = followup_msg.get("tool_calls") or []
                current_content = followup_msg.get("content") or ""
                followup_thinking = followup_msg.get("thinking") or ""

                dlog(
                    f"Tool round {tool_round} — content={len(current_content)} chars, "
                    f"thinking={len(followup_thinking)} chars, tools={len(current_tool_calls)}"
                )

                if cfg.SHOW_TOOL_LOOP_THINKING and followup_thinking:
                    await put("thinking", delta=f"[Tool {tool_round}]\n{followup_thinking}")
                    thinking_buf += f"[Tool {tool_round}]\n{followup_thinking}\n\n"

                if not current_tool_calls:
                    dlog("Model produced no more tool calls. Exiting tool loop.")
                    await put("status", msg="Generating response...")
                    break

            # Tool effort escalation: raise response effort if any invoked tool
            # demands more depth than the heuristic/self-elected level.
            if tools_used_list and not ui_override:
                tool_names_used = [t.split("[")[0] for t in tools_used_list if t]
                if tool_names_used:
                    max_tool_effort = max(
                        (TOOL_THINK_EFFORT.get(n, "medium") for n in tool_names_used),
                        key=lambda e: _EFFORT_RANK.get(str(e).lower(), 1),
                        default="medium",
                    )
                    curr_rank = _EFFORT_RANK.get(str(think_effort).lower(), 1)
                    max_rank = _EFFORT_RANK.get(str(max_tool_effort).lower(), 1)
                    if max_rank > curr_rank:
                        dlog(f"Tool effort escalation: {think_effort} → {max_tool_effort} (tools: {tool_names_used})")
                        think_effort = max_tool_effort
                        think_source = "tool_escalation"

            metrics_dict["think_effort"] = str(think_effort)
            metrics_dict["think_source"] = think_source

            # Final streaming response after tool loop
            has_prior_thinking = bool(thinking_buf.strip())
            await drain_stream(_stream_content(messages, think_effort=think_effort),
                               response_label=has_prior_thinking)

    finally:
        # Always commit to DB — independent of whether SSE pipe is alive
        final_content = content_buf.strip()
        tools_str = ",".join(tools_used_list) if tools_used_list else None
        tools_meta_str = json.dumps(tool_metadata_list) if tool_metadata_list else None
        if final_content:
            update_message(
                assistant_row_id,
                final_content,
                thinking=thinking_buf.strip() if thinking_buf.strip() else None,
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
        session.push_chunk(f"data: {json.dumps({'type': 'done'})}\n\n")
        session.mark_complete()

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


def clean_shutdown_all_tasks():
    """Cleanly pause all research tasks and cancel in-flight background worker tasks."""
    print("[SERVER SHUTDOWN] Gracefully shutting down all tasks...", flush=True)
    try:
        pause_all_active_research()
    except Exception as e:
        print(f"[SERVER SHUTDOWN ERROR] Research pause failed: {e}", flush=True)
        
    try:
        cancel_pending_consolidation()
        cancel_pending_procedure_consolidation()
        cancel_pending_extraction()
        cancel_pending_evolution()
    except Exception as e:
        print(f"[SERVER SHUTDOWN ERROR] Background task cancellation failed: {e}", flush=True)





async def chat_stream(user_message: str, is_regenerate: bool = False,
                      think_effort=None, ui_override: bool = False,
                      request: Request | None = None):
    """Open an SSE connection to stream the generated chat response.

    Args:
        user_message: The text of the user's incoming chat message.
        is_regenerate: True if regenerating the last assistant response.
        think_effort: Resolved thinking effort level for this turn.
        ui_override: True when think_effort came from the UI chip (skips
            self-election and tool escalation).
        request: Optional FastAPI Request object for disconnect detection.

    Yields:
        str: Server-Sent Events formatted data blocks.
    """
    global _last_activity_ts
    _last_activity_ts = time.time()
    importlib.reload(cfg)

    # Immediately pause any active deep research to unblock Ollama
    pause_all_active_research()

    cancel_pending_consolidation()
    cancel_pending_procedure_consolidation()
    cancel_pending_extraction()
    cancel_pending_evolution()

    if not is_regenerate:
        time_ctx = get_time_gap_context()
        if time_ctx:
            dlog("Time-gap annotation:", time_ctx)
        save_message("user", user_message)
    else:
        time_ctx = None
        dlog("Regenerating last response")

    # Resolve effort: fallback to heuristic if no UI override was provided
    resolved_effort = think_effort if think_effort is not None else cfg.THINK
    dlog(f"Think effort resolved: {resolved_effort} (ui_override={ui_override})")

    # Reserve DB row and spawn the background task synchronously.
    # From this point the task owns all processing — client can disconnect freely.
    assistant_row_id = save_message_get_id("assistant", "")
    
    stream_id = f"stream_{int(time.time() * 1000)}_{os.urandom(4).hex()}"
    session = stream_registry.create(stream_id)

    asyncio.create_task(
        _process_chat_background(
            user_message, is_regenerate, time_ctx, assistant_row_id, session,
            think_effort=resolved_effort, ui_override=ui_override,
        )
    )
    print(f"{_CYN}[CHAT]{_RST} Background task started for session {stream_id} — SSE pipe open", flush=True)

    # Replay/stream chunks from the session buffer
    async for event in stream_session_events(session, after=-1, request=request):
        yield event







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

    Delegates to task_manager.is_any_running() — the single canonical source
    of truth for mutual exclusion. Preserves the existing exclude_name
    parameter for backwards compatibility with all call sites.

    Args:
        exclude_name: Optional task name to exclude from checking.

    Returns:
        bool: True if another heavy task is currently running, False otherwise.
    """
    import task_manager
    return task_manager.is_any_running(exclude=exclude_name)


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
    import Evelyn.tools.task_manager as task_manager
    task_manager.load_persistent_state()
    asyncio.create_task(task_manager.start_watchdog())
    print(f"{_BLD}{_CYN}Evelyn server starting on {cfg.BIND_HOST}:{cfg.SERVER_PORT}{_RST}")
    print(f"  Model: {cfg.MODEL_NAME} | Context: {cfg.NUM_CTX} | Think: {cfg.THINK}")
    print(f"  History cap: {cfg.MAX_HISTORY_MESSAGES} msgs | Debug: {cfg.DEBUG_LOGGING}")

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
                import procedure_consolidator
                t1 = asyncio.create_task(run_consolidation())
                t2 = asyncio.create_task(run_procedure_consolidation())
                fact_consolidator._consolidation_task = t1
                procedure_consolidator._procedure_task = t2
                await asyncio.gather(t1, t2, return_exceptions=True)
                print(f"{_MAG}[CONSOLIDATOR]{_RST} Consolidation pass completed — triggering automatic memory refresh.", flush=True)
                await start_refresh_memory_internal()

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
            
            # Check research active-hours window. Topic generation, auto-resume,
            # and queue starts are all gated here. An already-executing step is
            # never hard-killed by the window — it runs to its natural step
            # boundary, after which the loop simply will not start the next one.
            research_window_open = _in_research_window()
            
            # 1. Topic generation
            global _last_self_initiate_ts
            if (
                research_window_open
                and getattr(cfg, "RESEARCH_SELF_INITIATE", True)
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
            
            # Sync any new task folders on disk into _background_tasks
            if os.path.exists(cfg.RESEARCH_DATA_DIR):
                for d in os.listdir(cfg.RESEARCH_DATA_DIR):
                    if d.startswith("task_") and d not in _background_tasks:
                        disk_s = load_state(d)
                        if disk_s:
                            _background_tasks[d] = {
                                "status": disk_s.get("status", "pending"),
                                "query": disk_s.get("query", ""),
                                "scope": disk_s.get("scope", "standard"),
                                "started_at": time.time()
                            }

            unfinished_tasks = []
            active_task = None
            
            for tid, task in list(_background_tasks.items()):
                if tid.startswith("task_"):
                    # Check disk state as well to stay perfectly in sync
                    disk_state = load_state(tid)
                    status = disk_state.get("status") if disk_state else task.get("status")
                    if status:
                        # Sync memory status back to prevent drift and release locks immediately
                        _background_tasks[tid]["status"] = status
                        if status in ("done", "error", "cancelled", "needs_guidance", "paused"):
                            if "finished_at" not in _background_tasks[tid] or not _background_tasks[tid].get("finished_at"):
                                _background_tasks[tid]["finished_at"] = time.time()

                    if status in ("running", "paused", "error", "searching", "synthesizing", "pending", "needs_guidance"):
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

            # 3. Handle active task pausing if user becomes active
            if active_task:
                tid = active_task["task_id"]
                state = load_state(tid)
                disk_status = state.get("status") if state else None
                
                # If finished or changed out-of-band on disk, sync it to memory
                if disk_status and disk_status not in ("running", "searching", "synthesizing"):
                    prev_status = _background_tasks.get(tid, {}).get("status")
                    print(f"[RESEARCH SYNC] Task {tid} completed or changed status on disk to '{disk_status}' — updating server memory.", flush=True)
                    _background_tasks[tid]["status"] = disk_status
                    if disk_status in ("done", "error", "cancelled"):
                        _background_tasks[tid]["finished_at"] = time.time()
                    if disk_status == "done" and prev_status in ("running", "searching", "synthesizing"):
                        print(f"[RESEARCH REFRESH] Research task {tid} finished — triggering automatic memory refresh.", flush=True)
                        await start_refresh_memory_internal()
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
                if not research_window_open:
                    # Outside active hours — log at most once per hour to avoid spamming
                    global _last_window_warn_ts
                    if time.time() - _last_window_warn_ts >= 3600:
                        start_h = getattr(cfg, "RESEARCH_ACTIVE_HOURS_START", 6)
                        end_h   = getattr(cfg, "RESEARCH_ACTIVE_HOURS_END", 21)
                        print(
                            f"[RESEARCH WINDOW] Outside active hours ({start_h:02d}:00–{end_h:02d}:00) "
                            f"— {len(unfinished_tasks)} task(s) waiting, will resume at {start_h:02d}:00.",
                            flush=True,
                        )
                        _last_window_warn_ts = time.time()
                    await asyncio.sleep(300)  # Check again in 5 min
                    continue

                # Layer 2: Spawn debounce — 60s quiet period after any recent spawn
                # prevents the 10s loop from firing twice before the first subprocess registers.
                global _last_research_spawn_ts
                if time.time() - _last_research_spawn_ts < 60:
                    continue

                if idle_seconds >= 300:  # Server idle for 5 min
                    # Sort unfinished tasks by created_at ascending (oldest gets priority)
                    unfinished_tasks.sort(key=lambda x: x.get("created_at") or "")
                    target_task = unfinished_tasks[0]

                    # Layer 3: Error cooldown — crashed tasks must wait 10 minutes before
                    # being auto-resumed to prevent cascade relaunches.
                    global _error_resume_ts
                    if target_task["status"] == "error":
                        last_attempt = _error_resume_ts.get(target_task["task_id"], 0)
                        if time.time() - last_attempt < 600:
                            continue  # Cooldown active — skip silently
                        _error_resume_ts[target_task["task_id"]] = time.time()

                    print(f"[RESEARCH AUTO-RECOVERY] Server idle for {idle_seconds:.1f}s — auto-resuming unfinished task {target_task['task_id']} (status: {target_task['status']})", flush=True)
                    from evelyn_tools import resume_research_task
                    resume_research_task(target_task['task_id'])
                    _last_research_spawn_ts = time.time()  # Record spawn timestamp
                    # Wait for subprocess thread to spin up and register
                    await asyncio.sleep(20)
                continue

            # 5. Process queued tasks
            if research_window_open and idle_seconds >= getattr(cfg, "RESEARCH_IDLE_THRESHOLD", 1800):
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
                        start_research(
                            next_task["query"],
                            scope=next_task.get("scope", "standard"),
                            triggered_by=next_task.get("source", "evelyn"),
                            intent_frame=next_task.get("intent_frame"),
                        )
                        _last_research_spawn_ts = time.time()  # Record spawn timestamp
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

    # Idle-time profile evolution loop (Hermes Tier 3 #12)
    # Wakes every 10 minutes to check idle state. The per-document 24-hour
    # cooldown is enforced inside run_profile_evolution(), not here.
    async def _idle_profile_evolution_loop():
        """Background loop that proposes persona file updates during deep idle."""
        while True:
            await asyncio.sleep(600)  # Check every 10 minutes
            importlib.reload(cfg)
            if not getattr(cfg, "PROFILE_EVOLUTION_ENABLED", False):
                continue
            idle_seconds = time.time() - _last_activity_ts
            threshold = getattr(cfg, "PROFILE_EVOLUTION_IDLE_THRESHOLD", 3600)
            if idle_seconds >= threshold:
                if not is_any_heavy_task_running():
                    print(f"{_GRN}[PROFILE EVOLVER]{_RST} Server idle for {idle_seconds / 60:.1f}m — triggering background profile evolution check.", flush=True)
                    asyncio.create_task(run_profile_evolution())

    asyncio.create_task(_idle_profile_evolution_loop())
    print(f"  {_GRN}Profile Evolver:{_RST} idle loop started (threshold=60m, cooldown=24h/doc)")

    # Idle-time Tag Librarian loop
    async def run_tag_librarian_task():
        """Runs Tag Librarian audit pass for configured batch size in a background thread."""
        import task_manager
        if is_any_heavy_task_running():
            return
        task_manager.set_running("tag_librarian")
        try:
            from Evelyn.tools import tag_librarian
            batch_size = getattr(cfg, "TAG_LIBRARIAN_BATCH_SIZE", 1)
            for i in range(batch_size):
                if is_any_heavy_task_running("tag_librarian"):
                    break  # Yield if another heavy task started
                res = await asyncio.to_thread(tag_librarian.audit_single_document)
                print(f"{_GRN}[TAG LIBRARIAN]{_RST} Audit pass {i+1}/{batch_size} result: {res}", flush=True)
                if res.get("status") in ("empty", "error"):
                    break

            # Periodically maintain master taxonomy to purge zero-usage orphan tags
            m_res = await asyncio.to_thread(tag_librarian.maintain_master_taxonomy)
            if m_res.get("removed_master_tags", 0) > 0:
                print(f"{_GRN}[TAG LIBRARIAN]{_RST} Taxonomy maintenance pruned {m_res['removed_master_tags']} orphan tags.", flush=True)
        except Exception as e:
            print(f"[TAG LIBRARIAN] Error during audit pass: {e}", flush=True)
            task_manager.clear_running("tag_librarian", status="error", error=str(e))
        finally:
            if task_manager.get_status("tag_librarian") == "running":
                task_manager.clear_running("tag_librarian", status="idle")


    async def _idle_tag_librarian_loop():
        """Background loop that triggers incremental tag auditing during idle periods."""
        while True:
            await asyncio.sleep(600)  # Check every 10 minutes
            importlib.reload(cfg)
            if not getattr(cfg, "TAG_LIBRARIAN_ENABLED", False):
                continue
            idle_seconds = time.time() - _last_activity_ts
            threshold = getattr(cfg, "TAG_LIBRARIAN_IDLE_THRESHOLD", 2700)
            if idle_seconds >= threshold:
                if not is_any_heavy_task_running():
                    print(f"{_GRN}[TAG LIBRARIAN]{_RST} Server idle for {idle_seconds / 60:.1f}m — triggering background tag librarian audit.", flush=True)
                    asyncio.create_task(run_tag_librarian_task())

    asyncio.create_task(_idle_tag_librarian_loop())
    print(f"  {_GRN}Tag Librarian:{_RST} idle loop started (threshold=45m, limit=1 doc/run)")


    # Periodic Google Calendar auto-sync loop (Hermes Tier 2 #7)
    async def _gcal_sync_loop():
        """Periodic background task that pulls events from Google Calendar and caches them.
        Runs on startup and then every 30 minutes.
        """
        await asyncio.sleep(10)  # Brief warm-up delay on startup
        while True:
            try:
                import gcal_sync
                result = await asyncio.to_thread(gcal_sync.sync_gcal_events)
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

    # Periodic Google Drive & Health Connect auto-sync loop
    async def _gdrive_sync_loop():
        """Periodic background task that checks Google Drive for Health Connect exports and syncs the DB.
        Runs on startup and then every 2 hours.
        """
        await asyncio.sleep(15)  # Brief warm-up delay on startup
        while True:
            try:
                import gdrive_sync
                result = await asyncio.to_thread(gdrive_sync.sync_health_connect_from_drive)
                if result.get("status") == "success":
                    action = result.get("action", "")
                    if action == "downloaded":
                        print(f"{_GRN}[GDRIVE SYNC]{_RST} {result['message']}", flush=True)
                    elif cfg.DEBUG_LOGGING:
                        print(f"{_GRN}[GDRIVE SYNC]{_RST} {result['message']}", flush=True)
                elif cfg.DEBUG_LOGGING:
                    print(f"{_YEL}[GDRIVE SYNC]{_RST} {result.get('message')}", flush=True)
            except Exception as e:
                print(f"{_RED}[GDRIVE SYNC ERROR]{_RST} {e}", flush=True)

            # Check every 2 hours (7200s)
            await asyncio.sleep(7200)

    asyncio.create_task(_gdrive_sync_loop())
    print(f"  {_GRN}GDrive Syncer:{_RST} periodic loop started (interval=2h)")

    yield
    # Shutdown phase: pause all active research and cancel background tasks cleanly
    clean_shutdown_all_tasks()



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
        "think_tool_loop": cfg.THINK_TOOL_LOOP,
        "think_self_elect": getattr(cfg, "THINK_SELF_ELECT", True),
        "debug": cfg.DEBUG_LOGGING,
        "num_ctx": cfg.NUM_CTX,
    }


@app.post("/chat")
async def chat(req: ChatRequest, request: Request, _: None = Depends(check_auth)):
    """Accept a user message and return a Server-Sent Events stream of the response.

    Args:
        req: The chat request object containing the user message.
        request: FastAPI Request object for disconnect detection.
        _: Authentication dependency placeholder.

    Returns:
        StreamingResponse: An SSE stream of the assistant's response.
    """
    ui_override = req.think is not None
    think_effort = req.think if ui_override else classify_message_effort(req.message)
    return StreamingResponse(
        chat_stream(req.message, think_effort=think_effort, ui_override=ui_override, request=request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/regenerate")
async def regenerate(request: Request, _: None = Depends(check_auth)):
    """Delete the last assistant message and re-generate a response."""
    user_message = delete_last_assistant_message()
    if not user_message:
        raise HTTPException(
            status_code=400, detail="No user message to regenerate from."
        )
    think_effort = classify_message_effort(user_message)
    return StreamingResponse(
        chat_stream(user_message, is_regenerate=True, think_effort=think_effort, request=request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/edit")
async def edit_message(req: EditRequest, request: Request, _: None = Depends(check_auth)):
    """Update the content of the last user message and re-generate a response."""
    user_message = edit_last_user_message(req.message)
    if not user_message:
        raise HTTPException(
            status_code=400, detail="No user message to edit."
        )
    think_effort = classify_message_effort(user_message)
    return StreamingResponse(
        chat_stream(user_message, is_regenerate=True, think_effort=think_effort, request=request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/chat/stream/{stream_id}")
async def get_chat_stream(stream_id: str, request: Request, after: int = -1, _: None = Depends(check_auth)):
    """Attach to an active or recently completed stream session and replay missed chunks."""
    session = stream_registry.get(stream_id)
    if not session:
        raise HTTPException(status_code=404, detail="Stream session not found or expired")
    return StreamingResponse(
        stream_session_events(session, after=after, request=request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/chat/active_stream")
async def get_active_stream(_: None = Depends(check_auth)):
    """Return the currently active stream session info, if any."""
    session = stream_registry.get_active()
    if session:
        return {
            "active": True,
            "stream_id": session.stream_id,
            "status": session.status,
            "chunks_count": len(session.chunks),
            "created_at": session.created_at,
        }
    return {"active": False}


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
    print(f"{_MAG}[THREAD]{_RST} New thread started", flush=True)
    return {"status": "new thread started"}


# background task tracking variables now located at the top of App setup


def _load_existing_research_tasks():
    """Scan the research data directory and register any paused, errored, or interrupted tasks.

    Layer 4: Also checks engine.pid files to detect tasks that were genuinely
    still running when the server was killed, rather than downgrading all
    in-flight tasks to 'paused' unconditionally.
    """
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

                                # Layer 4: If status was 'running', check engine.pid to
                                # distinguish a genuine orphan from a server restart.
                                if status == "running":
                                    from Evelyn.tools.evelyn_tools import _is_research_engine_running
                                    still_alive = _is_research_engine_running(d)
                                    if still_alive:
                                        print(
                                            f"[RESEARCH RECOVERY] Task {d} has a live process — "
                                            f"marking as running (orphan subprocess detected).",
                                            flush=True,
                                        )

                                    if not still_alive:
                                        # Server restarted without the subprocess alive
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
    import task_manager
    from evelyn_tools import TOOL_FUNCTIONS

    # Free Ollama before a heavy background operation starts
    cancel_pending_consolidation()
    cancel_pending_extraction()
    cancel_pending_evolution()

    task_manager.set_running("sync", phase="Syncing Chroma DB...")

    def _run():
        """Run sync_context_memory in a daemon thread and update the task registry."""
        try:
            print(f"{_GRN}[SYNC]{_RST} Manual sync triggered via /sync endpoint", flush=True)
            TOOL_FUNCTIONS["sync_context_memory"]()
            task_manager.clear_running("sync", status="done")
            print(f"{_GRN}[SYNC]{_RST} Complete.", flush=True)
        except Exception as e:
            task_manager.clear_running("sync", status="error", error=str(e))
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
    import task_manager

    # Free Ollama before a heavy background operation starts
    cancel_pending_consolidation()
    cancel_pending_extraction()
    cancel_pending_evolution()

    task_manager.set_running("vault_map", phase="Mapping Obsidian Vault...")

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
                task_manager.clear_running("vault_map", status="done")
                print(f"{_GRN}[VAULT MAP]{_RST} Done.", flush=True)
            else:
                task_manager.clear_running("vault_map", status="error", error=f"Exit code {result.returncode}")
                print(f"{_RED}[VAULT MAP ERROR]{_RST} Process exited with code {result.returncode}", flush=True)
        except Exception as e:
            task_manager.clear_running("vault_map", status="error", error=str(e))
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
    cancel_pending_extraction(reason="memory_refresh")
    cancel_pending_evolution()

    import task_manager
    task_manager.set_running("refresh_memory", phase="Starting...")

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
                    phase_label = _REFRESH_PHASE_LABELS.get(key, f"Running {key}...")
                    task_manager.set_running("refresh_memory", phase=phase_label)
                    if key == "vault_map":
                        task_manager.set_running("vault_map", phase="Mapping Obsidian Vault...")
                    elif key in ("ingest_knowledge", "ingest_gists"):
                        task_manager.set_running("sync", phase="Syncing Chroma DB...")

                elif line.startswith("[PHASE_DONE:"):
                    key = line.split("[PHASE_DONE:")[1].split("]")[0]
                    if key == "vault_map":
                        task_manager.clear_running("vault_map", status="done")
                    elif key == "ingest_gists":
                        task_manager.clear_running("sync", status="done")

                elif line.startswith("[PHASE_FAIL:"):
                    key = line.split("[PHASE_FAIL:")[1].split("]")[0]
                    if key == "vault_map":
                        task_manager.clear_running("vault_map", status="error", error=f"Phase '{key}' failed.")
                    elif key in ("ingest_knowledge", "ingest_gists"):
                        task_manager.clear_running("sync", status="error", error=f"Phase '{key}' failed.")
                    raise RuntimeError(f"Phase '{key}' failed.")

            await proc.wait()

            if proc.returncode == 0:
                task_manager.clear_running("refresh_memory", status="done")
                task_manager.clear_running("vault_map", status="done")
                task_manager.clear_running("sync", status="done")
                if "refresh_memory" in _background_tasks:
                    _background_tasks["refresh_memory"]["phase"] = "Completed successfully."
                print(f"{_GRN}[REFRESH]{_RST} All phases done.", flush=True)
            else:
                raise RuntimeError(f"Pipeline exited with code {proc.returncode}")

        except Exception as e:
            task_manager.clear_running("refresh_memory", status="error", error=str(e))
            if "refresh_memory" in _background_tasks:
                _background_tasks["refresh_memory"]["phase"] = "Failed."
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
    intent_frame: Optional[str] = None


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
    result = start_research(
        req.query,
        scope=req.scope,
        triggered_by="user",
        intent_frame=req.intent_frame or None,
        bypass_queue=True,
    )
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


@app.post("/shutdown")
async def api_shutdown_server(_: None = Depends(check_auth)):
    """Cleanly pause all tasks and release lock files during service shutdown."""
    clean_shutdown_all_tasks()
    return {"status": "shutting_down"}


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
    """Pydantic model representing a request to rewrite a sub-question or its search query."""
    sq_id: str
    new_question: Optional[str] = None
    new_search_query: Optional[str] = None

class SQRemoveRequest(BaseModel):
    """Pydantic model representing a request to remove a sub-question from a research task."""
    sq_id: str

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
        dict: Status message confirming guidance injection and resumption.
    """
    _demote_running_task_if_any(task_id)
    from evelyn_tools import guide_research
    result = guide_research(task_id, request.guidance)
    return {"message": result}

@app.post("/research/guide/{task_id}/finalize")
async def api_guide_research_finalize(task_id: str, _: None = Depends(check_auth)):
    """Finalize manual guidance edits and queue the task in waiting state.

    Args:
        task_id: The ID of the task.
        _: Authorization dependency.

    Returns:
        dict: Status message confirming finalization and queuing.
    """
    from evelyn_tools import finalize_guidance
    result = finalize_guidance(task_id)
    return {"message": result}

@app.post("/research/guide/{task_id}/remove")
async def api_remove_sub_question(task_id: str, request: SQRemoveRequest, _: None = Depends(check_auth)):
    """Remove a sub-question from the research plan and delete any partial notes for it.

    Args:
        task_id: The ID of the task.
        request: Request containing the sub-question ID to remove.
        _: Authorization dependency.

    Returns:
        dict: Status message confirming removal.
    """
    from evelyn_tools import remove_sub_question
    result = remove_sub_question(task_id, request.sq_id)
    if result.startswith("Failed") or "not found" in result:
        raise HTTPException(status_code=400, detail=result)
    return {"message": result}

@app.post("/research/guide/{task_id}/rewrite")
async def api_guide_research_rewrite(task_id: str, request: SQRewriteRequest, _: None = Depends(check_auth)):
    """Submit a single sub-question rewrite (does not resume the task).

    Args:
        task_id: The ID of the task.
        request: Request containing the sub-question ID and new text or search query.
        _: Authorization dependency.

    Returns:
        dict: Status message confirming the rewrite.
    """
    from evelyn_tools import rewrite_sub_question
    result = rewrite_sub_question(
        task_id,
        request.sq_id,
        new_question=request.new_question,
        new_search_query=request.new_search_query
    )
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


@app.post("/tts/stream")
async def tts_stream_proxy(request: Request):
    """Proxy streaming TTS SSE from the TTS server to the client.

    Forwards sentence-chunk events emitted by tts_server as they are produced,
    allowing the client to begin playback before the full response is synthesized.
    Keeps the TTS server local-only while allowing Tailscale/mobile clients
    to reach it through evelyn_server (which is already on 0.0.0.0).
    """
    body = await request.body()

    async def _forward():
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                async with client.stream(
                    "POST",
                    f"{cfg.TTS_SERVER_URL}/v1/audio/speech/stream",
                    content=body,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if line.strip():
                            yield line + "\n\n"
        except httpx.ConnectError:
            yield 'data: {"error": "TTS server is not running"}\n\n'
        except Exception as e:
            yield f'data: {{"error": "{e}"}}\n\n'

    return StreamingResponse(
        _forward(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/tts-audio/{filename}")
async def tts_audio_proxy(filename: str):
    """Proxy individual TTS chunk WAV files from the TTS server.

    Allows Tailscale/mobile clients to fetch chunk files through evelyn_server
    without direct access to the TTS server's localhost port.
    """
    from fastapi.responses import Response
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(f"{cfg.TTS_SERVER_URL}/tts-audio/{filename}")
            resp.raise_for_status()
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail="TTS server is not running")
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=str(e))
    return Response(content=resp.content, media_type="audio/wav")


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

@app.get("/api/heavy_tasks")
async def get_heavy_tasks(_: None = Depends(check_auth)):
    """Return real-time status of all heavy background tasks and mutual-exclusion lock state."""
    import Evelyn.tools.task_manager as task_manager
    from Evelyn.tools.research_engine import load_state
    now = time.time()
    is_any_running = task_manager.is_any_running()

    known_keys = [
        ("extractor", "Fact Extractor"),
        ("consolidator", "Fact Consolidator"),
        ("procedure_consolidator", "Procedure Consolidator"),
        ("profile_evolver", "Profile Evolver"),
        ("tag_librarian", "Tag Librarian"),
        ("refresh_memory", "Memory Refresh"),
        ("sync", "Chroma Sync"),
        ("vault_map", "Vault Map Generator"),
    ]

    tasks_info = []
    active_lock_holder = None

    for key, display_name in known_keys:
        task_data = _background_tasks.get(key, {})
        status = task_data.get("status", "idle")
        started_at = task_data.get("started_at")
        finished_at = task_data.get("finished_at")
        last_run_at = task_data.get("last_run_at") or finished_at or started_at
        
        elapsed = None
        if status == "running" and started_at:
            elapsed = round(now - started_at, 1)
            active_lock_holder = display_name
        elif finished_at and started_at:
            elapsed = round(finished_at - started_at, 1)
        elif task_data.get("elapsed_seconds") is not None:
            elapsed = task_data.get("elapsed_seconds")

        runtime_mins = round(elapsed / 60.0, 1) if elapsed is not None else 0.0

        doc_statuses = None
        if key == "profile_evolver":
            try:
                from Evelyn.tools.profile_evolver import get_profile_evolution_statuses
                doc_statuses = get_profile_evolution_statuses()
            except Exception as e:
                pass

        sub_status = task_data.get("sub_status")
        summary = task_data.get("summary")
        diagnostics = task_data.get("diagnostics")

        # Dynamic diagnostic enrichment if sub_status wasn't explicitly populated
        try:
            if key == "extractor":
                import json, os, sqlite3
                state_path = str(BASE_DIR / "data" / "evelyn_extraction_state.json")
                last_id = 0
                if os.path.exists(state_path):
                    with open(state_path, "r", encoding="utf-8") as sf:
                        st = json.load(sf)
                        last_id = st.get("last_extracted_id", 0)
                db_path = str(BASE_DIR / "data" / "evelyn_chat.db")
                backlog = 0
                if os.path.exists(db_path):
                    conn = sqlite3.connect(db_path, timeout=1.0)
                    try:
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(*) FROM messages WHERE id > ?", (last_id,))
                        backlog = cur.fetchone()[0]
                    finally:
                        conn.close()
                sub_status = sub_status or {
                    "last_extracted_id": last_id,
                    "unextracted_backlog": backlog
                }
            elif key == "consolidator":
                import json, os
                scan_path = str(BASE_DIR / "data" / "evelyn_consolidation_offsets.json")
                scan_st = {}
                if os.path.exists(scan_path):
                    with open(scan_path, "r", encoding="utf-8") as sf:
                        scan_st = json.load(sf)
                active_cat = task_data.get("phase") if status == "running" else None
                if not sub_status:
                    sub_status = {
                        "scan_state": scan_st,
                        "active_category": active_cat
                    }
                else:
                    if "scan_state" not in sub_status or not sub_status["scan_state"]:
                        sub_status["scan_state"] = scan_st
                    if "active_category" not in sub_status:
                        sub_status["active_category"] = active_cat
            elif key == "procedure_consolidator":
                import sqlite3, os
                mdb = str(BASE_DIR / "data" / "evelyn_memory.db")
                proc_cnt = 0
                pending_proposals = 0
                if os.path.exists(mdb):
                    conn = sqlite3.connect(mdb, timeout=1.0)
                    try:
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(*) FROM procedures WHERE status='live'")
                        proc_cnt = cur.fetchone()[0]
                        cur.execute("SELECT COUNT(*) FROM memory_proposals WHERE type='procedure_merge' AND status='pending'")
                        pending_proposals = cur.fetchone()[0]
                    except Exception:
                        pass
                    finally:
                        conn.close()
                if not sub_status:
                    sub_status = {
                        "total_procedures": proc_cnt,
                        "pending_proposals": pending_proposals,
                        "clusters_found": 0,
                    }
                else:
                    sub_status.setdefault("total_procedures", proc_cnt)
                    sub_status.setdefault("pending_proposals", pending_proposals)
            elif key == "tag_librarian":
                import sqlite3, os
                vdb = str(BASE_DIR / "data" / "evelyn_vault.db")
                audited = 0
                total = 0
                tags_cnt = 0
                if os.path.exists(vdb):
                    conn = sqlite3.connect(vdb, timeout=1.0)
                    try:
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(*) FROM master_tag_taxonomy")
                        tags_cnt = cur.fetchone()[0]
                        cur.execute("SELECT COUNT(*) FROM vault_documents WHERE last_tag_audit IS NOT NULL")
                        audited = cur.fetchone()[0]
                        cur.execute("SELECT COUNT(*) FROM vault_documents")
                        total = cur.fetchone()[0]
                    except Exception:
                        pass
                    finally:
                        conn.close()
                sub_status = sub_status or {
                    "master_tags": tags_cnt,
                    "audited_notes": audited,
                    "total_notes": total
                }
            elif key == "sync":
                import sqlite3, os
                mdb = str(BASE_DIR / "data" / "evelyn_memory.db")
                facts_cnt = 0
                procs_cnt = 0
                if os.path.exists(mdb):
                    conn = sqlite3.connect(mdb, timeout=1.0)
                    try:
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(*) FROM context_entries WHERE status='live'")
                        facts_cnt = cur.fetchone()[0]
                        cur.execute("SELECT COUNT(*) FROM procedures")
                        procs_cnt = cur.fetchone()[0]
                    except Exception:
                        pass
                    finally:
                        conn.close()
                chroma_cnt = 0
                try:
                    import chromadb, evelyn_config
                    client = chromadb.PersistentClient(path=evelyn_config.CHROMA_DB_PATH)
                    for col in client.list_collections():
                        if col.name in ("evelyn_memory", "evelyn_gists", "obsidian_vault"):
                            chroma_cnt += col.count()
                except Exception:
                    pass
                sub_status = sub_status or {
                    "context_facts": facts_cnt,
                    "system_procedures": procs_cnt,
                    "chroma_vectors": chroma_cnt,
                }
            elif key == "vault_map":
                map_file = str(BASE_DIR / "reference" / "engine_architecture.md")
                exists = os.path.exists(map_file)
                mtime = os.path.getmtime(map_file) if exists else None
                sub_status = sub_status or {
                    "ref_doc": "reference/engine_architecture.md",
                    "target": "engine_architecture.md",
                    "file_exists": exists,
                    "last_modified": mtime
                }
            elif key == "refresh_memory":
                phase = task_data.get("phase", "Idle")
                current_step = 1
                if "Phase 2" in phase:
                    current_step = 2
                elif "Phase 3" in phase:
                    current_step = 3
                elif phase == "Completed successfully.":
                    current_step = 3
                sub_status = sub_status or {
                    "total_steps": 3,
                    "current_step": current_step,
                    "steps": ["Vault Map", "Knowledge Ingest", "Gist Ingest"]
                }
        except Exception as e:
            pass

        tasks_info.append({
            "key": key,
            "name": display_name,
            "status": status,
            "phase": task_data.get("phase"),
            "started_at": started_at,
            "finished_at": finished_at,
            "last_run_at": last_run_at,
            "elapsed_seconds": elapsed,
            "runtime_minutes": runtime_mins,
            "error": task_data.get("error"),
            "summary": summary,
            "sub_status": sub_status,
            "diagnostics": diagnostics,
            "doc_statuses": doc_statuses,
        })

    research_tasks_info = []
    for key, task_data in _background_tasks.items():
        if key.startswith("task_"):
            status = task_data.get("status", "idle")
            started_at = task_data.get("started_at")
            finished_at = task_data.get("finished_at")
            
            disk_state = load_state(key) or {}
            accum_sec = disk_state.get("accumulated_runtime", 0.0)
            
            if status in ("running", "searching", "synthesizing") and started_at:
                session_sec = now - started_at
                actual_sec = accum_sec + session_sec
                if not active_lock_holder:
                    active_lock_holder = f"Research: {task_data.get('query', key)}"
            else:
                actual_sec = accum_sec

            runtime_mins = round(actual_sec / 60.0, 1)
            last_run_at = finished_at or started_at

            research_tasks_info.append({
                "key": key,
                "name": f"Research: {task_data.get('query', key)}",
                "query": task_data.get("query", ""),
                "scope": task_data.get("scope", "standard"),
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "last_run_at": last_run_at,
                "elapsed_seconds": round(actual_sec, 1),
                "runtime_minutes": runtime_mins,
            })

    return {
        "is_any_running": is_any_running,
        "active_lock_holder": active_lock_holder,
        "tasks": tasks_info,
        "research_tasks": research_tasks_info,
    }


@app.get("/api/review/unified")
async def get_unified_review(_: None = Depends(check_auth)):
    """Return all pending review items (extractions, proposals, profile updates, procedures)
    in a single unified list with item_type metadata.
    """
    import Evelyn.tools.memory_db as memory_db
    unified_items = []

    # 1. Extractions
    raw_extractions = memory_db.get_all_entries(statuses=["extracted"])
    for item in raw_extractions:
        item["item_type"] = "extraction"
        unified_items.append(item)

    # 2. Proposals
    proposals = memory_db.get_pending_proposals()
    for p in proposals:
        source_entries = []
        for eid in p.get("source_ids", []):
            if p.get("type") == "procedure_merge":
                proc = memory_db.get_procedure(eid)
                if proc:
                    source_entries.append({
                        "category": "procedure",
                        "observation": f"[{proc['trigger_pattern']}] {proc['steps'][:120]}..."
                    })
            else:
                entry = memory_db.get_entry(eid)
                if entry:
                    source_entries.append(entry)
        p["source_entries"] = source_entries

        if p.get("type") == "profile_update":
            p["item_type"] = "profile_update"
        else:
            p["item_type"] = "proposal"
        unified_items.append(p)

    # 3. Procedures
    procedures = memory_db.get_all_procedures(status="extracted")
    for proc in procedures:
        proc["item_type"] = "procedure"
        unified_items.append(proc)

    return unified_items


class EditEntryRequest(BaseModel):
    """Pydantic model representing a request to edit a memory entry."""
    category: str | None = None
    subject: str | None = None
    observation: str | None = None
    tags: str | None = None

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
        req:    Required for "edit" — carries updated fields.
    """
    import Evelyn.tools.memory_db as memory_db
    if action == "approve":
        memory_db.update_entry(id, status="live")
        await start_refresh_memory_internal()
    elif action == "delete":
        memory_db.delete_entry(id)
        memory_db.remove_source_id_from_pending_proposals(id)
    elif action == "edit" and req:
        fields = {}
        if req.category is not None:
            fields["category"] = req.category
        if req.subject is not None:
            fields["subject"] = req.subject
        if req.observation is not None:
            fields["observation"] = req.observation
        if req.tags is not None:
            fields["tags"] = req.tags
        # No-op if caller sent an empty body — don't raise a 400,
        # the entry already exists and nothing needs changing.
        if fields:
            # Only promote status to live if the entry is currently extracted.
            # Editing an already-live entry (e.g. a profile_update source entry)
            # should not touch its status.
            entry = memory_db.get_entry(id)
            if entry and entry.get("status") == "extracted":
                fields["status"] = "live"
            memory_db.update_entry(id, **fields)
            await start_refresh_memory_internal()
    elif action == "edit" and not req:
        raise HTTPException(status_code=400, detail="Edit action requires a request body")
    else:
        raise HTTPException(status_code=400, detail="Invalid action")
    return {"status": "ok"}

@app.get("/api/persona/{filename}")
async def get_persona_file(filename: str, _: None = Depends(check_auth)):
    """Read a persona file's current content for diff display."""
    safe_names = {"Evelyn_Narrative_Persona.md", "Ricky_Narrative_Profile.md", "System_Directives.md"}
    if filename not in safe_names:
        raise HTTPException(status_code=400, detail="Invalid filename")
    fpath = PERSONA_DIR / filename
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return {"filename": filename, "content": fpath.read_text(encoding="utf-8")}


class ProposalActionRequest(BaseModel):
    """Pydantic model representing optional parameters when acting on a proposal."""
    modified_text: str | None = None
    source_id: int | None = None


@app.get("/api/review/proposals")
async def get_proposals(_: None = Depends(check_auth)):
    """Return all pending consolidation/recategorization proposals with their source entries."""
    import Evelyn.tools.memory_db as memory_db
    proposals = memory_db.get_pending_proposals()
    for p in proposals:
        source_entries = []
        for eid in p.get("source_ids", []):
            if p.get("type") == "procedure_merge":
                proc = memory_db.get_procedure(eid)
                if proc:
                    source_entries.append({
                        "category": "procedure",
                        "observation": f"[{proc['trigger_pattern']}] {proc['steps'][:120]}..."
                    })
            else:
                entry = memory_db.get_entry(eid)
                if entry:
                    source_entries.append(entry)
        p["source_entries"] = source_entries
    return proposals

@app.post("/api/review/proposals/{id}/{action}")
async def action_proposal(id: int, action: str, req: ProposalActionRequest = None, _: None = Depends(check_auth)):
    """Approve, deny, or unlink source context entries on a proposal.

    Args:
        id:     Proposal row ID.
        action: "approve" | "deny" | "unlink_source".
        req:    Optional JSON body containing modified_text or source_id.
    """
    import Evelyn.tools.memory_db as memory_db
    if action == "deny":
        memory_db.reject_proposal(id)
        return {"status": "ok"}
    elif action == "unlink_source":
        if not req or req.source_id is None:
            raise HTTPException(status_code=400, detail="unlink_source requires source_id in request body")
        memory_db.remove_proposal_source_id(id, req.source_id)
        return {"status": "ok"}
    elif action == "approve":
        proposals = memory_db.get_pending_proposals()
        prop = next((p for p in proposals if p["id"] == id), None)
        if not prop:
            raise HTTPException(status_code=404, detail="Proposal not found")
            
        final_text = req.modified_text if (req and req.modified_text is not None) else prop["merged_observation"]

        source_entries = []
        for eid in prop.get("source_ids", []):
            entry = memory_db.get_entry(eid)
            if entry:
                source_entries.append(entry)
        
        if prop["type"] == "recategorize":
            # final_text intentionally unused here — recategorize only moves entries,
            # it does not write a merged document.
            for entry in source_entries:
                memory_db.update_entry(entry["id"], category=prop["suggested_category"])
            memory_db.apply_proposal(id)
        elif prop["type"] == "profile_update":
            # Repurposed suggested_category contains the target filename (e.g. Evelyn_Narrative_Persona.md)
            target_file = PERSONA_DIR / prop["suggested_category"]
            if not target_file.exists():
                raise HTTPException(status_code=404, detail=f"Target file not found: {prop['suggested_category']}")
            target_file.write_text(final_text, encoding="utf-8")
            memory_db.update_proposal(id, merged_observation=final_text)
            memory_db.apply_proposal(id)
            # Stamp last_evolved_at on all source entries so they are not re-evaluated
            # until their observation content actually changes.
            now_ts = time.time()
            for eid in prop.get("source_ids", []):
                memory_db.touch_entry_evolved(eid, now_ts)
            # Reset the per-document cooldown from approval time, not proposal generation
            # time. Without this, the evolver's cooldown runs from when the proposal was
            # staged (potentially hours earlier), causing immediate re-evaluation overnight.
            advance_doc_run_timestamp(prop["suggested_category"])
            # Run update_frontmatter script to update date modified/tags
            import subprocess
            subprocess.run(
                [sys.executable, "scripts/update_frontmatter.py", str(target_file)],
                cwd=str(BASE_DIR), capture_output=True
            )
        elif prop["type"] == "procedure_merge":
            import yaml
            source_ids = prop.get("source_ids", [])
            for eid in source_ids:
                memory_db.delete_procedure(eid)
            try:
                parsed_proc = yaml.safe_load(final_text)
            except Exception:
                parsed_proc = {}
            if isinstance(parsed_proc, dict) and "trigger_pattern" in parsed_proc:
                memory_db.insert_procedure(
                    trigger_pattern=parsed_proc["trigger_pattern"],
                    steps=parsed_proc.get("steps", ""),
                    pitfalls=parsed_proc.get("pitfalls"),
                    verification=parsed_proc.get("verification"),
                    source="consolidated",
                    status="live",
                    tags=parsed_proc.get("tags")
                )
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
                observation=final_text,
                source="consolidated",
                date=date,
                tags=merged_tags
            )
            memory_db.apply_proposal(id)
        await start_refresh_memory_internal()
        return {"status": "ok"}
    else:
        raise HTTPException(status_code=400, detail="Invalid action")


class ProcedureReviewBody(BaseModel):
    trigger_pattern: str | None = None
    steps: str | None = None
    pitfalls: str | None = None
    verification: str | None = None
    tags: str | None = None


@app.get("/api/review/procedures")
async def get_procedures_review(_: None = Depends(check_auth)):
    """Return all pending extracted procedures for review."""
    import Evelyn.tools.memory_db as memory_db
    return memory_db.get_all_procedures(status="extracted")


@app.post("/api/review/procedures/{id}/{action}")
async def action_procedure(
    id: int,
    action: str,
    body: ProcedureReviewBody | None = None,
    _: None = Depends(check_auth)
):
    """Approve, edit and approve, or deny/archive an extracted procedure.

    Args:
        id:     Procedure row ID.
        action: "approve" | "deny".
        body:   Optional edits to the procedure trigger/steps/pitfalls/verification/tags.
    """
    import Evelyn.tools.memory_db as memory_db
    if action == "deny":
        memory_db.update_procedure(id, status="archived")
        return {"status": "ok"}
    elif action == "approve":
        update_fields = {}
        if body:
            if body.trigger_pattern is not None:
                update_fields["trigger_pattern"] = body.trigger_pattern
            if body.steps is not None:
                update_fields["steps"] = body.steps
            if body.pitfalls is not None:
                update_fields["pitfalls"] = body.pitfalls
            if body.verification is not None:
                update_fields["verification"] = body.verification
            if body.tags is not None:
                update_fields["tags"] = body.tags

        update_fields["status"] = "live"
        success = memory_db.update_procedure(id, **update_fields)
        if not success:
            raise HTTPException(status_code=404, detail="Procedure not found or not updated")
        return {"status": "ok"}
    else:
        raise HTTPException(status_code=400, detail="Invalid action")


# ---------------------------------------------------------------------------
# Terminal Agency Endpoints (Hermes Tier 3 #9)
# ---------------------------------------------------------------------------

@app.get("/api/terminal/pending")
async def get_pending_commands(_: None = Depends(check_auth)):
    """Return all commands/writes awaiting user approval."""
    import Evelyn.tools.terminal_agent as terminal_agent
    return terminal_agent.get_pending_approvals()


class ApprovalStatusRequest(BaseModel):
    ids: list[str]


@app.post("/api/terminal/status")
async def get_multiple_approvals_status(body: ApprovalStatusRequest, _: None = Depends(check_auth)):
    """Get the status of multiple approval IDs in bulk."""
    import Evelyn.tools.terminal_agent as terminal_agent
    return {
        approval_id: terminal_agent.get_approval_status(approval_id)
        for approval_id in body.ids
    }


@app.post("/api/terminal/approve/{approval_id}")
async def approve_terminal_command(approval_id: str, _: None = Depends(check_auth)):
    """Approve and execute a pending command or file write."""
    import Evelyn.tools.terminal_agent as terminal_agent
    result = terminal_agent.approve_command(approval_id)
    return {"status": "ok", "result": result}


@app.post("/api/terminal/deny/{approval_id}")
async def deny_terminal_command(approval_id: str, _: None = Depends(check_auth)):
    """Deny and delete a pending command or file write."""
    import Evelyn.tools.terminal_agent as terminal_agent
    terminal_agent.deny_command(approval_id)
    return {"status": "ok"}


if __name__ == "__main__":

    import uvicorn
    import os

    SSL_KEY = "sanctum.internal.net.key"
    SSL_CERT = "sanctum.internal.net.crt"
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
