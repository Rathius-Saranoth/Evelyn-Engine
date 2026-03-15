# Master Project Roadmap: Evelyn (Local AI)

## Phase 1: Persona & Directives Consolidation (Current Focus)

- [x] Integrate Gemini's narrative suggestions into `Ricky_Narrative_Profile.md`. (Done by Ricky)
- [x] Integrate Gemini's system directive suggestions into `System_Directives.md` (e.g. Two-Level Policy Protocol, Journaling Rules, File Permission constraints). (Done by Ricky)
- [x] Clean up and condense `personal_instructions_gemini_version.md` (remove legacy formatting rules and merge into the unified persona files if needed).
- [x] Create/Update the official `evelyn:v14` Modelfile using ONLY the finalized, cleaned persona instructions (base model: mistral-small3.1).
- [x] Compile and deploy the new version to OpenWebUI.

## Phase 2: RAG & Knowledge Base Tuning

- [x] Optimize Knowledge Base Retrieval Parameters in OpenWebUI. (Gist & Document Chunking)
- [x] Create systems for memory creation, retrieval, and updating thereof. (Journal & Context Facts; inspired by ref doc 4).
- [x] Implement and enable `write_journal_entry` tool (Draft mode now natively enforces Master Protocol format).
- [x] Implement and enable `log_context_fact` tool (Draft mode now natively enforces preview constraint).
- [x] Implement a system that allows for updating context facts to keep them relevant.

## Phase 3: Future Tooling & Features

- [ ] Explore Google Drive File Integration.
- [x] Implement Text-to-Speech (TTS).
  - [x] Deploy local Kokoro TTS API via Docker.
  - [x] Configure OpenWebUI to use the local Kokoro endpoint.
- [x] Implement Speech-to-Text (STT).
- [ ] Implement capabilities of time awareness, scheduling, and reminders.
- [ ] Explore 'always on' functionality to bring a sense of life and day night cycles along with random messages throughout the day if not currently engaged in a conversation.

## Phase 4: Advanced Capabilities & Refinement

- [ ] Add v-tuber style avatar and animation system.
- [ ] Add real-time visual awareness.
- [ ] Add VR integration.
- [ ] Explore and implement TTS emotional tags (from Qwen3 TTS workflow examples).
