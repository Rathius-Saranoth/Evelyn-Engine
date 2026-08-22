# evelyn_config.py
# date created: 2026-03-23 15:37:14
# date modified: 2026-08-19 18:42:06
# tags: #config, #constants, #globals, #environment, #settings

"""
evelyn_config.py — Single source of truth for the Evelyn backend stack.

Edit this file to change any path, URL, or behaviour flag.
No restart required for DEBUG_LOGGING changes — the server reads it per-request.
"""

import os
import time

# User Timezone (America/Chicago for Central Time)
USER_TIMEZONE = "America/Chicago"
os.environ["TZ"] = USER_TIMEZONE
if hasattr(time, "tzset"):
    time.tzset()

# HuggingFace & Transformers Offline Mode (loads from local cache without web checks)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# =============================================================================
# Identity Configuration
# =============================================================================
# These define WHO the assistant and user are. The engine uses these values
# in prompts, memory taxonomy, UI labels, and persona file lookups.
# Change these to personalize your instance.

ASSISTANT_NAME = "Evelyn"    # The AI companion's name
USER_NAME = "Ricky"          # The human operator's name

# Subject codes used in memory taxonomy (Cat01-U, Cat01-A, etc.)
# These are abstract identifiers — they map to USER_NAME / ASSISTANT_NAME
# in display contexts.
SUBJECT_CODE_USER = "U"       # Migrated from "R" (User)
SUBJECT_CODE_ASSISTANT = "A"  # Migrated from "E" (Assistant)

# Persona document basenames — dynamically named from identity config.
PERSONA_FILE_ASSISTANT = f"{ASSISTANT_NAME}_Narrative_Persona.md"
PERSONA_FILE_USER = f"{USER_NAME}_Narrative_Profile.md"
PERSONA_FILE_DIRECTIVES = "System_Directives.md"
PERSONA_FILES = [PERSONA_FILE_ASSISTANT, PERSONA_FILE_USER, PERSONA_FILE_DIRECTIVES]


# =============================================================================
# Model
# =============================================================================
OLLAMA_URL = "http://localhost:11434"
MODEL_NAME = "gemma4:12b"
NUM_CTX = 32768

# Thinking effort for the final streaming response (Evelyn's visible reply).
# Overridden per-message by heuristic classifier, model self-election, tool
# escalation, or UI chip (in ascending priority).
# Options: False (disabled), "low", "medium", "high", "max"
#   "low"    — brief sanity-check, fast  (casual sign-offs, simple acks)
#   "medium" — standard conversational reasoning  ← DEFAULT
#   "high"   — emotional depth, multi-step planning, complex questions
#   "max"    — deep problem solving, research synthesis (use sparingly)
THINK = "medium"

# =============================================================================
# Model Parameters  (passed to Ollama's "options" dict on every request)
# Set a value to None to omit it and let Ollama use its built-in default.
# Docs: https://github.com/ollama/ollama/blob/main/docs/modelfile.md#valid-parameters-and-values
# =============================================================================

# Temperature — controls randomness. Lower = more deterministic.
# Range: 0.0–2.0  |  Ollama default: 0.8 | Tested: 1.1 (too random) | Tested: 0.9 (very cheerleader)
# Recommended Gemma 4 default: 1.0 (Old value: 0.8)
TEMPERATURE = 1.0

# Min-P — minimum probability relative to the top token. Trims the long tail
# of unlikely tokens cheaply, which noticeably speeds up generation.
# Range: 0.0–1.0  |  Ollama default: 0.0 (disabled)
MIN_P = 0.05

# Top-K — limits the pool to the K most likely tokens. 0 = disabled.
# Recommended Gemma 4 default: 64 (Old value: 40)
# Range: 0–∞      |  Ollama default: 40
TOP_K = 64

# Top-P (nucleus sampling) — cumulative probability cutoff.
# Recommended Gemma 4 default: 0.95 (Old value: 0.9)
# Range: 0.0–1.0  |  Ollama default: 0.9
TOP_P = 0.95

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
NUM_PREDICT = 8192

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
# Expanded to 40 for Gemma 4 12B's 32K context (Old value: 20)
MAX_HISTORY_MESSAGES = 40

# Maximum agentic tool-dispatch rounds per turn.
# Each round: model is offered tools; if it calls one, results are fed back and
# it gets another turn. Loop exits when the model produces no tool calls or this
# cap is hit, then the streaming response pass runs.
MAX_TOOL_ROUNDS = 5

# Thinking effort for ALL tool-loop routing rounds (tool detection + result
# evaluation). These rounds make binary routing decisions only — "low" is
# appropriate and keeps latency low. Set to False to disable entirely.
THINK_TOOL_LOOP = "low"

# Token budget for each tool-loop reasoning round. Needs sufficient headroom
# for Gemma 4 native thinking tokens plus tool call generation.
TOOL_LOOP_NUM_PREDICT = 2048

# When True, intermediate thinking from each tool-loop round is forwarded to
# the client as thinking SSE events. Useful for seeing Evelyn's decision chain
# in the UI. When False (default), reasoning is internal only and only the
# final response's thinking block is shown.
SHOW_TOOL_LOOP_THINKING = True

# When True, Evelyn may self-elect her response effort during Tool Round 0 by
# including {"requested_effort":"X"} in her output. This overrides the heuristic
# classifier but not a UI chip override. Set to False to disable self-election.
THINK_SELF_ELECT = True

# --- Context Summarizer (DEPRECATED & DISABLED) ---
# Context summarizer has been removed to eliminate prompt clutter and temporal
# hallucinations in journal writing. Active conversation history (MAX_HISTORY_MESSAGES=40)
# + SQLite context_entries + Chroma RAG handle context retention.
SUMMARY_WINDOW_SIZE = 20
SUMMARY_MAX_WORDS = 200
SUMMARY_OVERLAP = 4
SUMMARY_MODEL_OVERRIDE = "default"

# =============================================================================
# Paths
# =============================================================================
BASE_DIR = r"/home/rathius/evelyn"
TOOLS_DIR = r"/home/rathius/evelyn/Evelyn/tools"
VAULT_BASE_DIR = r"/home/rathius/obsidian_vault" # [[Obsidian_Vault]]
PERSONA_DIR = r"/home/rathius/evelyn/Evelyn/persona" # [[persona]]
VAULT_DB_PATH = r"/home/rathius/evelyn/data/evelyn_vault.db" # [[evelyn_vault.db]]
VAULT_SYNC_STATE = r"/home/rathius/evelyn/data/vault_sync_state.json" # [[vault_sync_state.json]]
GIST_SYNC_STATE = r"/home/rathius/evelyn/data/gist_sync_state.json" # [[gist_sync_state.json]]
CHROMA_DB_PATH = r"/home/rathius/evelyn/data/chroma_db" # [[chroma_db]]
CHAT_DB_PATH = r"/home/rathius/evelyn/data/evelyn_chat.db" # [[evelyn_chat.db]]
MEMORY_DB_PATH = r"/home/rathius/evelyn/data/evelyn_memory.db" # [[evelyn_memory.db]]
MEDIA_DB_PATH = r"/home/rathius/evelyn/data/evelyn_media.db" # [[evelyn_media.db]]
ATTACHMENTS_DIR = r"/home/rathius/evelyn/data/attachments" # [[attachments]]
GCAL_CREDENTIALS_PATH = r"/home/rathius/evelyn/data/gcal_credentials.json"
GCAL_TOKEN_PATH = r"/home/rathius/evelyn/data/gcal_token.json"
GDRIVE_CREDENTIALS_PATH = r"/home/rathius/evelyn/data/gdrive_credentials.json"
GDRIVE_TOKEN_PATH = r"/home/rathius/evelyn/data/gdrive_token.json"

# =============================================================================
# Vault Write Paths & Access Control
# =============================================================================
# Root directory where the assistant writes vault content (journals, context,
# research reports). Sub-paths are derived by convention but individually
# overridable. The vault itself is personal — the engine doesn't dictate
# overall structure, only where IT writes.
ASSISTANT_WRITE_DIR = os.path.join(VAULT_BASE_DIR, ASSISTANT_NAME)

# Convention-based sub-paths (override individually if your vault is shaped differently)
JOURNAL_DIR = os.path.join(ASSISTANT_WRITE_DIR, f"{ASSISTANT_NAME}'s Journal")
CONTEXT_DIR = os.path.join(ASSISTANT_WRITE_DIR, f"{ASSISTANT_NAME}'s Context")
RESEARCH_VAULT_DIR = os.path.join(ASSISTANT_WRITE_DIR, "Research")
PENDING_DIR = os.path.join(ASSISTANT_WRITE_DIR, "Pending_Approvals")

# Directories the engine should NOT read from (excluded from RAG indexing,
# vault search, and context ingestion). Paths are relative to VAULT_BASE_DIR.
VAULT_READ_IGNORE = [
    "Archived",
    "Pending_Approvals",
    f"{ASSISTANT_NAME}'s Context/Context Entries/Extracted",
    f"{ASSISTANT_NAME}'s Context/Context Entries/Pending",
    ".obsidian",
    # Add personal directories the assistant should never read:
    # "Private",
    # "Work/Confidential",
]

# Directories the engine should NOT write to. Prevents accidental file
# creation outside the assistant's designated write paths.
# Paths are relative to VAULT_BASE_DIR.
VAULT_WRITE_IGNORE = [
    "Archived",
    ".obsidian",
    # Add directories the assistant should never modify:
    # "Templates",
    # "Reference",
]

# Health Connect & Oura Ring Configuration
HEALTH_DATA_DIR = r"/home/rathius/evelyn/data/health"
HEALTH_DB_PATH = r"/home/rathius/evelyn/data/health/health_connect.db"
HEALTH_SYNC_STATE_PATH = r"/home/rathius/evelyn/data/health_sync_state.json"
OURA_TOKEN_PATH = r"/home/rathius/evelyn/data/oura_token.json"

# SQLite PRAGMAs — tuned per hardware tier.
# Power Tier  (64GB+ RAM, server):  mmap=2GB,  cache=64MB
# Standard    (16-32GB RAM, desktop): mmap=512MB, cache=32MB
# Light Tier  (8-16GB RAM, laptop):  mmap=256MB, cache=16MB
SQLITE_PRAGMAS = [
    "PRAGMA journal_mode=WAL;",       # Enable WAL for concurrent non-blocking reads/writes
    "PRAGMA synchronous=NORMAL;",     # Optimal disk flush balance under WAL mode
    "PRAGMA mmap_size=2147483648;",   # 2 GB Memory-mapped I/O (mmap) for zero-copy file reads
    "PRAGMA cache_size=-64000;",      # 64 MB DRAM page cache per connection
    "PRAGMA temp_store=MEMORY;",      # Store intermediate result sets in DRAM
]

# Official category names — dynamically generated for consolidator, reviewer, and fact extractor.
# Sourced from: Context Categories/Cat00 - Index.md
_CATEGORY_LABELS = [
    "Core Identity",
    "Core Values and Beliefs",
    "Emotional Awareness",
    "Communication Style",
    "Preferences & Interests",
    "Relationship Dynamics",
    "Motivations and Aspirations",
    "Shared Experiences & Daily Events",
    "Cognitive & Decision-Making Style",
    "Humor, Creativity, and Play",
    "Factual References & Knowledge",
    "Emotional States & Responses",
    "Goals & Future Planning",
    "Platform & Environment",
    "The Lexicon",
    "Protocols & Routines",
]

CATEGORY_NAMES: dict = {}
for i, label in enumerate(_CATEGORY_LABELS, start=1):
    cat_num = f"Cat{i:02d}"
    CATEGORY_NAMES[f"{cat_num}-{SUBJECT_CODE_USER}"] = f"{label} ({USER_NAME})"
    CATEGORY_NAMES[f"{cat_num}-{SUBJECT_CODE_ASSISTANT}"] = f"{label} ({ASSISTANT_NAME})"


# =============================================================================
# Chroma RAG
# =============================================================================
CHROMA_MEMORY_COLLECTION = "evelyn_memory"  # Full-text vault notes & memory chunks
# Increased to 8 for Gemma 4 12B's 32K context (Old value: 5)
RAG_TOP_K = 8  # Number of chunks to retrieve per query

# Cosine distance threshold for RAG injection (0.0 = identical, 1.0 = unrelated).
# Chunks with distance ABOVE this value are discarded before injection.
# If all chunks are filtered out, nothing is added to the system prompt for that turn.
# NOTE: Model-specific threshold. BAAI/bge-large-en-v1.5 (1024-dim): 0.45 cosine distance.
#       Previous: all-MiniLM-L6-v2 (0.55), nomic-embed-text (0.35).
RAG_DISTANCE_THRESHOLD = 0.45

# RAG exclusions — derived from VAULT_READ_IGNORE plus any RAG-specific additions.
RAG_EXCLUDED_SUBDIRS = VAULT_READ_IGNORE + [
    f"{ASSISTANT_NAME}'s Journal",
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
FACT_EXTRACTION_TIMEOUT = 180

# Maximum number of sequential batches allowed per continuous idle session.
# Caps worst-case extraction time to ~N × (2 × timeout) so a large backlog
# can't consume an entire overnight idle period. Resets when a new chat
# request arrives (i.e., when cancel_pending_extraction() is called).
# 5 batches × 20 msgs × ~5-8 min/batch ≈ 25-40 minutes maximum.
FACT_EXTRACTION_MAX_BATCHES_PER_SESSION = 5

# Starting DB message ID for the high-water mark.
# 0 = process all history on first run (default).
# Set to the current max message ID to skip all history and only extract
# messages from this point forward (useful after bulk imports or resets).
FACT_EXTRACTION_START_ID = 0

# Model override for extraction. "default" = use MODEL_NAME (recommended).
# Set to a specific model name only to use a different model for extraction.
# Independent from SUMMARY_MODEL_OVERRIDE — the two tasks can be configured separately.
FACT_EXTRACTION_MODEL_OVERRIDE = "default"

# Vector RAG & Semantic Taxonomy Retrieval for Fact Extraction
# Max candidate master taxonomy tags and domain branches to retrieve
FACT_EXTRACTION_TOP_K_TAXONOMY = 30
# Max existing memory chunks / fact clusters to retrieve for context alignment
FACT_EXTRACTION_TOP_K_FACTS = 6
# Cosine distance threshold for novel domain detection (0.0 = identical, 1.0 = orthogonal)
# Distances >= this threshold prompt the model to mint clean domain-level hierarchies.
FACT_EXTRACTION_NOVELTY_THRESHOLD = 0.55


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
CONSOLIDATION_IDLE_THRESHOLD = 900  # 15 minutes

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

# Automatic Fissure/Split detection for bloated compound context entries.
# Flags single context entries exceeding this word count or containing multiple
# distinct domain predicates for atomic decomposition.
CONSOLIDATION_SPLIT_ENABLED = True
CONSOLIDATION_SPLIT_WORD_THRESHOLD = 35

# Per-cluster LLM call timeout (seconds). Consolidation uses think=True
# for proposal generation — allow generous headroom for reasoning traces.
# Detection calls use think=False and complete well under this limit.
CONSOLIDATION_TIMEOUT = 180

# =============================================================================
# Deep Research
# =============================================================================
RESEARCH_ENABLED = True
RESEARCH_DATA_DIR = r"/home/rathius/evelyn/data/research"

# Vault directory for finished reports.
# Uses RESEARCH_VAULT_DIR defined in Vault Write Paths above.

# Maximum sub-questions the planner can generate per research task.
RESEARCH_MAX_SUB_QUESTIONS = 6

# Maximum characters to extract from a single web page.
# Longer pages are truncated. 100000 chars ≈ ~25000 tokens (enough for full academic papers).
RESEARCH_MAX_PAGE_CHARS = 100000

# --- Confidence-Driven Termination ---

# Minimum confidence score (0-100) before a sub-question is considered answered.
# The evaluate step returns a confidence score; research continues on a
# sub-question until this threshold is met or per-SQ depth is exhausted.
# Scope presets override this: quick=70, standard=80, deep=85.
RESEARCH_CONFIDENCE_THRESHOLD = 80

# --- Necessity Pre-Filter ---
# Before planning any sub-questions, checks whether the query can already be
# answered from recent conversation history or existing live memory facts,
# without launching a full research task. Gated behind a deterministic
# time-sensitivity keyword check (never skips research for anything that
# could have changed recently, e.g. "current president", "latest version").
# On success the task directory is deleted entirely -- no trace, no report,
# no vault write ever exists on disk.

# Master switch. Set False to always plan and run full research.
RESEARCH_NECESSITY_PREFILTER_ENABLED = True

# Minimum confidence (0-100) required to skip research entirely. Deliberately
# higher than RESEARCH_CONFIDENCE_THRESHOLD (and the per-scope 70/80/85
# presets) since being wrong here discards the ENTIRE task with zero
# external corroboration, not just one sub-question.
RESEARCH_NECESSITY_CONFIDENCE_THRESHOLD = 90

# --- Safety Nets (emergency brakes — should rarely trigger) ---
# NOTE: These are fallback defaults for tasks created before the 2026-06-21
# scope-budget refactor. New tasks carry their own per-scope budgets in
# state.json (max_orchestrator_turns and wall_clock_timeout) set at creation
# time, so changing these values here does NOT affect in-flight tasks.

# Maximum total high-level orchestrator turns per task. Catch infinite state loops.
# Per-scope budgets in state.json now govern this: quick=30, standard=80, deep=200.
RESEARCH_MAX_ORCHESTRATOR_TURNS = 80  # Fallback only

# Wall-clock timeout (seconds). Task is force-synthesized after this duration.
# Per-scope budgets: quick=1800s, standard=7200s, deep=28800s (8 hours).
RESEARCH_WALL_CLOCK_TIMEOUT = 7200  # Fallback only (standard scope equivalent)

# --- Operational ---

# Seconds between research steps during idle-time execution.
# Gives Ollama breathing room between calls.
RESEARCH_STEP_COOLDOWN = 5

# Synthesis notes compression threshold (characters).
# Before synthesis, each sub-question's notes are checked against this limit.
# Notes exceeding it are summarized by the LLM before being injected into the
# synthesis prompt, preventing context-window saturation on deep 8-SQ runs.
# 12000 chars ≈ ~3000 tokens — roughly a full article of dense evidence.
# Set to 0 to disable compression (always pass raw notes).
RESEARCH_NOTES_SUMMARY_THRESHOLD = 12000

# Idle-time trigger: seconds of inactivity before research can start a new queued task.
# Wave 3 idle trigger (30m / 1800s): Staggered to prevent NUMA Node 0 CPU contention with extraction (5m) and consolidation (15m).
# Note: auto-RESUME of a paused task requires only 300s (5m) idle — see evelyn_server.py.
RESEARCH_IDLE_THRESHOLD = 1800  # 30 minutes (Wave 3)

# Model override for research calls. "default" = use MODEL_NAME.
RESEARCH_MODEL_OVERRIDE = "default"

# Allow Evelyn to self-initiate research during idle time.
# When True, the idle-time loop can generate research topics from
# recent conversations or vault gaps and queue them automatically.
RESEARCH_SELF_INITIATE = True

# Maximum queued self-initiated topics. Prevents runaway queue growth.
RESEARCH_MAX_QUEUE_SIZE = 5

# Active-hours window for research task execution (local time, 24-hour clock).
# Research tasks will only START or RESUME within this window.
# Any task already mid-step at window close will finish that step cleanly, then
# pause at the step boundary — no hard kills. Set both to 0 to disable windowing
# (research runs any hour).
# Intention: reserve overnight hours for evolution/consolidation tasks, mimicking
# a human sleep/dream cycle where memory consolidation happens during rest.
RESEARCH_ACTIVE_HOURS_START = 6   # 06:00 local time
RESEARCH_ACTIVE_HOURS_END   = 21  # 21:00 local time

# =============================================================================
# Services
# =============================================================================
TTS_SERVER_URL = "http://localhost:5050"  # Chatterbox TTS server
IMAGE_SERVER_URL = "http://ricky-pc.tail0e161b.ts.net:5055"  # FLUX.1 [schnell] Image server (or http://localhost:5055)
IMAGE_OUTPUT_DIR = r"/home/rathius/evelyn/services/image/output"

# =============================================================================
# Server
# =============================================================================
SERVER_PORT = 7860
BIND_HOST = "0.0.0.0"  # Reachable over Tailscale / LAN

# API key for thin auth — set via environment variable EVELYN_API_KEY
# or override the default below (not recommended for committed code)
API_KEY = os.environ.get("EVELYN_API_KEY", "")

# CORS origins — defaults to localhost. Add your Tailscale/LAN hostnames below.
# Example Tailscale: "https://my-server.tail12345.ts.net:7860"
# Example LAN:       "https://192.168.1.100:7860"
ALLOWED_ORIGINS = [
    f"http://localhost:{SERVER_PORT}",
    f"https://localhost:{SERVER_PORT}",
    f"http://127.0.0.1:{SERVER_PORT}",
    f"https://127.0.0.1:{SERVER_PORT}",
    # --- Tailscale & LAN Origins ---
    "http://sanctum.tail0e161b.ts.net:7860",
    "https://sanctum.tail0e161b.ts.net:7860",
    "http://192.168.1.187:7860",
    "https://192.168.1.187:7860",
    "http://ricky-pc.tail0e161b.ts.net:7860",
    "https://ricky-pc.tail0e161b.ts.net:7860",
    "http://rickys-lenovo-tab-k-11.tail0e161b.ts.net:7860",
    "https://rickys-lenovo-tab-k-11.tail0e161b.ts.net:7860",
    "http://rickys-pixel-9-pro.tail0e161b.ts.net:7860",
    "https://rickys-pixel-9-pro.tail0e161b.ts.net:7860",
]

# =============================================================================
# Debug / Logging
# =============================================================================
# Set to True to log full prompts, RAG chunks, tool calls, and thinking content.
# Reads per-request — no restart needed to toggle.
DEBUG_LOGGING = False

# Set to True to print the full text of each tool result to the console.
# Reads per-request — no restart needed to toggle.
# Useful when inspecting search_vault, web_search, recall_specific_memory, etc.
DEBUG_TOOL_FULL = False

# =============================================================================
# Profile Auto-Evolution (Hermes Tier 3 #12)
# =============================================================================
# Background task that proposes updates to persona files based on accumulated
# context entries. All proposals go through human review in dev.html.

PROFILE_EVOLUTION_ENABLED = True

# Minimum seconds between evolution runs (per document).
# Default: 12 hours. The evolver checks all three documents per run.
PROFILE_EVOLUTION_COOLDOWN = 43200  # 12 hours

# Minimum number of NEW context entries (since last run) before evolution
# is triggered for a given document. Prevents churning on sparse data.
PROFILE_EVOLUTION_MIN_ENTRIES = 5

# Idle threshold — Wave 5 idle trigger (60 minutes / 3600s).
# Staggered after Tag Librarian (45m) to complete non-overlapping idle task waves on NUMA Node 0.
PROFILE_EVOLUTION_IDLE_THRESHOLD = 3600  # 60 minutes (1 hour)

# Model override. "default" = use MODEL_NAME.
PROFILE_EVOLUTION_MODEL_OVERRIDE = "default"

# Maximum context entries fed to the model per evolution pass.
# When the qualifying entry count exceeds this, _evolve_document() processes
# them in successive batches, each pass refining the previous output.
# This prevents context-window saturation on the first run (which sees all
# accumulated history). Entries are sorted oldest-first so later passes
# layer on top of earlier refinements. 40 entries ≈ ~6000 chars of evidence.
PROFILE_EVOLUTION_BATCH_SIZE = 40

# Target word limits for persona/profile evolution documents.
# Keeping these compact prevents prompt dilution and attention decay in long chats.
PROFILE_EVOLUTION_LIMITS = {
    PERSONA_FILE_ASSISTANT: 600,
    PERSONA_FILE_USER: 600,
    PERSONA_FILE_DIRECTIVES: 500,
}


# =============================================================================
# Code & Terminal Agency (Hermes Tier 3 #9)
# =============================================================================
# Gives Evelyn scoped terminal access within allowed directories.
TERMINAL_ENABLED = True

TERMINAL_ALLOWED_PATHS = [
    "/home/rathius/evelyn",
    "/tmp",
    "/home/rathius/obsidian_vault",
]

TERMINAL_DEFAULT_TIMEOUT = 30      # seconds
TERMINAL_MAX_TIMEOUT = 300         # 5 minutes max
TERMINAL_MAX_OUTPUT_CHARS = 10000  # Truncate beyond this


# =============================================================================
# Tag Librarian Configuration (Incremental Vault Tag Maintenance)
# =============================================================================
TAG_LIBRARIAN_ENABLED = True
# Wave 4 idle trigger (45 minutes / 2700s). Staggered after deep research (30m).
TAG_LIBRARIAN_IDLE_THRESHOLD = 2700  # 45 minutes idle (Wave 4)
TAG_LIBRARIAN_BATCH_SIZE = 1         # Process 1 document per idle trigger

# Protected tag regexes (never modified, removed, or normalized)
# CY-YYYY/MM/DD is strictly protected.
TAG_LIBRARIAN_EXCLUSIONS = [
    r"^CY-\d{4}/\d{2}/\d{2}$",  # Calendar year/month/day tags (e.g. CY-2026/08/02)
    r"^status/",                 # System status tags
    r"^kanban",                  # Kanban board tags
]

# Tag formatting standards
TAG_LIBRARIAN_FORMAT_RULES = {
    "default_multi_word": "hyphen",   # "acceptable-use", "habit-tracking"
    "entity_multi_word": "underscore", # "John_Smith", "Evelyn_Engine"
    "lowercase_subpaths": True,       # "tech/python/fastapi"
}

# Chroma Vector Tag Taxonomy Settings (Tag RAG)
CHROMA_TAG_COLLECTION = "evelyn_tag_taxonomy"
CHROMA_MEDIA_COLLECTION = "evelyn_media"
TAG_LIBRARIAN_TOP_K_TAGS = 35           # Max semantically matched master tags to retrieve
TAG_NOVELTY_DISTANCE_THRESHOLD = 0.55   # Cosine distance above which a note domain is deemed novel



