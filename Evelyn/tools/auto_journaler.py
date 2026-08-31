# auto_journaler.py
# date created: 2026-08-30 15:45:00
# date modified: 2026-08-31 17:28:10
# tags: #journal, #autonomous, #daemon, #map-reduce, #compaction, #nightly

"""
auto_journaler.py — Autonomous After-Hours Journal Daemon & Map-Reduce Compaction.

Exports:
    resolve_target_journal_date()    — Resolve target date accounting for midnight crossovers.
    should_trigger_auto_journal()    — Multi-gate check for late-night autonomous triggering.
    compact_history_map_reduce()     — In-memory map-reduce compaction for high-turn conversation days.
    run_auto_journaling()            — End-to-end autonomous background journaling worker.

Key config: evelyn_config.py (AUTO_JOURNAL_*), journal_manager.py, task_manager.py
See also: reference/engine_architecture.md
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any

# Anchoring paths
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
for _d in (ROOT_DIR, TOOLS_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import evelyn_config as cfg
import evelyn_server
from Evelyn.tools import journal_manager, ollama_client, task_manager
from Evelyn.tools.evelyn_tools import MODEL_TOOL_DEFINITIONS
from Evelyn.tools.string_utils import escape_xml_content, wrap_xml_envelope

logger = logging.getLogger("evelyn.auto_journaler")

TASK_NAME = "auto_journaler"
_auto_journal_task: asyncio.Task | None = None


def resolve_target_journal_date(
    now_dt: datetime | None = None,
) -> tuple[date, float, float]:
    """Resolve the logical target calendar date for journal writing.

    Handles midnight crossovers: if running during late-night hours before the
    configured end hour (e.g. 00:00 to 04:00), the target date is resolved to
    the previous calendar day (yesterday), representing the conclusion of that
    day's active session.

    Args:
        now_dt: Optional timezone-aware datetime. Defaults to current local time.

    Returns:
        tuple[date, float, float]: (target_date, target_day_start_ts, target_day_end_ts)
    """
    if now_dt is None:
        now_dt = datetime.now(UTC).astimezone()

    end_hour = getattr(cfg, "AUTO_JOURNAL_END_HOUR", 4)
    target_date = (now_dt - timedelta(days=1)).date() if now_dt.hour < end_hour else now_dt.date()

    day_start = datetime.combine(target_date, dtime.min).replace(tzinfo=UTC).astimezone()
    day_end = datetime.combine(target_date, dtime.max).replace(tzinfo=UTC).astimezone()

    return target_date, day_start.timestamp(), day_end.timestamp()


def should_trigger_auto_journal(
    now_dt: datetime | None = None,
    idle_seconds: float = 0.0,
) -> tuple[bool, str]:
    """Evaluate whether all gate conditions are met for autonomous after-hours journaling.

    Gate checks:
      1. Enabled switch (`AUTO_JOURNAL_ENABLED`).
      2. Circadian late-night window (`AUTO_JOURNAL_START_HOUR` to `AUTO_JOURNAL_END_HOUR`).
      3. Inactivity threshold (`AUTO_JOURNAL_IDLE_THRESHOLD` or 02:30 failsafe).
      4. Vault collision check: target date note must not already exist.
      5. Minimum activity threshold: at least `AUTO_JOURNAL_MIN_MESSAGES` must have occurred.

    Args:
        now_dt: Optional current datetime.
        idle_seconds: Inactivity elapsed in seconds since last user interaction.

    Returns:
        tuple[bool, str]: (should_trigger, reason_message)
    """
    if not getattr(cfg, "AUTO_JOURNAL_ENABLED", True):
        return False, "Auto-journaling is disabled in configuration."

    if now_dt is None:
        now_dt = datetime.now(UTC).astimezone()

    start_hour = getattr(cfg, "AUTO_JOURNAL_START_HOUR", 23)
    end_hour = getattr(cfg, "AUTO_JOURNAL_END_HOUR", 4)

    # 1. Circadian window check
    in_window = now_dt.hour >= start_hour or now_dt.hour < end_hour
    if not in_window:
        return False, f"Current hour ({now_dt.hour}:00) is outside after-hours window ({start_hour}:00–{end_hour}:00)."

    # 2. Inactivity threshold check (allow standard threshold or fixed 2:30 AM failsafe)
    idle_threshold = getattr(cfg, "AUTO_JOURNAL_IDLE_THRESHOLD", 5400)
    is_late_failsafe = now_dt.hour == 2 and now_dt.minute >= 30 and idle_seconds >= 1800
    if idle_seconds < idle_threshold and not is_late_failsafe:
        return False, f"Inactivity duration ({idle_seconds / 60:.1f}m) below threshold ({idle_threshold / 60:.1f}m)."

    # 3. Resolve target date
    target_date, day_start_ts, day_end_ts = resolve_target_journal_date(now_dt)
    target_date_str = target_date.strftime("%Y-%m-%d")

    # 4. Vault collision check: does a journal note already exist for target date?
    existing_path = journal_manager._resolve_journal_filepath(target_date_str)
    if existing_path and os.path.exists(existing_path):
        return False, f"Journal entry for {target_date_str} already exists in vault ({os.path.basename(existing_path)})."

    # 5. Database activity check
    chat_db_path = getattr(cfg, "CHAT_DB_PATH", os.path.join(getattr(cfg, "DATA_DIR", ""), "evelyn_chat.db"))
    min_messages = getattr(cfg, "AUTO_JOURNAL_MIN_MESSAGES", 4)

    try:
        con = sqlite3.connect(chat_db_path)
        cur = con.cursor()
        row = cur.execute(
            """SELECT COUNT(*) FROM messages
               WHERE ts >= ? AND ts <= ?
                 AND role IN ('user', 'assistant')
                 AND content != '[THREAD_BREAK]'
                 AND content NOT LIKE '[PLACEHOLDER]%'""",
            (day_start_ts, day_end_ts),
        ).fetchone()
        con.close()
        msg_count = row[0] if row else 0
    except (sqlite3.Error, OSError) as e:
        return False, f"Database error verifying target date activity: {e}"

    if msg_count < min_messages:
        return False, f"Insufficient conversation turns on {target_date_str} ({msg_count} < {min_messages})."

    return True, f"Eligible for autonomous journaling for {target_date_str} ({msg_count} turns recorded)."


async def compact_history_map_reduce(
    messages: list[dict],
    chunk_size: int = 25,
    safe_budget: int = 16000,
) -> list[dict]:
    """Compress high-turn conversation transcripts using chronological Map-Reduce batching.

    If the total conversation fits within the safe token budget, returns raw messages.
    If it exceeds the budget, older turns are sliced into chunks (~25 turns each) and
    condensed into dense bullet digests of concrete actions, tools, projects, and banter,
    which are then prepended to recent raw evening turns.

    Args:
        messages: Chronological message dictionary list for the target date.
        chunk_size: Number of message turns per compression batch.
        safe_budget: Token ceiling before triggering map-reduce compression.

    Returns:
        list[dict]: Compacted message list ready for journal tool synthesis.
    """
    total_tokens = sum(evelyn_server._estimate_message_tokens(m) for m in messages)
    if total_tokens <= safe_budget or len(messages) <= (chunk_size + 4):
        return messages

    logger.info(
        f"[AUTO-JOURNAL] High-turn transcript detected ({len(messages)} msgs, ~{total_tokens} tokens > {safe_budget}). "
        f"Executing Map-Reduce history compaction..."
    )

    # Keep the most recent chunk_size turns raw (e.g. evening wind-down conversation)
    raw_recent_turns = messages[-chunk_size:]
    older_turns = messages[:-chunk_size]

    # Slice older turns into chronological blocks
    blocks = [older_turns[i : i + chunk_size] for i in range(0, len(older_turns), chunk_size)]
    block_digests: list[str] = []

    compaction_system = (
        "You are a dense biographical memory compressor. "
        "Extract a concise bulleted record of all concrete actions, physical tasks, creative projects, "
        "tools used, technical subjects explored, and distinctive banter from this conversation block. "
        "Keep specific names, tools, topics, and humorous details; strictly omit small talk and generic pleasantries."
    )

    for b_idx, block in enumerate(blocks, 1):
        if task_manager.is_chat_preempted():
            logger.info("[AUTO-JOURNAL] Chat preemption detected during map compaction. Yielding.")
            raise asyncio.CancelledError("Preempted by incoming chat interaction")

        # Format block transcript
        transcript_lines = []
        for m in block:
            role_label = m.get("role", "unknown").upper()
            content = m.get("content", "").strip()
            transcript_lines.append(f"{role_label}: {content}")
        block_text = "\n".join(transcript_lines)

        block_prompt = f"Transcript Block {b_idx}/{len(blocks)}:\n\n{block_text}\n\nDense Activity & Topic Summary:"

        try:
            digest = await asyncio.to_thread(
                ollama_client.query_ollama,
                prompt=block_prompt,
                system=compaction_system,
                options={"temperature": 0.2, "num_predict": 1024},
                strip_thinking=True,
            )
            if digest.strip():
                block_digests.append(f"### Activity Digest (Part {b_idx})\n{digest.strip()}")
        except (OSError, RuntimeError, ValueError) as e:
            logger.warning(f"[AUTO-JOURNAL] Warning: Compaction block {b_idx} extraction failed: {e}")

    compiled_digest_text = "\n\n".join(block_digests)
    digest_envelope = (
        f"<day_history_digest>\n"
        f"The following bullet digests summarize earlier activities and discussions from today:\n\n"
        f"{compiled_digest_text}\n"
        f"</day_history_digest>"
    )

    compacted_messages = [
        {"role": "system", "content": digest_envelope},
        *raw_recent_turns,
    ]
    new_token_est = sum(evelyn_server._estimate_message_tokens(m) for m in compacted_messages)
    logger.info(
        f"[AUTO-JOURNAL] Compaction complete: reduced {len(messages)} turns (~{total_tokens} tokens) "
        f"to {len(compacted_messages)} items (~{new_token_est} tokens)."
    )
    return compacted_messages


async def run_auto_journaling(
    dry_run: bool = False,
    target_date_override: date | None = None,
) -> dict[str, Any]:
    """Execute the full autonomous background journaling cycle.

    Steps:
      1. Register task start in `task_manager`.
      2. Check preemption and evaluate gate conditions.
      3. Retrieve target day's transcript from chat DB.
      4. Apply Map-Reduce compaction if transcript exceeds safe token budget.
      5. Inject master journaling procedure protocol into `<context_retrieval>`.
      6. Query Ollama tool loop for `write_journal_entry`.
      7. Save formatted entry directly to Obsidian vault via `journal_manager`.
      8. Mark task complete and return execution metrics.

    Args:
        dry_run: If True, generates output without writing to the Obsidian vault.
        target_date_override: Optional explicit date object to target for testing/simulation.

    Returns:
        dict[str, Any]: Execution outcome status dictionary.
    """
    task_manager.set_running(TASK_NAME, phase="initializing")
    start_time = time.time()

    try:
        if task_manager.is_chat_preempted():
            task_manager.clear_running(TASK_NAME, status="cancelled", error="Preempted before start")
            return {"status": "preempted", "message": "Preempted by user chat interaction"}

        # 1. Gate evaluation (skipped if explicit date override or dry-run requested)
        if not dry_run and target_date_override is None:
            eligible, reason = should_trigger_auto_journal()
            if not eligible:
                logger.info(f"[AUTO-JOURNAL] Skipping execution: {reason}")
                task_manager.clear_running(TASK_NAME, status="idle", summary=f"Skipped: {reason}")
                return {"status": "skipped", "reason": reason}

        # 2. Resolve target date and retrieve messages
        now_dt = datetime.now(UTC).astimezone()
        if target_date_override is not None:
            target_date = target_date_override
            day_start = datetime.combine(target_date, dtime.min).replace(tzinfo=UTC).astimezone()
            day_end = datetime.combine(target_date, dtime.max).replace(tzinfo=UTC).astimezone()
            day_start_ts, day_end_ts = day_start.timestamp(), day_end.timestamp()
        else:
            target_date, day_start_ts, day_end_ts = resolve_target_journal_date(now_dt)

        target_date_str = target_date.strftime("%Y-%m-%d")

        chat_db_path = getattr(cfg, "CHAT_DB_PATH", os.path.join(getattr(cfg, "DATA_DIR", ""), "evelyn_chat.db"))
        con = sqlite3.connect(chat_db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT id, role, content, tools_used, ts FROM messages
               WHERE ts >= ? AND ts <= ?
                 AND role IN ('user', 'assistant')
                 AND content != '[THREAD_BREAK]'
                 AND content NOT LIKE '[PLACEHOLDER]%'
               ORDER BY id ASC""",
            (day_start_ts, day_end_ts),
        ).fetchall()
        con.close()

        if not rows:
            task_manager.clear_running(TASK_NAME, status="idle", summary="No messages found")
            return {"status": "skipped", "reason": "No messages found for target date"}

        # Assemble formatted messages
        raw_messages: list[dict] = []
        for r in rows:
            role = r["role"]
            content = r["content"]
            if role == "user":
                content = f"{evelyn_server._time_of_day_label(r['ts'])}{content}"
            elif role == "assistant" and r["tools_used"]:
                tools_summary = r["tools_used"].strip()
                if tools_summary:
                    content = f"{content}\n\n[Tools Executed: {tools_summary}]"
            raw_messages.append({"role": role, "content": content})

        # Check preemption
        if task_manager.is_chat_preempted():
            task_manager.clear_running(TASK_NAME, status="cancelled", error="Preempted during assembly")
            return {"status": "preempted", "message": "Preempted by user chat interaction"}

        # 3. Map-Reduce compaction
        task_manager.set_running(TASK_NAME, phase="compaction")
        num_ctx = getattr(cfg, "NUM_CTX", 32768)
        tool_predict = getattr(cfg, "TOOL_LOOP_NUM_PREDICT", 8192)
        safe_budget = max(4000, num_ctx - (3500 + 1500 + 2500 + tool_predict + 1000))
        chunk_size = getattr(cfg, "AUTO_JOURNAL_CHUNK_SIZE", 25)

        compacted_history = await compact_history_map_reduce(
            raw_messages, chunk_size=chunk_size, safe_budget=safe_budget
        )

        # 4. System prompt + Ambient Stream
        from Evelyn.tools import memory_db

        task_manager.set_running(TASK_NAME, phase="synthesis")
        system = evelyn_server.load_system_prompt()

        # Query daytime ambient impressions for cross-layer synthesis
        unconsumed_impressions = memory_db.get_unconsumed_ambient_impressions(target_date_str)
        if unconsumed_impressions:
            impressions_lines = []
            for imp in unconsumed_impressions:
                imp_type = imp.get("type", "thought")
                imp_ts = imp.get("ts")
                time_str = datetime.fromtimestamp(imp_ts, tz=now_dt.tzinfo).strftime("%H:%M") if imp_ts else ""
                imp_content = escape_xml_content(imp.get("content", ""))
                impressions_lines.append(f'  <impression type="{imp_type}" time="{time_str}">{imp_content}</impression>')
            ambient_stream_xml = wrap_xml_envelope("ambient_stream", body=impressions_lines)
            context_retrieval_xml = f"<context_retrieval>\n{ambient_stream_xml}\n</context_retrieval>"
            system += f"\n\n{context_retrieval_xml}"

        prompt_messages = [
            {"role": "system", "content": system},
            *compacted_history,
            {
                "role": "user",
                "content": (
                    f"It is late night and our active day ({target_date.strftime('%A, %b %d, %Y')}) has drawn to a close. "
                    f"Please record today's reflection journal entry using the write_journal_entry tool."
                ),
            },
        ]

        if task_manager.is_chat_preempted():
            task_manager.clear_running(TASK_NAME, status="cancelled", error="Preempted before tool generation")
            return {"status": "preempted", "message": "Preempted by user chat interaction"}

        # 5. Call Ollama tool loop
        logger.info(f"[AUTO-JOURNAL] Querying model for autonomous journal entry generation ({target_date_str})...")
        resp = await evelyn_server.call_ollama_full(
            prompt_messages,
            tools=MODEL_TOOL_DEFINITIONS,
            num_predict_override=tool_predict,
        )

        msg = resp.get("message", {})
        tool_calls = msg.get("tool_calls", [])

        if not tool_calls:
            err_msg = "Model did not emit write_journal_entry tool call"
            logger.warning(f"[AUTO-JOURNAL] {err_msg}: {msg.get('content', '')}")
            task_manager.clear_running(TASK_NAME, status="error", error=err_msg)
            return {"status": "error", "message": err_msg, "raw_content": msg.get("content", "")}

        # Parse tool arguments
        journal_args = None
        for tc in tool_calls:
            fn = tc.get("function", {})
            if fn.get("name") == "write_journal_entry":
                journal_args = fn.get("arguments", {})
                break

        if not journal_args:
            err_msg = "No matching write_journal_entry in tool calls"
            task_manager.clear_running(TASK_NAME, status="error", error=err_msg)
            return {"status": "error", "message": err_msg}

        mood = journal_args.get("mood", "Reflective")
        vibe_check = journal_args.get("vibe_check", "")
        narrative = journal_args.get("narrative", "")
        message_in_a_bottle = journal_args.get("message_in_a_bottle", "")
        tags_arg = journal_args.get("tags", "")
        tags = (
            [t.strip().lstrip("#") for t in tags_arg.split(",") if t.strip()]
            if isinstance(tags_arg, str)
            else tags_arg
        )

        # 6. Save via journal_manager (automatically handles ambient consumption upon disk write)
        if dry_run:
            result_str = f"[Dry-run success] Generated entry for {target_date_str} (Mood: {mood})"
        else:
            result_str = journal_manager.create_journal_entry(
                vibe_check=vibe_check,
                narrative=narrative,
                message_in_a_bottle=message_in_a_bottle,
                mood=mood,
                tags=tags,
                date_str=target_date_str,
            )

        duration = time.time() - start_time
        summary_txt = f"Auto-journaled {target_date_str} (Mood: {mood}) in {duration:.1f}s"
        task_manager.clear_running(TASK_NAME, status="done", summary=summary_txt)
        logger.info(f"[AUTO-JOURNAL] Completed autonomous journal generation for {target_date_str} in {duration:.1f}s.")

        return {
            "status": "success",
            "target_date": target_date_str,
            "mood": mood,
            "vibe_check": vibe_check,
            "narrative": narrative,
            "message_in_a_bottle": message_in_a_bottle,
            "tags": tags,
            "duration": round(duration, 2),
            "result": result_str,
        }

    except asyncio.CancelledError:
        task_manager.clear_running(TASK_NAME, status="cancelled", error="Task cancelled/preempted")
        logger.info("[AUTO-JOURNAL] Task cancelled or preempted.")
        raise
    except Exception as e:
        task_manager.clear_running(TASK_NAME, status="error", error=str(e))
        logger.error(f"[AUTO-JOURNAL ERROR] Autonomous journaling failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
