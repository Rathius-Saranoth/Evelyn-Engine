---
title: RECOMMENDED_TOOLING.md
date created: 2026-10-09 23:57:00
date modified: 2026-10-09 23:59:47
tags: [tooling, mcp, extensions, environment, recommendations, setup, evelyn]
---

# 🛠️ Recommended Tooling, IDE Extensions & MCP Ecosystem

> Navigation: [[README.md]] · [[SETUP_GUIDE.md]] · [[AGENTS.md]] · [[engine_architecture.md]] · [[evelyn-db-ops/SKILL.md]]

> [!ABSTRACT]
> This document defines the canonical specification for development host permissions, IDE extensions, and Model Context Protocol (MCP) servers across the Evelyn Engine ecosystem. It serves as both an onboarding guide for human developers and a persistent baseline for AI agents undergoing workspace migrations.

---

## 1. ⚙️ Host System Permissions & Git Configuration

To enable non-interactive automation, service management, and remote code synchronization, the host environment must meet the following criteria:

### 1.1 Non-Interactive Passwordless Sudo
Service management scripts (`restart_evelyn_services.sh`, `stop_evelyn_services.sh`, `start_evelyn_services.sh`, `graceful_stop.sh`) interact with `systemctl` and `journalctl`. Unattended agent execution requires passwordless sudo.

* **Configuration File**: `/etc/sudoers.d/99-evelyn-nopasswd`
* **Contents**:
  ```sudoers
  $USER ALL=(ALL) NOPASSWD: ALL
  ```
* **Validation**:
  ```bash
  sudo visudo -c
  sudo -n true && echo "Sudo is passwordless and non-interactive"
  ```

### 1.2 SSH-Based Git Operations
Remote repositories must be wired over SSH rather than HTTPS to prevent interactive credential prompts during agent backups or releases.

* **Key Generation**:
  ```bash
  ssh-keygen -t ed25519 -C "<user>@<host>" -f ~/.ssh/id_ed25519 -N ""
  ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts
  ```
* **Git Remote Configuration**:
  ```bash
  git remote set-url origin git@github.com:<OWNER>/<REPO>.git
  ```
* **Verification**:
  ```bash
  ssh -T -o BatchMode=yes git@github.com
  git push --dry-run origin main
  ```

---

## 2. 🧩 Recommended IDE Extensions

Workspace extension recommendations reside canonically in [`.vscode/extensions.json`](file:///home/rathius/evelyn/.vscode/extensions.json).

| Extension ID | Name | Role & Value in Evelyn |
| :--- | :--- | :--- |
| **`charliermarsh.ruff`** | Ruff | Ultra-fast in-editor linting and auto-formatting adhering to `pyproject.toml` standards. |
| **`ms-python.python`** | Python | Python language support, virtualenv interpreter resolution, and debugging. |
| **`humao.rest-client`** | REST Client | Interactive HTTP execution for [reference/evelyn_api.http](file:///home/rathius/evelyn/reference/evelyn_api.http) directly inside the IDE. |
| **`qwtel.sqlite-viewer`** | SQLite Viewer | Tabular spreadsheet inspection for `evelyn_chat.db`, `evelyn_memory.db`, `evelyn_vault.db`. |
| **`bierner.markdown-mermaid`** | Markdown Mermaid | Native rendering of Mermaid diagrams (`mindmap`, `graph TD`) in Visual PKM notes. |
| **`redhat.vscode-yaml`** | YAML Language Support | Syntax validation for frontmatter and system configurations. |
| **`mikestead.dotenv`** | DotENV | Syntax highlighting and environment variable tracking for local `.env` files. |

### CLI Installation Command
To install all recommended extensions non-interactively on the host:
```bash
antigravity-ide \
  --install-extension charliermarsh.ruff \
  --install-extension ms-python.python \
  --install-extension humao.rest-client \
  --install-extension qwtel.sqlite-viewer \
  --install-extension bierner.markdown-mermaid \
  --install-extension redhat.vscode-yaml \
  --install-extension mikestead.dotenv
```

---

## 3. 🌐 Model Context Protocol (MCP) Servers

MCP servers provide structured, tool-based interfaces for LLMs and agents to interact with host systems, databases, and APIs without terminal command guesswork.

Configuration resides in **`~/.gemini/config/mcp_config.json`** (template available at [`.agents/mcp_config.example.json`](file:///home/rathius/evelyn/.agents/mcp_config.example.json)).

### 3.1 `evelyn-sqlite` — Evelyn Engine MCP Server
* **Source Script**: [`scripts/sqlite_mcp_server.py`](file:///home/rathius/evelyn/scripts/sqlite_mcp_server.py)
* **Transport**: Stdio via project Python virtual environment (`venv/bin/python`).
* **Environment**: `PYTHONPATH=/path/to/evelyn` (or repository root), inherits `EVELYN_API_KEY` from `evelyn_config.py`.

#### Exposed Tool Capabilities (18 Tools):
1. **SQLite Database Operations**:
   * `list_databases` — Lists database aliases (`chat`, `memory`, `vault`, `media`, `health`), file paths, and MB sizes.
   * `list_tables(database)` — Lists all tables and exact row counts.
   * `describe_table(database, table_name)` — Schema, column types, primary keys, defaults, and index definitions.
   * `query_database(database, sql, limit)` — Safely executes read-only SQL (`SELECT`, `PRAGMA`, `EXPLAIN`, `WITH`).
2. **ChromaDB Vector Store**:
   * `list_chroma_collections` — Lists vector collections (`evelyn_memory`, `evelyn_tag_taxonomy`, etc.) and counts.
   * `query_chroma(query_text, collection_name, n_results)` — Semantic similarity search returning distance and metadata.
   * `get_chroma_status` — Inspects vector store health, write lock state, and pending sync queue items.
3. **Engine Telemetry & Cognition**:
   * `get_server_status` — FastAPI health, active models, context size, and thinking configs.
   * `get_thought_bubble` — Real-time ambient thought stream and latest cognitive impression.
   * `get_telemetry(metric)` — System metrics (`thinking`, `rag`, `vault_domains`, or `all`).
   * `get_heavy_tasks` — Live background pipeline states (Extractor, Consolidator, Evolver, Librarian).
   * `get_ollama_status` — VRAM model loads, context allocations, and running model IDs.
4. **Knowledge Review & Triage**:
   * `get_pending_reviews` — Full unified triage queue items.
   * `get_proposals(status, limit)` — Memory/fact and tag proposals filtered by status.
   * `review_proposal(proposal_id, action, feedback)` — Programmatically approves (`approve`) or rejects (`deny`) pending proposals with feedback.
5. **System Maintenance**:
   * `trigger_pipeline(pipeline_name)` — Triggers background maintenance pipelines (`memory_refresh`, `vault_sync`, `wal_checkpoint`).
6. **Terminal Agent Governance**:
   * `get_terminal_pending` — Lists commands/writes awaiting operator approval.
   * `respond_terminal_approval(approval_id, action)` — Approves or denies terminal actions.

---

### 3.2 `github` — GitHub MCP Server
* **Package**: `@modelcontextprotocol/server-github` via `npx`
* **Transport**: Stdio
* **Prerequisites**: Node.js 18+ and `npm`/`npx` installed on the host.
* **Authentication**: `GITHUB_PERSONAL_ACCESS_TOKEN` environment variable with classic `repo` scope.

#### Exposed Tool Capabilities (26 Tools):
* **Pull Requests**: `create_pull_request`, `get_pull_request`, `list_pull_requests`, `merge_pull_request`, `create_pull_request_review`, `get_pull_request_files`, `get_pull_request_status`, `update_pull_request_branch`, `get_pull_request_comments`, `get_pull_request_reviews`.
* **Issues**: `create_issue`, `get_issue`, `list_issues`, `update_issue`, `add_issue_comment`, `search_issues`.
* **Repositories & Branches**: `search_repositories`, `create_repository`, `fork_repository`, `create_branch`, `list_commits`.
* **Code & Files**: `get_file_contents`, `create_or_update_file`, `push_files`, `search_code`, `search_users`.

---

## 4. 📋 Migration & Verification Checklist

When migrating to a new machine or restoring the workspace environment, verify all subsystems in sequence:

```bash
# 1. Verify Non-Interactive Sudo
sudo -n true && echo "✔ Sudo OK"

# 2. Verify GitHub SSH Key Auth
ssh -T -o BatchMode=yes git@github.com

# 3. Verify Node & NPM Runtime
node -v && npx --version

# 4. Verify MCP Configuration Exists
cat ~/.gemini/config/mcp_config.json

# 5. Verify Python Virtualenv & Deterministic Code Hygiene
PYTHONPATH=. venv/bin/python scripts/check_code_hygiene.py

# 6. Verify Service Lifecycle
bash scripts/restart_evelyn_services.sh
```
