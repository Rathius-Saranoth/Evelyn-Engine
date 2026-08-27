---
title: CHANGELOG.md
date created: 2026-08-22 15:53:28
date modified: 2026-08-23 08:01:50
tags: changelog, versioning, history, release-notes, evelyn
---
# 📜 Changelog

> Navigation: [[README.md]] · [[ROADMAP.md]] · [[AGENTS.md]] · [[engine_architecture.md]]

All notable changes to the Evelyn Engine are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to **3-digit zero-padded Semantic Versioning** (`000.000.000`).

## [000.006.004] - 2026-08-27 — *Permanent Deletion Controls for Procedures & Triage Items*

### Added & Enhanced
- **Permanent Hard-Deletion Database Primitives (`Evelyn/tools/memory_db.py`)**:
  - Implemented `hard_delete_procedure(proc_id: int)` to permanently remove procedures and purge orphaned references from `procedure_split_queue` and `procedure_merge_queue`.
  - Implemented `delete_proposal(proposal_id: int)` to permanently remove pending or rejected triage proposals from SQLite.
  - Implemented `hard_delete_entry(entry_id: int)` to permanently delete context entries and unlink references from pending proposals.
- **Server Endpoints for Permanent Removal (`evelyn_server.py`)**:
  - Added `DELETE /api/procedures/{id}` and `POST /api/procedures/{id}/delete` endpoints.
  - Updated `POST /api/review/procedures/{id}/{action}` to handle permanent hard deletion when `action in ("delete", "hard_delete")`.
  - Updated `POST /api/review/proposals/{id}/{action}` to handle permanent proposal deletion when `action in ("delete", "hard_delete")`.
- **UI Permanent Delete & Remove Controls (`evelyn_ui/dev.html`)**:
  - Added permanent `🗑️ Delete` button on Procedure Management cards with confirmation modal, supporting hard deletion of old/outdated procedures.
  - Added permanent `🗑️ Remove` / `🗑️ Delete` buttons with confirmation dialogs to all Triage Queue cards (Procedures, Fact Splits, Merges, Recategorizations, and Profile Updates).
- **Test Suite Cleanups & Coverage (`Evelyn/tests/test_procedures_upgrade.py`)**:
  - Cleaned up 91 orphaned test procedure records from `data/evelyn_memory.db`.
  - Updated all procedure unit tests to use `hard_delete_procedure` teardown, preventing test runs from accumulating archived dummy rows in the active database.
  - Added `test_hard_deletion_primitives` covering `hard_delete_procedure`, `hard_delete_entry`, and `delete_proposal`.

---

## [000.006.003] - 2026-08-27 — *Procedure Management Search Focus Preservation & UI Fixes*

### Fixed & Enhanced
- **Procedures Management Search Filter (`evelyn_ui/dev.html`)**:
  - Decoupled the filter bar and search input controls from the procedures list DOM container.
  - Resolved input focus loss bug where typing in the procedure search box wiped and recreated the entire tab container on each keystroke.
  - Kept filter pill status counts and selection bar dynamically reactive while preserving continuous typing focus and caret position.

---

## [000.006.002] - 2026-08-27 — *Persistent FIFO Idle Task Queue & Cooperative Batch Catch-Up*

### Added & Enhanced
- **Persistent FIFO Idle Task Queue (`task_manager.py`, `evelyn_config.py`)**:
  - Implemented centralized FIFO task queue (`_idle_queue`) in `task_manager.py` with disk persistence (`data/evelyn_task_queue.json`) and crash recovery.
  - Interrupted running tasks upon reboot/server restart are automatically reconciled back to the front of the queue.
  - Implemented `IDLE_STARTUP_GRACE_PERIOD` (default 60s) to prevent deep research and background tasks from prematurely firing upon boot.
- **Cooperative Yield & Multi-Tool Batch Catch-Up (`fact_extractor.py`, `tag_librarian.py`, `evelyn_server.py`)**:
  - Uncapped fact extraction with `FACT_EXTRACTION_MAX_BATCHES_PER_SESSION = 0` (unlimited idle drain) and added `FACT_EXTRACTION_BACKLOG_DELAY = 5`s.
  - Refactored `fact_extractor.py` and `tag_librarian.py` to commit progress cursors to SQLite after each batch/item and check `task_manager.should_yield()`.
  - When peer tasks are queued, the active tool yields cleanly and re-enqueues at the tail of the line; when the queue is empty, it continues draining its backlog.
- **Zero-Delay Chat Preemption (`evelyn_server.py`, `task_manager.py`)**:
  - Interactive user chat immediately sets `task_manager.set_chat_preemption(True)` and triggers `cancel_all_idle_tasks()`, releasing 100% of compute and GPU inference power to conversational turns.
- **Centralized Idle Dispatcher (`evelyn_server.py`)**:
  - Replaced isolated task execution loops with a central `_idle_task_dispatcher_loop()`, while individual timer loops enqueue their intent via `task_manager.enqueue_idle_task()`.
- **Testing & Verification**:
  - Added `Evelyn/tests/test_idle_task_queue.py` verifying FIFO queuing, persistence, crash recovery, cooperative yield/re-enqueue, and preemption.
  - Full test suite passing at 144/144 tests.

---

## [000.006.001] - 2026-08-27 — *High-Resolution Granular Biometrics & Intraday Health Queries*

### Added & Enhanced
- **High-Resolution Intraday Biometrics Engine (`health_manager.py`, `oura_client.py`)**:
  - Implemented `get_granular_heart_rate(hours=N)` to fetch high-resolution live heart rate readings from Oura Cloud API v2 (`/v2/usercollection/heartrate`) with local Health Connect SQLite fallback.
  - Generates instant statistical summaries: `current_latest_bpm`, `min_bpm`, `max_bpm`, `avg_bpm`, total sample count, activity source breakdowns (`workout`, `awake`, `rest`, `sleep`), and downsampled 15-minute timeline chunks for clean model synthesis.
  - Implemented `get_intraday_activity(hours=N)` to slice step counts, active calories, and distance over custom intraday time windows.
  - Enhanced `get_recent_workouts(days=N, hours=N)` to seamlessly merge live Oura workout sessions with Health Connect records, deduplicating identical events by timestamp.
- **Health Model Tool Enhancements (`evelyn_tools.py`)**:
  - Updated `get_health_metrics` and `get_recent_workouts` to accept an `hours` parameter (e.g. `hours=2` for last 2 hours), supporting granular sub-day queries.
  - Updated OpenAPI tool definitions in `MODEL_TOOL_DEFINITIONS` with explicit instructions on querying live heart rate and sub-day activity.
- **Testing & Verification**:
  - Added `test_18_health_metrics_granular_and_intraday` to `Evelyn/tests/test_all_tools_end_to_end.py`. Total test suite passes at 138/138.

---

## [000.006.000] - 2026-08-27 — *Unified Single-Stream Agentic Architecture*

### Added & Enhanced
- **Unified Single-Stream Agentic Architecture (`evelyn_server.py`, `evelyn_config.py`)**:
  - Completely decommissioned the legacy 2-pass inference pipeline (non-streaming tool detection Pass 1 followed by streaming text Pass 2).
  - Implemented `_agentic_stream_loop()`, providing a single unified async generator where Ollama streams native thinking deltas in real-time and transitions seamlessly into tool execution or markdown synthesis in the same HTTP stream.
  - Eliminated duplicate thinking latency on regular conversational turns, slashing response time by ~50% and cutting token overhead.
  - Hardened with 6 production safeguards:
    1. **Preamble Token Quarantining**: Quarantines pre-tool text deltas from Round 1 if tool calls are emitted, preventing content duplication in final responses.
    2. **Exception-Safe Tool Feedback**: Catches all tool execution errors in try/except and formats structured feedback (`role: "tool"`), allowing the model to inspect errors and self-correct across rounds.
    3. **Hard Terminal Round Enforcement**: Forces `tools=None` when reaching `MAX_TOOL_ROUNDS` to guarantee synthesis.
    4. **Cumulative Metrics Accounting**: Aggregates token counts (`eval_count`, `prompt_eval_count`) and timing duration across all agentic sub-rounds.
    5. **Async Interruption Safety**: Preserves `asyncio.CancelledError` safety with shielded SQLite commits for interrupted sessions.
    6. **Tool Effort Escalation**: Automatically raises thinking depth across subsequent rounds when tools requiring deeper reasoning are invoked.
- **Frontend Unified Activity Stepper (`index.html`)**:
  - Replaced disconnected thinking accordion bars with a unified `<details class="agent-activity-trace">` component rendered at the top of assistant bubbles.
  - Tracks discrete round steps (`● Round 1: Reasoning & Exploration`, `● Tools Executed`, `● Round 2: Synthesis`), displaying interactive tool chips with status spinners and failure badges.
  - Auto-collapses cleanly upon stream completion into a compact summary header (`▾ Thought for 3.4s • 1 tool used`), keeping the conversation clean and readable.
  - Added full retrospective support in `loadHistory()`, rendering historical thinking and tool execution chains into unified activity traces.
- **Agentic Streaming Test Suite (`test_agentic_stream.py`)**:
  - Added dedicated unit tests covering single-pass direct conversation, multi-round tool dispatch, preamble quarantine, tool error resilience, and terminal round enforcement.

---

## [000.005.021] - 2026-08-27 — *Tool Prediction Budget Expansion & Special Token Sanitization*

### Fixed & Enhanced
- **Tool Loop Prediction Budget (`evelyn_config.py`)**:
  - Expanded `TOOL_LOOP_NUM_PREDICT` from `2048` to `8192` tokens. Previously, when generating long files (such as comprehensive dream journal entries or structured reports) along with chain-of-thought reasoning in Tool Round 0, the prediction exceeded 2048 tokens and truncated before the tool call payload was emitted, causing the turn to skip the tool loop and fall back into an ungrounded response pass.
- **Gemma 4 Channel & Triangle Token Sanitization (`evelyn_server.py`)**:
  - Added Gemma 4 special tokens (`◀channel▶`, `◀thought▶`, `◀/thought▶`, `◀call:`, `▶call`, `<|channel|>`, `<|thought|>`, `<|tool_call|>`, `◀|`, `|▶`) to `_LEAKED_MODEL_TOKENS` to ensure model internal channel transitions are filtered cleanly from user-facing streams and don't cause thinking or response stream anomalies.

---

## [000.005.020] - 2026-08-27 — *Unified Vault File Staging Pipeline & Tool Disambiguation*

### Added & Enhanced
- **Mutual Tool Schema Disambiguation (`evelyn_tools.py`)**:
  - Sharpened the LLM schema docstring for `write_journal_entry` to exclusively cover Evelyn's personal daily reflection diary (vibe check, narrative recap, message in a bottle) and explicitly forbade its use for user-authored notes, dream journals, or general vault documents.
  - Updated `write_file`'s schema to explicitly include dream journals (`Dream Journal/Dream Entries/Dream Entry YYYY-MM-DD.md`), feature ideas, user notes, and scripts.
- **Unified Staging Pipeline for Journal Entries (`journal_manager.py`, `evelyn_server.py`, `index.html`)**:
  - Re-routed `create_journal_entry()` through `terminal_agent.write_file()` targeting `JOURNAL_DIR` directly, completely eliminating temporary file creation in `_Pending Approvals/` or vault root.
  - Unified journal entries with the terminal agency modal preview, allowing one-click `👁️ Preview & Review`, `✓ Approve & Write`, and guided denial feedback.
- **Multi-Turn Tool Execution Context in Chat History (`evelyn_server.py`)**:
  - Enhanced `load_history()` to append `[Tools Executed: ...]` for assistant turns with tool invocations, ensuring the model retains full multi-turn awareness of its past actions when receiving denial or approval feedback in subsequent turns.

---

## [000.005.019] - 2026-08-27 — *Terminal & File Write Modal Previews and Silent Approvals*

### Added & Enhanced
- **Terminal & File Write Modal Inspection (`index.html`, `terminal_agent.py`, `evelyn_server.py`)**:
  - Implemented `get_approval_details(approval_id)` in `terminal_agent.py` and exposed `GET /api/terminal/details/{approval_id}` in `evelyn_server.py` to retrieve full un-truncated file content, write mode, command strings, and directory paths for pending and past approval requests.
  - Added rich modal review (`openModal('approval', id)`) in `index.html` matching journal entries, rendering full markdown formatting for `.md` documents, code syntax previews for raw files, target path badges, and action bars.
  - Added `👁️ Preview & Review` action button to in-chat approval cards and made approved badges clickable to reopen and view saved files.
- **Silent Approvals & Guided Denial Feedback (`index.html`, `evelyn_server.py`)**:
  - Configured `handleApproval` to execute `write_file` silently without dispatching redundant `[System: Command output: ...]` chat turns back to the agent loop, preserving natural conversation flow.
  - Added guided rejection prompt on Deny, enabling users to submit concise feedback (e.g. format corrections or folder adjustments) that is cleanly passed as user input to guide Evelyn's subsequent turn.

---

## [000.005.018] - 2026-08-27 — *Procedures Tool Integration, Queue Pipeline & DevUI Management*

### Added & Enhanced
- **Procedure Tool Guidance & Disambiguation (`fact_extractor.py`, `chroma_rag.py`)**:
  - Added `suggested_tools` column to the `procedures` table in `evelyn_memory.db` via database migration `000.005.018`.
  - Updated procedure extraction prompt with the engine's canonical active tool palette, explicitly guiding the extractor to associate procedures with tools like `write_file` (for Dream Journals, feature ideas, and vault notes) while strictly reserving `write_journal_entry` for Evelyn's personal daily reflection recap.
  - Enhanced RAG context assembly in `chroma_rag.py` to format retrieved procedures as actionable operational protocols (`[Operational Protocol: ...]`) with highlighted `Suggested Tool(s): <tools>`.
- **Standardized Procedure Merge & Split Queues (`memory_db.py`, `procedure_consolidator.py`, `pending_reviewer.py`)**:
  - Implemented `procedure_merge_queue` and `procedure_split_queue` tables in `evelyn_memory.db` with CRUD helper functions.
  - Extended `procedure_consolidator.py` to process manually queued merge and split requests during background idle passes before running automated trigger clustering.
  - Added `generate_procedure_split_proposal()` and updated proposal approval handlers in `pending_reviewer.py` and `evelyn_server.py` to support `procedure_split` proposals and preserve `suggested_tools`.
- **Dedicated DevUI Procedures Management Tab (`dev.html`, `evelyn_server.py`)**:
  - Added **⚙️ Procedures** tab in DevUI with live count, real-time search filter across triggers, steps, tools, and tags, and status filter pills (`All`, `Live`, `Pending Review`, `Archived`).
  - Added floating multi-select merge action bar allowing one-click selection of multiple procedure cards to queue for background LLM consolidation.
  - Added inline procedure editing (`Save Changes`), background split queuing (`Queue Split`), and soft archiving/restoration.
  - Exposed REST endpoints in `evelyn_server.py`: `GET /api/procedures`, `PATCH /api/procedures/{id}`, `POST /api/procedures/queue_merge`, `POST /api/procedures/{id}/queue_split`, `POST /api/procedures/{id}/archive`.

---

## [000.005.017] - 2026-08-26 — *RAG Ingestion Boilerplate Filtering & YAML Exclusion Support*

### Added & Enhanced
- **Pattern-Based RAG Ingestion Exclusion (`evelyn_config.py`, `ingest_obsidian_knowledge.py`)**:
  - Configured `RAG_IGNORE_PATTERNS` to automatically bypass structural book boilerplate (back-of-book indexes like `* - Index.md`, `*_index.md`, `*Table of Contents.md`, `* - Colophon.md`, `* - About the Author*.md`) during vector embedding.
  - Keeps navigation/index files accessible in the Obsidian vault for manual browsing while preventing semantic keyword saturation and false-positive vector hits in RAG context.
- **YAML Frontmatter & Tag-Based RAG Exclusion**:
  - Added support for explicit document-level RAG bypass via YAML frontmatter (`rag_exclude: true`, `rag_ignore: true`, `no_rag: true`) or tags (`#rag-ignore`, `#rag-exclude`, `#no-rag`).
  - Integrated automatic Chroma document garbage collection (`chroma_rag.delete_document`) during sync passes when notes are marked excluded or match ignore patterns.

---

## [000.005.016] - 2026-08-26 — *Chat UI Stream Lifecycle & Reconciler Consolidation*

### Fixed & Enhanced
- **Consolidated Chat Streaming Architecture (`index.html`)**:
  - Implemented unified `setupAssistantStreamContext()` and `executeChatStream()` across message sends, message edits, and response regenerations.
  - Replaced legacy `recoverFromConnectionDrop()` and blind 2-minute `startResponsePoll()` timers with an authoritative, coordinated `reconcileStreamFailure()` routine.
  - Eliminated race conditions between visibility recovery and background fetch promises when reconnecting active streams or pulling missed history.
  - Synchronized `initApp()` startup sequence to smoothly catch up on in-flight stream sessions without UI jitter or duplicate message bubbles.

---

## [000.005.015] - 2026-08-26 — *Pinned Alias Word Boundaries & Client-Side Chunk Highlighting*

### Fixed & Enhanced
- **Word-Boundary Matching on Pinned Aliases (`chroma_rag.py`)**:
  - Replaced naive substring matching with regex word boundaries (`\b`) when scanning query text for pinned vault note aliases.
  - Eliminated false positives where common words (e.g. `"same"`, `"sample"`) inadvertently triggered pinned notes for aliases like `"Sam"`.
- **Client-Side Chunk Highlighting & De-Emphasis (`dev.html`)**:
  - Implemented zero-database-overhead chunk extraction in `dev.html` (`splitDocumentIntoChunks` and `formatChunkHighlightInDoc`).
  - When expanding a retrieved chunk, the viewer highlights the exact section injected into the LLM prompt with a luminous accent border while smoothly de-emphasizing non-referenced surrounding document sections.
  - Added automated unit test coverage in `Evelyn/tests/test_feedback_and_rag_telemetry.py`.

---

## [000.005.014] - 2026-08-26 — *Analytics & Feedback Filter Controls with 1-Day Default Windowing*

### Added & Enhanced
- **Analytics & Feedback Filter Controls (`dev.html`)**:
  - Implemented independent **Time Range Quick Pickers** (`1 Day`, `1 Week`, `1 Month`, `All Time`) with active pill highlights.
  - Implemented independent **Type Filter** selector (`All Analytics Types`, `Conversational Feedback`, `RAG Context Retrieval Log`) allowing targeted visibility without resetting time range.
  - Added a **Reset Filters** button returning state to the optimized 1-day all-types view.
  - Set default view on tab switch to **1 Day** (`days=1`), drastically cutting initial payload sizes and preventing unnecessary full-history database scans on load.
- **Server-Side Time Windowing (`evelyn_server.py` & `chroma_rag.py`)**:
  - Enhanced `GET /telemetry/feedback` and `GET /telemetry/rag` to accept an optional `days: float` query parameter.
  - Filtered feedback counts (`total_rated`, `upvotes`, `downvotes`, `satisfaction_rate`) and recent records dynamically based on `created_at >= cutoff`.
  - Added test coverage in `Evelyn/tests/test_feedback_and_rag_telemetry.py` for time-range windowing and API responses.

---

## [000.005.013] - 2026-08-25 — *Consolidator Scan Continuity & Fast Exact Deduplication*

### Fixed & Enhanced
- **Consolidator Scan Continuity (`fact_consolidator.py`)**:
  - Fixed an issue where `_get_anchor_batch` reset the scan anchor pointer to `0` whenever a new fact modified category entry count `len(records)`. The pointer now wraps continuously (`anchor = anchor % n`) across consolidation passes.
  - Optimized `remediate_database_categories` to use SQL `NOT GLOB` filtering to avoid loading hundreds of thousands of historical proposal records into memory.
- **Fast Deterministic Deduplication Pre-Pass**:
  - Implemented `fast_deduplicate_exact_matches()` to detect and collapse exact and whitespace-normalized duplicate context entries into primary records, merging metadata and enqueuing vector deletions.
- **Targeted Cluster Consolidation**:
  - Merged 8 fragmented, redundant *Dungeon Crawler Carl* context facts in `Cat05-U` (IDs 2310, 2313, 2391, 2407, 2548, 2563, 2565, 2650) into a single, unified evolved entry (`#3972`), recorded proposal `#176816`, and updated Chroma vector embeddings.

---

## [000.005.012] - 2026-08-25 — *Interactive Feedback Comments & Vault Note Editor in DevUI*

### Added
- **Vault Note Reader & Editor API (`evelyn_server.py`)**:
  - Added `GET /api/vault/note` to retrieve full markdown content of any note within vault boundaries with path traversal protection.
  - Added `POST /api/vault/note` to write note edits directly to disk, update `vault_db`, and enqueue custodial re-indexing into `chroma_sync_queue`.
- **RAG Telemetry Content & Expandable Chunks**:
  - Captured full chunk content in `chroma_rag.py` retrieval logs (`rag_retrieval_log`).
  - Added expandable full chunk viewing and inline `✏️ Edit Note` buttons in `dev.html` telemetry inspector.
  - Added dedicated Vault Note Editor Modal in `dev.html` with in-browser editing and re-indexing.
- **Feedback Comments & Expandable Responses (`dev.html` & `index.html`)**:
  - Added `💬 Add/Edit Comment` action to `index.html` chat message actions.
  - Added expandable full response and thinking trace toggles to feedback cards in `dev.html`.
  - Added Feedback Explanation & Comment modal in `dev.html` for reviewing and amending ratings.
- **Automated Tests**:
  - Added `test_vault_note_endpoints` in [Evelyn/tests/test_feedback_and_rag_telemetry.py](file:///home/rathius/evelyn/Evelyn/tests/test_feedback_and_rag_telemetry.py).

---

## [000.005.011] - 2026-08-25 — *Thinking Level Telemetry & Metrics Exposure*

### Added
- **Thinking Effort & Source Exposure in Telemetry APIs (`evelyn_server.py`)**:
  - Added `GET /telemetry/thinking` endpoint providing aggregate counts of resolved thinking effort levels (`low`, `medium`, `high`, `max`), resolution sources (`heuristic`, `self_elected`, `tool_escalation`, `ui_override`), and recent message thinking audit logs.
  - Linked `think_effort` and `think_source` from `message_metrics` into `GET /history` and `GET /telemetry/feedback` payloads for review.
- **Automated Tests**:
  - Expanded [Evelyn/tests/test_feedback_and_rag_telemetry.py](file:///home/rathius/evelyn/Evelyn/tests/test_feedback_and_rag_telemetry.py) to assert thinking level telemetry collection and endpoint accuracy.

---

## [000.005.010] - 2026-08-25 — *Conversational Feedback & RAG Telemetry Logging System*

### Added
- **Database Schema Migrations (`000.005.010`)**:
  - Registered migration `000.005.010` in [Evelyn/tools/db_migrator.py](file:///home/rathius/evelyn/Evelyn/tools/db_migrator.py) creating `message_feedback` table in `evelyn_chat.db` (for 👍/👎 user rating and comments per assistant message).
  - Registered migration `000.005.010` creating `rag_retrieval_log` table in `evelyn_memory.db` (for real-time tracking of vector retrieval events, similarity distances, kept vs dropped threshold status, and source note paths).
- **RAG Telemetry Logging Interceptor (`chroma_rag.py`)**:
  - Implemented `log_rag_retrieval()`, `get_recent_rag_telemetry()`, and `link_rag_telemetry_to_message()` in [Evelyn/tools/chroma_rag.py](file:///home/rathius/evelyn/Evelyn/tools/chroma_rag.py).
  - Wired telemetry logging directly into `build_rag_context()` with fire-and-forget execution and zero added latency.
- **Server Feedback & Telemetry Endpoints (`evelyn_server.py`)**:
  - Added `save_or_update_feedback()` and `get_feedback_for_messages()` database helpers.
  - Added `POST /chat/feedback` (upsert user ratings), `GET /chat/feedback/{message_id}`, `GET /telemetry/rag` (recent retrieval logs), and `GET /telemetry/feedback` (feedback counts and satisfaction ratios).
  - Hydrated feedback state into `GET /history` messages payload.
  - Emitted `message_id` inside the final SSE `done` event chunk for immediate UI feedback binding.
- **Chat UI Feedback Toolbar (`index.html`)**:
  - Added interactive 👍 / 👎 buttons with toggle animation and active color states inside `.msg-actions` for assistant messages.
  - Restores saved feedback state upon loading conversation history.
- **DevUI Telemetry Dashboard (`dev.html`)**:
  - Added **📊 Telemetry & Feedback** dashboard tab displaying total ratings, upvote/downvote satisfaction rate, recent rated responses, and expandable RAG context retrieval inspection logs.
- **Automated Tests**:
  - Added test suite in [Evelyn/tests/test_feedback_and_rag_telemetry.py](file:///home/rathius/evelyn/Evelyn/tests/test_feedback_and_rag_telemetry.py) covering CRUD feedback operations, RAG telemetry logging, and API endpoints.

---

## [000.005.009] - 2026-08-23 — *Obsidian Vault List & Checklist Management System*

### Added
- **Obsidian Vault List Manager (`vault_list_manager.py`)**:
  - Implemented [Evelyn/tools/vault_list_manager.py](file:///home/rathius/evelyn/Evelyn/tools/vault_list_manager.py) providing offline-first list and checklist operations directly on markdown notes in the Obsidian Vault (`vault.root/Lists/`).
  - Added template-driven initialization supporting category templates ([templates/lists/groceries.md](file:///home/rathius/evelyn/templates/lists/groceries.md)) and generic lists ([templates/list_template.md](file:///home/rathius/evelyn/templates/list_template.md)).
  - Implemented item-first presentation format (`Item (Qty Unit)` / `Item (2x)`), category-aware section routing, intelligent quantity incrementing on existing items, fuzzy checkbox toggling (`- [ ]` $\leftrightarrow$ `- [x]`), item removal, and completed cleanup.
- **Model Function Calling Tool**:
  - Registered `manage_vault_list` in [Evelyn/tools/evelyn_tools.py](file:///home/rathius/evelyn/Evelyn/tools/evelyn_tools.py) `MODEL_TOOL_DEFINITIONS` and `TOOL_FUNCTIONS` supporting structured item objects, string lists, and flexible actions (`read`, `add`, `check`, `uncheck`, `remove`, `clear_completed`, `list_all`).
- **Configuration**:
  - Added `LISTS_DIR` path in [evelyn_config.py](file:///home/rathius/evelyn/evelyn_config.py).

---

## [000.005.008] - 2026-08-23 — *Google Tasks Integration & Dedicated Task Synchronizer*

### Added
- **Google Tasks Dedicated Synchronizer (`gtasks_sync.py`)**:
  - Implemented [Evelyn/tools/gtasks_sync.py](file:///home/rathius/evelyn/Evelyn/tools/gtasks_sync.py) providing offline-first task synchronization, SQLite caching, OAuth credential loading/refreshing, and full CRUD operations (`sync_gtasks`, `get_cached_tasks`, `create_gtask`, `complete_gtask`, `delete_gtask`).
  - Added [scripts/setup_gtasks.py](file:///home/rathius/evelyn/scripts/setup_gtasks.py) for interactive OAuth2 setup with automatic fallback to existing Google credentials.
- **Model Function Calling Tools**:
  - Registered `create_task`, `complete_task`, `delete_task`, `list_tasks`, and `sync_google_tasks` in [Evelyn/tools/evelyn_tools.py](file:///home/rathius/evelyn/Evelyn/tools/evelyn_tools.py) `MODEL_TOOL_DEFINITIONS` and `TOOL_FUNCTIONS`.
  - Updated `get_agenda` to present a unified schedule displaying both Google Calendar events and pending Google Tasks.
- **Server Background Sync & System Context**:
  - Added periodic background `_gtasks_sync_loop()` in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py) (running every 30 minutes).
  - Updated `get_upcoming_agenda_prompt_context()` in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py) to inject pending task notifications into the system prompt.
- **Database Schema Migration**:
  - Registered migration `000.005.008` (`create_tasks_table`) in [Evelyn/tools/db_migrator.py](file:///home/rathius/evelyn/Evelyn/tools/db_migrator.py) creating the `tasks` SQLite cache table in `evelyn_chat.db`.

---

## [000.005.007] - 2026-08-23 — *Graceful Service Shutdown Lifecycle & UPS Integration*

### Added
- **Graceful Stop Script & Workflow**:
  - Enhanced [scripts/stop_evelyn_services.sh](file:///home/rathius/evelyn/scripts/stop_evelyn_services.sh) with `--all`/`--with-ollama` and `--checkpoint-wal`/`--flush-wal` options for clean teardown.
  - Added dedicated workflow guide in [.agents/workflows/stop-services.md](file:///home/rathius/evelyn/.agents/workflows/stop-services.md) (`/stop-services`).
  - Added "Stop All Services (with Ollama & WAL flush)" task in [.vscode/tasks.json](file:///home/rathius/evelyn/.vscode/tasks.json).
- **Physical Environment UPS Hook (Sanctum)**:
  - Created `scripts/personal/ups_shutdown_hook.sh` and registered symlinks in `/etc/apcupsd/` (`doshutdown`, `failing`, `timeout`, `loadlimit`, `runlimit`, `emergency`) to safely stop services and checkpoint SQLite WAL journals when UPS signals power failure.

### Fixed
- **ChromaDB Queue Drain Deadline Hardening**:
  - Added strict per-item `deadline` parameter and checking to `drain_sync_queue()` and `flush_sync_queue()` in [chroma_rag.py](file:///home/rathius/evelyn/Evelyn/tools/chroma_rag.py).
  - Implemented automatic transaction rollback from `'processing'` to `'pending'` for unprocessed items when deadline expires mid-batch, preventing shutdown hangs.
- **FastAPI Lifespan Background Task Cancellation**:
  - Tracked all background `asyncio.Task` instances in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py) lifespan and cleanly cancelled/gathered them before calling `clean_shutdown_all_tasks()`.
- **Systemd Timeout Tuning**:
  - Tuned `TimeoutStopSec=15` in `/etc/systemd/system/evelyn.service`.

---

## [000.005.006] - 2026-08-23 — *Granular Source Entry Management for Merge Proposals*

### Added
- **Granular Source Item Editing & Unlinking in Proposals**:
  - Added support for editing, unlinking, and deleting individual source procedures directly on `procedure_merge` proposal cards in [dev.html](file:///home/rathius/evelyn/evelyn_ui/dev.html).
  - Extended `/api/review/procedures/{id}/{action}` in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py) to support `edit` and `delete` actions that commit changes immediately to the database.
  - Unified `renderSourceEntriesList` across all proposal types (`procedure_merge`, `merge`, `supersede`, `split`, and `profile_update`) to allow instant inline edits to persist to the database regardless of whether the overarching proposal is approved, denied, or unlinked.

---

## [000.005.005] - 2026-08-23 — *YAML Scalar Unquoting & Proposal Text Sanitization*

### Fixed
- **DevUI Proposal Text Rendering & YAML Escaping**:
  - Implemented `cleanYamlScalar` in [dev.html](file:///home/rathius/evelyn/evelyn_ui/dev.html) to properly decode single-quoted, double-quoted, folded, and block YAML scalars.
  - Resolved double apostrophe escaping (`''` to `'`) in quotes and contractions (`it's`, `AI's`) across proposed steps, trigger patterns, and split observations.
  - Eliminated random mid-sentence line breaks caused by PyYAML 80-character line folding.
  - Updated [procedure_consolidator.py](file:///home/rathius/evelyn/Evelyn/tools/procedure_consolidator.py) to dump YAML with `width=10000` to prevent line wrapping during proposal generation.
  - Sanitized existing pending procedure merge proposals in `evelyn_memory.db`.

---

## [000.005.004] - 2026-08-23 — *Environment Configuration & Network Parameterization*

### Added
- **Local Environment Support (`.env`)**:
  - Implemented automatic `.env` loader in [evelyn_config.py](file:///home/rathius/evelyn/evelyn_config.py) to read local network, port, SSL, and service endpoint overrides.
  - Added clean, documented [.env.example](file:///home/rathius/evelyn/.env.example) template for version control and new environment provisioning.

### Changed
- **Network & Host Parameterization**:
  - Parameterized CORS `ALLOWED_ORIGINS` to dynamically incorporate values from `EVELYN_ALLOWED_ORIGINS` alongside standard localhost origins.
  - Parameterized `IMAGE_SERVER_URL` via `EVELYN_IMAGE_SERVER_URL` in [evelyn_config.py](file:///home/rathius/evelyn/evelyn_config.py) and [check_evelyn_status.sh](file:///home/rathius/evelyn/scripts/check_evelyn_status.sh).
  - Parameterized SSL key/cert paths in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py).
  - Generalized architecture diagrams and component topologies in [engine_architecture.md](file:///home/rathius/evelyn/reference/engine_architecture.md) and unit test mocks in [test_image_generation.py](file:///home/rathius/evelyn/Evelyn/tests/test_image_generation.py).

---

## [000.005.003] - 2026-08-23 — *Image Host Requirements Documentation & Sanitization*

### Added
- **Core Requirements Integration**:
  - Incorporated the **FLUX.1 Schnell NF4 Image Generation Microservice** specifications and dependencies into [REQUIREMENTS.md](file:///home/rathius/evelyn/REQUIREMENTS.md) under External Services.

### Changed
- **Documentation Sanitization & Identity Parameterization**:
  - Parameterized host-specific domains, IPs, and user directory paths in [REQUIREMENTS_IMAGE_HOST.md](file:///home/rathius/evelyn/services/image/REQUIREMENTS_IMAGE_HOST.md) into generic placeholders (`<image-host>`, `<tailnet>`, `<username>`).
  - Added multi-platform (Windows & Linux) virtual environment and firewall configuration instructions.

---

## [000.005.002] - 2026-08-23 — *DevUI Split Proposal & Ingestion Layout Refinements*

### Fixed
- **DevUI Split & Proposal Card Layout**:
  - Separated metadata/badges and action buttons (`Split`, `Edit`, `Unlink`, `Delete`, `Remove`) into a top header row across Source Compound Entry, Proposed Atomic Context Facts, and Profile Update cards in [dev.html](file:///home/rathius/evelyn/evelyn_ui/dev.html).
  - Observation text content and domain tags now render on dedicated full-width rows rather than being squished into a narrow column alongside button groups.
  - Added responsive `flex-wrap` and minimum widths to inline editing forms and Split Modal draft inputs.
- **Document Ingestion Staging & Mode Layout**:
  - Restructured **Direct Filesystem Staging** directory guide cards so folder paths and explanations sit on separate full-width rows instead of cramping horizontally.
  - Made the **Ingestion Mode** radio card container responsive with `repeat(auto-fit, minmax(260px, 1fr))` for mobile and narrow viewports.

---

## [000.005.001] - 2026-08-22 — *Tag Librarian Vault DB Audit Fix*

### Fixed
- **Tag Librarian Vault DB Interface**:
  - Implemented missing `vault_db.update_document_tag_audit(path, tags=None)` in [vault_db.py](file:///home/rathius/evelyn/Evelyn/tools/vault_db.py) to reliably record audit timestamps and update document tags.
  - Resolved `AttributeError: module 'Evelyn.tools.vault_db' has no attribute 'update_document_tag_audit'` occurring during background idle Tag Librarian tasks.
  - Added unit test `test_vault_db_update_document_tag_audit` in [test_vault_move_optimization.py](file:///home/rathius/evelyn/Evelyn/tests/test_vault_move_optimization.py).

---

## [000.005.000] - 2026-08-22 — *Vault Document Ingestion Subsystem & Sidecar Architecture*

### Added
- **Automated PDF Staging Pipeline & Worker**:
  - Created dedicated dual-queue staging directories (`Attachments/Staging/Full_Extraction/`, `Attachments/Staging/Sidecar_Only/`).
  - Built `Evelyn/tools/pdf_staging_worker.py` queue scanner supporting `.meta.json` domain routing, PyMuPDF extraction, Sidecar synthesis, and Task Manager mutual exclusion.
- **Vault Watcher Staging Detection**:
  - Updated `scripts/obsidian_vault_watcher.py` to observe `Attachments/Staging/` and automatically trigger staging ingestion when files are dropped into the vault via filesystem or Syncthing.
- **FastAPI Endpoints**:
  - Added `GET /api/vault/domains` to list all valid domain destinations with their root paths and labels.
  - Added `POST /api/vault/upload_staging` to accept multi-part file uploads, write metadata, and asynchronously queue processing.
- **DevUI Document & PDF Ingestion Card**:
  - Added dedicated **📄 Document Ingestion** tab to `evelyn_ui/dev.html` featuring drag-and-drop file upload, mode toggle (Full Extraction vs Sidecar Card Only), destination domain dropdown, and upload status telemetry.
- **Rich Library Index Card (Sidecar Generator)**:
  - Generates rich `.md` Sidecar notes for non-markdown assets containing frontmatter, author, normalized taxonomy tags (`Tech/AI`, `literature/reference`), embedded PDF attachment links (`![[Attachments/Source Material/...]]`), chapter tables, overview gists, and semantic cross-links.
- **Zero-Overhead Reorganization & Content Hashing**:
  - Added SHA-256 content hashing (`compute_content_hash`) in `Evelyn/tools/ingest_obsidian_knowledge.py` to identify file moves across vault sync cycles and skip redundant GPU vector re-embedding.
  - Added `vault_db.move_document()` for atomic $<1\text{ms}$ SQLite path updates.
  - Added `chroma_rag.direct_remap()` and `chroma_rag.enqueue_remap()` to transfer precomputed Chroma embedding chunks directly to new document paths with zero model inference.
  - Enhanced `scripts/obsidian_vault_watcher.py` to detect `on_moved` events and perform atomic SQLite and Chroma remapping.

### Changed
- **Reference Library & Vault PDF Standardization**:
  - Normalized and extracted 26 Owner's Manuals and Spec Sheets into zero-padded chapter notes in `Reference Library/Owner's Manuals/`.
  - Converted medical psychology reports in `Ricky/Medical/Psychology/` and Python reference notes into structured chapter notes.
  - Relocated all remaining 31 loose PDFs across the entire vault into `Attachments/Source Material/<Domain>/` with interactive Sidecar markdown notes in their place.

---

## [000.004.003] - 2026-08-22 — *Vault Maintenance, Sidecar Index Cards & Move Optimization*

### Added
- **PDF Title Normalization & Word Segmentation**:
  - Implemented dynamic-programming word segmentation and TitleCase normalization in `scripts/extract_pdf_library.py` to convert concatenated filenames (`buildingapplicationswithaiagents...`) into clean Title Case and subtitle metadata.
- **Rich Library Index Card (Sidecar Generator)**:
  - Generates rich `.md` Sidecar notes for non-markdown assets containing frontmatter, author, normalized taxonomy tags (`Tech/AI`, `literature/reference`), embedded PDF attachment links (`![[Attachments/Source Material/...]]`), chapter tables, overview gists, and semantic cross-links.
- **Semantic Nearest-Neighbor & Entity Cross-Linking**:
  - Added `chroma_rag.find_semantic_neighbors()` to retrieve top semantically related vault notes via cosine similarity without LLM overhead.
  - Added `vault_db.get_all_entities()` to match known note titles/aliases mentioned in extracted literature.
- **Zero-Overhead Reorganization & Content Hashing**:
  - Added SHA-256 content hashing (`compute_content_hash`) in `Evelyn/tools/ingest_obsidian_knowledge.py` to identify file moves across vault sync cycles and skip redundant GPU vector re-embedding.
  - Added `vault_db.move_document()` for atomic $<1\text{ms}$ SQLite path updates.
  - Added `chroma_rag.direct_remap()` and `chroma_rag.enqueue_remap()` to transfer precomputed Chroma embedding chunks directly to new document paths with zero model inference.
  - Enhanced `scripts/obsidian_vault_watcher.py` to detect `on_moved` events and perform atomic SQLite and Chroma remapping.

### Changed
- **Roadmap Harmonization**:
  - Updated `ROADMAP.md` Phase 4 to replace separate custom plugin and ghost link items with the native Sidecar Catalog and Zero-Overhead Reorganization engine.

---

## [000.004.002] - 2026-08-22 — *Memory Tag Taxonomy Sanitization & DB Status Fix*

### Fixed
- **Database Migration Framework Up-To-Date Evaluation**:
  - Fixed `check_all_dbs_status()` in `Evelyn/tools/db_migrator.py` so databases without pending migrations correctly evaluate as up-to-date when engine version advances.

### Database Migrations
- **Memory Tag Sanitization (`000.004.002`)**:
  - Registered and executed migration `strip_legacy_kw_tags_from_memory` to strip redundant `kw/` and `ctx/` noise prefixes from `context_entries.tags` and `proposals.merged_tags` in `data/evelyn_memory.db`.

---

## [000.004.001] - 2026-08-22 — *Research Watchdog & Scope Dynamic Timeouts*

### Fixed
- **Deep Research Watchdog Timeout Premature Abort**:
  - Added dedicated soft timeout baselines for research scopes (`research_quick`: 2,400s / 40m, `research_standard`: 9,000s / 2.5h, `research_deep`: 32,400s / 9h) in `task_manager.py:DEFAULT_SOFT_TIMEOUTS`.
  - Updated `task_manager.get_dynamic_timeout()` to dynamically resolve task scope and `wall_clock_timeout` directly from the server task registry or on-disk `state.json` (`max(wall_clock_timeout + 1800, wall_clock_timeout * 1.25)`).
  - Resolved dynamic statistical historical aggregation for research tasks using wildcard matching (`WHERE task_name LIKE 'task_%'`) in SQLite `heavy_task_history`.
- **Heavy Task Registry Synchronization**:
  - Registered `tag_librarian` in `task_manager.HEAVY_TASK_KEYS` and synchronized known heavy tasks in `reference/engine_architecture.md`.

---

## [000.004.000] - 2026-08-22 — *Sanctum Architecture & Guardrails*

### Added
- **Zero-Padded Versioning & Migration Framework**:
  - Centralized version definition `__version__ = "000.004.000"` with parsing and comparison utilities in `Evelyn/version.py`.
  - Built `Evelyn/tools/db_migrator.py` with transactional DDL execution, Python callable data transforms, per-database tracking tables (`schema_migrations`), safety snapshots (`data/backups/`), and post-migration Chroma/Vault synchronization hooks.
  - Implemented standalone CLI migration manager `scripts/migrate_db.py` supporting `--status`, `--execute`, `--dry-run`, and automated Git release tagging (`--tag`).
  - Added fail-fast boot validation to `evelyn_server.py` with an optional `AUTO_MIGRATE_ON_BOOT` configuration toggle.
- **Repository Documentation**:
  - Published comprehensive, modern repository `README.md` with system overview, architecture diagrams, and AI-collaboration & human-architecting disclosures.
  - Created formal `CHANGELOG.md`.

### Changed
- **Open-Source Sanitization & Persona Parameterization**:
  - Extracted hardcoded persona, user identity, and vault path variables into parameterized configurations in `evelyn_config.py`.
  - Migrated Fast Memory taxonomy codes from `-R`/`-E` to abstract `-U` (User) and `-A` (Assistant).
  - Deployed generic persona, user profile, and system directive templates in `templates/` with an interactive setup wizard (`evelyn_setup.py`).

### Architectural & Resilience
- **Centralized Task Mutual Exclusion (`task_manager.py`)**:
  - Unified heavy task registry and watchdog preventing concurrent CPU/GPU resource thrashing across research, fact extraction, and profile evolution.
- **ChromaDB Single-Writer Custodian**:
  - SQLite WAL-backed staging queue (`chroma_sync_queue`) with single-process custodial writes to eliminate HNSW vector index corruption and file-lock collisions.
- **Dual-Socket NUMA Partitioning**:
  - Node 0 CPU/GPU pinning for core LLM inference and SQLite I/O; Node 1 isolation for Chatterbox TTS speech synthesis.

### Database Migrations
- Baseline migration `000.004.000` registered and applied across `chat`, `memory`, `vault`, and `media` databases.

---

## [000.003.000] - 2026-07-15 — *Senses, Tools & Agency*

### Added
- **Autonomous Deep Research Subsystem**:
  - Background multi-step search engine with Trafilatura crawling, atomic query generation, evidence synthesis, discovered technical alias expansion, and direct Markdown compilation into Obsidian.
- **Chatterbox Speech Synthesis (F5-TTS/Matcha)**:
  - Streaming low-latency neural TTS server with sentence chunking and dynamic emotion tags.
- **Multimodal Visual Memory & Attachment Indexing**:
  - SQLite media asset registry (`evelyn_media.db`), EXIF/GPS coordinate parsing, and local vision indexing pipeline.
- **Agentic Health & Life Tracking**:
  - Oura Ring Cloud API v2 integration (sleep/readiness/stress metrics) and Google Drive Health Connect database synchronization.
- **Developer Triage & Review Console**:
  - Touch-optimized web dashboard (`evelyn_ui/dev.html`) with real-time heavy task monitoring and proposal review queues.

---

## [000.002.000] - 2026-05-15 — *Long-Term Memory & Vector RAG*

### Added
- **Persistent SQLite Memory Storage**:
  - Introduced `evelyn_memory.db` for categorized context entries, consolidation proposals, and procedural workflows.
  - Introduced `evelyn_vault.db` for incremental file mapping, link graphs, and backlink resolution.
- **Semantic ChromaDB RAG Vector Store**:
  - Full-vault vector embeddings using `BAAI/bge-large-en-v1.5` with priority boosting.
- **Autonomous Memory Extraction & Consolidation**:
  - Background fact extractor and deduplication engines running during server idle periods.

---

## [000.001.000] - 2026-03-25 — *Persona & Brain Core*

### Added
- **Custom FastAPI Server (`evelyn_server.py`)**:
  - Standalone server replacing OpenWebUI/Modelfile runtime for sub-millisecond overhead.
  - Streaming SSE chat completions, regeneration, message editing, and history endpoints.
- **Ollama Local LLM Integration**:
  - Optimized system prompt assembly, dynamic context window budgeting, and temperature tuning.
- **Interactive Chat Interface**:
  - Clean HTML/CSS companion chat client with offline Markdown rendering (`marked.js` + `DOMPurify`).
