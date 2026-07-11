# context_summarizer.py
# date created: 2026-04-24 20:17:58
# date modified: 2026-07-11 07:18:28
# tags: #context, #summarizer, #summarization, #async, #sliding_window

"""
context_summarizer.py — Sliding-window conversation summarizer for Evelyn.

Compresses older messages that have fallen out of the active history window
into a lean summary block, injected into the system prompt each turn.

Architecture:
  - build_conversation_summary()  — returns the cached summary string (fast, sync)
  - trigger_summary_update()      — async background task that regenerates the
                                    summary by calling Ollama (same model, same
                                    process, no model swap)
  - invalidate_summary_cache()    — clears the cache (called on thread break)
  - _prune_tool_outputs()         — strips bulky tool outputs from older messages
                                    before summarization (zero LLM cost)

The summarizer uses the same call_ollama_full() path as the main chat loop,
so Ollama reuses the already-loaded model with zero swap overhead.

All config is read from evelyn_config.py (single source of truth).
"""

import asyncio
import hashlib
import importlib
import sqlite3
import time
import os
import json

import evelyn_config as cfg

# ---------------------------------------------------------------------------
# Cache Persistence and Structure
# ---------------------------------------------------------------------------
# Persisted to disk to avoid hammering Ollama on server restart.

CACHE_FILE = os.path.join("data", "summary_cache.json")

_cache = {
    "summary": "",          # The generated summary text
    "msg_hash": "",         # Hash of message IDs used to generate it
    "last_updated": 0.0,    # Timestamp of last successful summarization
    "date_span": "",        # Human-readable date range of summarized messages
}

def _save_cache_to_disk():
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, indent=2)
    except Exception as e:
        print(f"[SUMMARIZER ERROR] Failed to save cache: {e}", flush=True)

def _load_cache_from_disk():
    global _cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "summary" in data and "msg_hash" in data:
                    _cache.update(data)
                    print(f"[SUMMARIZER] Loaded cached summary from disk (hash: {_cache['msg_hash'][:8]}...)", flush=True)
        except Exception as e:
            print(f"[SUMMARIZER ERROR] Failed to load cache: {e}", flush=True)

_load_cache_from_disk()

# Guard against concurrent summarization tasks
_summarizing = False

# Reference to the in-flight summarization asyncio.Task so we can cancel it
# if a new chat request arrives before it finishes (prevents Ollama queue contention)
_summary_task = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_conversation_summary() -> tuple[str, str]:
    """Return the cached conversation summary and its date span for injection.

    Returns:
        tuple[str, str]: (summary_text, date_span) where date_span is a
            human-readable range like 'Fri Jul 10 morning → Fri Jul 10 evening'.
            Both are empty strings if no summary exists.
    """
    return _cache["summary"], _cache.get("date_span", "")


def invalidate_summary_cache():
    """Clear the cached summary when starting a new thread."""
    global _cache
    _cache = {"summary": "", "msg_hash": "", "last_updated": 0.0, "date_span": ""}
    _save_cache_to_disk()
    cancel_pending_summary()
    print("[SUMMARIZER] Cache invalidated (new thread)", flush=True)


def cancel_pending_summary():
    """Cancel any in-flight summarization task to free Ollama."""
    global _summary_task, _summarizing
    if _summary_task and not _summary_task.done():
        _summary_task.cancel()
        _summarizing = False
        print("[SUMMARIZER] Cancelled in-flight summarization (new chat request)", flush=True)
    _summary_task = None


async def trigger_summary_update():
    """Regenerate the conversation summary in the background.

    Spawns an asynchronous background task to query the DB and call Ollama.
    """
    global _summarizing, _summary_task

    if _summarizing:
        # Another summarization is already in flight — skip
        return

    _summarizing = True
    try:
        await _do_summary_update()
    except asyncio.CancelledError:
        print("[SUMMARIZER] Summarization cancelled", flush=True)
    except Exception as e:
        print(f"[SUMMARIZER ERROR] {type(e).__name__}: {e}", flush=True)
    finally:
        _summarizing = False


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------


def _get_db():
    """Get a SQLite connection (same DB as evelyn_server)."""
    con = sqlite3.connect(cfg.CHAT_DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _get_summary_window() -> tuple[list[dict], str, str]:
    """Fetch the messages that should be summarized and compute their hash.

    Returns:
        (messages, msg_hash, date_span) where messages is a list of
        {role, content, ts} dicts, msg_hash is a hex digest of message IDs
        for change detection, and date_span is a human-readable range string
        built from the first and last message timestamps.
    """
    importlib.reload(cfg)

    con = _get_db()

    # Find the latest thread-break marker (same logic as load_history)
    brk = con.execute(
        "SELECT id FROM messages WHERE content = '[THREAD_BREAK]' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    after_id = brk["id"] if brk else 0

    # Get ALL messages in the current thread (newest first)
    all_rows = con.execute(
        "SELECT id, role, content, ts FROM messages WHERE id > ? ORDER BY id DESC",
        (after_id,),
    ).fetchall()
    con.close()

    # Filter out empty, placeholder, and thread-break messages
    valid_rows = [
        r for r in all_rows
        if r["content"].strip()
        and not r["content"].startswith("[Response interrupted")
        and r["content"] != "[THREAD_BREAK]"
    ]

    active_limit = cfg.MAX_HISTORY_MESSAGES
    overlap = cfg.SUMMARY_OVERLAP
    window_size = cfg.SUMMARY_WINDOW_SIZE

    # If we don't have enough messages to fill the active window,
    # there's nothing to summarize yet
    if len(valid_rows) <= active_limit:
        return [], ""

    # Messages are newest-first. The active window is valid_rows[:active_limit].
    # The summary window starts after the active window, with overlap.
    # Overlap: grab the last `overlap` messages from the active window
    # to give the summarizer continuity context.
    overlap_start = max(0, active_limit - overlap)
    summary_end = active_limit + window_size

    # Slice: overlap msgs from active + older msgs beyond active
    summary_rows = valid_rows[overlap_start:summary_end]

    # Reverse to chronological order (oldest first)
    summary_rows = list(reversed(summary_rows))

    if not summary_rows:
        return [], "", ""

    messages = [
        {"role": r["role"], "content": r["content"], "ts": r["ts"]}
        for r in summary_rows
    ]

    # Hash the message IDs for change detection
    id_string = ",".join(str(r["id"]) for r in summary_rows)
    msg_hash = hashlib.md5(id_string.encode()).hexdigest()

    # Build a human-readable date span from first and last message timestamps
    date_span = _build_date_span(
        summary_rows[0]["ts"], summary_rows[-1]["ts"]
    )

    return messages, msg_hash, date_span


def _prune_tool_outputs(messages: list[dict]) -> list[dict]:
    """Replace verbose tool outputs in older messages with a placeholder.

    Runs before LLM summarization. Tool outputs (image generation results,
    research reports, Obsidian links) can be hundreds of tokens but contribute
    nothing useful to a conversation summary. Stripping them here saves tokens
    at zero LLM cost.

    Only assistant messages exceeding the threshold AND containing a known
    tool-output marker are pruned — ordinary long assistant replies are left
    intact.

    Args:
        messages: Chronologically ordered list of {role, content} dicts.

    Returns:
        list[dict]: Copy of the list with bulky tool outputs replaced by
            '[Tool output cleared]'.
    """
    # Patterns that identify a message as primarily tool output.
    _TOOL_MARKERS = (
        "![",            # Markdown image embed (generated images)
        "[[Research/",   # Obsidian research link
        "[RESEARCH",     # Research task marker
        "[Tool output",  # Already-pruned placeholder (idempotent)
    )
    # Messages below this length are cheap enough to keep as-is.
    _PRUNE_THRESHOLD = 400

    pruned = []
    for msg in messages:
        if (
            msg["role"] == "assistant"
            and len(msg["content"]) > _PRUNE_THRESHOLD
            and any(marker in msg["content"] for marker in _TOOL_MARKERS)
        ):
            pruned.append({"role": "assistant", "content": "[Tool output cleared]"})
        else:
            pruned.append(msg)
    return pruned


def _time_of_day_label(ts) -> str:
    """Convert a unix timestamp to a 'Day Mon DD · period' label.

    Returns a bracketed label like '[Mon Jun 09 · afternoon]' for use as a
    transcript prefix. Returns an empty string if ts is absent or invalid.

    Args:
        ts: Unix timestamp (float or int), or None.

    Returns:
        str: Formatted label string, or '' on failure.
    """
    if not ts:
        return ""
    import datetime as dt
    try:
        d = dt.datetime.fromtimestamp(ts)
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


def _build_date_span(first_ts, last_ts) -> str:
    """Build a human-readable date range string from two unix timestamps.

    Used to label the summary window so Evelyn can interpret relative time
    words (today, tomorrow, yesterday) against the correct calendar anchor.

    Args:
        first_ts: Unix timestamp of the earliest message in the window.
        last_ts: Unix timestamp of the latest message in the window.

    Returns:
        str: A range string like 'Fri Jul 10 morning → Fri Jul 10 evening',
            or an empty string if timestamps are absent or invalid.
    """
    import datetime as dt

    def _label(ts) -> str:
        try:
            d = dt.datetime.fromtimestamp(ts)
            hour = d.hour
            if 5 <= hour < 12:
                period = "morning"
            elif 12 <= hour < 17:
                period = "afternoon"
            elif 17 <= hour < 21:
                period = "evening"
            else:
                period = "night"
            return f"{d.strftime('%a %b %d')} {period}"
        except (OSError, OverflowError, ValueError, TypeError):
            return ""

    start = _label(first_ts)
    end = _label(last_ts)
    if not start and not end:
        return ""
    if start == end:
        return start
    return f"{start} \u2192 {end}"


def _format_messages_for_prompt(messages: list[dict]) -> str:
    """Format message list into a readable conversation transcript.

    Each line is prefixed with a date + time-of-day label derived from the
    message's stored timestamp, giving the summarizer temporal context for
    multi-day windows.

    Args:
        messages: A list of message dictionaries with 'role', 'content', and
            optional 'ts' keys.

    Returns:
        str: The formatted transcript string.
    """
    lines = []
    for msg in messages:
        role_label = "Ricky" if msg["role"] == "user" else "Evelyn"
        # Truncate very long individual messages to keep the summarization
        # prompt reasonable (each message capped at ~500 chars)
        content = msg["content"]
        if len(content) > 500:
            content = content[:497] + "..."
        time_label = _time_of_day_label(msg.get("ts"))
        lines.append(f"{time_label}{role_label}: {content}")
    return "\n\n".join(lines)


async def _do_summary_update():
    """Core summarization logic. Called by trigger_summary_update()."""
    messages, msg_hash, date_span = _get_summary_window()

    if not messages:
        # Nothing to summarize (conversation too short)
        if _cache["summary"]:
            # Edge case: had a summary but now don't (e.g. thread break
            # happened but invalidate wasn't called). Clear it.
            _cache["summary"] = ""
            _cache["msg_hash"] = ""
            _cache["date_span"] = ""
        return

    if msg_hash == _cache["msg_hash"]:
        # Messages haven't changed since last summary — skip
        return

    # Pre-pass: replace bulky tool outputs before the LLM call.
    # This saves tokens at zero cost — the summarizer has nothing useful
    # to extract from a raw image embed or research report block.
    messages = _prune_tool_outputs(messages)

    # Build the structured summarization prompt
    max_words = cfg.SUMMARY_MAX_WORDS
    conversation_text = _format_messages_for_prompt(messages)

    # Build a date-span preamble for the prompt so the archivist model has
    # calendar context before it reads the transcript.
    date_span_note = (
        f"IMPORTANT: This conversation segment covers {date_span}. "
        if date_span
        else ""
    )

    summary_prompt = (
        "Summarize the following conversation segment between Ricky (user) and Evelyn (AI).\n"
        "Output ONLY the structured template below — no preamble, no closing remarks.\n"
        f"Keep the entire summary under {max_words} words total.\n"
        f"{date_span_note}"
        "When any message uses relative time words (today, tomorrow, yesterday, this week, "
        "next week, tonight, etc.), replace them in your summary with the actual calendar "
        "date or day so the summary is unambiguous when read later.\n\n"
        "## Conversation State\n"
        "### Chronology\n"
        "[Date range and general time flow, e.g. 'Jul 10 morning \u2192 Jul 10 evening']\n\n"
        "### Topics\n"
        "[What was being discussed — one bullet per distinct topic if multiple]\n\n"
        "### Decisions Made\n"
        "[Key conclusions or agreements reached, or 'None']\n\n"
        "### Action Items\n"
        "[What needs to happen next, or 'None']\n\n"
        "### Important Details\n"
        "[Specific values, names, file paths, configurations, dates, or facts mentioned. "
        "Resolve any relative time references to absolute dates.]\n\n"
        "### Emotional Context\n"
        "[Ricky's mood or emotionally significant moments, or 'None']\n\n"
        f"CONVERSATION:\n{conversation_text}"
    )

    summary_messages = [
        {
            "role": "system",
            "content": (
                "You are a precise conversation archivist. "
                "Fill in the structured template exactly as specified. "
                "Output only the completed template, nothing else."
            ),
        },
        {"role": "user", "content": summary_prompt},
    ]

    # Use the same model and Ollama endpoint — no model swap
    override = cfg.SUMMARY_MODEL_OVERRIDE
    model = cfg.MODEL_NAME if override == "default" else override
    # Build options dict identically to call_ollama_full() so Ollama
    # treats this as the same model configuration — no unload/reload cycle.
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

    import httpx

    payload = {
        "model": model,
        "messages": summary_messages,
        "stream": True,
        "options": options,
        "think": cfg.THINK,  # Must match main chat config to avoid model swap
    }

    print(
        f"[SUMMARIZER] Generating summary for {len(messages)} messages "
        f"(hash: {msg_hash[:8]}...)",
        flush=True,
    )

    start = time.time()
    content_buffer = ""

    try:
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream("POST", f"{cfg.OLLAMA_URL}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        import json
                        chunk = json.loads(line)
                        msg = chunk.get("message", {})
                        content_buffer += msg.get("content", "")
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        print(f"[SUMMARIZER ERROR] Ollama call failed: {e}", flush=True)
        return

    content = content_buffer.strip()

    if not content:
        print("[SUMMARIZER] Warning: empty summary returned", flush=True)
        return

    elapsed = time.time() - start

    # Update the cache
    _cache["summary"] = content
    _cache["msg_hash"] = msg_hash
    _cache["last_updated"] = time.time()
    _cache["date_span"] = date_span
    _save_cache_to_disk()

    print(
        f"[SUMMARIZER] Summary cached ({len(content)} chars, "
        f"{len(content.split())} words, {elapsed:.1f}s)",
        flush=True,
    )

    if cfg.DEBUG_LOGGING:
        print(f"[SUMMARIZER] Content: {content[:300]}...", flush=True)
