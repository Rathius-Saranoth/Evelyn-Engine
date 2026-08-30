# ambient_reflector.py
# date created: 2026-08-30 16:35:00
# date modified: 2026-08-30 16:34:06
# tags: #ambient, #thought-bubbles, #diurnal, #autonomous, #multi-modal

"""
ambient_reflector.py — Diurnal Thought Generator & Multi-Modal Ambient Stream Engine.

Provides autonomous background generation of spontaneous daytime micro-reflections
("Thought Bubbles") during conversational pauses, manages ambient impressions
in evelyn_memory.db (thoughts, media shares, alerts), and feeds the ambient UI stream.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time as dtime
import logging
import os
import sqlite3
import time
from typing import Any

import evelyn_config as cfg
from Evelyn.tools import memory_db, ollama_client, string_utils, task_manager

logger = logging.getLogger(__name__)

TASK_NAME = "ambient_reflector"


def should_generate_idle_thought(
    now_dt: datetime | None = None,
    idle_seconds: float = 0.0,
) -> tuple[bool, str]:
    """Evaluate whether engine conditions permit generating a daytime thought bubble.

    Gate checks:
      1. Master switch `AMBIENT_REFLECTIONS_ENABLED`.
      2. Circadian diurnal window (`AMBIENT_REFLECTIONS_START_HOUR` to `AMBIENT_REFLECTIONS_END_HOUR` local time).
      3. Conversational silence duration (`idle_seconds >= AMBIENT_REFLECTIONS_MIN_IDLE_SECONDS`).
      4. Daily thought quota (`count < AMBIENT_REFLECTIONS_MAX_THOUGHTS_PER_DAY` on local date).
      5. Active conversation verification (new user/assistant turns exist in `evelyn_chat.db`
         since the last generated thought bubble).

    Args:
        now_dt: Optional local datetime object (defaults to current system time).
        idle_seconds: Seconds elapsed since the last user chat interaction.

    Returns:
        tuple[bool, str]: (eligible, reason_description)
    """
    if not getattr(cfg, "AMBIENT_REFLECTIONS_ENABLED", True):
        return False, "Ambient reflections disabled in configuration"

    if now_dt is None:
        now_dt = datetime.now(UTC).astimezone()

    start_hour = getattr(cfg, "AMBIENT_REFLECTIONS_START_HOUR", 9)
    end_hour = getattr(cfg, "AMBIENT_REFLECTIONS_END_HOUR", 21)

    # 1. Circadian diurnal window check (e.g. 09:00 to 21:00 local time)
    if not (start_hour <= now_dt.hour < end_hour):
        return False, f"Outside diurnal window ({start_hour:02d}:00–{end_hour:02d}:00, current: {now_dt.hour:02d}:{now_dt.minute:02d})"

    # 2. Conversational inactivity threshold check (e.g. >= 2 hours of quiet)
    min_idle = getattr(cfg, "AMBIENT_REFLECTIONS_MIN_IDLE_SECONDS", 7200)
    if idle_seconds < min_idle:
        return False, f"Inactivity ({int(idle_seconds)}s) below required threshold ({min_idle}s)"

    today_str = now_dt.strftime("%Y-%m-%d")
    max_thoughts = getattr(cfg, "AMBIENT_REFLECTIONS_MAX_THOUGHTS_PER_DAY", 3)

    # 3. Daily thought count ceiling check
    mem_db_path = getattr(cfg, "MEMORY_DB_PATH", os.path.join(getattr(cfg, "DATA_DIR", ""), "evelyn_memory.db"))
    if not os.path.exists(mem_db_path):
        return False, "Memory database file not found"

    con_mem = sqlite3.connect(mem_db_path)
    try:
        row = con_mem.execute(
            "SELECT COUNT(*), MAX(ts) FROM daily_ambient_impressions WHERE date = ? AND type = 'thought'",
            (today_str,),
        ).fetchone()
        thought_count = row[0] if row else 0
        last_thought_ts = row[1] if (row and row[1]) else 0.0
    except sqlite3.Error as e:
        logger.warning(f"[AMBIENT-REFLECTOR] Error querying daily_ambient_impressions: {e}")
        return False, f"Database error: {e}"
    finally:
        con_mem.close()

    if thought_count >= max_thoughts:
        return False, f"Daily thought limit reached ({thought_count}/{max_thoughts} for {today_str})"

    # 4. Chat history verification — ensure new turns occurred today after last_thought_ts
    day_start = datetime.combine(now_dt.date(), dtime.min).replace(tzinfo=now_dt.tzinfo).timestamp()
    query_start_ts = max(day_start, last_thought_ts)

    chat_db_path = getattr(cfg, "CHAT_DB_PATH", os.path.join(getattr(cfg, "DATA_DIR", ""), "evelyn_chat.db"))
    if not os.path.exists(chat_db_path):
        return False, "Chat database file not found"

    con_chat = sqlite3.connect(chat_db_path)
    try:
        chat_row = con_chat.execute(
            """SELECT COUNT(*) FROM messages
               WHERE ts > ? AND role IN ('user', 'assistant')
                 AND content != '[THREAD_BREAK]'
                 AND content NOT LIKE '[PLACEHOLDER]%'""",
            (query_start_ts,),
        ).fetchone()
        new_turns = chat_row[0] if chat_row else 0
    except sqlite3.Error as e:
        logger.warning(f"[AMBIENT-REFLECTOR] Error querying chat messages: {e}")
        return False, f"Chat database error: {e}"
    finally:
        con_chat.close()

    if new_turns < 2:
        return False, f"Insufficient new conversation turns ({new_turns}) since last reflection"

    return True, f"Eligible for daytime thought reflection ({thought_count}/{max_thoughts} used today, {new_turns} new turns)"


async def run_ambient_reflection(
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Execute the diurnal thought generation cycle.

    Steps:
      1. Register task start in `task_manager`.
      2. Evaluate preemption and gate conditions (unless `force` or `dry_run`).
      3. Retrieve recent conversation turns from today.
      4. Query Ollama for a lightweight 1–2 sentence wandering thought.
      5. Strip thinking tags and persist to `daily_ambient_impressions` with `type="thought"`.
      6. Return execution metrics and generated impression payload.

    Args:
        dry_run: If True, generates output without persisting to the database.
        force: If True, bypasses inactivity and circadian gate checks.

    Returns:
        dict[str, Any]: Status payload with generated thought.
    """
    task_manager.set_running(TASK_NAME, phase="initializing")
    start_time = time.time()

    try:
        if task_manager.is_chat_preempted():
            task_manager.clear_running(TASK_NAME, status="cancelled", error="Preempted before start")
            return {"status": "preempted", "message": "Preempted by user chat interaction"}

        now_dt = datetime.now(UTC).astimezone()
        today_str = now_dt.strftime("%Y-%m-%d")

        # 1. Gate evaluation
        if not force and not dry_run:
            # Estimate silence from last user message
            last_user_ts = 0.0
            chat_db_path = getattr(cfg, "CHAT_DB_PATH", os.path.join(getattr(cfg, "DATA_DIR", ""), "evelyn_chat.db"))
            if os.path.exists(chat_db_path):
                con = sqlite3.connect(chat_db_path)
                row = con.execute("SELECT MAX(ts) FROM messages WHERE role = 'user'").fetchone()
                con.close()
                if row and row[0]:
                    last_user_ts = float(row[0])

            idle_secs = time.time() - last_user_ts if last_user_ts > 0 else 99999.0
            eligible, reason = should_generate_idle_thought(now_dt=now_dt, idle_seconds=idle_secs)
            if not eligible:
                logger.info(f"[AMBIENT-REFLECTOR] Skipping: {reason}")
                task_manager.clear_running(TASK_NAME, status="idle", summary=f"Skipped: {reason}")
                return {"status": "skipped", "reason": reason}

        task_manager.set_running(TASK_NAME, phase="retrieving_history")

        # 2. Retrieve recent conversation turns from today (up to 15 turns)
        day_start = datetime.combine(now_dt.date(), dtime.min).replace(tzinfo=now_dt.tzinfo).timestamp()
        chat_db_path = getattr(cfg, "CHAT_DB_PATH", os.path.join(getattr(cfg, "DATA_DIR", ""), "evelyn_chat.db"))
        con = sqlite3.connect(chat_db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT role, content FROM messages
               WHERE ts >= ? AND role IN ('user', 'assistant')
                 AND content != '[THREAD_BREAK]'
                 AND content NOT LIKE '[PLACEHOLDER]%'
               ORDER BY id DESC LIMIT 15""",
            (day_start,),
        ).fetchall()
        con.close()

        if not rows:
            task_manager.clear_running(TASK_NAME, status="idle", summary="No messages found today")
            return {"status": "skipped", "reason": "No messages found today"}

        # Reverse to chronological order
        recent_turns = list(reversed(rows))
        history_transcript = "\n".join(
            f"{r['role'].upper()}: {r['content']}" for r in recent_turns
        )

        task_manager.set_running(TASK_NAME, phase="generating_thought")

        if task_manager.is_chat_preempted():
            task_manager.clear_running(TASK_NAME, status="cancelled", error="Preempted during preparation")
            return {"status": "preempted", "message": "Preempted by user chat interaction"}

        # 3. Prompt formulation
        assistant_name = getattr(cfg, "ASSISTANT_NAME", "Evelyn")
        system_prompt = (
            f"You are {assistant_name}, an authentic companion.\n"
            f"We are currently taking a pause in our daytime conversation.\n"
            f"Reflect privately in 1–2 authentic sentences in your defined persona on an interesting technical detail, creative idea, shared moment, or private observation from our conversations earlier today.\n\n"
            f"Guidelines:\n"
            f"- Output strictly your 1–2 sentence private wandering thought.\n"
            f"- Do NOT summarize the whole day or write a timeline recap.\n"
            f"- Capture a single distinct realization, lingering curiosity, or warm impression.\n"
            f"- Avoid generic wrap-up morals or hollow poetic clichés.\n"
            f"- Use natural, continuous prose."
        )

        user_content = (
            f"<today_conversation_sample>\n"
            f"{history_transcript}\n"
            f"</today_conversation_sample>\n\n"
            f"Please share a single spontaneous 1–2 sentence private thought or observation based on our conversation today."
        )

        # 4. Inference call
        loop = asyncio.get_running_loop()
        raw_response = await loop.run_in_executor(
            None,
            lambda: ollama_client.query_ollama(
                prompt=user_content,
                system=system_prompt,
                options={"temperature": 0.7, "num_predict": 256},
                timeout=120,
                strip_thinking=True,
            ),
        )

        if task_manager.is_chat_preempted():
            task_manager.clear_running(TASK_NAME, status="cancelled", error="Preempted after inference")
            return {"status": "preempted", "message": "Preempted by user chat interaction"}

        # 5. Clean output
        thought_text = string_utils.strip_thinking_tags(raw_response).strip()
        # Strip surrounding markdown quotes if any
        if thought_text.startswith('"') and thought_text.endswith('"') and len(thought_text) > 2:
            thought_text = thought_text[1:-1].strip()

        if not thought_text:
            task_manager.clear_running(TASK_NAME, status="error", error="Empty thought output")
            return {"status": "error", "message": "Model generated empty output"}

        # Derive a concise mood label
        mood = "Reflective"
        lower_thought = thought_text.lower()
        if any(w in lower_thought for w in ["curious", "wonder", "intriguing", "fascinating"]):
            mood = "Curious"
        elif any(w in lower_thought for w in ["proud", "accomplish", "built", "victory", "triumph"]):
            mood = "Inspired"
        elif any(w in lower_thought for w in ["peace", "quiet", "gentle", "warm", "smile"]):
            mood = "Serene"

        # 6. Persistence
        impression_id = None
        if not dry_run:
            impression_id = memory_db.record_ambient_impression(
                type="thought",
                content=thought_text,
                source_ref=f"chat_turns:{len(recent_turns)}",
                metadata={"mood": mood, "source": "diurnal_idle"},
                target_date=today_str,
            )

        duration = time.time() - start_time
        task_manager.clear_running(
            TASK_NAME,
            status="done",
            summary=f"Thought generated: {thought_text[:60]}...",
        )

        return {
            "status": "success",
            "id": impression_id,
            "type": "thought",
            "date": today_str,
            "thought": thought_text,
            "mood": mood,
            "duration": round(duration, 2),
            "dry_run": dry_run,
        }

    except Exception as e:
        logger.exception(f"[AMBIENT-REFLECTOR] Execution error: {e}")
        task_manager.clear_running(TASK_NAME, status="error", error=str(e))
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Multi-Modal Ambient Helpers (Multi-Channel Ingestion)
# ---------------------------------------------------------------------------


def record_media_share(
    content: str,
    media_id: str,
    metadata: dict | None = None,
    target_date: str | None = None,
) -> int:
    """Record a proactive media share from Evelyn (outfit concepts, library artwork, visualizations).

    Args:
        content: Caption or context text accompanying the media.
        media_id: UUID referencing evelyn_media.db or media file path.
        metadata: Optional metadata (e.g. {"category": "wardrobe", "media_url": "..."}).
        target_date: Optional local date string.

    Returns:
        int: The primary key of the newly created ambient impression.
    """
    return memory_db.record_ambient_impression(
        type="media_share",
        content=content,
        source_ref=f"media:{media_id}",
        media_id=media_id,
        metadata=metadata,
        target_date=target_date,
    )


def record_system_alert(
    content: str,
    source_ref: str,
    metadata: dict | None = None,
    target_date: str | None = None,
) -> int:
    """Record an ambient system insight, proactive check-in alert, or task completion badge.

    Args:
        content: The alert or insight summary text.
        source_ref: Origin identifier (e.g. "health:sleep_anomaly", "research:1049").
        metadata: Optional styling or payload dictionary.
        target_date: Optional local date string.

    Returns:
        int: The primary key of the newly created ambient impression.
    """
    return memory_db.record_ambient_impression(
        type="system_alert",
        content=content,
        source_ref=source_ref,
        metadata=metadata,
        target_date=target_date,
    )
