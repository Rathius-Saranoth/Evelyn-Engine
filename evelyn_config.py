# evelyn_config.py
# date created: 2026-03-23 15:37:14
# date modified: 2026-08-08 07:20:00
# tags: #config, #constants, #globals, #environment, #settings

"""
evelyn_config.py — Single source of truth for the Evelyn backend stack.

Edit this file to change any path, URL, or behaviour flag.
No restart required for DEBUG_LOGGING changes — the server reads it per-request.
"""

# =============================================================================
# Model
# =============================================================================
OLLAMA_URL = "http://localhost:11434"
MODEL_NAME = "gemma4:12b"
NUM_CTX = 32768
THINK = True  # Pass think:true to Ollama for native reasoning tokens

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
NUM_PREDICT = 4096

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

# Whether to enable native reasoning (think=True) for tool-loop rounds.
# When True, the model reasons at each decision point — evaluating tool results
# and deciding whether the task is complete before calling the next tool.
# Costs additional latency per round (~30-60s) but enables proactive, multi-step
# agentic behavior. Set to False to revert to fast no-think routing.
THINK_TOOL_LOOP = True

# Token budget for each tool-loop reasoning round. Needs sufficient headroom
# for Gemma 4 native thinking tokens plus tool call generation.
TOOL_LOOP_NUM_PREDICT = 4096

# When True, intermediate thinking from each tool-loop round is forwarded to
# the client as thinking SSE events. Useful for seeing Evelyn's decision chain
# in the UI. When False (default), reasoning is internal only and only the
# final response's thinking block is shown.
SHOW_TOOL_LOOP_THINKING = True

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
VAULT_BASE_DIR = r"G:\My Drive\Obsidian_Vault" # [[Obsidian_Vault]]
EVELYN_MEMORY_DIR = r"G:\My Drive\Obsidian_Vault\Evelyn" # [[Obsidian_Vault\Evelyn]]
PHYSICAL_DESC_FILE = r"G:\My Drive\Obsidian_Vault\Notes\Prompt Lab\Physical Descriptions\Physical Description - Evelyn.md" # [[Physical Description - Evelyn.md]]
VAULT_DB_PATH = r"C:\Projects\LocalAI\data\evelyn_vault.db" # [[evelyn_vault.db]]
VAULT_SYNC_STATE = r"C:\Projects\LocalAI\data\vault_sync_state.json" # [[vault_sync_state.json]]
GIST_SYNC_STATE = r"C:\Projects\LocalAI\data\gist_sync_state.json" # [[gist_sync_state.json]]
CHROMA_DB_PATH = r"C:\Projects\LocalAI\data\chroma_db" # [[chroma_db]]
CHAT_DB_PATH = r"C:\Projects\LocalAI\data\evelyn_chat.db" # [[evelyn_chat.db]]
MEMORY_DB_PATH = r"C:\Projects\LocalAI\data\evelyn_memory.db" # [[evelyn_memory.db]]
PERSONA_DIR = r"C:\Projects\LocalAI\Evelyn\persona" # [[persona]]
GCAL_CREDENTIALS_PATH = r"C:\Projects\LocalAI\data\gcal_credentials.json"
GCAL_TOKEN_PATH = r"C:\Projects\LocalAI\data\gcal_token.json"



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
# Increased to 8 for Gemma 4 12B's 32K context (Old value: 5)
RAG_TOP_K = 8  # Number of chunks to retrieve per query

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

# Per-cluster LLM call timeout (seconds). Consolidation uses think=True
# for proposal generation — allow generous headroom for reasoning traces.
# Detection calls use think=False and complete well under this limit.
CONSOLIDATION_TIMEOUT = 180

# =============================================================================
# Deep Research
# =============================================================================
RESEARCH_ENABLED = True
RESEARCH_DATA_DIR = r"C:\Projects\LocalAI\data\research"

# Vault directory for finished reports.
RESEARCH_VAULT_DIR = r"G:\My Drive\Obsidian_Vault\Evelyn\Research"

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
# Must be LONGER than consolidation threshold to avoid conflicts.
# Reduced from 1800s (30m) to 900s (15m) — 2026-06-21 budget review.
# Note: auto-RESUME of a paused task requires only 300s (5m) idle — see evelyn_server.py.
RESEARCH_IDLE_THRESHOLD = 900  # 15 minutes

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
IMAGE_SERVER_URL = "http://localhost:5055"  # FLUX.1 [schnell] Image server
IMAGE_OUTPUT_DIR = r"C:\Projects\LocalAI\services\image\output"

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

# Idle threshold — same as deep memory refresh (45 minutes).
PROFILE_EVOLUTION_IDLE_THRESHOLD = 2700  # 45 minutes

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
    "Evelyn_Narrative_Persona.md": 600,
    "Ricky_Narrative_Profile.md": 600,
    "System_Directives.md": 500,
}


# =============================================================================
# Code & Terminal Agency (Hermes Tier 3 #9)
# =============================================================================
# Gives Evelyn scoped terminal access within allowed directories.
TERMINAL_ENABLED = True

TERMINAL_ALLOWED_PATHS = [
    r"C:\Projects\LocalAI",
    r"C:\Temp",
    r"G:\My Drive\Obsidian_Vault",
]

TERMINAL_DEFAULT_TIMEOUT = 30      # seconds
TERMINAL_MAX_TIMEOUT = 300         # 5 minutes max
TERMINAL_MAX_OUTPUT_CHARS = 10000  # Truncate beyond this


# =============================================================================
# Tag Librarian Configuration (Incremental Vault Tag Maintenance)
# =============================================================================
TAG_LIBRARIAN_ENABLED = True
TAG_LIBRARIAN_IDLE_THRESHOLD = 1800  # 30 minutes idle
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
    "entity_multi_word": "underscore", # "Ricky_Sekulich", "Evelyn_Engine"
    "lowercase_subpaths": True,       # "tech/python/fastapi"
}



