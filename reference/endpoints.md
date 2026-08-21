---
title: endpoints.md
date created: 2026-02-26 20:05:15
date modified: 2026-08-15 17:32:45
tags: api, endpoints, routing, backend, local_server, evelyn
---

# Evelyn Server API Endpoints

This document is the single source of truth for the custom REST and Server-Sent Events (SSE) API endpoints exposed by the **Evelyn Engine** backend ([[evelyn_server.py]]). All legacy Open WebUI pathways have been completely retired.

---

## 1. Chat & Conversation Management

### `GET /status`
* **Purpose**: Performs a quick health check on the server runtime and connection statuses of downstream local services (Ollama, TTS server, Image server).
* **Returns**: JSON object showing service statuses, active model parameters, thinking effort settings (`think`, `think_tool_loop`, `think_self_elect`), and active context limit (`num_ctx`).

### `POST /chat`
* **Purpose**: Processes a new conversational message from the UI.
* **Payload**: JSON object `{"message": "<user text>", "think": "<optional effort level>"}` where `think` can be `"low"`, `"medium"`, `"high"`, `"max"`, or `false` (overrides heuristic/self-election when provided).
* **Flow**:
  1. Runs [[query_reformulator.py]] for conversational keywords.
  2. Executes semantic vector search via [[chroma_rag.py]] across `evelyn_memory` full-text index using `BAAI/bge-large-en-v1.5` (1024-dim, 1,600-char chunks) with priority score boosting (`rag_priority: high` multiplier 0.75).
  3. Query matches dense facts from [[context_manager.py]].
  4. Pre-classifies thinking effort (`classify_message_effort`) and streams **Server-Sent Events (SSE)** with phase labels (`[Initial]`, `[Tool N]`, `[Response]`), dynamic tool-call updates, and per-message telemetry logging.

### `POST /regenerate`
* **Purpose**: Triggers a regeneration of the latest response in the chat chain.
* **Returns**: Streamed SSE assistant message.

### `POST /edit`
* **Purpose**: Updates the content of the latest user message in SQLite, deletes the previous assistant response, and streams a fresh response for the corrected prompt.
* **Payload**: JSON object `{"message": "<updated text>"}`
* **Returns**: Streamed SSE assistant message.

### `POST /chat/stop`
* **Purpose**: Safely stops an active in-flight chat generation session, aborting model inference and terminating tool child processes.
* **Payload**: Optional JSON object `{"stream_id": "<optional stream id>"}` (defaults to currently active stream).
* **Returns**: JSON object `{"status": "stopped" | "noop", "stream_id": "<id>"}`


### `GET /chat/stream/{stream_id}`
* **Purpose**: Reconnects to an in-memory streaming session (`ActiveStreamSession`) with cursor replay.
* **Parameters**: Query parameter `after` (integer, default `-1`).
* **Returns**: Streamed Server-Sent Events (SSE) starting from chunk `after + 1` with sequential `id: <int>` lines, live event forwarding, and keep-alive heartbeats until completion or disconnection.

### `GET /chat/active_stream`
* **Purpose**: Queries whether an assistant generation turn is currently active/in-flight on the server.
* **Returns**: JSON object:
  * Active: `{"active": true, "stream_id": "<id>", "status": "running", "chunks_count": <int>, "created_at": <float>}`
  * Inactive: `{"active": false}`

### `GET /latest_message_id`
* **Purpose**: Mobile connection recovery helper. Polled on tab visibility changes to compare the UI's last message ID against the database, triggering an automatic history recovery if there is a mismatch.

### `GET /history`
* **Purpose**: Fetches saved chat logs from `evelyn_chat.db`.
* **Limits**: Honors `MAX_HISTORY_MESSAGES = 40` config cap for model ingestion, but UI retrieves full scrollable history (including prompt and response evaluation token counts for context telemetry).

### `DELETE /history`
* **Purpose**: Wipes all conversation history rows from `evelyn_chat.db`.

### `POST /new_thread`
* **Purpose**: Inserts a `[THREAD_BREAK]` marker to start a fresh chat segment without losing any historical logs.

### `GET /artifact`
* **Purpose**: Reads generated media assets (such as FLUX images, journal drafts) and synthesized Deep Research reports (`type=research`) directly in the UI with dynamic Obsidian vault and workspace search fallbacks.

---

## 2. Ingestion & Background Task Orchestration

### `POST /refresh_memory`
* **Purpose**: The master ingestion trigger. Executes [[refresh_memory.py]] as an asynchronous subprocess.
* **Sequence**: Map Vault Index $\rightarrow$ Ingest Obsidian Knowledge (Full Vault).
* **Monitoring**: Outputs real-time phase tags (`[PHASE_START:]`, `[PHASE_DONE:]`) to drive progress loops.

### `GET /task_status/{task_name}`
* **Purpose**: Polls the status of active background processes (e.g., `refresh_memory`, `vault_map`, `sync`).
* **States**: `running`, `done`, `error`, `unknown`.

### `POST /sync`
* **Purpose**: Direct trigger for the Knowledge Sync pipeline ([[ingest_obsidian_knowledge.py]]).

### `POST /vault_map`
* **Purpose**: Direct trigger for SQLite directory index mapping ([[vault_indexer.py]]).

---

## 3. Local Inference Bridges

### `POST /tts/stream`
* **Purpose**: Initiates chunked TTS generation via [[tts_server.py]]. Accepts an OpenAI-format body (`{"model": "...", "input": "<text>"}`).
* **Returns**: Server-Sent Events stream. Emits a `data: {"chunk": "<filename.wav>"}` event per sentence group (split by paragraph boundaries first, then capped at `CHUNK_SENTENCES` sentences, default 3), followed by a terminal `data: {"done": true}` event. Errors yield `data: {"error": "<message>"}`.
* **Behaviour**: Ollama is evicted from VRAM once at the start of the request; Chatterbox loads and stays resident for the full synthesis run, then unloads and prefetches Ollama in the background. Progressive playback begins on the client as soon as the first chunk event arrives.

### `GET /tts-audio/{filename}`
* **Purpose**: Proxies individual sentence WAV chunks from [[tts_server.py]]'s output directory to the client.
* **Behaviour**: Allows Tailscale/mobile clients to fetch chunk files through `evelyn_server` (already on `0.0.0.0`) without direct access to the TTS server's `localhost:5050` port. Files are cleaned up automatically after `FILE_CLEANUP_DELAY_S` (600 s).

---

## 4. UI Dashboard Routes

### `GET /`
* **Purpose**: Renders the main custom companion web dashboard interface (`evelyn_ui/index.html`).

---

## 5. Developer & Review Queue APIs (Interactive Triaging)

Endpoints driving the cards in `dev.html` to manage memories during idle-time background extractions:

### `GET /api/heavy_tasks`
* **Purpose**: Returns real-time status of all heavy background tasks (`extractor`, `consolidator`, `procedure_consolidator`, `profile_evolver`, `tag_librarian`, `refresh_memory`, `sync`, `vault_map`), mutual exclusion lock state, runtime timers, and phase descriptions. Includes specialized domain diagnostics (`sub_status`), completion summaries (`summary`), and error notes (`error`/`diagnostics`) for all tasks, alongside `profile_evolver` per-document execution status codes (`doc_statuses`: `PROPOSAL_STAGED`, `NO_CORE_CHANGES`, `BELOW_THRESHOLD`, `COOLDOWN_ACTIVE`, `PENDING_EXISTS`, `INTERRUPTED_SAVED`, `MODEL_ERROR`).

### `GET /api/review/extractions`
* **Purpose**: Retrieves the queue of newly discovered assertions staged in `evelyn_memory.db` by [[fact_extractor.py]].

### `POST /api/review/extractions/{id}/{action}`
* **Purpose**: Action triage on a staged memory entry.
* **Payload**: Optional JSON body (`EditEntryRequest`) carrying updated fields (`category`, `subject`, `observation`, `tags`).
* **Actions**:
  * `approve`: Commits the entry to `status='live'` and triggers a memory refresh.
  * `delete`: Hard-deletes the entry and propagates the removal to any pending proposal `source_ids` lists via `remove_source_id_from_pending_proposals`.
  * `edit`: Updates the given fields. If the entry is currently `extracted`, it is promoted to `live` and a memory refresh is triggered. If the entry is already `live` (e.g., a source entry within a profile update proposal), only the field values are updated — status is not touched.

### `GET /api/persona/{filename}`
* **Purpose**: Fetches the current content of a core persona file (`Evelyn_Narrative_Persona.md`, `Ricky_Narrative_Profile.md`, or `System_Directives.md`) to display side-by-side or line-by-line diffs.
* **Returns**: Plain text markdown file content.

### `GET /api/review/proposals`
* **Purpose**: Retrieves active context consolidation and profile update proposals staged by [[fact_consolidator.py]] and [[profile_evolver.py]].

### `POST /api/review/proposals/{id}/{action}`
* **Purpose**: Action triage on proposals.
* **Payload**: Optional JSON body (`ProposalActionRequest`) carrying `modified_text` (str) and/or `source_id` (int).
* **Actions**:
  * `approve`: Executes the proposal based on type:
    * `profile_update` — writes `modified_text` (or the stored `merged_observation` if none provided) to the target persona file on disk, stamps `last_evolved_at` on all source entries, resets the per-document evolution cooldown to the approval timestamp (prevents immediate re-evaluation on the next idle cycle), runs `update_frontmatter.py`, and marks the proposal applied.
    * `merge` / `supersede` — deletes source entries and inserts the merged fact (using `modified_text` if provided).
    * `split` — deletes the source compound entry and inserts decomposed atomic child context facts parsed from `final_text` as YAML/JSON.
    * `recategorize` — moves source entries to `suggested_category`. `modified_text` is accepted but unused (no document is written).
    * `procedure_merge` — deletes source procedures and inserts a new consolidated procedure parsed from `final_text` as YAML.
  * `deny`: Rejects the proposal (`reject_proposal`). Deleted from the queue.
  * `unlink_source`: Removes the entry identified by `source_id` from this proposal's `source_ids` list without deleting the entry itself.

### `POST /api/context/split_preview`
* **Purpose**: Decompose a compound or over-merged context entry into atomic child entries with LLM assistance and Vector RAG taxonomy tagging.
* **Payload**: `SplitPreviewRequest` JSON: `{"entry_id": int, "observation": "string", "category": "string", "subject": "string", "tags": "string"}`.
* **Response**: `{"original": {...}, "splits": [{"category": "Cat05-R", "subject": "Ricky", "observation": "...", "tags": "...", "suggested_tags": [...], "alignment_label": "Aligned", "novelty_score": 0.22}, ...]}`.

### `POST /api/context/split_apply`
* **Purpose**: Atomically soft-delete a compound parent context entry and insert the verified atomic child entries.
* **Payload**: `SplitApplyRequest` JSON: `{"source_id": 123, "entries": [{"category": "Cat05-R", "subject": "Ricky", "observation": "...", "tags": "..."}]}`.
* **Response**: `{"status": "ok", "new_ids": [124, 125]}`. Triggers a background memory refresh automatically.

### `GET /api/review/procedures`
* **Purpose**: Retrieves all extracted procedures staged in `evelyn_memory.db` by [[fact_extractor.py]] that are pending review (`status='extracted'`).

### `POST /api/review/procedures/{id}/{action}`
* **Purpose**: Action triage on a staged procedure.
* **Payload**: Optional JSON body (`ProcedureReviewBody`) carrying edits to trigger pattern, steps, pitfalls, verification, or tags.
* **Actions**:
  * `approve`: Commits the procedure (optionally with edits) and marks it `status='live'` so it is active in the RAG retrieval pipeline.
  * `deny`: Soft-deletes the procedure by updating its status to `archived`.


---

## 6. Deep Research APIs

Endpoints driving the background research engine and the interactive developer dashboard:

### `POST /research/start`
* **Purpose**: Launches an asynchronous background deep research run on a given topic.
* **Payload**: `ResearchStartRequest` JSON:
  ```json
  {
    "query": "research topic or specific question",
    "scope": "standard" 
  }
  ```
  *(Scopes: `quick` ≈ 3-5 sources, `standard` ≈ 10-15 sources, `deep` ≈ 20+ sources with custom task index).*
* **Returns**: Success message indicating the research has been launched.

### `GET /research/status/{task_id}`
* **Purpose**: Polls the real-time execution state of a specific research task.
* **Returns**: JSON state dictionary showing `status` (`pending`, `planning`, `searching`, `evaluating`, `synthesizing`, `done`, `paused`, `cancelled`, `error`), `current_step`, `orchestrator_turns`, `sources_processed`, `confidence_score`, and error messages if applicable.

### `GET /research/report/{task_id}`
* **Purpose**: Retrieves the compiled Markdown report compiled by the synthesis engine.
* **Returns**: JSON object containing the `report` text.

### `GET /research/list`
* **Purpose**: Retrieves a merged, deduplicated list of all active/completed research tasks (from `data/research/*/state.json`) **and** pending queued items (from `data/research/queue.json`), sorted by creation date.
* **Side effect**: On each call, auto-purges queue items whose queries semantically duplicate an already-started task (Jaccard word-overlap ≥ 0.45), permanently cleaning `queue.json` on disk.
* **Returns**: JSON list of task state dicts. Queued items use temporary IDs (`queued_0`, `queued_1`, …) with `status: "queued"` and `current_step: "queued"`.

### `POST /research/cancel/{task_id}`
* **Purpose**: Safe cancellation trigger. For active tasks, sets status to `cancelled` on disk and in memory. For queued items (IDs starting with `queued_`), pops the item directly from `queue.json` on disk. The orchestrator checks status at the start of each execution turn to terminate safely and release VRAM.

### `POST /research/resume/{task_id}`
* **Purpose**: Safe resume and retry trigger. Re-spawns the background subprocess (`research_engine.py`) completely silently (using `CREATE_NO_WINDOW`) for any paused, cancelled, or failed research task to resume execution exactly where it was interrupted.

### `POST /research/start-now/{task_id}`
* **Purpose**: Force-start a queued or paused research task immediately, bypassing idle-time scheduling.
* **Queued items** (`task_id` starts with `queued_`): Pops the item at the given index from `queue.json` on disk and immediately invokes `start_research()`, obeying the same mutual-exclusion logic used by the idle loop (cancels any in-flight consolidation/extraction to free VRAM first).
* **Paused / cancelled / error tasks** (real `task_id`): Delegates to `resume_research_task()` to re-spawn the subprocess in-place.
* **UI behaviour**: Renders as an amber **▶ Start Now** button on `queued` and `paused` cards, alongside the Cancel button (and Resume for paused). Absent from active, done, error, or cancelled cards.

### `POST /research/guide/{task_id}`
* **Purpose**: Inject generic user-defined guidance into a stalled research task that exhausted its search depth without meeting confidence thresholds.
* **Payload**: `GuideRequest` JSON: `{"guidance": "string"}`
* **Action**: Injects the guidance string into the task's gaps file, resets the search depth, sets status to `pending`, and immediately resumes the task subprocess so it can retry the active sub-question with the new hints.

### `POST /research/guide/{task_id}/rewrite`
* **Purpose**: Submit an explicit, manual rewrite for a single low-confidence sub-question.
* **Payload**: `SQRewriteRequest` JSON: `{"sq_id": "string", "new_question": "optional string", "new_search_query": "optional string"}`
* **Action**: Updates the sub-question text and/or search query in state, clears its gaps, resets its depth to `0`, and sets it to `pending`. Does NOT resume the background process.

### `POST /research/guide/{task_id}/finalize`
* **Purpose**: Signal that all manual sub-question rewrites are complete.
* **Action**: Finds the first pending sub-question, sets it as the active index, and resumes the background research subprocess to execute the new queries.

### `POST /research/delete/{task_id}`
* **Purpose**: Permanently destroy a research task and all its disk/memory artifacts.
* **Action**: If active, forcefully terminates the subprocess, implements a file-lock retry loop, recursively deletes the task folder from `cfg.RESEARCH_DATA_DIR`, and evicts it from the server's tracking dictionary. Used by the "Remove" button in the dashboard.

## Terminal Agency (Hermes Tier 3 #9)

### `GET /api/terminal/pending`
* **Purpose**: Retrieve all staged terminal commands and file writes awaiting user approval.
* **Response**: A JSON array of pending actions with their metadata (id, type, command/file_path, cwd, etc.), excluding raw file contents for list-view performance.

### `POST /api/terminal/approve/{approval_id}`
* **Purpose**: Approve and execute a pending command or write operation.
* **Response**: `{"status": "ok", "result": "output_string"}`. The command output or file write result is returned and sent back to the agentic loop.

### `POST /api/terminal/deny/{approval_id}`
* **Purpose**: Deny and discard a pending command or write operation.
* **Response**: `{"status": "ok"}`.

### `POST /api/terminal/status`
* **Purpose**: Query the execution/approval status of multiple approval IDs in bulk.
* **Payload**: `{"ids": ["id1", "id2", ...]}`
* **Response**: A JSON object mapping each requested approval ID to its current status details (status, type, metadata), excluding the raw file content payloads.


[evelyn_server.py]: ../evelyn_server.py "evelyn_server.py"
[query_reformulator.py]: ../Evelyn/tools/query_reformulator.py "query_reformulator.py"
[chroma_rag.py]: ../Evelyn/tools/chroma_rag.py "chroma_rag.py"
[context_manager.py]: ../Evelyn/tools/context_manager.py "context_manager.py"
[refresh_memory.py]: ../Evelyn/tools/refresh_memory.py "refresh_memory.py"
[ingest_obsidian_knowledge.py]: ../Evelyn/tools/ingest_obsidian_knowledge.py "ingest_obsidian_knowledge.py"
[vault_indexer.py]: ../Evelyn/tools/vault_indexer.py "vault_indexer.py"
[tts_server.py]: ../services/tts/tts_server.py "tts_server.py"
[fact_extractor.py]: ../Evelyn/tools/fact_extractor.py "fact_extractor.py"
[fact_consolidator.py]: ../Evelyn/tools/fact_consolidator.py "fact_consolidator.py"
[profile_evolver.py]: ../Evelyn/tools/profile_evolver.py "profile_evolver.py"
