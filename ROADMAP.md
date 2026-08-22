---
title: ROADMAP.md
date created: 2026-03-14 22:34:06
date modified: 2026-08-21 20:57:41
tags: roadmap, goals, features, implementation, planning
---

# Evelyn Project Roadmap

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
- [ ] **Unified Multimodal Affective & VAD Engine**: Real-time prosody/audio emotion extraction and 3D VAD (Valence-Arousal-Dominance) tracking across chat and journal memory.
- [ ] **Geospatial & Location Awareness**: Ingest mobile GPS telemetry with geofencing (home, work, contacts) and travel-state detection for localized queries.
- [ ] **Message Biometrics & State Mapping**: Asynchronously map message IDs to timestamped physiological metrics (Oura/Health HRV, stress) for retroactive wellbeing inquiry without prompt clutter.

### Agency & Tools
- [x] **Deep Research Engine**: Autonomous multi-step background research orchestrator with web search (DuckDuckGo), intent framing, atomic query generation, source evaluation, and Obsidian Vault synthesis.
- [x] **Code & Terminal Agency**: Scoped execution tools with security tiers (safe, approval-required, blocked), interactive Chat UI approval cards, and FastAPI terminal endpoints.
- [x] **Profile Auto-Evolution**: Background memory scanner that proposes iterative updates to persona, profile, and directive documents, with pre-approval editing and live diff panels in DevUI.
- [x] **Procedural Knowledge Capture**: Background pipeline that extracts operational rules, workflows, and pitfalls from chat history into searchable procedural memory.
- [x] **Workspace & Health Integrations**: Integrated Google Calendar (read/write scheduling), Google Drive/Docs/Sheets sync, Health Connect clinical EHR data, and Oura Ring Cloud API v2 vitals.
- [ ] **Autonomous Engine Maintenance & Self-Coding**: Collaborative engine proposal workflow with sandboxed background code generation, test verification, and DevUI review.
- [ ] **System-Event Prompting Flow**: Inject proactive notifications into conversation turns for background triggers (agenda alerts, completed research, health anomalies).
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
- [x] **Idle Tag Librarian**: Incremental background process auditing vault notes against a Master Tag Taxonomy using Vector RAG and cosine novelty scoring.
- [ ] **Obsidian Related Documents Plugin**: Vault sidebar plugin displaying semantically related notes based on Master Tag Taxonomy overlap without runtime LLM overhead.
- [ ] **Ghost Link Manifestation**: Automated stub note generator for high-frequency unresolved wiki-links in the Obsidian vault.
- [ ] **Conversational Feedback & Adaptive Preference Tuning**: Interactive response rating (upvote/downvote) feedback loop with dynamic persona weight adjustments.
- [ ] **Multi-Node Distributed Expansion**: Split inference workloads across network nodes (Sanctum primary host, workstation GPU sidecars).

---

## Phase 5: Embodiment & Advanced Senses (Future)

*Goal: Physical presence, spatial awareness, and rich avatar embodiment.*

- [ ] **Visual Avatar**: Real-time VTuber-style expressive avatar with dynamic expressions and lip sync.
- [ ] **Real-Time Visual Awareness**: Ambient screen and camera perception for contextual assistance.
- [ ] **XR Integration**: Spatial computing and VR/AR presence.

---

## Phase 6: Open Source & Community (Future)

*Goal: Share the Evelyn Engine as a clean, extensible framework for hyper-personalized local AI companions.*

- [x] **Privacy Guardrails**: Strict boundary enforcement separating personal data ("Soul") from codebase ("Engine").
- [ ] **Template Sanitization**: Create generic persona and memory templates for open-source distribution.
- [ ] **Architectural Documentation**: Publish comprehensive setup and developer guides.
- [ ] **Public GitHub Repository**: Initial open-source release of the Evelyn Engine framework.
