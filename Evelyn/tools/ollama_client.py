# ollama_client.py
# date created: 2026-08-28 12:25:00
# date modified: 2026-08-28 12:25:00
# tags: #ollama, #llm, #http, #gateway, #json, #utils

"""
ollama_client.py — Canonical Local LLM HTTP Client Gateway.

Exports:
    query_ollama()          — Synchronous text query to Ollama (/api/chat or /api/generate).
    query_ollama_json()     — Structured JSON query with markdown code fence extraction.
    get_ollama_status()     — Quick status probe and installed model inventory.

Key config: evelyn_config.py (OLLAMA_URL, MODEL_NAME), string_utils.py
See also: reference/engine_architecture.md
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any

# Anchoring paths before importing evelyn_config
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
for _d in (ROOT_DIR, TOOLS_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import evelyn_config as cfg
from Evelyn.tools.string_utils import strip_thinking_tags


def query_ollama(
    prompt: str,
    model: str | None = None,
    system: str | None = None,
    endpoint: str = "/api/chat",
    options: dict[str, Any] | None = None,
    timeout: int = 120,
    strip_thinking: bool = True,
) -> str:
    """Execute a synchronous HTTP request to the local Ollama instance.

    Args:
        prompt: User prompt text.
        model: Target model name (defaults to cfg.MODEL_NAME).
        system: Optional system instruction.
        endpoint: API route ("/api/chat" or "/api/generate").
        options: Model generation parameters (temperature, num_predict, etc.).
        timeout: Socket read timeout in seconds.
        strip_thinking: Whether to automatically remove <think> tags.

    Returns:
        Generated response text string, or empty string on network/execution failure.
    """
    base_url = getattr(cfg, "OLLAMA_URL", "http://localhost:11434").rstrip("/")
    target_model = model or getattr(cfg, "MODEL_NAME", "gemma4:12b")

    if endpoint == "/api/generate":
        payload: dict[str, Any] = {
            "model": target_model,
            "prompt": prompt,
            "stream": False,
        }
        if system:
            payload["system"] = system
        if options:
            payload["options"] = options
    else:
        # Default: /api/chat
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": target_model,
            "messages": messages,
            "stream": False,
        }
        if options:
            payload["options"] = options

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}{endpoint}",
        data=data_bytes,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))

            if endpoint == "/api/generate":
                raw_text = resp_data.get("response", "")
            else:
                raw_text = resp_data.get("message", {}).get("content", "")

            if strip_thinking:
                return strip_thinking_tags(raw_text)
            return raw_text.strip()

    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        print(f"[OLLAMA_CLIENT] Query error on {endpoint}: {e}")
        return ""


def query_ollama_json(
    prompt: str,
    model: str | None = None,
    system: str | None = None,
    schema: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """Execute a structured JSON query to Ollama and parse the dictionary safely.

    Handles markdown code fences (```json ... ```) and trailing commentary.

    Args:
        prompt: User prompt text.
        model: Target model name.
        system: Optional system instruction.
        schema: Optional JSON schema dictionary.
        options: Generation options.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON dictionary, or empty dict on failure.
    """
    base_url = getattr(cfg, "OLLAMA_URL", "http://localhost:11434").rstrip("/")
    target_model = model or getattr(cfg, "MODEL_NAME", "gemma4:12b")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, Any] = {
        "model": target_model,
        "messages": messages,
        "stream": False,
        "format": "json" if schema is None else schema,
    }
    if options:
        payload["options"] = options

    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=data_bytes,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            raw_text = resp_data.get("message", {}).get("content", "").strip()

            # Clean thinking tags first
            clean_text = strip_thinking_tags(raw_text)

            # Strip markdown json code fences if present
            fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean_text)
            json_str = fence_match.group(1).strip() if fence_match else clean_text

            if not json_str:
                return {}

            parsed = json.loads(json_str)
            if isinstance(parsed, dict):
                return parsed
            elif isinstance(parsed, list):
                return {"items": parsed}
            return {"value": parsed}

    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
        print(f"[OLLAMA_CLIENT] JSON query error: {e}")
        return {}


def get_ollama_status(timeout: int = 5) -> dict[str, Any]:
    """Check Ollama service reachability and retrieve available model tags.

    Args:
        timeout: Socket timeout for the health probe in seconds.

    Returns:
        Dict with 'status' ('online' or 'offline') and 'models' list.
    """
    base_url = getattr(cfg, "OLLAMA_URL", "http://localhost:11434").rstrip("/")
    req = urllib.request.Request(f"{base_url}/api/tags")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name") for m in data.get("models", [])]
            return {
                "status": "online",
                "models": models,
                "url": base_url,
            }
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        return {
            "status": "offline",
            "error": str(e),
            "url": base_url,
        }
