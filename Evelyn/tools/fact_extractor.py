# fact_extractor.py
# date created: 2026-05-03 18:05:36
# date modified: 2026-08-29 16:04:10
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

Internal security:
  _sanitize_entry()           — Strips invisible Unicode and rejects prompt-injection
                                patterns before any text reaches the memory DB.

Key config: evelyn_config.py (FACT_EXTRACTION_*, THINK, NUM_CTX)
Architecture notes: reference/docstring_guide.md#fact_extractorpy--architecture-notes
"""


import asyncio
import datetime as dt
import importlib
import json
import os
import re
import sqlite3
import time
from typing import Any

import httpx
import yaml

import evelyn_config as cfg  # [[evelyn_config.py]]
from Evelyn.tools import chroma_rag, vault_db
from Evelyn.tools.tag_librarian import is_excluded_tag, normalize_tag_format

# ---------------------------------------------------------------------------
# Module-level regex constants
# ---------------------------------------------------------------------------

_YAML_BLOCK_RE = re.compile(r"```(?:facts|yaml)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_FACTS_KEY_RE  = re.compile(r"^\s*facts\s*:", re.MULTILINE)
_PROC_YAML_BLOCK_RE = re.compile(r"```(?:procedures|yaml)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_PROC_KEY_RE = re.compile(r"^\s*procedures\s*:", re.MULTILINE)
_DATE_RE       = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ---------------------------------------------------------------------------
# Security: prompt-injection detection and invisible-character sanitization
# ---------------------------------------------------------------------------

# Patterns that signal an adversarial attempt to hijack the extraction pipeline.
_INJECTION_RE = re.compile(
    r"(?:ignore|disregard)\s+(?:previous|above|prior|all|the\s+above)"
    r"|new\s+instruction"
    r"|system\s*:"
    r"|\[INST\]"
    r"|forget\s+(?:the\s+)?(?:instruction|context|memory)",
    re.IGNORECASE,
)

# Invisible Unicode codepoints used for steganographic text hiding.
_INVISIBLE_CHARS = "\u200b\u200c\u200d\ufeff\u00ad"


def _sanitize_entry(text: str) -> str | None:
    """Sanitize a candidate memory entry against injection and invisible characters.

    Runs three ordered checks:
      1. Strip invisible Unicode (zero-width spaces, BOM, soft-hyphens) that
         may be used to conceal injected text from human review.
      2. Reject entries matching prompt-injection patterns (e.g.
         'ignore previous instructions', 'new instruction', '[INST]').
      3. Reject entries where a category code (Cat##) is embedded in the
         summary text itself — a known model quirk the extraction prompt
         already guards against, caught here as a second layer.

    This is defence-in-depth: the extraction prompt is already hardened, but
    this gate catches adversarial inputs and model hallucinations that slip
    through the prompt layer.

    Args:
        text: The raw summary or observation string to validate.

    Returns:
        str | None: The cleaned text if all checks pass, or None if the
            entry must be silently dropped.
    """
    # Pass 1 — strip invisible/steganographic Unicode
    cleaned = text.translate(str.maketrans("", "", _INVISIBLE_CHARS))

    # Pass 2 — reject prompt-injection patterns
    if _INJECTION_RE.search(cleaned):
        print(
            "[EXTRACTOR] Sanitization: prompt injection pattern detected — dropping entry.",
            flush=True,
        )
        return None

    # Pass 3 — reject embedded category codes in summary body
    if re.search(r"\bCat\d{2}\b", cleaned):
        print(
            "[EXTRACTOR] Sanitization: category code embedded in summary — dropping entry.",
            flush=True,
        )
        return None

    return cleaned if cleaned.strip() else None


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
        with open(cat00_path, encoding="utf-8") as f:
            text = f.read()
        stripped = re.sub(r"^---.*?---\s*", "", text, count=1, flags=re.DOTALL)
        _cat00_text = stripped.strip()
        _cat00_loaded_at = now
        print("[EXTRACTOR] Cat00 index loaded.", flush=True)
    except OSError as e:
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


def _load_extraction_state() -> tuple[int, float]:
    """Load the persisted high-water mark and last-run timestamp from disk.

    Both values are stored in the same state file so a server restart
    respects the cooldown — preventing an immediate re-run after reboot.

    Returns:
        tuple[int, float]: (last_extracted_id, last_run_ts).
            last_run_ts defaults to 0.0 if not present (first run ever).
    """
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        last_id = int(data.get("last_extracted_id", cfg.FACT_EXTRACTION_START_ID))
        last_ts = float(data.get("last_run_ts", 0.0))
        return last_id, last_ts
    except FileNotFoundError:
        return cfg.FACT_EXTRACTION_START_ID, 0.0
    except (KeyError, ValueError, json.JSONDecodeError, OSError) as e:
        print(f"[EXTRACTOR] Warning: could not read state file: {e}", flush=True)
        return cfg.FACT_EXTRACTION_START_ID, 0.0


def _save_extraction_state(last_id: int) -> None:
    """Persist the high-water mark and last-run timestamp to disk.

    Both are written atomically in the same file so a server restart
    can honour the cooldown without re-running immediately.

    Args:
        last_id: The last processed message ID.
    """
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_extracted_id": last_id, "last_run_ts": _last_run_ts}, f)
    except OSError as e:
        print(f"[EXTRACTOR] Warning: could not save state file: {e}", flush=True)


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

_extracting = False

# High-water mark: highest DB message id already processed.
# Loaded from disk on startup; written after each successful run.
# To reset: delete evelyn_extraction_state.json and restart.
_last_extracted_id: int
_last_run_ts: float
_last_extracted_id, _last_run_ts = _load_extraction_state()

# Task reference for cancellation (same pattern as fact_consolidator)
_extraction_task: asyncio.Task | None = None

# Per-session batch counter — number of batches processed in the current
# continuous idle window. Resets when a chat request arrives (cancel_pending_extraction).
# Guards against processing an unbounded backlog overnight.
_session_batches_this_idle: int = 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _heavy_tasks_running() -> bool:
    """Check if any heavy server background task is running.

    Delegates to task_manager.is_any_running() — the single canonical
    source of truth for mutual exclusion across all heavy tasks.

    Returns:
        bool: True if another heavy background task is active, False otherwise.
    """
    from Evelyn.tools import task_manager
    return task_manager.is_any_running(exclude="extractor")


def _set_status_in_server(
    status: str | None,
    error: str | None = None,
    summary: str | None = None,
    sub_status: dict | None = None,
    diagnostics: dict | None = None,
    items_processed: int = 0,
) -> None:
    """Register or clear extractor status in the server's central registry.

    Delegates to task_manager.set_running() / task_manager.clear_running().

    Args:
        status: The status string to register (e.g., 'running'), or None/status string on completion.
        error: Optional error message string.
        summary: Optional completion summary text.
        sub_status: Optional sub-status metrics dict.
        diagnostics: Optional diagnostic details dict.
        items_processed: Optional number of items processed.
    """
    from Evelyn.tools import task_manager
    if status == "running":
        task_manager.set_running("extractor", sub_status=sub_status, diagnostics=diagnostics)
    else:
        task_manager.clear_running(
            "extractor",
            status=status or "idle",
            error=error,
            summary=summary,
            sub_status=sub_status,
            diagnostics=diagnostics,
            items_processed=items_processed,
        )


def cancel_pending_extraction(reason: str = "chat_request"):
    """Cancel any in-flight extraction task.

    Frees the Ollama instance immediately when a new user chat request or internal refresh is triggered.
    Also resets the per-session batch counter so the next idle window starts fresh.
    """
    global _extraction_task, _extracting, _session_batches_this_idle
    if _extraction_task and not _extraction_task.done():
        _extraction_task.cancel()
        _extracting = False
        _set_status_in_server("cancelled")
        print(f"[EXTRACTOR] Cancelled ({reason}).", flush=True)
    _extraction_task = None
    _session_batches_this_idle = 0  # New idle session will start fresh


async def run_extraction():
    """Run the idle-time extraction process to find new facts in the chat history.

    Coordinates mutual exclusion, cooldowns, cooperative yield checks, and batching.
    Processes consecutive batches during idle periods, yielding to peer tasks
    in the FIFO idle queue after each batch.
    """
    global _extracting, _last_extracted_id, _session_batches_this_idle
    importlib.reload(cfg)

    if not cfg.FACT_EXTRACTION_ENABLED:
        return

    if _extracting:
        print("[EXTRACTOR] Already running — skipping.", flush=True)
        return

    # Defer if any other heavy background task is actively running.
    if _heavy_tasks_running():
        print(
            "[EXTRACTOR] Another heavy task is running — deferring extraction.",
            flush=True,
        )
        return

    from Evelyn.tools import task_manager

    # Cooldown check (only on initial invocation if no backlog)
    _last_run_ts = task_manager.get_last_run_ts("extractor")
    now = time.time()
    if (now - _last_run_ts) < cfg.FACT_EXTRACTION_COOLDOWN:
        remaining = int(cfg.FACT_EXTRACTION_COOLDOWN - (now - _last_run_ts))
        print(
            f"[EXTRACTOR] Cooldown active — {remaining}s remaining. Skipping.",
            flush=True,
        )
        return

    _extracting = True
    max_batches = getattr(cfg, "FACT_EXTRACTION_MAX_BATCHES_PER_SESSION", 0)
    backlog_delay = getattr(cfg, "FACT_EXTRACTION_BACKLOG_DELAY", 5.0)

    try:
        while True:
            # 1. Fetch next batch of unprocessed messages
            messages, max_id = _fetch_new_messages()
            min_new = cfg.FACT_EXTRACTION_MIN_MESSAGES
            if len(messages) < min_new:
                if _session_batches_this_idle == 0:
                    print(
                        f"[EXTRACTOR] Only {len(messages)} new message(s) (need {min_new}). Skipping.",
                        flush=True,
                    )
                else:
                    print(
                        f"[EXTRACTOR] Backlog caught up ({len(messages)} remaining < {min_new}). Finishing pass.",
                        flush=True,
                    )
                break

            # 2. Register running status in server registry
            _set_status_in_server(
                "running",
                sub_status={"last_extracted_id": _last_extracted_id},
            )

            # 3. Perform LLM extraction
            await _do_extraction(messages)

            # 4. Commit progress cursor atomically to disk & state
            _last_extracted_id = max_id
            _session_batches_this_idle += 1
            _update_last_run_ts()
            _save_extraction_state(max_id)
            _set_status_in_server(
                "idle",
                summary=f"Extracted {len(messages)} messages (up to id #{max_id})",
                sub_status={"last_extracted_id": max_id},
                items_processed=len(messages),
            )
            print(
                f"[EXTRACTOR] Batch {_session_batches_this_idle} complete — advanced to message id={max_id}.",
                flush=True,
            )

            # 5. Check if more messages exist in the database
            remaining_messages, _ = _fetch_new_messages()
            if len(remaining_messages) < min_new:
                print("[EXTRACTOR] Backlog fully processed.", flush=True)
                break

            # 6. Check for cooperative yield: if other peer tasks are waiting or chat arrived
            if task_manager.should_yield("extractor"):
                print("[EXTRACTOR] Cooperative yield requested (peer task queued or chat active). Re-enqueueing at tail.", flush=True)
                task_manager.enqueue_idle_task("extractor")
                break

            # 7. Check optional session batch limit (if max_batches > 0)
            if max_batches > 0 and _session_batches_this_idle >= max_batches:
                print(
                    f"[EXTRACTOR] Session batch cap reached ({_session_batches_this_idle}/{max_batches}) — yielding.",
                    flush=True,
                )
                task_manager.enqueue_idle_task("extractor")
                break

            # 8. Brief pause between consecutive batches before next extraction
            await asyncio.sleep(backlog_delay)

    except asyncio.CancelledError:
        print("[EXTRACTOR] Cancelled — current batch aborted.", flush=True)
        _set_status_in_server("cancelled")
    except (sqlite3.Error, OSError, RuntimeError, ValueError, KeyError, httpx.HTTPError) as e:
        err_cls = type(e).__name__
        err_msg = str(e).strip()
        formatted_err = f"{err_cls}: {err_msg}" if err_msg else err_cls
        print(f"[EXTRACTOR ERROR] {formatted_err}", flush=True)
        _set_status_in_server(
            "error",
            error=formatted_err,
            sub_status={"last_extracted_id": _last_extracted_id},
        )
    finally:
        _extracting = False
        if task_manager.get_status("extractor") == "running":
            _set_status_in_server("idle")


# ---------------------------------------------------------------------------
# Cooldown tracking (mirrors fact_consolidator pattern)
# ---------------------------------------------------------------------------


def _update_last_run_ts():
    """Update the global background task last-run timestamp in task_manager."""
    global _last_run_ts
    from Evelyn.tools import task_manager
    _last_run_ts = task_manager.save_last_run_ts("extractor")


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
    except sqlite3.Error as e:
        print(f"[EXTRACTOR] DB read error: {e}", flush=True)
        return [], 0

    if not rows:
        return [], 0

    messages = []
    # Structural markers stored as assistant messages — no factual content
    _SKIP_PREFIXES = ("[THREAD_BREAK]", "[Response interrupted")
    for _row_id, role, content, ts in rows:
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
    lines = []
    for msg in messages:
        role_label = cfg.USER_NAME if msg["role"] == "user" else cfg.ASSISTANT_NAME
        content = msg["content"]
        if len(content) > 600:
            content = content[:597] + "..."
        ts = msg.get("ts")
        if ts:
            try:
                date_str = dt.datetime.fromtimestamp(ts, tz=dt.UTC).astimezone().strftime("%Y-%m-%d")
                prefix = f"[{date_str}] {role_label}"
            except (OSError, OverflowError, ValueError):
                prefix = role_label
        else:
            prefix = role_label
        lines.append(f"{prefix}: {content}")
    return "\n\n".join(lines)


def retrieve_candidate_taxonomy_and_clusters(
    messages: list[dict],
    top_k_tags: int | None = None,
    top_k_facts: int | None = None,
) -> tuple[list[dict], list[dict], float, str]:
    """Retrieve semantically relevant candidate taxonomy branches and memory clusters.

    Queries ChromaDB tag taxonomy (evelyn_tag_taxonomy) and memory chunks (evelyn_memory)
    using multi-angle queries derived from recent messages.
    Computes minimum cosine distance to assess taxonomy alignment vs novelty.

    Args:
        messages: List of chat messages in the current batch.
        top_k_tags: Max candidate master tags to retrieve. Defaults to FACT_EXTRACTION_TOP_K_TAXONOMY.
        top_k_facts: Max candidate memory chunks to retrieve. Defaults to FACT_EXTRACTION_TOP_K_FACTS.

    Returns:
        tuple[list[dict], list[dict], float, str]:
            - Candidate taxonomy tags/branches (tag, category, description, distance)
            - Candidate prior memory chunks/facts (content, distance, source)
            - Minimum cosine distance found across candidates
            - Novelty Guidance Directive string for Ollama prompt
    """
    if top_k_tags is None:
        top_k_tags = getattr(cfg, "FACT_EXTRACTION_TOP_K_TAXONOMY", 30)
    if top_k_facts is None:
        top_k_facts = getattr(cfg, "FACT_EXTRACTION_TOP_K_FACTS", 6)

    tag_col_name = getattr(cfg, "CHROMA_TAG_COLLECTION", "evelyn_tag_taxonomy")
    mem_col_name = getattr(cfg, "CHROMA_MEMORY_COLLECTION", "evelyn_memory")

    queries: list[str] = []

    # 1. User messages extracted text / intent
    user_texts = [m.get("content", "") for m in messages if m.get("role") == "user"]
    if user_texts:
        combined_user = " ".join(user_texts)[:600].strip()
        if combined_user:
            queries.append(combined_user)

    # 2. Overall conversation transcript sample
    transcript_sample = _format_messages_for_extraction(messages)[:600].strip()
    if transcript_sample and transcript_sample not in queries:
        queries.append(transcript_sample)

    if not queries:
        return [], [], 1.0, "NO_QUERY_AVAILABLE"

    candidates_tag_map: dict[str, dict] = {}
    memory_candidates: list[dict] = []
    seen_mem_docs = set()

    for q in queries:
        # Tag taxonomy query
        try:
            results = chroma_rag.query_collection(q, tag_col_name, n_results=top_k_tags)
            for r in results:
                meta = r.get("metadata") or {}
                tag = meta.get("tag")
                if not tag or is_excluded_tag(tag):
                    continue
                dist = float(r.get("distance", 1.0))
                if tag not in candidates_tag_map or dist < candidates_tag_map[tag]["distance"]:
                    candidates_tag_map[tag] = {
                        "tag": tag,
                        "category": meta.get("category", "general"),
                        "description": meta.get("description", ""),
                        "distance": dist,
                    }
        except (sqlite3.Error, OSError, RuntimeError, ValueError) as e:
            print(f"[EXTRACTOR] Tag taxonomy query failed for '{q[:30]}...': {e}", flush=True)

        # Memory chunks query
        try:
            mem_results = chroma_rag.query_collection(q, mem_col_name, n_results=top_k_facts)
            for mr in mem_results:
                doc = mr.get("document", "").strip()
                dist = float(mr.get("distance", 1.0))
                meta = mr.get("metadata") or {}
                if doc and doc not in seen_mem_docs:
                    seen_mem_docs.add(doc)
                    memory_candidates.append({
                        "content": doc[:250],
                        "distance": dist,
                        "source": meta.get("source", "memory"),
                    })
        except (sqlite3.Error, OSError, RuntimeError, ValueError) as e:
            print(f"[EXTRACTOR] Memory cluster query failed for '{q[:30]}...': {e}", flush=True)

    # Fallback to SQLite master tags if Chroma tag collection is empty
    if not candidates_tag_map:
        try:
            fallback_tags = vault_db.get_master_tags()
            for m in fallback_tags[:top_k_tags]:
                t = m.get("tag", "")
                if t and not is_excluded_tag(t):
                    candidates_tag_map[t] = {
                        "tag": t,
                        "category": m.get("category", "general"),
                        "description": m.get("description", ""),
                        "distance": 0.50,
                    }
        except (sqlite3.Error, OSError, RuntimeError, ValueError) as e:
            print(f"[EXTRACTOR] Fallback master tags retrieval failed: {e}", flush=True)

    sorted_tags = sorted(candidates_tag_map.values(), key=lambda x: x["distance"])[:top_k_tags]
    sorted_facts = sorted(memory_candidates, key=lambda x: x["distance"])[:top_k_facts]

    min_dist = 1.0
    if sorted_tags:
        min_dist = min(min_dist, sorted_tags[0]["distance"])
    if sorted_facts:
        min_dist = min(min_dist, sorted_facts[0]["distance"])

    novelty_threshold = getattr(cfg, "FACT_EXTRACTION_NOVELTY_THRESHOLD", 0.55)

    if min_dist < 0.40:
        novelty_guidance = (
            f"TAXONOMY MATCH CONFIDENCE: HIGH (Nearest match distance: {min_dist:.2f}).\n"
            "Strong domain alignment exists in the Master Taxonomy. Strictly adhere to existing parent domain "
            "hierarchies (e.g. #Domain/Subtopic) and matching Cat## codes."
        )
    elif min_dist < novelty_threshold:
        novelty_guidance = (
            f"TAXONOMY MATCH CONFIDENCE: MODERATE (Nearest match distance: {min_dist:.2f}).\n"
            "Related parent domains found. You may extend existing parent branches (e.g. '3D-Printing/...', "
            "'Tech/...', 'Health/...', 'Lore/...') or specialize child tags."
        )
    else:
        novelty_guidance = (
            f"TAXONOMY MATCH CONFIDENCE: LOW / NOVEL DOMAIN (Nearest match distance: {min_dist:.2f}).\n"
            "This conversation introduces topics not well-covered by existing taxonomy. "
            "You are EXPLICITLY ENCOURAGED to mint new domain-level tag hierarchies (e.g. #Domain/Subtopic or #Domain/Category/Subtopic)."
        )

    return sorted_tags, sorted_facts, min_dist, novelty_guidance


def _build_extraction_prompt(
    messages: list[dict],
    cat00: str,
    taxonomy_candidates: list[dict] | None = None,
    memory_candidates: list[dict] | None = None,
    novelty_guidance: str | None = None,
) -> str:
    """Assemble the extraction prompt with vector taxonomy and prior knowledge alignment.

    Args:
        messages: A list of message dictionaries.
        cat00: The Cat00 index taxonomy block.
        taxonomy_candidates: Retrieved master taxonomy candidates.
        memory_candidates: Retrieved prior memory chunks.
        novelty_guidance: Novelty directive string based on cosine distance.

    Returns:
        str: The assembled prompt text.
    """
    transcript = _format_messages_for_extraction(messages)

    category_block = (
        f"\n\nCATEGORY REFERENCE (use these codes for the 'category' field):\n{cat00}"
        if cat00
        else f"\n\n(Category reference unavailable — use best judgment for Cat##-{{{cfg.SUBJECT_CODE_ASSISTANT},{cfg.SUBJECT_CODE_USER}}} codes.)"
    )

    taxonomy_block = ""
    if taxonomy_candidates:
        tag_lines = []
        for t in taxonomy_candidates[:20]:
            desc = f" — {t['description']}" if t.get("description") else ""
            tag_lines.append(f"  - #{t['tag']}{desc}")
        taxonomy_block = (
            "\n\nRELEVANT MASTER TAXONOMY DOMAINS & TAGS (Tag RAG):\n" + "\n".join(tag_lines)
        )

    memory_block = ""
    if memory_candidates:
        fact_lines = [f"  - {m['content']}" for m in memory_candidates[:4]]
        memory_block = (
            "\n\nRELEVANT EXISTING KNOWLEDGE CLUSTERS (for deduplication & context):\n" + "\n".join(fact_lines)
        )

    guidance_block = f"\n\n{novelty_guidance}" if novelty_guidance else ""

    return (
        "You are a precise, highly observant personal memory extractor. "
        "Analyze the following conversation and extract ONLY concrete, durable personal facts "
        f"about {cfg.USER_NAME} (the user) or {cfg.ASSISTANT_NAME} (the AI).\n"
        "Extract: preferences, physical traits, relationships, goals, beliefs, skills, events, "
        "opinions, habits, routines, or any detail worth remembering long-term.\n"
        "DO NOT extract: greetings, small talk, questions without answers, or hypotheticals.\n"
        f"{category_block}"
        f"{taxonomy_block}"
        f"{memory_block}"
        f"{guidance_block}\n\n"
        "CRITICAL SUBSTANCE & OBSERVATION RULES:\n"
        "1. WRITE DEEP, SUBSTANTIVE OBSERVATIONS: State the exact specific facts with nouns, preferences, "
        "conditions, reasons, and temporal context. AVOID vague or shallow one-liners (e.g. Do NOT write 'Likes coffee'; "
        "instead write 'Prefers dark roast pour-over coffee with a splash of oat milk in the morning, avoiding sugar').\n"
        "2. MULTI-TIER DOMAIN TAXONOMY: Structure tags as hierarchical domain trees (e.g. `Tech/Python/FastAPI`, "
        "`Home/Coffee/Espresso`, `Lore/Dungeon_Crawler_Carl`, `Health/Sleep/Routine`). "
        "Use TitleCase with underscores for named entities (`John_Smith`, `FastAPI`).\n"
        "3. OBJECTIVITY: Write pure factual observations. Do NOT summarize or evaluate the event. "
        "Do NOT inject Category Reference titles into the observation text.\n\n"
        "Output ONLY a fenced YAML block in this exact format. "
        "If no durable facts are found, output an empty list.\n\n"
        "```facts\n"
        "facts:\n"
        f"  - subject: {cfg.USER_NAME}          # or {cfg.ASSISTANT_NAME}\n"
        f"    category: Cat05-{cfg.SUBJECT_CODE_USER}        # best matching Cat##-{cfg.SUBJECT_CODE_ASSISTANT} or Cat##-{cfg.SUBJECT_CODE_USER} code\n"
        "    tags: \"Tech/Python/FastAPI, John_Smith\"  # comma-separated hierarchical domain tags\n"
        "    summary: \"Exact, specific, contextualized observation.\"  # full substantive statement\n"
        "    confidence: high         # high / medium / low\n"
        "    date: \"2025-03-15\"      # date this was discussed (from message timestamps)\n"
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
        # Strip optional leading markdown code fence if opening fence was not closed
        cleaned_raw = re.sub(r"^```(?:facts|yaml)?\s*\n?", "", raw.strip(), flags=re.IGNORECASE)
        cleaned_raw = re.sub(r"\n?```\s*$", "", cleaned_raw)
        block = cleaned_raw
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
        raw_tags = str(item.get("tags", "")).strip()
        tags = ", ".join([normalize_tag_format(t) for t in raw_tags.split(",") if t.strip()])

        summ = str(item.get("summary", "")).strip()
        # Sanitize before any further processing — drop if injection or
        # invisible-char abuse detected.
        summ = _sanitize_entry(summ)
        if summ is None:
            print("[EXTRACTOR] Skipping fact — failed sanitization check.", flush=True)
            continue
        conf = str(item.get("confidence", "medium")).strip().lower()
        raw_date = str(item.get("date", "")).strip()

        if not subj or not cat or not summ:
            continue

        cat_base = cat.split("-")[0] if "-" in cat else cat
        if cat_base not in valid_cats:
            print(f"[EXTRACTOR] Skipping fact — unknown category: {cat}", flush=True)
            continue
        if not cat.endswith(f"-{cfg.SUBJECT_CODE_ASSISTANT}") and not cat.endswith(f"-{cfg.SUBJECT_CODE_USER}"):
            suffix = f"-{cfg.SUBJECT_CODE_USER}" if subj.lower() == cfg.USER_NAME.lower() else f"-{cfg.SUBJECT_CODE_ASSISTANT}"
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


def _build_procedure_extraction_prompt(messages: list[dict]) -> str:
    """Assemble the prompt to extract procedural instructions (how-to rules) from conversation.

    Args:
        messages: A list of message dictionaries.

    Returns:
        str: The assembled prompt text.
    """
    transcript = _format_messages_for_extraction(messages)

    return (
        "You are a precise procedure extractor for a personal memory system. "
        "Analyze the following conversation and extract ONLY concrete, repeatable procedural knowledge, "
        f"instructions, workflows, rules, or guidelines that {cfg.USER_NAME} (the user) asks {cfg.ASSISTANT_NAME} (the AI) to follow. "
        "Specifically look for patterns like: 'When X happens, do Y, watch out for Z' or 'If I ask for A, do B'. "
        "Ignore standard factual statements, preferences, small talk, and general chat. \n\n"
        "STRICT NEGATIVE CONSTRAINTS:\n"
        "- Do NOT extract static facts, personal food preferences, consumer dislikes, family entity spellings, or local business hours as procedures — these belong strictly in context entries.\n"
        "- Only extract actionable, repeatable procedural steps and behavioral workflows.\n\n"
        "Active Engine Tools Available for Procedures:\n"
        "- write_dream_entry: Save structured dream entry notes for Ricky in the Dream Entries vault archive (never use write_journal_entry for dreams)\n"
        "- write_journal_entry: Reserved EXCLUSIVELY for Evelyn's personal daily reflection / wrap-up journal entry (not dream logs or user notes)\n"
        "- write_file: Write or update general notes, feature ideas, or vault documents\n"
        "- read_file: Read files in workspace or vault notes\n"
        "- create_task, complete_task, list_tasks, get_agenda: Manage Google Tasks and schedule\n"
        "- get_health_metrics, get_recent_workouts: Query Oura Ring and Health Connect data\n"
        "- manage_vault_list: Manage checklists in vault (groceries, packing, hardware lists)\n"
        "- run_command: Run shell commands or tests in terminal\n"
        "- web_search, start_research: Web searches and multi-source research\n"
        "- generate_image: FLUX image generation\n"
        "- sync_google_tasks, sync_google_calendar, sync_google_drive: Cloud sync operations\n\n"
        "Output ONLY a fenced YAML block in this exact format. "
        "If no procedural rules are found, output an empty list.\n\n"
        "```procedures\n"
        "procedures:\n"
        "  - trigger_pattern: \"When the user asks to X\" # clear description of when this rule applies\n"
        "    steps: |\n"
        "      1. First step to take.\n"
        "      2. Second step to take.\n"
        "    suggested_tools: \"write_file\" # comma-separated tool names if applicable, or None\n"
        "    pitfalls: \"Common mistakes or things to watch out for/avoid.\" # optional\n"
        "    verification: \"How to verify the action succeeded.\" # optional\n"
        "    tags: \"skill/x, procedure/y\" # comma-separated semantic tags starting with skill/ or procedure/\n"
        "```\n\n"
        f"CONVERSATION:\n{transcript}"
    )


def _parse_procedures_yaml(raw: str) -> list[dict]:
    """Parse the YAML procedures block from the model's raw response.

    Args:
        raw: The raw response string from Ollama.

    Returns:
        list[dict]: A list of parsed and validated procedure dictionaries.
    """
    match = _PROC_YAML_BLOCK_RE.search(raw)
    if match:
        block = match.group(1)
    elif _PROC_KEY_RE.search(raw):
        # Strip optional leading markdown code fence if opening fence was not closed
        cleaned_raw = re.sub(r"^```(?:procedures|yaml)?\s*\n?", "", raw.strip(), flags=re.IGNORECASE)
        cleaned_raw = re.sub(r"\n?```\s*$", "", cleaned_raw)
        block = cleaned_raw
    else:
        return []

    if not block.strip().startswith("procedures:"):
        block = "procedures:\n" + block

    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as e:
        print(f"[EXTRACTOR] Procedures YAML parse error: {e}", flush=True)
        return []

    if not isinstance(data, dict) or "procedures" not in data:
        return []

    procedures = data["procedures"]
    if not isinstance(procedures, list):
        return []

    validated = []
    for item in procedures:
        if not isinstance(item, dict):
            continue
        trigger = str(item.get("trigger_pattern", "")).strip()
        steps = str(item.get("steps", "")).strip()
        pitfalls = item.get("pitfalls")
        verification = item.get("verification")
        suggested_tools = item.get("suggested_tools")
        raw_tags = str(item.get("tags", "")).strip()
        tags = ", ".join([normalize_tag_format(t) for t in raw_tags.split(",") if t.strip()])

        # Sanitize trigger and steps against injection
        trigger = _sanitize_entry(trigger)
        steps = _sanitize_entry(steps)

        if not trigger or not steps:
            continue

        if pitfalls:
            pitfalls = _sanitize_entry(str(pitfalls).strip())
        if verification:
            verification = _sanitize_entry(str(verification).strip())
        if suggested_tools:
            if isinstance(suggested_tools, list):
                sanitized_tools = [s for t in suggested_tools if (s := _sanitize_entry(str(t).strip()))]
                suggested_tools = ", ".join(sanitized_tools)
            else:
                suggested_tools = _sanitize_entry(str(suggested_tools).strip())

        validated.append({
            "trigger_pattern": trigger,
            "steps": steps,
            "pitfalls": pitfalls,
            "verification": verification,
            "suggested_tools": suggested_tools,
            "tags": tags
        })

    return validated


async def _do_extraction(messages: list[dict]):
    """Run the core fact extraction LLM call on a batch of messages.

    Args:
        messages: A list of chat message dictionaries.
    """
    cat00 = load_cat00_index()
    tag_candidates, mem_candidates, _min_dist, novelty_guidance = retrieve_candidate_taxonomy_and_clusters(messages)
    prompt = _build_extraction_prompt(
        messages=messages,
        cat00=cat00,
        taxonomy_candidates=tag_candidates,
        memory_candidates=mem_candidates,
        novelty_guidance=novelty_guidance,
    )


    # Compute fallback date from the latest message timestamp in the batch
    latest_ts = max((m.get("ts") or 0) for m in messages)
    if latest_ts:
        try:
            fallback_date = dt.datetime.fromtimestamp(latest_ts, tz=dt.UTC).astimezone().strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            fallback_date = dt.datetime.now(dt.UTC).astimezone().date().strftime("%Y-%m-%d")
    else:
        fallback_date = dt.datetime.now(dt.UTC).astimezone().date().strftime("%Y-%m-%d")

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
    options: dict[str, Any] = {
        "num_ctx": cfg.NUM_CTX,
        **{
            key: val
            for key, val in {
                "temperature": cfg.TEMPERATURE,
                "min_p": cfg.MIN_P,
                "top_k": cfg.TOP_K,
                "top_p": cfg.TOP_P,
                "repeat_penalty": cfg.REPEAT_PENALTY,
                "repeat_last_n": cfg.REPEAT_LAST_N,
                "seed": cfg.SEED,
            }.items()
            if val is not None
        },
    }

    # Scale token budget to batch size — a small batch needs far fewer tokens
    options["num_predict"] = min(64 * len(messages), 512)

    # Configure extraction stop sequences to halt model generation immediately upon closing the YAML fence
    extraction_stops = list(cfg.STOP_SEQUENCES or [])
    for s in ["\n```\n", "\n```", "```\n"]:
        if s not in extraction_stops:
            extraction_stops.append(s)
    options["stop"] = extraction_stops

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

    timeout = getattr(cfg, "FACT_EXTRACTION_TIMEOUT", 450)
    httpx_timeout = httpx.Timeout(timeout, connect=60.0, read=timeout, write=60.0, pool=60.0)
    content_buffer = ""
    try:
        async with (
            httpx.AsyncClient(timeout=httpx_timeout) as client,
            client.stream("POST", f"{cfg.OLLAMA_URL}/api/chat", json=payload) as resp,
        ):
            resp.raise_for_status()
            aiter = resp.aiter_lines()
            while True:
                try:
                    line = await asyncio.wait_for(aiter.__anext__(), timeout=180.0)
                except StopAsyncIteration:
                    break
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                    msg = chunk.get("message", {})
                    content_buffer += msg.get("content", "")
                except json.JSONDecodeError:
                    continue
    except asyncio.CancelledError:
        raise  # Let it propagate — caller handles it
    except (httpx.HTTPError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
        print(f"[EXTRACTOR] Ollama call failed: {e}", flush=True)
        raise

    raw = content_buffer.strip()
    elapsed = time.time() - start

    if not raw:
        print("[EXTRACTOR] Warning: empty response from model.", flush=True)
    else:
        print(f"[EXTRACTOR] Response received in {elapsed:.1f}s.", flush=True)
        facts = _parse_facts_yaml(raw, fallback_date)
        if facts:
            print(f"[EXTRACTOR] {len(facts)} fact(s) extracted. Writing to memory DB...", flush=True)
            written = write_extracted_facts(facts)
            print(f"[EXTRACTOR] Wrote {written} fact(s) to SQLite DB.", flush=True)
        else:
            print("[EXTRACTOR] No valid facts extracted.", flush=True)

    # Pass 2: Procedural Knowledge Capture
    proc_prompt = _build_procedure_extraction_prompt(messages)
    proc_messages = [
        {
            "role": "system",
            "content": "You are a precise procedure extractor. You record pure, repeatable actions and rules. "
                       "Output only the YAML block, nothing else.",
        },
        {"role": "user", "content": proc_prompt},
    ]

    payload["messages"] = proc_messages
    payload["options"]["num_predict"] = 384
    payload["options"]["stop"] = extraction_stops

    print(
        f"[EXTRACTOR] Extracting procedures from {len(messages)} new message(s)...",
        flush=True,
    )
    start_proc = time.time()
    proc_content_buffer = ""

    try:
        async with (
            httpx.AsyncClient(timeout=httpx_timeout) as client,
            client.stream("POST", f"{cfg.OLLAMA_URL}/api/chat", json=payload) as resp,
        ):
            resp.raise_for_status()
            proc_aiter = resp.aiter_lines()
            while True:
                try:
                    line = await asyncio.wait_for(proc_aiter.__anext__(), timeout=180.0)
                except StopAsyncIteration:
                    break
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                    msg = chunk.get("message", {})
                    proc_content_buffer += msg.get("content", "")
                except json.JSONDecodeError:
                    continue
    except asyncio.CancelledError:
        raise
    except (httpx.HTTPError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
        print(f"[EXTRACTOR] Ollama procedure call failed: {e}", flush=True)
        raise

    proc_raw = proc_content_buffer.strip()
    elapsed_proc = time.time() - start_proc

    if proc_raw:
        print(f"[EXTRACTOR] Procedure response received in {elapsed_proc:.1f}s.", flush=True)
        procedures = _parse_procedures_yaml(proc_raw)
        if procedures:
            print(f"[EXTRACTOR] {len(procedures)} procedure(s) extracted. Writing to memory DB...", flush=True)
            written_proc = write_extracted_procedures(procedures)
            print(f"[EXTRACTOR] Wrote {written_proc} procedure(s) to SQLite DB.", flush=True)
        else:
            print("[EXTRACTOR] No valid procedures extracted.", flush=True)
    else:
        print("[EXTRACTOR] Warning: empty procedure response from model.", flush=True)


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

    today_str = dt.datetime.now(dt.UTC).astimezone().date().strftime("%Y-%m-%d")
    written = 0

    for fact in facts:
        category = fact["category"]
        subject  = fact["subject"]
        raw_summary = fact["summary"]
        # Final sanitization gate — defence-in-depth for any caller that
        # bypasses _parse_facts_yaml (e.g. direct API calls or future tooling).
        summary = _sanitize_entry(raw_summary)
        if summary is None:
            print(
                f"[EXTRACTOR] Skipping write — failed sanitization gate: "
                f"{raw_summary[:80]}",
                flush=True,
            )
            continue
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
        except (sqlite3.Error, OSError, ValueError) as e:
            print(f"[EXTRACTOR] Failed to insert fact: {e}", flush=True)

    return written


def write_extracted_procedures(procedures: list[dict]) -> int:
    """Write extracted procedures to the SQLite memory database.

    Args:
        procedures: List of validated procedure dictionaries.

    Returns:
        int: The number of procedures successfully inserted.
    """
    import memory_db
    written = 0

    for proc in procedures:
        # Check for duplication. We look up existing procedures and compare
        # their triggers and steps to avoid exact or near duplicates.
        existing = memory_db.get_all_procedures(status="live") + memory_db.get_all_procedures(status="extracted")
        duplicate = False
        candidate_words = set(re.findall(r"\b[a-z0-9_]{3,}\b", proc["trigger_pattern"].lower()))
        stopwords = {"when", "the", "user", "says", "asks", "tells", "you", "for", "with", "that", "this", "and", "are", "your", "they"}
        candidate_kws = candidate_words - stopwords

        merge_candidate_id = None
        best_overlap = 0.0

        for ext in existing:
            if ext["trigger_pattern"].lower() == proc["trigger_pattern"].lower():
                duplicate = True
                break
            ext_words = set(re.findall(r"\b[a-z0-9_]{3,}\b", ext["trigger_pattern"].lower()))
            ext_kws = ext_words - stopwords
            if candidate_kws and ext_kws:
                jaccard = len(candidate_kws & ext_kws) / len(candidate_kws | ext_kws)
                if jaccard >= 0.70:
                    duplicate = True
                    break
                elif jaccard >= 0.35 and ext.get("status") == "live" and jaccard > best_overlap:
                    best_overlap = jaccard
                    merge_candidate_id = ext.get("id")

        if duplicate:
            print(f"[EXTRACTOR] Skipping duplicate procedure trigger: {proc['trigger_pattern'][:80]}...", flush=True)
            continue

        try:
            memory_db.insert_procedure(
                trigger_pattern=proc["trigger_pattern"],
                steps=proc["steps"],
                pitfalls=proc.get("pitfalls"),
                verification=proc.get("verification"),
                source="extracted",
                status="extracted",  # Always start as extracted (pending review)
                tags=proc.get("tags"),
                suggested_tools=proc.get("suggested_tools"),
                merged_into_id=merge_candidate_id,
            )
            written += 1
        except (sqlite3.Error, OSError, ValueError) as e:
            print(f"[EXTRACTOR] Failed to insert procedure: {e}", flush=True)

    return written
