---
title: CHANGELOG.md
date created: 2026-08-22 15:53:28
date modified: 2026-08-23 08:01:50
tags: changelog, versioning, history, release-notes, evelyn
---
# 📜 Changelog

> Navigation: [[README.md]] · [[ROADMAP.md]] · [[AGENTS.md]] · [[engine_architecture.md]]

All notable changes to the Evelyn Engine are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to **3-digit zero-padded Semantic Versioning** (`000.000.000`).

## [000.005.013] - 2026-08-25 — *Consolidator Scan Continuity & Fast Exact Deduplication*

### Fixed & Enhanced
- **Consolidator Scan Continuity (`fact_consolidator.py`)**:
  - Fixed an issue where `_get_anchor_batch` reset the scan anchor pointer to `0` whenever a new fact modified category entry count `len(records)`. The pointer now wraps continuously (`anchor = anchor % n`) across consolidation passes.
  - Optimized `remediate_database_categories` to use SQL `NOT GLOB` filtering to avoid loading hundreds of thousands of historical proposal records into memory.
- **Fast Deterministic Deduplication Pre-Pass**:
  - Implemented `fast_deduplicate_exact_matches()` to detect and collapse exact and whitespace-normalized duplicate context entries into primary records, merging metadata and enqueuing vector deletions.
- **Targeted Cluster Consolidation**:
  - Merged 8 fragmented, redundant *Dungeon Crawler Carl* context facts in `Cat05-U` (IDs 2310, 2313, 2391, 2407, 2548, 2563, 2565, 2650) into a single, unified evolved entry (`#3972`), recorded proposal `#176816`, and updated Chroma vector embeddings.

---

## [000.005.012] - 2026-08-25 — *Interactive Feedback Comments & Vault Note Editor in DevUI*

### Added
- **Vault Note Reader & Editor API (`evelyn_server.py`)**:
  - Added `GET /api/vault/note` to retrieve full markdown content of any note within vault boundaries with path traversal protection.
  - Added `POST /api/vault/note` to write note edits directly to disk, update `vault_db`, and enqueue custodial re-indexing into `chroma_sync_queue`.
- **RAG Telemetry Content & Expandable Chunks**:
  - Captured full chunk content in `chroma_rag.py` retrieval logs (`rag_retrieval_log`).
  - Added expandable full chunk viewing and inline `✏️ Edit Note` buttons in `dev.html` telemetry inspector.
  - Added dedicated Vault Note Editor Modal in `dev.html` with in-browser editing and re-indexing.
- **Feedback Comments & Expandable Responses (`dev.html` & `index.html`)**:
  - Added `💬 Add/Edit Comment` action to `index.html` chat message actions.
  - Added expandable full response and thinking trace toggles to feedback cards in `dev.html`.
  - Added Feedback Explanation & Comment modal in `dev.html` for reviewing and amending ratings.
- **Automated Tests**:
  - Added `test_vault_note_endpoints` in [Evelyn/tests/test_feedback_and_rag_telemetry.py](file:///home/rathius/evelyn/Evelyn/tests/test_feedback_and_rag_telemetry.py).

---

## [000.005.011] - 2026-08-25 — *Thinking Level Telemetry & Metrics Exposure*

### Added
- **Thinking Effort & Source Exposure in Telemetry APIs (`evelyn_server.py`)**:
  - Added `GET /telemetry/thinking` endpoint providing aggregate counts of resolved thinking effort levels (`low`, `medium`, `high`, `max`), resolution sources (`heuristic`, `self_elected`, `tool_escalation`, `ui_override`), and recent message thinking audit logs.
  - Linked `think_effort` and `think_source` from `message_metrics` into `GET /history` and `GET /telemetry/feedback` payloads for review.
- **Automated Tests**:
  - Expanded [Evelyn/tests/test_feedback_and_rag_telemetry.py](file:///home/rathius/evelyn/Evelyn/tests/test_feedback_and_rag_telemetry.py) to assert thinking level telemetry collection and endpoint accuracy.

---

## [000.005.010] - 2026-08-25 — *Conversational Feedback & RAG Telemetry Logging System*

### Added
- **Database Schema Migrations (`000.005.010`)**:
  - Registered migration `000.005.010` in [Evelyn/tools/db_migrator.py](file:///home/rathius/evelyn/Evelyn/tools/db_migrator.py) creating `message_feedback` table in `evelyn_chat.db` (for 👍/👎 user rating and comments per assistant message).
  - Registered migration `000.005.010` creating `rag_retrieval_log` table in `evelyn_memory.db` (for real-time tracking of vector retrieval events, similarity distances, kept vs dropped threshold status, and source note paths).
- **RAG Telemetry Logging Interceptor (`chroma_rag.py`)**:
  - Implemented `log_rag_retrieval()`, `get_recent_rag_telemetry()`, and `link_rag_telemetry_to_message()` in [Evelyn/tools/chroma_rag.py](file:///home/rathius/evelyn/Evelyn/tools/chroma_rag.py).
  - Wired telemetry logging directly into `build_rag_context()` with fire-and-forget execution and zero added latency.
- **Server Feedback & Telemetry Endpoints (`evelyn_server.py`)**:
  - Added `save_or_update_feedback()` and `get_feedback_for_messages()` database helpers.
  - Added `POST /chat/feedback` (upsert user ratings), `GET /chat/feedback/{message_id}`, `GET /telemetry/rag` (recent retrieval logs), and `GET /telemetry/feedback` (feedback counts and satisfaction ratios).
  - Hydrated feedback state into `GET /history` messages payload.
  - Emitted `message_id` inside the final SSE `done` event chunk for immediate UI feedback binding.
- **Chat UI Feedback Toolbar (`index.html`)**:
  - Added interactive 👍 / 👎 buttons with toggle animation and active color states inside `.msg-actions` for assistant messages.
  - Restores saved feedback state upon loading conversation history.
- **DevUI Telemetry Dashboard (`dev.html`)**:
  - Added **📊 Telemetry & Feedback** dashboard tab displaying total ratings, upvote/downvote satisfaction rate, recent rated responses, and expandable RAG context retrieval inspection logs.
- **Automated Tests**:
  - Added test suite in [Evelyn/tests/test_feedback_and_rag_telemetry.py](file:///home/rathius/evelyn/Evelyn/tests/test_feedback_and_rag_telemetry.py) covering CRUD feedback operations, RAG telemetry logging, and API endpoints.

---

## [000.005.009] - 2026-08-23 — *Obsidian Vault List & Checklist Management System*

### Added
- **Obsidian Vault List Manager (`vault_list_manager.py`)**:
  - Implemented [Evelyn/tools/vault_list_manager.py](file:///home/rathius/evelyn/Evelyn/tools/vault_list_manager.py) providing offline-first list and checklist operations directly on markdown notes in the Obsidian Vault (`vault.root/Lists/`).
  - Added template-driven initialization supporting category templates ([templates/lists/groceries.md](file:///home/rathius/evelyn/templates/lists/groceries.md)) and generic lists ([templates/list_template.md](file:///home/rathius/evelyn/templates/list_template.md)).
  - Implemented item-first presentation format (`Item (Qty Unit)` / `Item (2x)`), category-aware section routing, intelligent quantity incrementing on existing items, fuzzy checkbox toggling (`- [ ]` $\leftrightarrow$ `- [x]`), item removal, and completed cleanup.
- **Model Function Calling Tool**:
  - Registered `manage_vault_list` in [Evelyn/tools/evelyn_tools.py](file:///home/rathius/evelyn/Evelyn/tools/evelyn_tools.py) `MODEL_TOOL_DEFINITIONS` and `TOOL_FUNCTIONS` supporting structured item objects, string lists, and flexible actions (`read`, `add`, `check`, `uncheck`, `remove`, `clear_completed`, `list_all`).
- **Configuration**:
  - Added `LISTS_DIR` path in [evelyn_config.py](file:///home/rathius/evelyn/evelyn_config.py).

---

## [000.005.008] - 2026-08-23 — *Google Tasks Integration & Dedicated Task Synchronizer*

### Added
- **Google Tasks Dedicated Synchronizer (`gtasks_sync.py`)**:
  - Implemented [Evelyn/tools/gtasks_sync.py](file:///home/rathius/evelyn/Evelyn/tools/gtasks_sync.py) providing offline-first task synchronization, SQLite caching, OAuth credential loading/refreshing, and full CRUD operations (`sync_gtasks`, `get_cached_tasks`, `create_gtask`, `complete_gtask`, `delete_gtask`).
  - Added [scripts/setup_gtasks.py](file:///home/rathius/evelyn/scripts/setup_gtasks.py) for interactive OAuth2 setup with automatic fallback to existing Google credentials.
- **Model Function Calling Tools**:
  - Registered `create_task`, `complete_task`, `delete_task`, `list_tasks`, and `sync_google_tasks` in [Evelyn/tools/evelyn_tools.py](file:///home/rathius/evelyn/Evelyn/tools/evelyn_tools.py) `MODEL_TOOL_DEFINITIONS` and `TOOL_FUNCTIONS`.
  - Updated `get_agenda` to present a unified schedule displaying both Google Calendar events and pending Google Tasks.
- **Server Background Sync & System Context**:
  - Added periodic background `_gtasks_sync_loop()` in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py) (running every 30 minutes).
  - Updated `get_upcoming_agenda_prompt_context()` in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py) to inject pending task notifications into the system prompt.
- **Database Schema Migration**:
  - Registered migration `000.005.008` (`create_tasks_table`) in [Evelyn/tools/db_migrator.py](file:///home/rathius/evelyn/Evelyn/tools/db_migrator.py) creating the `tasks` SQLite cache table in `evelyn_chat.db`.

---

## [000.005.007] - 2026-08-23 — *Graceful Service Shutdown Lifecycle & UPS Integration*

### Added
- **Graceful Stop Script & Workflow**:
  - Enhanced [scripts/stop_evelyn_services.sh](file:///home/rathius/evelyn/scripts/stop_evelyn_services.sh) with `--all`/`--with-ollama` and `--checkpoint-wal`/`--flush-wal` options for clean teardown.
  - Added dedicated workflow guide in [.agents/workflows/stop-services.md](file:///home/rathius/evelyn/.agents/workflows/stop-services.md) (`/stop-services`).
  - Added "Stop All Services (with Ollama & WAL flush)" task in [.vscode/tasks.json](file:///home/rathius/evelyn/.vscode/tasks.json).
- **Physical Environment UPS Hook (Sanctum)**:
  - Created `scripts/personal/ups_shutdown_hook.sh` and registered symlinks in `/etc/apcupsd/` (`doshutdown`, `failing`, `timeout`, `loadlimit`, `runlimit`, `emergency`) to safely stop services and checkpoint SQLite WAL journals when UPS signals power failure.

### Fixed
- **ChromaDB Queue Drain Deadline Hardening**:
  - Added strict per-item `deadline` parameter and checking to `drain_sync_queue()` and `flush_sync_queue()` in [chroma_rag.py](file:///home/rathius/evelyn/Evelyn/tools/chroma_rag.py).
  - Implemented automatic transaction rollback from `'processing'` to `'pending'` for unprocessed items when deadline expires mid-batch, preventing shutdown hangs.
- **FastAPI Lifespan Background Task Cancellation**:
  - Tracked all background `asyncio.Task` instances in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py) lifespan and cleanly cancelled/gathered them before calling `clean_shutdown_all_tasks()`.
- **Systemd Timeout Tuning**:
  - Tuned `TimeoutStopSec=15` in `/etc/systemd/system/evelyn.service`.

---

## [000.005.006] - 2026-08-23 — *Granular Source Entry Management for Merge Proposals*

### Added
- **Granular Source Item Editing & Unlinking in Proposals**:
  - Added support for editing, unlinking, and deleting individual source procedures directly on `procedure_merge` proposal cards in [dev.html](file:///home/rathius/evelyn/evelyn_ui/dev.html).
  - Extended `/api/review/procedures/{id}/{action}` in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py) to support `edit` and `delete` actions that commit changes immediately to the database.
  - Unified `renderSourceEntriesList` across all proposal types (`procedure_merge`, `merge`, `supersede`, `split`, and `profile_update`) to allow instant inline edits to persist to the database regardless of whether the overarching proposal is approved, denied, or unlinked.

---

## [000.005.005] - 2026-08-23 — *YAML Scalar Unquoting & Proposal Text Sanitization*

### Fixed
- **DevUI Proposal Text Rendering & YAML Escaping**:
  - Implemented `cleanYamlScalar` in [dev.html](file:///home/rathius/evelyn/evelyn_ui/dev.html) to properly decode single-quoted, double-quoted, folded, and block YAML scalars.
  - Resolved double apostrophe escaping (`''` to `'`) in quotes and contractions (`it's`, `AI's`) across proposed steps, trigger patterns, and split observations.
  - Eliminated random mid-sentence line breaks caused by PyYAML 80-character line folding.
  - Updated [procedure_consolidator.py](file:///home/rathius/evelyn/Evelyn/tools/procedure_consolidator.py) to dump YAML with `width=10000` to prevent line wrapping during proposal generation.
  - Sanitized existing pending procedure merge proposals in `evelyn_memory.db`.

---

## [000.005.004] - 2026-08-23 — *Environment Configuration & Network Parameterization*

### Added
- **Local Environment Support (`.env`)**:
  - Implemented automatic `.env` loader in [evelyn_config.py](file:///home/rathius/evelyn/evelyn_config.py) to read local network, port, SSL, and service endpoint overrides.
  - Added clean, documented [.env.example](file:///home/rathius/evelyn/.env.example) template for version control and new environment provisioning.

### Changed
- **Network & Host Parameterization**:
  - Parameterized CORS `ALLOWED_ORIGINS` to dynamically incorporate values from `EVELYN_ALLOWED_ORIGINS` alongside standard localhost origins.
  - Parameterized `IMAGE_SERVER_URL` via `EVELYN_IMAGE_SERVER_URL` in [evelyn_config.py](file:///home/rathius/evelyn/evelyn_config.py) and [check_evelyn_status.sh](file:///home/rathius/evelyn/scripts/check_evelyn_status.sh).
  - Parameterized SSL key/cert paths in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py).
  - Generalized architecture diagrams and component topologies in [engine_architecture.md](file:///home/rathius/evelyn/reference/engine_architecture.md) and unit test mocks in [test_image_generation.py](file:///home/rathius/evelyn/Evelyn/tests/test_image_generation.py).

---

## [000.005.003] - 2026-08-23 — *Image Host Requirements Documentation & Sanitization*

### Added
- **Core Requirements Integration**:
  - Incorporated the **FLUX.1 Schnell NF4 Image Generation Microservice** specifications and dependencies into [REQUIREMENTS.md](file:///home/rathius/evelyn/REQUIREMENTS.md) under External Services.

### Changed
- **Documentation Sanitization & Identity Parameterization**:
  - Parameterized host-specific domains, IPs, and user directory paths in [REQUIREMENTS_IMAGE_HOST.md](file:///home/rathius/evelyn/services/image/REQUIREMENTS_IMAGE_HOST.md) into generic placeholders (`<image-host>`, `<tailnet>`, `<username>`).
  - Added multi-platform (Windows & Linux) virtual environment and firewall configuration instructions.

---

## [000.005.002] - 2026-08-23 — *DevUI Split Proposal & Ingestion Layout Refinements*

### Fixed
- **DevUI Split & Proposal Card Layout**:
  - Separated metadata/badges and action buttons (`Split`, `Edit`, `Unlink`, `Delete`, `Remove`) into a top header row across Source Compound Entry, Proposed Atomic Context Facts, and Profile Update cards in [dev.html](file:///home/rathius/evelyn/evelyn_ui/dev.html).
  - Observation text content and domain tags now render on dedicated full-width rows rather than being squished into a narrow column alongside button groups.
  - Added responsive `flex-wrap` and minimum widths to inline editing forms and Split Modal draft inputs.
- **Document Ingestion Staging & Mode Layout**:
  - Restructured **Direct Filesystem Staging** directory guide cards so folder paths and explanations sit on separate full-width rows instead of cramping horizontally.
  - Made the **Ingestion Mode** radio card container responsive with `repeat(auto-fit, minmax(260px, 1fr))` for mobile and narrow viewports.

---

## [000.005.001] - 2026-08-22 — *Tag Librarian Vault DB Audit Fix*

### Fixed
- **Tag Librarian Vault DB Interface**:
  - Implemented missing `vault_db.update_document_tag_audit(path, tags=None)` in [vault_db.py](file:///home/rathius/evelyn/Evelyn/tools/vault_db.py) to reliably record audit timestamps and update document tags.
  - Resolved `AttributeError: module 'Evelyn.tools.vault_db' has no attribute 'update_document_tag_audit'` occurring during background idle Tag Librarian tasks.
  - Added unit test `test_vault_db_update_document_tag_audit` in [test_vault_move_optimization.py](file:///home/rathius/evelyn/Evelyn/tests/test_vault_move_optimization.py).

---

## [000.005.000] - 2026-08-22 — *Vault Document Ingestion Subsystem & Sidecar Architecture*

### Added
- **Automated PDF Staging Pipeline & Worker**:
  - Created dedicated dual-queue staging directories (`Attachments/Staging/Full_Extraction/`, `Attachments/Staging/Sidecar_Only/`).
  - Built `Evelyn/tools/pdf_staging_worker.py` queue scanner supporting `.meta.json` domain routing, PyMuPDF extraction, Sidecar synthesis, and Task Manager mutual exclusion.
- **Vault Watcher Staging Detection**:
  - Updated `scripts/obsidian_vault_watcher.py` to observe `Attachments/Staging/` and automatically trigger staging ingestion when files are dropped into the vault via filesystem or Syncthing.
- **FastAPI Endpoints**:
  - Added `GET /api/vault/domains` to list all valid domain destinations with their root paths and labels.
  - Added `POST /api/vault/upload_staging` to accept multi-part file uploads, write metadata, and asynchronously queue processing.
- **DevUI Document & PDF Ingestion Card**:
  - Added dedicated **📄 Document Ingestion** tab to `evelyn_ui/dev.html` featuring drag-and-drop file upload, mode toggle (Full Extraction vs Sidecar Card Only), destination domain dropdown, and upload status telemetry.
- **Rich Library Index Card (Sidecar Generator)**:
  - Generates rich `.md` Sidecar notes for non-markdown assets containing frontmatter, author, normalized taxonomy tags (`Tech/AI`, `literature/reference`), embedded PDF attachment links (`![[Attachments/Source Material/...]]`), chapter tables, overview gists, and semantic cross-links.
- **Zero-Overhead Reorganization & Content Hashing**:
  - Added SHA-256 content hashing (`compute_content_hash`) in `Evelyn/tools/ingest_obsidian_knowledge.py` to identify file moves across vault sync cycles and skip redundant GPU vector re-embedding.
  - Added `vault_db.move_document()` for atomic $<1\text{ms}$ SQLite path updates.
  - Added `chroma_rag.direct_remap()` and `chroma_rag.enqueue_remap()` to transfer precomputed Chroma embedding chunks directly to new document paths with zero model inference.
  - Enhanced `scripts/obsidian_vault_watcher.py` to detect `on_moved` events and perform atomic SQLite and Chroma remapping.

### Changed
- **Reference Library & Vault PDF Standardization**:
  - Normalized and extracted 26 Owner's Manuals and Spec Sheets into zero-padded chapter notes in `Reference Library/Owner's Manuals/`.
  - Converted medical psychology reports in `Ricky/Medical/Psychology/` and Python reference notes into structured chapter notes.
  - Relocated all remaining 31 loose PDFs across the entire vault into `Attachments/Source Material/<Domain>/` with interactive Sidecar markdown notes in their place.

---

## [000.004.003] - 2026-08-22 — *Vault Maintenance, Sidecar Index Cards & Move Optimization*

### Added
- **PDF Title Normalization & Word Segmentation**:
  - Implemented dynamic-programming word segmentation and TitleCase normalization in `scripts/extract_pdf_library.py` to convert concatenated filenames (`buildingapplicationswithaiagents...`) into clean Title Case and subtitle metadata.
- **Rich Library Index Card (Sidecar Generator)**:
  - Generates rich `.md` Sidecar notes for non-markdown assets containing frontmatter, author, normalized taxonomy tags (`Tech/AI`, `literature/reference`), embedded PDF attachment links (`![[Attachments/Source Material/...]]`), chapter tables, overview gists, and semantic cross-links.
- **Semantic Nearest-Neighbor & Entity Cross-Linking**:
  - Added `chroma_rag.find_semantic_neighbors()` to retrieve top semantically related vault notes via cosine similarity without LLM overhead.
  - Added `vault_db.get_all_entities()` to match known note titles/aliases mentioned in extracted literature.
- **Zero-Overhead Reorganization & Content Hashing**:
  - Added SHA-256 content hashing (`compute_content_hash`) in `Evelyn/tools/ingest_obsidian_knowledge.py` to identify file moves across vault sync cycles and skip redundant GPU vector re-embedding.
  - Added `vault_db.move_document()` for atomic $<1\text{ms}$ SQLite path updates.
  - Added `chroma_rag.direct_remap()` and `chroma_rag.enqueue_remap()` to transfer precomputed Chroma embedding chunks directly to new document paths with zero model inference.
  - Enhanced `scripts/obsidian_vault_watcher.py` to detect `on_moved` events and perform atomic SQLite and Chroma remapping.

### Changed
- **Roadmap Harmonization**:
  - Updated `ROADMAP.md` Phase 4 to replace separate custom plugin and ghost link items with the native Sidecar Catalog and Zero-Overhead Reorganization engine.

---

## [000.004.002] - 2026-08-22 — *Memory Tag Taxonomy Sanitization & DB Status Fix*

### Fixed
- **Database Migration Framework Up-To-Date Evaluation**:
  - Fixed `check_all_dbs_status()` in `Evelyn/tools/db_migrator.py` so databases without pending migrations correctly evaluate as up-to-date when engine version advances.

### Database Migrations
- **Memory Tag Sanitization (`000.004.002`)**:
  - Registered and executed migration `strip_legacy_kw_tags_from_memory` to strip redundant `kw/` and `ctx/` noise prefixes from `context_entries.tags` and `proposals.merged_tags` in `data/evelyn_memory.db`.

---

## [000.004.001] - 2026-08-22 — *Research Watchdog & Scope Dynamic Timeouts*

### Fixed
- **Deep Research Watchdog Timeout Premature Abort**:
  - Added dedicated soft timeout baselines for research scopes (`research_quick`: 2,400s / 40m, `research_standard`: 9,000s / 2.5h, `research_deep`: 32,400s / 9h) in `task_manager.py:DEFAULT_SOFT_TIMEOUTS`.
  - Updated `task_manager.get_dynamic_timeout()` to dynamically resolve task scope and `wall_clock_timeout` directly from the server task registry or on-disk `state.json` (`max(wall_clock_timeout + 1800, wall_clock_timeout * 1.25)`).
  - Resolved dynamic statistical historical aggregation for research tasks using wildcard matching (`WHERE task_name LIKE 'task_%'`) in SQLite `heavy_task_history`.
- **Heavy Task Registry Synchronization**:
  - Registered `tag_librarian` in `task_manager.HEAVY_TASK_KEYS` and synchronized known heavy tasks in `reference/engine_architecture.md`.

---

## [000.004.000] - 2026-08-22 — *Sanctum Architecture & Guardrails*

### Added
- **Zero-Padded Versioning & Migration Framework**:
  - Centralized version definition `__version__ = "000.004.000"` with parsing and comparison utilities in `Evelyn/version.py`.
  - Built `Evelyn/tools/db_migrator.py` with transactional DDL execution, Python callable data transforms, per-database tracking tables (`schema_migrations`), safety snapshots (`data/backups/`), and post-migration Chroma/Vault synchronization hooks.
  - Implemented standalone CLI migration manager `scripts/migrate_db.py` supporting `--status`, `--execute`, `--dry-run`, and automated Git release tagging (`--tag`).
  - Added fail-fast boot validation to `evelyn_server.py` with an optional `AUTO_MIGRATE_ON_BOOT` configuration toggle.
- **Repository Documentation**:
  - Published comprehensive, modern repository `README.md` with system overview, architecture diagrams, and AI-collaboration & human-architecting disclosures.
  - Created formal `CHANGELOG.md`.

### Changed
- **Open-Source Sanitization & Persona Parameterization**:
  - Extracted hardcoded persona, user identity, and vault path variables into parameterized configurations in `evelyn_config.py`.
  - Migrated Fast Memory taxonomy codes from `-R`/`-E` to abstract `-U` (User) and `-A` (Assistant).
  - Deployed generic persona, user profile, and system directive templates in `templates/` with an interactive setup wizard (`evelyn_setup.py`).

### Architectural & Resilience
- **Centralized Task Mutual Exclusion (`task_manager.py`)**:
  - Unified heavy task registry and watchdog preventing concurrent CPU/GPU resource thrashing across research, fact extraction, and profile evolution.
- **ChromaDB Single-Writer Custodian**:
  - SQLite WAL-backed staging queue (`chroma_sync_queue`) with single-process custodial writes to eliminate HNSW vector index corruption and file-lock collisions.
- **Dual-Socket NUMA Partitioning**:
  - Node 0 CPU/GPU pinning for core LLM inference and SQLite I/O; Node 1 isolation for Chatterbox TTS speech synthesis.

### Database Migrations
- Baseline migration `000.004.000` registered and applied across `chat`, `memory`, `vault`, and `media` databases.

---

## [000.003.000] - 2026-07-15 — *Senses, Tools & Agency*

### Added
- **Autonomous Deep Research Subsystem**:
  - Background multi-step search engine with Trafilatura crawling, atomic query generation, evidence synthesis, discovered technical alias expansion, and direct Markdown compilation into Obsidian.
- **Chatterbox Speech Synthesis (F5-TTS/Matcha)**:
  - Streaming low-latency neural TTS server with sentence chunking and dynamic emotion tags.
- **Multimodal Visual Memory & Attachment Indexing**:
  - SQLite media asset registry (`evelyn_media.db`), EXIF/GPS coordinate parsing, and local vision indexing pipeline.
- **Agentic Health & Life Tracking**:
  - Oura Ring Cloud API v2 integration (sleep/readiness/stress metrics) and Google Drive Health Connect database synchronization.
- **Developer Triage & Review Console**:
  - Touch-optimized web dashboard (`evelyn_ui/dev.html`) with real-time heavy task monitoring and proposal review queues.

---

## [000.002.000] - 2026-05-15 — *Long-Term Memory & Vector RAG*

### Added
- **Persistent SQLite Memory Storage**:
  - Introduced `evelyn_memory.db` for categorized context entries, consolidation proposals, and procedural workflows.
  - Introduced `evelyn_vault.db` for incremental file mapping, link graphs, and backlink resolution.
- **Semantic ChromaDB RAG Vector Store**:
  - Full-vault vector embeddings using `BAAI/bge-large-en-v1.5` with priority boosting.
- **Autonomous Memory Extraction & Consolidation**:
  - Background fact extractor and deduplication engines running during server idle periods.

---

## [000.001.000] - 2026-03-25 — *Persona & Brain Core*

### Added
- **Custom FastAPI Server (`evelyn_server.py`)**:
  - Standalone server replacing OpenWebUI/Modelfile runtime for sub-millisecond overhead.
  - Streaming SSE chat completions, regeneration, message editing, and history endpoints.
- **Ollama Local LLM Integration**:
  - Optimized system prompt assembly, dynamic context window budgeting, and temperature tuning.
- **Interactive Chat Interface**:
  - Clean HTML/CSS companion chat client with offline Markdown rendering (`marked.js` + `DOMPurify`).
