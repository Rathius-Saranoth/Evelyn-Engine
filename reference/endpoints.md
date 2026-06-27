---
title: endpoints.md
date created: 2026-02-26 20:05:15
date modified: 2026-06-27 08:47:54
tags: api, endpoints, routing, backend, local_server, evelyn
---

# Evelyn Server API Endpoints

This document is the single source of truth for the custom REST and Server-Sent Events (SSE) API endpoints exposed by the **Evelyn Engine** backend ([[evelyn_server.py]]). All legacy Open WebUI pathways have been completely retired.

---

## 1. Chat & Conversation Management

### `GET /status`
* **Purpose**: Performs a quick health check on the server runtime and connection statuses of downstream local services (Ollama, TTS server, Image server).
* **Returns**: JSON object showing service statuses, active model parameters, and the active context limit (`num_ctx`).

### `POST /chat`
* **Purpose**: Processes a new conversational message from the UI.
* **Flow**:
  1. Runs [[query_reformulator.py]] for conversational keywords.
  2. Executes semantic vector search via [[chroma_rag.py]] to extract relevant vault gists.
  3. Query matches dense facts from [[context_manager.py]].
  4. Returns a stream of **Server-Sent Events (SSE)** including the thinking trace and the dynamic tool-call updates.

### `POST /regenerate`
* **Purpose**: Triggers a regeneration of the latest response in the chat chain.
* **Returns**: Streamed SSE assistant message.

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
* **Sequence**: Map Vault Index $\rightarrow$ Ingest Obsidian Knowledge $\rightarrow$ Ingest Gists.
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

### `POST /tts`
* **Purpose**: Sends text to [[tts_server.py]] to generate expressive natural speech audio streams.

---

## 4. UI Dashboard Routes

### `GET /`
* **Purpose**: Renders the main custom companion web dashboard interface (`evelyn_ui/index.html`).

---

## 5. Developer & Review Queue APIs (Interactive Triaging)

Endpoints driving the cards in `dev.html` to manage memories during idle-time background extractions:

### `GET /api/review/extractions`
* **Purpose**: Retrieves the queue of newly discovered assertions staged in `evelyn_memory.db` by [[fact_extractor.py]].

### `POST /api/review/extractions/{id}/{action}`
* **Purpose**: Action triage on a staged memory.
* **Actions**: `approve` (commits fact), `reject` (drops fact).

### `GET /api/persona/{filename}`
* **Purpose**: Fetches the current content of a core persona file (`Evelyn_Narrative_Persona.md`, `Ricky_Narrative_Profile.md`, or `System_Directives.md`) to display side-by-side or line-by-line diffs.
* **Returns**: Plain text markdown file content.

### `GET /api/review/proposals`
* **Purpose**: Retrieves active context consolidation and profile update proposals staged by [[fact_consolidator.py]] and [[profile_evolver.py]].

### `POST /api/review/proposals/{id}/{action}`
* **Purpose**: Action triage on proposals.
* **Actions**:
  * `approve`: Merges facts (for consolidations) or writes the approved markdown changes back to disk and runs `update_frontmatter.py` (for profile updates).
  * `reject` / `deny`: Rejects the proposal and deletes it.
  * `keep_both`: (For consolidations) preserves both context entries without deleting.


---

## 6. Deep Research APIs (Background Search & Synthesis)

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
* **Payload**: `SQRewriteRequest` JSON: `{"sq_id": "string", "new_question": "string"}`
* **Action**: Updates the sub-question text in state, clears its gaps, resets its depth to `0`, and sets it to `pending`. Does NOT resume the background process.

### `POST /research/guide/{task_id}/finalize`
* **Purpose**: Signal that all manual sub-question rewrites are complete.
* **Action**: Finds the first pending sub-question, sets it as the active index, and resumes the background research subprocess to execute the new queries.

### `POST /research/delete/{task_id}`
* **Purpose**: Permanently destroy a research task and all its disk/memory artifacts.
* **Action**: If active, forcefully terminates the subprocess, implements a file-lock retry loop, recursively deletes the task folder from `cfg.RESEARCH_DATA_DIR`, and evicts it from the server's tracking dictionary. Used by the "Remove" button in the dashboard.