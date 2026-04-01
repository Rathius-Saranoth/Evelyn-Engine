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
- [ ] **RAG Tuning**: Optimize chunk size and similarity thresholds for Magistral 24B. Per the Magistral paper (arXiv:2506.10910), the model has a **128k context window** but is trained with a **32k–40k reasoning budget**. Target: **512–768 token chunks** (post-frontmatter strip) with **100–150 token overlap**, `RAG_TOP_K = 3–5`. This keeps retrieved context under 4k tokens, preserving headroom for reasoning traces.
- [/] **Search Priority Order**: Enforce Gist-first → Core Knowledge → Obsidian → Web Search tool priority. **Phase 1 done**: tool `description` strings in `evelyn_tools.py` updated with explicit STEP 1/STEP 2 ordering and DO NOT use guards. **Phase 2 pending**: RAG pre-processing and frontmatter stripping.
- [ ] **RAG Pre-processing**: Strip YAML frontmatter and Obsidian headers from knowledge documents before ingestion so RAG chunks contain dense content, not metadata. Prevents context lobotomy from header-bloated chunks.
- [ ] **RAG Summarize-Before-Inject**: Add a lightweight summarization step between RAG retrieval and context injection for large documents — Evelyn summarizes the chunk before reasoning on it, keeping context lean.
- [x] **NUM_CTX Uplift**: Confirmed GPU is **RTX 4070 (12 GB VRAM)**. Magistral 24B at Q4_K_M ≈ 13.5 GB model weights \u2014 already CPU-offloads some layers. Current `NUM_CTX = 16384` is the correct ceiling for this hardware. Raising it would risk OOM or severe latency. **Resolution: keep 16384.** See `reference/system/system_specs.md` for full analysis.
- [x] **Model Testing**: Evaluated aia/Dolphin3.0-Mistral-24B and CognitiveComputations/dolphin-mistral-nemo against mistral-small3.1. **Result: mistral-small3.1 retained.** Nemo was too fantastical/non-grounded; Dolphin 24B had no memory anchoring and hallucinated. Small uses vault retrieval correctly and now actively calls the context update tool.
- [ ] **Entity Resolution**: Investigate Schyler entity mismatch — model matched `Schyler Sekulich` (vault file) but tried to update `Schyler (persona)` (different entry). Review context_manager.py entity lookup logic.
- [x] **Message History Cap**: `load_history()` was sending every message ever stored to Ollama with no limit. Added `MAX_HISTORY_MESSAGES = 30` (15 turns) config cap. Only the most recent messages are sent to the model; all messages remain in the DB and `/history` UI endpoint.
- [x] **Thread Break System**: Added `[THREAD_BREAK]` marker row and `POST /new_thread` endpoint. "✦ New Thread" button in the UI inserts a boundary — `load_history()` only returns messages after the latest break. Visual `── new thread ──` divider renders in chat history. Gives Evelyn a clean conversational slate without losing any stored messages.
- [x] **Mobile Connection Recovery**: Added Screen Wake Lock API to keep the display alive while streaming (prevents phone screen-off mid-response). Added `visibilitychange` recovery handler — if the SSE connection dies while the page is backgrounded, returning to the page reloads the completed response from the DB.
- [x] **Write-Tool Badges**: Persistent badges on assistant messages when file-writing tools fire: 📓 Journal entry written, 📌 Context fact logged, 📝 Context fact updated, 🎨 Image generated. Applied to both `sendMessage()` and `regenerateResponse()` flows.
- [ ] **Context Summarizer**: Replace the hard message-count cap with an intelligent sliding window — a lightweight summarizer compresses older messages into a lean summary block before they fall off the context. Keeps the most important conversational facts without the fluff, maximizing effective memory within the token budget.
- [ ] **Token Count Display**: Surface per-message or per-request token counts in the chat UI or server console. Enables monitoring of context utilization and early warning when approaching the `num_ctx` ceiling.

## Future Expansion

*Experimental features and high-level upgrades.*

- [ ] **Visuals**: Add v-tuber style avatar and animation system.
- [ ] **Awareness**: Add real-time visual awareness.
- [ ] **XR**: Add VR/AR integration.
- [ ] **Voice Nuance**: Explore and implement TTS emotional tags (Qwen3 TTS).
- [ ] **Web Search Tool**: Build a custom `search_web` tool backed by **SearXNG** (self-hosted, free, no API key) rather than Tavily. SearXNG queries Google/Bing/DDG and returns clean JSON. Register it in `evelyn_tools.py` with a tight trigger docstring — fires only for current events / public info not in the vault. Include a chunk-and-summarize step before injecting results into context to prevent overflow.
- [ ] **Research Mode**: A separate model config or `evelyn_server.py` route that bundles web search + a different retrieval priority order — useful for looking things up vs. Evelyn's normal memory-first conversation mode.
- [ ] **Upgraded Tool Badges**: Enhance the write-tool badges with an expandable detail label showing *which* document was accessed or created (e.g., "📓 Journal: 2026-03-31.md"). Requires passing file path/name back through the tool result into the SSE event stream.
- [ ] **Source Badges**: When RAG documents are referenced in a response, display source indicator badges in the chat showing which vault files contributed to the message. Adds transparency to Evelyn's knowledge retrieval.

## Phase 6: Open Source & Community (Future)

*Goal: Share the "Evelyn Engine" as a template for hyper-personalized local AI.*

- [x] **Privacy Guardrails**: Implement `.gitignore` to separate personal "Soul" data from the code "Engine."
- [ ] **Template Sanitization**: Create generic versions of persona files for others to fill in.
- [ ] **Documentation**: Write a "How to Build Your Own Evelyn" guide.
- [ ] **GitHub Repository**: Initialize the public template repository.
