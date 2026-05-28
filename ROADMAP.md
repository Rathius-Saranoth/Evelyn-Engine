---
title: ROADMAP.md
date created: 2026-03-14 22:34:06
date modified: 2026-05-25 20:38:16
tags: roadmap, goals, features, implementation, planning
---

## Evelyn Project Roadmap

This is the primary source of truth for project progress. AI agents MUST update this file after completing significant milestones.

## Phase 1: Persona & Brain (Complete)

*Goal: Port Evelyn from Gemini to a local model while keeping her personality intact.*

- [x] Integrate Gemini's narrative suggestions into `Ricky_Narrative_Profile.md`.
- [x] Integrate Gemini's system directive suggestions into `System_Directives.md`.
- [x] Clean up and condense `personal_instructions_gemini_version.md`.
- [x] Create/Update the official `evelyn:v14` Modelfile (base: mistral-small3.1).
- [x] Compile and deploy the new version to OpenWebUI.

## Phase 2: Long-Term Memory (Complete)

*Goal: Give Evelyn access to her shared history and specialized knowledge.*

- [x] Optimize Knowledge Base Retrieval Parameters (Gist & Document Chunking).
- [x] Create systems for memory creation, retrieval, and updating.
- [x] Implement `write_journal_entry` tool (Master Protocol format).
- [x] Implement `log_context_fact` tool (Draft mode/Preview constraint).
- [x] Implement system for context fact updates.

## Phase 3: Senses, Tools, & Agency (In Progress)

*Goal: Equip Evelyn with a voice, basic file/system interaction, and autonomous agency.*

- [x] Migrate TTS to the robust Chatterbox (F5-TTS/Matcha) local engine, supporting natural phrasing, voice cloning, and dynamic emotion/expression tags.
- [x] Port image generation from legacy multi-field ComfyUI websockets to a standalone, on-demand FLUX.1 [schnell] NF4 FastAPI server (port 5055) with lazy-loading, sequential CPU offloading, and auto-unload on idle (120s) to guarantee zero VRAM impact on coexisting services.
- [ ] **Implement Expressive Speech-to-Text (STT)**: Go beyond basic transcription (currently handled via phone OS keyboard) and integrate a local STT engine capable of extracting vocal nuance tags (pitch, stress, hesitation) so Evelyn can 'hear' the emotion behind the words.
- [x] Implement time awareness via date/time injection in `evelyn_server.py`'s `load_system_prompt()` + behavioral directive.
- [x] **Web Search Tool**: Build a custom `search_web` tool backed by DuckDuckGo Search (ddgs) for current events / public info not in the vault.
- [x] **Deep Research Mode — Phase 1: Core Engine (MVP)**: Designed and implemented the complete multi-step background orchestrator-worker loop (Option B). Created `web_reader.py` (async fetch, `trafilatura` extraction, and chunker), `research_prompts.py` (stateless templates for planning, extraction, evaluation, and synthesis), and `research_engine.py` (state persistence, confidence evaluation, safety guards, and Obsidian Vault publishing). Successfully tested end-to-end via CLI!
- [x] **Deep Research Mode — Phase 2: Integration & Web UI**: Integrated the deep research background engine into the FastAPI server lifespan loop and idle orchestrator, built out the active-conversation interrupt triggers (pause/resume), implemented the self-initiated queue system, registered model-facing and system tools, and exposed real-time progress and markdown report reading directly in the Developer Web UI. (Completed 2026-05-26)
- [x] **Deep Research Mode — Phase 3: Knowledge Ecosystem & Self-Initiation**: Completed the full knowledge-link integration. Wired the automated research topic queue (`queue.json`), integrated the Obsidian Vault database directly as a primary research search source, implemented per-task Chroma vector indexing for `deep` scope runs, and added cross-task Chroma querying to leverage previous research chunks via a virtual memory cache. (Completed 2026-05-26)
- [x] **Deep Research Mode — Phase 4: UI & Polish**: Fully integrated active background indicators and badges into the main chat window. Built custom Markdown parsing to translate Obsidian `[[Research/topic_slug]]` syntax into clickable interactive badges, and added a seamless overlay lightbox allowing you to read full synthesized research reports directly inside your chat view. (Completed 2026-05-26)
- [x] **Deep Research Mode — Phase 5: Concurrency & UI Telemetry Stability**: Fixed dashboard data mapping mismatches (loading exact confidence percentages and total processed sources cleanly), dynamically applied CSS color states mapped to the active pipeline step (`planning`, `searching`, `evaluating`, `synthesizing`). Added immediate-pause triggers upon active conversation threads to instantly free up Ollama concurrency, sped up the background research lifespan polling to 2-second intervals for real-time responsiveness, hardened Evelyn's system directives to launch all research requests in the background, and implemented a robust background Resume/Retry pipeline with a dashboard action button to seamlessly recover tasks aborted by connection resets or server restarts. (Completed 2026-05-27)
- [x] **Deep Research Mode — Phase 6: Research Queue Protocol Hardening**: Stabilized the Research Engine. Implemented strict single-task concurrency gating, chronological queue date-sorting, auto-recovery retries for errored tasks, and tool-level routing that automatically queues overlapping requests during chat sessions rather than blocking or causing Ollama contention. (Completed 2026-05-27)
- [ ] **Code & Terminal Agency**: Equip Evelyn with safe, scoped tools to read files, write scripts, and execute commands within the LocalAI workspace environment, enabling true pair-programming and self-modification.
- [ ] Explore Google Drive File Integration.
- [ ] Implement scheduling and reminders.
- [ ] Explore 'always on' functionality (day/night cycles & random messages).

---

## Optimization & Refinement

*Ongoing technical improvements and AI guardrails.*

- [x] **AI Instructions**: Formalize `.ai-instructions.md` for assistant continuity (strengthened ROADMAP authority rule).
- [x] **Service Management**: Implement `.agents/workflows/start-services.md`.
- [x] **Coding Standards**: Enforce Google-style Docstrings across core scripts.
- [x] **Version Control**: Initialize local Git repository with protective `.gitignore`.
- [x] **Architecture Overhaul**: Retired Modelfile pipeline and Open WebUI entirely; `evelyn_server.py` is now the sole authority for model config, system prompt, and parameters.
- [x] **Prompt Engineering**: Rewrote Evelyn persona (first-person structured), system prompt (with tool priority ordering), and RAG prompt.
- [x] **Sync Scripts**: Fixed state-based file_id tracking in ingest scripts; fixed openwebui_sync_tool.py Phase 2 duplication bug.
- [x] **Workspace Cleanup**: Reorganized reference/, archived stale outputs, renamed status checker script.
- [x] **Backup**: Regularly push code "Engine" to GitHub using the `backup-to-github` workflow.
- [x] **Model Tuning Parameters**: Added `TEMPERATURE`, `MIN_P`, `TOP_K`, `TOP_P`, `REPEAT_PENALTY`, `REPEAT_LAST_N`, `SEED`, and `NUM_PREDICT` to `evelyn_config.py` Model Parameters section. All params hot-reload per-request; set any to `None` to defer to Ollama default. `MIN_P = 0.05` is the key speed improvement from the OWUI migration.
- [x] **Startup Sequencing**: Rewrote `tasks.json` so "Start Evelyn Services" sequences `Run Ollama` → `Wait for Ollama` (TCP gate via `wait_for_ollama.ps1`) → all remaining services in parallel. Ensures Ollama claims GPU layers before ComfyUI loads.
- [x] **ComfyUI Deprecation & Purge**: Successfully decoupled the Media Engine architecture from ComfyUI. Removed `websocket-client` dependencies, deleted the massive 90GB ComfyUI installation, and replaced legacy websocket proxy scripts (`comfy_image_gen.py`, `qwen_tts_server.py`) with standalone, lean FastAPI inference servers for TTS (Chatterbox) and Image Generation (FLUX.1).
- [x] **On-Demand Model Unload**: Added "Unload Evelyn Model" VS Code task — sends `keep_alive:0` to Ollama API to evict the model from VRAM without stopping the server. Frees ~9.2 GB VRAM for gaming or other GPU-intensive workloads.
- [x] **RAG Tuning**: Optimize chunk size and similarity thresholds for Magistral 24B. Per the Magistral paper (arXiv:2506.10910), the model has a **128k context window** but is trained with a **32k–40k reasoning budget**. Target: **512–768 token chunks** (post-frontmatter strip) with **100–150 token overlap**, `RAG_TOP_K = 3–5`. This keeps retrieved context under 4k tokens, preserving headroom for reasoning traces.
- [x] **Search Priority Order**: Enforce Gist-first → Core Knowledge → Obsidian → Web Search tool priority. **Phase 1 done**: tool `description` strings in `evelyn_tools.py` updated with explicit STEP 1/STEP 2 ordering and DO NOT use guards. **Phase 2 done**: RAG pre-processing and frontmatter stripping.
- [x] **RAG Pre-processing**: Strip YAML frontmatter and Obsidian headers from knowledge documents before ingestion so RAG chunks contain dense content, not metadata. Prevents context lobotomy from header-bloated chunks.
- [x] **RAG Summarize-Before-Inject**: Add a lightweight summarization step between RAG retrieval and context injection for large documents — Evelyn summarizes the chunk before reasoning on it, keeping context lean.
- [x] **NUM_CTX Uplift**: Confirmed GPU is **RTX 4070 (12 GB VRAM)**. Magistral 24B at Q4_K_M ≈ 13.5 GB model weights \u2014 already CPU-offloads some layers. Current `NUM_CTX = 16384` is the correct ceiling for this hardware. Raising it would risk OOM or severe latency. **Resolution: keep 16384.** See `reference/system/system_specs.md` for full analysis.
- [x] **Model Testing**: Evaluated aia/Dolphin3.0-Mistral-24B and CognitiveComputations/dolphin-mistral-nemo against mistral-small3.1. **Result: mistral-small3.1 retained.** Nemo was too fantastical/non-grounded; Dolphin 24B had no memory anchoring and hallucinated. Small uses vault retrieval correctly and now actively calls the context update tool.
- [x] **Gemma 4 26B Evaluation** *(started 2026-04-07, 1-week trial)*: Switched active model from `magistral:24b` to `gemma4:26b` (MoE, 26.8B total / 3.8B active). Required Ollama upgrade from 0.18.2 → 0.20.3. Initial findings: 47% GPU / 53% CPU split (slight improvement over Magistral's ~40/60), noticeably faster token streaming due to MoE sparse activation, tool calling confirmed working (journal write on first test). `magistral:24b` kept as commented-out fallback in `evelyn_config.py`. **Promote to permanent if no regressions by ~2026-04-14.**
- [x] **Entity Resolution**: Investigate Schyler entity mismatch — model matched `Schyler Sekulich` (vault file) but tried to update `Schyler (persona)` (different entry). Review [[context_manager.py]] entity lookup logic.
- [x] **Message History Cap**: `load_history()` was sending every message ever stored to Ollama with no limit. Added `MAX_HISTORY_MESSAGES = 30` (15 turns) config cap. Only the most recent messages are sent to the model; all messages remain in the DB and `/history` UI endpoint.
- [x] **Thread Break System**: Added `[THREAD_BREAK]` marker row and `POST /new_thread` endpoint. "✦ New Thread" button in the UI inserts a boundary — `load_history()` only returns messages after the latest break. Visual `── new thread ──` divider renders in chat history. Gives Evelyn a clean conversational slate without losing any stored messages.
- [x] **Mobile Connection Recovery**: Added Screen Wake Lock API to keep the display alive while streaming (prevents phone screen-off mid-response). Added `visibilitychange` recovery handler — pull-based design using `GET /latest_message_id` endpoint. On tab-focus, the UI compares the server's latest committed message ID against the last rendered ID; if the server has newer content, history reloads automatically. Immune to new SSE event types — previous timestamp-based approach broke when status/heartbeat events were added. *(Redesigned 2026-05-17)*
- [x] **Write-Tool Badges**: Persistent badges on assistant messages when file-writing tools fire: 📓 Journal entry written, 📌 Context fact logged, 📝 Context fact updated, 🎨 Image generated. Applied to both `sendMessage()` and `regenerateResponse()` flows.
- [x] **Context Summarizer**: Implemented async sliding-window summarizer (`context_summarizer.py`). Compresses older messages (beyond the active 20-msg window) into a ~200-word summary injected into the system prompt. Runs in background via `asyncio.create_task()` after each response — zero user-facing latency. Uses same model/`num_ctx` via in-process Ollama call (no model swap). Cache rebuilds on server startup; invalidates on thread break. Config in `evelyn_config.py`: `SUMMARY_WINDOW_SIZE`, `SUMMARY_MAX_WORDS`, `SUMMARY_OVERLAP`, `SUMMARY_MODEL`.
- [x] **Token Count Display**: Surface per-message or per-request token counts in the chat UI or server console. Enables monitoring of context utilization and early warning when approaching the `num_ctx` ceiling.
- [x] **SSH Remote Access (Retired)**: Enabled Windows OpenSSH Server for Tailscale-routed remote access from Android (Termux) and created `evelyn_tools.ps1`. *Retired in favor of Google Remote Desktop (cleaner, easier to use).*
- [x] **Engineering Standards**: Codified Dave Plummer's "Notes to Live By" quality gates and operational disciplines into `.ai-instructions.md` §2. Added `/quality-review` workflow for structured self-review. *Audit complete (2026-05-19): performed a full Quality Review over `evelyn_server.py`, `fact_extractor.py`, `fact_consolidator.py`, `pending_reviewer.py`, and `context_manager.py`, confirming 100% compliant and stable status.*
- [ ] **Evelyn Axiom Injection**: Embed a standing engineering axiom (e.g., "Every line of code has mass") into Evelyn's system directives. Deferred until Evelyn has code-generation capabilities.
- [x] **Suppress Windows Asyncio Noise**: The `ProactorEventLoop` on Windows throws noisy `ConnectionResetError` tracebacks when browser polling requests disconnect mid-response. Harmless but clutters the console. Suppress or compress to a single-line warning.
- [x] **RAG Retrieval Benchmark**: Built golden test set (`reference/rag_benchmark_queries.json`, 25 queries across 6 categories) and standalone `Evelyn/tools/benchmark_rag.py` script computing Hit Rate, MRR, and per-category breakdowns. Includes `--compare` flag for side-by-side embedding model evaluation and `--reformulate` flag for query reformulation testing. L12 vs L6 comparison showed no improvement — bottleneck is query quality, not embedding depth. *(Completed 2026-04-26)*
- [x] **RAG Query Reformulation**: Implemented `Evelyn/tools/query_reformulator.py` — uses the already-loaded Gemma 4 (think=false, num_predict=50, ~3s per call) to extract search keywords from conversational messages before embedding. Skip heuristic for short messages (< 4 words). Wired into `build_rag_context()` with pinned alias matching still using the original query. **Results: 36% → 59% hit rate** on conversational queries. Remaining gap is vocabulary mismatch between user phrasing and document terminology. *(Completed 2026-04-26)*
- [x] **Gist Vocabulary Bridge**: Enhanced the vault map gist prompt to include a `Keywords:` line with 5-8 conversational search terms per document. Also tightened summary language (no more "narrator" phrasing), set `think: False` and `num_predict: 200` for gist generation, removed entity write-back/atlas/Last Week generation, and added periodic checkpointing to scan_vault(). Full vault rebuild in progress. *(Completed 2026-04-26)*
- [x] **Keyword-to-Tag Pipeline — Phase 1**: Backfill `kw/` tags to all vault files from existing gist keywords. Implemented `apply_keyword_tags.py` (Clean Slate strategy: purge stale `kw/` tags, preserve manual tags, inject fresh `kw/` tags). Tagged 1,840 files in ~18s. Idempotent via mtime tracking. Handles both inline and multiline YAML frontmatter; normalizes to inline on write. Recovery tooling created: `repair_lost_tags.py`, `repair_journal_tags.py`, `sync_vault_map_tags.py`. *(Completed 2026-05-02)*
- [x] **Context Categories Refactor**: Consolidate `Cat01.md` through `Cat16.md` descriptions directly into `Cat00 - Index.md` and delete the standalone files to reduce vault clutter. Update the index to link directly to the Evelyn/Ricky summarized categories and folders. Backlinks normalized to `[[Cat00 - Index#Category XX]]` format; 9 orphaned links resolved. *(Completed 2026-05-02)*
- [x] **Journal & Context Entry Location**: Journal entries now write directly to `Evelyn's Journal/` (bypassing `Pending_Approvals/`) via `JOURNAL_DIRECT_WRITE = True` in `evelyn_config.py`. Context entries moved to `Evelyn/Evelyn's Context/Context Entries/Pending/` for visibility in the vault sync pipeline. *(Completed 2026-05-02)*
- [x] **UI Background Task Polling**: Replaced blind 4-second button timeouts on Vault Map and Sync buttons with a robust polling loop (`GET /task_status/{task_name}`). In-memory `_background_tasks` dict tracks `running → done/error` state. UI injects `.system-notice` status messages into the chat area. Poll handles server restart (`unknown` status) and repeated network errors (3-strike bail-out). Vault map subprocess output now streams to console in real-time. *(Completed 2026-05-02)*
- [x] **Consolidation Verdict Safety**: Patched `pending_reviewer.py` to correctly handle `keep_both` verdicts in the consolidation approval queue, ensuring original source files are preserved rather than deleted. *(Completed 2026-05-18)*
- [x] **Memory Extraction Prompt Hardening**: Upgraded the system and user prompts inside `fact_extractor.py` to enforce factual, objective writing. Injected a strict behavior priming guard instructing the model to write pure observations without meta-commentary, evaluation, or embedding category titles in the summary. *(Completed 2026-05-18)*
- [x] **Taxonomy Context-Entry Remediation**: Developed a multi-phase cleanup pipeline for context entries containing meta-commentary and taxonomy titles. Cleaned ~300 entries via regex, and executed an optimized background batch-cleaning script using `gemma4:26b` to rewrite remaining entries. Disabled reasoning traces and limited context size to `4096` tokens in the background script to yield a 60x processing speedup (down to ~1.3s per entry). *(Completed 2026-05-18)*
- [x] **Workspace Tool Cleanup**: Reorganized the Evelyn `tools` folder by moving one-time experimental and repair scripts to the root `scratch` directory, neatly structured into categorised subfolders (`lovelang/`, `gists/`, `misc_debug/`). Promoted `remove_wikilinks.py` to a permanent system utility in `tools` alongside the `undo_thread.py` tool. *(Completed 2026-05-18)*
- [x] **Frontmatter Ingestion Stabilization**: Migrated the YAML frontmatter and title injection trigger from a heavy VS Code Integrated Terminal task (`Trigger Task on Save` extension) to the silent background `emeraldwalk.runonsave` extension. Resolves the terminal-conflict bug that forcefully closed the `Evelyn Server` process, reduces file-save trigger latency from ~1.5s to <50ms, and eliminates all terminal panel clutter. *(Completed 2026-05-19)*
- [x] **Unified Memory Refresh (Option BC)**: Merged the separate Vault Map and Sync operations into a single `POST /refresh_memory` endpoint backed by `Evelyn/tools/refresh_memory.py`. Uses `asyncio.create_subprocess_exec` so the FastAPI event loop stays unblocked during all three phases (Vault Map → Core Knowledge Ingest → Gist Ingest). Phase-tagged stdout (`[PHASE_START:]`, `[PHASE_DONE:]`, `[PHASE_FAIL:]`) drives real-time status updates via the existing `_background_tasks` / `GET /task_status/refresh_memory` polling API. The two old `🗺 Vault Map` and `⟳ Sync` header buttons replaced by a single `✦ Refresh Memory` button with phase-label polling at 10s intervals. Old `/vault_map` and `/sync` endpoints preserved for direct API access. *(Completed 2026-05-21)*
- [x] **Idle-Time Fact Extraction**: `fact_extractor.py` reads directly from `evelyn_chat.db` using a persistent high-water mark (`evelyn_extraction_state.json`). Only new messages since the last successful run are processed — zero duplicate extractions across restarts. Runs during server idle time (5 min threshold) as a standalone background task, completely decoupled from the summarizer. Message timestamps injected into the transcript so the LLM dates each extracted fact to when it was actually discussed. Structural markers (`[THREAD_BREAK]`, `[Response interrupted]`) filtered before the LLM call. Mutual exclusion guard prevents overlap with the consolidator. Config: `FACT_EXTRACTION_*` keys in `evelyn_config.py`. *(Completed 2026-05-04)*
- [x] **Idle-Time Fact Consolidation & Pending Reviewer**: Developed a robust background system to detect duplicate/superseded memory context entries during server idle time. Includes anchor-based all-pairs scanning, an interactive terminal reviewer (`pending_reviewer.py`), duplicate suppression, and strict category taxonomy enforcement. *(Completed 2026-05-19)*
- [x] **Tool Schema Refactor**: Removed `log_context_fact`, `update_context_fact`, and `sync_context_memory` from the Ollama model-facing tool schema. Saves ~653 tokens per request (~4% of the 16k context window). Functions remain in `TOOL_FUNCTIONS` for system dispatch. `TOOL_DEFINITIONS` renamed to `MODEL_TOOL_DEFINITIONS` to make the distinction explicit. *(Completed 2026-05-04)*

## Phase 4: Data Architecture & Ecosystem (Planned)

*Goal: Evolve the underlying databases, formalize tool metadata, and tighten the Obsidian knowledge link.*

- [x] **Context Entries → SQLite Migration**: Replace the `Cat##/Cat##-{E,R}/*.md` flat-file layout with a proper SQLite table (columns: `id`, `category`, `subject`, `date`, `summary`, `confidence`, `source`, `created_at`). Eliminates all file-scanning overhead in the extractor and consolidator, enables true all-pairs indexing with a single join. (Completed 2026-05-24)
- [x] **Vault Map → SQLite Migration**: Migrated the massive `vault_map_data.json` flat-file index into a proper SQLite database (`evelyn_vault.db`). Enables millisecond-fast incremental `UPSERT` updates, eliminates full-vault JSON rebuilds, allows scalable relationship modeling (links/backlinks), and drops memory usage by preventing the massive JSON file from being loaded into RAM by all downstream RAG scripts. *(Completed 2026-05-24)*
- [x] **Formal Tool Metadata schema**: Added a `tool_metadata` JSON column to the SQLite `messages` table. Instead of string-hacking tool outputs into the `tools_used` column, stores a structured key-value map mapping tool executions to their raw output file names/identifiers (e.g., `{"write_journal_entry": "2026-05-23.md"}`). *(Completed 2026-05-24)*
- [x] **Upgraded Tool Badges & UI Viewers**: Leveraged the `tool_metadata` column to turn all write-tool badges into interactive elements. Provided clickable links or inline UI modals for *all* outputs: viewing generated images and reading newly created journal entries directly in the chat UI without opening Obsidian. *(Completed 2026-05-24)*
- [x] **Developer Web UI**: Built a dedicated browser-based interface (`dev.html`) for tool access, replacing terminal scripts for the Review Queues (Extracted facts, Pending proposals). Features a touch-optimized card layout, inline markdown rendering for source CEs, keyboard shortcuts for triage, and full state hydration without heavy frontend frameworks. *(Completed 2026-05-24)*
- [x] **Engine Architecture Map**: Designed and deployed a Foam-compatible structural map (`[[engine_architecture.md]]`) using Mermaid visual flowcharts to tie all active standalone scripts and database layers into a fully connected Foam visual graph, completely eliminating orphan script nodes. *(Completed 2026-05-25)*
- [x] **Journal Entry Triage & One-Click Vault Archiver**: Integrated a secure, multi-path draft locator and custom year/month directory parser in the backend (`evelyn_server.py`) and a premium interactive triage banner inside the front-end chat modal. Allows previewing draft reflections from the chat overlay and filing them cleanly into the chronological Obsidian Vault folders with a single tap. *(Completed 2026-05-26)*
- [x] **Proactive Memory Refresh & Idle Maintenance**: Integrated automatic background memory refresh subprocess triggers on successful user-action completions (journal approval, fact extraction approval, or proposal execution) alongside a low-priority deep idle background maintenance loop (running once every 2 hours when idle 45m+) to keep vector stores perfectly synced with out-of-band vault edits. *(Completed 2026-05-26)*
- [ ] **Obsidian Related Documents Plugin**: Custom Obsidian plugin that displays semantically related documents in a sidebar panel. Leverages the `#kw/` and `#ctx/` tags written by the Keyword-to-Tag Pipeline — ranks related notes by tag overlap count (no LLM call needed at runtime).
- [ ] **Ghost Link Manifestation**: Auto-create stub notes for high-frequency unresolved wiki-links in the Obsidian vault. When the Fact Extraction pipeline identifies entities that match existing ghost links (tracked by `ghost_link_counter.py`), generate a templated stub note with auto-extracted context.

## Phase 5: Embodiment & Advanced Senses (Future)

*Goal: Give Evelyn a physical presence and deeper environmental awareness.*

- [ ] **Visuals**: Add v-tuber style avatar and animation system.
- [ ] **Awareness**: Add real-time visual awareness.
- [ ] **XR**: Add VR/AR integration.

## Phase 6: Open Source & Community (Future)

*Goal: Share the "Evelyn Engine" as a template for hyper-personalized local AI.*

- [x] **Privacy Guardrails**: Implement `.gitignore` to separate personal "Soul" data from the code "Engine."
- [ ] **Template Sanitization**: Create generic versions of persona files for others to fill in.
- [ ] **Documentation**: Write a "How to Build Your Own Evelyn" guide.
- [ ] **GitHub Repository**: Initialize the public template repository.
