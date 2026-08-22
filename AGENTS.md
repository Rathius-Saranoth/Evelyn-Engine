# Evelyn Workspace Agent Rules

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
- **Databases Map**:
  - `chat`: `/home/rathius/evelyn/data/evelyn_chat.db`
  - `memory`: `/home/rathius/evelyn/data/evelyn_memory.db`
  - `vault`: `/home/rathius/evelyn/data/evelyn_vault.db`
  - `health`: `/home/rathius/evelyn/data/health/health_connect.db`

## 3. Documentation & Roadmap Maintenance
- **ROADMAP.md**: Single source of truth for milestones. Keep entries concise and milestone-oriented (1–2 sentences). Do NOT append verbose changelogs, function trace dumps, or commit logs (Git history serves as the detailed log). Keep completed tasks (`- [x]`) grouped at the top of each section and pending items (`- [ ]`) at the bottom.
- **Reference Docs**: Keep `reference/engine_architecture.md`, `reference/endpoints.md`, `requirements.txt`, and `SETUP_GUIDE.md` in sync whenever code contracts change.

## 4. Identity Parameterization & Memory Taxonomy
- **Config as Single Source of Identity**: Identity variables (`ASSISTANT_NAME`, `USER_NAME`, `SUBJECT_CODE_USER = "U"`, `SUBJECT_CODE_ASSISTANT = "A"`) and vault write roots/boundaries live in `evelyn_config.py`. Never hardcode raw persona/operator names or absolute vault subtrees into engine tools, prompts, or tests.
- **Fast Memory Taxonomy**: Categories strictly follow `Cat##-U` for User facts and `Cat##-A` for Assistant facts. The database `subject` column stores the configured entity name (`cfg.USER_NAME` / `cfg.ASSISTANT_NAME`).
- **Templates & Private Scripts**: Generic open-source markdown templates reside in `templates/`. Private/machine-specific scripts belong in `scripts/personal/` (gitignored).

## 5. Versioning, Changelog & Database Migrations
- **Zero-Padded Versioning (`000.000.000`)**: All version numbers must strictly use 3-digit zero-padding (`MAJOR.MINOR.PATCH`, e.g. `000.004.000`). Canonical version is defined in `Evelyn/version.py`.
- **Database Migration Framework**: All database schema changes (table creation, column additions, indexes) and structural data transformations (field splitting, backfills, cross-database data moves) must be registered as a versioned migration step in `Evelyn/tools/db_migrator.py` (`MIGRATIONS` registry) and executed via `scripts/migrate_db.py`.
- **Immutability Rule**: Once a migration version (`000.004.00X`) is committed and applied, its migration code and SQL are **strictly immutable**. Any corrective schema changes, data patches, or structural adjustments must be registered in a **new, incremented migration step** (`000.004.00X+1`).
- **No Out-of-Band Schema Mutations**: Modifying production database schemas or transforming database structures ad-hoc via inline scripts or unversioned queries is strictly forbidden.
- **Changelog Maintenance**: Major and Minor version milestones (`000.X00.000`) require a documented entry in `CHANGELOG.md` detailing added capabilities, changed behaviors, architectural milestones, and migrations applied. Keep `ROADMAP.md` concise and milestone-oriented.
