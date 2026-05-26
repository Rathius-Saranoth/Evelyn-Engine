---
title: endpoints.md
date created: 2026-02-26 20:05:15
date modified: 2026-05-25 20:42:13
tags: api, endpoints, routing, backend, local_server, evelyn
---

# Evelyn Server API Endpoints

This document is the single source of truth for the custom REST and Server-Sent Events (SSE) API endpoints exposed by the **Evelyn Engine** backend ([`evelyn_server.py`](file:///c:/Projects/LocalAI/evelyn_server.py)). All legacy Open WebUI pathways have been completely retired.

---

## 1. Chat & Conversation Management

### `GET /status`
* **Purpose**: Performs a quick health check on the server runtime and connection statuses of downstream local services (Ollama, TTS server, Image server).
* **Returns**: JSON object showing service statuses.

### `POST /chat`
* **Purpose**: Processes a new conversational message from the UI.
* **Flow**:
  1. Runs [`query_reformulator.py`](file:///c:/Projects/LocalAI/Evelyn/tools/query_reformulator.py) for conversational keywords.
  2. Executes semantic vector search via [`chroma_rag.py`](file:///c:/Projects/LocalAI/Evelyn/tools/chroma_rag.py) to extract relevant vault gists.
  3. Query matches dense facts from [`context_manager.py`](file:///c:/Projects/LocalAI/Evelyn/tools/context_manager.py).
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
* **Purpose**: Reads generated media assets (such as FLUX images and journal drafts) directly in the UI.

---

## 2. Ingestion & Background Task Orchestration

### `POST /refresh_memory`
* **Purpose**: The master ingestion trigger. Executes [`refresh_memory.py`](file:///c:/Projects/LocalAI/Evelyn/tools/refresh_memory.py) as an asynchronous subprocess.
* **Sequence**: Map Vault Index $\rightarrow$ Ingest Obsidian Knowledge $\rightarrow$ Ingest Gists.
* **Monitoring**: Outputs real-time phase tags (`[PHASE_START:]`, `[PHASE_DONE:]`) to drive progress loops.

### `GET /task_status/{task_name}`
* **Purpose**: Polls the status of active background processes (e.g., `refresh_memory`, `vault_map`, `sync`).
* **States**: `running`, `done`, `error`, `unknown`.

### `POST /sync`
* **Purpose**: Direct trigger for the Knowledge Sync pipeline (`ingest_obsidian_knowledge.py`).

### `POST /vault_map`
* **Purpose**: Direct trigger for SQLite directory index mapping (`vault_indexer.py`).

---

## 3. Local Inference Bridges

### `POST /tts`
* **Purpose**: Sends text to [`tts_server.py`](file:///c:/Projects/LocalAI/services/tts/tts_server.py) to generate expressive natural speech audio streams.

---

## 4. UI Dashboard Routes

### `GET /`
* **Purpose**: Renders the main custom companion web dashboard interface (`evelyn_ui/index.html`).

---

## 5. Developer & Review Queue APIs (Interactive Triaging)

Endpoints driving the cards in `dev.html` to manage memories during idle-time background extractions:

### `GET /api/review/extractions`
* **Purpose**: Retrieves the queue of newly discovered assertions staged in `evelyn_memory.db` by [`fact_extractor.py`](file:///c:/Projects/LocalAI/Evelyn/tools/fact_extractor.py).

### `POST /api/review/extractions/{id}/{action}`
* **Purpose**: Action triage on a staged memory.
* **Actions**: `approve` (commits fact), `reject` (drops fact).

### `GET /api/review/proposals`
* **Purpose**: Retrieves active context consolidation proposals staged by [`fact_consolidator.py`](file:///c:/Projects/LocalAI/Evelyn/tools/fact_consolidator.py).

### `POST /api/review/proposals/{id}/{action}`
* **Purpose**: Action triage on duplication proposals.
* **Actions**: `approve` (merges facts), `reject` (retains staging), `keep_both` (preserves both without deletion).