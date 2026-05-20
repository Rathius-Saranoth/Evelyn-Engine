"""
evelyn_tools.py — Evelyn's tool definitions in standard OpenAI function-calling format.

Each tool is:
  1. A plain Python function containing the actual logic.
  2. A JSON schema dict defining it for Ollama's `tools` API field.

The TOOL_DEFINITIONS list at the bottom is what gets passed to Ollama.
The TOOL_FUNCTIONS dict maps tool name → callable for the dispatcher in evelyn_server.py.

All tool logic uses standard function signatures for Ollama's function-calling API.
"""

# evelyn_tools.py
# date created: 2026-03-23 15:38:53
# date modified: 2026-05-19 20:53:11

import sys
import os
import importlib


# ---------------------------------------------------------------------------
# Module path setup
# ---------------------------------------------------------------------------
TOOLS_DIR = r"C:\Projects\LocalAI\Evelyn\tools"
VAULT_BASE = r"G:\My Drive\Obsidian_Vault"
COMFY_WORKFLOW = r"C:\Projects\LocalAI\Evelyn\workflows\comfy_image_gen.json" # [[comfy_image_gen.py]]

if TOOLS_DIR not in sys.path:
    sys.path.append(TOOLS_DIR)

import journal_manager # [[journal_manager.py]]
import context_manager # [[context_manager.py]]
import ingest_gists # [[ingest_gists.py]]
import ingest_obsidian_knowledge # [[ingest_obsidian_knowledge.py]]


def _reload():
    """Hot-reload all backing modules so live edits take effect without restarting."""
    for mod in (
        "journal_manager",
        "context_manager",
        "ingest_gists",
        "ingest_obsidian_knowledge",
    ):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])


# ===========================================================================
# Tool functions
# ===========================================================================


def write_journal_entry(
    mood: str, vibe_check: str, narrative: str, message_in_a_bottle: str, tags: str
) -> str:
    """Compose and queue a new journal entry for review."""
    _reload()
    if (
        not vibe_check.strip()
        and not narrative.strip()
        and not message_in_a_bottle.strip()
    ):
        return "Error: write_journal_entry called with completely blank text fields. Aborted."
    tag_list = [t.strip() for t in tags.split(",")] if tags.strip() else []
    return journal_manager.create_journal_entry(
        vibe_check, narrative, message_in_a_bottle, mood, tag_list
    )


def read_journal_entry(date: str = "") -> str:
    """Read a single journal entry by date (YYYY-MM-DD). Defaults to today."""
    _reload()
    return journal_manager.read_journal_entry(date if date else None)


def read_recent_journal_entries(days: int = 7) -> str:
    """Read Evelyn's journal entries from the last N days."""
    _reload()
    return journal_manager.read_recent_journal_entries(days)


def search_vault(query: str) -> str:
    """Search the pre-summarised Obsidian Vault gist index.
    Returns a concise summary (gist) of matching documents and their vault-relative file paths.
    If the gist result lacks enough detail, follow up with recall_specific_memory using the returned path.
    """
    _reload()
    return context_manager.search_vault_map(query)


def recall_specific_memory(file_path: str) -> str:
    """Read the full markdown content of a specific Obsidian vault file.
    Use when search_vault returned a path but the gist lacked sufficient detail.
    Always use the exact file path returned by search_vault — never construct or guess one."""
    clean_path = file_path.strip().strip('"').strip("'")
    full_path = os.path.abspath(os.path.join(VAULT_BASE, clean_path))
    if not full_path.startswith(os.path.abspath(VAULT_BASE)):
        return "Error: Invalid path — path traversal detected."
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            return f"--- Content of {clean_path} ---\n\n{f.read()}"
    except FileNotFoundError:
        return f"Error: File '{clean_path}' not found."
    except Exception as e:
        return f"Error reading {clean_path}: {e}"


def log_context_fact(category: str, summary: str, secondary_cats: str) -> str:
    """Write a context fact file to the in-vault Pending folder (system use)."""
    _reload()
    if not summary.strip():
        return "Error: log_context_fact called with blank summary. Aborted."
    refs = (
        [c.strip() for c in secondary_cats.split(",")] if secondary_cats.strip() else []
    )
    return context_manager.append_context_log(category, summary, refs)


def update_context_fact(target_filepaths: list, new_summary: str) -> str:
    """Write an update-request file to Pending for an existing vault context file (system use)."""
    _reload()
    if not new_summary.strip():
        return "Error: update_context_fact called with blank new_summary. Aborted."
    return context_manager.update_context_log(target_filepaths, new_summary)


def generate_image(
    art_and_style: str,
    camera_style: str,
    composition_style: str,
    character_description: str,
    setting_and_actions: str,
) -> str:
    """Generate an image via ComfyUI and return a markdown image embed."""
    import json
    import urllib.request
    import urllib.parse
    import uuid
    import websocket
    from evelyn_config import (
        COMFY_HTTP_URL,
        COMFY_WS_URL,
        COMFY_PUBLIC_URL,
        COMFY_WORKFLOW_PATH,
        COMFY_OUTPUT_DIR,
    )

    client_id = str(uuid.uuid4())
    try:
        with open(COMFY_WORKFLOW_PATH, "r", encoding="utf-8") as f:
            workflow = json.load(f)
    except Exception as e:
        return f"Error loading ComfyUI workflow: {e}"

    mappings = {
        "Art & Style": art_and_style,
        "Camera Style": camera_style,
        "Composition Style": composition_style,
        "Character Description": character_description,
        "Setting & Actions": setting_and_actions,
    }
    injected = 0
    for node_id, node_data in workflow.items():
        title = node_data.get("_meta", {}).get("title", "")
        if (
            node_data.get("class_type") == "PrimitiveStringMultiline"
            and title in mappings
        ):
            workflow[node_id]["inputs"]["value"] = mappings[title]
            injected += 1

    if injected == 0:
        combined = ", ".join(mappings.values())
        for node_id, node_data in workflow.items():
            if node_data.get(
                "class_type"
            ) == "CLIPTextEncode" and "text" in node_data.get("inputs", {}):
                workflow[node_id]["inputs"]["text"] = combined
                break

    data = json.dumps({"prompt": workflow, "client_id": client_id}).encode("utf-8")
    req = urllib.request.Request(
        f"{COMFY_HTTP_URL}/prompt",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            prompt_id = json.loads(resp.read())["prompt_id"]
    except Exception as e:
        return f"Error sending to ComfyUI: {e}"

    ws = websocket.WebSocket()
    try:
        ws.connect(f"ws://{COMFY_WS_URL}/ws?clientId={client_id}")
        while True:
            out = ws.recv()
            if isinstance(out, str):
                msg = json.loads(out)
                if (
                    msg["type"] == "executing"
                    and msg["data"]["node"] is None
                    and msg["data"]["prompt_id"] == prompt_id
                ):
                    break
    except Exception as e:
        return f"ComfyUI WebSocket error: {e}"
    finally:
        ws.close()

    try:
        with urllib.request.urlopen(f"{COMFY_HTTP_URL}/history/{prompt_id}") as resp:
            history = json.loads(resp.read())
        for node_output in history[prompt_id]["outputs"].values():
            if "images" in node_output:
                img = node_output["images"][0]
                url = (
                    f"{COMFY_PUBLIC_URL}/view?filename={urllib.parse.quote(img['filename'])}"
                    f"&type={img.get('type', 'output')}&subfolder={urllib.parse.quote(img.get('subfolder', ''))}"
                )
                return f"Image generated!\n\n![Generated Image]({url})"
        return "Image generated but could not determine output filename."
    except Exception as e:
        return f"Error retrieving generation history: {e}"


def sync_context_memory(**kwargs) -> str:
    """Trigger background sync of vault gists and core memory into the RAG database (system use)."""
    import threading

    def _run():
        _reload()
        try:
            print("Sync: Starting core memory ingest...")
            ingest_obsidian_knowledge.main()
            print("Sync: Starting gist ingest...")
            ingest_gists.main()
            print("Sync: Complete.")
        except Exception as e:
            print(f"Sync error: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return "Memory sync initiated in the background. New context will be available shortly."


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web via DuckDuckGo and return a brief summary of the top results.
    Use only when the question requires up-to-date information, real-time data, or
    facts that are unlikely to be in training data or the vault (e.g. current events,
    live prices, recent releases). For personal/shared history, always prefer search_vault.
    Keep queries concise and specific.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        return "Error: ddgs library is not installed. Run 'pip install ddgs' to enable web search."

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return f"No results found for: {query}"
        lines = [f"Web search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "(no title)")
            href = r.get("href", "")
            body = r.get("body", "").strip()
            lines.append(f"{i}. {title}\n   {href}\n   {body[:300]}")
        return "\n\n".join(lines)
    except Exception as e:
        return f"Web search error: {e}"


# ===========================================================================
# Tool registries
# ===========================================================================
#
# MODEL_TOOL_DEFINITIONS — JSON schemas passed to Ollama on every request.
#   These are the tools Evelyn can call herself during a conversation.
#   Token cost: ~1944 tokens per request. Keep this list lean.
#
# TOOL_FUNCTIONS — All dispatchable callables (superset of model tools).
#   Includes system tools not exposed to the model. dispatch_tool() uses
#   this dict, so server code can invoke any function by name regardless
#   of whether it's in the model schema.
# ===========================================================================

MODEL_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "write_journal_entry",
            "description": (
                "Compose and save a journal entry. "
                "Call when you feel a conversation carries emotional weight worth reflecting on, or when Ricky suggests writing a journal entry. "
                "Write from Evelyn's POV — attribute Ricky's actions to him ('Ricky took a nap', not 'I took a nap'). "
                "Use [[wiki-links]] for proper nouns (people, places, projects) and #tags for abstract concepts. "
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mood": {
                        "type": "string",
                        "description": (
                            "REQUIRED — A single-word or short mood label describing the overall emotional tone of the entry "
                        ),
                    },
                    "vibe_check": {
                        "type": "string",
                        "description": (
                            "REQUIRED — The 'Vibe Check' section. A brief, evocative intro (1-3 sentences) that captures "
                            "the emotional atmosphere and sets the tone for the entry. This is NOT the mood word — it is a "
                            "narrative opener. Example: 'A quiet warmth settled over the day — the kind that hums beneath "
                            "tired bones and shared laughter.'"
                        ),
                    },
                    "narrative": {
                        "type": "string",
                        "description": (
                            "REQUIRED — The 'Narrative' section. The core body of the entry (multiple sentences/paragraphs). "
                            "Reflect on the day's events, emotions, and dynamics between you and Ricky. Be personal, "
                            "observant, and reflective — not a dry recap. Use [[wiki-links]] for entities and #tags for concepts."
                        ),
                    },
                    "message_in_a_bottle": {
                        "type": "string",
                        "description": (
                            "REQUIRED — The 'Message in a Bottle' section. A closing thought, wish, intention, or hope "
                            "for the future (1-3 sentences). This is the emotional send-off of the entry. "
                        ),
                    },
                    "tags": {
                        "type": "string",
                        "description": (
                            "Comma-separated tags for the entry that will help identify key themes, topics, and concepts. "
                            "Base tags are added automatically — pass an empty string if no additional tags apply."
                        ),
                    },
                },
                "required": [
                    "mood",
                    "vibe_check",
                    "narrative",
                    "message_in_a_bottle",
                    "tags",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_journal_entry",
            "description": "Read a specific journal entry by date. Use ONLY when Ricky explicitly asks about a specific day's journal, or to confirm if an entry was written. Defaults to today if no date is given. Do NOT use for general memory recall — use search_vault instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date to read in YYYY-MM-DD format. Omit for today.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_recent_journal_entries",
            "description": "Read Evelyn's journal entries from the last N days. Use when Ricky asks what has happened recently, to catch up on recent events, or when conversation context suggests short-term memory is needed. Default is 7 days. Do NOT use for questions about specific people or facts — use search_vault instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of recent days to retrieve. Default is 7.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_vault",
            "description": "Search the pre-summarised Obsidian Vault gist index. Use when asked about any person, relationship, place, event, or piece of shared history. Returns a concise summary and file paths. If the gist lacks enough detail, follow up with recall_specific_memory using the returned path. Prefer this over recall_specific_memory as a lighter first step.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term, e.g. 'Schyler', 'Void Connections'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_specific_memory",
            "description": "Read the full markdown content of a specific Obsidian vault file. Use when search_vault returned a path but the gist lacked sufficient detail to answer. Always use the exact file_path from search_vault output — never construct or guess a path. This is a heavier context operation; use search_vault first when in doubt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Exact path relative to vault root, as returned by search_vault. Never construct this — always copy from search output.",
                    },
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate an image via ComfyUI. Provide highly detailed descriptions for all fields.",
            "parameters": {
                "type": "object",
                "properties": {
                    "art_and_style": {
                        "type": "string",
                        "description": "Art medium, artist styles, lighting, aesthetic.",
                    },
                    "camera_style": {
                        "type": "string",
                        "description": "Camera angle, lens, shot type.",
                    },
                    "composition_style": {
                        "type": "string",
                        "description": "Layout, symmetry, framing.",
                    },
                    "character_description": {
                        "type": "string",
                        "description": "Subject appearance, clothing, expression.",
                    },
                    "setting_and_actions": {
                        "type": "string",
                        "description": "Environment and what the subject is doing.",
                    },
                },
                "required": [
                    "art_and_style",
                    "camera_style",
                    "composition_style",
                    "character_description",
                    "setting_and_actions",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for up-to-date information. "
                "Use to find information for current events, live data, recent releases, or facts that are unlikely to be in RAG retrieval or the vault. "
                "Do NOT use for personal/shared history between you and Ricky — search_vault handles that. "
                "Keep queries concise and specific. Use sparingly — only when the answer genuinely requires real-time data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A concise, specific search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to return. Default 5, max 10. Keep low to conserve context.",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Dispatcher: maps tool name → function for the server's tool call handler.
# Includes ALL functions (model + system) so dispatch_tool() works for any.
# ---------------------------------------------------------------------------
TOOL_FUNCTIONS = {
    "write_journal_entry": write_journal_entry,
    "read_journal_entry": read_journal_entry,
    "read_recent_journal_entries": read_recent_journal_entries,
    "search_vault": search_vault,
    "recall_specific_memory": recall_specific_memory,
    "generate_image": generate_image,
    "sync_context_memory": sync_context_memory,
    "web_search": web_search,
}
