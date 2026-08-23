---
name: evelyn-db-ops
description: >-
  Inspect, query, and debug Evelyn's SQLite databases (chat history, memory/proposals, vault index, health connect).
  Activate when investigating database tables, retrieving recent messages/thinking traces, inspecting proposals, or debugging data schemas.
tags: skill, db, sqlite, mcp, query, debug, evelyn
title: SKILL.md
date created: 2026-08-23 08:04:51
date modified: 2026-08-23 08:04:51
---

# Evelyn Database Operations Skill

> Navigation: [[debug-chat-db.md]] · [[engine_architecture.md]] · [[AGENTS.md]] · [[README.md]]

This skill outlines how to inspect, query, and debug Evelyn's SQLite databases cleanly and without terminal noise or syntax errors.

---

## 1. Database Locations & Aliases

| Alias | Full Path | Primary Tables | Purpose |
| :--- | :--- | :--- | :--- |
| `chat` | `data/evelyn_chat.db` | `messages`, `calendar_events`, `message_metrics` | Chat history, assistant thinking traces, active conversation context |
| `memory` | `data/evelyn_memory.db` | `context_entries`, `proposals`, `procedures`, `schema_migrations` | Extracted facts, profile evolution proposals, consolidation history |
| `vault` | `data/evelyn_vault.db` | `vault_documents`, `master_tag_taxonomy`, `schema_migrations` | Indexed Obsidian vault notes, tag taxonomy, and metadata hashes |
| `media` | `data/evelyn_media.db` | `media_assets`, `chat_media_links`, `schema_migrations` | Binary media attachments, EXIF metadata, visual OCR captions |
| `health` | `data/health/health_connect.db` | `sleep_sessions`, `daily_metrics`, `heart_rate` | Health Connect & Oura Ring biometric data |

> [!NOTE]
> **Fast Memory Taxonomy**: Context entries use category codes `Cat##-U` for User facts (e.g. `Cat01-U` to `Cat16-U`) and `Cat##-A` for Assistant facts (e.g. `Cat01-A` to `Cat16-A`). The `subject` column stores the entity name dynamically configured in `evelyn_config.py`.

---

## 2. Using the `evelyn-sqlite` MCP Server

When available in the session toolset, use the structured MCP tools:

### SQLite Tools
1. **`list_databases`**: Returns database aliases, status, and file size in MB.
2. **`list_tables(database="memory")`**: Lists all tables and row counts.
3. **`describe_table(database="memory", table_name="proposals")`**: Inspects column types, nullability, defaults, primary keys, and index definitions.
4. **`query_database(database="memory", sql="SELECT id, type, status FROM proposals ORDER BY id DESC LIMIT 5")`**: Executes read-only queries with automatic JSON serialization.

### ChromaDB Vector Tools
1. **`list_chroma_collections`**: Lists all vector collections (`evelyn_memory`, `evelyn_tag_taxonomy`, etc.) with vector counts and distance metrics.
2. **`query_chroma(query_text="...", collection_name="evelyn_memory", n_results=5)`**: Performs semantic similarity vector search and returns nearest documents, metadata, and cosine distance scores.
3. **`get_chroma_status`**: Inspects write lock state (`.chroma_write.lock`) and pending sync queue items.

### FastAPI & System Telemetry Tools
1. **`get_server_status`**: Queries `https://localhost:7860/status` for server health, active model, and thinking configs.
2. **`get_heavy_tasks`**: Inspects background task states (Fact Extractor, Fact Consolidator, Profile Evolver, Tag Librarian, etc.).
3. **`get_pending_reviews`**: Retrieves pending triage items from the unified review queue.
4. **`get_ollama_status`**: Inspects active Ollama models loaded in VRAM (`/api/ps`) and context allocations.

---

## 3. Direct CLI Queries (`sqlite3`)

When running shell commands, use the native `sqlite3` CLI utility instead of ad-hoc python scripts:

```bash
# View table schema
sqlite3 /home/rathius/evelyn/data/evelyn_memory.db ".schema proposals"

# Query in JSON format
sqlite3 -json /home/rathius/evelyn/data/evelyn_chat.db \
  "SELECT id, role, substr(thinking, 1, 200) as think, ts FROM messages ORDER BY id DESC LIMIT 3;"

# Query in formatted table mode
sqlite3 -header -column /home/rathius/evelyn/data/evelyn_memory.db \
  "SELECT id, type, status, datetime(created_at, 'unixepoch', 'localtime') as created FROM proposals ORDER BY id DESC LIMIT 5;"
```

---

## 4. Python Environment Standard

When running Python scripts that interact with Evelyn's backend or databases:

```bash
# Always use the project venv
PYTHONPATH=. /home/rathius/evelyn/venv/bin/python <script_path>

# Run pytest
PYTHONPATH=. /home/rathius/evelyn/venv/bin/pytest Evelyn/tests
```
