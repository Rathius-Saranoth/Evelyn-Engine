# context_summarizer.py
# date created: 2026-04-24 20:17:58
# date modified: 2026-06-07 10:28:29
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

The summarizer uses the same call_ollama_full() path as the main chat loop,
so Ollama reuses the already-loaded model with zero swap overhead.

All config is read from evelyn_config.py (single source of truth).
"""

import asyncio
import hashlib
import importlib
import sqlite3
import time

import evelyn_config as cfg

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------
# The summary only matters for the current thread. Thread breaks and server
# restarts naturally invalidate it. A stale/missing summary is harmless —
# the model just doesn't have extended recall for that one turn.

_cache = {
    "summary": "",          # The generated summary text
    "msg_hash": "",         # Hash of message IDs used to generate it
    "last_updated": 0.0,    # Timestamp of last successful summarization
}

# Guard against concurrent summarization tasks
_summarizing = False

# Reference to the in-flight summarization asyncio.Task so we can cancel it
# if a new chat request arrives before it finishes (prevents Ollama queue contention)
_summary_task = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_conversation_summary() -> str:
    """Return the cached conversation summary for injection into the system prompt.

    Returns:
        str: The cached summary text, or an empty string if none exists.
    """
    return _cache["summary"]


def invalidate_summary_cache():
    """Clear the cached summary when starting a new thread."""
    global _cache
    _cache = {"summary": "", "msg_hash": "", "last_updated": 0.0}
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


def _get_summary_window() -> tuple[list[dict], str]:
    """Fetch the messages that should be summarized and compute their hash.

    Returns:
        (messages, msg_hash) where messages is a list of {role, content} dicts
        and msg_hash is a hex digest of the message IDs for change detection.
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
        "SELECT id, role, content FROM messages WHERE id > ? ORDER BY id DESC",
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
        return [], ""

    messages = [{"role": r["role"], "content": r["content"]} for r in summary_rows]

    # Hash the message IDs for change detection
    id_string = ",".join(str(r["id"]) for r in summary_rows)
    msg_hash = hashlib.md5(id_string.encode()).hexdigest()

    return messages, msg_hash


def _format_messages_for_prompt(messages: list[dict]) -> str:
    """Format message list into a readable conversation transcript.

    Args:
        messages: A list of message dictionaries.

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
        lines.append(f"{role_label}: {content}")
    return "\n\n".join(lines)


async def _do_summary_update():
    """Core summarization logic. Called by trigger_summary_update()."""
    messages, msg_hash = _get_summary_window()

    if not messages:
        # Nothing to summarize (conversation too short)
        if _cache["summary"]:
            # Edge case: had a summary but now don't (e.g. thread break
            # happened but invalidate wasn't called). Clear it.
            _cache["summary"] = ""
            _cache["msg_hash"] = ""
        return

    if msg_hash == _cache["msg_hash"]:
        # Messages haven't changed since last summary — skip
        return

    # Build the summarization prompt
    max_words = cfg.SUMMARY_MAX_WORDS
    conversation_text = _format_messages_for_prompt(messages)

    summary_prompt = (
        "Summarize the following conversation between a user (Ricky) and an AI assistant (Evelyn). "
        f"Extract ONLY the key facts, decisions, topics discussed, and emotional tone. "
        "Do NOT include greetings, filler, or meta-commentary about the summarization. "
        f"Keep your summary under {max_words} words. "
        "Use present tense for ongoing topics and past tense for concluded ones. "
        "Format as a concise paragraph, not bullet points."
        f"\n\nCONVERSATION:\n{conversation_text}"
    )

    summary_messages = [
        {"role": "system", "content": "You are a precise summarizer. Output only the summary, nothing else."},
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

    print(
        f"[SUMMARIZER] Summary cached ({len(content)} chars, "
        f"{len(content.split())} words, {elapsed:.1f}s)",
        flush=True,
    )

    if cfg.DEBUG_LOGGING:
        print(f"[SUMMARIZER] Content: {content[:300]}...", flush=True)
