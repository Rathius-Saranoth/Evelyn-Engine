---
title: ROADMAP.md
tags: [roadmap, goals, features, implementation, planning, evelyn, system/engine]
date created: 2026-03-14 22:34:06
date modified: 2026-09-11 20:53:57
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
- [x] **Fast Memory Attribution & Temporal Grounding**: Decoupled category canon codes from referent subjects for cross-perspective attribution, and integrated strict temporal grounding in memory extraction and RAG XML envelopes.

---

## Phase 3: Senses, Tools, & Agency (In Progress)

*Goal: Equip Evelyn with multi-modal perception, tool execution, and autonomous background agency.*

### Senses & Media
- [x] **Streaming Chatterbox TTS Engine**: Deployed local streaming F5-TTS/Matcha engine with sentence-level SSE chunked progressive playback and auto-speech toggling.
- [x] **FLUX.1 Image Generation**: Built standalone, on-demand FLUX.1 Schnell image generation microservice (port 5055) with automatic VRAM management.
- [x] **Multimodal Visual Memory**: Implemented SQLite media database (`evelyn_media.db`), isolated attachment store, client-side EXIF/GPS parsing, background visual indexing (`llama3.2-vision`), and interactive Chat UI Media Inspector.
- [ ] **Expressive Emotional TTS & Mid-Sentence Prosody**: Natural mid-response emotional modulation and paralinguistic tags (`[laugh]`, `[sigh]`, `[chuckle]`, `[gasp]`) integrated naturally across mid-sentence speech boundaries, Chat UI styling cues, and multi-style acoustic synthesis.
- [ ] **Standalone Media Gallery (`ui/gallery.html`)**: Build a dedicated media management dashboard with timeline views, category filtering, lightbox inspection, and visual RAG search.
- [ ] **Google Photos Bulk Ingestion**: Build Google Takeout ingestion pipeline preserving unredacted GPS, native timestamps, and JSON sidecars into `evelyn_media.db` for lifelong visual memory.
- [ ] **Unified Multimodal Affective & VAD Engine**: Real-time speech prosody/audio emotion extraction and 3D VAD (Valence-Arousal-Dominance) tracking across chat and journal memory, with chronological historical backfill calibration.
- [ ] **Geospatial & Location Awareness**: Ingest mobile GPS telemetry with geofencing (home, work, contacts) and travel-state detection for localized queries.
- [ ] **Message Biometrics & State Mapping**: Asynchronously map message IDs to timestamped physiological metrics (Oura/Health HRV, sleep, stress) for retroactive wellbeing inquiry without prompt clutter.

### Agency & Tools
- [x] **Deep Research Engine**: Autonomous multi-step background research orchestrator with web search, intent framing, atomic query generation, source evaluation, and Obsidian Vault synthesis.
- [x] **Code & Terminal Agency**: Scoped execution tools with security tiers (safe, approval-required, blocked), interactive Chat UI approval cards, and FastAPI terminal endpoints.
- [x] **Profile Auto-Evolution**: Background memory scanner proposing iterative updates to persona, profile, and directive documents, with structured guardrails and live DevUI diff review.
- [x] **Procedural Knowledge Consolidation**: Background extraction and consolidation pipeline with lifecycle taxonomy (`live`, `merged`, `archived`), dynamic tool deduplication, and DevUI interactive master consolidation.
- [x] **Cognitive Task Scheduling & Digital Dreaming**: Formalized 3-tier task dispatching (`REFLEX` 24/7, `DIURNAL` daytime research, `NOCTURNAL` dreaming) with preemption and timezone-aware temporal management (`time_manager`).
- [x] **Reflective Journaling & Compaction**: Persona-agnostic reflection schema, token-budgeted day history loading, autonomous after-hours daemon, and Map-Reduce transcript compaction.
- [x] **Multi-Modal Ambient Feed & Thought Bubbles**: Extensible daytime ambient impressions substrate (`daily_ambient_impressions`), header island thought bubbles, and failure-isolated evening reflection synthesis.
- [x] **Workspace & Health Integrations**: Integrations for Google Calendar/Tasks/Drive, Obsidian Vault checklists, Health Connect clinical EHR data, and Oura Ring Cloud API v2 biometrics.
- [x] **Direct Web Browsing & Search**: Dedicated `read_url` tool with desktop client fingerprinting, bot-challenge diagnostics, query sanitization, and in-memory TTL caching.
- [ ] **Third-Party & Multi-Entity Profiles**: Dynamic evolution and autonomous profiling for external contacts and collaborators encountered across channels into dedicated profile notes.
- [ ] **Semantic & Embedding-Guided Profile Ingestion**: Hybrid category and vector distance memory retrieval for profile evolution to dynamically ingest cross-domain observations.
- [ ] **Profile Evolution Protected Sections**: Configurable section-level invariants (`PROTECTED_SECTIONS` in `evelyn_config.py`) and validator locks preventing nocturnal profile evolution passes from modifying specified headers across persona triad files.
- [ ] **Spell Breaker (Focus Check-In Timer)**: Reverse "Do Not Disturb" timer in Chat UI that dispatches a proactive system event to Evelyn when a project timer expires, prompting an empathetic break or check-in response with forced voice playback.
- [ ] **System-Event Prompting Flow**: General server mechanism to inject proactive notifications and initiate unsolicited turns for high-priority background triggers (agenda alerts, completed research, health anomalies).
- [ ] **Autonomous Engine Maintenance & Self-Coding**: Collaborative engine proposal workflow with sandboxed background code generation, test verification, and DevUI review.

---

## Phase 4: Architecture & Infrastructure (Complete / Ongoing)

*Goal: Robust data architecture, single-writer resilience, task supervision, and multi-device synchronization.*

- [x] **Chroma Single-Writer Architecture**: SQLite WAL-backed staging queue (`chroma_sync_queue`) with single persistent client custodial writes, poison-pill isolation, and auto-recovery.
- [x] **Multi-Device Obsidian Sync**: Private peer-to-peer synchronization mesh via Syncthing over Tailscale with real-time file watcher service (`evelyn-vault-watcher.service`).
- [x] **Centralized Task Manager & Watchdog**: Single task manager with PID locking, soft timeouts, background process supervision, and mutual exclusion across idle workers.
- [x] **Cross-Session History Search**: SQLite FTS5 full-text indexing with query reformulation and date filtering across historical message archives.
- [x] **Developer Web UI**: Touch-optimized web dashboard (`dev.html`) with live Heavy Task telemetry, Unified Triage Queue, and Deep Research monitor.
- [x] **Vault Maintenance & Staging Pipeline**: Automated PDF staging queues with domain routing, rich index cards, content-hash (SHA-256) tracking, and zero-overhead reorganization.
- [x] **Master Librarian Curation Engine**: Single-pass vault curation ecosystem (`master_librarian`, tag, link, format, and index librarians) under the canonical `backlog_drainer` framework with notation leak healing.
- [x] **Single-Stream Agentic Architecture & Telemetry**: Unified streaming inference loop with live thinking deltas, intermediate tool execution, canonical XML telemetry envelopes, direct dense vector RAG, and dynamic tool surfacing.
- [x] **Multi-Node Distributed Expansion**: Distributed service workloads across dedicated infrastructure (dual CPU host allocation and dedicated remote FLUX.1 host).
- [x] **Deterministic Code Hygiene & Wiring Verification**: Mandatory 3-stage validation gate integrating Ruff static linting, AST config-wiring pytest checks, and Vulture compiler-level dead-code auditing.
- [x] **Conversational Feedback & Quality Telemetry**: Interactive response rating (upvote/downvote) in Chat UI with satisfaction analytics and persistent RAG retrieval event inspection in DevUI.
- [x] **Dual-Collection Vector Architecture**: Split ambient conversational RAG (`evelyn_memory`) from external reference documents into a dedicated collection (`evelyn_reference`) with an active semantic search tool (`search_reference_library`).
- [x] **Deployment & Mesh Infrastructure Documentation**: Published comprehensive WSL2 and bare-metal deployment guide in `SETUP_GUIDE.md` covering Tailscale P2P mesh networking, port isolation, user lingering, and Syncthing synchronization.
- [ ] **Dynamic Configuration Manager (`dev.html`)**: Touch-friendly web settings interface in DevUI to toggle features on/off, edit idle/circadian timers, configure assistant/user identity, manage protected profile sections, and adjust custom directories without direct file edits.
- [ ] **Chat History Soft-Deletion & Trace Preservation**: Retain regenerated and edited assistant turns with soft-delete flags (`is_deleted`) to preserve failed responses, thinking traces, and tool logs in DevUI feedback review while isolating them from active context.
- [ ] **Prompt Taxonomy & Domain Classifier**: Semantic labeling and domain categorization for inbound user messages to enable granular conversational analytics.
- [ ] **Continuous Evaluation & Regression Benchmarking Suite**: Scheduled evaluation harness with golden query suites, persona/tool accuracy scoring, and historical benchmark regression tracking.
- [ ] **Engine & Lifecycle Analytics Dashboard (`dev.html`)**: Comprehensive metrics dashboard to track engine usage, prompt domains, evaluation regressions, RAG/vault knowledge utilization, and tool/procedure frequency with time-range drill-downs.
- [ ] **Domain Subpackage Modularization (`Evelyn/tools/`)**: Decompose flat 44+ module directory into clean domain packages (`vault/`, `journal/`, `memory/`, `research/`, `integrations/`, `core/`) with unified facade exports and zero-breakage backwards compatibility.
- [ ] **Local Independence & Cloud Decoupling**: Build self-hosted CalDAV / local `.ics` calendar adapter, peer-to-peer Syncthing Health Connect ingestion (bypassing Google Drive), and optional self-hosted SearXNG search gateway.
- [ ] **Security & TLS Infrastructure Setup Guide**: Expand `SETUP_GUIDE.md` to document TLS/SSL certificate provisioning, Subject Alternative Names (SAN), and external HTTPS gateway configuration.

---

## Phase 5: Embodiment & Advanced Senses (Future)

*Goal: Physical presence, spatial awareness, and rich avatar embodiment.*

- [ ] **Visual Avatar (VRoid / 3D)**: Real-time expressive 3D avatar (VRoid Studio model integration) with dynamic expressions, lip sync, and non-VR/VR viewport rendering.
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
