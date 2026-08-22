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
