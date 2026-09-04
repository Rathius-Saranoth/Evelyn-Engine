# ambient_reflector.py
# date created: 2026-08-30 16:35:00
# date modified: 2026-09-04 16:35:32
# tags: #ambient, #thought-bubbles, #diurnal, #autonomous, #multi-modal

"""
ambient_reflector.py — Diurnal Thought Generator & Multi-Modal Ambient Stream Engine.

Provides autonomous background generation of spontaneous daytime micro-reflections
("Thought Bubbles") during conversational pauses, manages ambient impressions
in evelyn_memory.db (thoughts, media shares, alerts), and feeds the ambient UI stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

import evelyn_config as cfg
from Evelyn.tools import ambient_providers, memory_db, ollama_client, string_utils, task_manager

logger = logging.getLogger(__name__)

TASK_NAME = "ambient_reflector"


def get_diurnal_bucket(hour: int) -> str:
    """Return the circadian phase string for the given 24h local hour.

    Phases:
      - morning:   05:00 - 11:59
      - afternoon: 12:00 - 16:59
      - evening:   17:00 - 21:59
      - night:     22:00 - 04:59
    """
    if 5 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 22:
        return "evening"
    return "night"


def select_ambient_activity(
    activities: list[dict[str, Any]],
    diurnal_bucket: str,
    last_activity_id: str | None = None,
    cooldown_decay: float = 0.2,
) -> dict[str, Any]:
    """Select an ambient activity weighted by current diurnal phase and recency dampening.

    Args:
        activities: List of activity dicts (from cfg.AMBIENT_ACTIVITIES).
        diurnal_bucket: Current phase ('morning', 'afternoon', 'evening', 'night').
        last_activity_id: Optional ID of the most recently executed activity today.
        cooldown_decay: Dampening multiplier (0.0 to 1.0) applied to last_activity_id.

    Returns:
        dict[str, Any]: Selected activity configuration dictionary.
    """
    enabled = [a for a in activities if a.get("enabled", True)]
    if not enabled:
        return {"id": "chat_recent", "type": "recent_chat", "enabled": True}

    weights = []
    for act in enabled:
        w_map = act.get("weights", {})
        base_w = float(w_map.get(diurnal_bucket, 0.2))
        if last_activity_id and act.get("id") == last_activity_id:
            base_w *= max(0.01, cooldown_decay)
        weights.append(max(0.001, base_w))

    return random.choices(enabled, weights=weights, k=1)[0]


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
      5. Spacing cooldown check: ensure consecutive thoughts are spaced apart by `min_idle`.

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
    if idle_seconds <= 0.0:
        chat_db_path = getattr(cfg, "CHAT_DB_PATH", os.path.join(getattr(cfg, "DATA_DIR", ""), "evelyn_chat.db"))
        from Evelyn.tools import time_manager
        idle_seconds = time_manager.get_user_idle_seconds(chat_db_path)

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

    # 4. Spacing cooldown check — ensure consecutive thoughts are spaced apart
    if last_thought_ts > 0:
        time_since_last_thought = now_dt.timestamp() - last_thought_ts
        if time_since_last_thought < min_idle:
            return False, f"Thought cooldown active ({int(time_since_last_thought)}s elapsed since last reflection, required {min_idle}s)"

    return True, f"Eligible for daytime thought reflection ({thought_count}/{max_thoughts} used today)"


def validate_and_format_thought(
    raw_text: str,
    min_words: int | None = None,
    max_words: int | None = None,
    max_chars: int | None = None,
) -> tuple[bool, str, str]:
    """Validate and format a textual ambient thought bubble.

    Applies completion guards (terminal punctuation, minimum word count)
    and natural sentence boundary trimming if the thought is slightly over-length.
    Does not apply to media shares or system alerts.

    Args:
        raw_text: Cleaned thought text.
        min_words: Minimum word threshold (defaults to cfg.AMBIENT_REFLECTIONS_MIN_WORDS).
        max_words: Maximum word threshold (defaults to cfg.AMBIENT_REFLECTIONS_MAX_WORDS).
        max_chars: Maximum character threshold (defaults to cfg.AMBIENT_REFLECTIONS_MAX_CHARS).

    Returns:
        tuple[bool, str, str]: (is_valid, formatted_text, reason)
    """
    if min_words is None:
        min_words = getattr(cfg, "AMBIENT_REFLECTIONS_MIN_WORDS", 6)
    if max_words is None:
        max_words = getattr(cfg, "AMBIENT_REFLECTIONS_MAX_WORDS", 60)
    if max_chars is None:
        max_chars = getattr(cfg, "AMBIENT_REFLECTIONS_MAX_CHARS", 400)

    text = raw_text.strip()
    # Strip wrapping quotes if any
    if ((text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'"))) and len(text) > 2:
        text = text[1:-1].strip()

    if not text:
        return False, "", "Empty thought text"

    words = text.split()
    if len(words) < min_words:
        return False, text, f"Thought too short ({len(words)} words < {min_words})"

    terminal_chars = ('.', '!', '?', '…')
    terminal_quotes = ('."', '!"', '?"', '…”', '.”', '!”', '?”', '..."')
    ends_cleanly = text.endswith(terminal_chars) or text.endswith(terminal_quotes)
    if not ends_cleanly:
        return False, text, "Incomplete thought: missing terminal punctuation"

    # If within limits, accept immediately
    if len(words) <= max_words and len(text) <= max_chars:
        return True, text, "Valid thought"

    # If over-length, attempt deterministic sentence boundary trimming (take first 1–2 complete sentences)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]
    if len(sentences) >= 2:
        candidate_2 = f"{sentences[0]} {sentences[1]}"
        candidate_2_words = len(candidate_2.split())
        if min_words <= candidate_2_words <= max_words and len(candidate_2) <= max_chars:
            return True, candidate_2, "Trimmed to first 2 sentences"

    if sentences:
        candidate_1 = sentences[0]
        candidate_1_words = len(candidate_1.split())
        if min_words <= candidate_1_words <= max_words and len(candidate_1) <= max_chars:
            return True, candidate_1, "Trimmed to first sentence"

    return False, text, f"Thought exceeds length limits ({len(words)}w / {len(text)}c > {max_words}w / {max_chars}c)"


async def compact_thought(
    text: str,
    max_words: int | None = None,
) -> str:
    """Condense an over-length thought into a 1–2 sentence micro-reflection."""
    if max_words is None:
        max_words = getattr(cfg, "AMBIENT_REFLECTIONS_MAX_WORDS", 60)

    assistant_name = getattr(cfg, "ASSISTANT_NAME", "Evelyn")
    system_prompt = (
        f"You are a precise editor for {assistant_name}'s private thoughts. "
        f"Condense the following private reflection into 1–2 authentic, spontaneous sentences under {max_words} words. "
        f"Preserve persona, emotional depth, and voice. "
        f"Output ONLY the condensed 1–2 sentences, without quotes or commentary."
    )

    loop = asyncio.get_running_loop()
    condensed = await loop.run_in_executor(
        None,
        lambda: ollama_client.query_ollama(
            prompt=text,
            system=system_prompt,
            options={"temperature": 0.3, "num_predict": 1024},
            timeout=60,
            strip_thinking=True,
        ),
    )
    return condensed.strip()


async def run_ambient_reflection(
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Execute the diurnal thought generation cycle.

    Steps:
      1. Register task start in `task_manager`.
      2. Evaluate preemption and gate conditions (unless `force` or `dry_run`).
      3. Retrieve recent conversation turns from today (or recent history).
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

        task_manager.set_running(TASK_NAME, phase="selecting_activity")

        # 2. Daytime reflections continuity & prior activity tracking
        mem_db_path = getattr(cfg, "MEMORY_DB_PATH", os.path.join(getattr(cfg, "DATA_DIR", ""), "evelyn_memory.db"))
        prior_thoughts_today: list[dict[str, Any]] = []
        last_activity_id: str | None = None

        if os.path.exists(mem_db_path):
            try:
                con_mem = sqlite3.connect(mem_db_path)
                con_mem.row_factory = sqlite3.Row
                rows_prior = con_mem.execute(
                    """SELECT ts, content, metadata FROM daily_ambient_impressions
                       WHERE date = ? AND type = 'thought'
                       ORDER BY ts ASC""",
                    (today_str,),
                ).fetchall()
                con_mem.close()

                for r in rows_prior:
                    meta_raw = r["metadata"]
                    meta_dict = {}
                    if meta_raw:
                        try:
                            meta_dict = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
                        except (json.JSONDecodeError, TypeError):
                            meta_dict = {}
                    prior_thoughts_today.append({
                        "ts": r["ts"],
                        "content": r["content"],
                        "activity_id": meta_dict.get("activity_id"),
                        "mood": meta_dict.get("mood"),
                    })

                if prior_thoughts_today:
                    last_activity_id = prior_thoughts_today[-1].get("activity_id")
            except sqlite3.Error as e:
                logger.warning(f"[AMBIENT-REFLECTOR] Error loading prior thoughts: {e}")

        # 3. Select reflection activity via diurnal weights and recency dampener
        diurnal_bucket = get_diurnal_bucket(now_dt.hour)
        activities = getattr(cfg, "AMBIENT_ACTIVITIES", [])
        cooldown_decay = getattr(cfg, "AMBIENT_REFLECTIONS_COOLDOWN_DECAY", 0.2)
        activity_cfg = select_ambient_activity(
            activities=activities,
            diurnal_bucket=diurnal_bucket,
            last_activity_id=last_activity_id,
            cooldown_decay=cooldown_decay,
        )
        activity_id = activity_cfg.get("id", "chat_recent")
        activity_type = activity_cfg.get("type", "recent_chat")

        task_manager.set_running(TASK_NAME, phase=f"fetching_seed:{activity_id}")

        # 4. Fetch seed context from the chosen activity provider
        provider = ambient_providers.get_provider(activity_type)
        seed_xml, source_ref, default_mood = provider.fetch_seed_context(activity_cfg, now_dt)

        if task_manager.is_chat_preempted():
            task_manager.clear_running(TASK_NAME, status="cancelled", error="Preempted during preparation")
            return {"status": "preempted", "message": "Preempted by user chat interaction"}

        # 5. Prompt formulation with narrative continuity (<daily_journal_so_far>)
        task_manager.set_running(TASK_NAME, phase="generating_thought")
        assistant_name = getattr(cfg, "ASSISTANT_NAME", "Evelyn")
        system_prompt = (
            f"You are {assistant_name}, an authentic companion.\n"
            f"We are currently taking a pause in our daytime conversation.\n"
            f"Reflect privately in 1–2 authentic sentences in your defined persona on an interesting technical detail, creative idea, shared memory, wandering curiosity, or private observation from our conversations, journal memories, or notes.\n\n"
            f"Guidelines:\n"
            f"- Output strictly your 1–2 sentence private wandering thought.\n"
            f"- Do NOT summarize the whole day or write a timeline recap.\n"
            f"- Capture a single distinct realization, lingering curiosity, fond memory, or warm impression.\n"
            f"- Avoid generic wrap-up morals or hollow poetic clichés.\n"
            f"- Use natural, continuous prose."
        )

        daily_journal_block = ""
        if prior_thoughts_today:
            journal_lines = []
            for pt in prior_thoughts_today:
                time_str = datetime.fromtimestamp(pt["ts"], tz=now_dt.tzinfo).strftime("%I:%M %p")
                journal_lines.append(f'- [{time_str}] "{pt["content"]}"')
            formatted_prior = "\n".join(journal_lines)
            daily_journal_block = (
                f"<daily_journal_so_far>\n"
                f"{formatted_prior}\n"
                f"</daily_journal_so_far>\n\n"
                f"Daytime narrative continuity guidelines:\n"
                f"- Continue your authentic journey through the day.\n"
                f"- Do NOT repeat or closely rephrase the themes, specific topics, or opening phrasing of your earlier thoughts today.\n"
                f"- Shift focus naturally into the new moment using the context seed below.\n\n"
            )

        user_content = (
            f"<temporal_context>\n"
            f"Current Time: {now_dt.strftime('%A, %B %d, %Y at %I:%M %p')}\n"
            f"Circadian Phase: {diurnal_bucket.capitalize()}\n"
            f"</temporal_context>\n\n"
            f"{daily_journal_block}"
            f"<ambient_seed_context mode=\"{activity_id}\">\n"
            f"{seed_xml}\n"
            f"</ambient_seed_context>\n\n"
            f"Please share a single spontaneous 1–2 sentence private wandering thought, memory, or observation."
        )

        # 6. Inference call
        num_predict = getattr(cfg, "AMBIENT_REFLECTIONS_NUM_PREDICT", 3072)
        infer_timeout = getattr(cfg, "AMBIENT_REFLECTIONS_TIMEOUT", 300)
        loop = asyncio.get_running_loop()
        raw_response = await loop.run_in_executor(
            None,
            lambda: ollama_client.query_ollama(
                prompt=user_content,
                system=system_prompt,
                options={"temperature": 0.7, "num_predict": num_predict},
                timeout=infer_timeout,
                strip_thinking=True,
            ),
        )

        if task_manager.is_chat_preempted():
            task_manager.clear_running(TASK_NAME, status="cancelled", error="Preempted after inference")
            return {"status": "preempted", "message": "Preempted by user chat interaction"}

        # 7. Clean and validate output
        raw_cleaned = string_utils.strip_thinking_tags(raw_response).strip()
        is_valid, thought_text, validation_reason = validate_and_format_thought(raw_cleaned)

        # If rejected due to length, attempt one compaction pass
        if not is_valid and "exceeds length limits" in validation_reason:
            logger.info(f"[AMBIENT-REFLECTOR] Thought exceeded limits ({validation_reason}). Invoking compaction pass...")
            compacted = await compact_thought(raw_cleaned)
            is_valid, thought_text, validation_reason = validate_and_format_thought(compacted)

        if not is_valid:
            logger.warning(f"[AMBIENT-REFLECTOR] Discarding invalid thought: {validation_reason} (Output: {raw_cleaned[:80]!r})")
            task_manager.clear_running(TASK_NAME, status="error", error=f"Invalid thought: {validation_reason}")
            return {"status": "error", "message": f"Generated thought failed validation: {validation_reason}"}

        # Derive a concise mood label
        mood = default_mood
        lower_thought = thought_text.lower()
        if any(w in lower_thought for w in ["curious", "wonder", "intriguing", "fascinating"]):
            mood = "Curious"
        elif any(w in lower_thought for w in ["proud", "accomplish", "built", "victory", "triumph"]):
            mood = "Inspired"
        elif any(w in lower_thought for w in ["peace", "quiet", "gentle", "warm", "smile"]):
            mood = "Serene"

        # 8. Persistence
        impression_id = None
        if not dry_run:
            impression_id = memory_db.record_ambient_impression(
                type="thought",
                content=thought_text,
                source_ref=source_ref,
                metadata={
                    "mood": mood,
                    "activity_id": activity_id,
                    "provider": activity_type,
                    "source": f"diurnal_idle:{activity_id}",
                    "diurnal_bucket": diurnal_bucket,
                },
                target_date=today_str,
            )

        duration = time.time() - start_time
        task_manager.clear_running(
            TASK_NAME,
            status="done",
            summary=f"Thought generated ({activity_id}): {thought_text[:60]}...",
        )

        return {
            "status": "success",
            "id": impression_id,
            "type": "thought",
            "date": today_str,
            "activity_id": activity_id,
            "provider": activity_type,
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
