# Evelyn Project Roadmap

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

## Phase 3: External Senses & Basic Tools (In Progress)

*Goal: Equip Evelyn with a voice and basic file/system interaction.*

- [x] Implement Text-to-Speech (TTS) via local Kokoro API.
- [x] Configure OpenWebUI to use the local Kokoro endpoint.
- [x] Implement Speech-to-Text (STT).
- [x] Implement time awareness via date/time injection in `evelyn_server.py`'s `load_system_prompt()` + behavioral directive.
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
- [x] **ComfyUI VRAM**: Added `--lowvram` flag to Run ComfyUI task so ComfyUI releases model weights from VRAM when idle rather than holding them continuously.
- [x] **On-Demand Model Unload**: Added "Unload Evelyn Model" VS Code task — sends `keep_alive:0` to Ollama API to evict the model from VRAM without stopping the server. Frees ~9.2 GB VRAM for gaming or other GPU-intensive workloads.
- [x] **RAG Tuning**: Optimize chunk size and similarity thresholds for Magistral 24B. Per the Magistral paper (arXiv:2506.10910), the model has a **128k context window** but is trained with a **32k–40k reasoning budget**. Target: **512–768 token chunks** (post-frontmatter strip) with **100–150 token overlap**, `RAG_TOP_K = 3–5`. This keeps retrieved context under 4k tokens, preserving headroom for reasoning traces.
- [x] **Search Priority Order**: Enforce Gist-first → Core Knowledge → Obsidian → Web Search tool priority. **Phase 1 done**: tool `description` strings in `evelyn_tools.py` updated with explicit STEP 1/STEP 2 ordering and DO NOT use guards. **Phase 2 done**: RAG pre-processing and frontmatter stripping.
- [x] **RAG Pre-processing**: Strip YAML frontmatter and Obsidian headers from knowledge documents before ingestion so RAG chunks contain dense content, not metadata. Prevents context lobotomy from header-bloated chunks.
- [x] **RAG Summarize-Before-Inject**: Add a lightweight summarization step between RAG retrieval and context injection for large documents — Evelyn summarizes the chunk before reasoning on it, keeping context lean.
- [x] **NUM_CTX Uplift**: Confirmed GPU is **RTX 4070 (12 GB VRAM)**. Magistral 24B at Q4_K_M ≈ 13.5 GB model weights \u2014 already CPU-offloads some layers. Current `NUM_CTX = 16384` is the correct ceiling for this hardware. Raising it would risk OOM or severe latency. **Resolution: keep 16384.** See `reference/system/system_specs.md` for full analysis.
- [x] **Model Testing**: Evaluated aia/Dolphin3.0-Mistral-24B and CognitiveComputations/dolphin-mistral-nemo against mistral-small3.1. **Result: mistral-small3.1 retained.** Nemo was too fantastical/non-grounded; Dolphin 24B had no memory anchoring and hallucinated. Small uses vault retrieval correctly and now actively calls the context update tool.
- [x] **Gemma 4 26B Evaluation** *(started 2026-04-07, 1-week trial)*: Switched active model from `magistral:24b` to `gemma4:26b` (MoE, 26.8B total / 3.8B active). Required Ollama upgrade from 0.18.2 → 0.20.3. Initial findings: 47% GPU / 53% CPU split (slight improvement over Magistral's ~40/60), noticeably faster token streaming due to MoE sparse activation, tool calling confirmed working (journal write on first test). `magistral:24b` kept as commented-out fallback in `evelyn_config.py`. **Promote to permanent if no regressions by ~2026-04-14.**
- [x] **Entity Resolution**: Investigate Schyler entity mismatch — model matched `Schyler Sekulich` (vault file) but tried to update `Schyler (persona)` (different entry). Review context_manager.py entity lookup logic.
- [x] **Message History Cap**: `load_history()` was sending every message ever stored to Ollama with no limit. Added `MAX_HISTORY_MESSAGES = 30` (15 turns) config cap. Only the most recent messages are sent to the model; all messages remain in the DB and `/history` UI endpoint.
- [x] **Thread Break System**: Added `[THREAD_BREAK]` marker row and `POST /new_thread` endpoint. "✦ New Thread" button in the UI inserts a boundary — `load_history()` only returns messages after the latest break. Visual `── new thread ──` divider renders in chat history. Gives Evelyn a clean conversational slate without losing any stored messages.
- [x] **Mobile Connection Recovery**: Added Screen Wake Lock API to keep the display alive while streaming (prevents phone screen-off mid-response). Added `visibilitychange` recovery handler — if the SSE connection dies while the page is backgrounded, returning to the page reloads the completed response from the DB.
- [x] **Write-Tool Badges**: Persistent badges on assistant messages when file-writing tools fire: 📓 Journal entry written, 📌 Context fact logged, 📝 Context fact updated, 🎨 Image generated. Applied to both `sendMessage()` and `regenerateResponse()` flows.
- [x] **Context Summarizer**: Implemented async sliding-window summarizer (`context_summarizer.py`). Compresses older messages (beyond the active 20-msg window) into a ~200-word summary injected into the system prompt. Runs in background via `asyncio.create_task()` after each response — zero user-facing latency. Uses same model/`num_ctx` via in-process Ollama call (no model swap). Cache rebuilds on server startup; invalidates on thread break. Config in `evelyn_config.py`: `SUMMARY_WINDOW_SIZE`, `SUMMARY_MAX_WORDS`, `SUMMARY_OVERLAP`, `SUMMARY_MODEL`.
- [ ] **Token Count Display**: Surface per-message or per-request token counts in the chat UI or server console. Enables monitoring of context utilization and early warning when approaching the `num_ctx` ceiling.
- [x] **Engineering Standards**: Codified Dave Plummer's "Notes to Live By" quality gates and operational disciplines into `.ai-instructions.md` §2. Added `/quality-review` workflow for structured self-review.
- [ ] **Evelyn Axiom Injection**: Embed a standing engineering axiom (e.g., "Every line of code has mass") into Evelyn's system directives. Deferred until Evelyn has code-generation capabilities.
- [ ] **Prompt & Docstring Lean-Out**: Audit and compress Evelyn's system prompt and all tool docstrings for token efficiency. Strip redundancy, tighten language, eliminate verbose phrasing that costs context window without adding signal.
- [x] **Suppress Windows Asyncio Noise**: The `ProactorEventLoop` on Windows throws noisy `ConnectionResetError` tracebacks when browser polling requests disconnect mid-response. Harmless but clutters the console. Suppress or compress to a single-line warning.
- [x] **RAG Retrieval Benchmark**: Built golden test set (`reference/rag_benchmark_queries.json`, 25 queries across 6 categories) and standalone `Evelyn/tools/benchmark_rag.py` script computing Hit Rate, MRR, and per-category breakdowns. Includes `--compare` flag for side-by-side embedding model evaluation and `--reformulate` flag for query reformulation testing. L12 vs L6 comparison showed no improvement — bottleneck is query quality, not embedding depth. *(Completed 2026-04-26)*
- [x] **RAG Query Reformulation**: Implemented `Evelyn/tools/query_reformulator.py` — uses the already-loaded Gemma 4 (think=false, num_predict=50, ~3s per call) to extract search keywords from conversational messages before embedding. Skip heuristic for short messages (< 4 words). Wired into `build_rag_context()` with pinned alias matching still using the original query. **Results: 36% → 59% hit rate** on conversational queries. Remaining gap is vocabulary mismatch between user phrasing and document terminology. *(Completed 2026-04-26)*
- [x] **Gist Vocabulary Bridge**: Enhanced the vault map gist prompt to include a `Keywords:` line with 5-8 conversational search terms per document. Also tightened summary language (no more "narrator" phrasing), set `think: False` and `num_predict: 200` for gist generation, removed entity write-back/atlas/Last Week generation, and added periodic checkpointing to scan_vault(). Full vault rebuild in progress. *(Completed 2026-04-26)*
- [x] **Keyword-to-Tag Pipeline — Phase 1**: Backfill `kw/` tags to all vault files from existing gist keywords. Implemented `apply_keyword_tags.py` (Clean Slate strategy: purge stale `kw/` tags, preserve manual tags, inject fresh `kw/` tags). Tagged 1,840 files in ~18s. Idempotent via mtime tracking. Handles both inline and multiline YAML frontmatter; normalizes to inline on write. Recovery tooling created: `repair_lost_tags.py`, `repair_journal_tags.py`, `sync_vault_map_tags.py`. *(Completed 2026-05-02)*
- [x] **Context Categories Refactor**: Consolidate `Cat01.md` through `Cat16.md` descriptions directly into `Cat00 - Index.md` and delete the standalone files to reduce vault clutter. Update the index to link directly to the Evelyn/Ricky summarized categories and folders. Backlinks normalized to `[[Cat00 - Index#Category XX]]` format; 9 orphaned links resolved. *(Completed 2026-05-02)*
- [x] **Journal & Context Entry Location**: Journal entries now write directly to `Evelyn's Journal/` (bypassing `Pending_Approvals/`) via `JOURNAL_DIRECT_WRITE = True` in `evelyn_config.py`. Context entries moved to `Evelyn/Evelyn's Context/Context Entries/Pending/` for visibility in the vault sync pipeline. *(Completed 2026-05-02)*
- [x] **UI Background Task Polling**: Replaced blind 4-second button timeouts on Vault Map and Sync buttons with a robust polling loop (`GET /task_status/{task_name}`). In-memory `_background_tasks` dict tracks `running → done/error` state. UI injects `.system-notice` status messages into the chat area. Poll handles server restart (`unknown` status) and repeated network errors (3-strike bail-out). Vault map subprocess output now streams to console in real-time. *(Completed 2026-05-02)*

## Future Expansion

*Experimental features and high-level upgrades.*

- [ ] **Visuals**: Add v-tuber style avatar and animation system.
- [ ] **Awareness**: Add real-time visual awareness.
- [ ] **XR**: Add VR/AR integration.
- [ ] **Voice Nuance**: Explore and implement TTS emotional tags (Qwen3 TTS).
- [x] **Web Search Tool**: Build a custom `search_web` tool backed by DuckDuckGo Search (ddgs). Register it in `evelyn_tools.py` with a tight trigger docstring — fires only for current events / public info not in the vault. Include a chunk-and-summarize step before injecting results into context to prevent overflow.
- [ ] **Research Mode**: A separate model config or `evelyn_server.py` route that bundles web search + a different retrieval priority order — useful for looking things up vs. Evelyn's normal memory-first conversation mode.
- [ ] **Upgraded Tool Badges**: Enhance the write-tool badges with an expandable detail label showing *which* document was accessed or created (e.g., "📓 Journal: 2026-03-31.md"). Requires passing file path/name back through the tool result into the SSE event stream.
- [ ] **Source Badges**: When RAG documents are referenced in a response, display source indicator badges in the chat showing which vault files contributed to the message. Adds transparency to Evelyn's knowledge retrieval.
- [ ] **Obsidian Related Documents Plugin**: Custom Obsidian plugin that displays semantically related documents in a sidebar panel. Leverages the `#kw/` and `#ctx/` tags written by the Keyword-to-Tag Pipeline — ranks related notes by tag overlap count (no LLM call needed at runtime). Significantly better than existing "similar notes" plugins because the tags are LLM-classified, not regex/NLP extracted. *(Added 2026-04-26)*
- [x] **Idle-Time Fact Extraction**: `fact_extractor.py` reads directly from `evelyn_chat.db` using a persistent high-water mark (`evelyn_extraction_state.json`). Only new messages since the last successful run are processed — zero duplicate extractions across restarts. Runs during server idle time (5 min threshold) as a standalone background task, completely decoupled from the summarizer. Message timestamps injected into the transcript so the LLM dates each extracted fact to when it was actually discussed. Structural markers (`[THREAD_BREAK]`, `[Response interrupted]`) filtered before the LLM call. Mutual exclusion guard prevents overlap with the consolidator. Config: `FACT_EXTRACTION_*` keys in `evelyn_config.py`. *(Completed 2026-05-04)*
- [x] **Idle-Time Fact Consolidation**: `fact_consolidator.py` scans all live `Context Entries/Cat##/Cat##-{E,R}/*.md` files during server idle time. Uses LLM with `think=True` + Cat00 taxonomy injection to detect duplicates/conflicts/superseded facts. Scanning uses an **anchor-based all-pairs strategy**: one entry is held fixed while all other entries in the category scroll past it in batches — guaranteeing every entry is eventually compared against every other entry across many idle passes. Per-category scan state `(anchor, offset, N)` is persisted to `evelyn_consolidation_offsets.json`; resets if N changes. Category rotation is round-robin via `_group_start_index`. *(Completed 2026-05-04; anchor-based scanning added 2026-05-04)*
  - **Stabilization** *(2026-05-05)*: Fixed systematic 90s timeout failures. Detection calls (`think=False`, `num_predict=512`) now complete in ~10–20s; proposal calls (`think=True`, `num_predict=3072`) have separate headroom. `CONSOLIDATION_TIMEOUT` raised to 150s. `CONSOLIDATION_MAX_RECORDS_PER_GROUP` reduced to 15 for focused comparisons. Error logging now distinguishes `ReadTimeout` from other exceptions. Added `_heavy_tasks_running()` mutual exclusion guard — any task registered in `evelyn_server._background_tasks` with `status=running` defers idle consolidation. Future heavy tasks inherit this guard automatically.
  - **Decoupled Output Files** *(2026-05-05)*: Detection pass now returns `(clusters, recat_items)` separately. `CONSOLIDATION_*.md` → strictly merge/supersede proposals; `RECATEGORIZE_*.md` → single-entry category move proposals (no LLM call, instant write). `source_date` frontmatter field added to consolidation proposals (most-recent source entry date) for chronological CE_ naming on approval. Removes the previous confusing mixed-mode proposal files.
  - **Context Reviewer Phase 2** *(planned)*: Interactive terminal script (`context_reviewer.py`) will add `CONSOLIDATION` and `RECATEGORIZE` review flows. `CONSOLIDATION` approval uses `source_date` for the new CE_ filename. `RECATEGORIZE` approval moves the file and updates its `Primary:` tag.
- [x] **Tool Schema Refactor**: Removed `log_context_fact`, `update_context_fact`, and `sync_context_memory` from the Ollama model-facing tool schema. Saves ~653 tokens per request (~4% of the 16k context window). Functions remain in `TOOL_FUNCTIONS` for system dispatch. `TOOL_DEFINITIONS` renamed to `MODEL_TOOL_DEFINITIONS` to make the distinction explicit. *(Completed 2026-05-04)*
- [ ] **Ghost Link Manifestation**: Auto-create stub notes for high-frequency unresolved wiki-links in the Obsidian vault. When the Fact Extraction pipeline identifies entities that match existing ghost links (tracked by `ghost_link_counter.py`), generate a templated stub note with auto-extracted context. Turns the "unresolved links" list into a self-healing system — ghost links are progressively manifested as conversations naturally mention them. Requires: fact extraction pipeline + ghost link inventory + stub templates per note type (contact, project, concept). *(Added 2026-04-26)*
- [ ] **Context Entries → SQLite Migration**: Replace the `Cat##/Cat##-{E,R}/*.md` flat-file layout with a proper SQLite table (columns: `id`, `category`, `subject`, `date`, `summary`, `confidence`, `source`, `created_at`). Eliminates all file-scanning overhead in the extractor and consolidator, enables true all-pairs indexing with a single join, and makes the anchor-based scan state trivially persistent (just store row IDs). The markdown files become export artifacts rather than the source of truth. Prerequisite: finalize the context entry schema; migration script to bulk-import existing `.md` files. *(Added 2026-05-04)*

## Phase 6: Open Source & Community (Future)

*Goal: Share the "Evelyn Engine" as a template for hyper-personalized local AI.*

- [x] **Privacy Guardrails**: Implement `.gitignore` to separate personal "Soul" data from the code "Engine."
- [ ] **Template Sanitization**: Create generic versions of persona files for others to fill in.
- [ ] **Documentation**: Write a "How to Build Your Own Evelyn" guide.
- [ ] **GitHub Repository**: Initialize the public template repository.
