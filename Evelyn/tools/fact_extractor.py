# fact_extractor.py
# date created: 2026-05-03 18:05:36
# date modified: 2026-06-07 10:19:00
# tags: #facts, #extractor, #extraction, #idle_time, #analysis

"""
fact_extractor.py — Idle-time personal-fact extraction for Evelyn's memory system.

Reads new messages from evelyn_chat.db (WHERE id > high-water mark) and extracts
durable personal facts via LLM call. Runs during server idle time to avoid competing
with the chat loop.

Exports:
  run_extraction()            — Idle-time entry point; called from the server loop.
  cancel_pending_extraction() — Called on each new chat request to free Ollama.
  write_extracted_facts()     — Write parsed facts to the SQLite memory DB.
  load_cat00_index()          — Return the Cat00 category taxonomy (cached 1 h).

Key config: evelyn_config.py (FACT_EXTRACTION_*, THINK, NUM_CTX)
Architecture notes: reference/docstring_content/pipeline_internals.md
"""


import asyncio
import datetime
import importlib
import json
import os
import re
import sqlite3
import time

import httpx
import yaml

import evelyn_config as cfg # [[evelyn_config.py]]

# ---------------------------------------------------------------------------
# Module-level regex constants
# ---------------------------------------------------------------------------

_YAML_BLOCK_RE = re.compile(r"```(?:facts|yaml)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_FACTS_KEY_RE  = re.compile(r"^\s*facts\s*:", re.MULTILINE)
_DATE_RE       = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ---------------------------------------------------------------------------
# Category taxonomy cache - [[Cat00 - Index.md]]
# ---------------------------------------------------------------------------

_cat00_text: str = ""
_cat00_loaded_at: float = 0.0
_CAT00_CACHE_TTL = 3600.0


def load_cat00_index() -> str:
    """Return the text content of Cat00 - Index.md (cached for 1 hour).

    Used to inject the authoritative category taxonomy into the extraction
    prompt so the model assigns the best-fitting Cat##-{E,R} code.

    Returns:
        str: Full markdown text of Cat00 (frontmatter stripped), or "" on error.
    """
    global _cat00_text, _cat00_loaded_at
    now = time.time()
    if _cat00_text and (now - _cat00_loaded_at) < _CAT00_CACHE_TTL:
        return _cat00_text

    cat00_path = os.path.join(
        cfg.VAULT_BASE_DIR,
        "Evelyn",
        "Evelyn's Context",
        "Context Categories",
        "Cat00 - Index.md",
    )
    try:
        with open(cat00_path, "r", encoding="utf-8") as f:
            text = f.read()
        stripped = re.sub(r"^---.*?---\s*", "", text, count=1, flags=re.DOTALL)
        _cat00_text = stripped.strip()
        _cat00_loaded_at = now
        print("[EXTRACTOR] Cat00 index loaded.", flush=True)
    except Exception as e:
        print(f"[EXTRACTOR] Warning: could not load Cat00 index: {e}", flush=True)
        _cat00_text = ""
    return _cat00_text


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

# State file lives next to the chat DB so both are together.
# [[evelyn_extraction_state.json]]
# Contains: {"last_extracted_id": <int>}
# To reset the high-water mark: delete this file and restart the server.
_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(cfg.CHAT_DB_PATH)),
    "evelyn_extraction_state.json",
)


def _load_extraction_state() -> int:
    """Load the persisted high-water mark from disk.

    Returns:
        int: The last processed message ID.
    """
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("last_extracted_id", cfg.FACT_EXTRACTION_START_ID))
    except FileNotFoundError:
        return cfg.FACT_EXTRACTION_START_ID
    except (KeyError, ValueError, json.JSONDecodeError, OSError) as e:
        print(f"[EXTRACTOR] Warning: could not read state file: {e}", flush=True)
        return cfg.FACT_EXTRACTION_START_ID


def _save_extraction_state(last_id: int) -> None:
    """Persist the high-water mark to disk after a successful extraction run.

    Args:
        last_id: The last processed message ID.
    """
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_extracted_id": last_id}, f)
    except OSError as e:
        print(f"[EXTRACTOR] Warning: could not save state file: {e}", flush=True)


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

_extracting = False

# High-water mark: highest DB message id already processed.
# Loaded from disk on startup; written after each successful run.
# To reset: delete evelyn_extraction_state.json and restart.
_last_extracted_id: int = _load_extraction_state()

# Task reference for cancellation (same pattern as fact_consolidator)
_extraction_task = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _heavy_tasks_running() -> bool:
    """Check if any heavy server background task is running.

    Returns:
        bool: True if another heavy background task is active, False otherwise.
    """
    import sys
    for mod_name in ("evelyn_server", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod:
            tasks = getattr(mod, "_background_tasks", None)
            if isinstance(tasks, dict):
                for k, task in tasks.items():
                    if k == "extractor":
                        continue
                    if k.startswith("task_"):
                        if task.get("status") in ("running", "searching", "synthesizing"):
                            return True
                    elif task.get("status") == "running":
                        return True
    return False


def _set_status_in_server(status: str | None) -> None:
    """Register or clear extractor status in the server's central registry.

    Args:
        status: The status string to register (e.g., 'running'), or None to clear.
    """
    import sys
    for mod_name in ("evelyn_server", "__main__"):
        mod = sys.modules.get(mod_name)
        if mod:
            tasks = getattr(mod, "_background_tasks", None)
            if isinstance(tasks, dict):
                if status == "running":
                    tasks["extractor"] = {
                        "status": "running",
                        "started_at": time.time(),
                    }
                else:
                    tasks.pop("extractor", None)


def cancel_pending_extraction():
    """Cancel any in-flight extraction task.

    Frees the Ollama instance immediately when a new user chat request is received.
    """
    global _extraction_task, _extracting
    if _extraction_task and not _extraction_task.done():
        _extraction_task.cancel()
        _extracting = False
        _set_status_in_server(None)
        print("[EXTRACTOR] Cancelled (new chat request).", flush=True)
    _extraction_task = None


async def run_extraction():
    """Run the idle-time extraction process to find new facts in the chat history.

    Coordinates mutual exclusion, cooldowns, and message batching before triggering extraction.
    """
    global _extracting, _last_extracted_id
    importlib.reload(cfg)

    if not cfg.FACT_EXTRACTION_ENABLED:
        return

    if _extracting:
        print("[EXTRACTOR] Already running — skipping.", flush=True)
        return

    # Defer if the consolidator is currently making an LLM call.
    # Both share the same Ollama instance; running in parallel causes timeouts.
    try:
        import fact_consolidator
        if fact_consolidator._consolidating:
            print(
                "[EXTRACTOR] Consolidator is running — deferring extraction.",
                flush=True,
            )
            return
    except ImportError:
        pass

    # Defer if a heavy server background task (Vault Map Gen, Sync, etc) is running.
    if _heavy_tasks_running():
        print(
            "[EXTRACTOR] Server background task is running — deferring extraction.",
            flush=True,
        )
        return

    now = time.time()
    if (now - _last_run_ts) < cfg.FACT_EXTRACTION_COOLDOWN:
        remaining = int(cfg.FACT_EXTRACTION_COOLDOWN - (now - _last_run_ts))
        print(
            f"[EXTRACTOR] Cooldown active — {remaining}s remaining. Skipping.",
            flush=True,
        )
        return

    messages, max_id = _fetch_new_messages()
    min_new = cfg.FACT_EXTRACTION_MIN_MESSAGES
    if len(messages) < min_new:
        print(
            f"[EXTRACTOR] Only {len(messages)} new message(s) "
            f"(need {min_new}). Skipping.",
            flush=True,
        )
        return

    _extracting = True
    success = False
    _set_status_in_server("running")
    try:
        await _do_extraction(messages)
        success = True
    except asyncio.CancelledError:
        print("[EXTRACTOR] Cancelled — high-water mark not advanced.", flush=True)
    except Exception as e:
        print(f"[EXTRACTOR ERROR] {type(e).__name__}: {e}", flush=True)
    finally:
        _extracting = False
        _set_status_in_server(None)
        if success:
            _last_extracted_id = max_id
            _save_extraction_state(max_id)
            _update_last_run_ts()
            print(
                f"[EXTRACTOR] High-water mark advanced to message id={max_id} (persisted).",
                flush=True,
            )


# ---------------------------------------------------------------------------
# Cooldown tracking (mirrors fact_consolidator pattern)
# ---------------------------------------------------------------------------

_last_run_ts: float = 0.0


def _update_last_run_ts():
    """Update the global background task last-run timestamp to the current time."""
    global _last_run_ts
    _last_run_ts = time.time()


# ---------------------------------------------------------------------------
# DB access
# ---------------------------------------------------------------------------


def _fetch_new_messages() -> tuple[list[dict], int]:
    """Read unprocessed messages from the chat DB.

    Returns:
        tuple[list[dict], int]: A tuple containing the list of new messages and the
            highest message ID seen in the batch.
    """
    importlib.reload(cfg)
    batch_size = cfg.FACT_EXTRACTION_BATCH_SIZE

    try:
        conn = sqlite3.connect(cfg.CHAT_DB_PATH)
        rows = conn.execute(
            "SELECT id, role, content, ts FROM messages "
            "WHERE id > ? AND role IN ('user', 'assistant') "
            "ORDER BY id ASC LIMIT ?",
            (_last_extracted_id, batch_size),
        ).fetchall()
        conn.close()
    except Exception as e:
        print(f"[EXTRACTOR] DB read error: {e}", flush=True)
        return [], 0

    if not rows:
        return [], 0

    messages = []
    # Structural markers stored as assistant messages — no factual content
    _SKIP_PREFIXES = ("[THREAD_BREAK]", "[Response interrupted")
    for row_id, role, content, ts in rows:
        if not content or not content.strip():
            continue
        if any(content.startswith(p) for p in _SKIP_PREFIXES):
            continue
        messages.append({"role": role, "content": content, "ts": ts})

    max_id = rows[-1][0]
    return messages, max_id


# ---------------------------------------------------------------------------
# Extraction core
# ---------------------------------------------------------------------------


def _format_messages_for_extraction(messages: list[dict]) -> str:
    """Render messages as a readable transcript for the extraction prompt.

    Args:
        messages: A list of message dictionaries.

    Returns:
        str: The formatted transcript string.
    """
    import datetime as dt
    lines = []
    for msg in messages:
        role_label = "Ricky" if msg["role"] == "user" else "Evelyn"
        content = msg["content"]
        if len(content) > 600:
            content = content[:597] + "..."
        ts = msg.get("ts")
        if ts:
            try:
                date_str = dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                prefix = f"[{date_str}] {role_label}"
            except (OSError, OverflowError, ValueError):
                prefix = role_label
        else:
            prefix = role_label
        lines.append(f"{prefix}: {content}")
    return "\n\n".join(lines)


def _build_extraction_prompt(messages: list[dict], cat00: str) -> str:
    """Assemble the extraction prompt.

    Args:
        messages: A list of message dictionaries.
        cat00: The Cat00 index taxonomy block.

    Returns:
        str: The assembled prompt text.
    """
    transcript = _format_messages_for_extraction(messages)

    category_block = (
        f"\n\nCATEGORY REFERENCE (use these codes for the 'category' field):\n{cat00}"
        if cat00
        else "\n\n(Category reference unavailable — use best judgment for Cat##-E/R codes.)"
    )

    return (
        "You are a precise fact extractor for a personal memory system. "
        "Analyze the following conversation and extract ONLY concrete, durable personal facts "
        "about Ricky (the user) or Evelyn (the AI). "
        "Extract: preferences, physical traits, relationships, goals, beliefs, skills, events, "
        "opinions, habits, or any detail worth remembering long-term. "
        "DO NOT extract: greetings, small talk, questions without answers, or hypotheticals. \n"
        f"{category_block}\n\n"
        "CRITICAL RULE: Write pure, factual observations. Do not 'summarize' or evaluate the event. "
        "Do NOT inject the Category Reference titles into the text of the observation.\n\n"
        "Output ONLY a fenced YAML block in this exact format. "
        "If no durable facts are found, output an empty list.\n\n"
        "```facts\n"
        "facts:\n"
        "  - subject: Ricky          # or Evelyn\n"
        "    category: Cat05-R        # best matching Cat##-E or Cat##-R code\n"
        "    tags: \"kw/ricky, kw/habit\" # comma-separated semantic tags starting with kw/\n"
        "    summary: \"Exact fact.\"   # one clear, self-contained sentence\n"
        "    confidence: high         # high / medium / low\n"
        "    date: \"2025-03-15\"      # date this was discussed (from message timestamps above)\n"
        "```\n\n"
        f"CONVERSATION:\n{transcript}"
    )


def _parse_facts_yaml(raw: str, fallback_date: str) -> list[dict]:
    """Parse the YAML facts block from the model's raw response.

    Args:
        raw: The raw response string from Ollama.
        fallback_date: A fallback date string in YYYY-MM-DD format.

    Returns:
        list[dict]: A list of parsed and validated fact dictionaries.
    """
    match = _YAML_BLOCK_RE.search(raw)
    if match:
        block = match.group(1)
    elif _FACTS_KEY_RE.search(raw):
        block = raw
    else:
        print("[EXTRACTOR] No YAML block found in model output.", flush=True)
        return []

    if not block.strip().startswith("facts:"):
        block = "facts:\n" + block

    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as e:
        print(f"[EXTRACTOR] YAML parse error: {e}", flush=True)
        return []

    if not isinstance(data, dict) or "facts" not in data:
        return []

    facts = data["facts"]
    if not isinstance(facts, list):
        return []

    validated = []
    valid_cats = {f"Cat{n:02d}" for n in range(1, 17)}

    for item in facts:
        if not isinstance(item, dict):
            continue
        subj = str(item.get("subject", "")).strip()
        cat = str(item.get("category", "")).strip()
        tags = str(item.get("tags", "")).strip()
        summ = str(item.get("summary", "")).strip()
        conf = str(item.get("confidence", "medium")).strip().lower()
        raw_date = str(item.get("date", "")).strip()

        if not subj or not cat or not summ:
            continue

        cat_base = cat.split("-")[0] if "-" in cat else cat
        if cat_base not in valid_cats:
            print(f"[EXTRACTOR] Skipping fact — unknown category: {cat}", flush=True)
            continue
        if not cat.endswith("-E") and not cat.endswith("-R"):
            suffix = "-R" if subj.lower() == "ricky" else "-E"
            cat = cat_base + suffix
            print(f"[EXTRACTOR] Inferred category suffix -> {cat}", flush=True)

        if conf not in ("high", "medium", "low"):
            conf = "medium"

        # Validate date — fall back to the batch's latest message date
        if _DATE_RE.match(raw_date):
            fact_date = raw_date
        else:
            fact_date = fallback_date
            if raw_date:
                print(
                    f"[EXTRACTOR] Invalid date '{raw_date}' — using fallback {fallback_date}",
                    flush=True,
                )

        validated.append(
            {"subject": subj, "category": cat, "tags": tags, "summary": summ,
             "confidence": conf, "date": fact_date}
        )

    return validated


async def _do_extraction(messages: list[dict]):
    """Run the core fact extraction LLM call on a batch of messages.

    Args:
        messages: A list of chat message dictionaries.
    """
    import datetime as dt
    cat00 = load_cat00_index()
    prompt = _build_extraction_prompt(messages, cat00)

    # Compute fallback date from the latest message timestamp in the batch
    latest_ts = max((m.get("ts") or 0) for m in messages)
    if latest_ts:
        try:
            fallback_date = dt.datetime.fromtimestamp(latest_ts).strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            fallback_date = dt.date.today().strftime("%Y-%m-%d")
    else:
        fallback_date = dt.date.today().strftime("%Y-%m-%d")

    extraction_messages = [
        {
            "role": "system",
            "content": "You are a precise fact extractor. You record pure, objective observations. "
                       "Output only the YAML block, nothing else.",
        },
        {"role": "user", "content": prompt},
    ]

    importlib.reload(cfg)
    override = cfg.FACT_EXTRACTION_MODEL_OVERRIDE
    model = cfg.MODEL_NAME if override == "default" else override
    options = {"num_ctx": cfg.NUM_CTX}
    for key, val in {
        "temperature": cfg.TEMPERATURE,
        "min_p": cfg.MIN_P,
        "top_k": cfg.TOP_K,
        "top_p": cfg.TOP_P,
        "repeat_penalty": cfg.REPEAT_PENALTY,
        "repeat_last_n": cfg.REPEAT_LAST_N,
        "seed": cfg.SEED,
    }.items():
        if val is not None:
            options[key] = val

    # Scale token budget to batch size — a small batch needs far fewer tokens
    options["num_predict"] = min(64 * len(messages), 512)
    if cfg.STOP_SEQUENCES:
        options["stop"] = cfg.STOP_SEQUENCES

    payload = {
        "model": model,
        "messages": extraction_messages,
        "stream": True,
        "options": options,
        "think": False,  # Structured extraction — no reasoning chain needed
    }

    print(
        f"[EXTRACTOR] Extracting from {len(messages)} new message(s)...",
        flush=True,
    )
    start = time.time()

    timeout = cfg.FACT_EXTRACTION_TIMEOUT
    content_buffer = ""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
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
    except asyncio.CancelledError:
        raise  # Let it propagate — caller handles it
    except Exception as e:
        print(f"[EXTRACTOR] Ollama call failed: {e}", flush=True)
        return

    raw = content_buffer.strip()
    elapsed = time.time() - start

    if not raw:
        print("[EXTRACTOR] Warning: empty response from model.", flush=True)
        return

    print(f"[EXTRACTOR] Response received in {elapsed:.1f}s.", flush=True)

    facts = _parse_facts_yaml(raw, fallback_date)
    if not facts:
        print("[EXTRACTOR] No valid facts extracted.", flush=True)
        return

    print(f"[EXTRACTOR] {len(facts)} fact(s) extracted. Writing to memory DB...", flush=True)
    written = write_extracted_facts(facts)
    print(f"[EXTRACTOR] Wrote {written} fact(s) to SQLite DB.", flush=True)


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------


def write_extracted_facts(facts: list[dict]) -> int:
    """Write extracted facts to the SQLite memory database.

    Args:
        facts: List of validated fact dictionaries.

    Returns:
        int: The number of facts successfully inserted.
    """
    import memory_db
    import datetime as dt

    today_str = dt.date.today().strftime("%Y-%m-%d")
    written = 0

    for fact in facts:
        category = fact["category"]
        subject = fact["subject"]
        summary = fact["summary"]
        confidence = fact["confidence"]
        fact_date = fact.get("date") or today_str

        # Dedup check against live entries in the same category
        # Adjust min_overlap for more or less strict duplicate detection
        similar = memory_db.find_similar_entries(
            category=category,
            observation_text=summary,
            min_overlap=0.90,
            status="live"
        )
        if similar:
            print(f"[EXTRACTOR] Skipping duplicate ({similar[0]['overlap']:.2f} overlap): {summary[:80]}...", flush=True)
            continue

        # Auto-accept high confidence extractions
        final_status = "live" if confidence == "high" else "extracted"

        try:
            memory_db.insert_entry(
                category=category,
                subject=subject,
                observation=summary,
                confidence=confidence,
                source="extracted",
                status=final_status,
                date=fact_date,
                tags=fact.get("tags"),
            )
            written += 1
        except Exception as e:
            print(f"[EXTRACTOR] Failed to insert fact: {e}", flush=True)

    return written
