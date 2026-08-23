---
title: CHANGELOG.md
date created: 2026-08-22 15:53:28
date modified: 2026-08-22 15:53:28
tags: 
---
# 📜 Changelog

All notable changes to the Evelyn Engine are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to **3-digit zero-padded Semantic Versioning** (`000.000.000`).

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
