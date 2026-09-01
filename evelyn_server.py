# evelyn_server.py
# date created: 2026-03-23 15:43:21
# date modified: 2026-09-01 17:40:45
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
import base64
import contextlib
import importlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psutil
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_server_background_tasks: set[asyncio.Task] = set()


def _server_sync_read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _server_sync_write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _server_sync_write_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _server_sync_load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _server_sync_dump_json(path: str, data: Any, indent: int = 2) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent)


# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
TOOLS_DIR = BASE_DIR / "Evelyn" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
PERSONA_DIR = BASE_DIR / "Evelyn" / "persona"

from chroma_rag import build_rag_context
from evelyn_tools import MODEL_TOOL_DEFINITIONS, TOOL_FUNCTIONS, TOOL_THINK_EFFORT
from fact_consolidator import cancel_pending_consolidation, run_consolidation
from fact_extractor import cancel_pending_extraction, run_extraction
from procedure_consolidator import (
    cancel_pending_procedure_consolidation,
    run_procedure_consolidation,
)
from profile_evolver import (
    advance_doc_run_timestamp,
    cancel_pending_evolution,
    run_profile_evolution,
)
from time_manager import TimeManager

import evelyn_config as cfg

time_manager = TimeManager()

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
_last_research_spawn_ts: float = 0.0  # Layer 2: spawn debounce
_error_resume_ts: dict = {}  # Layer 3: per-task error cooldown


def _get_current_idle_seconds() -> float:
    """Return elapsed seconds of silence since the last user message in evelyn_chat.db.

    Fallback to in-memory _last_activity_ts if DB query fails.
    """
    try:
        from Evelyn.tools import time_manager

        return time_manager.get_user_idle_seconds()
    except (ImportError, sqlite3.Error, OSError, ValueError):
        return max(0.0, time.time() - _last_activity_ts)

# ---------------------------------------------------------------------------
# In-Memory Stream Buffer & Session Management
# ---------------------------------------------------------------------------


class ActiveStreamSession:
    """Buffer and notification manager for a single active chat generation turn."""

    def __init__(self, stream_id: str):
        self.stream_id: str = stream_id
        self.chunks: list[dict] = []  # [{"id": int, "event": str}]
        self.status: str = "running"  # "running", "completed", "stopped", "error"
        self.event_notify: asyncio.Event = asyncio.Event()
        self.created_at: float = time.time()
        self.completed_at: float | None = None
        self.error_msg: str | None = None
        self.task: asyncio.Task | None = None
        self.is_cancelled: bool = False

    def push_chunk(self, raw_event_str: str):
        """Append an event chunk and wake all awaiting listeners without race conditions."""
        chunk_id = len(self.chunks)
        self.chunks.append({"id": chunk_id, "event": raw_event_str})
        old_event = self.event_notify
        self.event_notify = asyncio.Event()
        old_event.set()

    def mark_complete(self, error: str | None = None, status: str | None = None):
        """Mark stream complete, error, or stopped and wake all listeners."""
        if status:
            self.status = status
        elif error:
            self.status = "error"
            self.error_msg = error
        elif self.status != "stopped":
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
            sid
            for sid, s in self.sessions.items()
            if s.completed_at and (now - s.completed_at > ttl_seconds)
        ]
        for sid in expired:
            del self.sessions[sid]
        if self.active_stream_id in expired:
            self.active_stream_id = None


stream_registry = StreamRegistry()


async def stream_session_events(
    session: ActiveStreamSession, after: int = -1, request: Request | None = None
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
            if session.status in ("completed", "error", "stopped") and cursor >= len(
                session.chunks
            ):
                break

            # 3. Check client disconnect
            if request and await request.is_disconnected():
                break

            # 4. Wait for new chunk or timeout (for keep-alive heartbeat)
            if cursor >= len(session.chunks):
                current_event = session.event_notify
                if cursor < len(session.chunks) or session.status in (
                    "completed",
                    "error",
                    "stopped",
                ):
                    continue
                try:
                    await asyncio.wait_for(current_event.wait(), timeout=1.0)
                except TimeoutError:
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
    end = getattr(cfg, "RESEARCH_ACTIVE_HOURS_END", 21)
    if start == 0 and end == 0:
        return True  # Windowing disabled
    current_hour = time.localtime().tm_hour
    return start <= current_hour < end


def terminate_research_process(task_id: str):
    """Immediately terminate the active background subprocess for a research task if running and clean up lock files."""
    proc = _active_research_processes.pop(task_id, None)
    if proc:
        try:
            print(
                f"[RESEARCH TERMINATE] Terminating active subprocess handle for task {task_id}",
                flush=True,
            )
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except (subprocess.SubprocessError, OSError):
                with suppress(subprocess.SubprocessError, OSError):
                    proc.kill()
        except (subprocess.SubprocessError, OSError) as e:
            print(
                f"[RESEARCH TERMINATE ERROR] Failed to terminate subprocess handle {task_id}: {e}",
                flush=True,
            )

    # Hardened cleanup: check engine.pid via psutil, kill orphan process if alive, and remove engine.pid
    try:
        from Evelyn.tools.research_engine import get_task_dir

        pid_path = os.path.join(get_task_dir(task_id), "engine.pid")
        if os.path.exists(pid_path):
            with contextlib.suppress(OSError, ValueError):
                with open(pid_path) as f:
                    pid = int(f.read().strip())

                if psutil.pid_exists(pid):
                    p = psutil.Process(pid)
                    if any("research_engine.py" in arg for arg in p.cmdline()):
                        print(
                            f"[RESEARCH TERMINATE] Killing process PID {pid} for task {task_id}",
                            flush=True,
                        )
                        p.terminate()
                        try:
                            p.wait(timeout=2.0)
                        except (psutil.Error, OSError):
                            p.kill()
            with suppress(OSError):
                os.remove(pid_path)
    except (psutil.Error, OSError) as e:
        print(
            f"[RESEARCH TERMINATE ERROR] PID cleanup failed for {task_id}: {e}",
            flush=True,
        )


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
    import json
    import os

    try:
        from Evelyn.tools.string_utils import (
            build_autonomous_trigger_envelope,
            build_system_event_envelope,
            stack_envelopes,
        )
    except ImportError:
        from string_utils import (
            build_autonomous_trigger_envelope,
            build_system_event_envelope,
            stack_envelopes,
        )

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
                    with open(state_file, encoding="utf-8") as f:
                        state = json.load(f)
                        if "task_id" not in state:
                            state["task_id"] = d
                        status = state.get("status")
                        is_quarantined = bool(state.get("quarantined"))
                        is_struggling = bool(state.get("struggling"))
                        plan = state.get("plan", {})
                        sqs = plan.get("sub_questions", [])
                        has_stuck_sq = any(
                            sq.get("status") == "needs_guidance" for sq in sqs
                        )

                        if status == "done" and not is_quarantined:
                            if not state.get("notified", False):
                                unnotified_count += 1
                        elif (
                            status == "needs_guidance"
                            or is_quarantined
                            or is_struggling
                            or has_stuck_sq
                        ):
                            stalled_tasks.append(state)
                except (OSError, json.JSONDecodeError, ValueError):
                    pass

    envelopes = []
    for t in stalled_tasks:
        query = t.get("query", "Unknown Topic")
        task_id = t.get("task_id", "")
        if t.get("quarantined"):
            status_desc = "quarantined due to low confidence"
            severity = "high"
        elif t.get("status") == "needs_guidance" or t.get("struggling"):
            status_desc = "struggling to find relevant evidence"
            severity = "medium"
        else:
            status_desc = str(t.get("status", "unknown")).lower()
            severity = "medium"

        idx = t.get("current_sq_idx", 0)
        plan = t.get("plan", {})
        sqs = plan.get("sub_questions", [])
        sq_query = ""
        if 0 <= idx < len(sqs):
            sq_query = (
                sqs[idx].get("question")
                or sqs[idx].get("search_query")
                or sqs[idx].get("query", "")
            )
        elif sqs:
            stuck = next(
                (s for s in sqs if s.get("status") == "needs_guidance"), sqs[0]
            )
            sq_query = (
                stuck.get("question")
                or stuck.get("search_query")
                or stuck.get("query", "")
            )

        summary = f"Research task on '{query}' is {status_desc}."
        if sq_query:
            summary += f" Sub-question stuck: '{sq_query}'"

        envelopes.append(
            build_autonomous_trigger_envelope(
                trigger_type="research_needs_guidance" if not t.get("quarantined") else "research_quarantined",
                entity_id=task_id,
                severity=severity,
                summary=summary,
                directive=f"Mention this to {cfg.USER_NAME} or use 'guide_research' / 'inspect_research_task' to adjust search parameters.",
            )
        )

    if unnotified_count > 0:
        envelopes.append(
            build_system_event_envelope(
                event="research_completed",
                status="ready",
                description=f"{unnotified_count} newly completed deep research task(s) are ready. You may call 'check_new_research' if relevant to {cfg.USER_NAME}'s prompt.",
            )
        )

    return stack_envelopes(*envelopes)


def get_upcoming_agenda_prompt_context() -> str:
    """Fetch structured temporal and agenda context from TimeManager.

    Returns:
        str: XML temporal context string.
    """
    con = get_db()
    try:
        return time_manager.build_temporal_envelope(con)
    except (sqlite3.Error, OSError, ValueError) as e:
        return f"\n[Agenda Error] Failed to load agenda notification: {e}"
    finally:
        con.close()


def load_system_prompt() -> str:
    """Assemble the system prompt from narrative persona files and direct instructions.

    Returns:
        str: The combined and formatted system prompt.
    """
    import re

    # Matches YAML frontmatter with either LF or CRLF line endings (Windows files use CRLF)
    _FRONTMATTER_RE = re.compile(r"^---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
    parts = []
    date_str = datetime.now(UTC).astimezone().strftime("%A, %B %d, %Y")
    time_str = datetime.now(UTC).astimezone().strftime("%I:%M %p")
    parts.append(f"The current date and time is {date_str} - {time_str}.")
    parts.append(
        "<system_telemetry_directives>\n"
        "Injected XML envelopes (`<temporal_context>`, `<context_retrieval>`, `<autonomous_trigger>`, `<system_event>`, `<memory_context>`) represent background environmental telemetry produced by the server runtime.\n"
        f"1. `<temporal_context>`: Reports the absolute clock, session resumption gap, and agenda alerts for {cfg.USER_NAME}. `<current_time>` is the sole authoritative clock; never estimate, calculate, or offset clock times. Treat `<session_gap>` as passive atmospheric awareness for natural transition grounding; never interrogate or call out silences unless {cfg.USER_NAME} explicitly mentions having been away or the gap spans multiple hours / overnight.\n"
        "2. `<context_retrieval>`: Contains relevant retrieved vault notes, documents, and active operational protocols. Use this data purely as background context and factual ground truth.\n"
        f"3. `<autonomous_trigger>` & `<system_event>`: Convey proactive background events, completed research tasks, or daemon alerts.\n"
        f"4. Never attribute telemetry blocks to {cfg.USER_NAME}.\n"
        "5. Injected XML envelopes are server telemetry wrappers: NEVER replicate, wrap, echo, or emit these raw XML tags in conversational responses.\n"
        "</system_telemetry_directives>"
    )
    parts.append(
        "Use thinking for fact verification, logical analysis, and selecting "
        "tools. Keep thinking concise -- you don't need lengthy chains for casual conversation. "
        "Never draft, simulate, outline, or rehearse your response text inside thinking. "
        "When actions or lookups are needed, call the tool directly, when in doubt use the tool. "
        "If a turn calls for unusually deep reflection (complex multi-step analysis, technical planning, "
        'or deep emotional nuance), you may include {"requested_effort":"high"} on its own line before '
        "your response. For brief acknowledgments or casual sign-offs where deep reasoning is "
        'unnecessary, include {"requested_effort":"low"} instead. '
        "Do not include this marker in routine replies."
    )
    for fname in cfg.PERSONA_FILES:
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
# Time-gap awareness (Legacy compatibility wrapper)
# ---------------------------------------------------------------------------


def get_time_gap_context() -> str | None:
    """Return a time-gap annotation if enough time has passed since the last message.

    Returns:
        str | None: A succinct bracketed explanation of the last message time,
            elapsed time gap, and current time if exceeding 15 minutes, otherwise None.
    """
    con = get_db()
    try:
        gap = time_manager.evaluate_session_gap(con)
        if gap:
            return f"[Last interaction: {gap['last_interaction_ts']} ({gap['duration_str']} ago)]"
        return None
    finally:
        con.close()


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
            print(
                f"[FTS5] Rebuilt search index for {msg_count} existing messages.",
                flush=True,
            )
    except (sqlite3.Error, OSError, ValueError) as e:
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
    with suppress(sqlite3.OperationalError):
        con.execute("ALTER TABLE messages ADD COLUMN tools_used TEXT")

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
    with suppress(Exception):
        con.execute("ALTER TABLE message_metrics ADD COLUMN think_effort TEXT")
    with suppress(Exception):
        con.execute("ALTER TABLE message_metrics ADD COLUMN think_source TEXT")

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

    con.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id           TEXT PRIMARY KEY,
            tasklist_id  TEXT NOT NULL DEFAULT '@default',
            title        TEXT NOT NULL,
            notes        TEXT,
            due_at       TEXT,
            status       TEXT NOT NULL DEFAULT 'needsAction',
            completed_at TEXT,
            source       TEXT NOT NULL DEFAULT 'google',
            last_sync    TEXT NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_tasks_due_at ON tasks(due_at)")

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
    "<|channel|>",
    "lania_thought\n",
    "<tool_call|>",
    "<|tool_call|>",
    "<|thought|>",
    "</|thought|>",
    "◀channel▶",
    "◀thought▶",
    "◀/thought▶",
    "◀call:",
    "▶call",
    "◀|",
    "|▶",
]


def _time_of_day_label(ts: float | None) -> str:
    """Convert a unix timestamp to a 'Day Mon DD \u00b7 period' label.

    Returns a bracketed label like '[Mon Jun 09 \u00b7 afternoon] ' for use as a
    transcript prefix. Returns an empty string if ts is absent or invalid.
    """
    if not ts:
        return ""
    try:
        d = datetime.fromtimestamp(ts, tz=UTC).astimezone()
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


def _estimate_message_tokens(msg: dict[str, Any]) -> int:
    """Conservative token estimate for a single message dictionary (~3 characters/token + role overhead)."""
    content = msg.get("content") or ""
    return max(1, len(content) // 3 + 4)


def load_history() -> list[dict]:
    """Load recent chat history bounded by day boundaries, thread breaks, and token budgets.

    Rules:
      1. Loads 100% of today's messages (ts >= midnight).
      2. Plus up to 6 messages from the previous day (for evening/transition context).
      3. Bounded by the latest [THREAD_BREAK] marker if present.
      4. Governed by a conservative safe token budget derived from cfg.NUM_CTX.
      5. If history exceeds the safe token budget, prunes older messages while preserving
         turn integrity, recent active turns, and system date boundary markers.
      6. Injects explicit date boundary markers with journal isolation instructions.

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

    from datetime import time as dtime

    today_start = datetime.combine(datetime.now(UTC).astimezone().date(), dtime.min).replace(tzinfo=UTC).astimezone().timestamp()

    # 1. Fetch all today's messages (newest first, no arbitrary message-count limit)
    today_rows = con.execute(
        "SELECT role, content, tools_used, ts FROM messages WHERE id > ? AND ts >= ? ORDER BY id DESC",
        (after_id, today_start),
    ).fetchall()

    # 2. Fetch up to 6 messages from yesterday (for morning/transition context)
    prev_rows = con.execute(
        "SELECT role, content, tools_used, ts FROM messages WHERE id > ? AND ts < ? ORDER BY id DESC LIMIT 6",
        (after_id, today_start),
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
                msg_date = datetime.fromtimestamp(ts, tz=UTC).astimezone().date()
                if last_date is not None and msg_date != last_date:
                    date_str = msg_date.strftime("%A, %b %d, %Y")
                    messages.append(
                        {
                            "role": "system",
                            "content": f"--- Date Changed: {date_str} (All journal entries and daily reflections must reference ONLY events occurring after this date marker) ---",
                        }
                    )
                last_date = msg_date
            except (OSError, OverflowError, ValueError):
                pass

        role = r["role"]
        content = r["content"]
        if role == "user":
            content = f"{_time_of_day_label(ts)}{content}"
        elif role == "assistant" and r["tools_used"]:
            tools_summary = r["tools_used"].strip()
            if tools_summary:
                content = f"{content}\n\n[Tools Executed: {tools_summary}]"

        messages.append(
            {
                "role": role,
                "content": content,
            }
        )

    # Strip orphaned trailing user/system messages (no assistant response yet).
    # These form double-user-message chains that confuse the model.
    while messages and messages[-1]["role"] in ("user", "system"):
        messages.pop()

    # Calculate safe token budget against NUM_CTX
    num_ctx = getattr(cfg, "NUM_CTX", 32768)
    tool_predict = getattr(cfg, "TOOL_LOOP_NUM_PREDICT", 8192)
    # Reserved overhead: System Prompt + Persona (~3500) + Tool schemas (~1500) + RAG context (~2500) + Output buffer (tool_predict) + Safety margin (1000)
    reserved_overhead = 3500 + 1500 + 2500 + tool_predict + 1000
    safe_history_budget = max(4000, num_ctx - reserved_overhead)

    total_tokens = sum(_estimate_message_tokens(m) for m in messages)

    # If exceeding token budget, prune older messages while preserving turn integrity
    # and ensuring system date markers remain intact.
    if total_tokens > safe_history_budget and len(messages) > 4:
        dlog(f"History token budget exceeded ({total_tokens} > {safe_history_budget}). Pruning older messages...")

        # Prune from front until within budget, preserving turn integrity
        while len(messages) > 4 and total_tokens > safe_history_budget:
            # Pop the oldest non-system message if possible
            first_idx = 0
            if messages[0].get("role") == "system":
                first_idx = 1 if len(messages) > 1 and messages[1].get("role") != "system" else 0

            removed = messages.pop(first_idx)
            total_tokens -= _estimate_message_tokens(removed)

        # After pruning, clean up any leading assistant message
        while messages and messages[0]["role"] == "assistant":
            removed = messages.pop(0)
            total_tokens -= _estimate_message_tokens(removed)

        # Strip any trailing user/system messages
        while messages and messages[-1]["role"] in ("user", "system"):
            removed = messages.pop()
            total_tokens -= _estimate_message_tokens(removed)

    dlog(
        f"History: loaded {len(today_rows)} today + {len(prev_rows)} prev day = {len(messages)} total msgs (~{total_tokens} est tokens)"
    )
    return messages


def save_message(
    role: str, content: str, thinking: str | None = None, tools_used: str | None = None
) -> None:
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


def save_message_get_id(
    role: str, content: str, thinking: str | None = None, tools_used: str | None = None
) -> int:
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
    return row_id if row_id is not None else 0


def update_message(
    row_id: int,
    content: str,
    thinking: str | None = None,
    tools_used: str | None = None,
    tool_metadata: str | None = None,
):
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
        ),
    )
    con.commit()
    con.close()


def save_or_update_feedback(
    message_id: int, rating: int, feedback: str | None = None
) -> dict:
    """Save or update user feedback (+1 / -1 / 0) for a message.

    If rating == 0, removes feedback for that message.
    """
    con = get_db()
    try:
        now = time.time()
        if rating == 0:
            con.execute(
                "DELETE FROM message_feedback WHERE message_id = ?", (message_id,)
            )
            con.commit()
            return {"message_id": message_id, "rating": 0, "feedback": None}

        cur = con.cursor()
        cur.execute(
            "SELECT id FROM message_feedback WHERE message_id = ?", (message_id,)
        )
        row = cur.fetchone()
        if row:
            con.execute(
                "UPDATE message_feedback SET rating = ?, feedback = ?, updated_at = ? WHERE message_id = ?",
                (rating, feedback, now, message_id),
            )
        else:
            con.execute(
                "INSERT INTO message_feedback (message_id, rating, feedback, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (message_id, rating, feedback, now, now),
            )
        con.commit()
        return {"message_id": message_id, "rating": rating, "feedback": feedback}
    finally:
        con.close()


def get_feedback_for_messages(message_ids: list[int]) -> dict[int, dict]:
    """Retrieve feedback dicts keyed by message_id for a batch of messages."""
    if not message_ids:
        return {}
    con = get_db()
    try:
        placeholders = ",".join("?" for _ in message_ids)
        rows = con.execute(
            f"SELECT message_id, rating, feedback, created_at FROM message_feedback WHERE message_id IN ({placeholders})",
            message_ids,
        ).fetchall()
        return {
            r["message_id"]: {
                "rating": r["rating"],
                "feedback": r["feedback"],
                "created_at": r["created_at"],
            }
            for r in rows
        }
    finally:
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


async def call_ollama_stream(
    messages: list[dict], tools: list[dict] | None = None, think_effort=None
):
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
    options: dict[str, Any] = {
        k: v
        for k, v in {
            "num_ctx": cfg.NUM_CTX,
            "temperature": cfg.TEMPERATURE,
            "min_p": cfg.MIN_P,
            "top_k": cfg.TOP_K,
            "top_p": cfg.TOP_P,
            "repeat_penalty": cfg.REPEAT_PENALTY,
            "repeat_last_n": cfg.REPEAT_LAST_N,
            "seed": cfg.SEED,
            "num_predict": cfg.NUM_PREDICT,
        }.items()
        if v is not None
    }
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

    async with httpx.AsyncClient(timeout=600) as client, client.stream(
        "POST", f"{cfg.OLLAMA_URL}/api/chat", json=payload
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if line.strip():
                yield line


async def call_ollama_full(
    messages: list[dict],
    tools: list[dict] | None = None,
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
    options: dict[str, Any] = {
        k: v
        for k, v in {
            "num_ctx": cfg.NUM_CTX,
            "temperature": cfg.TEMPERATURE,
            "min_p": cfg.MIN_P,
            "top_k": cfg.TOP_K,
            "top_p": cfg.TOP_P,
            "repeat_penalty": cfg.REPEAT_PENALTY,
            "repeat_last_n": cfg.REPEAT_LAST_N,
            "seed": cfg.SEED,
            "num_predict": num_predict_override
            if num_predict_override is not None
            else cfg.NUM_PREDICT,
        }.items()
        if v is not None
    }
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
    except (AttributeError, TypeError, ValueError, KeyError, RuntimeError, OSError) as e:
        import traceback

        print(f"\n{_RED}[TOOL ERROR]{_RST} Exception in '{name}':", flush=True)
        traceback.print_exc()
        return f"Tool '{name}' raised an error: {e}"


async def _agentic_stream_loop(
    msgs: list[dict],
    think_effort=None,
    ui_override: bool = False,
):
    """
    Unified agentic streaming loop.
    Executes iterative streaming rounds with tools enabled (up to MAX_TOOL_ROUNDS).
    Streams live thinking deltas, dispatches intermediate tool calls, and streams
    the final synthesized response content in a single unified pipeline.

    Args:
        msgs: Conversation history messages to send to Ollama.
        think_effort: Initial thinking effort ("low", "medium", "high", "max").
        ui_override: True if user explicitly selected thinking effort in UI.

    Yields:
        str: SSE formatted JSON event lines.
    """
    accumulated_thinking = ""
    final_content = ""
    tools_used_list = []
    tool_metadata_list = []

    current_think_effort = think_effort if think_effort is not None else cfg.THINK
    think_source = "ui_override" if ui_override else "heuristic"

    aggregated_metrics = {
        "prompt_eval_count": 0,
        "eval_count": 0,
        "prompt_eval_duration": 0,
        "eval_duration": 0,
        "total_duration": 0,
        "load_duration": 0,
        "think_effort": str(current_think_effort),
        "think_source": think_source,
    }

    loop = asyncio.get_running_loop()
    _SENTINEL = object()
    OPEN_TAG = "<think>"
    CLOSE_TAG = "</think>"

    for round_num in range(1, cfg.MAX_TOOL_ROUNDS + 1):
        is_terminal_round = round_num >= cfg.MAX_TOOL_ROUNDS
        tools_for_round = None if is_terminal_round else MODEL_TOOL_DEFINITIONS

        round_thinking = ""
        round_content = ""
        round_tool_calls = []
        parse_buf = ""
        in_think = False

        print(
            f"{_CYN}[STREAM ROUND {round_num}/{cfg.MAX_TOOL_ROUNDS}]{_RST} "
            f"think={current_think_effort}, tools={'None' if tools_for_round is None else len(tools_for_round)}. Roles:",
            [m["role"] for m in msgs],
            flush=True,
        )

        queue: asyncio.Queue = asyncio.Queue()

        async def _feed(feed_msgs, feed_tools, feed_think, target_q=queue):
            try:
                async for line in call_ollama_stream(
                    feed_msgs, tools=feed_tools, think_effort=feed_think
                ):
                    await target_q.put(("line", line))
            except (httpx.HTTPError, RuntimeError, OSError, ValueError) as exc:
                await target_q.put(("error", exc))
                return
            await target_q.put(("done", _SENTINEL))

        feeder = asyncio.create_task(_feed(msgs, tools_for_round, current_think_effort))

        try:
            while True:
                try:
                    kind, item = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    yield 'data: {"type":"heartbeat"}\n\n'
                    continue

                if kind == "error":
                    print(
                        f"{_RED}[STREAM ERROR R{round_num}]{_RST} {type(item).__name__}: {item}",
                        flush=True,
                    )
                    raise item
                if kind == "done":
                    break

                chunk = {}
                with contextlib.suppress(json.JSONDecodeError):
                    chunk = json.loads(item)
                if not chunk:
                    continue

                msg = chunk.get("message", {})

                # 1. Native thinking field
                native_think = msg.get("thinking", "")
                if native_think:
                    round_thinking += native_think
                    yield f"data: {json.dumps({'type': 'thinking', 'round': round_num, 'delta': native_think})}\n\n"

                # 2. Tool calls (captured when emitted by model)
                if msg.get("tool_calls"):
                    round_tool_calls = msg.get("tool_calls")

                # 3. Content field parsing
                text_delta = msg.get("content", "")
                if text_delta:
                    for _tok in _LEAKED_MODEL_TOKENS:
                        text_delta = text_delta.replace(_tok, "")

                    # Self-election parsing in Round 1
                    if round_num == 1 and cfg.THINK_SELF_ELECT and not ui_override:
                        m_elect = _SELF_ELECT_RE.search(text_delta)
                        if m_elect:
                            elected = re.search(
                                r'"requested_effort":\s*"(low|medium|high|max)"',
                                m_elect.group(0),
                                re.IGNORECASE,
                            )
                            if elected:
                                current_think_effort = elected.group(1)
                                think_source = "self_elect"
                                aggregated_metrics["think_effort"] = str(
                                    current_think_effort
                                )
                                aggregated_metrics["think_source"] = think_source
                                dlog(
                                    f"Self-elected think effort: {current_think_effort}"
                                )

                    text_delta = _SELF_ELECT_RE.sub("", text_delta)
                    parse_buf += text_delta

                    while parse_buf:
                        if in_think:
                            ct_idx = parse_buf.find(CLOSE_TAG)
                            if ct_idx == -1:
                                safe = len(parse_buf) - len(CLOSE_TAG)
                                if safe > 0:
                                    out = parse_buf[:safe]
                                    round_thinking += out
                                    yield f"data: {json.dumps({'type': 'thinking', 'round': round_num, 'delta': out})}\n\n"
                                    parse_buf = parse_buf[safe:]
                                break
                            else:
                                if ct_idx > 0:
                                    out = parse_buf[:ct_idx]
                                    round_thinking += out
                                    yield f"data: {json.dumps({'type': 'thinking', 'round': round_num, 'delta': out})}\n\n"
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
                                            round_content += out
                                            yield f"data: {json.dumps({'type': 'text', 'round': round_num, 'delta': out})}\n\n"
                                            parse_buf = parse_buf[safe:]
                                        found_partial = True
                                        break
                                if not found_partial:
                                    round_content += parse_buf
                                    yield f"data: {json.dumps({'type': 'text', 'round': round_num, 'delta': parse_buf})}\n\n"
                                    parse_buf = ""
                                break
                            else:
                                if ot_idx > 0:
                                    out = parse_buf[:ot_idx]
                                    round_content += out
                                    yield f"data: {json.dumps({'type': 'text', 'round': round_num, 'delta': out})}\n\n"
                                parse_buf = parse_buf[ot_idx + len(OPEN_TAG) :]
                                in_think = True

                # 4. Stream completion metrics
                if chunk.get("done"):
                    for m_key in (
                        "prompt_eval_count",
                        "eval_count",
                        "prompt_eval_duration",
                        "eval_duration",
                        "total_duration",
                        "load_duration",
                    ):
                        if chunk.get(m_key):
                            aggregated_metrics[m_key] = (
                                aggregated_metrics.get(m_key, 0) + chunk[m_key]
                            )
                    if parse_buf:
                        if in_think:
                            round_thinking += parse_buf
                            yield f"data: {json.dumps({'type': 'thinking', 'round': round_num, 'delta': parse_buf})}\n\n"
                        else:
                            round_content += parse_buf
                            yield f"data: {json.dumps({'type': 'text', 'round': round_num, 'delta': parse_buf})}\n\n"
                    break
        finally:
            if not feeder.done():
                feeder.cancel()

        # Check round outcome
        if round_tool_calls:
            dlog(
                f"Round {round_num}: model emitted {len(round_tool_calls)} tool call(s)"
            )

            # Preamble Quarantine: If text was streamed before/with tool calls,
            # emit quarantine notification so UI doesn't render it in the response body
            if round_content.strip():
                yield f"data: {json.dumps({'type': 'quarantine_preamble', 'round': round_num, 'text': round_content})}\n\n"

            if round_thinking.strip():
                label = f"[Round {round_num}]\n"
                accumulated_thinking += f"{label}{round_thinking.strip()}\n\n"

            # Append assistant turn with tool calls
            msgs.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": round_tool_calls,
                }
            )

            for tc in round_tool_calls:
                fn_name = tc.get("function", {}).get("name", "unknown")
                fn_args = tc.get("function", {}).get("arguments", {})
                if isinstance(fn_args, str):
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        fn_args = json.loads(fn_args)
                    if isinstance(fn_args, str):
                        fn_args = {}

                yield f"data: {json.dumps({'type': 'tool_start', 'round': round_num, 'tool': fn_name, 'args': fn_args})}\n\n"
                dlog(f"Dispatching tool: {fn_name}({fn_args})")

                tool_status = "ok"
                try:
                    result = await loop.run_in_executor(
                        None, lambda fn=fn_name, fa=fn_args: dispatch_tool(fn, fa)
                    )
                except (AttributeError, TypeError, ValueError, KeyError, RuntimeError, OSError) as exc:
                    result = f"Error executing {fn_name}: {exc}"
                    tool_status = "error"

                tool_entry = fn_name
                meta_entry: dict[str, Any] = {"name": fn_name, "data": None}
                approval_id_or_data = None

                if fn_name == "generate_image":
                    m_img = re.search(r"(/images/[^\s\)]+)", str(result))
                    if m_img:
                        tool_entry = f"{fn_name}[{m_img.group(1)}]"
                        meta_entry["data"] = {"path": m_img.group(1)}
                        approval_id_or_data = m_img.group(1)
                elif fn_name in ("run_command", "write_file", "write_journal_entry"):
                    m_appr = re.search(
                        r"Approval ID:\s*(cmd_\w+|write_\w+)", str(result)
                    )
                    if m_appr:
                        approval_id = m_appr.group(1)
                        tool_entry = f"{fn_name}[{approval_id}]"
                        meta_entry["data"] = {
                            "id": approval_id,
                            "type": "approval_required",
                        }
                        approval_id_or_data = approval_id
                        yield f"data: {json.dumps({'type': 'approval_required', 'approval_id': approval_id, 'tool': fn_name, 'args': fn_args})}\n\n"
                    elif fn_name == "write_journal_entry":
                        m_journal_date = re.search(r"Journal Entry (\d{4}-\d{2}-\d{2})\.md", str(result))
                        target_date_str = m_journal_date.group(1) if m_journal_date else datetime.now(UTC).astimezone().strftime("%Y-%m-%d")
                        tool_entry = f"{fn_name}[{target_date_str}]"
                        meta_entry["data"] = {"date": target_date_str, "file": f"Journal Entry {target_date_str}.md"}
                        approval_id_or_data = target_date_str

                tools_used_list.append(tool_entry)
                tool_metadata_list.append(meta_entry)

                yield f"data: {json.dumps({'type': 'tool_end', 'round': round_num, 'tool': fn_name, 'status': tool_status, 'summary': str(result)[:300], 'data': approval_id_or_data})}\n\n"
                if approval_id_or_data:
                    yield f"data: {json.dumps({'type': 'tool_data', 'name': fn_name, 'data': approval_id_or_data})}\n\n"

                msgs.append(
                    {
                        "role": "tool",
                        "content": str(result),
                        "name": fn_name,
                    }
                )

            # Tool effort escalation for subsequent rounds if needed
            if tools_used_list and not ui_override:
                tool_names_used = [t.split("[")[0] for t in tools_used_list if t]
                if tool_names_used:
                    max_tool_effort = max(
                        (TOOL_THINK_EFFORT.get(n, "medium") for n in tool_names_used),
                        key=lambda e: _EFFORT_RANK.get(str(e).lower(), 1),
                        default="medium",
                    )
                    curr_rank = _EFFORT_RANK.get(str(current_think_effort).lower(), 1)
                    max_rank = _EFFORT_RANK.get(str(max_tool_effort).lower(), 1)
                    if max_rank > curr_rank:
                        current_think_effort = max_tool_effort
                        aggregated_metrics["think_effort"] = str(current_think_effort)
                        aggregated_metrics["think_source"] = "tool_escalation"

            # Continue to next tool round
            continue

        else:
            # Terminal response reached (no tool calls)
            final_content = round_content
            if round_thinking.strip():
                label = (
                    f"[Round {round_num}]\n"
                    if round_num > 1 or accumulated_thinking
                    else ""
                )
                accumulated_thinking += f"{label}{round_thinking.strip()}\n\n"
            break

    yield f"data: {json.dumps({'type': '_state', 'content': final_content, 'thinking': accumulated_thinking.strip(), 'tools_used': tools_used_list, 'tool_metadata': tool_metadata_list, 'metrics': aggregated_metrics})}\n\n"


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Pydantic model representing an incoming chat request from the user."""

    message: str
    think: str | bool | None = (
        None  # UI override: "low"/"medium"/"high"/"max"/False/None
    )
    images: list[str | dict] = []  # Base64 strings or attachment objects with metadata


class EditRequest(BaseModel):
    """Pydantic model representing an incoming edit message request from the user."""

    message: str


class StopChatRequest(BaseModel):
    """Pydantic model representing a stop chat request."""

    stream_id: str | None = None


class FeedbackRequest(BaseModel):
    """Pydantic model representing user rating feedback on an assistant message."""

    message_id: int
    rating: int  # 1 for upvote, -1 for downvote, 0 for clear
    feedback: str | None = None


async def _process_chat_background(
    user_message: str,
    is_regenerate: bool,
    time_ctx: str | None,
    assistant_row_id: int,
    session: ActiveStreamSession,
    images: list[str] | None = None,
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

    async def put(type_: str, **kw):
        """Enqueue a serialized SSE event dictionary to the active stream session."""
        session.push_chunk("data: " + json.dumps({"type": type_, **kw}) + "\n\n")

    import task_manager

    task_manager.set_chat_preemption(True)
    task_manager.cancel_all_idle_tasks("chat_request")

    try:
        session.push_chunk(
            "data: "
            + json.dumps({"type": "stream_session", "stream_id": session.stream_id})
            + "\n\n"
        )
        await put("status", msg="Processing...")

        # RAG + system prompt + history (fast synchronous work)
        rag_context = await asyncio.to_thread(
            build_rag_context, user_message, assistant_row_id
        )
        system = load_system_prompt()
        if rag_context:
            system += f"\n\n{rag_context}"
            chunk_count = rag_context.count("\n[")
            pinned_count = rag_context.count("[primary source]")
            dlog(
                f"RAG injected: chars={len(rag_context)} chunks={chunk_count} pinned={pinned_count}"
            )

        history = load_history()

        # Build structured temporal envelope telemetry
        con = get_db()
        try:
            temporal_envelope = time_manager.build_temporal_envelope(con)
        finally:
            con.close()

        research_ctx = get_research_context()
        try:
            from Evelyn.tools.string_utils import escape_xml_content, inject_envelope_to_turn, stack_envelopes, wrap_xml_envelope
        except ImportError:
            from string_utils import escape_xml_content, inject_envelope_to_turn, stack_envelopes, wrap_xml_envelope

        # Build daytime ambient stream context if unconsumed daytime impressions exist
        ambient_stream_ctx = ""
        try:
            from Evelyn.tools import memory_db
            now_local = datetime.now(UTC).astimezone()
            today_str = now_local.strftime("%Y-%m-%d")
            unconsumed = memory_db.get_unconsumed_ambient_impressions(today_str)
            if unconsumed:
                is_evening = now_local.hour >= 17 or now_local.hour < 5
                msg_lower = user_message.lower()
                is_journal_query = any(k in msg_lower for k in ("journal", "wind down", "wrap up", "day recap", "reflect on today", "bedtime", "goodnight"))
                if is_evening or is_journal_query:
                    imp_lines = []
                    for imp in unconsumed:
                        imp_type = imp.get("type", "thought")
                        imp_ts = imp.get("ts")
                        time_str = datetime.fromtimestamp(imp_ts, tz=now_local.tzinfo).strftime("%H:%M") if imp_ts else ""
                        imp_content = escape_xml_content(imp.get("content", ""))
                        imp_lines.append(f'  <impression type="{imp_type}" time="{time_str}">{imp_content}</impression>')
                    ambient_stream_ctx = wrap_xml_envelope("ambient_stream", body=imp_lines)
        except (sqlite3.Error, OSError, ValueError, RuntimeError, AttributeError) as e:
            dlog(f"Ambient stream build error: {e}")

        envelope_stack = stack_envelopes(temporal_envelope, research_ctx, ambient_stream_ctx)
        user_msg_for_model = inject_envelope_to_turn(user_message, envelope_stack)

        messages = [{"role": "system", "content": system}, *history]

        user_turn: dict[str, Any] = {"role": "user", "content": user_msg_for_model}
        if images:
            user_turn["images"] = images
        messages.append(user_turn)

        await put("status", msg="Querying model...")

        # Unified Agentic Stream Loop
        async for event in _agentic_stream_loop(
            messages, think_effort=think_effort, ui_override=ui_override
        ):
            if event.startswith("data: "):
                try:
                    d = json.loads(event[6:])
                    if d.get("type") == "_state":
                        content_buf = d.get("content", "")
                        thinking_buf = d.get("thinking", "")
                        tools_used_list = d.get("tools_used", [])
                        tool_metadata_list = d.get("tool_metadata", [])
                        metrics_dict.update(d.get("metrics", {}))
                        if metrics_dict:
                            await put("metrics", **metrics_dict)
                        continue
                    if d.get("type") == "text":
                        content_buf += d.get("delta", "")
                    elif d.get("type") == "thinking":
                        thinking_buf += d.get("delta", "")
                except (json.JSONDecodeError, TypeError, KeyError):
                    pass
            session.push_chunk(event)

    except asyncio.CancelledError:
        dlog(f"Chat background task cancelled for session {session.stream_id}")
        session.is_cancelled = True
        raise
    except (httpx.HTTPError, sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
        print(
            f"{_RED}[CHAT BACKGROUND ERROR]{_RST} {type(exc).__name__}: {exc}",
            flush=True,
        )
        session.mark_complete(error=str(exc))
    finally:
        # Always commit to DB inside shielded block — independent of whether task is cancelled
        tools_str = ",".join(tools_used_list) if tools_used_list else None
        tools_meta_str = json.dumps(tool_metadata_list) if tool_metadata_list else None

        if session.is_cancelled or session.status == "stopped":
            update_message(
                assistant_row_id,
                "[Response interrupted -- please try again.]",
                thinking=thinking_buf.strip() if thinking_buf.strip() else None,
                tools_used=tools_str,
                tool_metadata=tools_meta_str,
            )
            session.push_chunk(f"data: {json.dumps({'type': 'stopped'})}\n\n")
            session.mark_complete(status="stopped")
            dlog(f"Chat session {session.stream_id} stopped cleanly")
        else:
            final_content = content_buf.strip()
            if final_content:
                update_message(
                    assistant_row_id,
                    final_content,
                    thinking=thinking_buf.strip() if thinking_buf.strip() else None,
                    tools_used=tools_str,
                    tool_metadata=tools_meta_str,
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
                    bool(tools_used_list),
                )

            dlog(
                f"Done -- content: {len(content_buf)} chars, thinking: {len(thinking_buf)} chars"
            )

            # Signal SSE pipe to close cleanly
            session.push_chunk(
                f"data: {json.dumps({'type': 'done', 'message_id': assistant_row_id, 'metrics': metrics_dict})}\n\n"
            )
            session.mark_complete()

        task_manager.set_chat_preemption(False)
        _last_activity_ts = time.time()


def pause_all_active_research():
    """Immediately pause any currently running background research tasks to prevent Ollama blockage."""
    global _background_tasks
    paused_any = False

    # 1. Terminate all tracked active process handles
    for task_id in list(_active_research_processes.keys()):
        print(
            f"[IMMEDIATE RESEARCH PAUSE] Terminating tracked active research process handle: {task_id}",
            flush=True,
        )
        terminate_research_process(task_id)
        paused_any = True

    # 2. Check disk state for any active tasks in data/research
    try:
        from Evelyn.tools.research_engine import load_state, save_state
    except (ImportError, ModuleNotFoundError):
        try:
            from research_engine import load_state, save_state
        except (ImportError, ModuleNotFoundError) as e:
            print(
                f"[IMMEDIATE RESEARCH PAUSE ERROR] Could not import research_engine: {e}",
                flush=True,
            )
            load_state, save_state, _get_task_dir = None, None, None

    if os.path.exists(cfg.RESEARCH_DATA_DIR):
        for d in os.listdir(cfg.RESEARCH_DATA_DIR):
            if d.startswith("task_"):
                task_dir = os.path.join(cfg.RESEARCH_DATA_DIR, d)
                pid_path = os.path.join(task_dir, "engine.pid")
                is_active = os.path.exists(pid_path)

                if load_state:
                    try:
                        state = load_state(d)
                        if state and state.get("status") in (
                            "running",
                            "searching",
                            "synthesizing",
                        ):
                            is_active = True
                            state["status"] = "paused"
                            state["error"] = (
                                "Paused: Interrupted automatically due to active user chat session (to prioritize conversational response speed)."
                            )
                            if save_state:
                                save_state(d, state)
                    except (json.JSONDecodeError, OSError, ValueError) as e:
                        print(
                            f"[IMMEDIATE RESEARCH PAUSE ERROR] Failed to pause task {d} state: {e}",
                            flush=True,
                        )

                if is_active:
                    print(
                        f"[IMMEDIATE RESEARCH PAUSE] Pausing active research task {d} on disk due to user chat activity.",
                        flush=True,
                    )
                    terminate_research_process(d)
                    paused_any = True

                if d in _background_tasks:
                    _background_tasks[d]["status"] = "paused"

    # 3. Sweep psutil for any orphan research_engine.py processes
    try:
        current_pid = os.getpid()
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if p.pid == current_pid:
                    continue
                cmdline = p.info.get("cmdline") or []
                if any("research_engine.py" in str(arg) for arg in cmdline):
                    print(
                        f"[IMMEDIATE RESEARCH PAUSE] Killing orphan research_engine process PID {p.pid}",
                        flush=True,
                    )
                    p.terminate()
                    try:
                        p.wait(timeout=2.0)
                    except (psutil.Error, OSError):
                        p.kill()
                    paused_any = True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except (psutil.Error, OSError) as e:
        print(
            f"[IMMEDIATE RESEARCH PAUSE ERROR] psutil process scan failed: {e}",
            flush=True,
        )

    return paused_any


def clean_shutdown_all_tasks():
    """Cleanly terminate child processes, pause active research, and drain in-flight Chroma queue items."""
    print("[SERVER SHUTDOWN] Gracefully shutting down all tasks...", flush=True)
    from Evelyn.tools import chroma_rag, task_manager

    # 1. Terminate write producers (subprocesses/tasks) first so no new rows are created
    try:
        task_manager.terminate_all_subprocesses(grace_period=3.0)
    except (subprocess.SubprocessError, psutil.Error, OSError) as e:
        print(f"[SERVER SHUTDOWN ERROR] Subprocess termination failed: {e}", flush=True)

    try:
        pause_all_active_research()
    except (subprocess.SubprocessError, psutil.Error, OSError) as e:
        print(f"[SERVER SHUTDOWN ERROR] Research pause failed: {e}", flush=True)

    try:
        cancel_pending_consolidation()
        cancel_pending_procedure_consolidation()
        cancel_pending_extraction()
        cancel_pending_evolution()
    except (RuntimeError, OSError) as e:
        print(
            f"[SERVER SHUTDOWN ERROR] Background task cancellation failed: {e}",
            flush=True,
        )

    # 2. Bounded final Chroma queue drain (budget: 5.0s maximum)
    print(
        "[SERVER SHUTDOWN] Performing bounded final Chroma queue drain (budget: 5.0s)...",
        flush=True,
    )
    try:
        chroma_rag.flush_sync_queue(timeout=5.0)
    except (sqlite3.Error, OSError, RuntimeError, ValueError) as e:
        print(f"[SERVER SHUTDOWN] Final queue flush notice: {e}", flush=True)
    print("[SERVER SHUTDOWN] Clean shutdown complete. Exiting.", flush=True)


async def chat_stream(
    user_message: str,
    images: list[str | dict] | None = None,
    is_regenerate: bool = False,
    think_effort=None,
    ui_override: bool = False,
    request: Request | None = None,
):
    """Open an SSE connection to stream the generated chat response.

    Args:
        user_message: The text of the user's incoming chat message.
        images: Optional list of base64-encoded image strings or data URIs.
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

    clean_b64_images = []
    if not is_regenerate:
        time_ctx = None
        user_row_id = save_message_get_id("user", user_message)

        if images:
            from Evelyn.tools import media_db
            from Evelyn.tools.visual_indexer import vision_indexing_queue

            for img_item in images:
                if not img_item:
                    continue
                orig_name = None
                client_meta = None
                if isinstance(img_item, dict):
                    raw_str = img_item.get("data", "")
                    orig_name = img_item.get("name")
                    client_meta = img_item.get("metadata")
                else:
                    raw_str = str(img_item)

                mime = "image/png"
                b64_payload = raw_str
                if raw_str.startswith("data:") and ";base64," in raw_str:
                    header, b64_payload = raw_str.split(";base64,", 1)
                    mime = header.replace("data:", "").strip() or "image/png"

                try:
                    raw_bytes = base64.b64decode(b64_payload)
                    asset = media_db.store_or_get_media_asset(
                        data=raw_bytes,
                        mime_type=mime,
                        source_msg_id=user_row_id,
                        original_name=orig_name,
                        metadata=client_meta,
                        media_type="image",
                    )
                    clean_b64_images.append(b64_payload)
                    if asset.get("is_new"):
                        vision_indexing_queue.put_nowait(
                            {
                                "guid": asset["id"],
                                "base64": b64_payload,
                                "user_context": user_message,
                            }
                        )
                except (sqlite3.Error, OSError, ValueError, KeyError) as exc:
                    print(
                        f"[MEDIA ERROR] Failed processing image attachment: {exc}",
                        flush=True,
                    )
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

    task = asyncio.create_task(
        _process_chat_background(
            user_message,
            is_regenerate,
            time_ctx,
            assistant_row_id,
            session,
            images=clean_b64_images if clean_b64_images else None,
            think_effort=resolved_effort,
            ui_override=ui_override,
        )
    )
    session.task = task
    print(
        f"{_CYN}[CHAT]{_RST} Background task started for session {stream_id} — SSE pipe open",
        flush=True,
    )

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


def is_any_heavy_task_running(exclude_name: str | None = None) -> bool:
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

    # 0. Engine Version & Database Schema Validation
    print(f"\n{_CYN}🌌 Evelyn Engine v{cfg.__version__} ({cfg.VERSION_NAME}){_RST}\n")
    from Evelyn.tools import db_migrator

    if getattr(cfg, "AUTO_MIGRATE_ON_BOOT", False):
        migrated = db_migrator.apply_pending_migrations()
        if migrated:
            print(
                f"  {_GRN}DB Migrator:{_RST} Applied {len(migrated)} pending database migration(s)."
            )
    else:
        try:
            db_migrator.validate_db_schemas_or_raise()
            print(
                f"  {_GRN}DB Schemas:{_RST} All databases verified up to date (v{cfg.__version__})."
            )
        except db_migrator.DatabaseSchemaMismatchError as e:
            print(f"  {_RED}{e}{_RST}", flush=True)
            raise

    init_db()
    from Evelyn.tools import chroma_rag, task_manager

    # 1. Startup Sanitization & Process Reaper
    reap_res = task_manager.reap_orphaned_processes()
    print(
        f"  {_GRN}Process Reaper:{_RST} Swept {len(reap_res['reaped_pids'])} orphaned processes, cleared {len(reap_res['cleaned_locks'])} stale locks."
    )

    # 2. Chroma Vector DB Health Probe & Auto-Repair
    health = chroma_rag.check_chroma_health()
    if health["status"] == "healthy":
        print(
            f"  {_GRN}Chroma Vector DB:{_RST} Health probe passed ({health['count']} documents indexed)."
        )
    else:
        print(
            f"  {_RED}[WARNING] Chroma Vector DB corrupted:{_RST} {health['error']}. Initiating auto-repair...",
            flush=True,
        )
        chroma_rag.repair_corrupted_chroma(background=True)

    _lifespan_tasks: list[asyncio.Task] = []

    # 3. Single Custodian Chroma Sync Queue Drain Loop
    async def _chroma_queue_drain_loop():
        """Continuous background worker that drains the SQLite Chroma staging queue."""
        while True:
            try:
                drained = await asyncio.to_thread(chroma_rag.drain_sync_queue, 50)
                if drained > 0:
                    dlog(f"[CHROMA DRAIN] Processed {drained} queued records.")
            except asyncio.CancelledError:
                break
            except (sqlite3.Error, OSError, ValueError, KeyError, RuntimeError) as e:
                print(f"[CHROMA DRAIN ERROR] {e}", flush=True)
            await asyncio.sleep(1.5)

    _lifespan_tasks.append(asyncio.create_task(_chroma_queue_drain_loop()))
    print(
        f"  {_GRN}Chroma Custodian:{_RST} Started single-writer drain worker (interval=1.5s)."
    )

    # 4. Media DB & Visual Memory Indexer
    from Evelyn.tools import media_db, visual_indexer

    media_db.init_media_db()
    _lifespan_tasks.append(
        asyncio.create_task(
            visual_indexer.visual_indexing_worker_loop(
                is_busy_predicate=lambda: bool(
                    stream_registry.get_active() or is_any_heavy_task_running()
                )
            )
        )
    )
    print(
        f"  {_GRN}Visual Indexer:{_RST} Started background media extraction queue worker."
    )

    task_manager.load_persistent_state()
    task_manager.load_persistent_queue()
    _lifespan_tasks.append(asyncio.create_task(task_manager.start_watchdog()))
    print(
        f"{_BLD}{_CYN}Evelyn server starting on {cfg.BIND_HOST}:{cfg.SERVER_PORT}{_RST}"
    )
    print(f"  Model: {cfg.MODEL_NAME} | Context: {cfg.NUM_CTX} | Think: {cfg.THINK}")
    print(
        f"  History cap: {cfg.MAX_HISTORY_MESSAGES} msgs | Debug: {cfg.DEBUG_LOGGING}"
    )

    # Central Idle Task Dispatcher Loop (Pure FIFO Scheduling)
    async def _idle_task_dispatcher_loop():
        """Central worker that dispatches queued background tasks during idle periods."""
        while True:
            await asyncio.sleep(2.0)
            importlib.reload(cfg)

            # 1. Check startup boot grace period
            if task_manager.is_boot_grace_period_active():
                continue

            # 2. Check chat preemption flag
            if task_manager.is_chat_preempted():
                continue

            # 3. Check mutual exclusion — is another heavy task currently running?
            if is_any_heavy_task_running():
                continue

            # 4. Check if any runnable task is waiting in the idle queue
            idle_seconds = _get_current_idle_seconds()
            item = task_manager.acquire_next_runnable_task(idle_seconds)
            if not item:
                continue

            dispatched_task = item.get("task")
            if not dispatched_task:
                continue

            sched = task_manager.get_task_schedule(dispatched_task).value
            print(
                f"{_CYN}[IDLE DISPATCHER]{_RST} Dispatched task '{dispatched_task}' (tier={sched}, idle={idle_seconds / 60:.1f}m).",
                flush=True,
            )

            try:
                if dispatched_task == "extractor":
                    import fact_extractor

                    fact_extractor._extraction_task = asyncio.create_task(
                        run_extraction()
                    )
                elif dispatched_task == "consolidator":
                    import fact_consolidator
                    import procedure_consolidator

                    t1 = asyncio.create_task(run_consolidation())
                    t2 = asyncio.create_task(run_procedure_consolidation())
                    fact_consolidator._consolidation_task = t1
                    procedure_consolidator._procedure_task = t2
                elif dispatched_task == "procedure_consolidator":
                    import procedure_consolidator

                    procedure_consolidator._procedure_task = asyncio.create_task(
                        run_procedure_consolidation()
                    )
                elif dispatched_task == "profile_evolver":
                    t_pe = asyncio.create_task(run_profile_evolution())
                    _server_background_tasks.add(t_pe)
                    t_pe.add_done_callback(_server_background_tasks.discard)
                elif dispatched_task == "tag_librarian":
                    t_tl = asyncio.create_task(run_tag_librarian_task())
                    _server_background_tasks.add(t_tl)
                    t_tl.add_done_callback(_server_background_tasks.discard)
                elif dispatched_task == "refresh_memory":
                    t_rm = asyncio.create_task(start_refresh_memory_internal())
                    _server_background_tasks.add(t_rm)
                    t_rm.add_done_callback(_server_background_tasks.discard)
                elif dispatched_task == "auto_journaler":
                    from Evelyn.tools import auto_journaler

                    t_aj = asyncio.create_task(auto_journaler.run_auto_journaling())
                    auto_journaler._auto_journal_task = t_aj
                    _server_background_tasks.add(t_aj)
                    t_aj.add_done_callback(_server_background_tasks.discard)
                elif dispatched_task == "ambient_reflector":
                    from Evelyn.tools import ambient_reflector

                    t_ar = asyncio.create_task(ambient_reflector.run_ambient_reflection())
                    _server_background_tasks.add(t_ar)
                    t_ar.add_done_callback(_server_background_tasks.discard)
                else:
                    print(
                        f"{_YEL}[IDLE DISPATCHER]{_RST} Unknown task '{dispatched_task}' in queue.",
                        flush=True,
                    )
            except (RuntimeError, OSError, ValueError) as e:
                print(
                    f"[IDLE DISPATCHER ERROR] Failed to dispatch '{dispatched_task}': {e}",
                    flush=True,
                )

    _lifespan_tasks.append(asyncio.create_task(_idle_task_dispatcher_loop()))
    print(
        f"  {_GRN}Idle Dispatcher:{_RST} started central FIFO queue worker (grace={getattr(cfg, 'IDLE_STARTUP_GRACE_PERIOD', 60)}s)"
    )

    # Idle-time auto-journaling loop — enqueues into central task queue during late night
    async def _idle_auto_journal_loop():
        """Background loop that periodically evaluates late-night autonomous journaling eligibility."""
        from Evelyn.tools import auto_journaler

        while True:
            await asyncio.sleep(getattr(cfg, "AUTO_JOURNAL_CHECK_INTERVAL", 900))
            importlib.reload(cfg)
            if not getattr(cfg, "AUTO_JOURNAL_ENABLED", True):
                continue
            idle_seconds = _get_current_idle_seconds()
            eligible, _ = auto_journaler.should_trigger_auto_journal(idle_seconds=idle_seconds)
            if eligible:
                task_manager.enqueue_idle_task("auto_journaler")

    _lifespan_tasks.append(asyncio.create_task(_idle_auto_journal_loop()))
    print(
        f"  {_GRN}Auto-Journal:{_RST} idle timer started "
        f"(window={getattr(cfg, 'AUTO_JOURNAL_START_HOUR', 23)}:00–{getattr(cfg, 'AUTO_JOURNAL_END_HOUR', 4)}:00, "
        f"idle={getattr(cfg, 'AUTO_JOURNAL_IDLE_THRESHOLD', 5400) // 60}m)"
    )

    # Idle-time ambient reflector loop — enqueues into central task queue during daytime
    async def _idle_ambient_reflector_loop():
        """Background loop that periodically evaluates daytime ambient reflection eligibility."""
        from Evelyn.tools import ambient_reflector

        while True:
            await asyncio.sleep(getattr(cfg, "AMBIENT_REFLECTIONS_CHECK_INTERVAL", 1800))
            importlib.reload(cfg)
            if not getattr(cfg, "AMBIENT_REFLECTIONS_ENABLED", True):
                continue
            idle_seconds = _get_current_idle_seconds()
            eligible, _ = ambient_reflector.should_generate_idle_thought(idle_seconds=idle_seconds)
            if eligible:
                task_manager.enqueue_idle_task("ambient_reflector")

    _lifespan_tasks.append(asyncio.create_task(_idle_ambient_reflector_loop()))
    print(
        f"  {_GRN}Ambient Island:{_RST} idle timer started "
        f"(window={getattr(cfg, 'AMBIENT_REFLECTIONS_START_HOUR', 9)}:00–{getattr(cfg, 'AMBIENT_REFLECTIONS_END_HOUR', 21)}:00, "
        f"idle={getattr(cfg, 'AMBIENT_REFLECTIONS_MIN_IDLE_SECONDS', 7200) // 60}m)"
    )

    # Idle-time consolidation loop — enqueues into central task queue
    async def _idle_consolidation_loop():
        """Background loop that periodically enqueues fact consolidation."""
        while True:
            await asyncio.sleep(cfg.CONSOLIDATION_IDLE_CHECK_INTERVAL)
            importlib.reload(cfg)
            if not cfg.CONSOLIDATION_ENABLED:
                continue
            idle_seconds = _get_current_idle_seconds()
            if idle_seconds >= cfg.CONSOLIDATION_IDLE_THRESHOLD:
                task_manager.enqueue_idle_task("consolidator")

    _lifespan_tasks.append(asyncio.create_task(_idle_consolidation_loop()))
    print(
        f"  {_GRN}Consolidator:{_RST} idle timer started "
        f"(threshold={cfg.CONSOLIDATION_IDLE_THRESHOLD // 60}m, "
        f"check={cfg.CONSOLIDATION_IDLE_CHECK_INTERVAL // 60}m)"
    )

    # Idle-time extraction loop — enqueues into central task queue
    async def _idle_extraction_loop():
        """Background loop that periodically enqueues fact extraction when new messages exist."""
        while True:
            await asyncio.sleep(cfg.FACT_EXTRACTION_IDLE_CHECK_INTERVAL)
            importlib.reload(cfg)
            if not cfg.FACT_EXTRACTION_ENABLED:
                continue
            idle_seconds = _get_current_idle_seconds()
            if idle_seconds >= cfg.FACT_EXTRACTION_IDLE_THRESHOLD:
                task_manager.enqueue_idle_task("extractor")

    _lifespan_tasks.append(asyncio.create_task(_idle_extraction_loop()))
    print(
        f"  {_GRN}Extractor:{_RST}   idle timer started "
        f"(threshold={cfg.FACT_EXTRACTION_IDLE_THRESHOLD // 60}m, "
        f"check={cfg.FACT_EXTRACTION_IDLE_CHECK_INTERVAL // 60}m)"
    )

    # Idle-time deep research loop
    async def _idle_research_loop():
        """Background loop for deep research management."""
        import json
        import os

        while True:
            await asyncio.sleep(10)
            importlib.reload(cfg)
            if not getattr(cfg, "RESEARCH_ENABLED", True):
                continue

            idle_seconds = _get_current_idle_seconds()
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
                except (RuntimeError, OSError, ValueError) as e:
                    print(f"[RESEARCH ERROR] Topic generation failed: {e}", flush=True)

            # 2. Build a unified view of unfinished tasks from memory and disk
            from research_engine import load_state, save_state

            # Sync any new task folders on disk into _background_tasks
            if os.path.exists(cfg.RESEARCH_DATA_DIR):
                for d in os.listdir(cfg.RESEARCH_DATA_DIR):
                    if d.startswith("task_") and d not in _background_tasks:
                        disk_s = load_state(d)
                        if disk_s:
                            if disk_s.get("status") == "resolved":
                                import shutil

                                task_dir = os.path.join(cfg.RESEARCH_DATA_DIR, d)
                                if os.path.exists(task_dir):
                                    shutil.rmtree(task_dir, ignore_errors=True)
                                print(
                                    f"[RESEARCH CLEANUP] Auto-cleared resolved task {d} from disk during sync.",
                                    flush=True,
                                )
                                continue
                            _background_tasks[d] = {
                                "status": disk_s.get("status", "pending"),
                                "query": disk_s.get("query", ""),
                                "scope": disk_s.get("scope", "standard"),
                                "started_at": time.time(),
                            }

            unfinished_tasks = []
            active_task = None

            for tid, task in list(_background_tasks.items()):
                if tid.startswith("task_"):
                    # Check disk state as well to stay perfectly in sync
                    disk_state = load_state(tid)
                    if not disk_state and task.get("status") not in (
                        "running",
                        "searching",
                        "synthesizing",
                    ):
                        del _background_tasks[tid]
                        continue
                    status = (
                        disk_state.get("status") if disk_state else task.get("status")
                    )
                    if status == "resolved":
                        import shutil

                        task_dir = os.path.join(cfg.RESEARCH_DATA_DIR, tid)
                        if os.path.exists(task_dir):
                            shutil.rmtree(task_dir, ignore_errors=True)
                        if tid in _background_tasks:
                            del _background_tasks[tid]
                        continue
                    if status:
                        # Sync memory status back to prevent drift and release locks immediately
                        _background_tasks[tid]["status"] = status
                        if status in (
                            "done",
                            "error",
                            "cancelled",
                            "needs_guidance",
                            "paused",
                        ) and ("finished_at" not in _background_tasks[
                            tid
                        ] or not _background_tasks[tid].get("finished_at")):
                            _background_tasks[tid]["finished_at"] = time.time()

                    if status in (
                        "running",
                        "paused",
                        "error",
                        "searching",
                        "synthesizing",
                        "pending",
                        "needs_guidance",
                    ):
                        task_info = {
                            "task_id": tid,
                            "status": status,
                            "query": disk_state.get("query")
                            if disk_state
                            else task.get("query", ""),
                            "scope": disk_state.get("scope")
                            if disk_state
                            else task.get("scope", "standard"),
                            "created_at": disk_state.get("created_at")
                            if disk_state
                            else "",
                        }
                        unfinished_tasks.append(task_info)
                        if status in ("running", "searching", "synthesizing"):
                            active_task = task_info

            # 3. Handle active task pausing if user becomes active
            if active_task and active_task.get("task_id"):
                tid = str(active_task["task_id"])
                state = load_state(tid)
                disk_status = state.get("status") if state else None

                # If finished or changed out-of-band on disk, sync it to memory
                if disk_status and disk_status not in (
                    "running",
                    "searching",
                    "synthesizing",
                ):
                    prev_status = _background_tasks.get(tid, {}).get("status")
                    print(
                        f"[RESEARCH SYNC] Task {tid} completed or changed status on disk to '{disk_status}' — updating server memory.",
                        flush=True,
                    )
                    if tid in _background_tasks:
                        _background_tasks[tid]["status"] = disk_status
                        if disk_status in ("done", "error", "cancelled", "timed_out"):
                            _background_tasks[tid]["finished_at"] = time.time()
                    if disk_status == "done" and prev_status in (
                        "running",
                        "searching",
                        "synthesizing",
                    ):
                        print(
                            f"[RESEARCH REFRESH] Research task {tid} finished — triggering automatic memory refresh.",
                            flush=True,
                        )
                        await start_refresh_memory_internal()
                    continue

                if idle_seconds < 10:  # User active!
                    print(
                        f"[RESEARCH INTERRUPT] User active (idle={idle_seconds:.1f}s) — pausing deep research task {tid}",
                        flush=True,
                    )
                    if state and state.get("status") in (
                        "running",
                        "searching",
                        "synthesizing",
                    ):
                        state["status"] = "paused"
                        state["error"] = (
                            "Paused: Interrupted automatically due to active user chat session (to prioritize conversational response speed)."
                        )
                        save_state(tid, state)
                        if tid in _background_tasks:
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
                        end_h = getattr(cfg, "RESEARCH_ACTIVE_HOURS_END", 21)
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

                    print(
                        f"[RESEARCH AUTO-RECOVERY] Server idle for {idle_seconds:.1f}s — auto-resuming unfinished task {target_task['task_id']} (status: {target_task['status']})",
                        flush=True,
                    )
                    from evelyn_tools import resume_research_task

                    resume_research_task(target_task["task_id"])
                    _last_research_spawn_ts = time.time()  # Record spawn timestamp
                    # Wait for subprocess thread to spin up and register
                    await asyncio.sleep(20)
                continue

            # 5. Process queued tasks
            if research_window_open and idle_seconds >= getattr(
                cfg, "RESEARCH_IDLE_THRESHOLD", 1800
            ):
                # Double guard
                if unfinished_tasks:
                    continue

                queue_file = os.path.join(cfg.RESEARCH_DATA_DIR, "queue.json")
                if os.path.exists(queue_file):
                    def _read_queue(q_path: str):
                        try:
                            with open(q_path, encoding="utf-8") as f:
                                return json.load(f)
                        except (json.JSONDecodeError, OSError):
                            return []

                    queue = await asyncio.to_thread(_read_queue, queue_file)

                    if queue:
                        # Sort chronologically by created_at date
                        queue.sort(
                            key=lambda x: (
                                x.get("created_at") or x.get("created_time") or ""
                            )
                        )

                        next_task = queue.pop(0)

                        def _write_queue(q_path: str, q_data: list):
                            with (
                                contextlib.suppress(OSError, TypeError),
                                open(q_path, "w", encoding="utf-8") as f,
                            ):
                                json.dump(q_data, f, indent=2)

                        await asyncio.to_thread(_write_queue, queue_file, queue)

                        print(
                            f"[RESEARCH IDLE START] Starting queued task: '{next_task['query']}'",
                            flush=True,
                        )
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

    _lifespan_tasks.append(asyncio.create_task(_idle_research_loop()))
    print(
        f"  {_GRN}Deep Research:{_RST} idle loop started "
        f"(threshold={getattr(cfg, 'RESEARCH_IDLE_THRESHOLD', 1800) // 60}m)"
    )

    # Idle-time memory refresh loop - runs during deep idle periods (45m+)
    async def _idle_memory_refresh_loop():
        """Background loop that periodically enqueues memory refresh during deep idle periods."""
        last_enqueued_time = 0
        while True:
            await asyncio.sleep(300)  # Check every 5 minutes
            importlib.reload(cfg)
            idle_seconds = _get_current_idle_seconds()
            if idle_seconds >= 2700 and time.time() - last_enqueued_time >= 7200:
                task_manager.enqueue_idle_task("refresh_memory")
                last_enqueued_time = time.time()

    _lifespan_tasks.append(asyncio.create_task(_idle_memory_refresh_loop()))
    print(f"  {_GRN}Mem Refresher:{_RST} idle timer started (threshold=45m, limit=2h)")

    # Idle-time profile evolution loop (Hermes Tier 3 #12)
    # Wakes every 10 minutes to check idle state. The per-document 24-hour
    # cooldown is enforced inside run_profile_evolution(), not here.
    async def _idle_profile_evolution_loop():
        """Background loop that periodically enqueues persona profile evolution."""
        while True:
            await asyncio.sleep(600)  # Check every 10 minutes
            importlib.reload(cfg)
            if not getattr(cfg, "PROFILE_EVOLUTION_ENABLED", False):
                continue
            idle_seconds = _get_current_idle_seconds()
            threshold = getattr(cfg, "PROFILE_EVOLUTION_IDLE_THRESHOLD", 3600)
            if idle_seconds >= threshold:
                task_manager.enqueue_idle_task("profile_evolver")

    _lifespan_tasks.append(asyncio.create_task(_idle_profile_evolution_loop()))
    print(
        f"  {_GRN}Profile Evolver:{_RST} idle timer started (threshold=60m, cooldown=24h/doc)"
    )

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
                if task_manager.should_yield("tag_librarian"):
                    print("[TAG LIBRARIAN] Yielding to peer task in queue.", flush=True)
                    task_manager.enqueue_idle_task("tag_librarian")
                    break
                res = await asyncio.to_thread(tag_librarian.audit_single_document)
                print(
                    f"{_GRN}[TAG LIBRARIAN]{_RST} Audit pass {i + 1}/{batch_size} result: {res}",
                    flush=True,
                )
                if res.get("status") in ("empty", "error"):
                    break

            # Periodically maintain master taxonomy to purge zero-usage orphan tags
            m_res = await asyncio.to_thread(tag_librarian.maintain_master_taxonomy)
            if m_res.get("removed_master_tags", 0) > 0:
                print(
                    f"{_GRN}[TAG LIBRARIAN]{_RST} Taxonomy maintenance pruned {m_res['removed_master_tags']} orphan tags.",
                    flush=True,
                )
        except (sqlite3.Error, OSError, ValueError, KeyError, RuntimeError) as e:
            print(f"[TAG LIBRARIAN] Error during audit pass: {e}", flush=True)
            task_manager.clear_running("tag_librarian", status="error", error=str(e))
        finally:
            if task_manager.get_status("tag_librarian") == "running":
                task_manager.clear_running("tag_librarian", status="idle")

    async def _idle_tag_librarian_loop():
        """Background loop that periodically enqueues Tag Librarian audit."""
        while True:
            await asyncio.sleep(600)  # Check every 10 minutes
            importlib.reload(cfg)
            if not getattr(cfg, "TAG_LIBRARIAN_ENABLED", False):
                continue
            idle_seconds = _get_current_idle_seconds()
            threshold = getattr(cfg, "TAG_LIBRARIAN_IDLE_THRESHOLD", 2700)
            if idle_seconds >= threshold:
                task_manager.enqueue_idle_task("tag_librarian")

    _lifespan_tasks.append(asyncio.create_task(_idle_tag_librarian_loop()))
    print(
        f"  {_GRN}Tag Librarian:{_RST} idle loop started (threshold=45m, limit=1 doc/run)"
    )

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
                    print(
                        f"{_GRN}[GCAL SYNC]{_RST} Auto-sync successful: {result['message']}",
                        flush=True,
                    )
                elif result.get("status") == "offline" and cfg.DEBUG_LOGGING:
                    # Only print if debug logging is enabled or on config change
                    print(
                        f"{_GRN}[GCAL SYNC]{_RST} Auto-sync fallback to cache: {result['message']}",
                        flush=True,
                    )
            except (httpx.HTTPError, sqlite3.Error, OSError, ValueError, KeyError, RuntimeError) as e:
                print(f"{_RED}[GCAL SYNC ERROR]{_RST} {e}", flush=True)

            # Run every 30 minutes
            await asyncio.sleep(1800)

    _lifespan_tasks.append(asyncio.create_task(_gcal_sync_loop()))
    print(f"  {_GRN}GCal Syncer:{_RST} periodic loop started (interval=30m)")

    # Periodic Google Tasks auto-sync loop
    async def _gtasks_sync_loop():
        """Periodic background task that pulls tasks from Google Tasks and caches them.
        Runs on startup and then every 30 minutes.
        """
        await asyncio.sleep(12)  # Brief warm-up delay on startup
        while True:
            try:
                import gtasks_sync

                result = await asyncio.to_thread(gtasks_sync.sync_gtasks)
                if result.get("status") == "success":
                    print(
                        f"{_GRN}[GTASKS SYNC]{_RST} Auto-sync successful: {result['message']}",
                        flush=True,
                    )
                elif result.get("status") == "offline" and cfg.DEBUG_LOGGING:
                    print(
                        f"{_GRN}[GTASKS SYNC]{_RST} Auto-sync fallback to cache: {result['message']}",
                        flush=True,
                    )
            except (httpx.HTTPError, sqlite3.Error, OSError, ValueError, KeyError, RuntimeError) as e:
                print(f"{_RED}[GTASKS SYNC ERROR]{_RST} {e}", flush=True)

            # Run every 30 minutes
            await asyncio.sleep(1800)

    _lifespan_tasks.append(asyncio.create_task(_gtasks_sync_loop()))
    print(f"  {_GRN}GTasks Syncer:{_RST} periodic loop started (interval=30m)")

    # Periodic Google Drive & Health Connect auto-sync loop
    async def _gdrive_sync_loop():
        """Periodic background task that checks Google Drive for Health Connect exports and syncs the DB.
        Runs on startup and then every 2 hours.
        """
        await asyncio.sleep(15)  # Brief warm-up delay on startup
        while True:
            try:
                import gdrive_sync

                result = await asyncio.to_thread(
                    gdrive_sync.sync_health_connect_from_drive
                )
                if result.get("status") == "success":
                    action = result.get("action", "")
                    if action == "downloaded" or cfg.DEBUG_LOGGING:
                        print(
                            f"{_GRN}[GDRIVE SYNC]{_RST} {result['message']}", flush=True
                        )
                elif cfg.DEBUG_LOGGING:
                    print(
                        f"{_YEL}[GDRIVE SYNC]{_RST} {result.get('message')}", flush=True
                    )
            except (httpx.HTTPError, sqlite3.Error, OSError, ValueError, KeyError, RuntimeError) as e:
                print(f"{_RED}[GDRIVE SYNC ERROR]{_RST} {e}", flush=True)

            # Check every 2 hours (7200s)
            await asyncio.sleep(7200)

    _lifespan_tasks.append(asyncio.create_task(_gdrive_sync_loop()))
    print(f"  {_GRN}GDrive Syncer:{_RST} periodic loop started (interval=2h)")

    # Autonomous Temporal Heartbeat loop (evaluates imminent/overdue tasks & events)
    async def _temporal_heartbeat_loop():
        """Periodic background task evaluating time thresholds every 60s for autonomous alerts."""
        await asyncio.sleep(10)  # Brief warm-up delay on startup
        while True:
            try:
                import task_manager

                # Do not trigger during active chat generation or preemption
                if not task_manager.is_chat_preempted():
                    con = get_db()
                    try:
                        triggers = time_manager.evaluate_heartbeat(con)
                        for trigger in triggers:
                            print(
                                f"{_YEL}[TEMPORAL HEARTBEAT ALERT]{_RST} {trigger['type'].upper()}: {trigger['details']}",
                                flush=True,
                            )
                    finally:
                        con.close()
            except (sqlite3.Error, OSError, ValueError, KeyError, RuntimeError) as e:
                if cfg.DEBUG_LOGGING:
                    print(f"{_RED}[TEMPORAL HEARTBEAT ERROR]{_RST} {e}", flush=True)

            # Tick every 60 seconds
            await asyncio.sleep(60)

    _lifespan_tasks.append(asyncio.create_task(_temporal_heartbeat_loop()))
    print(f"  {_GRN}Temporal Heartbeat:{_RST} periodic loop started (interval=60s)")

    yield

    # Shutdown phase: cancel all background async tasks cleanly before shutting down tasks
    print(
        f"[SERVER SHUTDOWN] Cancelling {len(_lifespan_tasks)} background lifespan task(s)...",
        flush=True,
    )
    for t in _lifespan_tasks:
        t.cancel()
    if _lifespan_tasks:
        await asyncio.gather(*_lifespan_tasks, return_exceptions=True)

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

# Serve media attachments directly via the main server
os.makedirs(cfg.ATTACHMENTS_DIR, exist_ok=True)
app.mount(
    "/attachments", StaticFiles(directory=cfg.ATTACHMENTS_DIR), name="attachments"
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/status")
async def status(_: None = Depends(check_auth)):
    """Return server health and active config (model, context size, think mode, engine version)."""
    from Evelyn.tools import db_migrator

    return {
        "status": "ok",
        "engine_version": getattr(cfg, "__version__", "000.004.000"),
        "version_name": getattr(
            cfg, "VERSION_NAME", "Sanctum Architecture & Guardrails"
        ),
        "db_versions": {k: db_migrator.get_db_version(k) for k in db_migrator.DB_MAP},
        "model": cfg.MODEL_NAME,
        "think": cfg.THINK,
        "think_tool_loop": cfg.THINK_TOOL_LOOP,
        "think_self_elect": getattr(cfg, "THINK_SELF_ELECT", True),
        "debug": cfg.DEBUG_LOGGING,
        "num_ctx": cfg.NUM_CTX,
    }


class MediaUpdateRequest(BaseModel):
    """Payload for updating user metadata and tags on a media asset."""

    description: str | None = None
    tags: list[str] | str | None = None
    taxonomy_domain: str | None = None


@app.get("/api/media/{guid}")
async def get_media_endpoint(guid: str, _: None = Depends(check_auth)):
    """Fetch metadata and paths for a media asset by GUID."""
    from Evelyn.tools import media_db

    asset = media_db.get_media_asset(guid)
    if not asset:
        raise HTTPException(status_code=404, detail="Media asset not found")
    return asset


@app.patch("/api/media/{guid}")
@app.post("/api/media/{guid}")
async def update_media_endpoint(
    guid: str, req: MediaUpdateRequest, _: None = Depends(check_auth)
):
    """Update description, tags, domain for a media asset and re-index into ChromaDB."""
    import evelyn_config as cfg
    from Evelyn.tools import chroma_rag, media_db

    asset = media_db.get_media_asset(guid)
    if not asset:
        raise HTTPException(status_code=404, detail="Media asset not found")

    tags_list = None
    if req.tags is not None:
        if isinstance(req.tags, list):
            tags_list = req.tags
        elif isinstance(req.tags, str):
            tags_list = [t.strip() for t in req.tags.split(",") if t.strip()]

    desc = req.description if req.description is not None else asset.get("description")
    domain = (
        req.taxonomy_domain
        if req.taxonomy_domain is not None
        else asset.get("taxonomy_domain")
    )

    media_db.update_media_metadata(
        guid=guid,
        description=desc,
        tags=tags_list if tags_list is not None else asset.get("tags"),
        taxonomy_domain=domain,
    )

    # Re-fetch updated asset to sync with ChromaDB
    updated = media_db.get_media_asset(guid)
    if updated:
        tags_str = ", ".join(updated.get("tags", []))
        ocr_text = updated.get("extracted_text") or ""
        ocr_snippet = (ocr_text[:200] + "...") if len(ocr_text) > 200 else ocr_text

        doc_text = (
            f"[Image Asset: {guid}]\n"
            f"Domain: {updated.get('taxonomy_domain', 'General/Media')}\n"
            f"Tags: {tags_str}\n"
            f"Description: {updated.get('description', '')}"
        )
        if ocr_snippet:
            doc_text += f"\nVisible Text: {ocr_snippet}"

        meta_json = updated.get("metadata_json") or {}
        if isinstance(meta_json, str):
            try:
                meta_json = json.loads(meta_json)
            except (json.JSONDecodeError, TypeError):
                meta_json = {}

        exif_details = []
        if meta_json.get("datetimeoriginal") or meta_json.get("datetime"):
            dt = meta_json.get("datetimeoriginal") or meta_json.get("datetime")
            exif_details.append(f"Taken: {dt}")
        if meta_json.get("camera_make") or meta_json.get("camera_model"):
            cam = f"{meta_json.get('camera_make', '')} {meta_json.get('camera_model', '')}".strip()
            exif_details.append(f"Camera: {cam}")
        if meta_json.get("gps") and isinstance(meta_json["gps"], dict):
            gps = meta_json["gps"]
            lat = gps.get("latitude")
            lon = gps.get("longitude")
            if lat is not None and lon is not None and (lat != 0 or lon != 0):
                exif_details.append(f"GPS: ({lat}, {lon})")

        if exif_details:
            doc_text += f"\nEXIF: {', '.join(exif_details)}"

        extra_meta = {
            "guid": guid,
            "media_type": updated.get("media_type", "image"),
            "file_path": updated.get("file_path", ""),
            "domain": updated.get("taxonomy_domain", "General/Media"),
            "created_ts": updated.get("created_ts", 0),
        }
        if meta_json.get("gps") and isinstance(meta_json["gps"], dict):
            gps = meta_json["gps"]
            if "latitude" in gps and "longitude" in gps:
                lat_f = float(gps["latitude"])
                lon_f = float(gps["longitude"])
                if lat_f != 0 or lon_f != 0:
                    extra_meta["latitude"] = lat_f
                    extra_meta["longitude"] = lon_f

        chroma_rag.enqueue_upsert(
            source_path=f"media::{guid}",
            content=doc_text,
            extra_metadata=extra_meta,
            collection_name=cfg.CHROMA_MEDIA_COLLECTION,
        )

    return {"status": "ok", "asset": updated}


@app.post("/chat")
async def chat(req: ChatRequest, request: Request, _: None = Depends(check_auth)):
    """Accept a user message and return a Server-Sent Events stream of the response.

    Args:
        req: The chat request object containing the user message and optional image attachments.
        request: FastAPI Request object for disconnect detection.
        _: Authentication dependency placeholder.

    Returns:
        StreamingResponse: An SSE stream of the assistant's response.
    """
    ui_override = req.think is not None
    think_effort = req.think if ui_override else classify_message_effort(req.message)
    return StreamingResponse(
        chat_stream(
            req.message,
            images=req.images,
            think_effort=think_effort,
            ui_override=ui_override,
            request=request,
        ),
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
        chat_stream(
            user_message, is_regenerate=True, think_effort=think_effort, request=request
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/edit")
async def edit_message(
    req: EditRequest, request: Request, _: None = Depends(check_auth)
):
    """Update the content of the last user message and re-generate a response."""
    user_message = edit_last_user_message(req.message)
    if not user_message:
        raise HTTPException(status_code=400, detail="No user message to edit.")
    think_effort = classify_message_effort(user_message)
    return StreamingResponse(
        chat_stream(
            user_message, is_regenerate=True, think_effort=think_effort, request=request
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/chat/stop")
async def stop_chat(
    req: StopChatRequest | None = None, _: None = Depends(check_auth)
):
    """Safely stop an active chat generation session."""
    stream_id = req.stream_id if req else None
    session = (
        stream_registry.get(stream_id) if stream_id else stream_registry.get_active()
    )
    if not session or session.status != "running":
        return {"status": "noop", "message": "No active running stream to stop"}

    session.is_cancelled = True
    session.status = "stopped"
    if session.task and not session.task.done():
        session.task.cancel()

    # Subprocess termination cascade: terminate any active tool child processes
    try:
        from Evelyn.tools import task_manager

        task_manager.terminate_all_subprocesses(grace_period=1.0)
    except (subprocess.SubprocessError, psutil.Error, OSError, RuntimeError) as e:
        dlog(f"Subprocess termination notice on stop: {e}")

    session.push_chunk(f"data: {json.dumps({'type': 'stopped'})}\n\n")
    session.mark_complete(status="stopped")
    return {"status": "stopped", "stream_id": session.stream_id}


@app.get("/chat/stream/{stream_id}")
async def get_chat_stream(
    stream_id: str, request: Request, after: int = -1, _: None = Depends(check_auth)
):
    """Attach to an active or recently completed stream session and replay missed chunks."""
    session = stream_registry.get(stream_id)
    if not session:
        raise HTTPException(
            status_code=404, detail="Stream session not found or expired"
        )
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
    row = con.execute(
        "SELECT MAX(id) as max_id FROM messages WHERE content != ''"
    ).fetchone()
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
            SELECT m.id, m.role, m.content, m.thinking, m.tools_used, m.tool_metadata, m.ts,
                   mm.prompt_eval_count, mm.eval_count, mm.think_effort, mm.think_source
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
            SELECT m.id, m.role, m.content, m.thinking, m.tools_used, m.tool_metadata, m.ts,
                   mm.prompt_eval_count, mm.eval_count, mm.think_effort, mm.think_source
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

    from Evelyn.tools import media_db

    msg_ids = [r["id"] for r in rows]
    feedback_map = get_feedback_for_messages(msg_ids)
    messages_out = []
    for r in rows:
        d = dict(r)
        d["feedback"] = feedback_map.get(d["id"])
        try:
            d["attachments"] = media_db.get_media_for_message(d["id"])
        except (sqlite3.Error, OSError):
            d["attachments"] = []
        messages_out.append(d)
    return messages_out


@app.post("/chat/feedback")
async def post_chat_feedback(req: FeedbackRequest, _: None = Depends(check_auth)):
    """Save or update user upvote/downvote rating on a message."""
    result = save_or_update_feedback(req.message_id, req.rating, req.feedback)
    return {"status": "ok", "feedback": result}


@app.get("/chat/feedback/{message_id}")
async def get_chat_feedback(message_id: int, _: None = Depends(check_auth)):
    """Get user feedback for a specific message."""
    feedbacks = get_feedback_for_messages([message_id])
    return {"message_id": message_id, "feedback": feedbacks.get(message_id)}


@app.get("/telemetry/rag")
async def get_rag_telemetry(
    limit: int = 50,
    offset: int = 0,
    days: float | None = None,
    _: None = Depends(check_auth),
):
    """Get recent RAG retrieval events with similarity scores and source paths."""
    from Evelyn.tools.chroma_rag import get_recent_rag_telemetry

    events = await asyncio.to_thread(get_recent_rag_telemetry, limit, offset, days)
    return {"status": "ok", "count": len(events), "events": events, "days": days}


@app.get("/telemetry/feedback")
async def get_feedback_telemetry(
    limit: int = 50, days: float | None = None, _: None = Depends(check_auth)
):
    """Get aggregate feedback metrics and recent rated messages."""
    con = get_db()
    try:
        cur = con.cursor()
        if days is not None and days > 0:
            cutoff = time.time() - (days * 86400.0)
            total_rated = cur.execute(
                "SELECT COUNT(*) FROM message_feedback WHERE created_at >= ?", (cutoff,)
            ).fetchone()[0]
            upvotes = cur.execute(
                "SELECT COUNT(*) FROM message_feedback WHERE rating > 0 AND created_at >= ?",
                (cutoff,),
            ).fetchone()[0]
            downvotes = cur.execute(
                "SELECT COUNT(*) FROM message_feedback WHERE rating < 0 AND created_at >= ?",
                (cutoff,),
            ).fetchone()[0]

            recent_rows = cur.execute(
                """
                SELECT mf.id, mf.message_id, mf.rating, mf.feedback, mf.created_at, mf.updated_at,
                       m.content, m.thinking, m.ts, m.tools_used,
                       mm.think_effort, mm.think_source
                FROM message_feedback mf
                JOIN messages m ON mf.message_id = m.id
                LEFT JOIN message_metrics mm ON mf.message_id = mm.message_id
                WHERE mf.created_at >= ?
                ORDER BY mf.id DESC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
        else:
            total_rated = cur.execute(
                "SELECT COUNT(*) FROM message_feedback"
            ).fetchone()[0]
            upvotes = cur.execute(
                "SELECT COUNT(*) FROM message_feedback WHERE rating > 0"
            ).fetchone()[0]
            downvotes = cur.execute(
                "SELECT COUNT(*) FROM message_feedback WHERE rating < 0"
            ).fetchone()[0]

            recent_rows = cur.execute(
                """
                SELECT mf.id, mf.message_id, mf.rating, mf.feedback, mf.created_at, mf.updated_at,
                       m.content, m.thinking, m.ts, m.tools_used,
                       mm.think_effort, mm.think_source
                FROM message_feedback mf
                JOIN messages m ON mf.message_id = m.id
                LEFT JOIN message_metrics mm ON mf.message_id = mm.message_id
                ORDER BY mf.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        satisfaction_pct = (
            round((upvotes / total_rated * 100), 1) if total_rated > 0 else None
        )

        return {
            "status": "ok",
            "total_rated": total_rated,
            "upvotes": upvotes,
            "downvotes": downvotes,
            "satisfaction_rate": satisfaction_pct,
            "recent_ratings": [dict(r) for r in recent_rows],
            "days": days,
        }
    finally:
        con.close()


@app.get("/telemetry/thinking")
async def get_thinking_telemetry(limit: int = 50, _: None = Depends(check_auth)):
    """Get thinking effort metrics, distribution breakdown, and recent resolution records."""
    con = get_db()
    try:
        cur = con.cursor()
        total_tracked = cur.execute(
            "SELECT COUNT(*) FROM message_metrics WHERE think_effort IS NOT NULL"
        ).fetchone()[0]

        effort_counts = dict(
            cur.execute(
                """
                SELECT think_effort, COUNT(*)
                FROM message_metrics
                WHERE think_effort IS NOT NULL
                GROUP BY think_effort
                """
            ).fetchall()
        )

        source_counts = dict(
            cur.execute(
                """
                SELECT think_source, COUNT(*)
                FROM message_metrics
                WHERE think_source IS NOT NULL
                GROUP BY think_source
                """
            ).fetchall()
        )

        recent_records = cur.execute(
            """
            SELECT mm.id, mm.message_id, mm.think_effort, mm.think_source,
                   mm.prompt_eval_count, mm.eval_count, mm.eval_duration, mm.total_duration,
                   m.role, m.content, m.ts, m.tools_used
            FROM message_metrics mm
            JOIN messages m ON mm.message_id = m.id
            WHERE mm.think_effort IS NOT NULL
            ORDER BY mm.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        return {
            "status": "ok",
            "total_tracked": total_tracked,
            "effort_breakdown": effort_counts,
            "source_breakdown": source_counts,
            "recent_records": [dict(r) for r in recent_records],
        }
    finally:
        con.close()


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

        from Evelyn.tools.journal_manager import JOURNAL_DIR, PENDING_DIR

        filename = id if id.endswith(".md") else f"{id}.md"
        filename = os.path.basename(filename)

        # 1. Try structured vault folder: Journal Entries/YYYY/MM-ShortMonth/Journal Entry YYYY-MM-DD.md
        m = re.search(r"Journal Entry (\d{4})-(\d{2})-\d{2}\.md", filename)
        if m:
            year = m.group(1)
            month_num = m.group(2)
            import datetime

            try:
                month_dt = datetime.date(int(year), int(month_num), 1)
                month_name = month_dt.strftime("%b")
                struct_path = os.path.join(
                    JOURNAL_DIR,
                    "Journal Entries",
                    year,
                    f"{month_num}-{month_name}",
                    filename,
                )
                if os.path.exists(struct_path):
                    content = await asyncio.to_thread(_server_sync_read, struct_path)
                    return {"content": content, "status": "approved"}
            except (OSError, ValueError):
                pass

        # 2. Try vault root path — written directly (JOURNAL_DIRECT_WRITE=True) but not yet
        # filed into the structured subfolder.  Return "unfiled" so the modal shows the
        # approve/file button rather than the "already approved" badge.
        root_path = os.path.join(JOURNAL_DIR, filename)
        if os.path.exists(root_path):
            content = await asyncio.to_thread(_server_sync_read, root_path)
            return {"content": content, "status": "unfiled"}

        # 3. Try pending folder (legacy — JOURNAL_DIRECT_WRITE=False mode)
        pending_path = os.path.join(PENDING_DIR, filename)
        if os.path.exists(pending_path):
            content = await asyncio.to_thread(_server_sync_read, pending_path)
            return {"content": content, "status": "pending"}

        # Fallback to journal_manager read
        from Evelyn.tools.journal_manager import read_journal_entry

        m = re.search(r"Journal Entry ([0-9\-]+)\.md", filename)
        if m:
            content = read_journal_entry(m.group(1))
            return {"content": content, "status": "unknown"}
        else:
            raise HTTPException(status_code=400, detail="Invalid journal ID")
    elif type == "research":
        import os
        import re

        import evelyn_config as cfg

        safe_id = re.sub(r"[^a-zA-Z0-9_\-]+", "-", id).strip("-")
        report_path = os.path.join(cfg.RESEARCH_VAULT_DIR, f"{safe_id}.md")
        if os.path.exists(report_path):
            content = await asyncio.to_thread(_server_sync_read, report_path)
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
                                    sdata = await asyncio.to_thread(_server_sync_load_json, state_path)
                                    if (
                                        sdata.get("task_id") == id
                                        or re.sub(
                                            r"[^a-zA-Z0-9_\-]+",
                                            "-",
                                            sdata.get("query", "").lower(),
                                        ).strip("-")
                                        == safe_id
                                    ):
                                        content = await asyncio.to_thread(_server_sync_read, rep_path)
                                        return {"content": content}
                                except (OSError, json.JSONDecodeError, ValueError):
                                    pass
            raise HTTPException(status_code=404, detail="Research report not found")
    else:
        raise HTTPException(status_code=400, detail="Unknown artifact type")


@app.get("/ambient/feed")
async def get_ambient_feed_endpoint(
    limit: int = 10,
    type: str | None = None,
    _: None = Depends(check_auth),
):
    """Retrieve active (undismissed) ambient impressions (thoughts, media shares, alerts) for the UI."""
    from Evelyn.tools import memory_db

    items = memory_db.get_active_ambient_feed(limit=limit, type_filter=type)
    return {"status": "ok", "items": items, "count": len(items)}


class DismissAmbientRequest(BaseModel):
    """Pydantic model representing a request to dismiss an ambient impression in the UI."""

    id: int


@app.post("/ambient/dismiss")
async def dismiss_ambient_endpoint(
    req: DismissAmbientRequest,
    _: None = Depends(check_auth),
):
    """Mark an ambient impression as dismissed in the UI."""
    from Evelyn.tools import memory_db

    success = memory_db.mark_ambient_impression_dismissed(req.id)
    return {"status": "ok", "updated": success}


@app.get("/thought_bubble")
async def get_thought_bubble_endpoint(_: None = Depends(check_auth)):
    """Fast single-thought endpoint for backwards-compatible UI ambient chip."""
    from Evelyn.tools import memory_db

    item = memory_db.get_latest_ambient_impression(type_filter="thought")
    return {"status": "ok", "latest_thought": item}


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
    import datetime
    import os
    import re
    import shutil

    from Evelyn.tools.journal_manager import JOURNAL_DIR, PENDING_DIR

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
            m_check = re.search(r"Journal Entry (\d{4})-(\d{2})-\d{2}\.md", filename)
            if m_check:
                try:
                    chk_year = m_check.group(1)
                    chk_month = m_check.group(2)
                    chk_dt = datetime.date(int(chk_year), int(chk_month), 1)
                    chk_name = chk_dt.strftime("%b")
                    struct_path = os.path.join(
                        JOURNAL_DIR,
                        "Journal Entries",
                        chk_year,
                        f"{chk_month}-{chk_name}",
                        filename,
                    )
                    if os.path.exists(struct_path):
                        return {
                            "status": "already_approved",
                            "destination": struct_path,
                        }
                except (OSError, ValueError):
                    pass
            raise HTTPException(
                status_code=404,
                detail="Journal entry file not found in pending or vault root",
            )

    m = re.search(r"Journal Entry (\d{4})-(\d{2})-(\d{2})\.md", filename)
    if not m:
        raise HTTPException(status_code=400, detail="Invalid journal filename format")

    year = m.group(1)
    month_num = m.group(2)
    month_dt = datetime.date(int(year), int(month_num), 1)
    month_name = month_dt.strftime("%b")  # e.g. "May"

    target_dir = os.path.join(
        JOURNAL_DIR, "Journal Entries", year, f"{month_num}-{month_name}"
    )
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, filename)

    try:
        await asyncio.to_thread(shutil.move, source_path, target_path)
        print(
            f"[JOURNAL APPROVE] Moved {filename} to structured vault path: {target_path}",
            flush=True,
        )
        await start_refresh_memory_internal()
        return {"status": "success", "destination": target_path}
    except (OSError, RuntimeError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to move journal file: {e}") from e


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
        import json
        import os

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
                            with open(state_file, encoding="utf-8") as f:
                                state = json.load(f)
                            status = state.get("status")

                            if status == "resolved":
                                import shutil

                                shutil.rmtree(task_dir, ignore_errors=True)
                                print(
                                    f"[RESEARCH CLEANUP] Auto-cleared legacy resolved task {d} from disk on startup.",
                                    flush=True,
                                )
                                continue

                            if status in ("paused", "running", "error"):
                                target_status = status

                                # Layer 4: If status was 'running', check engine.pid to
                                # distinguish a genuine orphan from a server restart.
                                if status == "running":
                                    from Evelyn.tools.evelyn_tools import (
                                        _is_research_engine_running,
                                    )

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
                                        with open(
                                            state_file, "w", encoding="utf-8"
                                        ) as fw:
                                            json.dump(state, fw, indent=2)

                                _background_tasks[d] = {
                                    "status": target_status,
                                    "query": state.get("query", ""),
                                    "scope": state.get("scope", "standard"),
                                    "started_at": os.path.getmtime(state_file),
                                }
                                print(
                                    f"[RESEARCH RECOVERY] Registered {target_status} task {d} from disk.",
                                    flush=True,
                                )
                        except (OSError, json.JSONDecodeError, ValueError):
                            pass
    except (OSError, ValueError) as e:
        print(
            f"[RESEARCH RECOVERY ERROR] Failed to load existing tasks: {e}", flush=True
        )


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

    import ingest_obsidian_knowledge
    import task_manager

    # Free Ollama before a heavy background operation starts
    cancel_pending_consolidation()
    cancel_pending_extraction()
    cancel_pending_evolution()

    task_manager.set_running("sync", phase="Starting Chroma Sync...")

    def _run():
        """Run sync phases in a daemon thread and update the task registry."""
        try:
            print(
                f"{_GRN}[SYNC]{_RST} Manual sync triggered via /sync endpoint",
                flush=True,
            )
            task_manager.set_running("sync", phase="Syncing Core Knowledge...")
            ingest_obsidian_knowledge.main()
            task_manager.clear_running(
                "sync", status="done", summary="Chroma Sync completed successfully."
            )
            print(f"{_GRN}[SYNC]{_RST} Complete.", flush=True)
        except (sqlite3.Error, OSError, ValueError, RuntimeError) as e:
            task_manager.clear_running("sync", status="error", error=str(e))
            print(f"{_RED}[SYNC ERROR]{_RST} {e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "sync started"}


@app.post("/vault_map")
async def trigger_vault_map(_: None = Depends(check_auth)):
    """Regenerate the Obsidian vault map in the background (no chat turn required).

    Cancels any in-flight consolidation or extraction tasks before starting.
    """
    import subprocess
    import sys
    import threading

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
            print(
                f"{_GRN}[VAULT MAP]{_RST} Regeneration triggered via /vault_map endpoint",
                flush=True,
            )
            result = subprocess.run(
                [sys.executable, "-u", script],
                stdout=sys.stdout,
                stderr=sys.stderr,
                cwd=str(BASE_DIR),
            )
            if result.returncode == 0:
                task_manager.clear_running("vault_map", status="done")
                print(f"{_GRN}[VAULT MAP]{_RST} Done.", flush=True)
            else:
                task_manager.clear_running(
                    "vault_map", status="error", error=f"Exit code {result.returncode}"
                )
                print(
                    f"{_RED}[VAULT MAP ERROR]{_RST} Process exited with code {result.returncode}",
                    flush=True,
                )
        except (subprocess.SubprocessError, psutil.Error, OSError, RuntimeError) as e:
            task_manager.clear_running("vault_map", status="error", error=str(e))
            print(f"{_RED}[VAULT MAP ERROR]{_RST} {e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "vault map generation started"}


# Phase display labels for the /refresh_memory endpoint's stdout parser.
# Module-level constant — no need to recreate on every request.
_REFRESH_PHASE_LABELS = {
    "vault_map": "Mapping Obsidian Vault...",
    "ingest_knowledge": "Ingesting Core Knowledge...",
}


async def start_refresh_memory_internal():
    """Trigger the unified Memory Refresh pipeline as an async background task.
    Safely ignores the run if another refresh is already running to avoid overlap.
    """
    if _background_tasks.get("refresh_memory", {}).get("status") == "running":
        print(
            f"{_GRN}[REFRESH]{_RST} Memory refresh is already running; skipping redundant trigger.",
            flush=True,
        )
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
                sys.executable,
                "-u",
                script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(BASE_DIR),
            )

            if proc.stdout:
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
                            task_manager.set_running(
                                "vault_map", phase="Mapping Obsidian Vault..."
                            )
                        elif key == "ingest_knowledge":
                            task_manager.set_running("sync", phase="Syncing Chroma DB...")
                    elif line.startswith("[PHASE_DONE:"):
                        key = line.split("[PHASE_DONE:")[1].split("]")[0]
                        if key == "vault_map":
                            task_manager.clear_running("vault_map", status="done")
                        elif key == "ingest_knowledge":
                            task_manager.clear_running("sync", status="done")
                    elif line.startswith("[PHASE_FAIL:"):
                        key = line.split("[PHASE_FAIL:")[1].split("]")[0]
                        if key == "vault_map":
                            task_manager.clear_running(
                                "vault_map", status="error", error=f"Phase '{key}' failed."
                            )
                        elif key == "ingest_knowledge":
                            task_manager.clear_running(
                                "sync", status="error", error=f"Phase '{key}' failed."
                            )
                        raise RuntimeError(f"Phase '{key}' failed.")

            await proc.wait()

            if proc.returncode == 0:
                task_manager.clear_running("refresh_memory", status="done")
                task_manager.clear_running("vault_map", status="done")
                task_manager.clear_running("sync", status="done")
                if "refresh_memory" in _background_tasks:
                    _background_tasks["refresh_memory"]["phase"] = (
                        "Completed successfully."
                    )
                print(f"{_GRN}[REFRESH]{_RST} All phases done.", flush=True)
            else:
                raise RuntimeError(f"Pipeline exited with code {proc.returncode}")

        except (subprocess.SubprocessError, OSError, RuntimeError) as e:
            task_manager.clear_running("refresh_memory", status="error", error=str(e))
            if "refresh_memory" in _background_tasks:
                _background_tasks["refresh_memory"]["phase"] = "Failed."
            print(f"{_RED}[REFRESH ERROR]{_RST} {e}", flush=True)

    t_proc = asyncio.create_task(_run_subprocess())
    _server_background_tasks.add(t_proc)
    t_proc.add_done_callback(_server_background_tasks.discard)


@app.post("/refresh_memory")
async def trigger_refresh_memory(_: None = Depends(check_auth)):
    """Trigger the unified Memory Refresh pipeline as an async subprocess.

    Sequentially runs:
      Phase 1 — Vault Map generation (vault_indexer.py)
      Phase 2 — Knowledge ingest into Chroma evelyn_memory (ingest_obsidian_knowledge.py)

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
    intent_frame: str | None = None


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
        raise HTTPException(
            status_code=404, detail="Report not synthesized yet or task failed"
        )
    content = await asyncio.to_thread(_server_sync_read, report_file)
    return {"report": content}


@app.get("/research/list")
async def api_research_list(_: None = Depends(check_auth)):
    """List all research tasks sorted by creation date, merging in-progress and queued items."""
    import json
    import os

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
                with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
                    state = await asyncio.to_thread(_server_sync_load_json, state_file)
                    tasks.append(state)
                    if state.get("query"):
                        existing_queries.add(state["query"])

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
        words1 = {w for w in re.findall(r"\w+", q1.lower()) if len(w) > 3}
        words2 = {w for w in re.findall(r"\w+", q2.lower()) if len(w) > 3}
        if not words1 or not words2:
            return False
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        jaccard = len(intersection) / len(union)
        if jaccard >= 0.45:
            return True
        overlap_count = len(intersection)
        min_len = min(len(words1), len(words2))
        return bool(overlap_count >= 4 and overlap_count / min_len >= 0.75)

    # 2. Process queue.json, filtering duplicates and adding queued items
    queue_file = os.path.join(research_dir, "queue.json")
    if os.path.exists(queue_file):
        try:
            queue = await asyncio.to_thread(_server_sync_load_json, queue_file)

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
                    print(
                        f"[RESEARCH QUEUE] Automatically removing duplicate task from queue: '{q}'",
                        flush=True,
                    )
                    queue_changed = True
                else:
                    filtered_queue.append(item)

            # If we stripped out duplicates, write the sanitized queue back to disk
            if queue_changed:
                await asyncio.to_thread(_server_sync_dump_json, queue_file, filtered_queue)
                queue = filtered_queue

            # Add remaining queued items to the tasks list
            for idx, item in enumerate(queue):
                temp_id = f"queued_{idx}"
                tasks.append(
                    {
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
                        "orchestrator_turns": 0,
                    }
                )
        except (OSError, json.JSONDecodeError, ValueError) as e:
            print(
                f"[RESEARCH LIST ERROR] Failed to process queue.json: {e}", flush=True
            )

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
                queue = await asyncio.to_thread(_server_sync_load_json, queue_file)
                if 0 <= idx < len(queue):
                    removed = queue.pop(idx)
                    await asyncio.to_thread(_server_sync_dump_json, queue_file, queue)
                    print(
                        f"[RESEARCH QUEUE] Cancelled queued task: '{removed.get('query')}'",
                        flush=True,
                    )
                    return {"status": "cancelled", "task_id": task_id}
            raise HTTPException(
                status_code=404, detail="Queue file not found or index invalid"
            )
        except HTTPException:
            raise
        except (OSError, json.JSONDecodeError, ValueError) as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to cancel queued task: {e}"
            ) from e

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
    import os
    import shutil

    # 1. Handle queued task ID (e.g. queued_N)
    if task_id.startswith("queued_"):
        try:
            idx = int(task_id.split("_")[1])
            queue_file = os.path.join(cfg.RESEARCH_DATA_DIR, "queue.json")
            if os.path.exists(queue_file):
                queue = await asyncio.to_thread(_server_sync_load_json, queue_file)
                if 0 <= idx < len(queue):
                    removed = queue.pop(idx)
                    await asyncio.to_thread(_server_sync_dump_json, queue_file, queue)
                    print(
                        f"[RESEARCH QUEUE] Deleted queued task: '{removed.get('query')}'",
                        flush=True,
                    )
                    return {"status": "deleted", "task_id": task_id}
            raise HTTPException(status_code=404, detail="Queue item or file not found")
        except HTTPException:
            raise
        except (OSError, json.JSONDecodeError, ValueError) as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to delete queued task: {e}"
            ) from e

    # Terminate process immediately if active
    terminate_research_process(task_id)

    # 2. Handle actual task directories
    task_dir = os.path.join(cfg.RESEARCH_DATA_DIR, task_id)
    if not os.path.exists(task_dir):
        raise HTTPException(status_code=404, detail="Research task not found")

    await asyncio.sleep(0.5)  # Give the terminated process a moment to release handles
    delete_success = False
    for _attempt in range(3):
        try:
            shutil.rmtree(task_dir)
            delete_success = True
            break
        except OSError:
            await asyncio.sleep(1.0)

    if not delete_success:
        # Last ditch effort ignoring errors
        shutil.rmtree(task_dir, ignore_errors=True)

    # 3. Clean up server background task tracking
    if task_id in _background_tasks:
        del _background_tasks[task_id]

    print(
        f"[RESEARCH DELETE] Permanently deleted task folder and tracking: {task_id}",
        flush=True,
    )
    return {"status": "deleted", "task_id": task_id}


def _demote_running_task_if_any(promoting_task_id: str):
    """Automatically pause the currently running research task (if any) and mark it on disk
    with a contextual explanation that it was demoted due to a dashboard manual override.
    """
    running_task = next(
        (
            tid
            for tid, t in list(_background_tasks.items())
            if tid.startswith("task_")
            and t.get("status") == "running"
            and tid != promoting_task_id
        ),
        None,
    )
    if running_task:
        from research_engine import load_state, save_state

        state = load_state(running_task)
        if state and state.get("status") == "running":
            state["status"] = "paused"
            state["error"] = (
                f"Demoted: Suspended automatically because you manually promoted another research task ({promoting_task_id}) from the dashboard."
            )
            save_state(running_task, state)
            _background_tasks[running_task]["status"] = "paused"
            _background_tasks[running_task]["finished_at"] = time.time()
            terminate_research_process(running_task)
            print(
                f"[RESEARCH DEMOTION] Auto-paused task {running_task} because task {promoting_task_id} was promoted by the user.",
                flush=True,
            )


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
    new_question: str | None = None
    new_search_query: str | None = None


class SQRemoveRequest(BaseModel):
    """Pydantic model representing a request to remove a sub-question from a research task."""

    sq_id: str


class FinalizeGuidanceRequest(BaseModel):
    """Pydantic model representing a request to finalize guidance on a research task."""

    pass


@app.post("/research/guide/{task_id}")
async def api_guide_research(
    task_id: str, request: GuideRequest, _: None = Depends(check_auth)
):
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
async def api_remove_sub_question(
    task_id: str, request: SQRemoveRequest, _: None = Depends(check_auth)
):
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
async def api_guide_research_rewrite(
    task_id: str, request: SQRewriteRequest, _: None = Depends(check_auth)
):
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
        new_search_query=request.new_search_query,
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

            queue = await asyncio.to_thread(_server_sync_load_json, queue_file)

            if not (0 <= idx < len(queue)):
                raise HTTPException(status_code=404, detail="Queue index out of range")

            item = queue.pop(idx)
            await asyncio.to_thread(_server_sync_dump_json, queue_file, queue)

            query = item.get("query", "")
            scope = item.get("scope", "standard")
            print(
                f"[RESEARCH START-NOW] Dequeued and starting: '{query}' (scope={scope})",
                flush=True,
            )

            from evelyn_tools import start_research

            result = start_research(query, scope=scope, bypass_queue=True)
            return {"message": result}

        except HTTPException:
            raise
        except (OSError, json.JSONDecodeError, ValueError) as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to start queued task: {e}"
            ) from e

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
            async with httpx.AsyncClient(timeout=300) as client, client.stream(
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
        except (httpx.HTTPError, RuntimeError, OSError) as e:
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
            raise HTTPException(status_code=503, detail="TTS server is not running") from None
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=str(e)) from e
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
    except (OSError, RuntimeError, ValueError) as e:
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
    from Evelyn.tools import task_manager
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
            with contextlib.suppress(sqlite3.Error, OSError, ValueError, KeyError):
                from Evelyn.tools.profile_evolver import get_profile_evolution_statuses

                doc_statuses = get_profile_evolution_statuses()

        sub_status = task_data.get("sub_status")
        summary = task_data.get("summary")
        diagnostics = task_data.get("diagnostics")

        # Dynamic diagnostic enrichment if sub_status wasn't explicitly populated
        with contextlib.suppress(OSError, ValueError):
            if key == "extractor":
                state_path = str(BASE_DIR / "data" / "evelyn_extraction_state.json")
                last_id = 0
                if os.path.exists(state_path):
                    with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
                        st = await asyncio.to_thread(_server_sync_load_json, state_path)
                        last_id = st.get("last_extracted_id", 0)
                if sub_status and "last_extracted_id" in sub_status:
                    last_id = max(last_id, sub_status.get("last_extracted_id", 0))

                db_path = str(BASE_DIR / "data" / "evelyn_chat.db")
                backlog = 0
                max_msg_id = 0
                if os.path.exists(db_path):
                    conn = sqlite3.connect(db_path, timeout=1.0)
                    try:
                        cur = conn.cursor()
                        cur.execute("SELECT MAX(id) FROM messages")
                        row = cur.fetchone()
                        max_msg_id = row[0] if row and row[0] is not None else 0

                        cur.execute(
                            "SELECT COUNT(*) FROM messages WHERE id > ? AND role IN ('user', 'assistant')",
                            (last_id,),
                        )
                        backlog = cur.fetchone()[0]
                    finally:
                        conn.close()

                active_facts = 0
                mdb_path = str(BASE_DIR / "data" / "evelyn_memory.db")
                if os.path.exists(mdb_path):
                    mconn = sqlite3.connect(mdb_path, timeout=1.0)
                    try:
                        mcur = mconn.cursor()
                        mcur.execute("SELECT COUNT(*) FROM context_entries WHERE status='live'")
                        active_facts = mcur.fetchone()[0]
                    finally:
                        mconn.close()

                progress_pct = round((last_id / max_msg_id * 100), 1) if max_msg_id > 0 else 100.0
                sub_status = {
                    **(sub_status or {}),
                    "last_extracted_id": last_id,
                    "max_message_id": max_msg_id,
                    "unextracted_backlog": backlog,
                    "progress_pct": progress_pct,
                    "active_facts_count": active_facts,
                }
            elif key == "consolidator":
                scan_path = str(BASE_DIR / "data" / "evelyn_consolidation_offsets.json")
                scan_st = {}
                if os.path.exists(scan_path):
                    with contextlib.suppress(OSError, json.JSONDecodeError, ValueError):
                        raw_st = await asyncio.to_thread(_server_sync_load_json, scan_path)
                        if isinstance(raw_st, dict):
                            scan_st = {
                                k: v
                                for k, v in raw_st.items()
                                if re.match(r"^Cat(0[1-9]|1[0-6])-[UA]$", k)
                            }
                active_cat = task_data.get("phase") if status == "running" else None

                mdb_path = str(BASE_DIR / "data" / "evelyn_memory.db")
                total_active_facts = 0
                pending_proposals = 0
                if os.path.exists(mdb_path):
                    mconn = sqlite3.connect(mdb_path, timeout=1.0)
                    try:
                        mcur = mconn.cursor()
                        mcur.execute("SELECT COUNT(*) FROM context_entries WHERE status='live'")
                        total_active_facts = mcur.fetchone()[0]
                        mcur.execute(
                            "SELECT COUNT(*) FROM proposals WHERE type IN ('merge', 'split', 'recategorize') AND status='pending'"
                        )
                        pending_proposals = mcur.fetchone()[0]
                    finally:
                        mconn.close()

                last_scanned = sub_status.get("total_records", 0) if sub_status else 0
                proposals_written = sub_status.get("proposals_written", 0) if sub_status else 0
                recats_written = sub_status.get("recats_written", 0) if sub_status else 0

                sub_status = {
                    **(sub_status or {}),
                    "scan_state": scan_st,
                    "active_category": active_cat or (sub_status.get("active_category") if sub_status else None),
                    "total_active_facts": total_active_facts,
                    "tracked_categories": len(scan_st),
                    "total_categories": 32,
                    "pending_proposals": pending_proposals,
                    "last_run_scanned": last_scanned,
                    "proposals_written": proposals_written,
                    "recats_written": recats_written,
                }
            elif key == "procedure_consolidator":
                mdb = str(BASE_DIR / "data" / "evelyn_memory.db")
                proc_cnt = 0
                pending_proposals = 0
                if os.path.exists(mdb):
                    conn = sqlite3.connect(mdb, timeout=1.0)
                    try:
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(*) FROM procedures WHERE status='live'")
                        proc_cnt = cur.fetchone()[0]
                        cur.execute(
                            "SELECT COUNT(*) FROM proposals WHERE type='procedure_merge' AND status='pending'"
                        )
                        pending_proposals = cur.fetchone()[0]
                    except (sqlite3.Error, OSError):
                        pass
                    finally:
                        conn.close()

                last_audited = sub_status.get("total_procedures", 0) if sub_status else 0
                sub_status = {
                    **(sub_status or {}),
                    "total_live_procedures": proc_cnt,
                    "pending_proposals": pending_proposals,
                    "last_run_audited": last_audited,
                }
            elif key == "tag_librarian":
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
                        cur.execute(
                            "SELECT COUNT(*) FROM vault_documents WHERE last_tag_audit IS NOT NULL AND last_tag_audit > 0"
                        )
                        audited = cur.fetchone()[0]
                        cur.execute("SELECT COUNT(*) FROM vault_documents")
                        total = cur.fetchone()[0]
                    except (sqlite3.Error, OSError):
                        pass
                    finally:
                        conn.close()
                audit_pct = round((audited / total * 100), 1) if total > 0 else 0.0
                sub_status = {
                    **(sub_status or {}),
                    "master_tags": tags_cnt,
                    "audited_notes": audited,
                    "total_notes": total,
                    "audit_pct": audit_pct,
                }
            elif key == "sync":
                mdb = str(BASE_DIR / "data" / "evelyn_memory.db")
                facts_cnt = 0
                procs_cnt = 0
                sync_queue_cnt = 0
                if os.path.exists(mdb):
                    conn = sqlite3.connect(mdb, timeout=1.0)
                    try:
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(*) FROM context_entries WHERE status='live'")
                        facts_cnt = cur.fetchone()[0]
                        cur.execute("SELECT COUNT(*) FROM procedures")
                        procs_cnt = cur.fetchone()[0]
                        with contextlib.suppress(sqlite3.Error):
                            cur.execute("SELECT COUNT(*) FROM chroma_sync_queue WHERE status='pending'")
                            sync_queue_cnt = cur.fetchone()[0]
                    except (sqlite3.Error, OSError):
                        pass
                    finally:
                        conn.close()
                chroma_cnt = 0
                try:
                    cdb_path = str(BASE_DIR / "data" / "chroma_db" / "chroma.sqlite3")
                    if os.path.exists(cdb_path):
                        cconn = sqlite3.connect(
                            f"file:{cdb_path}?mode=ro", uri=True, timeout=1.0
                        )
                        try:
                            ccur = cconn.cursor()
                            ccur.execute("""
                                SELECT COUNT(e.id)
                                FROM collections c
                                JOIN segments s ON s.collection = c.id AND s.scope='METADATA'
                                LEFT JOIN embeddings e ON e.segment_id = s.id
                                WHERE c.name = 'evelyn_memory'
                            """)
                            row = ccur.fetchone()
                            chroma_cnt = row[0] if row and row[0] is not None else 0
                        finally:
                            cconn.close()
                except (sqlite3.Error, OSError):
                    pass
                sub_status = {
                    **(sub_status or {}),
                    "context_facts": facts_cnt,
                    "system_procedures": procs_cnt,
                    "chroma_vectors": chroma_cnt,
                    "pending_sync_queue": sync_queue_cnt,
                }
            elif key == "vault_map":
                vdb = str(BASE_DIR / "data" / "evelyn_vault.db")
                indexed_docs = 0
                exists = os.path.exists(vdb)
                mtime = os.path.getmtime(vdb) if exists else None
                if exists:
                    conn = sqlite3.connect(vdb, timeout=1.0)
                    try:
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(*) FROM vault_documents")
                        indexed_docs = cur.fetchone()[0]
                    except (sqlite3.Error, OSError):
                        pass
                    finally:
                        conn.close()
                sub_status = {
                    **(sub_status or {}),
                    "indexed_documents": indexed_docs,
                    "db_target": "data/evelyn_vault.db",
                    "file_exists": exists,
                    "last_modified": mtime,
                }
            elif key == "refresh_memory":
                phase = task_data.get("phase", "Idle")
                current_step = 1
                if "Phase 2" in phase or "Ingest" in phase or "Knowledge" in phase or phase == "Completed successfully.":
                    current_step = 2

                vdb = str(BASE_DIR / "data" / "evelyn_vault.db")
                vault_docs_cnt = 0
                if os.path.exists(vdb):
                    conn = sqlite3.connect(vdb, timeout=1.0)
                    try:
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(*) FROM vault_documents")
                        vault_docs_cnt = cur.fetchone()[0]
                    except (sqlite3.Error, OSError):
                        pass
                    finally:
                        conn.close()

                chroma_cnt = 0
                try:
                    cdb_path = str(BASE_DIR / "data" / "chroma_db" / "chroma.sqlite3")
                    if os.path.exists(cdb_path):
                        cconn = sqlite3.connect(
                            f"file:{cdb_path}?mode=ro", uri=True, timeout=1.0
                        )
                        try:
                            ccur = cconn.cursor()
                            ccur.execute("""
                                SELECT COUNT(e.id)
                                FROM collections c
                                JOIN segments s ON s.collection = c.id AND s.scope='METADATA'
                                LEFT JOIN embeddings e ON e.segment_id = s.id
                                WHERE c.name = 'evelyn_memory'
                            """)
                            row = ccur.fetchone()
                            chroma_cnt = row[0] if row and row[0] is not None else 0
                        finally:
                            cconn.close()
                except (sqlite3.Error, OSError):
                    pass

                sub_status = {
                    **(sub_status or {}),
                    "total_steps": 2,
                    "current_step": current_step,
                    "steps": ["Vault Indexer", "Knowledge Ingest"],
                    "vault_documents_count": vault_docs_cnt,
                    "knowledge_vectors_count": chroma_cnt,
                }

        tasks_info.append(
            {
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
            }
        )

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

            research_tasks_info.append(
                {
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
                }
            )

    return {
        "is_any_running": is_any_running,
        "active_lock_holder": active_lock_holder,
        "tasks": tasks_info,
        "research_tasks": research_tasks_info,
    }


def _enrich_extraction_with_taxonomy(item: dict) -> dict:
    """Enrich an extraction item with Vector RAG taxonomy suggestions and novelty score."""
    obs = item.get("observation", "")
    if not obs:
        return item

    try:
        from Evelyn.tools import chroma_rag
        from Evelyn.tools.tag_librarian import is_excluded_tag

        tag_col = getattr(cfg, "CHROMA_TAG_COLLECTION", "evelyn_tag_taxonomy")
        results = chroma_rag.query_collection(obs, tag_col, n_results=5)

        suggested_tags = []
        min_dist = 1.0

        for r in results:
            meta = r.get("metadata") or {}
            tag = meta.get("tag")
            if tag and not is_excluded_tag(tag) and tag not in suggested_tags:
                suggested_tags.append(tag)
            dist = float(r.get("distance", 1.0))
            if dist < min_dist:
                min_dist = dist

        item["suggested_tags"] = suggested_tags[:4]
        item["novelty_score"] = round(min_dist, 2)
        if min_dist < 0.40:
            item["alignment_label"] = "Aligned"
        elif min_dist < 0.55:
            item["alignment_label"] = "Related"
        else:
            item["alignment_label"] = "Novel"
    except (sqlite3.Error, OSError, ValueError, KeyError, RuntimeError):
        item["suggested_tags"] = []
        item["novelty_score"] = 1.0
        item["alignment_label"] = "Novel"

    return item


@app.get("/api/review/unified")
async def get_unified_review(_: None = Depends(check_auth)):
    """Return all pending review items (extractions, proposals, profile updates, procedures)
    in a single unified list with item_type metadata and vector taxonomy suggestions.
    """
    from Evelyn.tools import memory_db

    unified_items = []
    queued_split_ids = memory_db.get_all_queued_split_entry_ids()

    # 1. Extractions
    raw_extractions = memory_db.get_all_entries(statuses=["extracted"])
    for item in raw_extractions:
        item["item_type"] = "extraction"
        item["is_split_queued"] = item["id"] in queued_split_ids
        unified_items.append(_enrich_extraction_with_taxonomy(item))

    # 2. Proposals
    proposals = memory_db.get_pending_proposals()
    for p in proposals:
        source_entries = []
        for eid in p.get("source_ids", []):
            if p.get("type") == "procedure_merge":
                proc = memory_db.get_procedure(eid)
                if proc:
                    source_entries.append(
                        {
                            "id": proc["id"],
                            "category": "procedure",
                            "subject": getattr(cfg, "ASSISTANT_NAME", "Evelyn"),
                            "trigger_pattern": proc.get("trigger_pattern", ""),
                            "steps": proc.get("steps", ""),
                            "pitfalls": proc.get("pitfalls", ""),
                            "verification": proc.get("verification", ""),
                            "observation": f"**Trigger:** {proc.get('trigger_pattern', '')}\n**Steps:**\n{proc.get('steps', '')}",
                            "tags": proc.get("tags", ""),
                            "is_split_queued": False,
                            "is_procedure": True,
                        }
                    )
            else:
                entry = memory_db.get_entry(eid)
                if entry:
                    entry["is_split_queued"] = eid in queued_split_ids
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
    """Return all extracted (pending review) memory entries with vector taxonomy enrichment."""
    from Evelyn.tools import memory_db

    raw_extractions = memory_db.get_all_entries(statuses=["extracted"])
    return [_enrich_extraction_with_taxonomy(item) for item in raw_extractions]


@app.post("/api/review/extractions/{id}/{action}")
async def action_extraction(
    id: int,
    action: str,
    req: EditEntryRequest | None = None,
    _: None = Depends(check_auth),
):
    """Approve, delete, or edit an extracted memory entry.

    Args:
        id:     SQLite row ID of the entry.
        action: "approve" | "delete" | "edit".
        req:    Required for "edit" — carries updated fields.
    """
    from Evelyn.tools import memory_db

    if action == "approve":
        memory_db.update_entry(id, status="live")
        await start_refresh_memory_internal()
    elif action in ("delete", "hard_delete"):
        memory_db.hard_delete_entry(id)
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
        raise HTTPException(
            status_code=400, detail="Edit action requires a request body"
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid action")
    return {"status": "ok"}


@app.get("/api/identity")
async def get_identity():
    """Return configured identity parameters for client-side display."""
    return {
        "assistant_name": cfg.ASSISTANT_NAME,
        "user_name": cfg.USER_NAME,
        "subject_code_user": cfg.SUBJECT_CODE_USER,
        "subject_code_assistant": cfg.SUBJECT_CODE_ASSISTANT,
        "persona_files": {
            "assistant": cfg.PERSONA_FILE_ASSISTANT,
            "user": cfg.PERSONA_FILE_USER,
            "directives": cfg.PERSONA_FILE_DIRECTIVES,
        },
    }


@app.get("/api/persona/{filename}")
async def get_persona_file(filename: str, _: None = Depends(check_auth)):
    """Read a persona file's current content for diff display."""
    safe_names = set(cfg.PERSONA_FILES)
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
    from Evelyn.tools import memory_db

    proposals = memory_db.get_pending_proposals()
    for p in proposals:
        source_entries = []
        for eid in p.get("source_ids", []):
            if p.get("type") == "procedure_merge":
                proc = memory_db.get_procedure(eid)
                if proc:
                    source_entries.append(
                        {
                            "id": proc["id"],
                            "category": "procedure",
                            "subject": getattr(cfg, "ASSISTANT_NAME", "Evelyn"),
                            "trigger_pattern": proc.get("trigger_pattern", ""),
                            "steps": proc.get("steps", ""),
                            "pitfalls": proc.get("pitfalls", ""),
                            "verification": proc.get("verification", ""),
                            "observation": f"**Trigger:** {proc.get('trigger_pattern', '')}\n**Steps:**\n{proc.get('steps', '')}",
                            "tags": proc.get("tags", ""),
                            "is_split_queued": False,
                            "is_procedure": True,
                        }
                    )
            else:
                entry = memory_db.get_entry(eid)
                if entry:
                    source_entries.append(entry)
        p["source_entries"] = source_entries
    return proposals


@app.post("/api/review/proposals/{id}/{action}")
async def action_proposal(
    id: int,
    action: str,
    req: ProposalActionRequest | None = None,
    _: None = Depends(check_auth),
):
    """Approve, deny, or unlink source context entries on a proposal.

    Args:
        id:     Proposal row ID.
        action: "approve" | "deny" | "unlink_source".
        req:    Optional JSON body containing modified_text or source_id.
    """
    from Evelyn.tools import memory_db

    if action == "deny":
        proposals = memory_db.get_pending_proposals()
        prop = next((p for p in proposals if p["id"] == id), None)
        if prop and prop["type"] == "profile_update":
            target_filename = os.path.basename(prop["suggested_category"])
            prop_ts = prop.get("created_at") or time.time()
            for eid in prop.get("source_ids", []):
                memory_db.touch_entry_evolved(eid, target_filename, prop_ts)
            advance_doc_run_timestamp(target_filename, "BELOW_THRESHOLD", "Proposal denied; entries stamped")
        memory_db.reject_proposal(id)
        return {"status": "ok"}
    elif action in ("delete", "hard_delete"):
        memory_db.delete_proposal(id)
        return {"status": "ok"}
    elif action == "edit":
        if not req or req.modified_text is None:
            raise HTTPException(
                status_code=400, detail="edit requires modified_text in request body"
            )
        memory_db.update_proposal(id, merged_observation=req.modified_text)
        return {"status": "ok"}
    elif action == "unlink_source":
        if not req or req.source_id is None:
            raise HTTPException(
                status_code=400,
                detail="unlink_source requires source_id in request body",
            )
        memory_db.remove_proposal_source_id(id, req.source_id)
        return {"status": "ok"}
    elif action == "approve":
        proposals = memory_db.get_pending_proposals()
        prop = next((p for p in proposals if p["id"] == id), None)
        if not prop:
            raise HTTPException(status_code=404, detail="Proposal not found")

        final_text = (
            req.modified_text
            if (req and req.modified_text is not None)
            else prop["merged_observation"]
        )

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
            target_filename = os.path.basename(prop["suggested_category"])
            target_file = PERSONA_DIR / target_filename
            if not target_file.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"Target file not found: {target_filename}",
                )
            target_file.write_text(final_text, encoding="utf-8")
            memory_db.update_proposal(id, merged_observation=final_text)
            memory_db.apply_proposal(id)
            # Stamp entry_document_evolution on all source entries with proposal created_at timestamp.
            # Using prop["created_at"] guarantees that any entries edited/split during human review
            # (updated_at > created_at) are recognized as dirty and remain eligible for re-evaluation.
            prop_ts = prop.get("created_at") or time.time()
            for eid in prop.get("source_ids", []):
                memory_db.touch_entry_evolved(eid, target_filename, prop_ts)
            # Reset the per-document cooldown from approval time, not proposal generation time.
            advance_doc_run_timestamp(target_filename, "APPROVED", "Proposal approved & applied to profile note")
            # Run update_frontmatter script to update date modified/tags
            import subprocess

            await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "scripts/update_frontmatter.py", str(target_file)],
                cwd=str(BASE_DIR),
                capture_output=True,
            )
        elif prop["type"] == "procedure_merge":
            import yaml

            source_ids = prop.get("source_ids", [])
            source_tags_set = set()
            for eid in source_ids:
                p_old = memory_db.get_procedure(eid)
                if p_old and p_old.get("tags"):
                    for t in str(p_old["tags"]).split(","):
                        cleaned_t = t.strip()
                        if cleaned_t and cleaned_t.lower() not in (
                            "procedure",
                            "merged",
                            "merge",
                            "consolidated",
                            "none",
                        ):
                            source_tags_set.add(cleaned_t)
                memory_db.delete_procedure(eid)
            try:
                parsed_proc = yaml.safe_load(final_text)
            except (yaml.YAMLError, ValueError, TypeError):
                parsed_proc = {}
            if isinstance(parsed_proc, dict) and "trigger_pattern" in parsed_proc:
                proc_tags = parsed_proc.get("tags")
                if isinstance(proc_tags, list):
                    proc_tags_str = ", ".join(
                        [str(t).strip() for t in proc_tags if str(t).strip()]
                    )
                else:
                    proc_tags_str = (
                        str(proc_tags).strip() if proc_tags is not None else ""
                    )

                parsed_tags_set = {
                    t.strip().lower() for t in proc_tags_str.split(",") if t.strip()
                }
                if not proc_tags_str or parsed_tags_set.issubset(
                    {"procedure", "merged", "merge", "consolidated", "none"}
                ):
                    final_tags = (
                        ", ".join(sorted(source_tags_set))
                        if source_tags_set
                        else (proc_tags_str or "procedure")
                    )
                else:
                    combined = {
                        t.strip() for t in proc_tags_str.split(",") if t.strip()
                    }
                    combined.update(source_tags_set)
                    if len(combined) > 1:
                        combined = {
                            t
                            for t in combined
                            if t.lower()
                            not in (
                                "procedure",
                                "merged",
                                "merge",
                                "consolidated",
                                "none",
                            )
                        }
                    final_tags = (
                        ", ".join(sorted(combined)) if combined else "procedure"
                    )

                memory_db.insert_procedure(
                    trigger_pattern=parsed_proc["trigger_pattern"],
                    steps=parsed_proc.get("steps", ""),
                    pitfalls=parsed_proc.get("pitfalls"),
                    verification=parsed_proc.get("verification"),
                    source="consolidated",
                    status="live",
                    tags=final_tags,
                    suggested_tools=parsed_proc.get("suggested_tools"),
                )
            memory_db.apply_proposal(id)
        elif prop["type"] == "procedure_split":
            import yaml

            source_ids = prop.get("source_ids", [])
            for eid in source_ids:
                memory_db.delete_procedure(eid)
            try:
                parsed_data = yaml.safe_load(final_text)
                child_procs = (
                    parsed_data.get("procedures", [])
                    if isinstance(parsed_data, dict)
                    else (parsed_data if isinstance(parsed_data, list) else [])
                )
            except (yaml.YAMLError, ValueError, TypeError):
                child_procs = []
            for cp in child_procs:
                if isinstance(cp, dict) and "trigger_pattern" in cp:
                    memory_db.insert_procedure(
                        trigger_pattern=cp["trigger_pattern"],
                        steps=cp.get("steps", ""),
                        pitfalls=cp.get("pitfalls"),
                        verification=cp.get("verification"),
                        source="split",
                        status="live",
                        tags=cp.get("tags"),
                        suggested_tools=cp.get("suggested_tools"),
                    )
            memory_db.apply_proposal(id)
        elif prop["type"] == "split":
            import yaml

            source_ids = prop.get("source_ids", [])
            source_id = source_ids[0] if source_ids else None
            if source_id:
                try:
                    parsed_splits = yaml.safe_load(final_text)
                    if isinstance(parsed_splits, dict) and "entries" in parsed_splits:
                        child_entries = parsed_splits["entries"]
                    elif isinstance(parsed_splits, list):
                        child_entries = parsed_splits
                    else:
                        child_entries = []
                except (yaml.YAMLError, ValueError, TypeError):
                    child_entries = []
                if child_entries:
                    memory_db.split_entry(source_id, child_entries)
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
                merged_tags = (
                    ", ".join(sorted(merged_tags_set)) if merged_tags_set else None
                )

            memory_db.insert_entry(
                category=prop["suggested_category"],
                subject=subject,
                observation=final_text,
                source="consolidated",
                date=date,
                tags=merged_tags,
            )
            memory_db.apply_proposal(id)
        await start_refresh_memory_internal()
        return {"status": "ok"}
    else:
        raise HTTPException(status_code=400, detail="Invalid action")


class SplitPreviewRequest(BaseModel):
    entry_id: int | None = None
    observation: str | None = None
    category: str | None = None
    subject: str | None = None
    tags: str | None = None


class SplitApplyRequest(BaseModel):
    source_id: int
    entries: list[dict]


@app.post("/api/context/split_preview")
async def preview_context_split(
    req: SplitPreviewRequest, _: None = Depends(check_auth)
):
    """Decompose a compound or over-merged context entry into atomic child entries."""
    from Evelyn.tools import fact_extractor, memory_db
    from Evelyn.tools.tag_librarian import normalize_tag_format

    obs = req.observation
    cat = req.category
    subj = req.subject
    tags = req.tags

    if req.entry_id:
        entry = memory_db.get_entry(req.entry_id)
        if entry:
            obs = obs or entry.get("observation")
            cat = cat or entry.get("category")
            subj = subj or entry.get("subject")
            tags = tags or entry.get("tags")

    if not obs or not obs.strip():
        raise HTTPException(
            status_code=400, detail="Observation text is required for split preview"
        )

    cat00 = fact_extractor.load_cat00_index()

    prompt = (
        "You are an expert knowledge decomposition engine for a personal memory system.\n"
        "Analyze the following compound observation and decompose it into 2 or more atomic, self-contained, "
        "durable context facts. Each fact should represent exactly one coherent observation or preference.\n\n"
        f"COMPOUND OBSERVATION:\n{obs}\n\n"
        f"SOURCE CATEGORY: {cat or f'Cat05-{cfg.SUBJECT_CODE_USER}'}\n"
        f"SOURCE SUBJECT: {subj or cfg.USER_NAME}\n"
        f"SOURCE TAGS: {tags or ''}\n\n"
        f"CATEGORY REFERENCE:\n{cat00}\n\n"
        "RULES:\n"
        "1. DO NOT lose specific details, nouns, conditions, or context from the original observation.\n"
        "2. MULTI-TIER DOMAIN TAGS: Assign clean domain hierarchy tags (e.g. `Tech/Python/FastAPI`, "
        "`Home/Coffee/Espresso`, `Lore/Dungeon_Crawler_Carl`) for each split item.\n"
        f"3. Assign the most fitting Cat##-{{{cfg.SUBJECT_CODE_ASSISTANT},{cfg.SUBJECT_CODE_USER}}} code for each split entry.\n\n"
        "Output ONLY a fenced YAML block:\n"
        "```yaml\n"
        "entries:\n"
        f"  - category: Cat05-{cfg.SUBJECT_CODE_USER}\n"
        f"    subject: {cfg.USER_NAME}\n"
        '    tags: "Tech/Python/FastAPI"\n'
        '    observation: "First clean, atomic fact with full specific detail."\n'
        f"  - category: Cat14-{cfg.SUBJECT_CODE_USER}\n"
        f"    subject: {cfg.USER_NAME}\n"
        '    tags: "Home/Server/ZWave"\n'
        '    observation: "Second clean, atomic fact with full specific detail."\n'
        "```"
    )

    from Evelyn.tools import tag_librarian

    raw_response = await asyncio.to_thread(
        tag_librarian.query_ollama,
        prompt,
        "You decompose compound memory facts into atomic observations. Output only YAML.",
    )

    import yaml

    match = re.search(
        r"```(?:yaml)?\s*\n(.*?)```", raw_response, re.DOTALL | re.IGNORECASE
    )
    block = match.group(1) if match else raw_response
    try:
        data = yaml.safe_load(block)
    except (yaml.YAMLError, ValueError, TypeError):
        data = None

    entries_list = []
    if (
        isinstance(data, dict)
        and "entries" in data
        and isinstance(data["entries"], list)
    ):
        entries_list = data["entries"]
    elif isinstance(data, list):
        entries_list = data

    splits = []
    for item in entries_list:
        if not isinstance(item, dict):
            continue
        c_obs = str(item.get("observation", "")).strip()
        if not c_obs:
            continue
        c_cat = str(
            item.get("category", cat or f"Cat05-{cfg.SUBJECT_CODE_USER}")
        ).strip()
        c_subj = str(item.get("subject", subj or cfg.USER_NAME)).strip()
        raw_t = str(item.get("tags", tags or "")).strip()
        norm_t = ", ".join(
            [normalize_tag_format(t) for t in raw_t.split(",") if t.strip()]
        )

        split_dict = {
            "category": c_cat,
            "subject": c_subj,
            "observation": c_obs,
            "tags": norm_t,
        }
        splits.append(_enrich_extraction_with_taxonomy(split_dict))

    return {
        "original": {
            "entry_id": req.entry_id,
            "observation": obs,
            "category": cat,
            "subject": subj,
            "tags": tags,
        },
        "splits": splits,
    }


@app.post("/api/context/split_apply")
async def apply_context_split(req: SplitApplyRequest, _: None = Depends(check_auth)):
    """Apply a split on a compound context entry."""
    from Evelyn.tools import memory_db

    if not req.entries:
        raise HTTPException(
            status_code=400, detail="At least one child entry is required to split"
        )

    new_ids = memory_db.split_entry(req.source_id, req.entries)
    await start_refresh_memory_internal()
    return {"status": "ok", "new_ids": new_ids}


@app.post("/api/context/{id}/queue_split")
async def queue_context_split(id: int, _: None = Depends(check_auth)):
    """Queue a context entry for split evaluation in the next consolidation run."""
    from Evelyn.tools import memory_db

    entry = memory_db.get_entry(id)
    if not entry:
        raise HTTPException(status_code=404, detail="Context entry not found")
    success = memory_db.enqueue_split(id)
    if not success:
        raise HTTPException(
            status_code=500, detail="Failed to enqueue context entry for split"
        )
    return {"status": "ok", "entry_id": id, "queued": True}


class ProcedureReviewBody(BaseModel):
    trigger_pattern: str | None = None
    steps: str | None = None
    pitfalls: str | None = None
    verification: str | None = None
    tags: str | None = None
    suggested_tools: str | None = None


class ProcedureQueueMergeRequest(BaseModel):
    proc_ids: list[int]


class ProcedureUpdateRequest(BaseModel):
    trigger_pattern: str | None = None
    steps: str | None = None
    pitfalls: str | None = None
    verification: str | None = None
    tags: str | None = None
    suggested_tools: str | None = None
    status: str | None = None


@app.get("/api/review/procedures")
async def get_procedures_review(_: None = Depends(check_auth)):
    """Return all pending extracted procedures for review."""
    from Evelyn.tools import memory_db

    return memory_db.get_all_procedures(status="extracted")


@app.post("/api/review/procedures/{id}/{action}")
async def action_procedure(
    id: int,
    action: str,
    body: ProcedureReviewBody | None = None,
    _: None = Depends(check_auth),
):
    """Approve, edit and approve, or deny/archive an extracted procedure.

    Args:
        id:     Procedure row ID.
        action: "approve" | "deny" | "edit".
        body:   Optional edits to the procedure trigger/steps/pitfalls/verification/tags/suggested_tools.
    """
    from Evelyn.tools import memory_db

    if action in ("deny", "archive"):
        memory_db.delete_procedure(id)
        return {"status": "ok"}
    elif action in ("delete", "hard_delete"):
        memory_db.hard_delete_procedure(id)
        return {"status": "ok"}
    elif action == "edit":
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
            if body.suggested_tools is not None:
                update_fields["suggested_tools"] = body.suggested_tools

        success = memory_db.update_procedure(id, **update_fields)
        if not success:
            raise HTTPException(
                status_code=404, detail="Procedure not found or not updated"
            )
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
            if body.suggested_tools is not None:
                update_fields["suggested_tools"] = body.suggested_tools

        update_fields["status"] = "live"
        success = memory_db.update_procedure(id, **update_fields)
        if not success:
            raise HTTPException(
                status_code=404, detail="Procedure not found or not updated"
            )
        return {"status": "ok"}
    else:
        raise HTTPException(status_code=400, detail="Invalid action")


@app.get("/api/procedures")
async def get_procedures(status: str | None = None, _: None = Depends(check_auth)):
    """Return all procedures matching status ('live', 'extracted', 'archived', or 'all')."""
    from Evelyn.tools import memory_db

    target_status = status if status and status != "all" else None
    procs = memory_db.get_all_procedures(status=target_status)
    merge_queued_ids = memory_db.get_all_queued_procedure_merge_ids()
    split_queued_ids = memory_db.get_all_queued_procedure_split_ids()
    for p in procs:
        p["is_queued_merge"] = p["id"] in merge_queued_ids
        p["is_queued_split"] = p["id"] in split_queued_ids
    return procs


@app.patch("/api/procedures/{id}")
async def patch_procedure(
    id: int, body: ProcedureUpdateRequest, _: None = Depends(check_auth)
):
    """Update fields of an existing procedure."""
    from Evelyn.tools import memory_db

    fields = {}
    if body.trigger_pattern is not None:
        fields["trigger_pattern"] = body.trigger_pattern
    if body.steps is not None:
        fields["steps"] = body.steps
    if body.pitfalls is not None:
        fields["pitfalls"] = body.pitfalls
    if body.verification is not None:
        fields["verification"] = body.verification
    if body.tags is not None:
        fields["tags"] = body.tags
    if body.suggested_tools is not None:
        fields["suggested_tools"] = body.suggested_tools
    if body.status is not None:
        fields["status"] = body.status

    if not fields:
        raise HTTPException(status_code=400, detail="No fields provided for update")
    success = memory_db.update_procedure(id, **fields)
    if not success:
        raise HTTPException(status_code=404, detail="Procedure not found")
    return {"status": "ok"}


@app.post("/api/procedures/queue_merge")
async def queue_procedure_merge(
    req: ProcedureQueueMergeRequest, _: None = Depends(check_auth)
):
    """Enqueue multiple procedure IDs to be merged in the background."""
    from Evelyn.tools import memory_db

    if len(req.proc_ids) < 2:
        raise HTTPException(
            status_code=400,
            detail="At least 2 procedure IDs are required to queue a merge",
        )
    queue_id = memory_db.enqueue_procedure_merge(req.proc_ids)
    return {"status": "ok", "queue_id": queue_id}


@app.post("/api/procedures/{id}/queue_split")
async def queue_procedure_split(id: int, _: None = Depends(check_auth)):
    """Enqueue a procedure ID to be evaluated for splitting in the background."""
    from Evelyn.tools import memory_db

    proc = memory_db.get_procedure(id)
    if not proc:
        raise HTTPException(status_code=404, detail="Procedure not found")
    success = memory_db.enqueue_procedure_split(id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to enqueue procedure split")
    return {"status": "ok", "proc_id": id, "queued": True}


@app.post("/api/procedures/{id}/archive")
async def archive_procedure(id: int, _: None = Depends(check_auth)):
    """Soft delete/archive a procedure."""
    from Evelyn.tools import memory_db

    success = memory_db.delete_procedure(id)
    if not success:
        raise HTTPException(status_code=404, detail="Procedure not found")
    return {"status": "ok"}


@app.delete("/api/procedures/{id}")
@app.post("/api/procedures/{id}/delete")
async def delete_procedure_endpoint(id: int, _: None = Depends(check_auth)):
    """Permanently delete a procedure."""
    from Evelyn.tools import memory_db

    success = memory_db.hard_delete_procedure(id)
    if not success:
        raise HTTPException(status_code=404, detail="Procedure not found")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Terminal Agency Endpoints (Hermes Tier 3 #9)
# ---------------------------------------------------------------------------


@app.get("/api/terminal/pending")
async def get_pending_commands(_: None = Depends(check_auth)):
    """Return all commands/writes awaiting user approval."""
    from Evelyn.tools import terminal_agent

    return terminal_agent.get_pending_approvals()


class ApprovalStatusRequest(BaseModel):
    ids: list[str]


@app.post("/api/terminal/status")
async def get_multiple_approvals_status(
    body: ApprovalStatusRequest, _: None = Depends(check_auth)
):
    """Get the status of multiple approval IDs in bulk."""
    from Evelyn.tools import terminal_agent

    return {
        approval_id: terminal_agent.get_approval_status(approval_id)
        for approval_id in body.ids
    }


@app.get("/api/terminal/details/{approval_id}")
async def get_approval_details(approval_id: str, _: None = Depends(check_auth)):
    """Return full details including content for a specific approval ID."""
    from Evelyn.tools import terminal_agent

    details = terminal_agent.get_approval_details(approval_id)
    if not details:
        raise HTTPException(
            status_code=404, detail="Approval request not found or expired"
        )
    return details


@app.post("/api/terminal/approve/{approval_id}")
async def approve_terminal_command(approval_id: str, _: None = Depends(check_auth)):
    """Approve and execute a pending command or file write."""
    from Evelyn.tools import terminal_agent

    result = terminal_agent.approve_command(approval_id)
    return {"status": "ok", "result": result}


@app.post("/api/terminal/deny/{approval_id}")
async def deny_terminal_command(approval_id: str, _: None = Depends(check_auth)):
    """Deny and delete a pending command or file write."""
    from Evelyn.tools import terminal_agent

    terminal_agent.deny_command(approval_id)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Vault PDF Staging & Document Ingestion Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/vault/domains")
async def get_vault_domains(_: None = Depends(check_auth)):
    """Return available vault domain options for document staging."""
    from Evelyn.tools import pdf_staging_worker

    return {"domains": pdf_staging_worker.get_available_domains()}


@app.post("/api/vault/upload_staging")
async def upload_document_staging(
    file: UploadFile = File(...),
    mode: str = Form("full"),
    domain_path: str = Form(""),
    domain_name: str = Form(""),
    _: None = Depends(check_auth),
):
    """
    Upload a document (PDF) to the vault staging queue.
    Modes:
      - 'full': Extracts chapters, markdown TOC, embedded viewer, and indexes to Chroma.
      - 'card': Generates a sidecar index card with embedded viewer and relocates source.
    """
    from Evelyn.tools import pdf_staging_worker

    filename = os.path.basename(file.filename or "uploaded_document.pdf")
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Only PDF documents are supported for staging ingestion.",
        )

    staging_dir = (
        pdf_staging_worker.FULL_EXTRACTION_STAGING
        if mode == "full"
        else pdf_staging_worker.SIDECAR_ONLY_STAGING
    )
    staging_dir.mkdir(parents=True, exist_ok=True)
    target_path = staging_dir / filename

    # Save uploaded file
    content = await file.read()
    await asyncio.to_thread(_server_sync_write_bytes, str(target_path), content)

    # Save metadata JSON sidecar for destination domain
    meta_path = staging_dir / f"{filename}.meta.json"
    meta_info = {
        "target_path": domain_path or "Notes",
        "domain": domain_name or "General",
        "mode": mode,
        "uploaded_at": time.time(),
    }
    await asyncio.to_thread(_server_sync_dump_json, str(meta_path), meta_info)

    # Trigger background staging worker in async task if task manager is idle
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, pdf_staging_worker.process_staging_queue)
    except (RuntimeError, OSError) as e:
        print(
            f"[SERVER WARNING] Failed to trigger async staging worker: {e}", flush=True
        )

    return {
        "status": "queued",
        "filename": filename,
        "mode": mode,
        "domain_path": domain_path or "Notes",
        "domain_name": domain_name or "General",
        "staging_file": str(target_path),
    }


# ---------------------------------------------------------------------------
# Vault Note Reading & Editing Endpoints
# ---------------------------------------------------------------------------


class VaultNoteUpdateRequest(BaseModel):
    path: str
    content: str


@app.get("/api/vault/note")
async def get_vault_note(path: str, _: None = Depends(check_auth)):
    """Read markdown content of a note within the Obsidian Vault or an SQLite context entry."""
    clean_path = path.replace("\\", "/").lstrip("/")

    # Handle SQLite Context Fact Entries (e.g. sqlite::context_entry::2650)
    if clean_path.startswith(("sqlite::context_entry::", "sqlite::fact::")):
        try:
            entry_id = int(clean_path.split("::")[-1])
            from Evelyn.tools import memory_db

            entry = memory_db.get_entry(entry_id)
            if not entry:
                raise HTTPException(
                    status_code=404,
                    detail=f"Context entry #{entry_id} not found in memory database",
                )

            lines = [
                f"Date: {entry.get('date') or 'N/A'}",
                f"Category: {entry.get('category') or 'N/A'}",
                f"Subject: {entry.get('subject') or 'N/A'}",
                f"Tags: {entry.get('tags') or 'None'}",
                f"Confidence: {entry.get('confidence') or 'medium'}",
                f"Status: {entry.get('status') or 'live'}",
                "",
                "Observation:",
                entry.get("observation") or "",
            ]
            return {
                "status": "ok",
                "path": clean_path,
                "content": "\n".join(lines),
                "is_context_entry": True,
                "entry": entry,
            }
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid context entry ID") from None
        except HTTPException:
            raise
        except (sqlite3.Error, OSError, KeyError) as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    full_path = os.path.abspath(os.path.join(cfg.VAULT_BASE_DIR, clean_path))
    vault_base = os.path.abspath(cfg.VAULT_BASE_DIR)
    if not (full_path == vault_base or full_path.startswith(vault_base + os.sep)):
        raise HTTPException(status_code=403, detail="Path outside vault boundaries")
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="Vault note not found")
    try:
        content = await asyncio.to_thread(_server_sync_read, full_path)
        return {"status": "ok", "path": clean_path, "content": content}
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/vault/note")
async def update_vault_note(req: VaultNoteUpdateRequest, _: None = Depends(check_auth)):
    """Update markdown content of a note in the Obsidian Vault or an SQLite context entry, and queue vector re-indexing."""
    clean_path = req.path.replace("\\", "/").lstrip("/")

    # Handle SQLite Context Fact Entries
    if clean_path.startswith(("sqlite::context_entry::", "sqlite::fact::")):
        try:
            entry_id = int(clean_path.split("::")[-1])
            from Evelyn.tools import chroma_rag, memory_db

            raw_content = req.content.strip()
            obs = raw_content
            if "Observation:\n" in raw_content:
                obs = raw_content.split("Observation:\n", 1)[1].strip()
            elif "Observation:" in raw_content:
                obs = raw_content.split("Observation:", 1)[1].strip()

            success = memory_db.update_entry(entry_id, observation=obs)
            if not success:
                raise HTTPException(
                    status_code=404,
                    detail=f"Context entry #{entry_id} could not be updated",
                )

            # Enqueue Chroma vector re-indexing
            chroma_rag.enqueue_upsert(
                source_path=clean_path,
                collection_name=cfg.CHROMA_MEMORY_COLLECTION,
                content=obs,
            )
            return {"status": "ok", "path": clean_path}
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid context entry ID") from None
        except HTTPException:
            raise
        except (sqlite3.Error, OSError, KeyError) as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    full_path = os.path.abspath(os.path.join(cfg.VAULT_BASE_DIR, clean_path))
    vault_base = os.path.abspath(cfg.VAULT_BASE_DIR)
    if not (full_path == vault_base or full_path.startswith(vault_base + os.sep)):
        raise HTTPException(status_code=403, detail="Path outside vault boundaries")
    try:
        await asyncio.to_thread(_server_sync_write, full_path, req.content)
        # Update SQLite vault database
        from Evelyn.tools import chroma_rag, vault_db

        mtime = os.path.getmtime(full_path)
        title = os.path.splitext(os.path.basename(clean_path))[0]
        vault_db.upsert_document(path=clean_path, title=title, mtime=mtime)
        # Enqueue Chroma vector re-indexing
        chroma_rag.enqueue_upsert(
            source_path=clean_path,
            collection_name=cfg.CHROMA_MEMORY_COLLECTION,
            content=req.content,
        )
        return {"status": "ok", "path": clean_path}
    except (sqlite3.Error, OSError, ValueError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


if __name__ == "__main__":
    import os

    import uvicorn

    SSL_KEY = getattr(cfg, "SSL_KEY", os.environ.get("EVELYN_SSL_KEY", "server.key"))
    SSL_CERT = getattr(cfg, "SSL_CERT", os.environ.get("EVELYN_SSL_CERT", "server.crt"))
    ssl_keyfile = (
        SSL_KEY if os.path.exists(SSL_KEY) and os.path.exists(SSL_CERT) else None
    )
    ssl_certfile = (
        SSL_CERT if os.path.exists(SSL_KEY) and os.path.exists(SSL_CERT) else None
    )
    if ssl_keyfile and ssl_certfile:
        print(f"SSL certs found ({SSL_CERT}) -- starting with HTTPS")
    else:
        print(
            "No SSL certs found -- starting with plain HTTP (fine for Tailscale / localhost)"
        )

    uvicorn.run(
        "evelyn_server:app",
        host=cfg.BIND_HOST,
        port=cfg.SERVER_PORT,
        reload=False,
        log_level="info",
        ssl_keyfile=ssl_keyfile,
        ssl_certfile=ssl_certfile,
    )
