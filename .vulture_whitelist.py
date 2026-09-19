# .vulture_whitelist.py
# Framework whitelist for Vulture dead-code auditing in Evelyn Engine
# date created: 2026-09-06 18:45:00
#
# Standard Vulture mock references: expressions of the form `_.symbol_name`
# inform Vulture that `symbol_name` is an external/dynamic hook or property.

from typing import Any

_: Any = None

# SQLite connection attributes
_.row_factory

# Dynamic tool definitions and registries
_.CORE_TOOL_DEFINITIONS
_.MODEL_TOOL_DEFINITIONS

# FastAPI / Server models and background tasks
_.FinalizeGuidanceRequest

# Database functions called dynamically or conditionally
_.list_unindexed_media
_.increment_entry_observed
_.count_entries
_.count_entries_by_category

# Google API client helpers & Oura client
_.get_tasks_service
_.get_docs_service
_.get_sheets_service
_.get_sessions

# Task manager lifecycle and inspection
_.HEAVY_TASK_KEYS
_.get_boot_ts
_.peek_next_idle_task

# Prompt builders and format librarian
_.MANDATORY_KEYS
_.build_necessity_check_prompt
_.EVELYN_DIR

# Configuration constants for environment bindings or roadmaps
_.ENV_FILE_PATH
_.VERSION_INFO
_.SERVER_HOST
_.SQLITE_PRAGMAS
_.VAULT_WRITE_IGNORE
_.DEBUG_TOOL_FULL
_.SHOW_TOOL_LOOP_THINKING
_.TASK_QUEUE_STATE_FILE
_.TERMINAL_DEFAULT_TIMEOUT
_.TERMINAL_ENABLED
_.SUMMARY_MAX_WORDS
_.SUMMARY_OVERLAP
_.SUMMARY_WINDOW_SIZE
_.GCAL_CREDENTIALS_PATH
_.GDRIVE_CREDENTIALS_PATH
_.GTASKS_CREDENTIALS_PATH
_.RESEARCH_CONFIDENCE_THRESHOLD
_.RESEARCH_MAX_SUB_QUESTIONS
_.RESEARCH_MODEL
_.TAG_LIBRARIAN_BATCH_SIZE
_.TAG_LIBRARIAN_ENABLED
_.TAG_LIBRARIAN_FORMAT_RULES
_.TAG_LIBRARIAN_IDLE_THRESHOLD
_.CONTEXT_DIR

# Standalone scripts, services, and MCP tools consumers
_.enqueue_remap
_.find_semantic_neighbors
_.move_document
_.get_all_entities
_.get_ollama_status
_.STT_MODEL_SIZE
_.STT_DEVICE
_.STT_COMPUTE_TYPE
_.rollback_db

# Public library primitives, dataclass telemetry, and utility helpers
_.duration_ms
_.register_provider
_.record_media_share
_.record_system_alert
_.link_rag_telemetry_to_message
_.tokenize_wikilink
_.run_master_librarian_audit
_.run_master_librarian_audit_async
_.get_entry_document_evolutions
_.get_all_queued_fact_merge_ids
_.query_ollama_json
_.get_profile_filename
_.build_memory_context_envelope
_.get_idle_queue
_.acquire_next_idle_task
_.reset_alert_cache
_.is_valid_version
_.normalize_vault_path
_.append_context_log
_.update_context_log
_.run_semantic_tag_audit
_.run_semantic_tag_audit_async
_.fetch_next_document_for_tag_audit
_.update_document_tag_audit
