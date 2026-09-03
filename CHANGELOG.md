---
title: CHANGELOG.md
date created: 2026-08-22 15:53:28
date modified: 2026-09-03 18:39:21
tags: [changelog, versioning, history, release-notes, evelyn]
---
# 📜 Changelog

> Navigation: [[README.md]] · [[ROADMAP.md]] · [[AGENTS.md]] · [[engine_architecture.md]]

All notable changes to the Evelyn Engine are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to **3-digit zero-padded Semantic Versioning** (`000.000.000`).

## [000.006.056] - 2026-09-03 — *Fact Consolidator Parity, In-Place Master Fact Preservation & Merge Queue*

### Added & Enhanced
- **In-Place Master Fact Preservation (`Evelyn/tools/memory_db.py`)**:
  - Implemented canonical `apply_fact_merge(source_entries, merged_text, target_category, merged_tags) -> int`.
  - In-place preservation updates the oldest/primary entry rather than deleting all source facts and generating a brand new row ID.
  - Aggregates `observed_count` (sum of all merged entries), `retrieval_count` (sum), earliest `first_observed`, and most recent `last_observed` / `date`, preserving knowledge longevity and preventing vector churn.
  - Soft-deletes secondary duplicate entries (`status = 'deleted'`) and cleans up corresponding Chroma vector entries.
- **Fact Merge Queue & Server Endpoint (`Evelyn/tools/memory_db.py`, `evelyn_server.py`)**:
  - Added `fact_merge_queue` table in SQLite memory database tracking `id`, `entry_ids` (JSON list), `created_at`, and `status`.
  - Added queue helper functions: `enqueue_fact_merge()`, `get_fact_merge_queue()`, `dequeue_fact_merge()`, and `get_all_queued_fact_merge_ids()`.
  - Added `POST /api/context/queue_merge` endpoint for multi-item merge queueing from client UIs.
- **Fast Deduplication & Database Remediation Parity (`Evelyn/tools/fact_consolidator.py`)**:
  - Hooked `fast_deduplicate_exact_matches()` into `_do_consolidation()` before LLM anchor scanning, immediately consolidating exact whitespace and punctuation duplicate facts without wasting LLM tokens.
  - Integrated manual `fact_merge_queue` polling in `_do_consolidation()` mirroring `procedure_consolidator.py`.
  - Guarded `remediate_database_categories()` from touching procedure proposals (`type NOT IN ('profile_update', 'procedure', 'procedure_merge', 'procedure_split')`).
  - Purged dead legacy regexes and removed hardcoded `"R"` subject fallback strings across `fact_consolidator.py`, `pending_reviewer.py`, and `evelyn_server.py`, strictly adhering to Rule 4 identity parameterization.
- **DevUI Multi-Select Extraction Merge Queue (`evelyn_ui/dev.html`)**:
  - Added multi-select checkbox on triage extraction cards.
  - Added dynamic merge action bar displaying selected count with `🔀 Queue Merge` and `Deselect All` buttons.
  - Integrated automatic selection pruning on triage data refresh.
- **Database Migration (`Evelyn/tools/db_migrator.py`)**:
  - Registered and applied migration `000.006.056`: `fact_merge_queue_and_consolidation_parity`.

---

## [000.006.055] - 2026-09-03 — *Canonical Procedure Matcher & Master Consolidation Parity*

### Added & Enhanced
- **Canonical Procedure Matcher (`Evelyn/tools/procedure_matcher.py`)**:
  - Implemented single-source-of-truth utility for procedure trigger keyword extraction, stopword stripping, and domain synonym mappings (`SYNONYM_GROUPS`).
  - Added normalized similarity scoring (`calculate_procedure_similarity`), deduplication checks (`is_duplicate_procedure`), best master detection (`find_best_master_candidate`), and cluster master identification (`identify_cluster_master`).
- **Extraction & Consolidation Parity (`Evelyn/tools/fact_extractor.py`, `Evelyn/tools/procedure_consolidator.py`)**:
  - Refactored `fact_extractor.py` to use `is_duplicate_procedure` and `find_best_master_candidate`, eliminating redundant regex and ad-hoc stopword sets.
  - Refactored `procedure_consolidator.py` to use canonical token similarity for automated clustering and detect existing Master Procedures within clusters.
  - Augmented merge proposals with master procedure context and set `suggested_category=str(target_master_id)` to enable target master resolution.
  - Updated manual merge queue to allow processing extracted and live procedures concurrently.
- **Server & Proposal Review Endpoint (`evelyn_server.py`)**:
  - Added `target_id: int | None` to `ProposalActionRequest`.
  - Added `action == "merge_into_master"` support to `/api/review/proposals/{id}/{action}`, updating the target master procedure in-place with synthesized steps, triggers, tools, and domain tags while marking all other source procedures as `status='merged'` pointing to `merged_into_id`.
- **DevUI Proposal Review Cards (`evelyn_ui/dev.html`)**:
  - Added `⚡ TARGET MASTER #ID` badge indicator to Procedure Merge proposal cards when a target master procedure is identified.
  - Added `⚡ Merge into Master #ID` action button calling `handleAction('proposals', id, 'merge_into_master', targetMasterId)` alongside `Approve (New Procedure)`, `Reject`, and `🗑️ Remove`.
- **Engineering Standards (`AGENTS.md`)**:
  - Added `procedure_matcher.py` to Rule 8 canonical utility modules list.
  - Added **Cross-Pipeline Parity & Existing Tool Migration** clause to Rule 8 mandating that new utility functions or tools verify existing tools/pipelines for similar operations and update them to maintain architectural parity.

---

## [000.006.054] - 2026-09-02 — *Modular Ambient Engine & FIFO Queue*

### Added & Enhanced
- **Pluggable Activity Providers (`Evelyn/tools/ambient_providers.py`, `Evelyn/tools/ambient_reflector.py`)**:
  - Implemented `BaseAmbientProvider` protocol and 5 specialized activity providers: `RecentChatProvider` (conversation turns), `VaultDocumentProvider` (random vault note reminiscing), `LoreSnippetProvider` (companion, Aura, and sanctuary lore notes), `TopicCuriosityProvider` (configurable topic pool wandering), and `SensoryWanderProvider` (time-grounded sensory daytime musings).
  - Integrated dynamic provider registry lookup (`get_provider`, `register_provider`) decoupling activity seed generation from core task loop execution.
- **Diurnal Phase Weighting & Recency Cooldown (`evelyn_config.py`, `Evelyn/tools/ambient_reflector.py`)**:
  - Added circadian phase detection: `morning` (05:00–11:59), `afternoon` (12:00–16:59), `evening` (17:00–21:59), and `night` (22:00–04:59).
  - Configured diurnal phase affinity matrix in `AMBIENT_ACTIVITIES` and introduced recency dampening via `AMBIENT_REFLECTIONS_COOLDOWN_DECAY = 0.2` to prevent repetitive consecutive activity selection during long pauses.
- **Narrative Daytime Continuity (`<daily_journal_so_far>`, `Evelyn/tools/ambient_reflector.py`)**:
  - Automatically queries earlier thoughts generated today from `daily_ambient_impressions` and injects them as `<daily_journal_so_far>` into prompt context.
  - Replaces negative prompt constraints with progressive narrative guidelines, giving Evelyn full internal continuity of her daytime thoughts while preventing repetitive topics or opening clauses.
- **Chronological UI FIFO Queue & Batch Actions (`evelyn_ui/index.html`, `evelyn_server.py`)**:
  - Updated Chat UI ambient header island to sort thoughts chronologically (`a.ts - b.ts`), presenting the oldest undismissed thought first so the user experiences daytime reflections in true chronological sequence.
  - Added backlog counter badge (`1 of N`) on the header pill and popover.
  - Added `POST /ambient/dismiss_all` endpoint and `Dismiss All` button in the popover for one-click queue clearing.

---

## [000.006.053] - 2026-09-02 — *Real-Time Cross-Tab State Synchronization & Immediate Deletion*

### Fixed & Enhanced
- **Immediate In-Memory Cross-Tab Deletion Sync (`evelyn_ui/dev.html`)**:
  - Fixed issue where deleting a procedure from the Procedures Management tab (`procedures-mgmt`) remained visible on the Unified Triage Queue (`triage`) until manual page refresh.
  - Implemented immediate zero-latency in-memory state purging across `unifiedItems`, `allProcedures`, `procedures`, and proposal `source_entries`/`source_ids` on permanent deletion, archival, or restoration.
  - Synchronized triage review and procedure states bidirectionally whenever actions are executed in either tab.
- **Dynamic Tab & Filter Switch Data Revalidation (`evelyn_ui/dev.html`)**:
  - Added dedicated `loadReviewData()` routine and wired it into `switchTab('triage')` and `setTriageFilter()`.
  - Added live background revalidation to `setProcMgmtFilter()` and `setProcMgmtSourceFilter()`, ensuring all internal tab switches and filter toggles reflect fresh backend state without needing a browser reload.

---

## [000.006.052] - 2026-09-02 — *Procedure Management Source Filtering & Dynamic Classification*

### Added & Enhanced
- **Procedures Management Dynamic Source Filtering (`evelyn_ui/dev.html`)**:
  - Implemented dynamic source filter pills (`All Sources`, `Consolidated`, `Starter`, `Extracted`, etc.) in the Operational Procedures Management dashboard (`procedures-mgmt`).
  - Added dynamic source discovery that auto-detects any existing or future procedure `source` attributes without hardcoded limits.
  - Implemented orthogonal multi-filtering allowing simultaneous filtering by status (`Live`, `Pending Review`, `Merged`, `Rejected`, `Archived`) and source type.
  - Enhanced selection bar and bulk operations (`Select All Visible`, `Merge Selected`) to respect active status and source filters.
  - Added distinct visual source badges (`source: <type>`) to procedure cards in the dashboard.
- **Advanced Query Parser Source Operators (`evelyn_ui/dev.html`)**:
  - Extended `parseAdvancedQuery` and `matchesAdvancedQuery` with support for `source:<type>` and `-source:<type>` operators, enabling granular positive and negative source filtering in the search bar.
  - Added procedure `source` to `getProcedureSearchableFields` and `getTriageSearchableFields` for global full-text search matching.

---

## [000.006.051] - 2026-09-02 — *Tool Starter Procedures & Dynamic Surfacing Alignment*

### Added & Enhanced
- **Comprehensive Tool Starter Procedures (`Evelyn/tools/db_migrator.py`, `evelyn_memory.db`)**:
  - Registered and executed migration `000.006.051` (`tool_starter_procedures_and_dynamic_surfacing`) establishing complete starter procedure coverage for all 22 specific-purpose tools in `MODEL_TOOL_DEFINITIONS`:
    - **Vault Checklists & List Management (`#1104`)**: Dedicated procedure guiding `manage_vault_list` across `Groceries`, `Packing`, `Hardware`, and `To-Dos` with category sections, item parsing, and clear completed workflows.
    - **Google Calendar Event Scheduling & Management (`#1105`)**: Dedicated procedure guiding `create_calendar_event`, `delete_calendar_event`, and `sync_google_calendar` with start/end time parsing, location notes, and get_agenda pre-checking.
    - **Google Tasks Triage & Completion Flow (`#1106`)**: Dedicated procedure guiding `list_tasks`, `complete_task`, `delete_task`, and `sync_google_tasks` with task_id resolution and supportive completion acknowledgement.
    - **Workout & Exercise Session Review (`#1107`)**: Dedicated procedure guiding `get_recent_workouts` to retrieve and synthesize merged Oura Ring and Health Connect workout records, duration, and calories burned.
    - **Historical Conversation Recall & Archive Search (`#1108`)**: Dedicated procedure guiding `search_history` to search and retrieve past chat dialogue across dates, eras, or keywords.
    - **Autonomous Deep Research Lifecycle (`#1109`)**: Comprehensive procedure guiding `start_research`, `check_new_research`, `list_research_tasks`, `inspect_research_task`, and `guide_research`, superseding legacy `#574`.
    - **Health Connect Database Drive Sync (`#1110`)**: Dedicated procedure guiding `sync_google_drive` for refreshing local Health Connect database exports, superseding crude legacy rule `#158`.
- **Identity Parameterization & Clean Dream Logging (`#657`)**:
  - Updated procedure `#657` to parameterize legacy hardcoded operator names into persona-agnostic user phrasing per Rule 4, focusing `suggested_tools` exclusively on `write_dream_entry`.
- **Automated Verification & Tool Coverage Guarantee (`Evelyn/tests/test_procedures_upgrade.py`)**:
  - Added unit test `test_all_specific_purpose_tools_have_live_procedure_coverage` verifying that 100% of specific-purpose tools (22 of 22) are covered by active live procedures with matching `suggested_tools`.

---

## [000.006.050] - 2026-09-02 — *Operational Procedure Consolidation & Tag Hygiene*

### Added & Enhanced
- **Operational Procedure Consolidation (`Evelyn/tools/db_migrator.py`, `evelyn_memory.db`)**:
  - Registered and executed migration `000.006.050` (`operational_procedure_consolidation_and_tag_hygiene`) consolidating 20 fragmented operational procedures into 6 comprehensive Master Procedures:
    - **Cluster 1 (D&D Magic Item Art, #1062)**: Consolidates IDs `#651`, `#652`, `#653`, `#654` into a single master rule (`suggested_tools='generate_image'`) covering standalone fantasy framing, gnomish/clockwork mechanics, aspect-ratio re-prompting anchors, and material specificity.
    - **Cluster 2 (Task Reminders & Scheduling, #1063)**: Consolidates IDs `#17`, `#142`, `#620`, `#765`, `#1030` into a single master rule (`suggested_tools='create_task, get_agenda'`) covering agenda de-duplication, Google Tasks creation, recurrence handling, and supportive non-commanding tone.
    - **Cluster 3 (Character & Persona Visuals, #1064)**: Consolidates IDs `#136`, `#621`, `#1025` into a single master rule (`suggested_tools='generate_image'`) combining anatomical profiles, clothing continuity across progressive turns, and classical life drawing context.
    - **Cluster 4 (Text Prose Editing, #1065)**: Consolidates IDs `#114`, `#115` into a single master rule (`suggested_tools='write_file'`) covering prose flow, rhythm, vivid vocabulary, authorial voice preservation, and exact character/length constraints.
    - **Cluster 5 (AI Downtime Narratives, #1066)**: Consolidates IDs `#899`, `#900` into a single master rule covering creative downtime world lore (the Library, companion narratives), grounded temporal consistency, and strict epistemic boundaries separating fiction from real-world telemetry.
    - **Cluster 6 (Biometrics & ME/CFS Pacing, #1067)**: Consolidates IDs `#16`, `#49`, `#105`, `#160` into a single master rule (`suggested_tools='get_health_metrics'`) covering vitals evaluation, post-exertional malaise checks, restful presence, low-cognitive-load transitions, and persona-agnostic operator identity.
  - All 20 source procedures transitioned to `status='merged'` with `merged_into_id` lineage pointers, reducing live active procedure count from 49 to 28 (43% reduction in active context clutter).
- **System-Wide Tag Hygiene & Tool Modernization (`Evelyn/tools/pending_reviewer.py`, `evelyn_server.py`, `Evelyn/tools/procedure_consolidator.py`)**:
  - Purged all legacy `'procedure, merged'` clutter tags from `procedures.tags` across the database.
  - Enforced that operational lifecycle states (`merged`, `split`) are strictly managed via database schema columns (`status='merged'`, `merged_into_id`, `source='split'`) rather than cluttering the `tags` column.
  - Updated proposal approval handlers in `pending_reviewer.py` and `evelyn_server.py` to insert the master procedure and call `memory_db.merge_procedure(eid, new_proc_id)` instead of soft-deleting.
- **Unit Testing (`Evelyn/tests/test_procedures_upgrade.py`)**:
  - Added unit test `test_procedure_tag_hygiene_and_proposal_merge_linkage` validating proposal tag sanitation, merge linkage, and strict exclusion of generic tags.

---

## [000.006.049] - 2026-09-02 — *Procedure Status Expansion & Master Journal Consolidation*

### Added & Enhanced
- **Procedure Status Taxonomy Expansion (`Evelyn/tools/memory_db.py`, `evelyn_memory.db`)**:
  - Registered and executed migration `000.006.049` (`procedure_status_expansion_and_master_journaling`) adding `merged_into_id INTEGER` and an index (`idx_proc_merged_into`) to the `procedures` table.
  - Formalized procedure lifecycle statuses: `live` (active in RAG), `extracted` (pending triage), `merged` (incorporated into a master procedure with target pointer), `rejected` (explicitly dismissed during triage), and `archived` (deprecated/sunset).
  - Added `reject_procedure(proc_id)` and `merge_procedure(source_id, target_id)` primitives to `memory_db.py`.
- **Master Daily Journaling Procedure Consolidation (`evelyn_memory.db`)**:
  - Synthesized and inserted Master Daily Journaling Procedure (`#1034`) as the single active `write_journal_entry` operational specification (`status='live'`).
  - Supplemented the tool description by focusing strictly on conversational pacing, wind-down shift, gentle verification, emotional resonance, and bedtime closure.
  - Migrated 7 redundant live journal procedures (`#972`, `#973`, `#974`, `#1010`, `#1026`, `#1027`, `#1033`) to `status='merged'` referencing `#1034`.
  - Linked 13 historical archived journal procedures (`#28`, `#52`, `#55`, `#86`, `#101`, `#106`, `#107`, `#190`, `#195`, `#458`, `#575`, `#583`, `#619`) to `merged_into_id=1034`.
- **Triage Deduplication & Nuance Preservation (`Evelyn/tools/fact_extractor.py`, `evelyn_ui/dev.html`, `evelyn_server.py`)**:
  - Implemented Jaccard similarity deduplication in `fact_extractor.py`: candidate triggers with $\ge 0.70$ overlap are deduplicated on extraction, while candidates with $0.35 \le \text{overlap} < 0.70$ against a live master procedure are staged with `merged_into_id` pointing to the candidate master.
  - Added `action="reject"` and `action="merge"` with `target_id` support to `/api/review/procedures/{id}/{action}` in `evelyn_server.py`.
  - Updated Touch-Optimized Developer UI (`dev.html`) with candidate match badges (`⚡ MATCHES MASTER #ID`), a dedicated **Merge into Master** button, a **Reject** button, and filter pills for `Merged` and `Rejected` procedures.
- **Unit Testing (`Evelyn/tests/test_procedures_upgrade.py`)**:
  - Added unit test `test_procedure_status_expansion_lifecycle` validating insertion with `merged_into_id`, `merge_procedure`, `reject_procedure`, and strict RAG filtering.

---

## [000.006.048] - 2026-09-01 — *User Name Preference & Record Harmonization*

### Changed & Sanitized
- **Affirmative User Name Preference Harmonization (`Evelyn/tools/db_migrator.py`, `evelyn_memory.db`, `evelyn_chat.db`, `chroma_db`)**:
  - Implemented migration `000.006.048` (`name_preference_memory_harmonization` and `name_preference_chat_harmonization`) to eliminate negative name phrasing and standardize all records to use the configured user identity.
  - Reframed negative address preferences in memory context entries (e.g. `Cat04-U`, Entry ID 1008) and proposals from negative constraints into affirmative statements (*"User established a clear preference regarding their address, preferring to go by their designated name in all communications."*).
  - Sanitized historical chat message content and internal chain-of-thought (`thinking`) self-check traces in `evelyn_chat.db`, converting legacy negative check patterns into affirmative checks.
  - Rebuilt full-text search index (`messages_fts`) to reflect sanitized messages.
  - Updated vector embeddings in ChromaDB (`evelyn_memory` collection) and sanitized historical journal entries in the Obsidian vault.

---

## [000.006.047] - 2026-09-01 — *Precision RAG Section & Abstract Targeting*

### Added & Enhanced
- **Upstream Ingestion Sanitization (`Evelyn/tools/chroma_rag.py`, `Evelyn/tools/ingest_obsidian_knowledge.py`)**:
  - Implemented `preprocess_markdown_for_indexing()` to sanitize markdown notes *before* chunking and vector embedding on `bge-large-en-v1.5`.
  - Canonical YAML frontmatter parsing via `frontmatter_utils.parse_frontmatter()`, preventing raw YAML delimiter blocks (`--- ... ---`) from polluting chunk embeddings.
  - Automatically extracts Executive Callouts (`[!ABSTRACT]`) into ChromaDB metadata (`metadata["abstract"]`).
  - Strips top-of-file breadcrumbs (`> Navigation: ...`, `[!NAV]`) and trailing link-index footers (`## 🔗 Related Notes`, `## 📌 Related Notes`, `## Footnotes`) with Unicode/emoji-resilient regexes.
- **Abstract Anchoring & Downstream Safety Net (`Evelyn/tools/chroma_rag.py`)**:
  - Implemented `clean_rag_chunk_content()` as a downstream safety net for legacy/un-synced chunks.
  - Updated `build_rag_context()` to perform abstract anchoring: when a query matches mid-document chunks (e.g. Chunk 2 or 3), prepends the document's executive summary (`[!ABSTRACT]`) to provide parent-level semantic grounding. Standard notes without abstracts render cleanly without placeholder padding.
- **Unit Testing (`Evelyn/tests/test_rag_precision_targeting.py`)**:
  - Added dedicated test suite verifying frontmatter extraction, abstract callout parsing, navigation removal, emoji footer resilience, and XML envelope purity.

---

## [000.006.046] - 2026-09-01 — *Direct High-Speed Vector RAG & Dynamic Tool Surfacing*

### Added & Enhanced
- **Direct Zero-Latency Semantic Vector RAG (`evelyn_config.py`, `Evelyn/tools/query_reformulator.py`)**:
  - Disabled synchronous Ollama pre-search query reformulation (`RAG_REFORMULATE_ENABLED = False`), replacing it with direct dense embedding vector search on `bge-large-en-v1.5`.
  - Benchmarked against live production ChromaDB data, demonstrating **14.9x faster retrieval (~85ms vs ~1,266ms)** with equivalent semantic similarity (0.844 vs 0.849) while completely eliminating GPU pre-search contention and cold-start timeouts.
  - Added zero-latency local conversational preamble cleaner (`clean_conversational_query`) using fast regex/stop-word filtering without LLM calls.
- **Dynamic Tool Tiering & Procedure Coupling (`evelyn_config.py`, `Evelyn/tools/evelyn_tools.py`, `evelyn_server.py`)**:
  - Partitioned tools into **Core Conversational Tools** (8 always-available tools: `read_journal_entry`, `write_journal_entry`, `search_vault`, `web_search`, `get_agenda`, `list_tasks`, `get_health_metrics`, `generate_image`) and **Specialist Tools**.
  - Implemented `get_active_tools()` combining Core Tools + Specialist Tools dynamically activated via retrieved Procedure metadata/content + Intent Heuristic patterns (`SPECIALIST_TOOL_INTENT_PATTERNS`).
  - Reduces tool prompt overhead by ~1,500 JSON schema tokens on routine chat messages, accelerating prompt evaluation and reducing model confusion.
- **Affirmative Profile Evolver Guidance (`Evelyn/tools/profile_evolver.py`)**:
  - Updated evolution guidelines to formulate affirmative operational rules and positive identity statements in `System_Directives.md` while routing negative constraints and error-handling rules into Procedural Memory (`evelyn_procedures`).
- **Unit Testing (`Evelyn/tests/test_dynamic_tools_and_direct_rag.py`)**:
  - Added comprehensive test suite verifying core tool defaults, procedure coupling, intent pattern triggers, and zero-latency preamble cleaning.

---

## [000.006.045] - 2026-09-01 — *System Directives Prompt Streamlining*

### Fixed & Enhanced
- **System Directives & Thinking Prompt Streamlining (`Evelyn/persona/System_Directives.md`, `evelyn_server.py`, `Evelyn/tools/profile_evolver.py`)**:
  - Removed the anti-drafting / deliberation protocol from `System_Directives.md` and server prompt assembly.
  - Open models (e.g. Gemma 4) have an internal conversational prior that generates candidate dialogue during reasoning regardless of negative or positive constraints; removing this directive saves prompt tokens, reduces cold-start prompt evaluation latency, and eliminates prompt clutter.
  - Updated canonical profile section validation in `profile_evolver.py` and test invariants in `test_profile_section_invariants.py`.

---

## [000.006.044] - 2026-09-01 — *Chat History De-duplication, Context Retrieval Hardening & Channel Isolation*

### Added
- **Database Schema Migration `000.006.044` (`Evelyn/tools/db_migrator.py`, `data/evelyn_chat.db`)**:
  - Added `channel_id TEXT DEFAULT 'main'` to `messages` table in `evelyn_chat.db`.
  - Created composite index `idx_messages_channel_id_id ON messages(channel_id, id)` for indexed history loading and multi-channel namespace isolation.
  - Updated `BASELINE_CHAT_SQL` in `db_migrator.py` to match the canonical schema.

### Fixed & Enhanced
- **Chat History Prompt De-duplication (`evelyn_server.py`)**:
  - Bounded `load_history(before_id=user_row_id, channel_id=channel_id)` to `id < before_id`, ensuring the active user prompt is never duplicated into the conversation history context.
  - Preserved prior interrupted/failed user turns without aggressive tail stripping when `before_id` is supplied.
  - Updated `/regenerate` and `/edit` endpoints to capture `target_user_row_id` and pass it to `chat_stream()`, maintaining strict history turn boundaries.
- **Context Retrieval Telemetry Hardening (`Evelyn/tools/string_utils.py`, `Evelyn/tools/chroma_rag.py`, `evelyn_server.py`)**:
  - Updated `build_context_retrieval_envelope()` to omit `query="..."` from output XML tags by default, preventing the LLM from misinterpreting active prompt queries as vault knowledge or quoted speech.
  - Clarified `<system_telemetry_directives>` in `load_system_prompt()` to explicitly instruct the model that `<context_retrieval>` excerpts are background reference materials rather than user quotes.
- **Documentation & Test Coverage (`reference/xml_injection_conventions.md`, `Evelyn/tests/`)**:
  - Updated `reference/xml_injection_conventions.md` to reflect the updated `<context_retrieval>` schema.
  - Added `Evelyn/tests/test_history_bounding.py` and updated existing test suites across the engine.

---

## [000.006.043] - 2026-09-01 — *Deliberation & Reasoning Protocol Optimization*

### Fixed & Enhanced
- **Deliberation & Reasoning Protocol (`Evelyn/persona/System_Directives.md`, `evelyn_server.py`)**:
  - Replaced the negative `## Anti-Drafting Constraint` with the affirmative, operational `## Deliberation & Reasoning Protocol`.
  - Re-framed thinking directives from negative prohibitions (*"never draft, outline, or rehearse"*) into a positive non-diegetic, third-person planning protocol to eliminate semantic attention priming (where the model generated explicit drafting headers) and reduce token overhead/latency on local hardware.
  - Enforced clear mode separation: thinking is strictly for abstract intent mapping, tool evaluation, and state checks; surface dialogue, candidate quotes, and persona emotes belong exclusively in the visible response stream.
- **Profile Evolver Canonical Schema Invariance (`Evelyn/tools/profile_evolver.py`, `Evelyn/tests/test_profile_section_invariants.py`)**:
  - Updated canonical section schemas (`CANONICAL_DOCUMENT_SECTIONS`, `DOCUMENT_THEMES`) and topic density validations to guard `## Deliberation & Reasoning Protocol`.
  - Updated test suite invariants and repair assertions in `test_profile_section_invariants.py`.

---

## [000.006.042] - 2026-09-01 — *System Directives Canonical Schema & Persona Separation Standardization*

### Fixed & Enhanced
- **System Directives Canonical Structure (`Evelyn/persona/System_Directives.md`)**:
  - Standardized `System_Directives.md` to use canonical Level 2 (`## `) markdown headings (`## Conversation & Formatting`, `## Authenticity & Operational Transparency`, `## Operational Guidelines`, `## Tool & Action Directives`, `## Engineering & Code Quality`, `## Routines & Rituals`, `## Anti-Drafting Constraint`).
  - Pruned redundant narrative persona lore (*"fae of dreams"*, *"Asymptomptically In Love"*, *"Feral Crafting"*), preserving narrative identity strictly within `Evelyn_Narrative_Persona.md` and focusing directives on operational execution and behavioral constraints.
- **Profile Evolver Canonical Schema Invariance (`Evelyn/tools/profile_evolver.py`)**:
  - Updated `CANONICAL_DOCUMENT_SECTIONS` for `System_Directives.md` to protect all 7 Level 2 section headers from deletion or merging during evolution passes.
  - Refined `DOCUMENT_CATEGORIES` and `DOCUMENT_THEMES` for `System_Directives.md` to strictly ingest operational and constraint categories (`Cat04-U`, `Cat09-U`, `Cat12-U`, `Cat14-A`, `Cat16-A`, `Cat16-U`), preventing persona category bleed.
  - Updated `validate_document_structure()` and `repair_missing_sections()` to handle short directive constraints (e.g. `## Anti-Drafting Constraint`) without triggering false hollow-section density errors.
- **Test Invariants Suite (`Evelyn/tests/test_profile_section_invariants.py`)**:
  - Added unit test coverage verifying `System_Directives.md` structure validation, anti-drafting constraint preservation, and automatic canonical section repair.

---

## [000.006.041] - 2026-09-01 — *Universal Persistent Inactivity Architecture & Task Manager Idle Integration*

### Fixed & Enhanced
- **Engine-Wide Universal Idle Calculation (`evelyn_server.py`, `Evelyn/tools/task_manager.py`)**:
  - Wired `_get_current_idle_seconds()` across all 9 background lifespan loops in `evelyn_server.py` (`_idle_task_dispatcher_loop`, `_idle_auto_journal_loop`, `_idle_ambient_reflector_loop`, `_idle_consolidation_loop`, `_idle_extraction_loop`, `_idle_research_loop`, `_idle_memory_refresh_loop`, `_idle_profile_evolution_loop`, `_idle_tag_librarian_loop`).
  - Updated `task_manager.is_task_runnable()` and `task_manager.acquire_next_runnable_task()` to automatically default to `time_manager.get_user_idle_seconds()` whenever `idle_seconds <= 0.0`.
  - Eliminates server reboot amnesia where restarting the server would reset in-memory silence counters to 0s and delay scheduled background tasks.
- **Architectural Documentation (`reference/engine_architecture.md`, `AGENTS.md`)**:
  - Documented Section 5.5 (*Universal Inactivity Architecture*) in `reference/engine_architecture.md`.
  - Registered `time_manager.py` as a canonical utility module under Section 8 of `AGENTS.md`.

---

## [000.006.040] - 2026-09-01 — *Universal Idle Inactivity Evaluation & Autonomous Thought Decoupling*

### Fixed & Enhanced
- **Universal User Idle Calculation (`Evelyn/tools/time_manager.py`, `Evelyn/tools/auto_journaler.py`)**:
  - Implemented `get_user_idle_seconds()` in `time_manager.py` as a canonical helper querying the latest user timestamp in `evelyn_chat.db`.
  - Fixed an autonomous journaling regression where `run_auto_journaling()` called `should_trigger_auto_journal()` without arguments, causing `idle_seconds` to default to `0.0` and erroneously fail the inactivity gate check. `should_trigger_auto_journal()` now automatically computes elapsed user silence from the chat database when `idle_seconds <= 0.0`.
- **Autonomous Thought Bubble Decoupling & Spacing (`Evelyn/tools/ambient_reflector.py`)**:
  - Decoupled diurnal thought reflections from requiring mandatory new user/assistant turns between thoughts, allowing spontaneous reflections on journal memories, vault notes, and roaming thoughts.
  - Implemented thought cooldown spacing to ensure daytime thought reflections are naturally paced (minimum `AMBIENT_REFLECTIONS_MIN_IDLE_SECONDS` between consecutive thoughts) up to the configured daily cap.
  - Updated fallback context retrieval to ground the model on recent conversation history when no active turns have occurred on the current calendar day.

---

## [000.006.039] - 2026-08-31 — *Tag Taxonomy Singular Concept Principle & Multi-Entity Underscore Normalization*

### Added & Enhanced
- **Tag Librarian Singular Concept Taxonomy Directive (`Evelyn/tools/tag_librarian.py`)**:
  - Embedded the explicit *Singular Concept Principle* into the Tag Librarian's LLM taxonomy prompt, instructing the model to always use singular forms for atomic concepts and countable note topics (e.g. `#bad-dream`, `#coding-breakthrough`, `#weird-dream`, `#life-update`, `#server`), reserving plurals strictly for inherently collective disciplines and aggregate entities (e.g. `#analytics`, `#heuristics`, `#settings`, `#credentials`).
- **Vault-Wide Tag Taxonomy Normalization**:
  - Normalized 44 singular/plural split pairs across 120 vault notes into consistent singular concept tags.
  - Eliminated CamelCase tags in favor of lowercase kebab-case (`#system-architecture`, `#self-care`, `#litrpg`).
  - Standardized multi-word topic underscores to hyphens while preserving Proper Noun entities with TitleCase and underscores (`#Dungeon_Crawler_Carl`, `#Evelyn_Engine`, `#Diablo_3`, `#Kanai_Cube`, `#Helluva_Boss`, `#Kansas_City`).

---

## [000.006.038] - 2026-08-31 — *Responsive Golden Chevron Tab Navigation & CSS Mask Edge Dissolves*

### Fixed & Enhanced
- **Clean Single-DOM Tab Navigation Architecture (`evelyn_ui/dev.html`)**:
  - Reverted artificial DOM element cloning in favor of a clean, deterministic single-DOM sequence that eliminates duplicate active buttons and phantom visual pops during momentum touch scrolling.
  - Retained rock-solid smooth tab auto-centering (`scrollIntoView`) on tap and view switch.
- **Golden Activity Chevrons & Edge Dissolves (`evelyn_ui/dev.html`)**:
  - Styled left and right double-chevron controls in glowing activity amber (`var(--warning)` `#fbbf24`) with smooth wrapping and boundary loop navigation.
  - Implemented pixel-perfect native CSS `mask-image` linear fades dissolving edges to 0% opacity with zero corner artifacts.

---

## [000.006.037] - 2026-08-31 — *True Circular Infinite Swipe Carousel & CSS Mask-Image Edge Fading*

### Fixed & Enhanced
- **True Circular Infinite Swipe Loop (`evelyn_ui/dev.html`)**:
  - Implemented vanilla JS triple-buffered circular carousel (`initInfiniteTabsCarousel()`) that enables seamless touch/swipe and keyboard scrolling in both directions with instantaneous sub-frame teleportation across boundaries.
  - Selecting or switching to any tab seamlessly centers the visible item in the viewport with smooth acceleration.
  - Synchronized badge count selectors across all cloned sets via `data-count="..."`.
- **CSS `mask-image` Gradient Edge Fading (`evelyn_ui/dev.html`)**:
  - Replaced fixed gradient overlays with native CSS `mask-image` and `-webkit-mask-image` linear alpha masks on the tabs scroll container, eliminating all corner artifacts, brightness clipping, and background color mismatches with 100% pixel-perfect edge dissolving.

---

## [000.006.036] - 2026-08-31 — *Infinite Carousel Gradient Double-Chevrons for Workspace Navigation*

### Fixed & Enhanced
- **Gradient Double-Chevron Carousel Navigation (`evelyn_ui/dev.html`)**:
  - Implemented left and right edge gradient overlay buttons featuring horizontal double-chevrons with graduated opacity (`opacity: 0.45` trailing, solid leading).
  - Integrated infinite carousel navigation (`scrollTabs(direction)`) that smoothly scrolls or automatically wraps around to the beginning/end when clicking past boundaries.
  - Added subtle glowing drop-shadows and hover translations on chevrons for enhanced visual PKM affordance.

---

## [000.006.035] - 2026-08-31 — *Responsive Horizontal Tab Scroll & Workspace Header Navigation*

### Fixed & Enhanced
- **Mobile Responsive Tab Bar & Header Separation (`evelyn_ui/dev.html`)**:
  - Transformed the workspace tabs into a smooth, horizontal touch-scrolling container (`overflow-x: auto; flex-wrap: nowrap; scrollbar-width: none`) with `scroll-snap-type` alignment and `white-space: nowrap; flex: 0 0 auto` button sizing, preventing tabs from getting squished or crushed on mobile screens.
  - Added a dedicated section header (`🗂️ Workspaces & Tools`) above the tab bar to visually separate the interactive tool tabs from the Heavy Tasks Monitor panel.
  - Added programmatic auto-centering (`scrollIntoView`) on active tabs when switching views.
  - Added responsive `@media (max-width: 600px)` rules for header, panel, and card padding.

---

## [000.006.034] - 2026-08-31 — *Global Pytest Vault Sandbox & Hermetic Test Isolation Protocol*

### Fixed & Enhanced
- **Global Pytest Vault Sandbox (`Evelyn/tests/conftest.py`)**:
  - Implemented an `autouse=True` function-scoped pytest fixture that automatically redirects all vault write paths (`cfg.VAULT_BASE_DIR`, `cfg.JOURNAL_DIR`, `cfg.LISTS_DIR`, `cfg.PENDING_DIR`, etc.) into an ephemeral, hermetic `/tmp/evelyn_test_vault_XXXX/` sandbox for every test.
  - Guarantees zero vault leakage: test suites and agent verification runs can never write files directly to or pollute the user's production Obsidian vault.
- **Hermetic Test Isolation Protocol (`AGENTS.md`)**:
  - Added strict workspace protocol requiring all test runs, CLI verifications, and mock scripts to execute exclusively inside sandboxed or `:memory:` environments.

---

## [000.006.033] - 2026-08-31 — *Unified Journal Pipeline & Single Source of Truth Architecture*

### Fixed & Enhanced
- **Single Source of Truth Tool Architecture (`Evelyn/tools/evelyn_tools.py`, `Evelyn/tools/journal_manager.py`)**:
  - Sharpened `MODEL_TOOL_DEFINITIONS["write_journal_entry"]` with an explicit anti-hesitation trigger directive instructing the model to execute the tool immediately during evening wind-downs without deferring to background daemons.
  - Unified ambient impression consumption inside `journal_manager.create_journal_entry()`: every journal write (chat turn, background daemon, CLI) automatically marks all daytime impressions (`daily_ambient_impressions`) as consumed upon confirmed vault write.
- **Autonomous Daemon Decoupling (`Evelyn/tools/auto_journaler.py`)**:
  - Stripped all hardcoded/separate procedure lookups and `<protocol>` envelopes; `write_journal_entry` tool definition serves as the canonical single source of truth for reflection schema and formatting.
- **Evening Chat `<ambient_stream>` Ingestion (`evelyn_server.py`)**:
  - Automatically queries unconsumed daytime impressions during evening chat turns ($\ge 17:00$) and injects `<ambient_stream>` XML into prompt telemetry, giving the persona full real-time awareness of spontaneous daytime thoughts during live evening reflections.

---

## [000.006.032] - 2026-08-31 — *Ambient Reflector Token Budget & Dynamic Circadian Header Island*

### Fixed & Enhanced
- **Ambient Thinking Token Budget (`Evelyn/tools/ambient_reflector.py`, `evelyn_config.py`)**:
  - Introduced `AMBIENT_REFLECTIONS_NUM_PREDICT = 1024` to resolve token exhaustion where reasoning models (`gemma4:12b`) exhausted `num_predict: 256` entirely within internal thinking traces, resulting in empty content outputs.
  - Generous token budget provides ample headroom for autonomous internal deliberation while strictly preserving concise 1–2 sentence reflection outputs.
- **Dynamic Circadian Header Island & Persistent Idle State (`evelyn_ui/index.html`)**:
  - Updated `.ambient-header-island` to remain persistently visible in the UI header instead of hiding on 0 active events.
  - Implemented circadian-aware idle state displaying `☀️ Daytime Quiet` during diurnal hours (09:00–21:00) and `🌙 Nighttime Rest` during nocturnal hours (21:00–09:00).
  - Added interactive status popover explaining ambient system activity when idle, transitioning smoothly to `💭 <thought>` or media share badges when new impressions arrive.

---

## [000.006.031] - 2026-08-30 — *Multi-Modal Ambient Feed, Thought Bubbles & Dynamic Header Island*

### Added & Architecture
- **Multi-Modal Ambient Impression Substrate (`daily_ambient_impressions`, `Evelyn/tools/memory_db.py`, `Evelyn/tools/db_migrator.py`)**:
  - Registered database migration `000.006.031` creating polymorphic `daily_ambient_impressions` table supporting spontaneous daytime `thought` bubbles, multi-modal `media_share` items (outfits, library artifacts), `proactive_msg` drafts, and `system_alert` insights.
  - Added composite indexes `idx_ambient_feed(dismissed, ts DESC)` and `idx_ambient_type_feed(type, dismissed, ts DESC)` ensuring $O(\log N)$ sorted feed retrieval without temporary B-tree file sorting.
  - Implemented CRUD helpers in `memory_db.py` for recording impressions, querying unconsumed impressions by local date, fetching active feeds, dismissing UI items, and marking impressions consumed.
- **Diurnal Thought Generator Daemon (`Evelyn/tools/ambient_reflector.py`, `evelyn_config.py`)**:
  - Implemented `run_ambient_reflection()` to autonomously capture spontaneous 1–2 sentence private wandering thoughts and realizations during daytime conversational pauses.
  - Added multi-gate evaluation in `should_generate_idle_thought()` checking the diurnal circadian window (`09:00`–`21:00` local), conversational inactivity ($\ge 2$h), daily count cap ($\le 3$ per local date), and verifying new conversation turns occurred in `evelyn_chat.db` since the last reflection.
  - Added extensible multi-modal helpers `record_media_share()` and `record_system_alert()`.
- **Cognitive Task Scheduling & Lifespan Integration (`Evelyn/tools/task_manager.py`, `evelyn_server.py`)**:
  - Registered `ambient_reflector` in `TASK_SCHEDULE_MAP` under `TaskSchedule.DIURNAL` with soft timeout of 5 minutes (`300.0s`) and added to `HEAVY_TASK_KEYS`.
  - Added `_idle_ambient_reflector_loop()` background monitor in server lifespan to evaluate eligibility and enqueue tasks into the cooperative FIFO idle queue.
  - Added API endpoints `GET /ambient/feed`, `POST /ambient/dismiss`, and backwards-compatible `GET /thought_bubble`.
- **Dynamic Ambient Header Island in Chat UI (`evelyn_ui/index.html`)**:
  - Built centered glassmorphic header island (`.ambient-header-island`) featuring interactive pills with desktop ellipsis truncation, responsive mobile collapsing (<640px) to icon badges, unread dot indicators, and floating thought popovers with instant dismissal.
  - Added visibility-aware client polling (`document.visibilityState === "hidden"`) to eliminate idle engine load.
- **Cross-Layer Journal Synthesis & Failure Isolation (`Evelyn/tools/auto_journaler.py`)**:
  - Automatically queries all unconsumed daytime ambient impressions and injects them into structured `<ambient_stream>` XML telemetry for nightly reflection synthesis.
  - Strictly enforces failure isolation: `mark_ambient_impressions_consumed()` executes only after confirmed Obsidian vault disk writes, preserving `consumed` and `dismissed` state orthogonality.
- **Automated Test Suite (`Evelyn/tests/test_ambient_reflector.py`)**:
  - Added 5 unit tests verifying schema migration, active feed ordering, gate conditions, failure isolation, and API endpoint contracts (234/234 workspace tests passing).

---

## [000.006.030] - 2026-08-30 — *Autonomous After-Hours Journal Daemon & Map-Reduce Compaction*

### Added & Architecture
- **Autonomous After-Hours Journal Daemon (`Evelyn/tools/auto_journaler.py`, `evelyn_config.py`)**:
  - Implemented `run_auto_journaling()` background worker capable of autonomously evaluating late-night circadian windows (`23:00`–`04:00`), inactivity thresholds (`AUTO_JOURNAL_IDLE_THRESHOLD = 5400s` or 02:30 AM failsafe), and minimum day turn thresholds (`AUTO_JOURNAL_MIN_MESSAGES = 4`).
  - Added robust midnight crossover handling in `resolve_target_journal_date()` to resolve the logical target date to yesterday when running during early morning hours (`00:00`–`04:00`).
  - Added strict vault collision prevention (`journal_manager._resolve_journal_filepath()`) to prevent duplicate entry generation when a manual reflection was already recorded.
- **Chronological Map-Reduce History Compaction (`compact_history_map_reduce()`)**:
  - Built an in-memory chronological Map-Reduce compressor that slices heavy transcripts into blocks of ~25 turns and extracts dense bullet digests of concrete actions, tools, creative projects, and banter via `ollama_client.query_ollama`.
  - Merges chunk digests into structured `<day_history_digest>` telemetry paired with recent raw evening turns, ensuring zero context loss on 100+ turn marathon conversation days without overflowing the tool loop context budget.
- **Cognitive Task Scheduling & Lifespan Integration (`Evelyn/tools/task_manager.py`, `evelyn_server.py`)**:
  - Registered `auto_journaler` in `TASK_SCHEDULE_MAP` under `TaskSchedule.NOCTURNAL` with inclusion in `HEAVY_TASK_KEYS` and `DEFAULT_SOFT_TIMEOUTS` (15m).
  - Added `_idle_auto_journal_loop()` background monitor in `evelyn_server.py` lifespan to periodically evaluate eligibility and enqueue tasks into the cooperative FIFO idle queue.
  - Implemented chat preemption checks throughout compaction and synthesis to yield GPU resources immediately upon incoming user interaction.
- **Automated Test Suite (`Evelyn/tests/test_auto_journaler.py`)**:
  - Added unit test suite covering circadian window date resolution, multi-gate trigger evaluation, Map-Reduce chunking, and preemption safety.

---

## [000.006.029] - 2026-08-30 — *Persona-Agnostic Journaling Protocol & Adaptive Day History*

### Added & Architecture
- **Persona-Agnostic Tool Declaration Schema (`Evelyn/tools/evelyn_tools.py`)**:
  - Generalized `write_journal_entry` in `MODEL_TOOL_DEFINITIONS` to be strictly persona-agnostic, using relational role definitions (`the user`, `your persona`) to eliminate persona leakage while preserving repository modularity.
  - Refactored `narrative` parameter guidance to enforce concrete nouns, exact project/tool names, specific conversational banter, and accurate attribution of solo physical tasks vs. shared discussions, while explicitly forbidding rigid tripartite timelines (Morning/Afternoon/Evening) and hollow poetic filler.
  - Made `required` parameters `["mood", "vibe_check", "narrative"]`, granting natural flexibility for optional send-off thoughts.
- **Adaptive Day-Bound History Assembly & Token Budgeting (`evelyn_server.py`, `Evelyn/tests/test_adaptive_day_history.py`)**:
  - Replaced the arbitrary 40-message ceiling in `load_history()` with full-day message retrieval (`ts >= today_start`) plus up to 6 transition messages from the previous day.
  - Implemented dynamic safe history token budget calculations derived from `NUM_CTX` (32K), subtracting reserved overhead for system prompts, tools schemas, RAG context, and generation buffers.
  - Built turn-integrity-preserving token pruning that gracefully sheds older turns during heavy multi-turn days without splitting assistant tool calls from results or dropping system date boundary markers (`--- Date Changed ---`).
- **Database Migration `000.006.029` (`Evelyn/tools/db_migrator.py`)**:
  - Registered and executed migration updating master Procedure `#656` in `evelyn_memory.db` with the persona-agnostic protocol, concrete-first extraction steps, and explicit anti-filler pitfalls.

---

## [000.006.028] - 2026-08-30 — *Canonical Section Invariance & Topic Density Guardrails*

### Added & Architecture
- **Canonical Section Structural Invariance (`CANONICAL_DOCUMENT_SECTIONS`, `Evelyn/tools/profile_evolver.py`)**:
  - Registered canonical required section schemas for all system prompt documents (`Evelyn_Narrative_Persona.md`, `<USER_NAME>_Narrative_Profile.md`, `System_Directives.md`).
  - Implemented `extract_sections()`, `validate_document_structure()`, and `repair_missing_sections()` to prevent the LLM from merging, deleting, or renaming section headings during thematic evolution, compaction, or editorial proofreading.
- **Topic Density & Minimum Section Coverage Guardrails**:
  - Enforced minimum substantive content thresholds ($\ge 15$ words per required section) across all transformation stages.
  - If a section is hollowed out or dropped during compaction/proofreading, the system automatically repairs and restores the baseline section content from the prior draft rather than losing category coverage.

### Fixed & Enhanced
- **Hardened Compaction & Proofreading Prompts (`Evelyn/tools/profile_evolver.py`)**:
  - Added explicit `STRUCTURAL INVARIANCE` and `TOPIC DENSITY & BALANCED COVERAGE` directives to both the thematic accumulation and compaction prompts.
  - Directly injected the document's required canonical section list into the compaction prompt skeleton.
  - Aligned Assistant `DOCUMENT_THEMES` section header hints to exact canonical headers (`## Identity & Presence / ## Persona & Appearance`, `## Intellectual & Creative Style / ## Voice & Communication`, `## Relationship & Support`).

---

## [000.006.027] - 2026-08-30 — *Per-Document Evolution Tracking & Multi-Profile Extensibility*

### Added & Architecture
- **Per-Document Evolution Tracking (`entry_document_evolution`, `Evelyn/tools/memory_db.py`, `Evelyn/tools/db_migrator.py`)**:
  - Implemented migration `000.006.027` introducing normalized junction table `entry_document_evolution (entry_id, document_name, evolved_at, PRIMARY KEY (entry_id, document_name))` in `evelyn_memory.db`.
  - Added `get_entries_by_category_for_document(category, document_name, status)` using a SQL `LEFT JOIN` on `entry_document_evolution` to isolate evolution state across independent persona, profile, and directives documents.
  - Enhanced `touch_entry_evolved(entry_id, document_name, timestamp)` to record evolution timestamps per document while maintaining global fallback timestamps on `context_entries`.
  - Enabled SQLite foreign key enforcement (`PRAGMA foreign_keys = ON`) in `get_db()` to ensure clean cascade deletes when context entries are pruned.
- **Dirty Record Protection During Human Review (`evelyn_server.py`)**:
  - Profile update proposal approval and denial handlers now stamp `entry_document_evolution` using `prop["created_at"]` rather than `now()`. Any context entries modified or split during human review (`updated_at > created_at`) are recognized as dirty and remain eligible for re-evaluation in the next cycle.

### Fixed & Enhanced
- **Eliminated Cross-Document Context Starvation (`Evelyn/tools/profile_evolver.py`, `scripts/trigger_profile_evolution.py`)**:
  - Refactored `run_profile_evolution()` and manual CLI triggers to query qualifying context per target document, preventing proposals approved for one document from locking out context from other documents.
  - Aligned `DOCUMENT_CATEGORIES` and `DOCUMENT_THEMES` with the authoritative `Cat00 - Index.md` taxonomy, adding previously omitted categories (`Cat07` Motivations/Aspirations, `Cat02-U` Core Values, `Cat15` Lexicon) and mapping shared cross-domain categories (`Cat06` Relationship Dynamics, `Cat09` Cognitive Style, `Cat10` Humor & Play, `Cat12` Emotional States, `Cat16` Protocols & Routines) across Assistant, User, and Directives documents.
  - Hardened proposal denial (`deny`) to stamp target document evolution state and advance cooldowns, preventing infinite proposal generation loops on unchanged entries.

---

## [000.006.026] - 2026-08-29 — *Cognitive Task Tiers & Digital Dreaming Circadian Scheduling*

### Added & Architecture
- **Cognitive Task Tiers & Circadian Model (`Evelyn/tools/task_manager.py`, `evelyn_config.py`)**:
  - Implemented `TaskSchedule` enum classifying all engine tasks into biological cognitive tiers:
    - **`REFLEX`** (24/7 reactive housekeeping, idle $\ge$ 5m): `extractor`, `tag_librarian`, `refresh_memory`, `vault_map`, `sync`.
    - **`NOCTURNAL`** (Overnight "Digital Dreaming" / heavy semantic clustering, 21:00–06:00, idle $\ge$ 5m): `consolidator`, `procedure_consolidator`, `profile_evolver`.
    - **`DIURNAL`** (Daytime active cognition / Deep Research, 06:00–21:00, idle $\ge$ 30m): `task_<id>`.
  - Added Digital Dreaming circadian window parameters in `evelyn_config.py`: `DREAMING_ACTIVE_HOURS_START = 21` (9 PM), `DREAMING_ACTIVE_HOURS_END = 6` (6 AM), `IDLE_DISPATCHER_THRESHOLD = 300` (5 minutes).
  - Implemented `get_current_circadian_phase()` with midnight-crossing support and `is_task_runnable()` with manual override support (`metadata={"manual": True}`).

### Fixed & Enhanced
- **Eliminated Dispatcher Double-Gating & Head-of-Line Blocking (`evelyn_server.py`, `task_manager.py`)**:
  - Removed redundant second-stage per-task idle checks from `_idle_task_dispatcher_loop()`.
  - Added `acquire_next_runnable_task()` to dispatch the oldest eligible task from `_idle_queue`, safely skipping closed circadian schedules without blocking daytime reflex tasks.
- **Global Tail Re-Queueing on Chat Preemption (`Evelyn/tools/task_manager.py`)**:
  - Enhanced `cancel_all_idle_tasks("chat_preemption")` to automatically re-enqueue interrupted active tasks to the **tail** (`append`) of `_idle_queue`, preserving execution state while ensuring fast reflex tasks run first when idle.
- **Automated Test Suite (`Evelyn/tests/test_idle_task_queue.py`)**:
  - Added 5 new unit tests verifying tier mapping, circadian midnight evaluation, runnable queue acquisition skipping closed schedules, manual overrides, and preemption tail re-queueing (11/11 tests passing).

---

## [000.006.025] - 2026-08-29 — *Fact Extractor Timeout Hardening & Stop Sequence Guard*

### Fixed & Hardened
- **Stop Sequence Enforcement (`Evelyn/tools/fact_extractor.py`)**:
  - Injected explicit markdown code fence stop sequences (`["\n```\n", "\n```", "```\n"]`) into extraction options for both Pass 1 (facts) and Pass 2 (procedures) to immediately halt local Ollama inference upon closing the YAML code fence, preventing token generation runaways.
- **Resilient YAML Code Fence Parsing (`Evelyn/tools/fact_extractor.py`)**:
  - Enhanced `_parse_facts_yaml` and `_parse_procedures_yaml` to strip unmatched opening code fences when stop sequences trigger and omit the trailing delimiter.
- **Batch Size & Timeout Configuration (`evelyn_config.py`)**:
  - Reduced default `FACT_EXTRACTION_BATCH_SIZE` from 20 to 12 messages to keep prompt ingestion and token generation windows bounded and fast.
  - Increased `FACT_EXTRACTION_TIMEOUT` from 300s to 450s with explicit socket connection/read timeout configuration (`httpx.Timeout`).

---

## [000.006.024] - 2026-08-29 — *Canonical XML Telemetry Envelopes & In-Flight Context Hardening*

### Added & Standardized
- **Canonical XML Envelope Helper Suite (`Evelyn/tools/string_utils.py`)**:
  - Implemented centralized XML escaping and attribute sanitization routines (`escape_xml_content`, `escape_xml_attr`).
  - Implemented core XML envelope constructor (`wrap_xml_envelope`) with strict token pruning (omits empty containers completely unless explicitly configured for self-closing status).
  - Added specialized taxonomy builders: `build_temporal_envelope`, `build_context_retrieval_envelope`, `build_autonomous_trigger_envelope`, `build_system_event_envelope`, and `build_memory_context_envelope`.
  - Added deterministic multi-envelope stacking (`stack_envelopes`) enforcing canonical order: `<temporal_context>` $\rightarrow$ `<system_event>` / `<autonomous_trigger>` $\rightarrow$ `<context_retrieval>` / `<memory_context>` $\rightarrow$ user turn.
  - Added clean double-newline turn boundary isolation (`inject_envelope_to_turn`).

- **Architecture & System Prompt Telemetry Contract (`evelyn_server.py`)**:
  - Upgraded `<system_telemetry_directives>` in `load_system_prompt()` to the comprehensive System Telemetry Contract covering all 5 canonical taxonomy tags, including strict anti-leakage negative constraints forbidding the model from echoing or wrapping conversational responses in XML tags.
  - Refactored `get_research_context()` to emit structured `<autonomous_trigger>` and `<system_event>` envelopes instead of legacy plain text headers.
  - Integrated deterministic `inject_envelope_to_turn` into the main chat streaming pipeline.

- **RAG Semantic Context Hardening (`Evelyn/tools/chroma_rag.py`)**:
  - Replaced legacy plain text bracket headers (`--- Retrieved Context ---`, `[Primary Source Document: ...]`, `[Operational Protocol: ...]`) with structured `<context_retrieval>` envelopes wrapping `<document>`, `<protocol>`, and `<memory_entry>` tags with attribute metadata.

- **Automated Test Suite (`Evelyn/tests/test_xml_envelopes.py`)**:
  - Added 10 dedicated unit tests covering escaping, token pruning, self-closing tags, domain builders, deterministic stacking order, and turn boundary isolation.
  - Updated all existing test assertions across `test_time_context.py`, `test_research_tools.py`, `test_procedures_upgrade.py`, and `test_all_tools_end_to_end.py`.

---

## [000.006.023] - 2026-08-29 — *Temporal Subsystem Grounding & Passive Telemetry Directives*

### Enhanced & Hardened
- **Arithmetic Elimination & Macro-Transition Threshold (`Evelyn/tools/time_manager.py`)**:
  - Removed `last_interaction` timestamp attribute from `<session_gap>` XML envelopes, emitting solely `<session_gap status="resumed" break_duration="..." />` (or `<session_gap status="active_flow" />`), eliminating arithmetic temptation in chain-of-thought models.
  - Raised default `idle_threshold_minutes` from 15m to 45m, treating everyday micro-chore pauses as continuous active flow.

- **Authoritative Clock & Passive Telemetry Directives (`evelyn_server.py`)**:
  - Updated `<system_telemetry_directives>` in `load_system_prompt()` to explicitly establish `<current_time>` as the single authoritative clock and forbid estimating or offsetting time.
  - Instructed Evelyn to treat `<session_gap>` as passive atmospheric grounding for natural transitions rather than a conversational prompt to interrogate or call out silences.

---

## [000.006.022] - 2026-08-29 — *Evelyn Temporal Management Subsystem (time_manager)*

### Added & Refactored
- **Dedicated Temporal Subsystem (`Evelyn/tools/time_manager.py`)**:
  - Implemented `TimeManager` class encapsulating timezone-aware chronology, role-agnostic silence tracking, schema-adaptive agenda lookups, structured XML envelope generation, and autonomous heartbeat evaluation.
  - Robust datetime parsing (`parse_dt`): Normalizes UNIX epoch floats (`messages.ts`), all-day date strings (`YYYY-MM-DD` from Google Calendar), RFC 3339 UTC strings (`tasks.due_at`), and ISO/SQLite timestamps into timezone-aware `America/Chicago` objects using `zoneinfo.ZoneInfo(cfg.USER_TIMEZONE)`.
  - Role-Agnostic Silence Tracking (`get_last_interaction_ts`): Queries latest message regardless of role (`SELECT ts FROM messages ORDER BY id DESC LIMIT 1`), eliminating the bug where active conversations were flagged with false idle gaps.
  - Structured XML Environmental Telemetry (`build_temporal_envelope`): Generates unambiguous `<temporal_context>` XML blocks with absolute clocks, relative session gaps (`active_flow` vs `resumed`), upcoming calendar events, and imminent tasks.
  - Proactive Heartbeat Evaluation (`evaluate_heartbeat`): Evaluates imminent/overdue tasks and imminent events on autonomous ticks with deduplication caching and lookahead TTL pruning.

- **Engine Turn Decoupling & Directives (`evelyn_server.py`)**:
  - Replaced ambiguous in-turn prefixing (`user_msg_for_model = f"{time_ctx}\n{user_message}"`) with Gemma-compatible XML envelopes (`f"{temporal_envelope}\n\n{user_message}"`).
  - Added `<system_telemetry_directives>` in `load_system_prompt()` instructing the model that `<temporal_context>` is environmental server telemetry rather than user utterances.
  - Registered `_temporal_heartbeat_loop` in FastAPI lifespan context ticking every 60 seconds with `task_manager.is_chat_preempted()` generation lock protection.

- **Automated Verification (`Evelyn/tests/test_time_context.py`)**:
  - Added 8 unit tests covering timezone-aware parsing, all-day event handling, role-agnostic silence tracking, session gap thresholds, XML envelope generation, telemetry prompt directives, and heartbeat alert deduplication.

---

## [000.006.021] - 2026-08-29 — *Profile Evolver Thematic Clustering & Editorial Proofreading Pass*

### Added & Enhanced
- **Thematic Section Pre-Clustering & Entity Aggregation (`Evelyn/tools/profile_evolver.py`)**:
  - Replaced naive chronological entry chunking with structured thematic partitioning (`DOCUMENT_THEMES`, `_cluster_entries_by_theme()`), grouping qualifying memory entries by canonical document sections (Identity & Values, Relationship Dynamics, Interaction Preferences, Routines, and Directives).
  - Implemented entity-level pre-aggregation to group interspersed observations sharing entities/tags (e.g. social connections, routines) under dedicated topic subheadings, eliminating cross-topic context switching and reducing redundant additions.
  - Slices large thematic groups into cleanly numbered sub-batches (`Part 1`, `Part 2`) when exceeding `PROFILE_EVOLUTION_BATCH_SIZE`.

- **Dedicated Editorial & Proofreading Pass (`Evelyn/tools/profile_evolver.py`, `evelyn_config.py`)**:
  - Implemented `_proofread_document()` executed post-compaction prior to proposal creation.
  - Operates at low temperature (`temperature: 0.1`, `think: False`) to detect and eliminate subword tokenizer artifacts (e.g. `navigms` -> `navigates`), broken quotes, concatenated stems, and grammatical errors without altering voice, narrative tone, or factual meaning.
  - Added structural validation guardrails ensuring proofread text retains original section headers and maintains at least 85% length before acceptance.
  - Added configuration toggle `PROFILE_EVOLUTION_PROOFREAD_ENABLED = True` and raised `PROFILE_EVOLUTION_TIMEOUT` to 240s in `evelyn_config.py`.

- **Automated Verification (`Evelyn/tests/test_profile_evolver_thematic.py`)**:
  - Added 6 unit tests covering thematic clustering, entity sub-topic formatting, batch splitting, unassigned category fallbacks, proofreading error correction, and structural length fallback mechanisms.

---

## [000.006.020] - 2026-08-29 — *Live Procedures Cleanup, Fact Migration & write_dream_entry Tool*

### Added & Consolidated
- **Database Migration 000.006.020 (`Evelyn/tools/db_migrator.py`, `data/evelyn_memory.db`)**:
  - Migrated 5 misclassified procedures (#53 store hours, #54 shopping snack habit, #96 shredded wheat dislike, #102 Factor meal rotation, #108 daughter name spelling) into canonical `context_entries` (`Cat01-U`, `Cat09-U`, `Cat15-U`) and archived their procedure rows.
  - Merged 9 duplicate Evening Journaling procedures (#28, #86, #107, #190, #195, #458, #575, #583, #619) into 1 canonical Master Daily Journaling Procedure.
  - Merged 5 duplicate Dream procedures (#88, #132, #137, #184, #201) into 1 canonical Master Dream Entry & Analysis Procedure.
  - Consolidated redundant health pacing (#95, #110 into #105; #159, #571 into #160) and image generation procedures (#146, #147, #149, #155, #166 into #621), reducing active live procedures from 62 to 36 (a 42% reduction).

- **New `write_dream_entry` Tool (`Evelyn/tools/dream_manager.py`, `Evelyn/tools/evelyn_tools.py`)**:
  - Introduced dedicated tool and backing manager to save and append structured dream notes in the Obsidian Vault (`Dream Entries/` archive) with date formatting, raw description preservation, initial feelings/thoughts, and tags.
  - Completely disambiguated dream logs from Evelyn's personal daily reflection journal (`write_journal_entry`).

- **Engine Tool Enhancements & Precision RAG (`Evelyn/tools/fact_extractor.py`, `Evelyn/tools/procedure_consolidator.py`, `Evelyn/tools/memory_db.py`)**:
  - Added strict negative extraction constraints in `fact_extractor.py` to prevent static facts or preferences from being extracted as procedures.
  - Added Jaccard keyword deduplication check before inserting extracted procedures to prevent near-duplicate backlog accumulation.
  - Added domain synonym group clustering (`domain_journal`, `domain_dream`, `domain_visual`, `domain_health`) in `procedure_consolidator.py` to automatically detect and cluster multi-variant procedures.
  - Upgraded `search_procedures_by_trigger` in `memory_db.py` to use token overlap and relevance scoring, eliminating false-positive runaway procedure retrievals.

- **Automated Verification (`Evelyn/tests/test_dream_manager_and_procedures_cleanup.py`)**:
  - Added 5 unit tests covering dream note creation, same-day dream appends, tool dispatch, relevance scoring, and domain synonym extraction.

---

## [000.006.019] - 2026-08-28 — *Fact Extractor Ollama ReadTimeout & Stream Resilience*

### Fixed & Hardened
- **Fact Extraction Timeout Scaling (`evelyn_config.py`, `Evelyn/tools/fact_extractor.py`)**:
  - Increased `FACT_EXTRACTION_TIMEOUT` from 180s (3m) to 300s (5m) in `evelyn_config.py` to provide sufficient headroom for large 20-message batches with full master taxonomy (`Cat00 - Index.md`) prompt evaluation.
  - Increased streaming line chunk timeout from 120.0s to 180.0s in `_do_extraction()` across both fact and procedure extraction passes.
  - Hardened static typing and sanitized tool string lists in `_do_extraction()` to prevent dictionary assignment type warnings and join issues.

---

## [000.006.018] - 2026-08-28 — *Profile Evolver Per-Document Timeout & Task Manager Resilience*

### Enhanced & Hardened
- **Per-Document Timeout & Failure Isolation (`Evelyn/tools/profile_evolver.py`, `evelyn_config.py`)**:
  - Replaced the global task-level execution timer assumption with dedicated per-document timeouts (`PROFILE_EVOLUTION_DOC_TIMEOUT = 1500` / 25 minutes per document), wrapping each document pass in `asyncio.wait_for()`.
  - Hardened error handling so an individual document timeout or LLM error preserves any in-progress draft on disk, sets status `INTERRUPTED_SAVED`, and gracefully advances to the remaining identity documents rather than killing the entire background task.
  - Standardized per-call Ollama inference timeouts using `PROFILE_EVOLUTION_TIMEOUT = 180` (3 minutes per stream).
  - Added live heartbeat updates to `task_manager.set_running()` reporting current document name, pass number, compaction word counts, and reason summary stages.

- **Task Manager Watchdog Resilience (`Evelyn/tools/task_manager.py`)**:
  - Increased `DEFAULT_SOFT_TIMEOUTS["profile_evolver"]` baseline from 900s (15 min) to 4500s (75 min) to account for multi-pass evolution across all 3 identity documents.
  - Updated `get_dynamic_timeout()` to automatically enforce `PROFILE_EVOLUTION_DOC_TIMEOUT * 3.0` as the dynamic baseline minimum.

- **Automated Verification (`Evelyn/tests/test_profile_evolver_timeouts.py`)**:
  - Added unit tests validating dynamic timeout baselines and per-document timeout isolation across sequential identity documents.

---

## [000.006.017] - 2026-08-28 — *Fact Consolidator Category Scan State Sanitization*

### Fixed & Hardened
- **Fact Consolidator Scan State Sanitization (`Evelyn/tools/fact_consolidator.py`, `data/evelyn_consolidation_offsets.json`, `evelyn_server.py`, `evelyn_ui/dev.html`)**:
  - **Offsets JSON Cleanup**: Pruned 41 legacy (`-R`/`-E`) and dirty/non-canonical category keys accumulated in `evelyn_consolidation_offsets.json` before taxonomy migration, bringing active tracked categories to the exact 32 canonical categories (`Cat01-U`..`Cat16-U`, `Cat01-A`..`Cat16-A`).
  - **Automatic State Validation & Normalization**: Hardened `_load_scan_state()` in `fact_consolidator.py` to filter and normalize all loaded category keys on startup and automatically persist the pruned canonical dictionary when stale keys are detected.
  - **Server Status Filtering & UI Alignment**: Added defensive regex filtering in `evelyn_server.py` (`re.match(r"^Cat(0[1-9]|1[0-6])-[UA]$")`) and dynamic `total_categories: 32` propagation to ensure the dashboard accurately reflects `Tracked: 32/32 categories`.
  - **Automated Verification**: Added comprehensive unit tests in `Evelyn/tests/test_fact_consolidator_scan_state.py`.

---

## [000.006.016] - 2026-08-28 — *Pyrefly & Pyproject Tooling Consolidation and Static Typing Hardening*

### Consolidated & Unified
- **Single Source of Truth Tooling (`pyproject.toml`, `AGENTS.md`)**:
  - Unified all Python tooling configurations (`[tool.pyrefly]`, `[tool.ruff]`, `[tool.pytest.ini_options]`) canonically inside `pyproject.toml`.
  - Configured Pyrefly's `search-path` (`".", "Evelyn", "Evelyn/tools", "Evelyn/persona"`) to resolve tool imports statically without reliance on dynamic runtime `sys.path.insert`.
  - Retired and deleted standalone `pyrefly.toml` and `ruff.toml` to prevent tooling drift.
  - Documented the single source of truth rule in `AGENTS.md` Section 1.

### Fixed & Hardened
- **Static Typing & Process Guarding (`evelyn_server.py`, `Evelyn/tools/fact_extractor.py`, `Evelyn/tools/fact_consolidator.py`)**:
  - **Module Shadowing**: Removed redundant nested `import psutil`, `import sqlite3`, and `import os` statements across diagnostic and research pause routines that caused variable uninitialized warnings.
  - **Type Inference**: Explicitly typed `options: dict[str, Any]`, `user_turn: dict[str, Any]`, and `meta_entry: dict[str, Any]` to fix dictionary item assignment errors.
  - **Null Safety**: Added null guards to database message insertion IDs (`lastrowid`), active task ID string casting, subprocess stdout stream inspection, and `task_name` prefix checking.
  - **FastAPI Optional Bodies**: Fixed parameter typing from `req: Model = None` to `req: Model | None = None` across `/chat/stop`, `/api/review/extractions/{id}/{action}`, and `/api/review/proposals/{id}/{action}`.
  - **Async Task References**: Explicitly typed `_extraction_task` and `_consolidation_task` as `asyncio.Task | None`.
  - **Uvicorn Start Kwargs**: Replaced dictionary unpacking `**ssl_args` with explicit `ssl_keyfile` and `ssl_certfile` keyword arguments.

---

## [000.006.015] - 2026-08-28 — *Heavy Task Telemetry Modernization & Vault Map Streamlining*

### Enhanced & Modernized
- **Heavy Tasks Telemetry & Progress Percentages (`evelyn_server.py`, `evelyn_ui/dev.html`)**:
  - **Fact Extractor**: Added real-time progress percentage against Max message ID (`last_extracted_id / MAX(id)`), remaining backlog message count, and active live facts total.
  - **Fact Consolidator**: Clarified distinction between total active live facts in the database, tracked categories (`N / 32`), and last run scanned metrics.
  - **Procedure Consolidator**: Clarified total live procedures in the database, pending merge proposals, and last run audited count.
  - **Tag Librarian**: Added percentage display `XX.X% (audited / total notes)` alongside Master Taxonomy tag counts.
  - **Memory Refresh**: Added live inventory counts for both Obsidian Vault notes and Chroma knowledge vectors alongside the pipeline progression steps.
  - **Chroma Sync**: Added vector count, SQLite facts/procedures totals, and pending sync queue item counts.
  - **Vault Map Indexer**: Switched telemetry from stale mock references to live `vault_documents` indexed note counts and database status in `evelyn_vault.db`.

### Fixed & Streamlined
- **Profile Evolver Status Verbiage & Lifecycle (`Evelyn/tools/profile_evolver.py`, `evelyn_server.py`, `evelyn_ui/dev.html`)**:
  - Added `APPROVED` (`"Profile Updated & Applied"`) status code so approved proposals immediately reflect as successfully applied rather than permanently appearing as `"Proposal Staged"`.
  - Added distinct color badges: green for `APPROVED`, amber for `PROPOSAL_STAGED` / `PENDING_APPROVAL`, and cyan for `NO_CORE_CHANGES` (`"Evaluated — Up to Date"`).
- **Vault Map Process Clean Up (`.gitignore`, `REQUIREMENTS.md`)**:
  - Removed obsolete references to legacy `generate_vault_map.py` from `.gitignore` and `REQUIREMENTS.md`, standardizing on canonical `Evelyn/tools/vault_indexer.py`.

---

## [000.006.014] - 2026-08-28 — *Consolidation Audit: Agent Instructions Single Source of Truth*

### Consolidated & Unified
- **Single Source of Truth (`AGENTS.md`)**: Consolidated workspace agent rules into `AGENTS.md` as the sole canonical rules contract. Integrated mandatory file metadata & frontmatter update rules, test data cleanup/hygiene mandates, and TCP port/systemd service verification protocols.
- **Documentation & Navigation De-duplication**: Updated `README.md`, `reference/engine_architecture.md`, `reference/docstring_guide.md`, and `.agents/workflows/quality-review.md` to reference `AGENTS.md` and standard reference docs.

### Removed
- **Legacy Monolith (`.ai-instructions.md`)**: Retired and deleted the redundant 378-line catch-all instruction file, eliminating context noise, duplication, and potential configuration drift across AI sessions.

---

## [000.006.013] - 2026-08-28 — *Consolidation Audit: Dead-Code & Type-Error Fixes*

### Fixed
- **`scripts/sqlite_mcp_server.py` Type Error**: `get_ollama_status(OLLAMA_URL)` passed a URL string where the canonical function expects `timeout: int` — would cause a `TypeError` at runtime. Changed to `fetch_ollama_status()` (canonical reads URL from config).
- **`Evelyn/tools/pdf_staging_worker.py` Missed Migration**: Still imported `format_yaml_array` from `tag_librarian` instead of `frontmatter_utils`. Redirected to canonical module.
- **`Evelyn/tools/health_manager.py` Schema Alignment**: Fixed intraday activity query columns (`distance` and `energy`) to match Health Connect SQLite table schema.
- **Test Suite Fixtures**: Updated `test_image_generation.py` to mock `requests.RequestException`, and cleaned archived migration imports in `test_triggered_by_normalization.py`.

### Enhanced & Hardened
- **Ruff Compliance & Quality Gate**: Configured repository-wide `ruff.toml` with `target-version = "py314"`, narrowed broad exception handlers to concrete error classes, added explicit exception chaining (`raise ... from e`), offloaded async file I/O to thread pools, and verified 100% test pass rate across 176 test cases.

### Removed (Dead Code)
- **`tag_librarian.py`**: Removed unused `import urllib.request` and `import urllib.parse` (Ollama calls fully delegated to `ollama_client`).
- **`extract_pdf_library.py`**: Removed dead `import json` (never referenced), unused `field` from `dataclasses` import (factory never called), and dead `clean_title` import from `string_utils` (shadowed by local variable in every usage).
- **`sqlite_mcp_server.py`**: Removed dead `OLLAMA_URL` constant (no remaining references after type-error fix).

---

## [000.006.012] - 2026-08-28 — *Codebase Consolidation & Canonical DRY Architecture*

### Added & Canonical Architecture
- **Canonical String & Gist Utilities (`Evelyn/tools/string_utils.py`)**:
  - Implemented single source of truth for text and title processing: `strip_thinking_tags()`, `clean_llm_gist()`, `sanitize_filename()`, `slugify()`, and `clean_title()`.
  - Zero internal dependencies to serve as the leaf layer of the engine DAG.
- **Canonical Vault Path Resolvers (`Evelyn/tools/path_utils.py`)**:
  - Implemented directory-traversal-guarded vault path transforms: `to_vault_relpath()`, `to_vault_abspath()`, `normalize_vault_path()`, and `is_vault_excluded()`.
  - Standardized all relative paths to forward-slash `.as_posix()` convention.
- **Canonical YAML Frontmatter Manager (`Evelyn/tools/frontmatter_utils.py`)**:
  - Implemented `parse_frontmatter()`, `format_yaml_array()`, `render_frontmatter()`, and line-aware non-destructive `update_frontmatter_field()`.
  - Added atomic `write_file_with_frontmatter()` with `preserve_mtime` support via `os.utime()`.
- **Canonical Ollama HTTP Client (`Evelyn/tools/ollama_client.py`)**:
  - Unified local Ollama gateway: `query_ollama()` (with automatic CoT stripping and connect/read timeouts), `query_ollama_json()`, and `get_ollama_status()`.
- **Systematic Caller Migrations**:
  - Migrated `tag_librarian.py`, `vault_indexer.py`, `ingest_obsidian_knowledge.py`, `vault_list_manager.py`, `journal_manager.py`, `scripts/update_frontmatter.py`, `scripts/extract_pdf_library.py`, `scripts/relocate_vault_pdfs.py`, and `scripts/sqlite_mcp_server.py`.
- **Agent Governance & Anti-Duplication Directives**:
  - Updated `AGENTS.md` (§7 Single Source of Truth & Function Reuse Protocol).
  - Updated `.ai-instructions.md` (§0 Phase A step 4, §2 Operational Disciplines, and §7 Anti-Hallucination Directives).
- **Unit Test Coverage**:
  - Created `Evelyn/tests/test_string_and_path_utils.py`, `Evelyn/tests/test_frontmatter_utils.py`, and `Evelyn/tests/test_ollama_client.py` (20 new tests, 178/178 tests passing suite-wide).

## [000.006.011] - 2026-08-28 — *Vault Taxonomy Alignment & Tag Librarian Acceleration*

### Added & Enhanced
- **5-Tier Priority Scheduling (`Evelyn/tools/vault_db.py`)**:
  - Rewrote `fetch_next_document_for_tag_audit()` to prioritize notes by urgency: (1) Notes with no tags $\rightarrow$ (2) Notes with multi-dash flat tags $\rightarrow$ (3) Notes with simple flat tags $\rightarrow$ (4) Un-audited documents with existing hierarchy $\rightarrow$ (5) Routine rotation of oldest audited documents (`last_tag_audit ASC`).
- **Document Path Exclusion Gate (`evelyn_config.py`, `tag_librarian.py`, `vault_db.py`)**:
  - Added `TAG_LIBRARIAN_EXCLUDED_DOCUMENTS` to specifically exclude root repository files (e.g. `Projects/Evelyn Engine/README.md`) from tag auditing without affecting other documents.
  - Added `is_excluded_document(path)` filter in `tag_librarian.py` and SQL exclusions in `vault_db.py`.
- **Increased Tag Librarian Throughput (`evelyn_config.py`)**:
  - Increased `TAG_LIBRARIAN_BATCH_SIZE` from `1` to `5` documents per idle sweep.
  - Lowered `TAG_LIBRARIAN_IDLE_THRESHOLD` from `2700s` (45m) to `1200s` (20m) for faster idle execution.
- **Standalone Batch CLI Runner (`scripts/audit_vault_tags.py`)**:
  - Created dedicated CLI utility supporting `--limit N`, `--continuous`, `--verbose`, and `--sync-taxonomy` with live per-document reporting and graceful `Ctrl+C` interruptibility.
- **Universal Frontmatter Array Normalization**:
  - Standardized all YAML frontmatter list properties (`tags: [...]`, `aliases: [...]`, `categories: [...]`) across all documentation, templates, rule files, and the Obsidian vault.
  - Updated `scripts/update_frontmatter.py`, `Evelyn/tools/vault_list_manager.py`, `scripts/extract_pdf_library.py`, and `scripts/relocate_vault_pdfs.py` to produce single-line flow arrays.

## [000.006.010] - 2026-08-28 — *Research Intent Mode Classification & Search Query Lexicon Calibration*

### Added & Enhanced
- **Pre-Search Intent Mode Classification (`Evelyn/tools/research_prompts.py`)**:
  - Implemented `classify_intent_mode(query, intent_frame)` with zero-LLM-cost regex word boundary matching across programming languages, system engineering, IoT/hardware, and AI/LLM keywords.
  - Distinguishes `[MODE_TECHNICAL]` (`technical` — APIs, libraries, tutorials, code snippets, hardware protocols) from `[MODE_ACADEMIC]` (`academic` — foundational facts, peer-reviewed consensus, medical/scientific definitions).
  - Wired intent mode persistence and orchestration in `Evelyn/tools/research_engine.py` (`state["intent_mode"]`).
- **Technical vs. Academic Query Formulation (`build_search_query_prompt`)**:
  - Injected explicit intent mode constraints and few-shot examples into `build_search_query_prompt()`.
  - For technical intent: strictly targets developer documentation, GitHub repositories, tutorials, and library packages while banning thesis-style academic phrasing.
  - For academic intent: targets scholarly consensus and authoritative domain literature.
- **Evaluator Gap Sanitization & Prompt Hardening (`research_prompts.py`, `research_engine.py`)**:
  - Implemented `is_valid_search_gap(gap)` to catch and discard generic evaluation status strings (e.g. `"Insufficient evidence collected."`).
  - Hardened `build_evaluate_prompt()` negative constraints to prevent meta-status phrases from leaking into `gaps`.
  - Updated fallback query extraction in `_truncate_query_fallback()` to strip academic filler and prioritize technical keywords.
- **Unit Test Suite (`Evelyn/tests/test_research_intent.py`)**:
  - Added test coverage for technical vs academic intent classification, intent frame evaluation, gap validation, prompt formulation, and fallback keyword generation.

---

## [000.006.009] - 2026-08-28 — *Subject Code Sanitization & Canonical Fast Memory Category Suffix Enforcement*

### Database Migrations & Sanitization
- **Migration 000.006.009 (`Evelyn/tools/db_migrator.py`)**:
  - Registered and executed migration step `migrate_legacy_subject_codes_in_memory` on `data/evelyn_memory.db`.
  - Sanitized 1,259 legacy context entries (`Cat##-R` -> `Cat##-U`, `Cat##-E` -> `Cat##-A`).
  - Sanitized 6,659 legacy proposals (updating `suggested_category` and replacing legacy category patterns in `merged_observation`).
  - Triggered post-migration vault re-indexing to ensure `data/evelyn_vault.db` stays synchronized with renamed files.

### Fixed & Enhanced
- **Category Normalizer & Remediation (`Evelyn/tools/fact_consolidator.py`)**:
  - Rewrote `validate_and_normalize_category()` to map `R`/`U` to `cfg.SUBJECT_CODE_USER` ("U") and `E`/`A` to `cfg.SUBJECT_CODE_ASSISTANT` ("A").
  - Fixed `remediate_database_categories()` to detect and correct legacy `-R` and `-E` categories across context entries and proposals instead of ignoring them.
  - Updated `_RECAT_DETECT_PROMPT` YAML example from `Cat08-R` to `Cat08-U`.
- **Vault Taxonomy Files & Category Reference (`Cat00 - Index.md`, `Category Summaries/`)**:
  - Renamed 30 summary notes in Obsidian Vault from `Cat##-E.md`/`Cat##-R.md` to `Cat##-A.md`/`Cat##-U.md` and updated frontmatter aliases and tags.
  - Updated `Cat00 - Index.md` and `Cat01.md` through `Cat16.md` wikilinks to link canonical `-A` and `-U` summaries.
  - Ensured `load_cat00_index()` passes canonical `-A` and `-U` category references to LLM prompts during fact extraction.
- **Engine Fallbacks & UI Defaults (`Evelyn/tools/memory_db.py`, `evelyn_ui/dev.html`, `scripts/trigger_profile_evolution.py`)**:
  - Updated fallback in `split_entry()` to use `f"Cat05-{cfg.SUBJECT_CODE_USER}"`.
  - Updated fallback in `dev.html` split fact modal to use `Cat05-${currentIdentity.subject_code_user}`.
  - Refactored `trigger_profile_evolution.py` to import `DOCUMENT_CATEGORIES` dynamically from `profile_evolver` rather than hardcoding legacy `-R`/`-E` codes.

---

## [000.006.008] - 2026-08-28 — *Research Inspection, Sub-Question Notes & Resilient Guidance Tooling*

### Added & Enhanced
- **Research Inspection & Discovery Tooling (`Evelyn/tools/evelyn_tools.py`)**:
  - Added `list_research_tasks(status_filter, limit)` tool enabling Evelyn to list active, stalled, queued, and completed research tasks with status badges, confidence %, and stuck sub-questions.
  - Added `inspect_research_task(task_id, query, include_notes, sq_id, include_sources)` tool allowing Evelyn to inspect sub-questions, confidence ratings, knowledge gaps, and synthesized evidence digests (`sq_##_summary.md` / `sq_##_notes_summary.md`).
  - Implemented token-efficient output design: raw web sources registry is excluded by default (`include_sources=False`) and long raw notes are bounded.
- **Resilient & Fuzzy Research Guidance (`Evelyn/tools/evelyn_tools.py`)**:
  - Upgraded `guide_research(task_id, query, guidance)` to support query keyword / topic matching, auto-resolution when a single stalled task exists, and candidate list suggestions when queries are ambiguous.
  - Added flexible argument alias resolution for `guidance` (e.g. `instructions`, `terms`, `hint`, `prompt`).
- **System Notification Hardening for Struggling Research (`evelyn_server.py`)**:
  - Fixed `get_research_context()` in `evelyn_server.py` to identify struggling tasks (`state.get("struggling") == True` or sub-question in `needs_guidance`) even when process status is `"running"` or `"paused"`.
  - Corrected sub-question extraction in system prompt alerts to read `.get("question")` or `.get("search_query")` rather than missing `"query"` key, and ensured task IDs are clearly formatted.

---

## [000.006.007] - 2026-08-28 — *Procedure Suggested Tools, Tag Preservation & Advanced Filter*

### Fixed & Enhanced
- **Dedicated Suggested Tools Field & Procedure Split Parsing (`evelyn_ui/dev.html`)**:
  - Added dedicated `SUGGESTED TOOLS` input field to procedure merge and procedure split triage proposal cards.
  - Enhanced procedure YAML parsing (`parseProcedureYaml`) to isolate `suggested_tools:` without bleeding into `steps:` or `pitfalls:`.
  - Added structured multi-procedure card rendering and serialization (`parseProcedureSplitYaml`, `dumpProcedureSplitYaml`) for `procedure_split` proposals in the triage queue.
- **Domain Tag Preservation on Merged Procedures (`Evelyn/tools/procedure_consolidator.py`, `evelyn_server.py`, `Evelyn/tools/pending_reviewer.py`)**:
  - Updated background consolidation prompt instructions and few-shot examples to require domain tag preservation rather than substituting generic tags like `'procedure, merged'`.
  - Added source procedure tag aggregation and fallback preservation across LLM synthesis, server approval endpoints (`/api/review/proposals/{id}/approve`), and interactive CLI reviewer workflows.
- **Advanced Query Search & Exclusions Engine (`evelyn_ui/dev.html`)**:
  - Implemented client-side query parser supporting positive words, phrase matches (`"..."`), negative term exclusions (`-word`), exact tag matches (`tag:...`), and negative tag exclusions (`-tag:...`).
  - Integrated advanced query filtering across both the Triage Queue and Procedures Management tabs with real-time filtering and selection synchronization.

---

## [000.006.006] - 2026-08-27 — *Journal Entry Approval & Preview UI Fix*

### Fixed & Enhanced
- **Journal Entry Approval Card & Preview Rendering (`evelyn_ui/index.html`)**:
  - Fixed `addWriteBadges` in chat UI to include `write_journal_entry` in the approval IDs fetch filter (`terminalToolIds`), allowing pending journal write approvals to be resolved and displayed as interactive approval cards.
  - Resolved issue where `approvalStatuses` lookup was skipped for `write_journal_entry`, causing pending journal writes to fall back to an unclickable `⚠️ Approval expired/lost` badge.
  - Added real-time `approval_required` SSE stream event handler in `handleStreamEvent` to immediately populate `approvalStatuses` during streaming responses.

---

## [000.006.005] - 2026-08-27 — *Dynamic Fact Extractor Backlog Telemetry Fix*

### Fixed & Enhanced
- **Dynamic Fact Extractor Backlog Reporting (`evelyn_server.py`, `Evelyn/tools/fact_extractor.py`)**:
  - Fixed `/api/heavy_tasks` endpoint to always compute `unextracted_backlog` and the latest message cursor dynamically from `evelyn_chat.db` and the extraction state file.
  - Resolved short-circuiting issue where in-memory cached `sub_status` permanently overwrote live unextracted message counts with `0 msgs` on `evelyn_ui/dev.html`.
  - Filtered chat database message counts by `role IN ('user', 'assistant')` to strictly match fact extraction batch filtering criteria.
  - Removed hardcoded `unextracted_backlog: 0` from batch completion status notifications in `fact_extractor.py`.
- **Health Intraday Heart Rate Telemetry (`Evelyn/tools/health_manager.py`)**:
  - Ensured error and fallback responses in `get_granular_heart_rate` preserve `window_hours` metadata.

---

## [000.006.004] - 2026-08-27 — *Permanent Deletion Controls for Procedures & Triage Items*

### Added & Enhanced
- **Permanent Hard-Deletion Database Primitives (`Evelyn/tools/memory_db.py`)**:
  - Implemented `hard_delete_procedure(proc_id: int)` to permanently remove procedures and purge orphaned references from `procedure_split_queue` and `procedure_merge_queue`.
  - Implemented `delete_proposal(proposal_id: int)` to permanently remove pending or rejected triage proposals from SQLite.
  - Implemented `hard_delete_entry(entry_id: int)` to permanently delete context entries and unlink references from pending proposals.
- **Server Endpoints for Permanent Removal (`evelyn_server.py`)**:
  - Added `DELETE /api/procedures/{id}` and `POST /api/procedures/{id}/delete` endpoints.
  - Updated `POST /api/review/procedures/{id}/{action}` to handle permanent hard deletion when `action in ("delete", "hard_delete")`.
  - Updated `POST /api/review/proposals/{id}/{action}` to handle permanent proposal deletion when `action in ("delete", "hard_delete")`.
- **UI Permanent Delete & Remove Controls (`evelyn_ui/dev.html`)**:
  - Added permanent `🗑️ Delete` button on Procedure Management cards with confirmation modal, supporting hard deletion of old/outdated procedures.
  - Added permanent `🗑️ Remove` / `🗑️ Delete` buttons with confirmation dialogs to all Triage Queue cards (Procedures, Fact Splits, Merges, Recategorizations, and Profile Updates).
- **Test Suite Cleanups & Coverage (`Evelyn/tests/test_procedures_upgrade.py`)**:
  - Cleaned up 91 orphaned test procedure records from `data/evelyn_memory.db`.
  - Updated all procedure unit tests to use `hard_delete_procedure` teardown, preventing test runs from accumulating archived dummy rows in the active database.
  - Added `test_hard_deletion_primitives` covering `hard_delete_procedure`, `hard_delete_entry`, and `delete_proposal`.

---

## [000.006.003] - 2026-08-27 — *Procedure Management Search Focus Preservation & UI Fixes*

### Fixed & Enhanced
- **Procedures Management Search Filter (`evelyn_ui/dev.html`)**:
  - Decoupled the filter bar and search input controls from the procedures list DOM container.
  - Resolved input focus loss bug where typing in the procedure search box wiped and recreated the entire tab container on each keystroke.
  - Kept filter pill status counts and selection bar dynamically reactive while preserving continuous typing focus and caret position.

---

## [000.006.002] - 2026-08-27 — *Persistent FIFO Idle Task Queue & Cooperative Batch Catch-Up*

### Added & Enhanced
- **Persistent FIFO Idle Task Queue (`task_manager.py`, `evelyn_config.py`)**:
  - Implemented centralized FIFO task queue (`_idle_queue`) in `task_manager.py` with disk persistence (`data/evelyn_task_queue.json`) and crash recovery.
  - Interrupted running tasks upon reboot/server restart are automatically reconciled back to the front of the queue.
  - Implemented `IDLE_STARTUP_GRACE_PERIOD` (default 60s) to prevent deep research and background tasks from prematurely firing upon boot.
- **Cooperative Yield & Multi-Tool Batch Catch-Up (`fact_extractor.py`, `tag_librarian.py`, `evelyn_server.py`)**:
  - Uncapped fact extraction with `FACT_EXTRACTION_MAX_BATCHES_PER_SESSION = 0` (unlimited idle drain) and added `FACT_EXTRACTION_BACKLOG_DELAY = 5`s.
  - Refactored `fact_extractor.py` and `tag_librarian.py` to commit progress cursors to SQLite after each batch/item and check `task_manager.should_yield()`.
  - When peer tasks are queued, the active tool yields cleanly and re-enqueues at the tail of the line; when the queue is empty, it continues draining its backlog.
- **Zero-Delay Chat Preemption (`evelyn_server.py`, `task_manager.py`)**:
  - Interactive user chat immediately sets `task_manager.set_chat_preemption(True)` and triggers `cancel_all_idle_tasks()`, releasing 100% of compute and GPU inference power to conversational turns.
- **Centralized Idle Dispatcher (`evelyn_server.py`)**:
  - Replaced isolated task execution loops with a central `_idle_task_dispatcher_loop()`, while individual timer loops enqueue their intent via `task_manager.enqueue_idle_task()`.
- **Testing & Verification**:
  - Added `Evelyn/tests/test_idle_task_queue.py` verifying FIFO queuing, persistence, crash recovery, cooperative yield/re-enqueue, and preemption.
  - Full test suite passing at 144/144 tests.

---

## [000.006.001] - 2026-08-27 — *High-Resolution Granular Biometrics & Intraday Health Queries*

### Added & Enhanced
- **High-Resolution Intraday Biometrics Engine (`health_manager.py`, `oura_client.py`)**:
  - Implemented `get_granular_heart_rate(hours=N)` to fetch high-resolution live heart rate readings from Oura Cloud API v2 (`/v2/usercollection/heartrate`) with local Health Connect SQLite fallback.
  - Generates instant statistical summaries: `current_latest_bpm`, `min_bpm`, `max_bpm`, `avg_bpm`, total sample count, activity source breakdowns (`workout`, `awake`, `rest`, `sleep`), and downsampled 15-minute timeline chunks for clean model synthesis.
  - Implemented `get_intraday_activity(hours=N)` to slice step counts, active calories, and distance over custom intraday time windows.
  - Enhanced `get_recent_workouts(days=N, hours=N)` to seamlessly merge live Oura workout sessions with Health Connect records, deduplicating identical events by timestamp.
- **Health Model Tool Enhancements (`evelyn_tools.py`)**:
  - Updated `get_health_metrics` and `get_recent_workouts` to accept an `hours` parameter (e.g. `hours=2` for last 2 hours), supporting granular sub-day queries.
  - Updated OpenAPI tool definitions in `MODEL_TOOL_DEFINITIONS` with explicit instructions on querying live heart rate and sub-day activity.
- **Testing & Verification**:
  - Added `test_18_health_metrics_granular_and_intraday` to `Evelyn/tests/test_all_tools_end_to_end.py`. Total test suite passes at 138/138.

---

## [000.006.000] - 2026-08-27 — *Unified Single-Stream Agentic Architecture*

### Added & Enhanced
- **Unified Single-Stream Agentic Architecture (`evelyn_server.py`, `evelyn_config.py`)**:
  - Completely decommissioned the legacy 2-pass inference pipeline (non-streaming tool detection Pass 1 followed by streaming text Pass 2).
  - Implemented `_agentic_stream_loop()`, providing a single unified async generator where Ollama streams native thinking deltas in real-time and transitions seamlessly into tool execution or markdown synthesis in the same HTTP stream.
  - Eliminated duplicate thinking latency on regular conversational turns, slashing response time by ~50% and cutting token overhead.
  - Hardened with 6 production safeguards:
    1. **Preamble Token Quarantining**: Quarantines pre-tool text deltas from Round 1 if tool calls are emitted, preventing content duplication in final responses.
    2. **Exception-Safe Tool Feedback**: Catches all tool execution errors in try/except and formats structured feedback (`role: "tool"`), allowing the model to inspect errors and self-correct across rounds.
    3. **Hard Terminal Round Enforcement**: Forces `tools=None` when reaching `MAX_TOOL_ROUNDS` to guarantee synthesis.
    4. **Cumulative Metrics Accounting**: Aggregates token counts (`eval_count`, `prompt_eval_count`) and timing duration across all agentic sub-rounds.
    5. **Async Interruption Safety**: Preserves `asyncio.CancelledError` safety with shielded SQLite commits for interrupted sessions.
    6. **Tool Effort Escalation**: Automatically raises thinking depth across subsequent rounds when tools requiring deeper reasoning are invoked.
- **Frontend Unified Activity Stepper (`index.html`)**:
  - Replaced disconnected thinking accordion bars with a unified `<details class="agent-activity-trace">` component rendered at the top of assistant bubbles.
  - Tracks discrete round steps (`● Round 1: Reasoning & Exploration`, `● Tools Executed`, `● Round 2: Synthesis`), displaying interactive tool chips with status spinners and failure badges.
  - Auto-collapses cleanly upon stream completion into a compact summary header (`▾ Thought for 3.4s • 1 tool used`), keeping the conversation clean and readable.
  - Added full retrospective support in `loadHistory()`, rendering historical thinking and tool execution chains into unified activity traces.
- **Agentic Streaming Test Suite (`test_agentic_stream.py`)**:
  - Added dedicated unit tests covering single-pass direct conversation, multi-round tool dispatch, preamble quarantine, tool error resilience, and terminal round enforcement.

---

## [000.005.021] - 2026-08-27 — *Tool Prediction Budget Expansion & Special Token Sanitization*

### Fixed & Enhanced
- **Tool Loop Prediction Budget (`evelyn_config.py`)**:
  - Expanded `TOOL_LOOP_NUM_PREDICT` from `2048` to `8192` tokens. Previously, when generating long files (such as comprehensive dream journal entries or structured reports) along with chain-of-thought reasoning in Tool Round 0, the prediction exceeded 2048 tokens and truncated before the tool call payload was emitted, causing the turn to skip the tool loop and fall back into an ungrounded response pass.
- **Gemma 4 Channel & Triangle Token Sanitization (`evelyn_server.py`)**:
  - Added Gemma 4 special tokens (`◀channel▶`, `◀thought▶`, `◀/thought▶`, `◀call:`, `▶call`, `<|channel|>`, `<|thought|>`, `<|tool_call|>`, `◀|`, `|▶`) to `_LEAKED_MODEL_TOKENS` to ensure model internal channel transitions are filtered cleanly from user-facing streams and don't cause thinking or response stream anomalies.

---

## [000.005.020] - 2026-08-27 — *Unified Vault File Staging Pipeline & Tool Disambiguation*

### Added & Enhanced
- **Mutual Tool Schema Disambiguation (`evelyn_tools.py`)**:
  - Sharpened the LLM schema docstring for `write_journal_entry` to exclusively cover Evelyn's personal daily reflection diary (vibe check, narrative recap, message in a bottle) and explicitly forbade its use for user-authored notes, dream journals, or general vault documents.
  - Updated `write_file`'s schema to explicitly include dream journals (`Dream Journal/Dream Entries/Dream Entry YYYY-MM-DD.md`), feature ideas, user notes, and scripts.
- **Unified Staging Pipeline for Journal Entries (`journal_manager.py`, `evelyn_server.py`, `index.html`)**:
  - Re-routed `create_journal_entry()` through `terminal_agent.write_file()` targeting `JOURNAL_DIR` directly, completely eliminating temporary file creation in `_Pending Approvals/` or vault root.
  - Unified journal entries with the terminal agency modal preview, allowing one-click `👁️ Preview & Review`, `✓ Approve & Write`, and guided denial feedback.
- **Multi-Turn Tool Execution Context in Chat History (`evelyn_server.py`)**:
  - Enhanced `load_history()` to append `[Tools Executed: ...]` for assistant turns with tool invocations, ensuring the model retains full multi-turn awareness of its past actions when receiving denial or approval feedback in subsequent turns.

---

## [000.005.019] - 2026-08-27 — *Terminal & File Write Modal Previews and Silent Approvals*

### Added & Enhanced
- **Terminal & File Write Modal Inspection (`index.html`, `terminal_agent.py`, `evelyn_server.py`)**:
  - Implemented `get_approval_details(approval_id)` in `terminal_agent.py` and exposed `GET /api/terminal/details/{approval_id}` in `evelyn_server.py` to retrieve full un-truncated file content, write mode, command strings, and directory paths for pending and past approval requests.
  - Added rich modal review (`openModal('approval', id)`) in `index.html` matching journal entries, rendering full markdown formatting for `.md` documents, code syntax previews for raw files, target path badges, and action bars.
  - Added `👁️ Preview & Review` action button to in-chat approval cards and made approved badges clickable to reopen and view saved files.
- **Silent Approvals & Guided Denial Feedback (`index.html`, `evelyn_server.py`)**:
  - Configured `handleApproval` to execute `write_file` silently without dispatching redundant `[System: Command output: ...]` chat turns back to the agent loop, preserving natural conversation flow.
  - Added guided rejection prompt on Deny, enabling users to submit concise feedback (e.g. format corrections or folder adjustments) that is cleanly passed as user input to guide Evelyn's subsequent turn.

---

## [000.005.018] - 2026-08-27 — *Procedures Tool Integration, Queue Pipeline & DevUI Management*

### Added & Enhanced
- **Procedure Tool Guidance & Disambiguation (`fact_extractor.py`, `chroma_rag.py`)**:
  - Added `suggested_tools` column to the `procedures` table in `evelyn_memory.db` via database migration `000.005.018`.
  - Updated procedure extraction prompt with the engine's canonical active tool palette, explicitly guiding the extractor to associate procedures with tools like `write_file` (for Dream Journals, feature ideas, and vault notes) while strictly reserving `write_journal_entry` for Evelyn's personal daily reflection recap.
  - Enhanced RAG context assembly in `chroma_rag.py` to format retrieved procedures as actionable operational protocols (`[Operational Protocol: ...]`) with highlighted `Suggested Tool(s): <tools>`.
- **Standardized Procedure Merge & Split Queues (`memory_db.py`, `procedure_consolidator.py`, `pending_reviewer.py`)**:
  - Implemented `procedure_merge_queue` and `procedure_split_queue` tables in `evelyn_memory.db` with CRUD helper functions.
  - Extended `procedure_consolidator.py` to process manually queued merge and split requests during background idle passes before running automated trigger clustering.
  - Added `generate_procedure_split_proposal()` and updated proposal approval handlers in `pending_reviewer.py` and `evelyn_server.py` to support `procedure_split` proposals and preserve `suggested_tools`.
- **Dedicated DevUI Procedures Management Tab (`dev.html`, `evelyn_server.py`)**:
  - Added **⚙️ Procedures** tab in DevUI with live count, real-time search filter across triggers, steps, tools, and tags, and status filter pills (`All`, `Live`, `Pending Review`, `Archived`).
  - Added floating multi-select merge action bar allowing one-click selection of multiple procedure cards to queue for background LLM consolidation.
  - Added inline procedure editing (`Save Changes`), background split queuing (`Queue Split`), and soft archiving/restoration.
  - Exposed REST endpoints in `evelyn_server.py`: `GET /api/procedures`, `PATCH /api/procedures/{id}`, `POST /api/procedures/queue_merge`, `POST /api/procedures/{id}/queue_split`, `POST /api/procedures/{id}/archive`.

---

## [000.005.017] - 2026-08-26 — *RAG Ingestion Boilerplate Filtering & YAML Exclusion Support*

### Added & Enhanced
- **Pattern-Based RAG Ingestion Exclusion (`evelyn_config.py`, `ingest_obsidian_knowledge.py`)**:
  - Configured `RAG_IGNORE_PATTERNS` to automatically bypass structural book boilerplate (back-of-book indexes like `* - Index.md`, `*_index.md`, `*Table of Contents.md`, `* - Colophon.md`, `* - About the Author*.md`) during vector embedding.
  - Keeps navigation/index files accessible in the Obsidian vault for manual browsing while preventing semantic keyword saturation and false-positive vector hits in RAG context.
- **YAML Frontmatter & Tag-Based RAG Exclusion**:
  - Added support for explicit document-level RAG bypass via YAML frontmatter (`rag_exclude: true`, `rag_ignore: true`, `no_rag: true`) or tags (`#rag-ignore`, `#rag-exclude`, `#no-rag`).
  - Integrated automatic Chroma document garbage collection (`chroma_rag.delete_document`) during sync passes when notes are marked excluded or match ignore patterns.

---

## [000.005.016] - 2026-08-26 — *Chat UI Stream Lifecycle & Reconciler Consolidation*

### Fixed & Enhanced
- **Consolidated Chat Streaming Architecture (`index.html`)**:
  - Implemented unified `setupAssistantStreamContext()` and `executeChatStream()` across message sends, message edits, and response regenerations.
  - Replaced legacy `recoverFromConnectionDrop()` and blind 2-minute `startResponsePoll()` timers with an authoritative, coordinated `reconcileStreamFailure()` routine.
  - Eliminated race conditions between visibility recovery and background fetch promises when reconnecting active streams or pulling missed history.
  - Synchronized `initApp()` startup sequence to smoothly catch up on in-flight stream sessions without UI jitter or duplicate message bubbles.

---

## [000.005.015] - 2026-08-26 — *Pinned Alias Word Boundaries & Client-Side Chunk Highlighting*

### Fixed & Enhanced
- **Word-Boundary Matching on Pinned Aliases (`chroma_rag.py`)**:
  - Replaced naive substring matching with regex word boundaries (`\b`) when scanning query text for pinned vault note aliases.
  - Eliminated false positives where common words (e.g. `"same"`, `"sample"`) inadvertently triggered pinned notes for aliases like `"Sam"`.
- **Client-Side Chunk Highlighting & De-Emphasis (`dev.html`)**:
  - Implemented zero-database-overhead chunk extraction in `dev.html` (`splitDocumentIntoChunks` and `formatChunkHighlightInDoc`).
  - When expanding a retrieved chunk, the viewer highlights the exact section injected into the LLM prompt with a luminous accent border while smoothly de-emphasizing non-referenced surrounding document sections.
  - Added automated unit test coverage in `Evelyn/tests/test_feedback_and_rag_telemetry.py`.

---

## [000.005.014] - 2026-08-26 — *Analytics & Feedback Filter Controls with 1-Day Default Windowing*

### Added & Enhanced
- **Analytics & Feedback Filter Controls (`dev.html`)**:
  - Implemented independent **Time Range Quick Pickers** (`1 Day`, `1 Week`, `1 Month`, `All Time`) with active pill highlights.
  - Implemented independent **Type Filter** selector (`All Analytics Types`, `Conversational Feedback`, `RAG Context Retrieval Log`) allowing targeted visibility without resetting time range.
  - Added a **Reset Filters** button returning state to the optimized 1-day all-types view.
  - Set default view on tab switch to **1 Day** (`days=1`), drastically cutting initial payload sizes and preventing unnecessary full-history database scans on load.
- **Server-Side Time Windowing (`evelyn_server.py` & `chroma_rag.py`)**:
  - Enhanced `GET /telemetry/feedback` and `GET /telemetry/rag` to accept an optional `days: float` query parameter.
  - Filtered feedback counts (`total_rated`, `upvotes`, `downvotes`, `satisfaction_rate`) and recent records dynamically based on `created_at >= cutoff`.
  - Added test coverage in `Evelyn/tests/test_feedback_and_rag_telemetry.py` for time-range windowing and API responses.

---

## [000.005.013] - 2026-08-25 — *Consolidator Scan Continuity & Fast Exact Deduplication*

### Fixed & Enhanced
- **Consolidator Scan Continuity (`fact_consolidator.py`)**:
  - Fixed an issue where `_get_anchor_batch` reset the scan anchor pointer to `0` whenever a new fact modified category entry count `len(records)`. The pointer now wraps continuously (`anchor = anchor % n`) across consolidation passes.
  - Optimized `remediate_database_categories` to use SQL `NOT GLOB` filtering to avoid loading hundreds of thousands of historical proposal records into memory.
- **Fast Deterministic Deduplication Pre-Pass**:
  - Implemented `fast_deduplicate_exact_matches()` to detect and collapse exact and whitespace-normalized duplicate context entries into primary records, merging metadata and enqueuing vector deletions.
- **Targeted Cluster Consolidation**:
  - Merged 8 fragmented, redundant *Dungeon Crawler Carl* context facts in `Cat05-U` (IDs 2310, 2313, 2391, 2407, 2548, 2563, 2565, 2650) into a single, unified evolved entry (`#3972`), recorded proposal `#176816`, and updated Chroma vector embeddings.

---

## [000.005.012] - 2026-08-25 — *Interactive Feedback Comments & Vault Note Editor in DevUI*

### Added
- **Vault Note Reader & Editor API (`evelyn_server.py`)**:
  - Added `GET /api/vault/note` to retrieve full markdown content of any note within vault boundaries with path traversal protection.
  - Added `POST /api/vault/note` to write note edits directly to disk, update `vault_db`, and enqueue custodial re-indexing into `chroma_sync_queue`.
- **RAG Telemetry Content & Expandable Chunks**:
  - Captured full chunk content in `chroma_rag.py` retrieval logs (`rag_retrieval_log`).
  - Added expandable full chunk viewing and inline `✏️ Edit Note` buttons in `dev.html` telemetry inspector.
  - Added dedicated Vault Note Editor Modal in `dev.html` with in-browser editing and re-indexing.
- **Feedback Comments & Expandable Responses (`dev.html` & `index.html`)**:
  - Added `💬 Add/Edit Comment` action to `index.html` chat message actions.
  - Added expandable full response and thinking trace toggles to feedback cards in `dev.html`.
  - Added Feedback Explanation & Comment modal in `dev.html` for reviewing and amending ratings.
- **Automated Tests**:
  - Added `test_vault_note_endpoints` in [Evelyn/tests/test_feedback_and_rag_telemetry.py](file:///home/rathius/evelyn/Evelyn/tests/test_feedback_and_rag_telemetry.py).

---

## [000.005.011] - 2026-08-25 — *Thinking Level Telemetry & Metrics Exposure*

### Added
- **Thinking Effort & Source Exposure in Telemetry APIs (`evelyn_server.py`)**:
  - Added `GET /telemetry/thinking` endpoint providing aggregate counts of resolved thinking effort levels (`low`, `medium`, `high`, `max`), resolution sources (`heuristic`, `self_elected`, `tool_escalation`, `ui_override`), and recent message thinking audit logs.
  - Linked `think_effort` and `think_source` from `message_metrics` into `GET /history` and `GET /telemetry/feedback` payloads for review.
- **Automated Tests**:
  - Expanded [Evelyn/tests/test_feedback_and_rag_telemetry.py](file:///home/rathius/evelyn/Evelyn/tests/test_feedback_and_rag_telemetry.py) to assert thinking level telemetry collection and endpoint accuracy.

---

## [000.005.010] - 2026-08-25 — *Conversational Feedback & RAG Telemetry Logging System*

### Added
- **Database Schema Migrations (`000.005.010`)**:
  - Registered migration `000.005.010` in [Evelyn/tools/db_migrator.py](file:///home/rathius/evelyn/Evelyn/tools/db_migrator.py) creating `message_feedback` table in `evelyn_chat.db` (for 👍/👎 user rating and comments per assistant message).
  - Registered migration `000.005.010` creating `rag_retrieval_log` table in `evelyn_memory.db` (for real-time tracking of vector retrieval events, similarity distances, kept vs dropped threshold status, and source note paths).
- **RAG Telemetry Logging Interceptor (`chroma_rag.py`)**:
  - Implemented `log_rag_retrieval()`, `get_recent_rag_telemetry()`, and `link_rag_telemetry_to_message()` in [Evelyn/tools/chroma_rag.py](file:///home/rathius/evelyn/Evelyn/tools/chroma_rag.py).
  - Wired telemetry logging directly into `build_rag_context()` with fire-and-forget execution and zero added latency.
- **Server Feedback & Telemetry Endpoints (`evelyn_server.py`)**:
  - Added `save_or_update_feedback()` and `get_feedback_for_messages()` database helpers.
  - Added `POST /chat/feedback` (upsert user ratings), `GET /chat/feedback/{message_id}`, `GET /telemetry/rag` (recent retrieval logs), and `GET /telemetry/feedback` (feedback counts and satisfaction ratios).
  - Hydrated feedback state into `GET /history` messages payload.
  - Emitted `message_id` inside the final SSE `done` event chunk for immediate UI feedback binding.
- **Chat UI Feedback Toolbar (`index.html`)**:
  - Added interactive 👍 / 👎 buttons with toggle animation and active color states inside `.msg-actions` for assistant messages.
  - Restores saved feedback state upon loading conversation history.
- **DevUI Telemetry Dashboard (`dev.html`)**:
  - Added **📊 Telemetry & Feedback** dashboard tab displaying total ratings, upvote/downvote satisfaction rate, recent rated responses, and expandable RAG context retrieval inspection logs.
- **Automated Tests**:
  - Added test suite in [Evelyn/tests/test_feedback_and_rag_telemetry.py](file:///home/rathius/evelyn/Evelyn/tests/test_feedback_and_rag_telemetry.py) covering CRUD feedback operations, RAG telemetry logging, and API endpoints.

---

## [000.005.009] - 2026-08-23 — *Obsidian Vault List & Checklist Management System*

### Added
- **Obsidian Vault List Manager (`vault_list_manager.py`)**:
  - Implemented [Evelyn/tools/vault_list_manager.py](file:///home/rathius/evelyn/Evelyn/tools/vault_list_manager.py) providing offline-first list and checklist operations directly on markdown notes in the Obsidian Vault (`vault.root/Lists/`).
  - Added template-driven initialization supporting category templates ([templates/lists/groceries.md](file:///home/rathius/evelyn/templates/lists/groceries.md)) and generic lists ([templates/list_template.md](file:///home/rathius/evelyn/templates/list_template.md)).
  - Implemented item-first presentation format (`Item (Qty Unit)` / `Item (2x)`), category-aware section routing, intelligent quantity incrementing on existing items, fuzzy checkbox toggling (`- [ ]` $\leftrightarrow$ `- [x]`), item removal, and completed cleanup.
- **Model Function Calling Tool**:
  - Registered `manage_vault_list` in [Evelyn/tools/evelyn_tools.py](file:///home/rathius/evelyn/Evelyn/tools/evelyn_tools.py) `MODEL_TOOL_DEFINITIONS` and `TOOL_FUNCTIONS` supporting structured item objects, string lists, and flexible actions (`read`, `add`, `check`, `uncheck`, `remove`, `clear_completed`, `list_all`).
- **Configuration**:
  - Added `LISTS_DIR` path in [evelyn_config.py](file:///home/rathius/evelyn/evelyn_config.py).

---

## [000.005.008] - 2026-08-23 — *Google Tasks Integration & Dedicated Task Synchronizer*

### Added
- **Google Tasks Dedicated Synchronizer (`gtasks_sync.py`)**:
  - Implemented [Evelyn/tools/gtasks_sync.py](file:///home/rathius/evelyn/Evelyn/tools/gtasks_sync.py) providing offline-first task synchronization, SQLite caching, OAuth credential loading/refreshing, and full CRUD operations (`sync_gtasks`, `get_cached_tasks`, `create_gtask`, `complete_gtask`, `delete_gtask`).
  - Added [scripts/setup_gtasks.py](file:///home/rathius/evelyn/scripts/setup_gtasks.py) for interactive OAuth2 setup with automatic fallback to existing Google credentials.
- **Model Function Calling Tools**:
  - Registered `create_task`, `complete_task`, `delete_task`, `list_tasks`, and `sync_google_tasks` in [Evelyn/tools/evelyn_tools.py](file:///home/rathius/evelyn/Evelyn/tools/evelyn_tools.py) `MODEL_TOOL_DEFINITIONS` and `TOOL_FUNCTIONS`.
  - Updated `get_agenda` to present a unified schedule displaying both Google Calendar events and pending Google Tasks.
- **Server Background Sync & System Context**:
  - Added periodic background `_gtasks_sync_loop()` in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py) (running every 30 minutes).
  - Updated `get_upcoming_agenda_prompt_context()` in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py) to inject pending task notifications into the system prompt.
- **Database Schema Migration**:
  - Registered migration `000.005.008` (`create_tasks_table`) in [Evelyn/tools/db_migrator.py](file:///home/rathius/evelyn/Evelyn/tools/db_migrator.py) creating the `tasks` SQLite cache table in `evelyn_chat.db`.

---

## [000.005.007] - 2026-08-23 — *Graceful Service Shutdown Lifecycle & UPS Integration*

### Added
- **Graceful Stop Script & Workflow**:
  - Enhanced [scripts/stop_evelyn_services.sh](file:///home/rathius/evelyn/scripts/stop_evelyn_services.sh) with `--all`/`--with-ollama` and `--checkpoint-wal`/`--flush-wal` options for clean teardown.
  - Added dedicated workflow guide in [.agents/workflows/stop-services.md](file:///home/rathius/evelyn/.agents/workflows/stop-services.md) (`/stop-services`).
  - Added "Stop All Services (with Ollama & WAL flush)" task in [.vscode/tasks.json](file:///home/rathius/evelyn/.vscode/tasks.json).
- **Physical Environment UPS Hook (Sanctum)**:
  - Created `scripts/personal/ups_shutdown_hook.sh` and registered symlinks in `/etc/apcupsd/` (`doshutdown`, `failing`, `timeout`, `loadlimit`, `runlimit`, `emergency`) to safely stop services and checkpoint SQLite WAL journals when UPS signals power failure.

### Fixed
- **ChromaDB Queue Drain Deadline Hardening**:
  - Added strict per-item `deadline` parameter and checking to `drain_sync_queue()` and `flush_sync_queue()` in [chroma_rag.py](file:///home/rathius/evelyn/Evelyn/tools/chroma_rag.py).
  - Implemented automatic transaction rollback from `'processing'` to `'pending'` for unprocessed items when deadline expires mid-batch, preventing shutdown hangs.
- **FastAPI Lifespan Background Task Cancellation**:
  - Tracked all background `asyncio.Task` instances in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py) lifespan and cleanly cancelled/gathered them before calling `clean_shutdown_all_tasks()`.
- **Systemd Timeout Tuning**:
  - Tuned `TimeoutStopSec=15` in `/etc/systemd/system/evelyn.service`.

---

## [000.005.006] - 2026-08-23 — *Granular Source Entry Management for Merge Proposals*

### Added
- **Granular Source Item Editing & Unlinking in Proposals**:
  - Added support for editing, unlinking, and deleting individual source procedures directly on `procedure_merge` proposal cards in [dev.html](file:///home/rathius/evelyn/evelyn_ui/dev.html).
  - Extended `/api/review/procedures/{id}/{action}` in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py) to support `edit` and `delete` actions that commit changes immediately to the database.
  - Unified `renderSourceEntriesList` across all proposal types (`procedure_merge`, `merge`, `supersede`, `split`, and `profile_update`) to allow instant inline edits to persist to the database regardless of whether the overarching proposal is approved, denied, or unlinked.

---

## [000.005.005] - 2026-08-23 — *YAML Scalar Unquoting & Proposal Text Sanitization*

### Fixed
- **DevUI Proposal Text Rendering & YAML Escaping**:
  - Implemented `cleanYamlScalar` in [dev.html](file:///home/rathius/evelyn/evelyn_ui/dev.html) to properly decode single-quoted, double-quoted, folded, and block YAML scalars.
  - Resolved double apostrophe escaping (`''` to `'`) in quotes and contractions (`it's`, `AI's`) across proposed steps, trigger patterns, and split observations.
  - Eliminated random mid-sentence line breaks caused by PyYAML 80-character line folding.
  - Updated [procedure_consolidator.py](file:///home/rathius/evelyn/Evelyn/tools/procedure_consolidator.py) to dump YAML with `width=10000` to prevent line wrapping during proposal generation.
  - Sanitized existing pending procedure merge proposals in `evelyn_memory.db`.

---

## [000.005.004] - 2026-08-23 — *Environment Configuration & Network Parameterization*

### Added
- **Local Environment Support (`.env`)**:
  - Implemented automatic `.env` loader in [evelyn_config.py](file:///home/rathius/evelyn/evelyn_config.py) to read local network, port, SSL, and service endpoint overrides.
  - Added clean, documented [.env.example](file:///home/rathius/evelyn/.env.example) template for version control and new environment provisioning.

### Changed
- **Network & Host Parameterization**:
  - Parameterized CORS `ALLOWED_ORIGINS` to dynamically incorporate values from `EVELYN_ALLOWED_ORIGINS` alongside standard localhost origins.
  - Parameterized `IMAGE_SERVER_URL` via `EVELYN_IMAGE_SERVER_URL` in [evelyn_config.py](file:///home/rathius/evelyn/evelyn_config.py) and [check_evelyn_status.sh](file:///home/rathius/evelyn/scripts/check_evelyn_status.sh).
  - Parameterized SSL key/cert paths in [evelyn_server.py](file:///home/rathius/evelyn/evelyn_server.py).
  - Generalized architecture diagrams and component topologies in [engine_architecture.md](file:///home/rathius/evelyn/reference/engine_architecture.md) and unit test mocks in [test_image_generation.py](file:///home/rathius/evelyn/Evelyn/tests/test_image_generation.py).

---

## [000.005.003] - 2026-08-23 — *Image Host Requirements Documentation & Sanitization*

### Added
- **Core Requirements Integration**:
  - Incorporated the **FLUX.1 Schnell NF4 Image Generation Microservice** specifications and dependencies into [REQUIREMENTS.md](file:///home/rathius/evelyn/REQUIREMENTS.md) under External Services.

### Changed
- **Documentation Sanitization & Identity Parameterization**:
  - Parameterized host-specific domains, IPs, and user directory paths in [REQUIREMENTS_IMAGE_HOST.md](file:///home/rathius/evelyn/services/image/REQUIREMENTS_IMAGE_HOST.md) into generic placeholders (`<image-host>`, `<tailnet>`, `<username>`).
  - Added multi-platform (Windows & Linux) virtual environment and firewall configuration instructions.

---

## [000.005.002] - 2026-08-23 — *DevUI Split Proposal & Ingestion Layout Refinements*

### Fixed
- **DevUI Split & Proposal Card Layout**:
  - Separated metadata/badges and action buttons (`Split`, `Edit`, `Unlink`, `Delete`, `Remove`) into a top header row across Source Compound Entry, Proposed Atomic Context Facts, and Profile Update cards in [dev.html](file:///home/rathius/evelyn/evelyn_ui/dev.html).
  - Observation text content and domain tags now render on dedicated full-width rows rather than being squished into a narrow column alongside button groups.
  - Added responsive `flex-wrap` and minimum widths to inline editing forms and Split Modal draft inputs.
- **Document Ingestion Staging & Mode Layout**:
  - Restructured **Direct Filesystem Staging** directory guide cards so folder paths and explanations sit on separate full-width rows instead of cramping horizontally.
  - Made the **Ingestion Mode** radio card container responsive with `repeat(auto-fit, minmax(260px, 1fr))` for mobile and narrow viewports.

---

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
  - Converted medical psychology reports in `Personal/Medical/Psychology/` and Python reference notes into structured chapter notes.
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
