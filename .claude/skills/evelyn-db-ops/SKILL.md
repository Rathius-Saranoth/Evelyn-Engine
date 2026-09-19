---
name: evelyn-db-ops
description: >-
  Inspect, query, and debug Evelyn's SQLite databases (chat history, memory/proposals,
  procedures, vault index, media, health connect). Use when investigating database tables,
  retrieving recent messages or thinking traces, inspecting proposals or procedures,
  checking RAG retrieval logs, or debugging data schemas.
---

# Evelyn Database Operations

The canonical instructions for this skill live at `.agents/skills/evelyn-db-ops/SKILL.md`
in this repository. Read that file and follow it — it is the single source of truth for
database aliases, table maps, and query conventions (AGENTS.md §8).

Key constraints that apply to every database interaction here:

- Prefer the `evelyn-sqlite` MCP tools (`list_databases`, `list_tables`, `describe_table`,
  `query_database`, `list_chroma_collections`, `query_chroma`, `get_server_status`).
- Falling back to the CLI, use the native `sqlite3` binary with `-json` or table output.
- Check the schema with `describe_table` or `.schema <table>` **before** querying. Never
  guess column names.
- Do not write ad-hoc inline `python3 -c "import sqlite3..."` probes or print unbounded
  stdout dumps.
- Purge any test or mock rows from production databases once verification is complete.
- Tests and verification scripts must never write to production databases, the live
  Obsidian vault, or active ChromaDB collections — use hermetic sandboxes.
