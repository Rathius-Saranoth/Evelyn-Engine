"""
evelyn_config.py — Single source of truth for the Evelyn backend stack.

Edit this file to change any path, URL, or behaviour flag.
No restart required for DEBUG_LOGGING changes — the server reads it per-request.
"""

# =============================================================================
# Model
# =============================================================================
OLLAMA_URL = "http://localhost:11434"
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

# Stop sequences — generation halts immediately when any of these strings are
# produced. Primarily added to prevent the model from looping inside its own
# <think> block on self-prompting tokens like "(Send)." instead of emitting
# the final response. Set to an empty list [] to disable.
STOP_SEQUENCES = ["(Send).", "(Final).", "(Done).", "*Perfect."]

# =============================================================================
# History
# =============================================================================
# Maximum number of messages (not turns) sent to the model as conversation
# history.  15 turns × 2 = 30 messages.  All messages remain in the DB and
# are still returned by the /history UI endpoint — this only caps what Ollama
# sees.  A "thread break" marker further narrows this to the current thread.
MAX_HISTORY_MESSAGES = 20

# Maximum agentic tool-dispatch rounds per turn.
# Each round: model is offered tools; if it calls one, results are fed back and
# it gets another turn. Loop exits when the model produces no tool calls or this
# cap is hit, then the streaming response pass runs.
MAX_TOOL_ROUNDS = 5

# --- Context Summarizer ---
# Compresses older messages that have fallen out of the active history window
# into a lean summary block, injected into the system prompt each turn.
# Summarization runs asynchronously after each response — zero user-facing latency.

# Number of messages (beyond the active window) to include in summarization.
# These are the messages that just fell out of MAX_HISTORY_MESSAGES.
SUMMARY_WINDOW_SIZE = 50

# Maximum word count for the generated summary. Controls token budget.
# ~200 words ≈ ~270 tokens. Keep under 600 tokens to preserve response headroom.
SUMMARY_MAX_WORDS = 200

# How many of the active messages to overlap into the summary window,
# giving the summarizer context about what the model already "sees."
SUMMARY_OVERLAP = 4

# Model override for summarization. "default" = use MODEL_NAME (recommended).
# Set to a specific model name only if you want to experiment with a lighter model.
SUMMARY_MODEL_OVERRIDE = "default"

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

# Context entry paths
CONTEXT_ENTRIES_DIR = r"G:\My Drive\Obsidian_Vault\Evelyn\Evelyn's Context\Context Entries"
PENDING_DIR = r"G:\My Drive\Obsidian_Vault\Evelyn\Evelyn's Context\Context Entries\Pending"
EXTRACTED_DIR = r"G:\My Drive\Obsidian_Vault\Evelyn\Evelyn's Context\Context Entries\Extracted"

# Official category names — single source of truth for the consolidator and reviewer.
# Sourced from: Context Categories/Cat00 - Index.md
CATEGORY_NAMES: dict = {
    "Cat01-R": "Core Identity (Ricky)",
    "Cat01-E": "Core Identity (Evelyn)",
    "Cat02-R": "Core Values and Beliefs (Ricky)",
    "Cat02-E": "Core Values and Beliefs (Evelyn)",
    "Cat03-R": "Emotional Awareness (Ricky)",
    "Cat03-E": "Emotional Awareness (Evelyn)",
    "Cat04-R": "Communication Style (Ricky)",
    "Cat04-E": "Communication Style (Evelyn)",
    "Cat05-R": "Preferences & Interests (Ricky)",
    "Cat05-E": "Preferences & Interests (Evelyn)",
    "Cat06-R": "Relationship Dynamics (Ricky)",
    "Cat06-E": "Relationship Dynamics (Evelyn)",
    "Cat07-R": "Motivations and Aspirations (Ricky)",
    "Cat07-E": "Motivations and Aspirations (Evelyn)",
    "Cat08-R": "Shared Experiences & Daily Events (Ricky)",
    "Cat08-E": "Shared Experiences & Daily Events (Evelyn)",
    "Cat09-R": "Cognitive & Decision-Making Style (Ricky)",
    "Cat09-E": "Cognitive & Decision-Making Style (Evelyn)",
    "Cat10-R": "Humor, Creativity, and Play (Ricky)",
    "Cat10-E": "Humor, Creativity, and Play (Evelyn)",
    "Cat11-R": "Factual References & Knowledge (Ricky)",
    "Cat11-E": "Factual References & Knowledge (Evelyn)",
    "Cat12-R": "Emotional States & Responses (Ricky)",
    "Cat12-E": "Emotional States & Responses (Evelyn)",
    "Cat13-R": "Goals & Future Planning (Ricky)",
    "Cat13-E": "Goals & Future Planning (Evelyn)",
    "Cat14-R": "Platform & Environment (Ricky)",
    "Cat14-E": "Platform & Environment (Evelyn)",
    "Cat15-R": "The Lexicon (Ricky)",
    "Cat15-E": "The Lexicon (Evelyn)",
    "Cat16-R": "Protocols & Routines (Ricky)",
    "Cat16-E": "Protocols & Routines (Evelyn)",
}


# =============================================================================
# Chroma RAG
# =============================================================================
CHROMA_MEMORY_COLLECTION = "evelyn_memory"  # Full markdown files (journals, context)
CHROMA_GISTS_COLLECTION = "evelyn_gists"  # LLM-generated gist summaries
RAG_TOP_K = 5  # Number of chunks to retrieve per query

# Cosine distance threshold for RAG injection (0.0 = identical, 1.0 = unrelated).
# Chunks with distance ABOVE this value are discarded before injection.
# If all chunks are filtered out, nothing is added to the system prompt for that turn.
# NOTE: This value is model-specific. Current: all-MiniLM-L6-v2 (0.55).
#       Previous: nomic-embed-text via Ollama (0.35). Recalibrate if embedding model changes.
RAG_DISTANCE_THRESHOLD = 0.55

RAG_EXCLUDED_SUBDIRS = [
    "Archived",
    "Pending_Approvals",
    "Evelyn's Context",
    "Evelyn's Journal",
]

# Priority score multipliers: documents tagged rag_priority=high/low have their
# cosine distance adjusted by these factors before threshold filtering.
# Lower multiplier = effectively closer = higher rank. All others unaffected.
RAG_PRIORITY_MULTIPLIERS = {
    "high":   0.75,  # Move 25% closer — boosted docs rise above equal competitors
    "normal": 1.0,   # No change
    "low":    1.25,  # Push slightly further — de-prioritised docs
}

# Max chunks to guaranteed-inject per pinned document.
# Prevents a very long contact card from monopolising the context window.
RAG_PINNED_MAX_CHUNKS = 2

# --- RAG Query Reformulation ---
# Uses the already-loaded LLM to extract search keywords from conversational
# messages before embedding. Adds ~1-2s latency but dramatically improves
# retrieval accuracy for casual/conversational messages.
RAG_REFORMULATE_ENABLED = True    # Master switch — set False to bypass
RAG_REFORMULATE_MIN_WORDS = 4    # Skip reformulation for messages with fewer words
RAG_REFORMULATE_TIMEOUT = 10     # Seconds before falling back to raw message

# =============================================================================
# Entry Management
# =============================================================================
# When True, journal entries are written directly to the live vault directory
# (Evelyn's Journal) instead of the Pending_Approvals quarantine folder.
# Context entries always go to their in-vault Pending folder regardless.
JOURNAL_DIRECT_WRITE = True

# =============================================================================
# Fact Extraction
# =============================================================================
# Extends the context summarizer's async pass to extract structured personal
# facts from the conversation. Zero extra VRAM cost — same model, same options.
# Extracted files are written to EXTRACTED_DIR for manual review.

# Master switch — set False to disable without touching the summarizer.
FACT_EXTRACTION_ENABLED = True

# Minimum number of new messages required before the extractor runs.
# At 2 messages per turn (user + assistant), 6 means ~3 turns of conversation.
FACT_EXTRACTION_MIN_MESSAGES = 6

# Seconds of server inactivity before extraction is allowed to run.
# Shorter than CONSOLIDATION_IDLE_THRESHOLD — extraction is fast and low-risk.
FACT_EXTRACTION_IDLE_THRESHOLD = 300  # 5 minutes

# How often (seconds) the idle-time loop checks for extraction eligibility.
FACT_EXTRACTION_IDLE_CHECK_INTERVAL = 300  # 5 minutes

# Minimum seconds between extraction runs (cooldown).
FACT_EXTRACTION_COOLDOWN = 600  # 10 minutes

# Maximum number of DB messages to fetch and process per extraction run.
# Keep low to bound each Ollama call to a predictable size (~5-10s).
FACT_EXTRACTION_BATCH_SIZE = 20

# Per-run Ollama call timeout (seconds).
FACT_EXTRACTION_TIMEOUT = 90

# Starting DB message ID for the high-water mark.
# 0 = process all history on first run (default).
# Set to the current max message ID to skip all history and only extract
# messages from this point forward (useful after bulk imports or resets).
FACT_EXTRACTION_START_ID = 0

# Model override for extraction. "default" = use MODEL_NAME (recommended).
# Set to a specific model name only to use a different model for extraction.
# Independent from SUMMARY_MODEL_OVERRIDE — the two tasks can be configured separately.
FACT_EXTRACTION_MODEL_OVERRIDE = "default"

# =============================================================================
# Idle-Time Consolidation
# =============================================================================
# Scans live context entries during server idle time to find duplicates,
# contradictions, and miscategorized facts. Uses think=True for nuanced
# semantic reasoning. Produces proposal files in PENDING_DIR for review.
# Nothing is auto-applied to the live vault.

# Master switch.
CONSOLIDATION_ENABLED = True

# When True, also scan EX_*.md files from the Extracted/ staging folder.
# Useful while the live CE_ vault is sparse — finds duplicate auto-extracted
# facts before they are promoted. Set False to limit scope to live CE_ entries only.
CONSOLIDATION_INCLUDE_EXTRACTED = True

# True  — Preserve fact evolution in merged summaries
#         (e.g., "Previously disliked apples [2020]; now likes them [2022].")
# False — Overwrite: keep only the most recent fact, discard older versions.
CONSOLIDATION_KEEP_HISTORY = True

# Seconds of server inactivity before consolidation is allowed to run.
# Default: 15 minutes (900s) so it never interrupts active conversations.
CONSOLIDATION_IDLE_THRESHOLD = 60  # 1 minute (for testing)

# How often (seconds) the idle-time loop checks for inactivity.
# Default: every 5 minutes. Keep low enough to catch idle windows but not
# so low that the check itself creates noticeable overhead.
CONSOLIDATION_IDLE_CHECK_INTERVAL = 300  # 5 minutes

# Minimum seconds between consolidation runs. Prevents back-to-back passes.
# Default: 5 minutes. The consolidator tracks its own last-run timestamp.
CONSOLIDATION_COOLDOWN = 300 # 5 minutes

# Maximum number of conflict clusters to process per run.
# Each cluster = one LLM call (detect) + one LLM call (merge). Keep low
# to bound the idle-time cost to a predictable budget.
CONSOLIDATION_BATCH_SIZE = 10

# Maximum number of category groups to run the detection LLM call against.
# Each group = one LLM call. BATCH_SIZE caps proposals; this caps detections.
# With 30 groups and think=True at ~45s each, limit to avoid monopolizing Ollama.
# Groups are scanned in category order (Cat01 first); adjust to taste.
CONSOLIDATION_GROUP_SCAN_LIMIT = 8

# Maximum records shown per group in the detection prompt.
# Newest-first; older entries are omitted with a count note.
# Keeps prompts focused and KV cache lean. With the anchor-based scan,
# all entries are still visited over multiple passes.
CONSOLIDATION_MAX_RECORDS_PER_GROUP = 15

# Per-cluster LLM call timeout (seconds). Consolidation uses think=True
# for proposal generation — allow generous headroom for reasoning traces.
# Detection calls use think=False and complete well under this limit.
CONSOLIDATION_TIMEOUT = 180

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

# Set to True to print the full text of each tool result to the console.
# Reads per-request — no restart needed to toggle.
# Useful when inspecting search_vault, web_search, recall_specific_memory, etc.
DEBUG_TOOL_FULL = False
