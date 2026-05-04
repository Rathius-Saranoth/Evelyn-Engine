"""
fact_extractor.py — Idle-time personal-fact extraction for Evelyn's memory system.

Reads directly from the chat database (evelyn_chat.db) using a persistent
high-water mark (last processed message ID). Only new messages since the last
successful run are processed, guaranteeing zero duplicate extractions regardless
of conversation length or server restarts (within a session).

Runs as an idle-time background task (same pattern as fact_consolidator) so it
never competes with the main chat loop for Ollama.

Architecture:
  - run_extraction()              — idle-time entry point (called from server loop)
  - cancel_pending_extraction()   — cancels in-flight run on new chat request
  - _fetch_new_messages()         — reads messages WHERE id > _last_extracted_id
  - _do_extraction()              — calls Ollama, parses YAML, writes staging files
  - write_extracted_facts()       — writes EX_*.md to EXTRACTED_DIR
  - load_cat00_index()            — Cat00 taxonomy (cached 1h)

High-water mark:
  _last_extracted_id tracks the highest DB message ID already processed.
  It is only advanced after a *successful* extraction so cancelled or failed
  runs retry the same message window next idle period.

All config is read from evelyn_config.py (single source of truth).
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

import evelyn_config as cfg

# ---------------------------------------------------------------------------
# Category taxonomy cache
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
# Contains: {"last_extracted_id": <int>}
# To reset the high-water mark: delete this file and restart the server.
_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(cfg.CHAT_DB_PATH)),
    "evelyn_extraction_state.json",
)


def _load_extraction_state() -> int:
    """Load the persisted high-water mark from disk.

    Falls back to cfg.FACT_EXTRACTION_START_ID (default 0) if the file
    doesn't exist yet (first run) or is unreadable.
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
    """Persist the high-water mark to disk after a successful extraction run."""
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


def cancel_pending_extraction():
    """Cancel any in-flight extraction task.

    Called at the top of chat_stream() so Ollama is freed immediately when
    the user sends a message. The high-water mark is NOT advanced on
    cancellation, so those messages are retried next idle period.
    """
    global _extraction_task, _extracting
    if _extraction_task and not _extraction_task.done():
        _extraction_task.cancel()
        _extracting = False
        print("[EXTRACTOR] Cancelled (new chat request).", flush=True)
    _extraction_task = None


async def run_extraction():
    """Idle-time entry point — called from the server's idle loop.

    Reads new messages from the DB (WHERE id > _last_extracted_id), runs
    the extraction LLM call, and writes staging files to EXTRACTED_DIR.
    Skips if disabled, already running, within cooldown, or insufficient
    new messages.

    Only advances _last_extracted_id on successful completion.
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
    try:
        await _do_extraction(messages)
        success = True
    except asyncio.CancelledError:
        print("[EXTRACTOR] Cancelled — high-water mark not advanced.", flush=True)
    except Exception as e:
        print(f"[EXTRACTOR ERROR] {type(e).__name__}: {e}", flush=True)
    finally:
        _extracting = False
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
    global _last_run_ts
    _last_run_ts = time.time()


# ---------------------------------------------------------------------------
# DB access
# ---------------------------------------------------------------------------


def _fetch_new_messages() -> tuple[list[dict], int]:
    """Read unprocessed messages from the chat DB.

    Fetches up to FACT_EXTRACTION_BATCH_SIZE messages with id > _last_extracted_id,
    ordered oldest-first. Skips tool result rows and messages with no meaningful
    content to keep the extraction prompt clean.

    Returns:
        Tuple of (message_list, max_id_seen).
        message_list: list of {role, content, ts} dicts.
        max_id_seen: the highest DB id in the returned batch (0 if empty).
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

    Includes a [YYYY-MM-DD] date prefix on each line so the LLM can
    accurately date each extracted fact to when it was discussed.
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
    """Assemble the extraction prompt with the Cat00 taxonomy injected."""
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
        "DO NOT extract: greetings, small talk, questions without answers, "
        "hypotheticals, jokes, or transient status updates.\n"
        f"{category_block}\n\n"
        "Output ONLY a fenced YAML block in this exact format. "
        "If no durable facts are found, output an empty list.\n\n"
        "```facts\n"
        "facts:\n"
        "  - subject: Ricky          # or Evelyn\n"
        "    category: Cat05-R        # best matching Cat##-E or Cat##-R code\n"
        "    summary: \"Exact fact.\"   # one clear, self-contained sentence\n"
        "    confidence: high         # high / medium / low\n"
        "    date: \"2025-03-15\"      # date this was discussed (from message timestamps above)\n"
        "```\n\n"
        f"CONVERSATION:\n{transcript}"
    )


def _parse_facts_yaml(raw: str, fallback_date: str) -> list[dict]:
    """Parse the YAML facts block from the model's raw response.

    Falls back gracefully on malformed output. If the LLM omits or mangles
    the date field, `fallback_date` (the latest message date in the batch)
    is used instead.

    Args:
        raw:           The raw string returned by Ollama.
        fallback_date: YYYY-MM-DD string to use when the LLM skips the date.

    Returns:
        List of validated fact dicts with keys:
        subject, category, summary, confidence, date.
    """
    _YAML_BLOCK_RE = re.compile(
        r"```(?:facts|yaml)?\s*\n(.*?)```",
        re.DOTALL | re.IGNORECASE,
    )
    _FACTS_KEY_RE = re.compile(r"^\s*facts\s*:", re.MULTILINE)

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
    _DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    for item in facts:
        if not isinstance(item, dict):
            continue
        subj = str(item.get("subject", "")).strip()
        cat = str(item.get("category", "")).strip()
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
            {"subject": subj, "category": cat, "summary": summ,
             "confidence": conf, "date": fact_date}
        )

    return validated


async def _do_extraction(messages: list[dict]):
    """Core extraction logic. Calls Ollama and writes staging files."""
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
            "content": "You are a precise fact extractor. Output only the YAML block, nothing else.",
        },
        {"role": "user", "content": prompt},
    ]

    importlib.reload(cfg)
    override = cfg.SUMMARY_MODEL_OVERRIDE
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
        "stream": False,
        "options": options,
        "think": False,  # Structured extraction — no reasoning chain needed
    }

    print(
        f"[EXTRACTOR] Extracting from {len(messages)} new message(s)...",
        flush=True,
    )
    start = time.time()

    timeout = cfg.FACT_EXTRACTION_TIMEOUT
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{cfg.OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
            result = resp.json()
    except asyncio.CancelledError:
        raise  # Let it propagate — caller handles it
    except Exception as e:
        print(f"[EXTRACTOR] Ollama call failed: {e}", flush=True)
        return

    raw = result.get("message", {}).get("content", "").strip()
    elapsed = time.time() - start

    if not raw:
        print("[EXTRACTOR] Warning: empty response from model.", flush=True)
        return

    print(f"[EXTRACTOR] Response received in {elapsed:.1f}s.", flush=True)

    facts = _parse_facts_yaml(raw, fallback_date)
    if not facts:
        print("[EXTRACTOR] No valid facts extracted.", flush=True)
        return

    print(f"[EXTRACTOR] {len(facts)} fact(s) extracted. Writing staging files...", flush=True)
    written = write_extracted_facts(facts)
    print(f"[EXTRACTOR] Wrote {written} file(s) to Extracted/ folder.", flush=True)


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------


def write_extracted_facts(facts: list[dict]) -> int:
    """Write extracted facts to the EXTRACTED_DIR staging folder.

    Each fact gets its own EX_YYYY-MM-DD_Cat##-{E,R}.md file where the date
    is taken from the fact's `date` field (when it was discussed), NOT today.
    Duplicate filenames get a numeric suffix.

    Args:
        facts: List of validated fact dicts (must include 'date' key).

    Returns:
        int: Number of files successfully written.
    """
    importlib.reload(cfg)
    extracted_dir = cfg.EXTRACTED_DIR
    os.makedirs(extracted_dir, exist_ok=True)

    import datetime as dt
    today_str = dt.date.today().strftime("%Y-%m-%d")  # fallback only
    written = 0

    for fact in facts:
        category = fact["category"]
        subject = fact["subject"]
        summary = fact["summary"]
        confidence = fact["confidence"]
        fact_date = fact.get("date") or today_str  # date fact was discussed

        base_name = f"EX_{fact_date}_{category}.md"
        filepath = os.path.join(extracted_dir, base_name)
        counter = 1
        while os.path.exists(filepath):
            base_name = f"EX_{fact_date}_{category} ({counter}).md"
            filepath = os.path.join(extracted_dir, base_name)
            counter += 1

        date_tag = fact_date.replace("-", "/")
        file_content = (
            f"---\n"
            f"tags: [CY-{date_tag}, extracted]\n"
            f"confidence: {confidence}\n"
            f"---\n\n"
            f"# {base_name.replace('.md', '')}\n\n"
            f"**Primary:** [[{category}]]\n\n"
            f"**Subject:** {subject}\n\n"
            f"**Summary:** {summary}\n\n"
            f"**Confidence:** {confidence}\n\n"
            f"> [!NOTE] Auto-extracted by fact_extractor.py - review before promoting to live vault.\n"
        )

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(file_content)
            written += 1
        except OSError as e:
            print(f"[EXTRACTOR] Failed to write {base_name}: {e}", flush=True)

    return written
