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
- [x] Implement time awareness via Open WebUI built-in Time & Calculation tool + behavioral directive.
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
- [x] **Architecture Overhaul**: Retired Modelfile pipeline; Open WebUI model builder is now the sole authority for model config, system prompt, and parameters.
- [x] **Prompt Engineering**: Rewrote Evelyn persona (first-person structured), system prompt (with tool priority ordering), and RAG prompt.
- [x] **Sync Scripts**: Fixed state-based file_id tracking in ingest scripts; fixed openwebui_sync_tool.py Phase 2 duplication bug.
- [x] **Workspace Cleanup**: Reorganized reference/, archived stale outputs, renamed status checker script.
- [ ] **Backup**: Regularly push code "Engine" to GitHub using the `backup-to-github` workflow.
- [ ] **RAG Tuning**: Optimize chunk size and similarity thresholds for Magistral 24B. Per the Magistral paper (arXiv:2506.10910), the model has a **128k context window** but is trained with a **32k–40k reasoning budget**. Target: **512–768 token chunks** (post-frontmatter strip) with **100–150 token overlap**, `RAG_TOP_K = 3–5`. This keeps retrieved context under 4k tokens, preserving headroom for reasoning traces.
- [/] **Search Priority Order**: Enforce Gist-first → Core Knowledge → Obsidian → Web Search tool priority. **Phase 1 done**: tool `description` strings in `evelyn_tools.py` updated with explicit STEP 1/STEP 2 ordering and DO NOT use guards. **Phase 2 pending**: RAG pre-processing and frontmatter stripping.
- [ ] **RAG Pre-processing**: Strip YAML frontmatter and Obsidian headers from knowledge documents before ingestion so RAG chunks contain dense content, not metadata. Prevents context lobotomy from header-bloated chunks.
- [ ] **RAG Summarize-Before-Inject**: Add a lightweight summarization step between RAG retrieval and context injection for large documents — Evelyn summarizes the chunk before reasoning on it, keeping context lean.
- [x] **NUM_CTX Uplift**: Confirmed GPU is **RTX 4070 (12 GB VRAM)**. Magistral 24B at Q4_K_M ≈ 13.5 GB model weights \u2014 already CPU-offloads some layers. Current `NUM_CTX = 16384` is the correct ceiling for this hardware. Raising it would risk OOM or severe latency. **Resolution: keep 16384.** See `reference/system/system_specs.md` for full analysis.
- [x] **Model Testing**: Evaluated aia/Dolphin3.0-Mistral-24B and CognitiveComputations/dolphin-mistral-nemo against mistral-small3.1. **Result: mistral-small3.1 retained.** Nemo was too fantastical/non-grounded; Dolphin 24B had no memory anchoring and hallucinated. Small uses vault retrieval correctly and now actively calls the context update tool.
- [ ] **Entity Resolution**: Investigate Schyler entity mismatch — model matched `Schyler Sekulich` (vault file) but tried to update `Schyler (persona)` (different entry). Review context_manager.py entity lookup logic.

## Future Expansion

*Experimental features and high-level upgrades.*

- [ ] **Visuals**: Add v-tuber style avatar and animation system.
- [ ] **Awareness**: Add real-time visual awareness.
- [ ] **XR**: Add VR/AR integration.
- [ ] **Voice Nuance**: Explore and implement TTS emotional tags (Qwen3 TTS).
- [ ] **Web Search Tool**: Build a custom `search_web` tool backed by **SearXNG** (self-hosted, free, no API key) rather than Tavily. SearXNG queries Google/Bing/DDG and returns clean JSON. Wrap as an Open WebUI tool with a tight trigger docstring — fires only for current events / public info not in the vault. Include a chunk-and-summarize step before injecting results into context to prevent overflow.
- [ ] **Research Mode**: A separate model/pipe or OWUI Skill that bundles web search + a different retrieval priority order — useful for looking things up vs. Evelyn's normal memory-first conversation mode. Investigate OWUI Skills tab as a potential packaging mechanism.

## Phase 6: Open Source & Community (Future)

*Goal: Share the "Evelyn Engine" as a template for hyper-personalized local AI.*

- [x] **Privacy Guardrails**: Implement `.gitignore` to separate personal "Soul" data from the code "Engine."
- [ ] **Template Sanitization**: Create generic versions of persona files for others to fill in.
- [ ] **Documentation**: Write a "How to Build Your Own Evelyn" guide.
- [ ] **GitHub Repository**: Initialize the public template repository.
