---
title: endpoints.md
date created: 2026-02-26 20:05:15
date modified: 2026-05-25 21:01:58
tags: api, endpoints, routing, backend, local_server, evelyn
---

# Evelyn Server API Endpoints

This document is the single source of truth for the custom REST and Server-Sent Events (SSE) API endpoints exposed by the **Evelyn Engine** backend ([[evelyn_server.py]]). All legacy Open WebUI pathways have been completely retired.

---

## 1. Chat & Conversation Management

### `GET /status`
* **Purpose**: Performs a quick health check on the server runtime and connection statuses of downstream local services (Ollama, TTS server, Image server).
* **Returns**: JSON object showing service statuses.

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
* **Limits**: Honors `MAX_HISTORY_MESSAGES = 30` config cap for model ingestion, but UI retrieves full scrollable history.

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

### `GET /api/review/proposals`
* **Purpose**: Retrieves active context consolidation proposals staged by [[fact_consolidator.py]].

### `POST /api/review/proposals/{id}/{action}`
* **Purpose**: Action triage on duplication proposals.
* **Actions**: `approve` (merges facts), `reject` (retains staging), `keep_both` (preserves both without deletion).

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
* **Purpose**: Retrieves a historical list of all deep research tasks in the queue and completed library, sorted by creation date.
* **Returns**: JSON list of task state dicts.

### `POST /research/cancel/{task_id}`
* **Purpose**: Safe cancellation trigger. Sets task status to `cancelled` on disk. The orchestrator checks this status at the start of each execution turn to terminate safely and release VRAM.