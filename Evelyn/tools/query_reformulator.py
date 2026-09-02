# query_reformulator.py
# date created: 2026-04-26 13:03:48
# date modified: 2026-09-01 20:10:39
# tags: #query, #reformulation, #search, #keywords, #prompts

"""
query_reformulator.py — Converts conversational messages into embedding-optimized search queries.

Raw user messages produce poor vector matches (e.g. "I talked to my mom today" → weak hit).
Uses the already-loaded LLM to extract concise search keywords with zero VRAM eviction
(same model/options as the chat loop). Falls back to the original message on timeout/error.

Exports:
  reformulate_query(user_message) — Main entry point; returns reformulated query string.

Key config: evelyn_config.py (RAG_REFORMULATE_ENABLED, RAG_REFORMULATE_MIN_WORDS, RAG_REFORMULATE_TIMEOUT)
Design rationale: reference/docstring_guide.md#query_reformulatorpy--design-rationale
"""


import json
import re
import time
from typing import Any

import httpx

import evelyn_config as cfg

# ---------------------------------------------------------------------------
# Extraction prompt — kept lean to minimize token cost (~60 tokens total)
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You are a search keyword extractor. Given a conversational message, "
    "output ONLY the key search terms that would help retrieve relevant "
    "personal knowledge. Include names, topics, emotional states, and "
    "implied subjects. Output keywords as a short phrase, nothing else. "
    "Do not explain, do not use bullet points."
)

# Cache: avoid redundant LLM calls for identical messages within a session
_cache: dict[str, str] = {}

# Common conversational leading patterns to strip for dense vector search
_PREAMBLE_PATTERNS = [
    re.compile(r"^(?:hey\s+(?:evelyn|there)?[\s,]*)+", re.IGNORECASE),
    re.compile(r"^(?:by\s+the\s+way[\s,]*)+", re.IGNORECASE),
    re.compile(r"^(?:can\s+you\s+(?:please\s+)?(?:check|tell\s+me|show\s+me|find|remind\s+me)\s+(?:about\s+)?)+", re.IGNORECASE),
    re.compile(r"^(?:do\s+you\s+(?:remember|recall)\s+(?:what\s+we\s+talked\s+about\s+)?(?:regarding\s+)?)+", re.IGNORECASE),
    re.compile(r"^(?:what\s+were\s+we\s+(?:planning|talking\s+about)\s+(?:for|regarding|about)\s+)+", re.IGNORECASE),
    re.compile(r"^(?:where\s+did\s+we\s+leave\s+off\s+on\s+)+", re.IGNORECASE),
    re.compile(r"^(?:tell\s+me\s+about\s+)+", re.IGNORECASE),
]


def clean_conversational_query(user_message: str) -> str:
    """Fast local conversational preamble cleaner (0ms LLM overhead).

    Strips common chat filler and question framing so embedding models receive
    high-density topical content without conversational noise.

    Args:
        user_message: Raw user message from chat input.

    Returns:
        Cleaned topical string for dense vector embedding.
    """
    cleaned = user_message.strip()
    if not cleaned:
        return cleaned

    for pattern in _PREAMBLE_PATTERNS:
        cleaned = pattern.sub("", cleaned).strip()

    # If stripped too aggressively, fall back to original
    if len(cleaned.split()) < 2 and len(user_message.split()) >= 2:
        return user_message.strip()

    return cleaned


def reformulate_query(user_message: str) -> str:
    """Extract search-relevant keywords from a conversational user message.

    When cfg.RAG_REFORMULATE_ENABLED is False (default), executes zero-latency
    local preamble cleaning. When True, queries Ollama for LLM keyword extraction.

    Args:
        user_message: Raw user message from the chat input.

    Returns:
        Reformulated query string, or the cleaned message.
    """
    # Master switch: direct zero-latency semantic search (15x faster)
    if not getattr(cfg, "RAG_REFORMULATE_ENABLED", False):
        return clean_conversational_query(user_message)

    # Skip heuristic: short messages are usually names or simple queries
    # that already work well as embedding queries (100% hit rate in benchmarks)
    min_words = getattr(cfg, "RAG_REFORMULATE_MIN_WORDS", 4)
    word_count = len(user_message.split())
    if word_count < min_words:
        if getattr(cfg, "DEBUG_LOGGING", False):
            print(
                f"[RAG REWRITE] SKIP (words={word_count} < {min_words})"
                f" query='{user_message}'",
                flush=True,
            )
        return user_message

    # Cache check
    if user_message in _cache:
        if getattr(cfg, "DEBUG_LOGGING", False):
            print(
                f"[RAG REWRITE] CACHED query='{user_message[:60]}'"
                f" rewritten='{_cache[user_message][:60]}'",
                flush=True,
            )
        return _cache[user_message]

    # Build Ollama request — identical options to main chat to avoid model swap
    timeout = getattr(cfg, "RAG_REFORMULATE_TIMEOUT", 10)
    options: dict[str, Any] = {
        "num_ctx": cfg.NUM_CTX,
        "num_predict": 50,
    }
    options.update({
        key: val
        for key, val in {
            "temperature": 0.3,  # Low temp for deterministic extraction
            "min_p": cfg.MIN_P,
            "top_k": cfg.TOP_K,
            "top_p": cfg.TOP_P,
            "repeat_penalty": cfg.REPEAT_PENALTY,
            "repeat_last_n": cfg.REPEAT_LAST_N,
            "seed": cfg.SEED,
        }.items()
        if val is not None
    })
    if cfg.STOP_SEQUENCES:
        options["stop"] = cfg.STOP_SEQUENCES

    payload = {
        "model": cfg.MODEL_NAME,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
        "options": options,
        # Thinking disabled: extraction is a simple task that doesn't need
        # a reasoning chain, and the thinking budget wastes the tight timeout.
        # This does NOT cause a model swap — Ollama handles think=false as a
        # per-request flag, not a model config change.
        "think": False,
    }

    start = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(f"{cfg.OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
            result = resp.json()
    except (httpx.HTTPError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
        # Fallback to original message — degraded but not broken
        elapsed = time.perf_counter() - start
        print(
            f"[RAG REWRITE] FAILED ({elapsed:.1f}s): {e}"
            f" — falling back to original query",
            flush=True,
        )
        return user_message

    elapsed = time.perf_counter() - start

    # Extract the response content, strip thinking tags if present
    content = result.get("message", {}).get("content", "").strip()
    if not content:
        print("[RAG REWRITE] EMPTY response — falling back to original query", flush=True)
        return user_message

    # Clean up: remove any thinking artifacts, quotes, or preamble
    content = re.sub(r"^.*?</think>", "", content, flags=re.DOTALL).strip()
    content = content.strip('"\'')

    # Cache the result
    _cache[user_message] = content

    if getattr(cfg, "DEBUG_LOGGING", False):
        print(
            f"[RAG REWRITE] ({elapsed:.1f}s)"
            f" original='{user_message[:60]}'"
            f" rewritten='{content[:60]}'",
            flush=True,
        )

    return content
