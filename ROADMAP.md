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
- [ ] Explore Google Drive File Integration.
- [ ] Implement time awareness, scheduling, and reminders.
- [ ] Explore 'always on' functionality (day/night cycles & random messages).

---

## Optimization & Refinement

*Ongoing technical improvements and AI guardrails.*

- [x] **AI Instructions**: Formalize `.ai-instructions.md` for assistant continuity.
- [x] **Service Management**: Implement `.agents/workflows/start-services.md`.
- [x] **Coding Standards**: Enforce Google-style Docstrings across core scripts.
- [x] **Version Control**: Initialize local Git repository with protective `.gitignore`.
- [ ] **Backup**: Regularly push code "Engine" to GitHub using the `backup-to-github` workflow.
- [ ] **RAG Tuning**: Further optimize chunk size and similarity thresholds.

## Future Expansion

*Experimental features and high-level upgrades.*

- [ ] **Visuals**: Add v-tuber style avatar and animation system.
- [ ] **Awareness**: Add real-time visual awareness.
- [ ] **XR**: Add VR/AR integration.
- [ ] **Voice Nuance**: Explore and implement TTS emotional tags (Qwen3 TTS).

## Phase 6: Open Source & Community (Future)

*Goal: Share the "Evelyn Engine" as a template for hyper-personalized local AI.*

- [x] **Privacy Guardrails**: Implement `.gitignore` to separate personal "Soul" data from the code "Engine."
- [ ] **Template Sanitization**: Create generic versions of persona files for others to fill in.
- [ ] **Documentation**: Write a "How to Build Your Own Evelyn" guide.
- [ ] **GitHub Repository**: Initialize the public template repository.
