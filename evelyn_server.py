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

# evelyn_server.py
# date created: 2026-03-23 15:43:21
# date modified: 2026-05-17 22:18:07

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


def load_system_prompt() -> str:
    """Assemble system prompt from persona markdown files."""
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
            parts.append(fpath.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Time-gap awareness
# ---------------------------------------------------------------------------


def get_time_gap_context() -> str | None:
    """Return a bracketed time-gap annotation if enough time has passed
    since the last user message, or None for continuous conversation."""
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
    con = sqlite3.connect(cfg.CHAT_DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
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
    con.commit()
    con.close()


PLACEHOLDER_MARKER = "[Response interrupted"
THREAD_BREAK_MARKER = "[THREAD_BREAK]"


def load_history() -> list[dict]:
    """Load recent chat history for the model, bounded by:
    1. The most recent thread-break marker (if any), AND
    2. cfg.MAX_HISTORY_MESSAGES (default 30).

    All messages remain in the DB — this only limits what Ollama sees.
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
        "SELECT role, content FROM messages WHERE id > ? ORDER BY id DESC LIMIT ?",
        (after_id, limit),
    ).fetchall()
    con.close()

    # Rows come back newest-first; reverse to chronological order
    rows = list(reversed(rows))

    # Skip empty-content rows, placeholder messages, and thread-break markers.
    # Placeholders must NOT be sent to the model -- they confuse magistral
    # and cause it to produce empty responses on every subsequent request.
    messages = [
        {"role": r["role"], "content": r["content"]}
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


def save_message(role: str, content: str, thinking: str = None, tools_used: str = None):
    con = get_db()
    con.execute(
        "INSERT INTO messages (role, content, thinking, tools_used, ts) VALUES (?, ?, ?, ?, ?)",
        (role, content, thinking, tools_used, time.time()),
    )
    con.commit()
    con.close()


def save_message_get_id(role: str, content: str, thinking: str = None, tools_used: str = None) -> int:
    """Insert a message and return its row ID (used for later updates)."""
    con = get_db()
    cur = con.execute(
        "INSERT INTO messages (role, content, thinking, tools_used, ts) VALUES (?, ?, ?, ?, ?)",
        (role, content, thinking, tools_used, time.time()),
    )
    row_id = cur.lastrowid
    con.commit()
    con.close()
    return row_id


def update_message(row_id: int, content: str, thinking: str = None, tools_used: str = None):
    """Update an existing message row's content, thinking, and tools_used."""
    con = get_db()
    con.execute(
        "UPDATE messages SET content = ?, thinking = ?, tools_used = ? WHERE id = ?",
        (content, thinking, tools_used, row_id),
    )
    con.commit()
    con.close()


def clear_history():
    con = get_db()
    con.execute("DELETE FROM messages")
    con.commit()
    con.close()


def delete_last_assistant_message() -> str | None:
    """Delete the last assistant message and return the last user message text.
    Returns None if no user message is found."""
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
    if not cfg.API_KEY:
        return  # No key configured = open (local-only use)
    key = request.headers.get("X-Evelyn-Key", "")
    if key != cfg.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ---------------------------------------------------------------------------
# Ollama interaction
# ---------------------------------------------------------------------------


async def call_ollama_stream(messages: list[dict], tools: list[dict] = None):
    """Stream a chat request to Ollama (content-only / follow-up pass, no tools).
    Yields raw JSON lines.

    NOTE: streaming + think=True silently swallows tool_call tokens in Ollama.
    Tool detection uses call_ollama_full (non-streaming) instead.
    This function is only used for the content follow-up pass (no tools).
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
    """Non-streaming Ollama call for tool detection (Pass 1).

    Streaming + think=True silently swallows tool_call tokens in Ollama --
    the model generates ~20 tokens for the tool call JSON but emits a single
    done chunk with empty message. Non-streaming correctly surfaces tool_calls.
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
    """Execute a tool by name with the given arguments."""
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
    in_think = False
    OPEN_TAG = "<think>"
    CLOSE_TAG = "</think>"

    print(f"{_CYN}[PASS2]{_RST} Streaming content. Roles:", [m["role"] for m in msgs], flush=True)

    _SENTINEL = object()
    queue: asyncio.Queue = asyncio.Queue()

    async def _feed():
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

            # Content field -- route through inline-tag parser
            text_delta = msg.get("content", "")
            if text_delta:
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

    yield f"data: {json.dumps({'type': '_state', 'content': content_buf, 'thinking': thinking_buf})}\n\n"


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str


async def _process_chat_background(
    user_message: str,
    is_regenerate: bool,
    time_ctx: str | None,
    assistant_row_id: int,
    queue: asyncio.Queue,
):
    """Background chat worker — runs independently of the SSE connection.

    Spawned via asyncio.create_task() so a client disconnect cannot cancel it.
    Puts SSE-formatted event strings into `queue` for the thin SSE pipe to
    forward to the client when connected.  Always commits the final response
    to the DB via the finally block regardless of client state.
    """
    content_buf = ""
    thinking_buf = ""
    tools_used_list = []

    async def put(type_: str, **kw):
        await queue.put("data: " + json.dumps({"type": type_, **kw}) + "\n\n")

    async def drain_stream(stream):
        """Iterate _stream_content, buffer state, and forward events to queue."""
        nonlocal content_buf, thinking_buf
        async for event in stream:
            if event.startswith("data: "):
                try:
                    d = json.loads(event[6:])
                    if d.get("type") == "_state":
                        content_buf = d["content"]
                        thinking_buf = d.get("thinking", "")
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

        user_msg_for_model = f"{time_ctx}\n{user_message}" if time_ctx else user_message
        messages = (
            [{"role": "system", "content": system}]
            + history
            + [{"role": "user", "content": user_msg_for_model}]
        )

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
                    "content": current_content,
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
                    tools_used_list.append(fn_name)
                    dlog(f"Dispatching tool: {fn_name}({fn_args})")

                    tool_task = loop.run_in_executor(
                        None, lambda fn=fn_name, fa=fn_args: dispatch_tool(fn, fa)
                    )
                    while not tool_task.done():
                        await queue.put('data: {"type":"heartbeat"}\n\n')
                        await asyncio.sleep(1.0)
                    result = tool_task.result()
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
        if final_content:
            update_message(
                assistant_row_id,
                final_content,
                thinking=thinking_buf if thinking_buf else None,
                tools_used=tools_str,
            )
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


async def chat_stream(user_message: str, is_regenerate: bool = False):
    """SSE pipe — thin wrapper around _process_chat_background.

    All Ollama calls and DB writes run in a detached asyncio task so client
    disconnect cannot cancel them.  This generator only forwards queued events
    to the connected client.  If the client disconnects mid-stream the
    background task keeps running and saves the response to the DB.
    """
    global _last_activity_ts
    _last_activity_ts = time.time()
    importlib.reload(cfg)

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
# App setup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Suppress noisy Windows ProactorEventLoop ConnectionResetError tracebacks.
    # These fire when browser clients (polling /task_status) disconnect mid-response.
    # WinError 10054 is harmless — the background task continues regardless.
    def _suppress_connection_reset(loop, context):
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

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/status")
async def status(_: None = Depends(check_auth)):
    return {
        "status": "ok",
        "model": cfg.MODEL_NAME,
        "think": cfg.THINK,
        "debug": cfg.DEBUG_LOGGING,
    }


@app.post("/chat")
async def chat(req: ChatRequest, _: None = Depends(check_auth)):
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
    """Return chat messages, newest last.

    Query params:
      limit  – max messages to return (default 50)
      before – return messages with id < this value (cursor pagination)
    """
    con = get_db()
    if before:
        rows = con.execute(
            "SELECT id, role, content, thinking, tools_used, ts "
            "FROM messages WHERE id < ? AND content != '' ORDER BY id DESC LIMIT ?",
            (before, limit),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT id, role, content, thinking, tools_used, ts "
            "FROM messages WHERE content != '' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    con.close()
    # Rows come back newest-first from DESC; reverse to chronological
    rows = list(reversed(rows))
    return [dict(r) for r in rows]


@app.delete("/history")
async def delete_history(_: None = Depends(check_auth)):
    clear_history()
    return {"status": "cleared"}


@app.post("/new_thread")
async def new_thread(_: None = Depends(check_auth)):
    """Insert a thread-break marker. History before this point won't be
    sent to the model, but remains in the DB for UI scrollback."""
    save_message("system", THREAD_BREAK_MARKER)
    invalidate_summary_cache()
    print(f"{_MAG}[THREAD]{_RST} New thread started", flush=True)
    return {"status": "new thread started"}


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


@app.get("/task_status/{task_name}")
async def task_status(task_name: str, _: None = Depends(check_auth)):
    """Return the current status of a background task."""
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
        try:
            script = str(BASE_DIR / "Vault_Map" / "generate_vault_map.py")
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
