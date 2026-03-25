"""
evelyn_config.py — Single source of truth for the Evelyn backend stack.

Edit this file to change any path, URL, or behaviour flag.
No restart required for DEBUG_LOGGING changes — the server reads it per-request.
"""

# =============================================================================
# Model
# =============================================================================
OLLAMA_URL = "http://localhost:11434"
MODEL_NAME = "magistral:24b"
NUM_CTX = 16384
THINK = True  # Pass think:true to Ollama for native reasoning tokens

# =============================================================================
# Paths
# =============================================================================
VAULT_BASE_DIR = r"G:\My Drive\Obsidian_Vault"
EVELYN_MEMORY_DIR = r"G:\My Drive\Obsidian_Vault\Evelyn"
PHYSICAL_DESC_FILE = r"G:\My Drive\Obsidian_Vault\Notes\Prompt Lab\Physical Descriptions\Physical Description - Evelyn.md"
VAULT_MAP_FILE = r"C:\Projects\LocalAI\Vault_Map\vault_map_data.json"
VAULT_SYNC_STATE = r"C:\Projects\LocalAI\Evelyn\tools\vault_sync_state.json"
GIST_SYNC_STATE = r"C:\Projects\LocalAI\Evelyn\tools\gist_sync_state.json"
CHROMA_DB_PATH = r"C:\Projects\LocalAI\chroma_db"
CHAT_DB_PATH = r"C:\Projects\LocalAI\evelyn_chat.db"
PERSONA_DIR = r"C:\Projects\LocalAI\Evelyn\persona"
COMFY_WORKFLOW_PATH = r"C:\Projects\LocalAI\Evelyn\workflows\comfy_image_gen.json"
COMFY_OUTPUT_DIR = r"C:\Projects\ComfyUI\output"

# =============================================================================
# Chroma RAG
# =============================================================================
CHROMA_MEMORY_COLLECTION = "evelyn_memory"  # Full markdown files (journals, context)
CHROMA_GISTS_COLLECTION = "evelyn_gists"  # LLM-generated gist summaries
RAG_TOP_K = 5  # Number of chunks to inject per query
RAG_EXCLUDED_SUBDIRS = ["Archived", "Pending_Approvals"]

# =============================================================================
# Services
# =============================================================================
TTS_SERVER_URL = "http://localhost:5050"  # Qwen TTS server
COMFY_HTTP_URL = "http://127.0.0.1:8188"
COMFY_WS_URL = "127.0.0.1:8188"
COMFY_PUBLIC_URL = "http://ricky-pc.tail0e161b.ts.net:8188"

# =============================================================================
# Server
# =============================================================================
SERVER_PORT = 7860
BIND_HOST = "0.0.0.0"  # Reachable over Tailscale

# API key for thin auth — set via environment variable EVELYN_API_KEY
# or override the default below (not recommended for committed code)
import os

API_KEY = os.environ.get("EVELYN_API_KEY", "")

# Tailscale + local CORS origins
ALLOWED_ORIGINS = [
    "http://localhost:7860",
    "https://localhost:7860",
    "http://127.0.0.1:7860",
    "http://ricky-pc.tail0e161b.ts.net:7860",
    "https://ricky-pc.tail0e161b.ts.net:7860",
    "https://rickys-lenovo-tab-k-11.tail0e161b.ts.net:7860",
    "https://rickys-pixel-9-pro.tail0e161b.ts.net:7860",
]

# =============================================================================
# Debug / Logging
# =============================================================================
# Set to True to log full prompts, RAG chunks, tool calls, and thinking content.
# Reads per-request — no restart needed to toggle.
DEBUG_LOGGING = True
