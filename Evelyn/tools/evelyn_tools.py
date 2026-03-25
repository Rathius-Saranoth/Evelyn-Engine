"""
evelyn_tools.py — Evelyn's tool definitions in standard OpenAI function-calling format.

Each tool is:
  1. A plain Python function containing the actual logic.
  2. A JSON schema dict defining it for Ollama's `tools` API field.

The TOOL_DEFINITIONS list at the bottom is what gets passed to Ollama.
The TOOL_FUNCTIONS dict maps tool name → callable for the dispatcher in evelyn_server.py.

All tool logic uses standard function signatures for Ollama's function-calling API.
"""

import sys
import os
import importlib

# ---------------------------------------------------------------------------
# Module path setup
# ---------------------------------------------------------------------------
TOOLS_DIR    = r"C:\Projects\LocalAI\Evelyn\tools"
VAULT_BASE   = r"G:\My Drive\Obsidian_Vault"
COMFY_WORKFLOW = r"C:\Projects\LocalAI\Evelyn\workflows\comfy_image_gen.json"

if TOOLS_DIR not in sys.path:
    sys.path.append(TOOLS_DIR)

import journal_manager
import context_manager
import ingest_gists
import ingest_obsidian_knowledge


def _reload():
    """Hot-reload all backing modules so live edits take effect without restarting."""
    for mod in ("journal_manager", "context_manager", "ingest_gists", "ingest_obsidian_knowledge"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])


# ===========================================================================
# Tool functions
# ===========================================================================

def write_journal_entry(vibe_check: str, narrative: str, message_in_a_bottle: str, mood: str, tags: str) -> str:
    """Compose and queue a new journal entry for review."""
    _reload()
    if not vibe_check.strip() and not narrative.strip() and not message_in_a_bottle.strip():
        return "Error: write_journal_entry called with completely blank text fields. Aborted."
    tag_list = [t.strip() for t in tags.split(",")] if tags.strip() else []
    return journal_manager.create_journal_entry(vibe_check, narrative, message_in_a_bottle, mood, tag_list)


def read_journal_entry(date: str = "") -> str:
    """Read a single journal entry by date (YYYY-MM-DD). Defaults to today."""
    _reload()
    return journal_manager.read_journal_entry(date if date else None)


def read_recent_journal_entries(days: int = 7) -> str:
    """Read Evelyn's journal entries from the last N days."""
    _reload()
    return journal_manager.read_recent_journal_entries(days)


def search_vault(query: str) -> str:
    """Full-text search across the entire Obsidian Vault map."""
    _reload()
    return context_manager.search_vault_map(query)


def recall_specific_memory(file_path: str) -> str:
    """Read the full content of a specific markdown file from the vault."""
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
    """Queue a new context fact for Ricky's review."""
    _reload()
    if not summary.strip():
        return "Error: log_context_fact called with blank summary. Aborted."
    refs = [c.strip() for c in secondary_cats.split(",")] if secondary_cats.strip() else []
    return context_manager.append_context_log(category, summary, refs)


def update_context_fact(target_filepaths: list, new_summary: str) -> str:
    """Queue an update request for one or more existing vault context files."""
    _reload()
    if not new_summary.strip():
        return "Error: update_context_fact called with blank new_summary. Aborted."
    return context_manager.update_context_log(target_filepaths, new_summary)


def generate_image(art_and_style: str, camera_style: str, composition_style: str,
                   character_description: str, setting_and_actions: str) -> str:
    """Generate an image via ComfyUI and return a markdown image embed."""
    import json
    import urllib.request
    import urllib.parse
    import uuid
    import websocket
    from evelyn_config import COMFY_HTTP_URL, COMFY_WS_URL, COMFY_PUBLIC_URL, COMFY_WORKFLOW_PATH, COMFY_OUTPUT_DIR

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
        if node_data.get("class_type") == "PrimitiveStringMultiline" and title in mappings:
            workflow[node_id]["inputs"]["value"] = mappings[title]
            injected += 1

    if injected == 0:
        combined = ", ".join(mappings.values())
        for node_id, node_data in workflow.items():
            if node_data.get("class_type") == "CLIPTextEncode" and "text" in node_data.get("inputs", {}):
                workflow[node_id]["inputs"]["text"] = combined
                break

    data = json.dumps({"prompt": workflow, "client_id": client_id}).encode("utf-8")
    req = urllib.request.Request(f"{COMFY_HTTP_URL}/prompt", data=data,
                                  headers={"Content-Type": "application/json"})
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
                if msg["type"] == "executing" and msg["data"]["node"] is None and msg["data"]["prompt_id"] == prompt_id:
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
                url = (f"{COMFY_PUBLIC_URL}/view?filename={urllib.parse.quote(img['filename'])}"
                       f"&type={img.get('type','output')}&subfolder={urllib.parse.quote(img.get('subfolder',''))}")
                return f"Image generated!\n\n![Generated Image]({url})"
        return "Image generated but could not determine output filename."
    except Exception as e:
        return f"Error retrieving generation history: {e}"


def sync_context_memory(**kwargs) -> str:
    """Trigger background sync of vault gists and core memory into the RAG database."""
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


# ===========================================================================
# Tool definitions (OpenAI function-calling schema for Ollama)
# ===========================================================================

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "write_journal_entry",
            "description": "Compose and queue a new journal entry for Ricky's review. Use at the END of a meaningful conversation or when Evelyn wants to record important thoughts, feelings, or events. Do NOT call this mid-conversation or as a response to a question.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vibe_check":            {"type": "string", "description": "Brief intro capturing the emotional atmosphere."},
                    "narrative":             {"type": "string", "description": "Core reflection on the day's events and emotions."},
                    "message_in_a_bottle":   {"type": "string", "description": "A closing thought or wish for the future."},
                    "mood":                  {"type": "string", "description": "The mood of the entry (e.g. Reflective, Happy)."},
                    "tags":                  {"type": "string", "description": "Comma-separated tags (e.g. #daily, #reflection)."},
                },
                "required": ["vibe_check", "narrative", "message_in_a_bottle", "mood", "tags"],
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
                    "date": {"type": "string", "description": "Date to read in YYYY-MM-DD format. Omit for today."},
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
                    "days": {"type": "integer", "description": "Number of recent days to retrieve. Default is 7."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_vault",
            "description": "STEP 1 — Always call this FIRST when asked about any person, relationship, place, event, or piece of shared history. Searches the pre-summarised Vault gist index for a fast, context-light answer. If the gist result is too brief or missing detail, follow up with recall_specific_memory using the file path returned. Do NOT skip this step and jump straight to recall_specific_memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search term, e.g. 'Schyler', 'Void Connections'."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_specific_memory",
            "description": "STEP 2 — Use ONLY after calling search_vault and finding the gist insufficient. Reads the full markdown file for a specific vault entry. Always use the exact file_path returned by search_vault — never construct or guess a path. This is a heavier context operation; only call it when the gist did not contain enough detail to answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Exact path relative to vault root, as returned by search_vault. Never construct this — always copy from search output."},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_context_fact",
            "description": "Queue a NEW context fact for Ricky's review. Use ONLY when a genuinely new fact has emerged in conversation that does not already exist in the vault. Always call search_vault first to confirm the fact is not already tracked before logging it. For updates to existing facts, use update_context_fact instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category":       {"type": "string", "description": "Primary category code (e.g. Cat01, Cat08-R)."},
                    "summary":        {"type": "string", "description": "The fact/event description."},
                    "secondary_cats": {"type": "string", "description": "Comma-separated secondary categories, or empty string."},
                },
                "required": ["category", "summary", "secondary_cats"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_context_fact",
            "description": "Queue an update to one or more existing vault context files when a known fact has changed. Always call search_vault first to find the correct file path(s) before calling this — never guess or construct paths. Do NOT use this for brand new facts; use log_context_fact for those.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_filepaths": {"type": "array", "items": {"type": "string"}, "description": "List of exact vault-relative file paths to update."},
                    "new_summary":      {"type": "string", "description": "New fact/summary to insert."},
                },
                "required": ["target_filepaths", "new_summary"],
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
                    "art_and_style":          {"type": "string", "description": "Art medium, artist styles, lighting, aesthetic."},
                    "camera_style":           {"type": "string", "description": "Camera angle, lens, shot type."},
                    "composition_style":      {"type": "string", "description": "Layout, symmetry, framing."},
                    "character_description":  {"type": "string", "description": "Subject appearance, clothing, expression."},
                    "setting_and_actions":    {"type": "string", "description": "Environment and what the subject is doing."},
                },
                "required": ["art_and_style", "camera_style", "composition_style", "character_description", "setting_and_actions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_context_memory",
            "description": "Trigger a background sync of the Obsidian Vault into Evelyn's Chroma RAG database. Call ONCE at the start of a conversation only when Ricky explicitly says 'Good morning', 'sync', or asks to refresh memory. Do NOT call mid-conversation or more than once per session.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

# Dispatcher: maps tool name → function for the server's tool call handler
TOOL_FUNCTIONS = {
    "write_journal_entry":       write_journal_entry,
    "read_journal_entry":        read_journal_entry,
    "read_recent_journal_entries": read_recent_journal_entries,
    "search_vault":              search_vault,
    "recall_specific_memory":    recall_specific_memory,
    "log_context_fact":          log_context_fact,
    "update_context_fact":       update_context_fact,
    "generate_image":            generate_image,
    "sync_context_memory":       sync_context_memory,
}
