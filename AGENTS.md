---
title: AGENTS.md
date created: 2026-08-22 15:53:58
date modified: 2026-08-28 14:41:24
tags: [agent-rules, guidelines, operations, protocol, evelyn]
---
# Evelyn Workspace Agent Rules

> Navigation: [[README.md]] · [[engine_architecture.md]] · [[quality-review.md]] · [[ROADMAP.md]] · [[CHANGELOG.md]]

## 1. Python Environment & Execution
- **Virtual Environment**: Always use the project virtual environment at `/home/rathius/evelyn/venv/bin/python` and `/home/rathius/evelyn/venv/bin/pytest`. Never invoke `/usr/bin/python3` directly for workspace tasks or test runs.
- **PYTHONPATH**: Prefix commands with `PYTHONPATH=.` when executing scripts or running tests from the workspace root (e.g. `PYTHONPATH=. /home/rathius/evelyn/venv/bin/pytest Evelyn/tests`).

## 2. Database & Vector Operations (MCP Server & CLI)
- **Primary Method (MCP Server)**: Use the `evelyn-sqlite` MCP tools:
  - **SQLite**: `list_databases`, `list_tables`, `describe_table`, `query_database`
  - **ChromaDB**: `list_chroma_collections`, `query_chroma`, `get_chroma_status`
  - **FastAPI / Telemetry**: `get_server_status`, `get_heavy_tasks`, `get_pending_reviews`, `get_ollama_status`
- **Secondary Method (CLI)**: When running terminal commands, use the native `sqlite3` binary with JSON/table formatting:
  ```bash
  sqlite3 -json /home/rathius/evelyn/data/<db_name>.db "<SELECT_QUERY>"
  sqlite3 /home/rathius/evelyn/data/<db_name>.db ".schema <table_name>"
  ```
- **No Ad-Hoc Inline Scripts**: Do not write unvalidated inline `python3 -c "import sqlite3..."` shell commands to guess column names or print unbounded stdout dumps. Check table schemas via `describe_table` or `.schema` before querying.
- **Data Hygiene & Test Cleanup**: Dummy test records, mock entries, or test proposals created during verification must be purged immediately from production databases (`evelyn_chat.db`, `evelyn_memory.db`, `evelyn_vault.db`) once testing is complete.
- **Databases Map**:
  - `chat`: `/home/rathius/evelyn/data/evelyn_chat.db`
  - `memory`: `/home/rathius/evelyn/data/evelyn_memory.db`
  - `vault`: `/home/rathius/evelyn/data/evelyn_vault.db`
  - `health`: `/home/rathius/evelyn/data/health/health_connect.db`

## 3. Documentation & Metadata Maintenance
- **ROADMAP.md**: Single source of truth for milestones. Keep entries concise and milestone-oriented (1–2 sentences). Do NOT append verbose changelogs, function trace dumps, or commit logs (Git history serves as the detailed log). Keep completed tasks (`- [x]`) grouped at the top of each section and pending items (`- [ ]`) at the bottom.
- **Reference Docs**: Keep `reference/engine_architecture.md`, `reference/endpoints.md`, `requirements.txt`, and `SETUP_GUIDE.md` in sync whenever code contracts change.
- **File Metadata & Frontmatter**: Run `python scripts/update_frontmatter.py "<filepath>"` after modifying files to ensure timestamps and headers stay accurate.

## 4. Identity Parameterization & Memory Taxonomy
- **Config as Single Source of Identity**: Identity variables (`ASSISTANT_NAME`, `USER_NAME`, `SUBJECT_CODE_USER = "U"`, `SUBJECT_CODE_ASSISTANT = "A"`) and vault write roots/boundaries live in `evelyn_config.py`. Never hardcode raw persona/operator names or absolute vault subtrees into engine tools, prompts, or tests.
- **Fast Memory Taxonomy**: Categories strictly follow `Cat##-U` for User facts and `Cat##-A` for Assistant facts. The database `subject` column stores the configured entity name (`cfg.USER_NAME` / `cfg.ASSISTANT_NAME`).
- **Templates & Private Scripts**: Generic open-source markdown templates reside in `templates/`. Private/machine-specific scripts belong in `scripts/personal/` (gitignored).

## 5. Versioning, Changelog & Database Migrations
- **Zero-Padded Versioning (`000.000.000`)**: All version numbers must strictly use 3-digit zero-padding (`MAJOR.MINOR.PATCH`, e.g. `000.004.000`). Canonical version is defined in `Evelyn/version.py`.
- **Database Migration Framework**: All database schema changes (table creation, column additions, indexes) and structural data transformations (field splitting, backfills, cross-database data moves) must be registered as a versioned migration step in `Evelyn/tools/db_migrator.py` (`MIGRATIONS` registry) and executed via `scripts/migrate_db.py`.
- **Immutability Rule**: Once a migration version (`000.004.00X`) is committed and applied, its migration code and SQL are **strictly immutable**. Any corrective schema changes, data patches, or structural adjustments must be registered in a **new, incremented migration step** (`000.004.00X+1`).
- **No Out-of-Band Schema Mutations**: Modifying production database schemas or transforming database structures ad-hoc via inline scripts or unversioned queries is strictly forbidden.
- **Changelog & Versioning Maintenance**: Every functional code modification (features, bugfixes, architectural adjustments, database migrations) requires an incremented canonical version in `Evelyn/version.py` (`MAJOR.MINOR.PATCH`, e.g. `000.004.001`) and a documented entry in `CHANGELOG.md` detailing added capabilities, fixed issues, changed behaviors, and migrations applied. Keep `ROADMAP.md` concise and milestone-oriented.

## 6. Service Verification & Process Management
- **TCP Port & Unit Binding Verification**: When verifying if services are running (Evelyn server, TTS server, Ollama, etc.), ALWAYS inspect by **TCP Port Binding** (`ss -tulpn` / `lsof -i:<port>`) or systemd status (`systemctl status <service>`). Never rely on loose process name matching (`python`) or stale PIDs.

## 7. Vault Note Formatting & Visual PKM Style
- **Default Visual PKM Standard**: All notes, guides, and Maps of Content (MOCs) in the Obsidian vault must adhere to the **Visual PKM / Digital Garden Dashboard** standard defined in `.agents/rules/vault-note-style.md`.
- **Key Elements**:
  - Structured YAML frontmatter with single-line flow arrays (`title`, `aliases: [...]`, `tags: [...]`, `date created`, `date modified`).
  - Executive Callout box (`[!ABSTRACT]`) beneath the title.
  - Thematic section anchor emojis in headers (e.g. 🪐, 🏜️, 🕯️, ⚡, 🏛️, 📊, 🎧, 🧭).
  - Mermaid charts (`mindmap`, `graph TD`, `graph LR`) for conceptual synthesis.
  - Comparative tables with high data density.
  - Deep bi-directional `[[WikiLinks]]` and a `## 🔗 Related Notes` footer.

## 8. Single Source of Truth & Function Reuse Protocol (DRY Codebase)
- **Mandatory Pre-Implementation Discovery**: Before introducing any new utility function, parser, string sanitizer, path resolver, or HTTP client wrapper, agents **must inspect existing canonical modules** in `Evelyn/tools/` (specifically `string_utils.py`, `path_utils.py`, `frontmatter_utils.py`, and `ollama_client.py`).
- **Canonical Utility Modules**:
  - `string_utils.py`: Text cleaning, thinking tag stripping (`strip_thinking_tags`, `clean_llm_gist`), title casing, slugification, and filename sanitization.
  - `path_utils.py`: Vault relative/absolute conversions with traversal security (`to_vault_relpath`, `to_vault_abspath`), path normalization, and ignore-list matching.
  - `frontmatter_utils.py`: Parsing (`parse_frontmatter`), rendering (`render_frontmatter`), in-place line updating (`update_frontmatter_field`), and file writes (`write_file_with_frontmatter`).
  - `ollama_client.py`: Local Ollama inference gateway (`query_ollama`, `query_ollama_json`, `get_ollama_status`).
- **Strict Anti-Duplication Rule**: Writing ad-hoc regex frontmatter parsers, inline `urllib.request` Ollama HTTP callers, duplicate `clean_gist()` / `slugify()` routines, or custom YAML list formatters across engine tools or scripts is strictly forbidden. Always import and reuse canonical functions.
