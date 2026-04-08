"""
evelyn_config.py — Single source of truth for the Evelyn backend stack.

Edit this file to change any path, URL, or behaviour flag.
No restart required for DEBUG_LOGGING changes — the server reads it per-request.
"""

# =============================================================================
# Model
# =============================================================================
OLLAMA_URL = "http://localhost:11434"
# MODEL_NAME = "magistral:24b"
MODEL_NAME = "gemma4:26b"
NUM_CTX = 16384
THINK = True  # Pass think:true to Ollama for native reasoning tokens

# =============================================================================
# Model Parameters  (passed to Ollama's "options" dict on every request)
# Set a value to None to omit it and let Ollama use its built-in default.
# Docs: https://github.com/ollama/ollama/blob/main/docs/modelfile.md#valid-parameters-and-values
# =============================================================================

# Temperature — controls randomness. Lower = more deterministic.
# Range: 0.0–2.0  |  Ollama default: 0.8 | Tested: 1.1 (too random) | Tested: 0.9 (very cheerleader)
TEMPERATURE = 0.8

# Min-P — minimum probability relative to the top token. Trims the long tail
# of unlikely tokens cheaply, which noticeably speeds up generation.
# Range: 0.0–1.0  |  Ollama default: 0.0 (disabled)
MIN_P = 0.05

# Top-K — limits the pool to the K most likely tokens. 0 = disabled.
# Range: 0–∞      |  Ollama default: 40
TOP_K = 40

# Top-P (nucleus sampling) — cumulative probability cutoff.
# Range: 0.0–1.0  |  Ollama default: 0.9
TOP_P = 0.9

# Repeat penalty — discourages repeating tokens that appeared recently.
# Values > 1.0 penalize repeats; 1.0 = disabled.
# Range: 0.0–2.0  |  Ollama default: 1.1
REPEAT_PENALTY = 1.1

# Repeat last N — how many tokens back to scan for the repeat penalty.
# 0 = disabled, -1 = full context window.
# Range: 0–num_ctx  |  Ollama default: 64
REPEAT_LAST_N = 64

# Seed — set to a fixed integer for reproducible outputs, 0 for random.
# Range: 0–2^32     |  Ollama default: 0 (random)
SEED = 0

# Num predict — maximum tokens to generate. -1 = unlimited, -2 = fill context.
# Range: -2–∞       |  Ollama default: -1
NUM_PREDICT = -1

# =============================================================================
# History
# =============================================================================
# Maximum number of messages (not turns) sent to the model as conversation
# history.  15 turns × 2 = 30 messages.  All messages remain in the DB and
# are still returned by the /history UI endpoint — this only caps what Ollama
# sees.  A "thread break" marker further narrows this to the current thread.
MAX_HISTORY_MESSAGES = 20

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
RAG_EXCLUDED_SUBDIRS = [
    "Archived",
    "Pending_Approvals",
    "Evelyn's Context",
    "Evelyn's Journal",
]

# =============================================================================
# Services
# =============================================================================
TTS_SERVER_URL = "http://localhost:5050"  # Qwen TTS server
COMFY_HTTP_URL = "http://127.0.0.1:8188"
COMFY_WS_URL = "127.0.0.1:8188"
COMFY_PUBLIC_URL = "http://image-host.internal.net:8188"

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
    "https://127.0.0.1:7860",
    "http://image-host.internal.net:7860",
    "https://image-host.internal.net:7860",
    "http://client-tablet.internal.net:7860",
    "https://client-tablet.internal.net:7860",
    "http://client-phone.internal.net:7860",
    "https://client-phone.internal.net:7860",
]

# =============================================================================
# Debug / Logging
# =============================================================================
# Set to True to log full prompts, RAG chunks, tool calls, and thinking content.
# Reads per-request — no restart needed to toggle.
DEBUG_LOGGING = True
