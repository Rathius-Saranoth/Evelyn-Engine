---
title: ROADMAP.md
tags: [roadmap, goals, features, implementation, planning, evelyn, system/engine]
date created: 2026-03-14 22:34:06
date modified: 2026-09-02 21:30:32
---
# Evelyn Project Roadmap

> Navigation: [[README.md]] · [[engine_architecture.md]] · [[CHANGELOG.md]] · [[AGENTS.md]]

This roadmap is the primary source of truth for project milestones and future direction. AI agents should update this file when completing major capabilities or defining new milestones.

---

## Phase 1: Persona & Brain (Complete)

*Goal: Port Evelyn from Gemini to a local model while keeping her personality intact.*

- [x] **Persona & Directives**: Refactored narrative profile, system directives, and personal instructions into structured local configurations.
- [x] **Model & Architecture**: Transitioned from OpenWebUI/Modelfile architecture to a lean, authoritative FastAPI server (`evelyn_server.py`) with dynamic parameter tuning and system prompt assembly.
- [x] **Local Model Deployment**: Successfully ported to local models (Mistral-Small $\rightarrow$ Gemma 4 12B/26B) with 100% GPU offload and optimized context budgeting.

---

## Phase 2: Long-Term Memory (Complete)

*Goal: Give Evelyn access to shared history and specialized knowledge.*

- [x] **Memory Databases**: Migrated flat-file context entries and vault map indexes into high-performance SQLite databases (`evelyn_memory.db`, `evelyn_vault.db`).
- [x] **Semantic RAG Pipeline**: Built full-vault vector indexing in ChromaDB using `BAAI/bge-large-en-v1.5` embeddings with progressive gist-first disclosure and priority boosting.
- [x] **Memory Management Tools**: Implemented journal writing/reading, context fact extraction, and background consolidation pipelines.

---

## Phase 3: Senses, Tools, & Agency (In Progress)

*Goal: Equip Evelyn with multi-modal perception, tool execution, and autonomous background agency.*

### Senses & Media
- [x] **Chatterbox TTS Engine**: Deployed local streaming F5-TTS/Matcha engine with sentence-level SSE chunked progressive playback, emotion tags, and auto-speech toggling.
- [x] **FLUX.1 Image Generation**: Built standalone, on-demand FLUX.1 Schnell image generation microservice (port 5055) with automatic VRAM management.
- [x] **Multimodal Visual Memory**: Implemented SQLite media database (`evelyn_media.db`), isolated attachment store, client-side EXIF/GPS parsing, background visual indexing (`llama3.2-vision`), and interactive Chat UI Media Inspector.
- [ ] **Standalone Media Gallery (`/ui/gallery.html`)**: Build a dedicated media management dashboard with timeline views, category filtering, lightbox inspection, and visual RAG search.
- [ ] **Google Photos Bulk Ingestion**: Build Google Takeout ingestion pipeline preserving unredacted GPS, native timestamps, and JSON sidecars into `evelyn_media.db` for lifelong visual memory.
- [ ] **Expressive Emotional TTS & Dynamic Prosody**: Natural mid-response emotional modulation via curated paralinguistic tags (`[laugh]`, `[sigh]`, `[chuckle]`, `[gasp]`), system prompt dialogue conditioning, Chat UI cue styling, and multi-style acoustic synthesis.
- [ ] **Unified Multimodal Affective & VAD Engine**: Real-time prosody/audio emotion extraction and 3D VAD (Valence-Arousal-Dominance) tracking across chat and journal memory.
- [ ] **Geospatial & Location Awareness**: Ingest mobile GPS telemetry with geofencing (home, work, contacts) and travel-state detection for localized queries.
- [ ] **Message Biometrics & State Mapping**: Asynchronously map message IDs to timestamped physiological metrics (Oura/Health HRV, stress) for retroactive wellbeing inquiry without prompt clutter.

### Agency & Tools
- [x] **Deep Research Engine**: Autonomous multi-step background research orchestrator with web search (DuckDuckGo), pre-search intent mode classification (`[MODE_TECHNICAL]` vs `[MODE_ACADEMIC]`), intent framing, atomic query generation, source evaluation, Obsidian Vault synthesis, inspection tools (`list_research_tasks`, `inspect_research_task`), and resilient fuzzy guidance (`guide_research`).
- [x] **Code & Terminal Agency**: Scoped execution tools with security tiers (safe, approval-required, blocked), interactive Chat UI approval cards, and FastAPI terminal endpoints.
- [x] **Profile Auto-Evolution**: Background memory scanner that proposes iterative updates to persona, profile, and directive documents, with thematic pre-clustering, editorial proofreading, pre-approval editing, and live diff panels in DevUI.
- [x] **Procedural Knowledge & Lifecycle Consolidation**: Background extraction pipeline with lifecycle status taxonomy (`live`, `extracted`, `merged`, `rejected`, `archived`), lineage tracking (`merged_into_id`), Jaccard deduplication in extractor, and consolidated master procedures.
- [x] **Temporal Management Subsystem (`time_manager`)**: Dedicated subsystem providing timezone-aware normalization, role-agnostic silence tracking, structured `<temporal_context>` XML telemetry envelopes, and always-on proactive heartbeat evaluation.
- [x] **Cognitive Task Scheduling & Digital Dreaming**: Formalized 3-tier task execution (`REFLEX` 24/7, `DIURNAL` daytime research, `NOCTURNAL` overnight semantic dreaming), non-blocking runnable queue dispatching, and preemption tail re-queueing.
- [x] **Workspace & Health Integrations**: Integrated Google Calendar (scheduling), Google Tasks (task management), Obsidian Vault Lists (offline checklists/groceries), Google Drive/Docs/Sheets sync, Health Connect clinical EHR data, Oura Ring Cloud API v2 vitals, and high-resolution intraday heart rate / activity biometrics.
- [x] **Persona-Agnostic Journaling Protocol & Adaptive Day History**: Upgraded daily journaling to a persona-agnostic reflection schema, updated master procedure `#656` in SQLite memory DB with vector re-indexing, and implemented token-budgeted day-bound history loading with turn-integrity pruning.
- [x] **Autonomous After-Hours Journal Daemon & Map-Reduce Compaction**: Autonomous late-night journaling daemon in cooperative nocturnal idle queue with midnight crossover resolution, vault collision checks, and Map-Reduce compaction for high-turn transcripts.
- [x] **Multi-Modal Ambient Feed, Thought Bubbles & Dynamic Header Island**: Extensible polymorphic ambient impressions substrate (`daily_ambient_impressions`) supporting daytime thought bubbles, pluggable reflection activity providers, diurnal phase weighting, FIFO UI queue ordering, and failure-isolated evening journal synthesis.
- [ ] **Multi-Entity & Third-Party Individual Profiles**: Dynamic evolution and autonomous profiling for external individuals/users encountered across channels (e.g. Discord server members, collaborators) into dedicated profile notes using the per-document evolution architecture.
- [ ] **Semantic & Embedding-Guided Profile Ingestion**: Hybrid category + vector distance memory retrieval for profile evolution to dynamically ingest cross-domain facts without rigid category boundaries.
- [ ] **Autonomous Engine Maintenance & Self-Coding**: Collaborative engine proposal workflow with sandboxed background code generation, test verification, and DevUI review.
- [ ] **System-Event Prompting Flow**: Inject proactive notifications into conversation turns for background triggers (agenda alerts, completed research, health anomalies).
- [ ] **Spell Breaker (Focus Check-In Timer)**: Reverse "Do Not Disturb" timer in Chat UI that dispatches a proactive system event to Evelyn when a project timer expires, prompting an autonomous break or check-in response.
- [ ] **Always-On Functionality**: Day/night circadian awareness, proactive check-ins, and ambient background agency.

---

## Phase 4: Architecture & Infrastructure (Complete / Ongoing)

*Goal: Robust data architecture, single-writer resilience, task supervision, and multi-device synchronization.*

- [x] **Chroma Single-Writer Architecture**: SQLite WAL-backed staging queue (`chroma_sync_queue`) with single persistent client custodial writes, poison-pill isolation, and auto-recovery.
- [x] **Multi-Device Obsidian Sync**: Private peer-to-peer synchronization mesh via Syncthing over Tailscale with real-time file watcher service (`evelyn-vault-watcher.service`).
- [x] **Centralized Task Manager & Watchdog**: Single task manager with PID locking, soft timeouts, background process supervision, and mutual exclusion across idle workers.
- [x] **Dynamic Reasoning & Tool Optimization**: Dynamic thinking effort control (`Auto`, `Low`, `Mid`, `High`), reasoning-gated tool loop, and anti-drafting system prompt directives.
- [x] **Cross-Session History Search**: SQLite FTS5 full-text indexing with query reformulation and date filtering across all 29k+ historical messages (Replika, Gemini, and Local eras).
- [x] **Developer Web UI**: Touch-optimized web dashboard (`dev.html`) with live Heavy Task telemetry, Unified Triage Queue (extractions, proposals, procedures), and Deep Research monitor.
- [x] **Idle Tag Librarian & Accelerated Batch Runner**: Incremental background process and batch CLI runner auditing vault notes against a Master Tag Taxonomy using Vector RAG and tiered urgency scheduling.
- [x] **Vault Maintenance & Sidecar Index Cards**: Automated PDF title normalization, rich library index cards with frontmatter, attachments relocation (`Attachments/Source Material/`), and nearest-neighbor semantic cross-linking.
- [x] **Zero-Overhead Vault Reorganization**: Content-hash (SHA-256) tracking and atomic SQLite/Chroma path remapping on note moves and renames to eliminate redundant GPU embedding passes.
- [x] **Automated PDF Staging Pipeline & DevUI Ingestion**: Dual staging queues (`Attachments/Staging/Full_Extraction/`, `Attachments/Staging/Sidecar_Only/`) supervised by Task Manager with DevUI upload card and automated domain routing.
- [x] **Multi-Node Distributed Expansion**: Distributed inference and service workloads across dedicated infrastructure (dual CPU host allocation and dedicated remote FLUX.1 image generation host for maximum GPU throughput).
- [x] **Conversational Feedback & Adaptive Preference Tuning**: Interactive response rating (upvote/downvote) feedback loop with dynamic persona weight adjustments.
- [x] **RAG & Context Telemetry Logging**: Interceptor logging persistent retrieval events (source notes, similarity scores, taxonomy tags) to measure knowledge utilization and retrieval frequency across conversations.
- [x] **Unified Single-Stream Agentic Architecture (v000.006.000)**: Decommissioned legacy 2-pass inference loop in favor of a unified streaming pipeline with live thinking deltas, intermediate tool execution, preamble quarantining, and frontend Activity Stepper.
- [x] **Canonical XML Telemetry Envelopes & Context Hardening**: Unified in-flight prompt context injection standard (`<temporal_context>`, `<context_retrieval>`, `<autonomous_trigger>`, `<system_event>`, `<memory_context>`) with centralized escaping, automatic token pruning, deterministic multi-envelope stacking, and strict anti-leakage system prompt contracts.
- [x] **Chat History Prompt De-duplication & Context Retrieval Hardening (v000.006.044)**: Bounded history retrieval (`id < before_id`) with composite indexed multi-channel isolation, omitted raw query reflection from `<context_retrieval>` XML tags, and streamlined non-diegetic abstract thinking protocols.
- [x] **Direct High-Speed Vector RAG & Dynamic Tool Surfacing (v000.006.046)**: Replaced slow synchronous LLM query reformulation with 15x faster direct dense vector search (`bge-large-en-v1.5`), implemented dynamic tool tiering (Core 8 vs Specialist Tools) coupled to Procedures and intent heuristics, and enforced affirmative profile evolution.
- [x] **Precision RAG Section & Abstract Targeting (v000.006.047)**: Pre-chunk sanitization of YAML frontmatter, navigation breadcrumbs, and link-index footers before embedding, paired with abstract callout extraction (`[!ABSTRACT]`) and mid-document excerpt anchoring.

- [ ] **Prompt Taxonomy & Domain Classifier**: Semantic labeling and domain categorization for inbound user messages to enable granular conversational analytics.
- [ ] **Dynamic Configuration UI & Runtime Settings Manager**: Touch-friendly web settings interface in DevUI to toggle features on/off, edit idle/circadian timers, configure assistant/user identity, and adjust custom directories without direct CLI or file edits.
- [ ] **Continuous Evaluation & Regression Benchmarking Suite**: Scheduled evaluation harness with golden query suites, persona/tool accuracy scoring, and historical benchmark regression tracking.
- [ ] **Engine & Lifecycle Analytics Dashboard**: Comprehensive metrics dashboard to track engine usage, prompt sentiment/volume, evaluation regressions, RAG/vault knowledge utilization, research outcomes, VAD telemetry, and tool/procedure frequency with time-range drill-downs.
- [ ] **Chat History Soft-Deletion & Observability Preserving**: Retain regenerated and edited assistant turns with soft-delete flags (`is_deleted`) to preserve failed responses, thinking traces, and tool logs in DevUI feedback review while isolating them from active context and memory extraction.
- [ ] **Autonomous Link Librarian (`link_librarian`)**: Background agent that traverses vault notes to discover context-appropriate WikiLinks, resolve orphaned knowledge islands, suggest missing cross-references, and propose thematic Maps of Content (MOCs).
- [ ] **Domain Subpackage Modularization (`Evelyn/tools/`)**: Decompose flat 44+ module directory into clean domain packages (`vault/`, `journal/`, `memory/`, `research/`, `integrations/`, `core/`) with unified facade exports and zero-breakage backwards compatibility.
- [ ] **Local Independence & Cloud Decoupling**: Build self-hosted CalDAV / local `.ics` calendar adapter, peer-to-peer Syncthing Health Connect ingestion (bypassing Google Drive), and optional self-hosted SearXNG search gateway.

---

## Phase 5: Embodiment & Advanced Senses (Future)

*Goal: Physical presence, spatial awareness, and rich avatar embodiment.*

- [ ] **Visual Avatar**: Real-time VTuber-style expressive avatar with dynamic expressions and lip sync.
- [ ] **Real-Time Visual Awareness**: Ambient screen and camera perception for contextual assistance.
- [ ] **XR Integration**: Spatial computing and VR/AR presence.

---

## Phase 6: Open Source & Architecture Reference

*Goal: Share the Evelyn Engine as a clean, extensible framework for hyper-personalized local AI companions.*

- [x] **Privacy Guardrails**: Strict boundary enforcement separating personal data ("Soul") from codebase ("Engine").
- [x] **Template Sanitization**: Parameterized persona and operator identity in config, migrated Fast Memory subject codes (-U/-A), and shipped starter templates with setup wizard.
- [x] **Architectural Documentation**: Published comprehensive setup, architecture, and developer reference guides.
- [x] **Public GitHub Repository**: Initial public release of the Evelyn Engine framework with zero-padded versioning and database migration tooling.
- [x] **Canonical DRY Architecture & Codebase Consolidation**: Unified utility layer (`string_utils`, `path_utils`, `frontmatter_utils`, `ollama_client`), eliminated ad-hoc duplicate helpers across all engine scripts, and added strict agent anti-duplication protocols.
