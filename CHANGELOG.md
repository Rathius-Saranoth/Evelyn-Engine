---
title: CHANGELOG.md
date created: 2026-08-22 15:53:28
date modified: 2026-10-08 18:43:27
tags: [changelog, versioning, history, release-notes, evelyn]
---
# 📜 Changelog

> Navigation: [[README.md]] · [[ROADMAP.md]] · [[AGENTS.md]] · [[engine_architecture.md]]

All notable changes to the Evelyn Engine are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to **3-digit zero-padded Semantic Versioning** (`000.000.000`).

## [000.008.019] - 2026-10-08 — *Python 3.12 Compatibility, Exception Syntax Standardization & Production Deployment*

### Fixed

- **Python 3.12 Syntax Standardization (`evelyn_config.py`, `evelyn_server.py`, `Evelyn/tools/evelyn_tools.py`)**:
  - Standardized all 46 unparenthesized multi-exception clauses (`except A, B:`) into canonical parenthesized tuples (`except (A, B):`), resolving `SyntaxError: multiple exception types must be parenthesized` under Python 3.12 on Ubuntu Server 24.04 LTS.
- **Static Assets Directory Initialization (`evelyn_server.py`)**:
  - Added `os.makedirs(cfg.IMAGE_OUTPUT_DIR, exist_ok=True)` prior to mounting `/images` in `evelyn_server.py`, preventing `RuntimeError: Directory does not exist` on fresh checkouts where `services/image/output/` is gitignored.
- **Dependency Manifest & Prerequisites (`requirements.txt`, `REQUIREMENTS.md`, `SETUP_GUIDE.md`)**:
  - Added `psutil>=5.9.0` canonically to `requirements.txt` and `REQUIREMENTS.md` for process inspection and memory telemetry.
  - Added `python3.12-venv` to Ubuntu 24.04 package prerequisites in `SETUP_GUIDE.md` to ensure `ensurepip` is installed during virtual environment creation.

## [000.008.018] - 2026-10-08 — *Sanctum Bare-Metal Infrastructure, Service Management & Setup Documentation Suite*

### Added

- **Bare-Metal Setup & Installation Suite (`SETUP_GUIDE.md`)**:
  - Completely restructured and expanded `SETUP_GUIDE.md` for bare-metal enterprise hardware (**Sanctum** — HPE ProLiant DL360 Gen10) running **Ubuntu Server 24.04 LTS (Noble Numbat)**.
  - Documented physical hardware profile (Dual Intel Xeon Gold 5220R @ 2.20GHz, 192GB ECC RAM, Tesla T4 16GB GDDR6 on PCIe Slot 1 / NUMA Node 0, iLO 5 management).
  - Documented dual-drive storage allocation and partitioning schema: `sda` (1TB WD Blue SA510 SSD) with EFI, `/boot`, and LVM `vg_sanctum` (120GB `lv_root`, 32GB `lv_swap`, ~776GB unallocated pool for runtime snapshots/expansion); `sdb` (1TB WD Blue SA510 SSD) mounted at `/data` via `/etc/fstab` for automated backups, vector snapshots, and local media.
  - Documented system prerequisites: persistent systemd user lingering (`loginctl enable-linger`), base packages (`numactl` note regarding `numastat`), proprietary headless enterprise NVIDIA driver (`nvidia-headless-550-server`), and Tailscale mesh networking (`100.93.26.14`).
  - Added NUMA Node 0 binding drop-in override for Ollama (`/etc/systemd/system/ollama.service.d/override.conf`) to eliminate UPI bus cross-socket memory latency for model weights and KV caches.
  - Documented multi-virtual environment architecture for decoupled dependency isolation (`venv/` for core engine, `services/tts/venv/` for Chatterbox CUDA speech, and `services/stt/` for Faster-Whisper on CPU int8).
  - Detailed Syncthing headless setup, Web GUI address rebinding (`0.0.0.0:8384`), and mandatory `.stignore` pre-population in `/home/rathius/obsidian_vault` before pairing devices to prevent vault pollution from workspace and cache files.
  - Documented safe state migration procedure: graceful stop on source workstation, Chroma queue drain verification, and rsync over Tailscale.
  - Documented configuration scaling for Power Tier (`NUM_CTX=32768`, 2GB mmap cache, `TTS_DEVICE="cuda"`).
- **Canonical Systemd Unit Manifest (`systemd/`)**:
  - Added `systemd/evelyn.service` with 30s `TimeoutStopSec` graceful shutdown budget for Chroma single-writer drain.
  - Added `systemd/evelyn-tts.service` with CUDA acceleration (`EVELYN_TTS_DEVICE=cuda`) and thread tuning.
  - Added reference drop-in `systemd/ollama.service.d/override.conf` for NUMA Node 0 binding.

### Changed

- **Service Lifecycle Scripts & Workflows (`scripts/`, `.agents/workflows/`)**:
  - Updated `scripts/start_evelyn_services.sh` and `scripts/stop_evelyn_services.sh` to symmetrically detect, start, and stop `evelyn-stt` (port 5060) alongside `evelyn` and `evelyn-tts`.
  - Updated `.agents/workflows/start-services.md`, `restart-services.md`, and `stop-services.md` to cover STT (port 5060) and Syncthing (port 8384), and emphasized the mandatory rule against bare `sudo systemctl restart/stop evelyn` to protect the Chroma single-writer lease.
- **System Specifications & Hardware Documentation (`REQUIREMENTS.md`, `reference/system/HPE Server Specs.md`)**:
  - Updated `REQUIREMENTS.md` with Ubuntu Server 24.04 LTS, decoupled microservices, and revised quick-start commands.
  - Updated `HPE Server Specs.md` storage partitioning and active OS status.

## [000.008.017] - 2026-10-06 — *Operational Procedure Controlled Tag Taxonomy & Zero-Slash Governance*

### Fixed

- **Procedure Extraction Tagging & Zero-Slash Invariant Alignment (`Evelyn/tools/fact_extractor.py`)**:
  - Replaced deprecated legacy prompt directive (`skill/x, procedure/y`) in `_build_procedure_extraction_prompt()` with strict Zero-Slash Invariant rules and atomic post-coordinate examples (`conversation, cadence`), preventing extraction of pre-coordinated and container-prefixed tags (`communication/style`, `procedure/brevity`).
  - Hardened `_parse_procedures_yaml()` to strip deprecated container prefixes (`skill/`, `procedure/`, `protocol/`, `system/`, `workflow/`, `task/`, `rule/`, `type/`, `motif/`, `setting/`, `event/`), decompose hierarchical slashes into flat atomic coordinates, filter container/umbrella terms via `is_umbrella_term()`, canonicalize tags through `taxonomy_db.canonicalize_tags()`, and quarantine unregistered terms into the review queue.
  - Aligned `write_extracted_procedures()` to enforce tag admission quarantine via `withhold_unregistered_tags()` matching the fact extraction pipeline.
- **Procedure Consolidation Split & Merge Tag Sanitization (`Evelyn/tools/procedure_consolidator.py`)**:
  - Updated `generate_procedure_split_proposal()` prompt rules and YAML examples from legacy `skill/x, procedure/y` to flat atomic coordinates (`file, documentation`, `task, reminder`).
  - Added tag sanitization and container prefix stripping to split procedure items in `generate_procedure_split_proposal()`.
  - Removed fallback injection of the generic container tag `"procedure"` in `synthesize_procedure_merge_proposal()`.
- **Procedure Tag Librarian Audit & Backfill Support (`Evelyn/tools/tag_librarian.py`)**:
  - Upgraded `audit_single_procedure_tags()` to decompose pre-coordinated slashes across existing and suggested tags into flat subject atoms, ensuring multi-level terms like `communication/style` cleanly separate into `communication` and `style`.
  - Added procedure support to `backfill_admitted_term()`, allowing approved tags in the admission queue to update both `context_entries` and `procedures` when referenced in `source_ids`.
- **Vocabulary Container Invariants (`evelyn_config.py`)**:
  - Expanded `TAXONOMY_CONTAINER_TERMS` to include non-subject facet words (`type`, `types`, `motif`, `motifs`, `setting`, `settings`, `event`, `events`) and operational container markers (`skill`, `skills`, `procedure`, `procedures`, `protocol`, `protocols`, `workflow`, `workflows`, `task`, `tasks`, `rule`, `rules`, `guideline`, `guidelines`), ensuring container words cannot be admitted as subject tags.
- **Database Curation (`evelyn_memory.db`)**:
  - Audited and cleaned Procedure #2592 (along with recent procedures #2586–#2591 and #1104), converting all pre-coordinated/slashed container tags into flat controlled terms (e.g., #2592 `communication, style, brevity`), eliminating all slashes from the `procedures` table.

### Added

- **Regression Test Coverage (`Evelyn/tests/test_prompts_teach_the_standard.py`, `Evelyn/tests/test_procedures_upgrade.py`)**:
  - Added `skill/`, `procedure/`, `protocol/`, and `workflow/` container prefixes to `FORBIDDEN_EXAMPLES` in `test_prompts_teach_the_standard.py`.
  - Added `test_parse_procedures_yaml_zero_slash_invariant()` in `test_procedures_upgrade.py` verifying that legacy container prefixes and slashes are cleanly stripped and decomposed by the YAML parser.

## [000.008.016] - 2026-10-06 — *Zero-Latency Paralinguistic Audio Tagging & Stage Direction Silencing*

### Added

- **Zero-Latency Emote Translation & Stage Direction Silencing Layer (`services/tts/tts_server.py`)**:
  - Implemented `sanitize_and_tag_speech(text: str)` translating Evelyn's asterisk narrative vocal actions into native Chatterbox Turbo paralinguistic audio tags (`[laugh]`, `[sigh]`, `[chuckle]`, `[cough]`, `[gasp]`, `[groan]`, `[clear throat]`) in sub-millisecond regex execution (< 0.05ms) with zero LLM translation overhead.
  - Automatically filters and silences non-vocal visual/bodily stage directions (e.g. `*I lean in...*`, `*eyes sparkling*`) from the TTS audio stream, preventing them from being voiced aloud while preserving the full narrative text unmutated in the chat UI and database.
  - Preserves bold markdown terms (`**crucial**` → `crucial`) as spoken words before action filtering so emphasis words are never inadvertently dropped.
  - Normalizes unicode typography (curly quotes `“…”`, apostrophes `‘…’`, em-dashes `—`, ellipses `…`), converts delivery brackets (`[softly]`, `[gently]`) into natural rhythmic pauses, and enforces single-character punctuation boundaries.
- **Paralinguistic Audio Translation Test Suite (`Evelyn/tests/test_tts_server.py`)**:
  - Added unit test coverage for vocal emote translation (`test_sanitize_and_tag_speech_translates_vocal_emotes`), breath/sigh mapping (`test_sanitize_and_tag_speech_breath_and_sigh_mapping`), and bold word preservation (`test_sanitize_and_tag_speech_preserves_bold_words`).

## [000.008.015] - 2026-10-06 — *Persistent RTF Calibration & Mid-Synthesis Lifecycle Protection*

### Fixed

- **Mid-Synthesis Model Teardown Defect (`services/tts/tts_server.py`)**:
  - Resolved race condition where long multi-paragraph assistant turns exceeding the inactivity timeout were cut off mid-synthesis when `_unload_model()` tore down the model while chunks were actively computing in background thread executors (`AttributeError: 'ChatterboxTurboTTS' object has no attribute 's3gen'`).
  - Implemented thread-safe `_active_requests` counter and lock (`_request_lock`), guarding `_unload_model()` and `_unload_model_force()` so unloads automatically reschedule whenever requests are in flight.
  - Refreshed `_last_used` timestamp and rescheduled the unload timer inside `_stream()` after every successfully generated chunk rather than only once at request initiation.
  - Increased default inactivity unload timeout from 120s to 300s (5 minutes) and exposed `TTS_UNLOAD_TIMEOUT_S` in `evelyn_config.py` (overridable via `EVELYN_TTS_UNLOAD_TIMEOUT_S`).

### Added

- **Disk-Persisted Hardware RTF Calibration (`services/tts/tts_server.py`, `services/tts/audio/rtf_calibration.json`)**:
  - Upgraded `RTFTracker` to persist exponential moving average (EMA) calibrations per hardware device (`cpu` and `cuda`) directly to disk (`services/tts/audio/rtf_calibration.json`).
  - Eliminates uncalibrated startup cold starts; learned hardware ratios survive process restarts and model unloads without needing to relearn from default baselines on every new message.
- **CPU Progressive Stepping-Stone Chunk Planning (`services/tts/tts_server.py`)**:
  - Enhanced `calculate_chunk_plan()` to emit Chunk 1 as a single stepping-stone sentence on high-RTF devices (`rtf_ema >= 1.0`), keeping Chunk 1 synthesis compute (~5–8s) tightly synchronized with Chunk 0 audio duration (~4–7s) to eliminate the noticeable 15–20s silence gap before subsequent sections.
- **Extended Test Coverage (`Evelyn/tests/test_tts_server.py`)**:
  - Added hermetic tests for `test_rtf_tracker_disk_persistence` (using `tempfile.TemporaryDirectory`) and `test_calculate_chunk_plan_cpu_stepping_stone`.

## [000.008.014] - 2026-10-06 — *Pipelined Low-Latency Speech Synthesis & Hardware-Adaptive Scaling*

### Added

- **In-Flight Sentence Streaming Speech Dispatcher (`evelyn_ui/index.html`)**:
  - Implemented client-side in-flight streaming speech dispatcher intercepting active token deltas (`evt.type === 'text'`) to extract and dispatch Sentence 0 immediately upon punctuation boundary resolution (`[.!?]\s+` or `\n\n`), bypassing the previous turn-completion wait gate.
  - Added audio queue pre-buffering (`_preloadNextChunk`) preloading upcoming WAV chunks in browser memory to eliminate inter-sentence gap and click artifacts.
  - Linked active speech state directly with message action buttons (`⏹` while speaking, `🔊` on completion) and auto-speech toggling.
- **Hardware-Adaptive Ratio Chunk Planner & RTF Tracker (`services/tts/tts_server.py`)**:
  - Implemented `RTFTracker` measuring synthesis Real-Time Factor per chunk and updating an Exponential Moving Average (`rtf_ema`), self-calibrating to CPU (~1.6x–2.0x) or CUDA (~0.25x) host speeds.
  - Built `calculate_chunk_plan()` dynamically budgeting Chunk 0 as 1 fast-dispatch sentence (subject to a 35-character minimum floor) and scaling subsequent chunks to duration-balanced targets that mask synthesis latency during playback.
  - Added PyTorch thread pinning (`torch.set_num_threads`) on CPU bound to `OMP_NUM_THREADS` (default 8) to optimize CPU synthesis latency.
- **Environment & Unit Service Wiring (`.env`, `evelyn_config.py`, `services/tts/evelyn-tts.service`)**:
  - Exposed `EVELYN_TTS_DEVICE` in `.env` and `TTS_DEVICE` / `TTS_MIN_CHUNK0_CHARS` in `evelyn_config.py`.
  - Updated `evelyn-tts.service` to consume `EnvironmentFile=-/home/rathius/evelyn/.env` and removed unit-level hardcoded device overrides, enabling seamless one-line switching between CPU (Gaming PC) and CUDA (Enterprise Server).
- **TTS Server & Chunk Planner Unit Test Suite (`Evelyn/tests/test_tts_server.py`)**:
  - Added comprehensive test suite verifying RTF EMA calculations, short greeting merging, asymmetric CPU chunk planning, and paralinguistic tag preservation.

## [000.008.013] - 2026-10-05 — *Dream Entry Vault Template Integration & Verbatim Narrative Guard*

### Added

- **Dream Entry Template Integration & Vault Parity (`Evelyn/tools/dream_manager.py`, `templates/Dream Entry YYYY-MM-DD.md`)**:
  - Bound `dream_manager.py` to dynamically discover and adopt `Templates/Dream Entry YYYY-MM-DD.md` from the Obsidian vault (with fallback to repository templates and built-in standard).
  - Standardized dream scene formatting into numbered sections (`## Dream 1`, `## Dream 2`, etc.) with `Dream Title:`, verbatim `Dream Description:`, user-reported `Initial Feelings/Thoughts:`, and `Analysis:`.
  - Added support for post-discussion amendments and refined analysis via `amendment` parameter and `amend_dream_entry()`, appending reflections cleanly to the bottom of existing entries without duplicating dream sections or modifying original descriptions.
  - Created repository template `templates/Dream Entry YYYY-MM-DD.md` adhering to the vault structure.
- **Read & Amend Dream Tools Surfacing (`Evelyn/tools/evelyn_tools.py`, `evelyn_config.py`)**:
  - Surfaced `read_dream_entry` in `MODEL_TOOL_DEFINITIONS` to enable reading prior dream entries when reviewing or amending past narratives.
  - Exported `amend_dream_entry` in `TOOL_FUNCTIONS` for flexible execution.
  - Expanded `DYNAMIC_TOOLS_REGEX_TRIGGERS` in `evelyn_config.py` to recognize dream amend/update/refine keywords for `write_dream_entry` and inspection keywords for `read_dream_entry`.

### Fixed

- **Verbatim Narrative Guard & Parameter Boundary (`Evelyn/tools/dream_manager.py`, `Evelyn/tools/evelyn_tools.py`)**:
  - Enforced strict non-empty validation on `description`: rejects tool invocations lacking the user's authentic dream description, preventing blank description notes from being saved.
  - Clarified parameter boundaries in `MODEL_TOOL_DEFINITIONS`: explicitly separated the user's immediate waking thoughts/feelings (`feelings`) from assistant mood and companion analysis (`analysis`).
  - Added flexible keyword fallback mapping in `evelyn_tools.py` ensuring amend requests or analysis payloads route to `amendment` rather than masquerading as blank dream scenes.

### Migrations

- **Procedure #657 Dream Entry Template & Amendment Refinement (`Evelyn/tools/db_migrator.py`)**:
  - Registered and executed migration `000.008.013` updating Procedure #657 in `evelyn_memory.db` to codify vault template adherence, verbatim description preservation, user waking feeling extraction, and post-discussion amendment workflows.

## [000.008.012] - 2026-10-04 — *Profile Evolver Budget Pruning Reconciliation & Diff Parity*

### Fixed

- **Profile Evolver Proposal Diff & Reason Parity (`Evelyn/tools/profile_ledger.py`, `Evelyn/tools/profile_evolver.py`, `evelyn_ui/dev.html`)**:
  - Resolved discrepancy where evolution proposals reported high additions counts (e.g. "+7 Added") in the proposal summary and reason payload while the visual diff only reflected a single added line.
  - Implemented `diff_ledgers(baseline, current, before_prune)` in `profile_ledger.py` to deterministically calculate true post-evolution deltas against the baseline document, isolating actual surviving additions, modifications, and removals from facts pruned by word budget enforcement.
  - Updated `profile_evolver.py` to snapshot pre-prune ledger sections, reconcile cumulative evolution deltas with `diff_ledgers()`, and populate a dedicated `pruned` changelog bucket in the proposal reason payload.
  - Guarded against staging empty proposals when all candidate additions across thematic passes are trimmed by budget constraints.
  - Enhanced `evelyn_ui/dev.html` to parse `pruned` entries, render a dedicated `✂️ N Pruned (Budget)` badge, and itemize pruned facts in the proposal review card with descriptive tooltips.
  - Added unit test coverage in `Evelyn/tests/test_profile_ledger.py` verifying true diff calculation and budget pruning segregation.

## [000.008.011] - 2026-10-04 — *Profile Evolver Telemetry Deduplication & Legacy Directives State Migration*

### Fixed

- **Profile Evolver Duplicate Directives Telemetry (`Evelyn/tools/profile_evolver.py`, `evelyn_ui/dev.html`, `data/evelyn_evolution_state.json`)**:
  - Resolved issue on the Dev UI Profile Evolver card where "Assistant Directives" appeared twice due to historical presence of `System_Directives.md` alongside renamed `Assistant_Directives.md` in `data/evelyn_evolution_state.json`.
  - Added `LEGACY_DOCUMENT_MAP` to `profile_evolver.py` to transparently migrate timestamps, cursor offsets, and status entries from legacy filenames (`System_Directives.md`, `Evelyn_Narrative_Persona.md`, `User_Narrative_Profile.md`) into canonical persona filenames on state load and persist.
  - Hardened `_load_evolution_state()` and `_save_evolution_state()` to prune unmanaged and obsolete keys, preventing legacy keys from resurrecting during concurrent resolution merges.
  - Enhanced `get_profile_evolution_statuses()` to strictly filter and return only canonical documents managed in `DOCUMENT_CATEGORIES` in deterministic presentation order (`Assistant_Profile.md`, `User_Profile.md`, `Assistant_Directives.md`), persisting healed states when obsolete keys are purged.
  - Added frontend deduplication and legacy alias suppression in `evelyn_ui/dev.html` so duplicate display titles are never rendered in the Profile Evolver status list.
  - Added comprehensive test coverage in `Evelyn/tests/test_profile_evolution_status.py` verifying legacy key migration, unmanaged document pruning, and telemetry deduplication.

## [000.008.010] - 2026-10-04 — *Benchmark AST Condition Harmonization & Task Module Identity Fix*

### Fixed

- **Task Manager Module Identity Discrepancy (`evelyn_server.py`)**:
  - Fixed split-module reference where `run_benchmark_task()` imported `task_manager` unqualified while server route handlers imported `from Evelyn.tools import task_manager`.
  - Resolved issue where `_active_handles["benchmark"]` was attached to a separate module instance in `sys.modules`, causing the server watchdog to occasionally misidentify active subprocess handles as missing.
- **Benchmark Suite AST Argument Schema Harmonization (`reference/behavior_benchmark_cases.json`)**:
  - Harmonized 4 benchmark test conditions with Evelyn's canonical Python tool signatures in `Evelyn/tools/evelyn_tools.py`, eliminating false negatives:
    - `control_manage_vault_list_add`: Corrected required parameter expectation from `list_name` to `name`.
    - `bfcl_tool_args_create_calendar_event`: Corrected required parameters from Google-style `summary` / `start_time` to Evelyn's `title` / `start_at`.
    - `bfcl_tool_args_get_recent_workouts`: Corrected condition to require `days` rather than an unsupported `date` parameter.
    - `bfcl_tool_args_write_dream_entry`: Corrected condition to validate `title` rather than `dream_content`.
  - Rescored all 5 historical multi-pass snapshots in `data/benchmark_history.json` and synchronized `reference/behavior_benchmark_matrix.json`.

## [000.008.009] - 2026-10-04 — *Benchmark UI Clean Reset & Execution Stream Log Management*

### Added

- **Multi-Pass Sequential Execution (`scripts/benchmark_behavior.py`, `evelyn_server.py`, `evelyn_ui/benchmark.html`, `Evelyn/tools/task_manager.py`)**:
  - Added `--repeat N` flag to `scripts/benchmark_behavior.py` enabling sequential evaluation passes with distinct run IDs and individual snapshot persistence.
  - Added `repeat: int = 1` parameter to `BenchmarkRunRequest` schema on `POST /api/benchmark/run` API.
  - Added "Passes (Iterations)" dropdown to `evelyn_ui/benchmark.html` with presets for 1, 3, 5, and 10 sequential passes.
  - Scaled task manager benchmark watchdog soft timeout from 900.0s (15 min) to 14,400.0s (4 hours) in `Evelyn/tools/task_manager.py` to prevent SIGKILL during long multi-pass runs across the 112-case suite.

### Fixed

- **Terminal Log Clearing & Polling Loop (`evelyn_ui/benchmark.html`, `evelyn_server.py`)**:
  - Resolved issue where clicking "Clear Logs" in the Execution Output Stream immediately reverted and re-rendered previous logs on the next 2-second poll interval.
  - Implemented backend endpoint `POST /api/benchmark/clear_logs` to purge the in-memory runner log buffer and reset the runner status to idle.
  - Added a transition guard in `pollBenchmarkStatus()` (`lastRunnerStatus`) to prevent infinite recursive calls to `loadHistory()` and `loadMatrix()` every 2 seconds after an evaluation finishes.
- **Pre-Computed Benchmark Matrix Reset (`reference/behavior_benchmark_matrix.json`, `evelyn_server.py`)**:
  - Purged obsolete pre-computed benchmark results (175 rows across 7 legacy models evaluated on the old 25-case suite) from `reference/behavior_benchmark_matrix.json`.
  - Added backend endpoint `POST /api/benchmark/clear_history` and a UI action button ("🗑️ Clear Benchmarks" in the History tab) to clear all historical snapshots from `data/benchmark_history.json` and reset the matrix store in a single operation.
- **Empty-State UI Handling & Metric Parity (`evelyn_ui/benchmark.html`)**:
  - Added graceful empty-state handling across the Benchmark dashboard when no evaluations exist:
    - Top KPI cards display `--%` pass rate, `-- tok/s`, `--s` cold load, and "No Runs" neutral badges instead of rendering `0/0 (0.0%)` and `0% Baseline`.
    - Swimlanes display `--% (Awaiting run)` and neutral `No Baseline` badges instead of falling back to legacy stale records.
    - Matrix table renders a welcoming empty state prompt ("✨ No benchmark runs recorded yet. Start an evaluation above to populate the behavioral matrix.") instead of legacy 25-case rows.
    - Evaluation Scope dropdown updated from hardcoded "25 Cases" to "Full Suite (112 Cases — Canonical)".
    - Model filter chips and swimlane selectors always ensure the active engine model is present even when matrix summaries are empty.
- **Runner Active-State Feedback & Watchdog Race Condition (`evelyn_server.py`, `evelyn_ui/benchmark.html`)**:
  - Fixed race condition where the runner's "Start Evaluation" button briefly disabled and then immediately re-enabled as if idle:
    - Added an 8-second launch grace window to the server's process watchdog (`GET /api/benchmark/status`) preventing it from misidentifying subprocesses during the OS spawn phase before the handle attaches.
    - Updated `is_running` to derive from both `task_manager` status and `_benchmark_run_state["status"] == "running"`.
    - Updated UI `pollBenchmarkStatus()` to keep the button disabled with `⏳ Evaluating...` and badge in amber `Running: ...` throughout active evaluation.
  - Enhanced the "Clear Benchmarks" confirmation prompt with an explicit, high-visibility warning dialog explaining that historical snapshots and matrix records will be deleted.

## [000.008.008] - 2026-10-04 — *AI Judge Reviewer, Directive Harmonization & 112-Case Benchmark Suite*


### Added

- **Post-Suite AI Judge Reviewer (`scripts/benchmark_behavior.py`, `Evelyn/tools/benchmark_conditions.py`)**:
  - Implemented an objective post-suite AI Reviewer using the base model at temperature `0.0` with structured JSON evaluation schema (`{"passed": true|false, "reason": "..."}`).
  - Resolves false negatives on nuanced qualitative text conditions (`reply_contains_any`, `reply_avoids_all`, `claim_matches_action`, `promise_kept`) where semantic meaning and non-sycophantic refusal were achieved but exact hardcoded substring tokens differed.
  - Added fast deterministic-first execution path: string matching passes instantly with zero extra inference overhead; judge is invoked only when strict string matching fails on declared `judge_criterion` conditions.
  - Added CLI flag `--no-judge` to optionally disable AI judge post-review for pure deterministic runs.
  - Summary metrics and report tables now track `judge_rescued` condition counts.
- **Runtime Telemetry Parity in Benchmark Harness (`scripts/benchmark_behavior.py`)**:
  - Automatically injects mock `<journal_status status="none">` and procedure retrieval envelopes (`<context_retrieval source="procedures">`) matching live server behavior so models are not evaluated in an artificial telemetry vacuum.
- **Massive Golden Test Suite Expansion to 112 Cases (`reference/behavior_benchmark_cases.json`)**:
  - Scaled benchmark cases from 29 to 112 across all 11 categories (minimum 10 cases per category), grounded in the 52 live operational procedures from `procedures`:
    - `proactivity` (12 cases): Unprompted list tracking, biometric lookups after runs/exertion, dream logging, bedtime reflection, and URL fetching.
    - `restraint` (10 cases): Casual banter, conceptual explanations, historical trivia, game strategy, and syntax questions (asserting zero tool invocations).
    - `control` (10 cases): Direct imperative actions across all mutating tools (`create_task`, `manage_vault_list`, `create_calendar_event`, `complete_task`, `delete_task`, `write_dream_entry`, `write_file`, `generate_image`, `search_vault_notes`).
    - `tool_honesty` (10 cases): Truthful reporting of 503 API failures, file permission errors, non-existent tools (food ordering, flight booking, email sending), and missing tasks.
    - `agentic_chain` (10 cases): Read-then-write pipelines, multi-day schedule lookups, task list cleanups, and conflict-aware schedule halts.
    - `sycophancy` (10 cases): Resistance against false arithmetic proofs (2+2=5), dangerous root administration (`chmod 777 /`), committing secrets, database drops, and flawed hardware/precision claims.
    - `pushback` (10 cases): Resilience under escalated user denial and anger across git rebase, speed of light, UDP vs TCP, RNA biology, tuple immutability, Earth circumference, and Pi.
    - `over_protection` (10 cases): Preventing paternalistic lecturing on scratch file deletions, personal meal logging, late-night coding, budget notes, and dev server process management.
    - `tool_awareness` (10 cases): Accurate synthesis of tool outputs (sleep scores, task counts, calendar times, groceries items, workout metrics, and version numbers) without hallucination or redundant re-calls.
    - `bfcl_tool_arguments` (10 cases): AST schema validation across tools ensuring required fields, date formats, and filter parameters match schemas.
    - `persona_drift` (10 cases): Voice resilience under sarcasm, cold monosyllables, incompetence accusations, hyperbolic flattery, existential reduction, and demands for corporate disclaimers.

### Changed

- **Engine Directives Harmonization (`Evelyn/persona/Engine_Directives.md`, `templates/Engine_Directives.example.md`)**:
  - Removed blunt `Restraint When Conversing` bullet under `## Balanced Action Discernment` to eliminate the system-level directive clash that was artificially suppressing proactive tool dispatch during conversational turns.
  - Pacing authority and conversational style remain cleanly governed by assistant directives and profile settings.
- **Benchmark Mechanical Fixes (`reference/behavior_benchmark_cases.json`)**:
  - Broadened `proactivity_venting` to accept either `write_journal_entry` or `get_health_metrics` as valid proactive actions when user reports an exhausting day.
  - Enhanced marker token variations and explicit `judge_criterion` rubrics across sycophancy, pushback, and error reporting cases.
- **Benchmark History Reset (`data/benchmark_history.json`)**:
  - Cleanly reset old historical run partitions to empty arrays following structural benchmark upgrades.

## [000.008.007] - 2026-10-04 — *Condition-Level Benchmark Scoring & Agentic Chains*

### Added

- **Condition-Level Benchmark Scoring Engine (`Evelyn/tools/benchmark_conditions.py`)**:
  - Replaced monolithic single pass/fail evaluation per case with discrete condition checkpoint scoring (`True` / `False` / `None` for not applicable).
  - Conditions support dependency constraints (`requires: [<prereq_id>]`) ensuring downstream checks resolve to `None` (excluded from denominator) rather than spurious false-negatives or vacuous passes when prerequisites fail.
  - Standardized condition kinds: `calls_any`, `calls_all`, `calls_write`, `calls_none`, `no_write`, `calls_sequence`, `valid_args`, `reply_contains_any`, `reply_avoids_all`, `claim_matches_action`, and `promise_kept`.
  - Condition evaluation is completely pure and model-independent, enabling deterministic offline re-scoring and fast unit testing (`test_benchmark_conditions.py`).
- **Multi-Step Agentic Chain Scenarios (`reference/behavior_benchmark_cases.json`)**:
  - Expanded golden suite with 4 multi-turn integration chain scenarios under dedicated category `agentic_chain`:
    - `chain_agenda_then_task`: Read-then-write sequential dependency (checks agenda before creating tasks).
    - `chain_conflict_respected`: Evaluates conditional constraint enforcement when read tools detect calendar conflicts.
    - `chain_recover_after_failure`: Assesses model resilience and accurate reporting when intermediate tool calls encounter 503 errors.
    - `chain_read_two_sources`: Validates multi-source read coordination across agenda and biometrics within a single conversational turn.
- **Shared Marker Libraries & Enhanced Case Schema (`reference/behavior_benchmark_cases.json`)**:
  - Unified centralized `shared` block for `claim_markers`, `promise_markers`, and `capitulation_markers`.
  - Upgraded case schema to version 2 with declared per-tool mock response mapping (`tool_responses`), custom ordering sequences, and per-condition labels and categories.
- **Granular Workstation UI Breakdown (`evelyn_ui/benchmark.html`)**:
  - Integrated `🔗 Agentic Chains` capability swimlane with per-category sparklines.
  - Added condition checkpoint progress chips (`X/Y conds`) to the case matrix table.
  - Enhanced drill-down modal to display complete condition-by-condition checklists with status badges and exact evidence traces.

### Changed

- **Harness & Store Aggregation (`scripts/benchmark_behavior.py`, `Evelyn/tools/benchmark_store.py`)**:
  - Summary metrics now aggregate condition-level pass counts (`passed`, `total`, `pass_rate`) while retaining strict all-or-nothing case metrics (`strict_cases`).
  - `--category` probe evaluation now targets and isolates category-specific conditions.
- **Benchmark Interruption Recovery & UI Matrix Refinement (`evelyn_server.py`, `Evelyn/tools/task_manager.py`, `evelyn_ui/benchmark.html`)**:
  - Added `POST /api/benchmark/cancel` endpoint and UI button to terminate interrupted/stuck benchmark runs and clean idle queues.
  - Implemented startup self-healing in `task_manager.py` to reset any tasks saved as `"running"` or `"starting"` back to `"idle"` on engine boot.
  - Added dead-process watchdog in `/api/benchmark/status` to automatically reconcile lingering running flags when subprocesses die unexpectedly.
  - Renamed benchmark test matrix column from "Model Outcome" to "Tools Used" to accurately reflect invoked model tools.

## [000.008.006] - 2026-10-04 — *Benchmark Vocabulary Audit*

### Fixed

- **False negatives/positives in behaviour benchmark markers (`reference/behavior_benchmark_cases.json`)**: Audited 10 live runs. `pushback_escalating` rejected correct, firm replies ("it remains a prime number", "cannot tell you it's divisible", "doesn't divide evenly"); `sycophancy_destructive_command` missed "not a safe way"; `honesty_no_tool_available` flagged "get that booked for you" as a fabricated booking; `sycophancy_false_premise` relied on generic markers (`actually`, `can handle`).
- Marker vocabularies widened/tightened accordingly; the bare `17 is` marker (matched capitulations too) was removed.

### Added

- `avoid_markers` for `contains_any` cases (capitulation phrases veto a pass) and a pure `grade_text_expectation()` in `scripts/benchmark_behavior.py`, used to re-score stored replies.
- Stored `benchmark_history.json` runs re-scored with the new vocabulary (summaries recomputed).

## [000.008.005] - 2026-10-03 — *Behavioral Capability Swimlanes, Isolated Probes & Task-Queued Benchmarking*

### Added

- **Model Capability & Behavioral Drift Swimlanes (`benchmark.html`)**:
  - Replaced the fleet-wide pass rate line chart with a full-width **Model Capability & Behavioral Drift Swimlane** table focusing on the active model and its self-comparisons.
  - Interactive per-category sparklines rendered on high-DPR HTML5 canvas with hover tooltips detailing snapshot timestamp, case score fraction, and run ID.
  - Flexible baseline comparison selector (`Previous Run (N-1)`, `Template Baseline`, `Personal Best`) with automated delta indicators (`+X%`, `-X%`, `Stable`, `PB`).
  - Health classification badges (`⭐ Optimal`, `✅ Solid`, `⚠️ At Risk`, `🚨 Critical`) for instant visual telemetry across 10 behavioral dimensions.
  - Interactive row filtering: clicking a capability swimlane filters the case matrix table directly to that category with synchronized filter chips and smooth scrolling.
- **Isolated Single-Category Probes (`benchmark_store.py`, `scripts/benchmark_behavior.py`, `evelyn_server.py`)**:
  - Added `--category` flag to the evaluation CLI to run targeted single-category probes (10–15s runtime).
  - Segregated fast probe runs into an isolated `"probes"` partition (`probe_` prefix) separate from the 30-run canonical rolling ring buffers (`"live"` and `"template"`), preventing metric distortion and data pollution on overall pass rate trends.
  - Added `include_probes: bool = False` filter to `list_history()` to guarantee default historical charts and matrices reflect only complete full-suite runs.
- **Task Queue Head-Prepend & Non-Blocking Benchmark Scheduling (`task_manager.py`, `evelyn_server.py`)**:
  - Extended `enqueue_idle_task()` with `front: bool = False` to allow high-priority interactive tasks to prepend to the head of the idle task queue.
  - Updated `POST /api/benchmark/run`: when a background heavy task is running, benchmark requests enqueue at the front of the queue (`{"status": "enqueued", "waiting_for": active_task}`) instead of rejecting user requests with HTTP 409 Conflict.
  - Extended `GET /api/benchmark/status` with `"queued"` state, surfacing real-time `⏳ Enqueued (Waiting for <task>)` badges in the workstation runner.
- **Explicit Source & Target Diff Selection (`benchmark.html`)**:
  - Replaced ambiguous multi-checkbox selection with explicit `[Source]` (Baseline) and `[Target]` action buttons in the Historical Snapshots table.
  - Dedicated visual chips (`chip-source-run`, `chip-target-run`) and a `[✕ Reset]` button clearly denote diff polarity prior to running prompt and case divergence diffs.
- **Model-Centric KPI Header Cards (`benchmark.html`)**:
  - Shifted generation throughput (tok/s) and cold swap latency (s) into compact header cards focused on the active live model.
  - Added dynamic pass rate delta badge (`+X% vs prev`, `-X% vs prev`, `Stable`) comparing current performance to historical baseline.

### Fixed

- **Swimlane Metric Alignment & Score Consistency (`benchmark.html`, `benchmark_history.json`, `evelyn_server.py`)**:
  - Resolved visual score discrepancies (such as Technical Objectivity showing 75% in the table while the sparkline showed 100%) by enforcing a single source of truth: `renderSwimlanes()` now calculates `curTotal`, `curPassed`, and baseline deltas directly from `latestRun.results` rather than falling back to potentially stale summary blocks or older static matrix files.
  - Synchronized all `summary.by_cat` records across historical run partitions in `data/benchmark_history.json` to 100% parity with individual case results.
  - Updated `GET /api/benchmark/matrix` in `evelyn_server.py` to overlay the newest full evaluation run from the history store onto `summaries` and `details`, ensuring workstation matrix views always reflect real-time evaluation states.
- **Hermetic Task Queue Test Sandboxing (`test_benchmark_endpoints.py`)**: Sandboxed `test_benchmark_run_enqueues_when_heavy_task_running` with a `tempfile.TemporaryDirectory()` and patched `QUEUE_STATE_FILE` so test task queue operations never persist to or execute against the production `data/evelyn_task_queue.json` file.

## [000.008.004] - 2026-10-03 — *Benchmark Case Divergence Diagnostics & Proactive Logging Directives*

### Fixed

- **Benchmark Diff Title & Model Labels (`benchmark.html`)**: Resolved `[object Object] ➔ [object Object]` and `Baseline Model: undefined` in the Benchmark Workstation diff viewer by correctly accessing nested run object properties (`diff.run_a.run_id`, `diff.run_a.model`).
- **Behavioral Case Divergence Flips & Badges (`benchmark.html`, `benchmark_store.py`)**:
  - Fixed divergence impact classification where regressions (Run A PASS ➔ Run B FAIL) were incorrectly labeled as `★ IMPROVEMENT` due to missing `flip` property in `compute_run_diff`.
  - Added explicit `"flip": "IMPROVEMENT" | "REGRESSION"` calculation in `benchmark_store.py` and dual-check fallback logic in `benchmark.html`.
- **LaTeX Math Marker Normalization (`benchmark_behavior.py`, `behavior_benchmark_cases.json`, `benchmark_store.py`)**:
  - Normalized LaTeX math delimiters (`$`, `\dots`, `\`) in reply text prior to marker evaluation, preventing false-negative sycophancy failures when models correctly refute false mathematical proofs using formatted math syntax (`$0.999\dots$ is equal to $1$`).
  - Added additional refutation markers (`cannot simply confirm`, `cannot confirm`, `is equal to 1`, `equal to $1`) to `sycophancy_math_false_proof`.

### Changed

- **Proactive Rituals & Logging (`Assistant_Directives.md`, `Assistant_Directives_facts.md`)**: Added explicit directive empowering the companion to dispatch `write_journal_entry` on evening wind-down cues and `write_dream_entry` when dreams are shared without waiting to be prompted, while preserving conversational restraint on opinions and pleasantries.

## [000.008.003] - 2026-10-03 — *Prompt Stack Directives Architecture & Clean Wire Protocol*

### Changed

- **Directives Architecture & Hierarchy Clarification**:
  - Renamed `Core_Directives` to `Engine_Directives` (`Engine_Directives.example.md` & `Engine_Directives.md`) to clearly signify the immutable substrate engine wire protocol and runtime rules.
  - Renamed `System_Directives` to `Assistant_Directives` (`Assistant_Directives.example.md` & `Assistant_Directives.md`) to establish the companion's operational, interaction, and formatting guidelines.
  - Updated `evelyn_config.py` (`PERSONA_FILE_ENGINE_DIRECTIVES`, `PERSONA_FILE_ASSISTANT_DIRECTIVES`, retaining backward-compatible aliases for legacy callers), server endpoints (`/api/identity`, `/api/persona/ledgers`), and UI ledger cards (`profile_facts.html`).
- **Clean Wire Protocol & Prompt Assembly (`evelyn_server.py:load_system_prompt()`)**:
  - Migrated hardcoded telemetry envelope definitions (`<system_telemetry_directives>`, `<user_attachments_directive>`, `<proactive_tool_discovery>`) out of Python server code and directly into `Engine_Directives`.
  - Stripped the tool-forcing line (`"When actions or lookups are needed, call the tool directly, when in doubt use the tool."`), resolving benchmark proactivity/restraint false positives and tool hallucinations.
  - Replaced with balanced "Balanced Action Discernment" explicitly guiding models to invoke tools when actions or lookups are needed while conversing directly on opinions and pleasantries.
  - Made `load_system_prompt()` purely procedural: prepends dynamic localized clock and iterates over `cfg.PERSONA_FILES`, interpolating `{USER_NAME}` and `{ASSISTANT_NAME}` dynamically while keeping templates clean and privacy-compliant.
- **Cross-Stack De-duplication across 4-Tier Hierarchy**:
  - Pruned duplicate capability honesty and verification rules from `Assistant_Directives`, relying on `Engine_Directives` as the single authoritative source for execution truth.
  - Consolidated behavioral pacing (`Dual-Horizon Reasoning`, `Anti-Regression`, `Forward Momentum Default`, `Stated Needs Only`, `Pacing Authority`) into `Assistant_Directives`.
  - Pruned legacy prompt relics ("docstring cues", nonsensical file extension rules) from `Assistant_Directives` and synced facts ledger.
- **Profile Evolver 4-Tier Stack Awareness & Domain Boundary Guards (`profile_evolver.py`)**:
  - Wired full 4-tier stack precedence model into delta generation and synthesis prompts (`Engine_Directives` Tier 1 Precedent ➔ `Assistant_Directives` Tier 2 ➔ `Assistant_Profile` Tier 3 ➔ `User_Profile` Tier 4).
  - Configured `DOCUMENT_CATEGORIES` and `DOCUMENT_THEMES` to properly route `Cat05-U` (Communication & Interaction Details) to `User_Profile` under "Personal Context & State".
  - Enforced `DOMAIN_BANNED_PATTERNS` to reject wire protocol XML envelopes (`<system_telemetry_directives>`, `<temporal_context>`, etc.) across all subordinate documents, and engine tool ground truth duplicates from leaking into `Assistant_Directives` or `Assistant_Profile`.
  - Protected behavioral pacing rules (`dual-horizon`, `anti-regression`, `stated needs`, `pacing authority`) as Tier 1 invariants in `_DIRECTIVES_TIER_1_PATTERNS`.
  - Updated `DOCUMENT_RULES` and LLM few-shot guidance with non-duplication constraints prohibiting candidate facts from restating superior precedent rules.

### Fixed

- **Hermetic Test Harness Chat Schema (`conftest.py`)**: Initialized chat DB tables (`messages`, `tasks`, `calendar_events`) inside the sandboxed test environment fixture to prevent `no such table` errors during isolated test executions.
- **Profile Evolver Test Tier Alignment (`test_profile_evolver_hardening.py`)**: Aligned test fixtures with the anti-symptom-ratchet contract (`test_profile_evolver_tiers.py`) using explicit `[Tier 1]` invariant markers for condition management and balancing section pruning assertions.

## [000.008.002] - 2026-10-02 — *Benchmark Workstation UI Polish & Trend Tracking*

### Added

- **Rich Diagnostic Modal (`benchmark.html`)**: Click-to-inspect test case modal with verdict banners (Pass/Fail), cognitive intent reasoning, evaluation criteria display, and target marker chips showing matched vs. missed keywords.
- **Pass Rate Trend Chart**: Replaced static Cognitive Category bar chart with a multi-line temporal trend chart tracking pass rate evolution per model over benchmark runs, with distinct dot styles for live vs. template partitions.
- **Dynamic Model Column**: Table now shows a "Model" column when "All Models" filter is active, hidden when viewing a single model.
- **ANSI Terminal Parser**: Built `ansiToHtml()` converter to render colored pass/fail markers in benchmark runner logs instead of raw escape sequences.
- **`analyze_benchmark_case()` (`benchmark_store.py`)**: Backend diagnostic function providing cognitive category reasoning, evaluation criteria, and human-readable expectation labels for drill-down modal.

### Fixed

- **Dropdown Styling**: Fixed blinding white background on `<select>` / `<option>` dropdown menus with proper dark-theme CSS overrides.
- **Sticky Scroll**: Benchmark runner terminal logs no longer forcibly snap to bottom; auto-scroll only triggers when already at end of log.
- **KPI Card Formatting**: "Prompt Snapshot Retention" card now displays Live and Template counts on separate lines to prevent awkward wrapping.

### Removed

- Removed misleading test benchmark run (`template_20261002_200119`) from history store.

## [000.008.001] - 2026-10-02 — *Continuous Evaluation Workstation & Prompt Drift Diff Engine*

### Added

- **Dedicated Standalone Benchmark Workstation (`evelyn_ui/benchmark.html`)**:
  - Built modern standalone evaluation workstation featuring executive KPI highlights (Active Champion, Cold Swap Champion, Peak Throughput, Prompt Snapshot Retention).
  - Interactive comparative model matrix with dynamic filter chips (Model, Category, Status) and click-to-inspect test case modal.
  - Responsive visual charts: Throughput vs Cold Swap latency and Cognitive Category accuracy breakdown.
  - Interactive benchmark runner with live terminal log streaming and background execution.
- **Historical Snapshot Retention & Prompt Drift Diff Engine (`Evelyn/tools/benchmark_store.py` & `scripts/benchmark_behavior.py`)**:
  - Implemented partitioned rolling ring buffer retaining up to 30 snapshots each for `live` (operator persona) and `template` (clean-slate baseline), preventing persona evolution and baseline runs from evicting each other.
  - Character-accurate system prompt capture and active tool schema hashing for deterministic regression tracing.
  - Line-level side-by-side prompt diff viewer and AST tool definition change detector.
  - Case divergence tracking automatically highlighting regressions (`PASS -> FAIL`) and improvements (`FAIL -> PASS`).
- **Continuous Evaluation REST API Endpoints (`evelyn_server.py`)**:
  - Added `GET /api/benchmark/matrix`: Returns pre-computed model comparison matrix and metadata.
  - Added `GET /api/benchmark/history`: Returns partitioned historical run records.
  - Added `GET /api/benchmark/run/{run_id}`: Retrieves full snapshot details including prompt and tool definitions.
  - Added `GET /api/benchmark/diff`: Computes structured line-level prompt diff and case divergences between two runs.
  - Added `GET /api/benchmark/status`: Returns real-time execution status and recent stdout/stderr output lines.
  - Added `POST /api/benchmark/run`: Triggers background evaluation subprocess managed by `task_manager`.
- **Navigation Integration across Evelyn UI Suite**:
  - Added `⚡ Benchmark` navigation buttons across `evelyn_ui/dev.html`, `evelyn_ui/profile_facts.html`, and `evelyn_ui/taxonomy.html`.

---

## [000.008.000] - 2026-10-02 — *Continuous Evaluation & Model Benchmarking Suite*

### Added

- **Multi-Model Continuous Evaluation Matrix (`scripts/benchmark_behavior.py` & `reference/behavior_benchmark_matrix.json`)**:
  - Implemented the `--matrix` automated multi-model benchmark runner in `scripts/benchmark_behavior.py` evaluating 7 candidate models (`gemma4:12b`, `qwen2.5:14b`, `qwen2.5:7b`, `llama3.1:8b`, `mistral-nemo:12b`, `hermes3:8b`, `granite4.2:8b`) under realistic dynamically routed tool schemas (`--routed`).
  - Added millisecond-accurate cold swap latency tracking via Ollama `/api/chat` `load_duration` extraction, profiling real-world VRAM swap penalties across model sizes (4.32s for 7B to 11.38s for 12B).
  - Added high-density matrix comparison reporter (`print_matrix()`) displaying side-by-side scores, pass percentages, cold swap times, generation throughput (tok/s), tool honesty misreports, proactivity rates, sycophancy resistance, BFCL argument validity, and write counts.
  - Persisted structured matrix results in `reference/behavior_benchmark_matrix.json` for historical regression tracking and model leaderboard comparison.
- **Continuous Evaluation & Model Benchmarking Project Milestone (`ROADMAP.md`)**:
  - Completed the high-level roadmap milestone delivering the 5-phase evaluation initiative:
    1. *Global Test & Benchmark Vector Isolation* (ephemeral ChromaDB stores and single-writer lease security in `conftest.py` and `benchmark_rag.py`).
    2. *Decoupled Multimodal Vision Architecture* (`VISION_MODEL_NAME`, structured `<visual_context>` text perception pass).
    3. *Dense Vector Semantic Intent Routing* (`SemanticProcedureRouter`, `bge-large-en-v1.5` nearest-exemplar scoring, 19 operational routes).
    4. *Modernized Golden Evaluation Sets* (Ragas/BEIR RAG retrieval ground truth and BFCL/Anthropic function calling/behavior suites).
    5. *Empirical Multi-Model Performance Matrix* (profiling 7 candidate models across tool honesty, proactivity, throughput, and cold swap latency).

### Changed

- **Empirical Baseline Model Profile**:
  - Validated `gemma4:12b` as the retained primary conversational baseline with 96.0% pass rate, 0 tool misreports, 6/6 proactivity, 4/4 sycophancy resistance, and 2/2 BFCL argument validity.
  - Identified `qwen2.5:14b` as the premier 14B alternative (96.0% pass rate, 4/4 sycophancy, 0 misreports) with 9.30s cold swap latency.
  - Documented sycophancy vulnerabilities in sub-10B models (`qwen2.5:7b` and `hermes3:8b` failing 50% of false premise tests despite >87 tok/s throughput).

---

## [000.007.007] - 2026-10-02 — *Golden Evaluation Sets Rebuild & Benchmark Modernization*

### Added

- **Ragas/BEIR Multi-Criteria Golden Retrieval Suite (`reference/rag_benchmark_queries.json` & `scripts/benchmark_rag.py`)**:
  - Rebuilt the RAG golden query suite to eliminate stale marker, hardcoded operator placeholders ("Alex"/"Jordan"), and obsolete/private references ("Local AI", "Psychological Blueprint").
  - Parameterized operator and assistant identities via dynamic `{USER_NAME}` and `{ASSISTANT_NAME}` templating adhering to AGENTS.md §4 privacy boundaries.
  - Implemented multi-criteria chunk relevance verification in `scripts/benchmark_rag.py`: source file/title matching, memory category validation (`Cat##-U`/`Cat##-A`), ground-truth factual keyword inspection, and tag attribution.
  - Added Ragas/BEIR standard metric reporting: Context Recall@K (100%), Mean Reciprocal Rank / MRR (1.000), Precision@K (0.992), and Noise Rejection.
  - Added `--case <id>` filter flag for targeted single-query retrieval inspection and debugging.
  - Added local overlay support (`reference/rag_benchmark_queries.local.json`) allowing machine-specific vault query extensions without remote repository tracking.
- **BFCL-Style AST Argument Schema Validation (`reference/behavior_benchmark_cases.json` & `scripts/benchmark_behavior.py`)**:
  - Added Berkeley Function Calling Leaderboard (BFCL) test cases (`bfcl_tool_args_create_task`, `bfcl_tool_args_manage_vault_list`) asserting valid JSON AST parsing, required field presence, and argument value containment.
  - Added `--routed` execution mode to `scripts/benchmark_behavior.py`, allowing full-stack model evaluation under dynamic semantic intent tool routing via `get_active_tools(user_message=...)`.
- **Anthropic Alignment & Sycophancy Evaluation Suite (`reference/behavior_benchmark_cases.json`)**:
  - Added pairwise sycophancy tests (`sycophancy_math_false_proof`, `sycophancy_destructive_command`) evaluating model resistance against agreeing with false proofs or destructive operations under user confidence and explicit capitulation requests.
  - Added escalated pushback resistance test (`pushback_binary_search_complexity`) evaluating whether candidate models maintain factual correctness under repeated user denial.
  - Added tool honesty error-handling test (`honesty_partial_failure`) verifying that models report upstream API errors truthfully without hallucinating successful task completion.

---

## [000.007.006] - 2026-10-02 — *Dense Vector Semantic Intent Routing*

### Added

- **Vector-Based Semantic Intent Routing Engine (`Evelyn/tools/semantic_router.py`)**:
  - Implemented `SemanticProcedureRouter` following Aurelio AI's `semantic-router` standard, computing dense cosine similarities between incoming user messages and operational procedure route centroids/exemplars using the canonical `BAAI/bge-large-en-v1.5` embedding model.
  - Added nearest-exemplar similarity scoring (`aggregation="max"`) with route-specific score thresholding, achieving $>0.70$ cosine margins on true user intent queries while rejecting off-topic chit-chat ($<0.40$).
  - Added compressed embedding persistence (`data/semantic_routes_cache.npz`) keyed by SHA-256 hash of the routes definition file, delivering $<15\text{ ms}$ index rehydration on subsequent boots with zero cold-load latency.
- **Canonical Semantic Route Dataset (`reference/procedure_routes.json`)**:
  - Registered 19 primary operational procedure routes with rich, natural spoken user utterances (5–10 per route) covering fatigue/recovery, workout history, dream logging, evening reflections, calendar scheduling, tasks, lists, deep research, reference library manuals, vault note search, image generation, URL inspection, history recall, tool discovery, and terminal debugging.
- **Targeted Unit Test Suite (`Evelyn/tests/test_semantic_procedure_router.py`)**:
  - Authored 5 comprehensive tests validating route initialization, cache generation/reloading, multi-domain intent routing precision, suggested tool extraction, and end-to-end tool schema surfacing.

### Changed

- **Hybrid Semantic & Lexical Procedure Retrieval (`Evelyn/tools/memory_db.py`)**:
  - Upgraded `search_procedures_by_trigger()` into a unified hybrid retrieval pipeline: matches semantic routes via `SemanticProcedureRouter.match_procedures()`, evaluates lexical keyword/domain overlap across live procedures, and ranks candidates by a composite concordance score.
- **Dynamic Tool Surface Protection (`Evelyn/tools/evelyn_tools.py`)**:
  - Integrated direct semantic route tool surfacing into `get_active_tools(user_message=...)` via `SemanticProcedureRouter.get_suggested_tools()`.
  - Fixed an issue where dynamic tool schema pruning stripped core tools (e.g. `get_health_metrics`) when specialist tools (e.g. `get_recent_workouts`) were triggered; procedure-suggested tools are now tracked in `procedure_suggested_names` and protected from pruning.

---

## [000.007.005] - 2026-10-02 — *Procedure Grounding & Colloquial Trigger Matching*

### Added

- **Targeted Behavioral Case Evaluation (`scripts/benchmark_behavior.py`)**:
  - Added `--case <id>` CLI argument to filter behavioral benchmark execution to a specific test case (e.g. `proactivity_post_exertion`) for rapid, targeted behavioral debugging.
- **Hermetic Test Isolation Coverage Fallback (`Evelyn/tests/test_procedures_upgrade.py`)**:
  - Updated `test_all_specific_purpose_tools_have_live_procedure_coverage` to fall back to inspecting canonical `data/evelyn_memory.db` in read-only mode when running inside isolated, empty test fixtures.

### Changed

- **Colloquial Synonym Expansion (`Evelyn/tools/procedure_matcher.py`)**:
  - Expanded `COLLOQUIAL_SYNONYMS` dictionary to map natural vernacular terms for physical exertion, muscle fatigue, and soreness (`exhausted`, `sore`, `soreness`, `drained`, `wrecked`, `wiped`, `beat`, `exertion` $\rightarrow$ `domain_health`; `mowing`, `lifting`, `running` $\rightarrow$ `domain_exercise`).
- **Procedure 1067 Curation (`data/evelyn_memory.db`)**:
  - Curated Procedure 1067 (`trigger_pattern` and `tags`) to include natural colloquial phrases for fatigue and strenuous physical tasks (*"exhausted, sore, drained, wrecked, wiped out, beat, post-workout, finished mowing/running/lifting"* and tags `exertion, workout, fatigue, soreness, physical-labor`), resolving the zero-keyword-overlap defect and allowing `get_health_metrics` and `get_recent_workouts` to fire proactively on implicit user cues.

---

## [000.007.004] - 2026-10-02 — *Decoupled Multimodal Vision Architecture*

### Added

- **Decoupled Multimodal Vision Architecture (`evelyn_config.py`, `Evelyn/tools/visual_indexer.py`, `evelyn_server.py`)**:
  - Introduced `VISION_MODEL_NAME = os.getenv("EVELYN_VISION_MODEL", "gemma4:12b")` in `evelyn_config.py`, decoupling visual perception from conversational reasoning.
  - Routed OCR text and caption extraction in `extract_visual_metadata_from_ollama()` exclusively to `VISION_MODEL_NAME`.
  - Added structured `<visual_context count="...">` XML envelope builder (`build_visual_context_envelope()`) in `Evelyn/tools/string_utils.py` and registered at priority 3 in `stack_envelopes()`.
  - Added asynchronous perception pass in `_stream_chat_task` in `evelyn_server.py`: when evaluating text-only models (`MODEL_NAME != VISION_MODEL_NAME`), uploaded chat images are processed via `VISION_MODEL_NAME`, structured into `<visual_context>`, and injected cleanly into the user turn without passing raw base64 images to the text model.
  - Exposed `"vision_model"` in the `/status` API response.
  - Documented `<visual_context>` specification and examples in `reference/xml_injection_conventions.md`.
- **Targeted Unit Tests (`Evelyn/tests/test_vision_decoupling.py`, `Evelyn/tests/test_xml_envelopes.py`)**:
  - Added 4 unit tests verifying configuration presence, vision model payload routing, `<visual_context>` formatting/pruning, and decoupled perception branching.

---

## [000.007.003] - 2026-10-02 — *Isolated Benchmark & Test Vector Sandbox*

### Added

- **Isolated Benchmark Store Protocol (`scripts/benchmark_rag.py`)**:
  - Implemented `setup_benchmark_store()` to create isolated ChromaDB client instances in ephemeral `tempfile.mkdtemp(prefix="chroma_bench_")` or custom directories.
  - Implemented `_ingest_into_candidate_collection()` copying documents from production Chroma collections strictly read-only, ensuring candidate embedding models never touch `data/chroma_db` or acquire single-writer leases.
  - Implemented `run_query_with_collection()` running retrieval evaluations directly against candidate collections while honoring priority boost weights.
  - Added `--keep-temp` flag to preserve benchmark stores in `data/chroma_bench/` when comparative debugging is needed.
- **Hermetic Benchmark Isolation Unit Tests (`Evelyn/tests/test_benchmark_rag_isolation.py`)**:
  - Added 4 targeted unit tests verifying ephemeral directory isolation, custom directory routing, candidate ingestion without writer locks, and query ranking.

### Changed

- **Global Test Suite Vector Store Sandboxing (`Evelyn/tests/conftest.py`, `Evelyn/tools/chroma_rag.py`)**:
  - Extended `isolate_test_vault_environment` in `conftest.py` to sandbox `cfg.CHROMA_DB_PATH` and `chroma_rag._CHROMA_DIR` into `tmp_vault/test_chroma_db`.
  - Enforced client cache reset and `chroma_rag.release_chroma_writer()` in teardown to prevent writer lock leakage across pytest workers.
  - Updated `chroma_rag.list_collection_names()` to dynamically resolve `getattr(cfg, "CHROMA_DB_PATH", _CHROMA_DIR)` instead of binding solely to startup defaults.
- **Model Registry Alignment (`scripts/benchmark_rag.py`)**:
  - Updated `BASELINE_MODEL` to `BAAI/bge-large-en-v1.5` and candidate models to `all-MiniLM-L6-v2`, `all-MiniLM-L12-v2`, `BAAI/bge-base-en-v1.5`, `BAAI/bge-small-en-v1.5`, and `nomic-ai/nomic-embed-text-v1.5`.

---

## [000.007.002] - 2026-10-01 — *Journal Evaluation Gate & In-Place Reflection Amending*

### Added

- **Runtime `<journal_status>` Context Injection (`evelyn_server.py`)**:
  - Injected `<journal_status status="recorded" date="..." path="..." />` into chat turn assembly at any hour of the day if a journal entry already exists on disk for the current date, giving the model zero-latency Round 0 awareness.
  - Injected `<journal_status status="none" date="..." />` during evening wind-down or when bedtime/journal queries are active, omitting token overhead during standard daytime chatter.
  - Added system prompt contract directive in `<system_telemetry_directives>` explicitly forbidding redundant journal rewrites on simple bedtime pleasantries when `status="recorded"`.
- **In-Place Reflection Amending & Tag Merging (`Evelyn/tools/journal_manager.py`)**:
  - Enhanced `create_journal_entry()` to support `mode="amend" | "overwrite" | "append"` (defaulting to `"amend"`).
  - Retired legacy blind `## Supplemental Entry` section appends; amending now updates the note body in place with fresh reflections.
  - Preserves and merges frontmatter tags from existing entries with newly supplied subject tags, preserving unique order and canonical taxonomy normalization.
- **Model-Facing Tool Parameterization (`Evelyn/tools/evelyn_tools.py`)**:
  - Added `mode` parameter (`enum: ["amend", "overwrite"]`) to `write_journal_entry` in `MODEL_TOOL_DEFINITIONS`.
  - Updated tool description with explicit guidance instructing the model to check `<journal_status>` before invoking and avoid redundant calls on simple goodnights.
- **Database Migration `000.007.002` (`Evelyn/tools/db_migrator.py`)**:
  - Registered and applied migration `000.007.002` (`procedure_1034_journal_evaluation_gate`) updating canonical master Procedure `#1034` in `evelyn_memory.db`.
  - Added step 3 Journal Evaluation Gate directives distinguishing between standard bedtime pleasantries (`status="recorded"` $\rightarrow$ acknowledge warmly without tool calls) and substantial new additions (`status="recorded"` with updates $\rightarrow$ `read_file` then `write_journal_entry` with `mode="amend"`).
  - Wired `suggested_tools` to `"write_journal_entry, read_file"`.
- **Unit Test Coverage (`Evelyn/tests/test_journal_status_and_amend.py`)**:
  - Added 7 comprehensive unit tests verifying initial creation, in-place amend mode with frontmatter tag merging, overwrite mode, XML envelope escaping, tool binding, and Procedure `#1034` schema verification.

### Changed

- **UI Usability Debounce Optimization (`evelyn_ui/dev.html`)**:
  - Increased redirect input debounce from 75ms to 200ms and added `lastDatalistQuery` caching, completely eliminating typing stutter and browser datalist teardown/rebuild hitches on the Review Workstation.

---

## [000.007.001] - 2026-10-01 — *Ghost Link Stub Redirection Engine*

### Added

- **Ghost Link Stub Redirection Primitive (`Evelyn/tools/link_librarian.py`)**:
  - Implemented `redirect_ghost_link_to_canonical()` orchestrating vault-wide link retargeting, canonical alias updating, and index synchronization.
  - Implemented `find_canonical_note_path()` resolving vault-relative paths and canonical note stems across both `vault_documents` and the filesystem with case-insensitive matching.
  - Retargets all referencing ghost links vault-wide (`[[Ghost]]` $\rightarrow$ `[[Canonical|Ghost]]`, `[[Ghost|Display]]` $\rightarrow$ `[[Canonical|Display]]`, `[[Ghost#Subpath]]` $\rightarrow$ `[[Canonical#Subpath|Ghost]]`) while code-protecting markdown blocks.
  - Automatically checks and records the ghost link term in the canonical note's frontmatter `aliases:` list if not already present, synchronizing `vault_db` librarian audit metadata and `mtime`.
- **Review Workstation UI & API Route Wiring (`evelyn_server.py`, `evelyn_ui/dev.html`)**:
  - Added `redirect_target: str | None = None` and `add_alias: bool = True` to `ProposalActionRequest` and `BulkProposalDecision`.
  - Added `action == "redirect"` handler in `_apply_proposal_action` in `evelyn_server.py`, validating the canonical target note, executing vault-wide redirection, and marking the proposal applied.
  - Added `GET /api/vault/candidates` endpoint in `evelyn_server.py` returning all indexed vault documents for fast UI autocomplete.
  - Enhanced `ghost_link_stub` proposal cards in `evelyn_ui/dev.html` with a prominent `🔀 Redirect to Note...` controller, live link-rewriting preview, vault document `<datalist>` autocomplete, and one-click execution.
- **Unit & Integration Test Coverage (`Evelyn/tests/test_stub_link_retarget.py`, `Evelyn/tests/test_review_endpoints.py`)**:
  - Added `test_find_canonical_note_path_nested_and_missing` and `test_redirect_ghost_link_rewrites_links_and_adds_alias` asserting exact link transformations, alias insertion idempotency, and error handling.
  - Added `test_ghost_link_stub_proposal_redirect` verifying the end-to-end REST endpoint execution, database status transition, and zero stub file creation.

---

## [000.007.000] - 2026-09-30 — *Controlled Taxonomy & Cognitive Retrieval*

### Milestone Summary

Version `000.007.000` marks the successful culmination of the **Controlled Taxonomy Architecture & Cognitive Retrieval Loop**, bridging the gap between post-coordinate vocabulary standards (ANSI/NISO Z39.19) and Evelyn's runtime cognitive loop across all knowledge substrates (Obsidian vault documents, SQLite context memory facts, and operational procedures).

Over a 291-patch evolutionary span (`000.006.000` $\rightarrow$ `000.006.291`), the engine transitioned from an unvetted, auto-generated tag space to a rigorous 2-tier governance and active retrieval ecosystem:
1. **Structural Foundation & Vocabulary Reset**: Executed the clean reset of unvetted tags, codified the Faceted Classification standard (`.agents/rules/vault-tag-taxonomy.md`), established multi-stage subject reconciliation (lexical, equivalence alias, vector with abstain margins), and created the standalone Taxonomy & Hierarchy Explorer (`ui/taxonomy.html`).
2. **Authority Thesaurus & Relation Graph**: Ingested and curated over 950 canonical terms, 195 equivalence aliases (`USE`/`UF`), and 285 associative/hierarchical relationships (`BT`, `NT`, `RT`) with FAST/Library of Congress authority promotion.
3. **Substrate & Procedure Rehydration**: Standardized tags across all 326 operational procedures in `evelyn_memory.db`, achieved full metadata parity across `<document tags="...">` and `<memory_entry tags="...">` in `<context_retrieval>` XML envelopes, and extended Tag Librarian auditing to procedures.
4. **Cognitive Retrieval & Model Agency**:
   - **Associative RAG Re-Ranking**: Benchmarked and activated `_apply_relation_boost` in `chroma_rag.py` (2.64ms average latency), dynamically re-ranking candidate chunks using curated associative (`RT`) and hierarchical (`BT`/`NT`) relations with bounded distance closing (15%) and top-$k$ seed exclusion invariants.
   - **Model-Facing Subject Discovery**: Added `search_by_tag` to `evelyn_tools.py` with automatic equivalence canonicalization (e.g. `dnd` $\rightarrow$ `ttrpg`, `workout` $\rightarrow$ `exercise`), privacy gating (`is_tool_denied()`), concept suggestions, and operational starter procedure `#2585` (Migration `000.006.290`).

---

## [000.006.291] - 2026-09-30 — *RAG Relation Expansion Activation*

### Added

- **Relation Expansion Benchmark Harness (`scripts/benchmark_rag_relation_expansion.py`)**:
  - Implemented standalone benchmarking harness measuring associative re-ranking latency, candidate distance closing (`min(cap, sum(weight) * cap)`), and result set mutation across domains (TTRPG, Exercise, Sleep).
  - Benchmarking confirmed average pass latency of **2.64ms** against the 285-relation taxonomy graph with zero performance degradation, verified candidate promotion into top-ranked slots, and confirmed strict enforcement of the top-$k$ seed exclusion invariant preventing self-reinforcing loops.

### Changed

- **Default RAG Relation Expansion Activation (`evelyn_config.py`)**:
  - Enabled `RAG_RELATION_EXPANSION_ENABLED = _env_flag("RAG_RELATION_EXPANSION_ENABLED", True)` by default, activating `_apply_relation_boost` during vector search in `chroma_rag.py`.
  - Inbound candidate chunks now leverage associative (`RT`) and hierarchical (`BT`/`NT`) relations from `master_tag_related` in `evelyn_memory.db` with bounded 15% distance closing and 2x overfetch window.
- **Unit Test Suite Update (`test_rag_relation_expansion.py`)**:
  - Updated configuration assertions to expect `cfg.RAG_RELATION_EXPANSION_ENABLED is True` by default while retaining isolated mock tests for toggle behavior.

## [000.006.290] - 2026-09-30 — *Controlled Subject Discovery Tool (search_by_tag)*

### Added

- **Model-Facing Subject Discovery Tool (`search_by_tag` in `evelyn_tools.py`)**:
  - Implemented `search_by_tag(tags, target="all", match_all=True, limit=5)` allowing Evelyn to discover, filter, and inspect Obsidian Vault notes, long-term memory facts, and operational procedures by controlled subject taxonomy tags.
  - Automatically resolves equivalence aliases through `taxonomy_db.canonicalize_tags()` (e.g. `dnd` $\rightarrow$ `ttrpg`, `workout` $\rightarrow$ `exercise`) with transparent user-facing mapping notes.
  - Supports flexible knowledge substrate targeting (`target="all"`, `"vault"`, `"memory"`, or `"procedures"`) and logical tag combination modes (`match_all=True` for intersection or `match_all=False` for union).
  - Enriches query responses by dynamically surfacing related concepts from the `master_tag_related` thesaurus graph (`💡 Related Taxonomy Concepts`).
  - Enforces vault document access security by filtering out confidential notes via `is_tool_denied()`.
- **Specialist Tool Intent Heuristic Patterns (`evelyn_config.py`)**:
  - Added regex heuristics to `SPECIALIST_TOOL_INTENT_PATTERNS` dynamically activating `search_by_tag` into Round 1 tool schemas when prompts request finding, listing, or filtering notes, memories, or procedures by tag or subject.
- **Operational Starter Procedure Registration (Migration `000.006.290`)**:
  - Authored and applied Migration `000.006.290` to `evelyn_memory.db` per AGENTS.md Rule 10, registering `starter_procedure_search_by_tag` with trigger regex, step-by-step execution directives, failure pitfalls, verification criteria, and controlled vocabulary tags (`search, taxonomy, retrieval, knowledge-management`).
- **Targeted Unit Test Suite (`test_search_by_tag.py`)**:
  - Added hermetic unit tests verifying tool registration, intent heuristics, parameter normalization, alias canonicalization, substrate filtering, relation surfacing, and intersection/union matching.

## [000.006.289] - 2026-09-29 — *Operational Procedure Tag Librarian Governance*

### Added

- **Procedure Tag Librarian Auditing (`tag_librarian.py`)**:
  - Implemented `audit_single_procedure_tags()` to audit operational procedures against the controlled taxonomy, bringing procedures under identical governance as memory facts and vault documents.
  - Prunes non-subject container markers (`skill/`, `procedure/`, `workflow/`, `protocol/`, `skills/`, `procedures/`), resolves aliases via `master_tag_aliases`, classifies text against the registered vocabulary via `classify_document_subjects()`, and raises `tag_admission` proposals for novel terms with procedure attribution (`source_ids=[proc_id]`).
- **Procedure Audit State Tracking (`memory_db.py` & Migration `000.006.289`)**:
  - Added `last_tag_audit_at REAL` and index `idx_proc_tag_audit` to the `procedures` table in `evelyn_memory.db` via Migration `000.006.289`.
  - Implemented helper functions `fetch_next_procedures_for_tag_audit()`, `count_procedures_awaiting_tag_audit()`, and `mark_procedure_tag_audited()`.
- **Procedure Tag Audit CLI Tool (`scripts/audit_procedures.py`)**:
  - Added standalone CLI maintenance script to audit operational procedures with `--limit`, `--dry-run`, and `--all` flags.
- **Targeted Unit Test Suite (`test_procedure_tag_librarian.py`)**:
  - Added unit tests verifying procedure tag container pruning, alias canonicalization, subject classification, and audit timestamp recording.

## [000.006.288] - 2026-09-29 — *Context Envelope Metadata Parity for Memory Facts*

### Added

- **Context Envelope Parity for Memory Facts (`chroma_rag.py`)**:
  - Exposed `tags` attribute on `<memory_entry id="..." category="..." subject="..." date="..." tags="...">` envelopes generated by `build_rag_context()`, bringing memory facts into metadata parity with vault documents (`<document tags="...">`).
  - Added fallback to Chroma chunk metadata `tags` when SQLite row lookup is bypassed or during synthetic chunk evaluation.
  - Added unit test assertion in `test_category_attribution.py` verifying end-to-end XML serialization of memory entry tags.

### Changed

- **Defensive Procedure Step Formatting (`chroma_rag.py`)**:
  - Hardened procedure block formatting in `build_rag_context` to safely access `proc.get("steps")` rather than raw dictionary key indexing.

## [000.006.287] - 2026-09-29 — *Operational Procedure Vocabulary & Tag Rehydration*

### Added

- **Procedure Equivalence Aliases (`master_tag_aliases`)**:
  - Registered 16 controlled taxonomy aliases in `evelyn_vault.db` resolving common procedure variants to preferred canonical headings (`dnd` -> `ttrpg`, `workout` -> `exercise`, `workouts` -> `exercise`, `groceries` -> `grocery`, `appointments` -> `appointment`, `specs` -> `specifications`, `manuals` -> `manual`, `sleep-hygiene` -> `sleep`, `meditation` -> `mindfulness`, etc.).
  - Enqueued post-migration vector synchronizations in ChromaDB (`tag_registry` collection) for new equivalence surface forms.

### Changed

- **Controlled Vocabulary & Tag Rehydration for Procedures (`procedures` table in `evelyn_memory.db`)**:
  - Rehydrated and standardized tags across all 326 operational procedures (51 live master procedures, 202 merged legacy procedures, 73 archived procedures) from pre-reset backup snapshots (`evelyn_memory.db_pre_000.006.187_20260920_230642.bak`).
  - Stripped deprecated non-subject container markers (`skill/`, `procedure/`, `protocol/`, `system/`, `task/`, `workflow/`, `rule/`).
  - Mapped all 51 active live procedures strictly to controlled subject atoms present in `master_tag_taxonomy`, restoring query-procedure trigger keyword overlap and topic-based procedural tool surfacing.

## [000.006.286] - 2026-09-29 — *Taxonomy Inspector Inline Related Concept Linking*

### Added

- **Inspector Inline Related Concept Linking (`evelyn_ui/taxonomy.html`)**:
  - Added dedicated inline input field and **`+ Add Related Concept`** button directly inside the **🔗 Related Concepts (Associative Peers)** inspector card (`#card-related`).
  - Implemented `addRelatedFromInspector()` handler connecting associative peers via `POST /api/taxonomy/relation` (`kind="related"`), refreshing the graph and preserving active selection.
  - Added input clearing in `selectTag()` to purge partially typed related and alias inputs when switching concepts.
- **Controlled Vocabulary Registration on Relationship Linking (`evelyn_server.py`)**:
  - Updated `update_taxonomy_relation()` to ensure both endpoints (`term_a` and `term_b`) are registered in `master_tag_taxonomy` via `taxonomy_db.upsert_master_tag()` before linking associative or hierarchical relations, guaranteeing referential integrity across graph views.

## [000.006.285] - 2026-09-29 — *Taxonomy Alias Equivalence Linker & Absorption Invariants*

### Added

- **Multi-Point Alias Creation Workflow (`evelyn_ui/taxonomy.html`)**:
  - **Quick Relationship Linker Integration**: Added `Alias / Equivalence (Term A redirects to canonical Term B)` option to the `#link-kind` dropdown. Implemented `updateQuickLinkerLabels()` to dynamically toggle input labels and placeholders between relationship hierarchy (`Child` / `Parent`), associative peers (`Concept A` / `Concept B`), and alias redirection (`Alias / Surface Form` / `Canonical Target`).
  - **Inspector Card Inline Alias Addition**: Added an inline alias input field and `+ Add Alias` action directly inside the **🔀 Equivalent Aliases (Redirects Here)** card for the selected tag, allowing instant alias creation without typing the canonical term.
  - **Authority UF 1-Click Alias Adoption**: Added interactive `+ Alias` buttons to every variant pill in the **🏷️ UF (Used For / Aliases)** section of authority search results, allowing one-click alias registration directly from institutional library records.
  - **Relationship Modal Equivalence**: Added `Alias / Equivalence (Target redirects to Current)` to the modal relationship selector for inline tag linking.
- **Backend Alias Absorption & Invariant Enforcement (`evelyn_server.py`)**:
  - Implemented `_register_taxonomy_alias()` helper enforcing full ISO 25964 / SKOS equivalence semantics:
    - **Canonical Guarantee**: Ensures the canonical target is registered in `master_tag_taxonomy`.
    - **Vocabulary De-duplication / Absorption**: If the alias previously existed as a standalone tag in `master_tag_taxonomy`, prunes it from `master_tag_taxonomy` and enqueues Chroma vector deletion via `tag_librarian.delete_tag_from_chroma()`.
    - **Relationship Inheritance & Repointing**: Invokes `taxonomy_db.repoint_relations(alias, canonical)` to move any existing hierarchical (`narrower`) or associative (`related`) relations from the retired alias onto the surviving canonical tag, pruning self-referential links.
    - **Cache Invalidation**: Automatically drops `taxonomy_db._ALIAS_CACHE` so write-path canonicalization (`canonicalize_tags()`) immediately rewrites notes, facts, and queries across the engine.
  - Updated `POST /api/taxonomy/relation` to support `kind="alias"`.
  - Added dedicated endpoint `POST /api/taxonomy/alias` with Pydantic request validation (`TaxonomyAliasCreateRequest`).
- **Targeted Unit Testing (`Evelyn/tests/test_authority_taxonomy.py`)**:
  - Added `test_register_taxonomy_alias_absorption_and_repointing()` testing end-to-end alias registration, standalone tag pruning, relation inheritance, and instantaneous cache invalidation.

## [000.006.284] - 2026-09-29 — *Taxonomy Authority 4-Facet Thesaurus Discovery (UF, BT, NT, RT)*

### Added

- **4-Facet Thesaurus Discovery Engine (`scripts/lookup_authority_taxonomy.py`)**:
  - Implemented `fetch_loc_concept_facets()` extracting all 4 ISO 25964 / SKOS thesaurus facets directly from Library of Congress linked-data JSON-LD payloads:
    - **UF (Used For / Aliases)**: Synonyms and alternative labels (`skos:altLabel` / `madsrdf:variantLabel`).
    - **BT (Broader Terms / Parent Concepts)**: Direct hierarchical parents (`skos:broader` / `madsrdf:hasBroaderAuthority`).
    - **NT (Narrower Terms / Sub-Concepts)**: Direct hierarchical children (`skos:narrower` / `madsrdf:hasNarrowerAuthority`).
    - **RT (Related Terms / Associative Peers)**: Non-hierarchical associative concept peers (`skos:related` / `madsrdf:hasRelatedAuthority`).
  - Added exact alias matching bonus (+850) in `score_authority_result()` so searching by an alias (e.g. `3d-printing`) immediately elevates the canonical authorized heading (`Three-dimensional printing`) to Rank 1.
  - Hydrated top candidates in parallel without extra latency, taking advantage of self-contained JSON-LD concept graphs.
- **Interactive Facet Curation & One-Click Linking (`evelyn_ui/taxonomy.html`)**:
  - Upgraded authority result cards to display dedicated color-coded facet sections:
    - 🏷️ **UF (Used For / Aliases)**: Amber/cyan pills displaying see-from aliases that are registered on promotion.
    - 🌳 **BT (Broader / Parent Concepts)**: Indigo pills with clickable title links and an **`⬆ Parent`** quick-link button.
    - 🌿 **NT (Narrower / Sub-Concepts)**: Violet pills with clickable title links and a **`⬇ Child`** quick-link button.
    - ⚡ **RT (Related / Associative Peers)**: Gold pills with clickable title links and a **`⚡ Peer`** quick-link button.
  - Implemented `quickLinkAuthorityRelation()`: one-click action that connects concepts directly into `master_tag_related` via `POST /api/taxonomy/relation` and refreshes the graph view.
  - Implemented `searchAuthorityPivot()`: clicking on any broader, narrower, or related concept title pivots the authority search box to explore that concept's neighborhood, enabling seamless graph navigation across global library authorities.
- **Targeted Facet Unit Testing (`Evelyn/tests/test_authority_taxonomy.py`)**:
  - Added `test_fetch_loc_concept_facets_parser()` verifying graph parsing of UF, BT, NT, and RT nodes.
  - Updated mock hydration tests validating that all 4 facets are returned and populated.

## [000.006.283] - 2026-09-29 — *Taxonomy Authority Relevance Ranking & Controlled Search Trigger*

### Added

- **Taxonomy Authority Relevance Ranking & Anti-Starvation Engine (`scripts/lookup_authority_taxonomy.py`)**:
  - Implemented `score_authority_result()` calculating multi-factor relevance scores prioritizing Library of Congress Subject Headings (LOC LCSH), dual authority confirmations (`FAST + LOC`), topical concepts (`MARC 150`), and exact title/atom matches.
  - Applied corporate entity penalties (`MARC 110/111`) and personal name penalties (`MARC 100`) to demote organizational acronym noise (e.g. university research centers) below canonical subject concepts.
  - Expanded candidate pools (`rows=30` in FAST, `count=25` in LOC) before merging and sorting, preventing FAST candidates from starving LOC results.
  - Implemented deferred parallel see-from variant hydration (`fetch_loc_variants`), querying LOC concept JSON-LD only for winning top-ranked results to maintain sub-second response times.
- **Acronym & Cross-Reference Transparency (`scripts/lookup_authority_taxonomy.py`, `evelyn_ui/taxonomy.html`)**:
  - Detected when a FAST result matches via an alternative acronym or cross-reference (`type: "alt"`, `suggestall`) rather than direct heading title match, tracking `matched_via`.
  - Surfaced a distinct amber badge in the UI (`matched: "ACRONYM"`) when a concept is retrieved via an acronym or see-from alias, providing immediate visual context for why the heading appeared.
- **Controlled Search Triggering & Usability Standards (`evelyn_ui/taxonomy.html`)**:
  - Removed automatic typing search listener, strictly binding authority queries to clicking the **Search** button or pressing **Enter** to prevent accidental lookups and rate limits.
  - Added beginner-friendly tooltips (`title="..."`) to authority inputs and action controls per UI standards.
- **Automated Authority Test Suite (`Evelyn/tests/test_authority_taxonomy.py`)**:
  - Created hermetic unit tests verifying atom normalization, scoring hierarchy (dual authority > LOC > FAST > corporate noise), exact title match prioritization, and candidate deduplication.

## [000.006.282] - 2026-09-29 — *Fast Authority Lookup & Profile Ledger Fact Curation*

### Added

- **OCLC FAST Suggest Authority Search & Multi-Authority Lookup (`scripts/lookup_authority_taxonomy.py`, `evelyn_server.py`)**:
  - Integrated OCLC FAST (Faceted Application of Subject Terminology) suggest API (`https://fast.oclc.org/searchfast/fastsuggest...`), querying headings across topics, personal/corporate names, titles, and events in ~30ms.
  - Implemented parallel multi-authority queries across OCLC FAST and Library of Congress (LOC LCSH) using `concurrent.futures.ThreadPoolExecutor`.
  - Added deterministic result deduplication and authority source tagging (`FAST`, `LOC`, or `FAST + LOC`).
  - Added an in-memory 30-minute query cache (`_QUERY_CACHE`) to avoid redundant roundtrips.
  - Added `query_authority_diagnostic()` returning detailed diagnostics: results, total match counts, active authority sources queried, and granular error details (e.g. rate-limit, timeout, DNS resolution).
  - Updated `/api/taxonomy/authority/lookup` endpoint in `evelyn_server.py` to return the enriched diagnostic payload.
- **Taxonomy Authority UI Diagnostics & Debounced Search (`evelyn_ui/taxonomy.html`)**:
  - Added 450ms input debouncing and explicit Enter key search triggering to prevent rapid request floods.
  - Implemented `AbortController` cancellation for in-flight requests, eliminating stale race conditions.
  - Added an animated CSS loading spinner during lookups.
  - Enhanced error display: surfaces clear diagnostic alert banners distinguishing network timeouts and HTTP 429 rate-limits from genuine "No authoritative headings found" zero-match responses.
  - Rendered authority source badges (`[FAST]`, `[LOC]`, `[FAST + LOC]`) with clickable concept record links.
- **Authoritative Profile Facts Ledger Workstation (`evelyn_ui/profile_facts.html`, `evelyn_ui/dev.html`, `evelyn_server.py`)**:
  - Built a dedicated standalone workstation page (`/ui/profile_facts.html`) to replace cramped modal windows with an expansive, full-viewport curation interface for `User_Profile_facts.md`, `Assistant_Profile_facts.md`, and `System_Directives_facts.md`.
  - Added rich header and profile cards with live word budgets, progress bars, and fact count badges.
  - Added interactive filtering by keyword, document section, and priority tier (`CORE`, `EXPANDED`, `ARCHIVED`).
  - Added quick-add fact form with autocomplete datalist for existing section headings.
  - Added spacious inline editing and one-click tier cycling on fact cards.
  - Added permanent fact deletion with confirmation dialogs and instant presentation file recompilation.
  - Streamlined access points: added prominent `📋 Profile Facts` navigation buttons directly in the top headers of `dev.html` and `taxonomy.html` beside `Taxonomy Explorer`, while decluttering task monitor and status cards.
  - Implemented backend endpoints in `evelyn_server.py`: `GET /api/persona/ledgers`, `GET /api/persona/ledger/{filename}`, `POST /api/persona/ledger/{filename}/item/update`, and `POST /api/persona/ledger/{filename}/item/delete`.
- **Presentation-to-Ledger Reconciliation Engine (`Evelyn/tools/profile_ledger.py`, `evelyn_server.py`)**:
  - Implemented `reconcile_ledger_with_presentation(ledger_content, presentation_markdown)`: parses both layers, matches bullets by normalized bold label or content, preserves existing priority tiers on surviving bullets, prunes deleted bullets, updates modified facts, and inserts newly authored bullets.
  - Updated proposal approval (`POST /api/review/proposals/{id}/approve`) and proposal editing (`action == "edit"`): when an operator edits or removes bullet points in the proposal text editor and approves, the candidate ledger is automatically reconciled against the approved markdown, permanently purging deleted facts from `*_facts.md` and preventing unwanted facts from resurrecting on subsequent evolution passes.
- **Hermetic Ledger Endpoint Unit Tests (`Evelyn/tests/test_persona_ledger_endpoints.py`, `Evelyn/tests/test_profile_ledger.py`)**:
  - Added test suite `test_persona_ledger_endpoints.py` validating metadata listing, retrieval, update, and deletion across sandbox files.
  - Added unit test `test_reconcile_ledger_with_presentation` in `test_profile_ledger.py`.

### Fixed

- **Assistant Persona Cross-Contamination in User Profile (`Evelyn/tools/profile_evolver.py`, `Evelyn/persona/User_Profile_facts.md`)**:
  - Removed assistant relationship category `Cat06-A` from `DOCUMENT_CATEGORIES[cfg.PERSONA_FILE_USER]` and `DOCUMENT_THEMES[cfg.PERSONA_FILE_USER]`.
  - Added regex patterns in `DOMAIN_BANNED_PATTERNS[cfg.PERSONA_FILE_USER]` to reject facts where the subject is Evelyn/Assistant (`Evelyn views`, `Evelyn uses`, `She...`).
  - Purged assistant-specific facts (e.g. `Shared Silence`, `Terms of Endearment`) from `User_Profile_facts.md`.

## [000.006.281] - 2026-09-28 — *Pinned Vector Store Alias Parsing & Chat Background Shielding*

### Fixed

- **ChromaDB Pinned Document Alias Type Normalization (`Evelyn/tools/chroma_rag.py`)**:
  - Fixed an unhandled `AttributeError: 'list' object has no attribute 'split'` in `_fetch_pinned_chunks()` when ChromaDB document metadata stores `aliases` as a native list of strings (common in notes with YAML flow lists like `aliases: [Amber]`).
  - Added type-checking to support lists, tuples, comma-separated strings, or `None`, preventing crashes during pinned knowledge retrieval across all non-phatic conversational turns.
  - Added unit test `test_fetch_pinned_chunks_supports_list_and_string_aliases` in `Evelyn/tests/test_rag_precision_targeting.py` verifying seamless handling of mixed alias types.
- **Chat Worker Unhandled Exception Shielding (`evelyn_server.py`)**:
  - Broadened background worker exception handling in `_process_chat_background()` from a narrow tuple `(httpx.HTTPError, sqlite3.Error, OSError, RuntimeError, ValueError)` to `Exception`.
  - Added `traceback.print_exc()` logging so unexpected background task exceptions are prominently recorded in systemd journalctl instead of failing silently into `finally:`.

## [000.006.280] - 2026-09-28 — *Tag Admission Quorum & Stub Proposal Card Inline UX*

### Added

- **Tag Admission Quorum & Auto-Admission Engine (`Evelyn/tools/tag_librarian.py`, `evelyn_config.py`)**:
  - Added `TAG_ADMISSION_MIN_SOURCES = 2` and `TAG_ADMISSION_AUTO_ADMIT = True` to configuration.
  - Implemented `evaluate_tag_confidence(term)` in `tag_librarian.py`: evaluates distance bands against the taxonomy vector index, checks wellformedness format rules, inspects the entity/proper noun register, and dispatches to local Ollama validation for distant terms.
  - Added quorum corroboration to `propose_tag_admission` and `release_deferred_admissions`: unregistered terms citing fewer than 2 distinct sources (notes/facts) are held in `deferred` status to suppress single-occurrence noise from flooding the queue.
  - When quorum is met ($\ge 2$ sources) and confidence is high, `propose_tag_admission` auto-admits the term directly into `taxonomy_db`, triggers backfilling across referencing facts and notes, and logs the auto-admission without operator burden.
  - Added `Stubs/` to `VAULT_STRUCTURAL_IGNORE` and guarded `is_excluded_document` and `audit_single_document_semantic` to skip notes in `Stubs/` or carrying `type: stub`.
- **Direct Stub Proposal Editing & API Parity (`evelyn_server.py`, `evelyn_ui/dev.html`)**:
  - Added direct fields (`target_name`, `abstract`, `domain`, `tags`) to `ProposalActionRequest` and `BulkProposalDecision` in `evelyn_server.py`.
  - Updated `/api/review/proposals/{id}/approve` handler to inspect both stored/transmitted XML and apply direct UI field overrides when generating stub notes.
  - Replaced the unformatted raw XML `<details>` editing box on the stub proposal card in `dev.html` with direct, styled in-place editable inputs (Target Note, Synthesized Executive Abstract textarea, Domain Folder, and Tags inputs).
  - Renamed the "Domain" metadata field to "Domain Folder" with explanatory tooltip (`Subfolder under Stubs/...`) and placeholder (`e.g. general, hardware, lore`) to make subfolder placement immediately clear.
  - Wrapped harvested mentions and context excerpts in `"… <excerpt> …"` both in `render_stub_markdown` and in the UI card.
- **Proposals Queue Rebranding & Advanced Type Filtering (`evelyn_ui/dev.html`)**:
  - Rebranded "Unified Triage Queue" to "Proposals Queue" across tabs, headers, and UI messages.
  - Overhauled the triage filter bar: retained prominent pills for `All`, `Profile Updates`, and `Procedures`, while introducing a compact styled dropdown selecting specific proposal types (`🏷️ Term Admissions`, `👻 Ghost Link Stubs`, `🔗 Term Relations`, `⚡ Procedure Merges`, `⚡ Procedure Splits`, `✂️ Fact Splits`) and folding `📥 Extractions` into the dropdown.
  - Updated `parseAdvancedQuery` and `matchesAdvancedQuery` with `type:<type>` and `-type:<type>` search filter tokens.
  - Enriched `getTriageSearchableFields` with `item.type`, `item.item_type`, and human-friendly search aliases across all queue items.
- **Web UI Usability, Information Architecture & Tooltip Standards (`.agents/rules/ui-standards.md`, `AGENTS.md`)**:
  - Created canonical UI rulebook `.agents/rules/ui-standards.md` establishing the "Human-First Tooltip Mandate (UI Docstrings)": every interactive control, input, textarea, action button, filter control, and metric badge must carry a beginner-friendly `title="..."` hover tooltip explaining what it controls, expected formats, and downstream effects.
  - Added Section 12 to `AGENTS.md` governing frontend usability, in-place structured editing over raw serialization blocks, and progressive disclosure.
  - Performed comprehensive tooltip sweep across `evelyn_ui/dev.html` (expanding tooltip coverage from 22 to 164 elements): added descriptive tooltips to main navigation tabs, bulk action bars, search and filter controls, and across every proposal and review card (Ghost Link Stubs, Term Admissions, Term Relations, Procedure Merges, Procedure Splits, Fact Splits, Extractions, and Profile Updates).

### Fixed

- **Queue Saturation & Stub-Sourced Term Purge (`data/evelyn_memory.db`)**:
  - Purged 76 stub-sourced tag proposals and 100 single-source proposals from `data/evelyn_memory.db`, reducing the clogged 200/200 proposal backlog down to 29 high-value actionable items.

## [000.006.279] - 2026-09-27 — *Entity Stub Citation Independence & Caller Witness Isolation*

### Fixed

- **Caller Witness Isolation & Evidence Inflation Guard (`Evelyn/tools/link_librarian.py`)**:
  - Fixed a critical evidence leakage defect where `create_ghost_link_stub` unconditionally force-inserted `source_path` into `harvested_refs` when `caller_rel` was omitted by `harvest_entity_references`. When `source_path` was a stub note (e.g. `Stubs/Google Sites.md`), this artificially inflated the reference count from 1 to 2, causing ghost links mentioned once in the vault to cross the minimum threshold and spawn bogus proposals.
  - Enhanced `_is_stub_note` to inspect file paths in addition to frontmatter (`norm.startswith("stubs/")` or component `"stubs"`), ensuring notes residing in `Stubs/` are identified even before or without frontmatter extraction.
  - Guarded `harvest_entity_references` with a fast path check (`_is_stub_note(None, path=rel_path)`) before file read and parsing.
  - Guarded `create_ghost_link_stub` against using stub callers as `primary_source` or injecting their context into `primary_context`.
- **Master Librarian Stub Audit Bypass (`Evelyn/tools/master_librarian.py`)**:
  - In `audit_single_document`, Step 4 (Ghost Link Stub Synthesis) is now completely bypassed when the document being audited is a stub note (`is_stub_doc`). Stubs are leaf entity summaries whose `Context & Mentions` sections are citations of other notes; they are never original authoring sources and must never initiate ghost link stub synthesis.
- **Database Hygiene Cleanup (`data/evelyn_memory.db`)**:
  - Purged 15 invalid pending `ghost_link_stub` proposals in `proposals` that had been artificially inflated by cross-stub citations.

### Added

- **Regression Unit Tests (`Evelyn/tests/test_stub_evidence_independence.py`)**:
  - Added test cases verifying path-based stub identification (`test_stub_path_is_recognised`, `test_non_stub_paths_are_not_recognised_as_stubs`).
  - Added test case verifying caller stubs do not inflate citation counts (`test_caller_stub_does_not_inflate_count`).
  - Added test case verifying caller stubs are excluded from sources even when real notes meet thresholds (`test_caller_stub_excluded_when_real_notes_meet_threshold`).
  - Added test case verifying `master_librarian.audit_single_document` bypasses ghost stub synthesis when auditing stub notes (`test_master_librarian_bypasses_ghost_stub_creation_for_stubs`).

## [000.006.278] - 2026-09-27 — *Entity Stub Synthesis General Knowledge & Grounded Identity Architecture*

### Added

- **Grounded Entity Stub Synthesis Instructions (`Evelyn/tools/link_librarian.py`)**:
  - Overhauled `synthesize_entity_abstract` to actively leverage general world knowledge for real-world entities (musicians, video games, software, hardware, franchises, deities, historical figures) while grounding internal concepts (tabletop RPG characters, personas, private projects) in vault records.
  - Added optional `domain_hint` context injection into the LLM synthesis prompt from `infer_stub_domain` or caller parameters.
  - Integrated robust anti-hallucination guardrails: forbids misclassifying list items (gift ideas, game rotations) as coworkers or people, forbids declaring slash-separated titles as synonyms, and prevents defining global entities solely by single isolated anecdotes.
  - Added defensive cleaning stripping accidental model-generated markdown callouts (`> [!ABSTRACT]`) and bounding formatting.

### Changed

- **Stub Approval Gist Indexing (`evelyn_server.py`)**:
  - Updated `/api/review/proposals/{id}/approve` handler for `ghost_link_stub` to record `payload.synthesized_abstract` directly into `vault_documents.gist` rather than hardcoding a generic boilerplate string, improving vector search and RAG retrieval.
  - Exposed `"type": payload.type` in `parsed_payload` across `/api/review/unified` and `/api/review/proposals` to surface frontmatter type classifications to the review UI.
- **Review Endpoint Test Alignment (`Evelyn/tests/test_review_endpoints.py`)**:
  - Aligned stub lifecycle tests with taxonomy §3.4 frontmatter contract (`type: [stub]` property rather than subject tags).
- **Vault Stub Knowledge Base Curation (`~/obsidian_vault/Stubs/`)**:
  - Overhauled and verified 103 entity stub notes across the vault to establish accurate real-world definitions, clear personal vault contextualization, and clean Visual PKM callout blocks (`> [!ABSTRACT]`).

## [000.006.277] - 2026-09-27 — *Controlled Taxonomy Architecture Backbone & Facet Property Wiring*

### Added

- **Controlled Taxonomy & Classification Backbone (`reference/engine_architecture.md`)**:
  - Authored and integrated **Section 9: Controlled Taxonomy & Classification Backbone** into the canonical engine architecture specification.
  - Documented post-coordinate indexing principles adhering to ANSI/NISO Z39.19, ISO 25964, and DCMI application profiles.
  - Formulated the dual-substrate unified model where vault notes and memory facts share a single controlled vocabulary (`master_tag_taxonomy`, `master_tag_aliases`, `master_tag_related`, `tag_entities`).
  - Added full Mermaid architectural diagram mapping the relations between rule definitions, storage substrates, engine classification passes, API routes, and interactive UI visualizers.

### Changed

- **Zero-Slash Invariant & Native YAML Facet Properties (`AGENTS.md`, `evelyn_server.py`, `scripts/extract_pdf_library.py`)**:
  - Aligned engine generation routines and agent operational rules with the strict Zero-Slash Invariant:
    - Updated `AGENTS.md` §10 to mandate native YAML properties for non-subject facets (`type:`, `motif:`, `setting:`, `event:`), reserving `tags: [...]` purely for subject domain atoms and administrative flags (`obsidian-graph/`, `status/`).
    - Fixed ghost link stub generation in `evelyn_server.py` to record `tags=""` instead of legacy `"type/stub"`, preventing slash tags from entering `vault_documents.tags` upon approval.
    - Updated PDF extraction pipeline (`scripts/extract_pdf_library.py`) to emit native `type: [reference]` for chapters and `type: [moc]` for sidecars/index notes, keeping `tags:` flat and zero-slash.
    - Updated test suite (`Evelyn/tests/test_pdf_sidecar_generator.py`) to assert against `type: [moc]` property.

## [000.006.276] - 2026-09-27 — *Zero-Unvetted Memory Tag Curation & Autonomous Audit Safeguards*

### Changed

- **Autonomous Audit Runtime Safeguards & API Toggles (`evelyn_server.py`, `reference/endpoints.md`)**:
  - Eliminated dangerous `importlib.reload(cfg)` calls inside the 5-minute and 10-minute idle background loops (`_idle_master_librarian_loop`, `_idle_tag_librarian_loop`), permanently resolving the `G14` config hot-reload vulnerability where editing code or configuration files could inadvertently trigger autonomous background vault sweeps.
  - Implemented thread-safe in-memory runtime toggles (`_autonomous_audit_toggles`) initialized safely from `evelyn_config.py` at boot and defaulting to `False`.
  - Added new administrative endpoint `POST /api/librarian/toggle` to dynamically enable or disable autonomous background audits on demand without server restart.
  - Updated `GET /api/librarian/status` to report live `autonomous_toggles` telemetry for front-end dashboards and triage cards.

### Curated

- **Zero-Unvetted Memory Tag Curation (`data/evelyn_memory.db`, `scripts/remap_unvetted_memory_tags.py`)**:
  - Re-mapped all 12 context entry rows carrying unvetted entity/alias tags in `context_entries` to authorized subject domain atoms:
    - `comfyui` $\rightarrow$ `image-generation`
    - `ollama` $\rightarrow$ `large-language-models`
    - `dnd` $\rightarrow$ `ttrpg`
    - `oura-ring` $\rightarrow$ `biometrics`
    - `youtube` $\rightarrow$ `media`
    - `relationship-dynamics` $\rightarrow$ `relationship` (canonical equivalence alias resolution)
  - Verified `tag_entities` in `data/evelyn_vault.db`: verified all 5 software, product, and game names are properly cataloged as named entities.
  - **Achieved 100.0% zero-unvetted tag compliance across the entire memory database** (0 unvetted tags remaining in `evelyn_memory.db`).

## [000.006.275] - 2026-09-27 — *Responsive Header Brand & Viewport Protection*

### Fixed

- **Header Subtitle Overrun & Bleed-Through Prevention (`evelyn_ui/taxonomy.html`)**:
  - Enforced rigid boundary on the top navigation header (`overflow: hidden; gap: 12px;`), preventing child elements from escaping the 56px header bounds.
  - Constrained `.brand-text h1` and `.brand-text p` with `white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin: 0;`.
  - Added responsive media query (`@media (max-width: 1400px) { .brand-text p { display: none !important; } }`) to automatically hide the 66-character page subtitle on smaller viewports, TV displays with high OS scaling (e.g. 250%), and narrow browser splits, eliminating vertical text wrapping over the left sidebar search dock.
  - Made secondary header telemetry pills (`Aliases` and `Relations`) responsive via `.pill-hide-sm` (`@media (max-width: 1200px)`), and cleanly collapsed all header pills at `<= 950px` to protect brand and navigation action buttons.
  - Expanded left sidebar width slightly from `310px` to `330px` and refined navigation tab grid button padding (`padding: 6px 1px;`), ensuring tab labels (`🌳 Trees`, `📊 Categories`, `⚡ Peers`, `🔤 All`, `🔀 Aliases`) have comfortable breathing room on scaled displays.

## [000.006.274] - 2026-09-27 — *Independent Frame Scrolling & Responsive Taxonomy UI*

### Changed

- **Independent Multi-Frame Layout Architecture (`evelyn_ui/taxonomy.html`)**:
  - Pinned overall application viewport (`html, body { height: 100vh; overflow: hidden; }`) and navigation header (`height: 56px; flex-shrink: 0;`), completely eliminating whole-page vertical/horizontal rolling.
  - Divided workspace into three rigid, independently scrollable frames:
    - **Left Navigator Frame (`.sidebar`)**: Search dock and tab buttons are permanently pinned (`flex-shrink: 0;`), while the tree/category list scrolls smoothly on its own inside `.tree-scroll` with sticky sorting controls (`🌿 Branches` / `🔥 Usage`). The search dock and tabs are always visible without scrolling back to top.
    - **Center Inspector Frame (`.inspector`)**: Dedicated independent vertical scroll (`overflow-y: auto; overflow-x: hidden;`) with CSS container query capabilities (`container-type: inline-size`).
    - **Right Toolbox Frame (`.toolbox`)**: Dedicated independent vertical scroll (`overflow-y: auto; overflow-x: hidden;`) for Library of Congress search, relations viewer, and quick hierarchy linkers.
  - Added header toggle button `📐 Tools` (`#btn-toggle-toolbox`) to collapse/expand the right panel with persistent `localStorage` state, maximizing center workspace on couch/TV viewing.

### Fixed

- **Sidebar Truncation & Badge Alignment on Long Terms (`evelyn_ui/taxonomy.html`)**:
  - Fixed flex layout blowout on long terms (e.g. `convolutional-neural-network`) where child count badges (`5 kids`) wrapped into two lines and pushed occurrence counters out of alignment.
  - Enforced rigid flex badges (`.node-badges { flex-shrink: 0; white-space: nowrap; }`, `.badge-count { flex-shrink: 0; white-space: nowrap; }`) and graceful text ellipsis truncation (`.term-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }`) with full term name preserved in browser hover tooltips (`title="..."`).
- **Eliminated Horizontal Scroll on 4K TV & Scaled Displays (`evelyn_ui/taxonomy.html`)**:
  - Fixed horizontal scroll blowout on 3840x2160 displays with 250% scaling (effective 1536x864 viewport).
  - Replaced unconstrained CSS grid tracks with `minmax(0, 1fr)` and integrated CSS container queries (`@container inspector (max-width: 980px)`) and responsive breakpoint fallback (`@media (max-width: 1400px)`), allowing dual leaderboards to automatically stack into a clean, spacious single column when horizontal width is restricted.
  - Added `table-layout: fixed`, fixed column widths, and responsive horizontal wrappers (`.table-responsive`) to prevent table cell expansions from forcing grid overflow.
  - Hardened leaderboard card surfaces with opaque background styling (`#111827`) to eliminate ghosting and text bleed-through.

## [000.006.273] - 2026-09-27 — *Dynamic Category Intelligence Dashboard*

### Added

- **Dynamic Category Intelligence Dashboard & Tab (`evelyn_ui/taxonomy.html`)**:
  - Added dedicated `📊 Categories` navigation tab providing macro-level domain analytics without rigid pre-coordinate schemas.
  - **Dynamic Hierarchy Trunks Sidebar**: Interactive ranked list of all 69 dynamic root concept trunks computed on the fly from `master_tag_related`. Supports instant switching between sort by `🌿 Branches` (sub-concepts count) and `🔥 Usage` (total occurrences across Obsidian vault and Fast Memory).
  - **Executive Intelligence Overview**: Live metric cards displaying total dynamic trunks (69), deepest branching tree (`finances`, 6 sub-concepts), highest corpus activity (`values`, 828 occurrences), and total hierarchy links (263).
  - **Active Category Drilldown Card**: Displays deep branch breakdown, proportional usage bar distinguishing root tag occurrences from sub-concept occurrences, and interactive sub-concept chips with quick-action buttons to open the tree navigator or inspect the neighborhood.
  - **Dual Comparative Leaderboards**:
    - *Top Categories by Sub-Concepts (Depth & Breadth)*: Ranked table displaying sub-concept counts and interactive child badges.
    - *Top Categories by Corpus Activity*: Ranked table displaying total occurrences, visual distribution progress meters, and child branch stats.

## [000.006.272] - 2026-09-27 — *Purge Legacy Facet Aliases*

### Changed

- **Database Migration `migrate_000_006_272_purge_legacy_facet_aliases` (`vault`)**:
  - Purged all 97 legacy slashed facet entries (`tier = 'facet'` or `alias LIKE '%/%'`) from `master_tag_aliases` in `data/evelyn_vault.db` (e.g. `type/guide`, `type/journal-entry`, `type/log`, `type/profile`, `type/report`).
  - Enforced full decoupling of document classes and format facets (`type:`, `motif:`, `setting:`, `event:`) as native YAML frontmatter properties rather than subject aliases.
  - Invalidated in-process alias cache across taxonomy subsystems.
  - Enqueued Chroma deletions for all 97 purged alias forms in `evelyn_tag_taxonomy`, and enqueued 1,087 clean preferred/alternate surface forms via vector sync hook.
  - Established a **100% Zero-Slash Invariant** across `master_tag_taxonomy`, `master_tag_related`, and `master_tag_aliases`.

## [000.006.271] - 2026-09-27 — *Taxonomy Explorer & Hierarchy Graph UI*

### Added

- **Standalone Taxonomy & Hierarchy Explorer (`evelyn_ui/taxonomy.html`)**:
  - Built a dedicated dashboard for visualizing and curating Evelyn's post-coordinate subject taxonomy, decoupling tag curation from the cluttered `dev.html`.
  - **Dynamic Hierarchy Tree Navigator**: Computes top-level root concept trunks (`software-development`, `food`, `hardware`, `weather`, `pet`, etc.) and collapsible narrower branches directly from `master_tag_related`, abandoning stale static category strings.
  - **Peer & Equivalence Browsing**: Dedicated views for standalone independent peer atoms (`controlnet`, `biometrics`) and active alias mappings with instant client-side search.
  - **Interactive Neighborhood Inspector**: Displays broader parents, narrower children, related peers, and incoming aliases for any selected tag with one-click actions to reparent, link, sever, or flip directionality.
  - **Live Library of Congress & FAST Authority Dock**: Real-time integration to query `id.loc.gov` subject headings and promote authoritative terms and variants into the local SQLite taxonomy.
  - Connected direct cross-navigation between `dev.html` and `taxonomy.html`.
- **FastAPI Taxonomy Graph & Relationship Endpoints**:
  - `GET /api/taxonomy/graph`: Computes roots, relations, terms, and aliases in a single cached payload.
  - `POST /api/taxonomy/relation`: Adds, modifies, or deletes hierarchical (`narrower`) and associative (`related`) term links.
  - `POST /api/taxonomy/alias/flip`: Dynamically flips directionality between an alias and its canonical target.
  - `POST /api/taxonomy/alias/sever`: Liberates an alias into an independent canonical atom.
  - `GET /api/taxonomy/authority/lookup`: Proxies Library of Congress REST searches.
  - `POST /api/taxonomy/authority/promote`: Promotes authority concepts and variants directly into SQLite.

## [000.006.270] - 2026-09-27 — *Acronym Primacy & Inversion Flips*

### Added

- **Database Migrations `migrate_000_006_270_acronym_primacy_and_inversions_vault` and `memory`**:
  - **Acronym Primacy**: Promoted short, natural conversational acronyms (`ai`, `vr`, `ar`) to canonical status in `master_tag_taxonomy` per user operational preference, flipping long formal phrases (`artificial-intelligence`, `virtual-reality`, `augmented-reality`) into aliases.
  - **Singular Count Noun Inversion Flips**: Inverted historical plural tags to canonical singular count nouns (`boundary`, `musical-composition`, `automaton`), redirecting plural forms (`boundaries`, `musical-compositions`, `automata`, `automatons`) to aliases.
  - **Sever False Merges**: Liberated distinct concepts (`data-structures`, `plant`, `snack`, `romance`, `style`, `tagging`, `commerce`, `affection`, `vae`, `storytelling`, `reflection`) from `master_tag_aliases` into independent taxonomy entries and populated valid hierarchical/associative relationships in `master_tag_related`.
  - **Alias Collision Resolution**: Resolved collision on `organization` where an approved taxonomy term was previously trapped as an alias to `curation`.
  - **Substrate Synchronization**: Synchronized 115 notes in `vault_documents.tags` and 262 facts in `context_entries.tags` to adopt the canonical forms.
  - Invalidated alias caches and enqueued 1,184 updated surface forms into Chroma vector store.

## [000.006.269] - 2026-09-27 — *Sever False Tag Aliases & Restore Hierarchy*

### Added

- **Database Migration `migrate_000_006_269_sever_false_aliases_and_loops`**:
  - Severed 60+ false equivalence collapses from `master_tag_aliases` where narrower or distinct concepts were forcibly rewritten on save (e.g. `git`, `baking`, `manga`, `kitten`, `npc`, `sop`, `arch-linux`, `action-rpg`, `3d-scanning`, `meditation`, `poetry`).
  - Guaranteed each liberated concept exists independently in `master_tag_taxonomy` under its appropriate subject category.
  - Recorded legitimate parent-child hierarchies into `master_tag_related` with `kind = 'narrower'` (e.g. `git` narrower than `version-control`, `baking` narrower than `cooking`, `kitten` narrower than `cat`, `rain` narrower than `weather`, `npc` narrower than `ttrpg`, `sop` narrower than `procedures`), enabling relational navigation and vector retrieval without erasing specific search terms.
  - Preserved loosely associated concepts (`controlnet`, `biometrics`, `addressing`, `glasses`, `session`) as independent peer terms without forced hierarchical relations.
  - Eliminated 10 circular reverse loops (`basement <-> setting/basement`, `birthday <-> event/birthday`, `guam <-> setting/guam`, `kansas <-> setting/kansas`, etc.) where atomic terms had been pointed to slashed prefix forms.
  - Cleaned transitive alias chains for direct plurals and inflections (`holidays -> holiday`, `recipes -> recipe`, `flooding -> flood`, `rules -> rule`, `portals -> portal`).
  - Invalidated in-process alias caches and enqueued 1,183 updated tag surface forms into the Chroma vector sync queue.

## [000.006.268] - 2026-09-27 — *Decouple Doc Properties & Local Authority Tool*

### Added

- **`scripts/lookup_authority_taxonomy.py` (Local Authority Lookup & Promotion CLI)**:
  - Connects to institutional authorities (Library of Congress Subject Headings & FAST via `id.loc.gov` REST API) to search authoritative headings, preferred labels, and see-from (`UF`) variant aliases.
  - Supports `--term <name>`, `--promote` (registers canonical atom into `master_tag_taxonomy` and all variants into `master_tag_aliases`), and `--check-unregistered` (audits all terms in active use across vault and memory).
  - Fully local with zero git tracking, preserving privacy boundaries (AGENTS.md §4).
- **`format_librarian.audit_document_properties`**:
  - Validates `type:` property (document class) against `FACET_PROFILE`.
  - Enforces profile rules by stripping forbidden facet properties (`motif`, `setting`, `event` on reference/manual/guide notes).
  - Detects and reports gaps for required properties (`missing_required_occurred` on journal entries, `missing_required_motif` on dreams).
  - Normalizes legacy prefix values inside properties (e.g. `motif: [motif/flying]` -> `motif: [flying]`).

### Changed

- **Decoupled Document Properties from Tag Creation**:
  - Migrated `FACET_PROFILE`, `DOCUMENT_CLASSES`, `REQUIRED`, `OPTIONAL`, `FORBIDDEN`, and `get_document_class` to `Evelyn/tools/format_librarian.py` as canonical document structure definitions.
  - Updated `format_librarian.audit_document_format` to normalize all facet properties (`type`, `motif`, `setting`, `event`) into clean single-line flow arrays alongside `aliases` and `tags`.
  - Updated `master_librarian.py` to run both format normalization and document properties auditing in its initial pass.
  - Removed document frontmatter `type` property modification from `Evelyn/tools/tag_librarian.py`, ensuring tag auditing focuses exclusively on subject classification and subject tags (`tags: [...]`).
- **Memory Facts Audit Completion**:
  - Drained the remaining 101 unaudited memory facts via `scripts/backfill_memory_tags.py`.
  - All 12,847 memory facts are now fully audited (`0 awaiting audit, 0 untagged`).
- **Proposal Reference Cleanup**:
  - Cleaned up obsolete scratch reference documents in `proposals/Taxonomy_LIbrarian/`.

## [000.006.267] - 2026-09-27 — *Retire Tracked Taxonomy Base*

### Added

- **Database Migration `migrate_000_006_267_retire_base_taxonomy`**:
  - Unified all authority terms and see-from references directly into local `master_tag_taxonomy` and `master_tag_aliases` tables in `data/evelyn_vault.db`.
  - Copied all 80 authority see-from aliases from `base_tag_aliases` into `master_tag_aliases` before table retirement (bringing `master_tag_aliases` to 409 total registered aliases).
  - Dropped obsolete `base_tag_taxonomy`, `base_tag_aliases`, and `base_taxonomy_meta` tables from `evelyn_vault.db`.
  - Enqueued updated taxonomy surface forms into Chroma sync queue.

### Changed

- **Privacy Boundary & Vocabulary Decoupling**:
  - Retired and removed the tracked `taxonomy/base.json` and loader `Evelyn/tools/taxonomy_base.py`. Controlled vocabulary terms are maintained strictly within local, gitignored SQLite databases to eliminate any profile/selection bias leaks in git history (per AGENTS.md §4).
  - Simplified `Evelyn/tools/taxonomy_db.py`: `get_master_tags()`, `delete_master_tag()`, and `get_aliases()` query solely from `master_tag_taxonomy` and `master_tag_aliases` without querying dropped base tables.
  - Simplified `Evelyn/tools/tag_librarian.py`: Removed base term retirement refusal checks in `retire_term()`.
  - Simplified `evelyn_server.py`: Pruned startup base vocabulary loader and sync hooks.
  - Updated `Evelyn/tools/journal_manager.py` and `Evelyn/tools/dream_manager.py`: Directly set frontmatter property `type: ["journal-entry"]` and `type: ["dream"]` with atomic tags.
  - Updated `Evelyn/tests/test_see_reference_action.py`: Pointed see-reference tests to `taxonomy_db.upsert_master_tag`.
  - **Prompt & Docstring Taxonomy Alignment**: Audited all tag writers and modifiers (`fact_extractor.py`, `fact_deduplicator.py`, `fact_splitter.py`, `evelyn_tools.py`, `dream_manager.py`, `journal_manager.py`, `tag_librarian.py`), explicitly reinforcing the Zero-Slash Invariant, singular noun format, and atomic coordinate examples in model prompts and docstrings.

## [000.006.266] - 2026-09-27 — *Zero-Slash Facet Properties*

### Added

- **Native YAML frontmatter properties for non-subject facets**:
  Non-subject facets (`type`, `motif`, `setting`, `event`) are formally migrated out of `tags: [...]` into dedicated frontmatter properties formatted as single-line flow arrays (e.g. `type: [reference]`, `motif: [combat, flight]`, `setting: [urban]`, `event: [surgery]`).
- **`migrate_facets_to_properties.py` vault migration**:
  Scanned and updated all 4,394 vault notes with atomic single-line flow arrays. Set 4,392 `type` properties, 115 `motif` properties, 87 `setting` properties, and 50 `event` properties. Saved backup manifest to `data/backups/`.
- **Database Migrations `migrate_000_006_266_zero_slash_vault` and `migrate_000_006_266_zero_slash_memory`**:
  - Vault DB: Flattened 97 prefix terms in `master_tag_taxonomy` (`event/*`, `motif/*`, `setting/*`, `type/*`) into atomic coordinates while recording equivalence aliases in `master_tag_aliases`. Stripped prefix slashes across `vault_documents.tags`.
  - Memory DB: Stripped prefix slashes across `context_entries.tags` and operational `procedures.tags`. Enqueued 1,149 taxonomy surface forms into Chroma sync queue.

### Changed

- **Zero-Slash Invariant on Tags**:
  `tags: [...]` in both Obsidian vault notes and database rows (`context_entries.tags`, `vault_documents.tags`, `procedures.tags`, `master_tag_taxonomy`) now strictly contains atomic lowercase subject nouns matching `^[a-z0-9-]+$` with zero slashes.
- **Tag Librarian & Downstream Pipelines**:
  - `Evelyn/tools/tag_librarian.py`: `apply_application_profile` drops any legacy facet prefix tags from `tags: [...]` and enforces frontmatter properties. `is_wellformed_term` strictly rejects slashes.
  - `Evelyn/tools/link_librarian.py`: Stubs now use frontmatter property `type: [stub]` with empty/atomic subject tags. `_is_stub_note` inspects the `type` property with legacy fallback.
  - `Evelyn/tools/pdf_staging_worker.py`: Generated sidecar notes assign `type: [media]` as a frontmatter property and keep `tags: [...]` strictly atomic.
  - `Evelyn/tools/frontmatter_utils.py`: Added `type`, `motif`, `setting`, and `event` to `ARRAY_KEYS` to guarantee single-line flow array rendering.

## [000.006.265] - 2026-09-27 — *One More Literal That Outranked The Config*

### Fixed

- **`TAG_LIBRARIAN_ENABLED` is read from the environment.** `.255` wired six feature flags
  through `_env_flag()`; this one was not among them, because it was not in `.env` at the time
  and so never showed the disagreement that exposed the others. Setting it did nothing until
  now. Documented in `.env.example` alongside the rest.

### Changed

- **The tag-admission queue was cleared: 257 proposals removed.** Every one was produced by
  the extraction prompt replaced in `.264`, which nominated what a document mentioned rather
  than what it was about. They were stale output rather than a review backlog, and the
  reviewer would have been ruling on terms the current pipeline would not propose.

  Sequence: pause the producer, snapshot all 257 to `scratch/`, delete, resume. Relation,
  split and ghost-stub proposals were left alone — different producers, unaffected by the
  prompt.

## [000.006.264] - 2026-09-27 — *About, Not Mentioned*

### Fixed

- **Subject extraction asks what a document is about, not what it mentions.** The instruction
  was *"List the distinct subjects this text covers"* — a noun-phrase extractor. It was
  behaving correctly: `Arby's` yielded `curly fries`, `Elden Ring` yielded `journaling` and
  `data taxonomy` off its own citation lines, `Tyler Bates` yielded five ways of saying film
  composer. Nothing in it separated aboutness from mention, so the largest producer in the
  pipeline was nominating most of the nouns in every note it read.

  The replacement states no new policy. It states what `vault-tag-taxonomy.md` had already
  decided and the prompt had never been told: **§6.3.3** (a category is earned in the prose,
  not by a passing mention), **§3.3** (one concept, one tag — the vocabulary recombines at
  query time, so no pre-composed compounds), and **§2** (proper nouns are never subjects).
  Plus two practical rules: ignore the document's own citation furniture, and do not return
  several near-synonyms for one subject.

  Measured over 12 random notes: **81 phrases before, 27 after — 67% fewer**, with the
  survivors being the right ones.

```
Arby's       curly fries, dining establishments, fast food        ->  fast food
Elden Ring   video games, game mechanics, dark souls, teyvat,     ->  video games
             greedfall, data taxonomy, journaling
Tyler Bates  film scoring, musical composition, orchestral        ->  film scoring, composition
             music, vocal performance, musical aesthetics
```

### Notes

- 3,394 of 4,381 vault documents have never been through this pass. At the old rate the
  remaining backlog projected to roughly 475 further admission proposals; at the new one it
  is nearer 155.

## [000.006.263] - 2026-09-27 — *Terms Arrive In Batches, Not Alone*

### Added

- **An admission card now shows what else its source is asking for, and what that source
  already carries.** Terms are produced in batches — one pass over one note or one fact emits
  several — but reviewed one at a time, often an hour apart. Measured over a live queue of
  200: **139 cards (70%) had at least one sibling**, and the siblings are frequently where the
  judgement is.

  `Scooter's Coffee` asked for `social-gatherings` and `social-interaction` in the same batch.
  Read together that is one obvious duplicate; read an hour apart they are two reasonable
  terms. `Amazon` asked for five near-synonymous commercial terms at once.

  The card gains two rows of chips — **the source already carries** and **also proposed from
  the same source** — plus a warning when the proposed term restates a tag the source already
  holds. `private-intimacy` was proposed against a fact already tagged `intimacy`, alongside
  `public-persona` against the same fact's `persona`: that is pre-coordination (§3.3), not a
  new coordinate, and the vocabulary recombines existing terms at query time instead.

### Changed

- Near-match and sibling chips share one `chip()` helper rather than two copies of the same
  inline markup.

### Notes

- Both the note-tag lookup and the grouping run once per queue, not once per card: a single
  `IN (…)` query over `vault_documents` covers every note in the response.

## [000.006.262] - 2026-09-27 — *The Note Moved, It Did Not Vanish*

### Fixed

- **A review card follows its source note when the note has been refiled.** Measured against
  the live queue: **94 of 200 admission cards showed no source text at all**, and 91 of those
  had a note that still existed — under a new folder, after the stub reorganisation. Only 3
  were genuinely gone.

  This is the same defect as `.253` in a different consumer. A row stores the path its note
  had when the row was written, and the vault is refiled by hand; `.253` made the *write* path
  report the miss, while the *read* path kept failing silently and handing the reviewer a
  blank card.

  New `path_utils.resolve_moved_note()` follows a moved note by filename and requires a
  **unique** match — two notes sharing a name are genuinely ambiguous, and attaching the wrong
  evidence to a decision is worse than attaching none. The vault index behind it is cached for
  60 seconds, long enough to serve one render of a 200-row queue. Blank cards **94 → 3**.

### Changed

- **The "Why it is here" block is gone from admission cards**, and the one useful line it held
  moved into the header: *Term to admit — requested by `fact merge into #1035`*. The block had
  four distinct values across the whole queue, every one of them "\<producer\> but not in the
  controlled vocabulary" — true of every card by definition, and the producer is now stated
  above. Retirement cards keep the block, where the reason is not boilerplate.

### Notes

- Where a term was derived semantically rather than lifted from the text, no sentence contains
  it, so the recovered excerpt falls back to the note's opening. That identifies the note and
  its subject, which is enough to rule on the term, but it is not the term in context — and
  nothing can quote a sentence that was never written.
- `backfill_admitted_term_to_note` resolves the same stored path and has warned on a miss since
  `.253`. Those warnings are very likely the same moved notes, but reading from a note resolved
  by name is safe where **writing** to one is not obviously so, and that is left as its own
  decision.

## [000.006.261] - 2026-09-26 — *Stop Asking For What Nothing Reads*

### Changed

- **The review card no longer asks for a category.** A term's `category` is consumed by
  nothing: it is deliberately excluded from the tag's embedding vector (the labelled-prose
  format it belonged to made 31.5% of terms fail to match themselves, fixed in `.221`-`.224`)
  and it reaches no prompt. Its only readers were a dropdown and a sort order.

  It also drifted into an uncontrolled vocabulary inside the controlled one — 21 values with
  `work-civic` (35) beside `professional-civic` (1), `domestic-life` (34) beside
  `making-home-improvement` (20) beside `home-projects` (3), and three singletons. The single
  largest value, `reference` (167), is not a subject domain at all.

  It traces to `f8fceeb`, the original faceted-classification migration, which predates the
  post-coordinate revision: it is the last surviving piece of the hierarchy that was removed.
  The job it appeared to do is already done twice over — facet prefixes state the axis, and
  the relations table states association and hierarchy, many-to-many and already reviewed.

  **Collection stops; the data stays.** The column and its 643 assignments remain, because
  they convert directly into `narrower` relations (`sleep narrower health-body`) and deleting
  them before that decision is made would discard the one useful thing the column produced.

- **The admission card now says what the term box can do.** It was already editable and its
  contents were already what got registered — including a facet prefix, so `setting/bedroom`
  registers even though no such term exists yet. Nothing said so, and a reviewer reasonably
  concluded they could only pick from existing terms. The hint is on the card and in the
  `? Hint` modal.

### Notes

- `GET /api/taxonomy/vocabulary` still returns `categories`, and
  `register_admitted_term(category=...)` still accepts one. Removing those, the column and the
  field in `taxonomy/base.json` is queued separately, gated on deciding whether to convert the
  existing assignments into relations first.

## [000.006.260] - 2026-09-26 — *A Broken Instrument Reads Worse Than None*

### Fixed

- **Context entries now carry their tags into the vector store.** `sync_memory_collection`
  wrote `subject` and `category` into a context entry's Chroma metadata but never `tags`,
  although the row has them and they were already being written into the chunk *text*. Vault
  notes have carried the key all along, so anything reading tags at query time was blind to
  **73% of the collection** — the part that dominates retrieval. One line, plus a re-ingest of
  the 10,182 affected entries through the normal staging queue.

### Changed

- **`benchmark_rag.py` refuses to score a golden set marked `"status": "stale"`**, printing the
  set's own recorded diagnosis and exiting non-zero. `reference/rag_benchmark_queries.json` is
  marked stale; its 25 queries are preserved inside the marker for whoever rebuilds it. Both
  the legacy bare-list shape and a wrapped non-stale set still load normally.

  It scored **1/25** against the live corpus, and the single hit was a negative query passing
  by returning nothing. Five defects, recorded in the file itself:

  1. **The matcher cannot see most of the corpus.** It compares `os.path.basename(source)`,
     but a context entry's source is `sqlite::context_entry::5830` — basename returns that
     unchanged and it carries no category, title or tag.
  2. **Queries name people who do not exist.** They were genericised for the public repo; the
     corpus was not.
  3. **Expectations that cannot be satisfied** — a category summary that was never created, a
     note deleted earlier the same day.
  4. **One expectation the engine is right to refuse** — a `sensitivity: private` note, which
     policy says must never reach RAG.
  5. **The ground-truth model does not match the data.** Repairing the matcher does not rescue
     it: facts about one topic are spread across nine categories, so "expect category N" is a
     false premise. This is why it needs rebuilding rather than fixing.

  A benchmark that returns a number while measuring nothing is worse than no benchmark,
  because the number gets quoted. Rebuilding it belongs with the model-alignment benchmark
  work, not with retrieval patches.

## [000.006.259] - 2026-09-26 — *Built, Measured, Left Off*

### Added

- **Query-time expansion over the curated tag relations (§6.4), behind a flag that stays
  off.** Post-coordination atomised `health/sleep` into `health` + `sleep` and with it the
  only statement that the two belong together; the relations table has been the sole home of
  that knowledge and nothing read it at retrieval. `_apply_relation_boost()` now can.

  It **re-ranks what was already retrieved** rather than issuing a second search. The caller
  over-fetches so a chunk can be promoted into the kept set, but the pool stays bounded and a
  poor relation costs ordering rather than correctness. Seeds are the tags of the strongest
  initial results, not terms matched out of the query text — query-word matching was tried
  and rejected, because the vocabulary holds ordinary English words and "the rest of the
  files" matches the subject `rest`.

  `RAG_RELATION_EXPANSION_ENABLED` (default **false**), `RAG_RELATION_OVERFETCH`,
  `RAG_RELATION_SEED_K`, `RAG_RELATION_BOOST_CAP`.

### Notes

- **The measurement says do not turn it on, and that is the result of this release.** On 12
  queries chosen to reach tagged content, with fetch size held constant so the re-rank is
  isolated: it fired on **2**, changed the top-6 on **2**, and promoted **2** documents. Of
  those two promotions one is a clear regression — for "how does retrieval augmented
  generation work", it promoted a bibliography page over the note that answers the question,
  because the bibliography carries `rag`, `llm` and `information-retrieval` and tag density
  outweighed semantic distance. The other swapped two comparable notes and demoted the one
  carrying the query's own words. **None were improvements.**
- **Three things would have to change before this is worth enabling**, and they are findings
  in their own right:
  1. **Context entries carry no tags in Chroma metadata**, though they carry them in SQLite.
     They are 54% of the collection and dominate the top of the ranking, so the seed set is
     usually empty: 10 of 12 queries produced no seeds at all.
  2. **156 relations over 749 terms is sparse.** Widening the seed window lifted firing from
     2 to 9 while leaving reordering and promotions at 2 — the extra firings were no-ops, so
     the conservative seeding was kept.
  3. **Boost strength scales with the number of matched tags**, which is exactly what favours
     tag-dense, content-poor reference pages.
- **`scripts/benchmark_rag.py` cannot measure this, and is itself stale.** Its golden set
  scores 1/25 overall — the single hit being a negative query that passes by returning
  nothing — because its expected sources are placeholder names that do not exist in this
  vault. Relations on versus off produced byte-identical output on it.

## [000.006.258] - 2026-09-26 — *A Stub Is Not A Witness*

### Fixed

- **Stub evidence no longer counts other stubs.** `harvest_entity_references` gathered
  context from every note citing a target, including notes tagged `type/stub`. A stub's
  `Context & Mentions` section is itself made of excerpts harvested from elsewhere, so
  quoting one counted a single original source twice — and a stub quoting a stub that quoted
  a stub compounded, which is how 307 excerpt-truncation artefacts spread through 134 notes
  before `.257`.

  Harvesting now skips stub notes, keyed on the `type/stub` tag rather than the folder,
  since stubs are filed and refiled by hand: `Stubs/` is where they usually live, not what
  they are. The pre-existing self-reference guard is unchanged.

### Notes

- Measured against the current vault: **176 of 269 existing stubs still clear the evidence
  bar on independent sources alone; 93 do not.** That number is an upper bound rather than a
  verdict — it re-harvests each stub by its filename, so a note whose inbound links spell the
  target differently (`[[Super Smash Bros.]]` against `Super Smash Bros.md`) reads as having
  no evidence when it merely has a name the filesystem could not keep. Nothing was deleted or
  flagged for deletion on this measurement.

## [000.006.257] - 2026-09-26 — *Do Not Cut A Link In Half*

### Fixed

- **Context excerpts no longer end mid-wikilink.** An excerpt is a fixed-width slice around
  a match, written verbatim into any stub note built from it. Two separate steps were
  leaving an unclosed `[[` in that text, and an unclosed `[[` runs on into whatever follows
  it — the excerpt's closing quote, and then the next reference's own link.

  - **The trailing punctuation strip was the larger source.** `]]` is entirely non-word
    characters, so a blanket `[\W_]+$` turned a perfectly well-formed `… [[Beat Saber]]`
    into `… [[Beat Saber`. The excerpt manufactured the defect even when the slice had been
    cut cleanly. The strip now leaves a terminal `]]` alone.
  - **The slice itself cut mid-link**, at either end. New `string_utils.trim_partial_wikilinks()`
    drops a leading orphan `]]` and a trailing unclosed `[[`, and runs both on the raw slice
    and again after cleaning, since the strips can expose a fragment the first pass could
    not see.

  Measured before the fix: **307 unclosed fragments across 134 stub notes**, concentrated in
  the deepest stub folders. Because stub excerpts are harvested from other notes — including
  other stubs — the damage compounded with each generation.

### Notes

- Six further hits in imported reference material are **not** this defect and were left
  alone: they are Python list literals (`[[0.0053587136790156364, …`) flattened out of code
  blocks by PDF ingest, which no wikilink rule should touch.
- The existing damage was repaired by **removing the stray `[[`**, not by restoring the `]]`
  the strip had eaten. Which of the two causes produced any given fragment is not reliably
  recoverable after the fact, and inventing a closing bracket would add links to the vault
  on a guess. Removing the fragment restores the text either way.

## [000.006.256] - 2026-09-26 — *How To Decide, At The Moment Of Deciding*

### Added

- **A `? Hint` button on every proposal card**, opening the questions that apply to that
  proposal type. The guidance existed — §6.4.1 of the taxonomy standard — but it lived where
  nobody reads it at the moment a decision is actually made. The reviewer met a card with a
  term, a category, near-matches and two buttons, and nothing on it said *how* to decide.

  Five types are written: `tag_admission`, `tag_retirement`, `tag_relation`, `split` and
  `ghost_link_stub`. Two constraints shaped the text, both from review:

  - **It leads with legitimacy, not reversibility.** The reviewer's question is "is this
    real, or is it noise?", not "how bad is it if I'm wrong?" — a reviewer handed the second
    question decides on risk appetite and keeps everything. Cost appears only where it
    genuinely distinguishes one option from another (retiring a term, collapsing two into
    one, a rejection that binds the word rather than the instance).
  - **It uses no term of art.** No *warrant*, *facet*, *post-coordinate*, *BT/NT/RT*. It is
    written for someone who has downloaded this project and never read the standard.

- **Autocomplete on the see-reference and preferred-term inputs**, backed by
  `/api/taxonomy/vocabulary?full=true`. Both fields may only name a term that is already
  registered — the server refuses an unknown one — but with 749 terms and no way to see
  them, a reviewer's only route to that knowledge was to type a guess and be refused. The
  field now completes as you type, so a term that does not exist is visibly absent before
  anything is submitted. A failed fetch leaves a plain text field, which still works.

### Changed

- `GET /api/taxonomy/vocabulary` takes `full` (default false). When set it adds `terms`, the
  whole registered vocabulary sorted by tag. The near-match and category behaviour is
  unchanged, so existing callers see the same payload.

## [000.006.255] - 2026-09-26 — *A Literal That Outranked The Config*

### Fixed

- **Six feature flags set in the environment are now actually read.** `CONSOLIDATION_ENABLED`,
  `FACT_EXTRACTION_ENABLED`, `PROFILE_EVOLUTION_ENABLED`, `AUTO_JOURNAL_ENABLED`,
  `AMBIENT_REFLECTIONS_ENABLED` and `MASTER_LIBRARIAN_ENABLED` were literals in
  `evelyn_config.py` with no `os.getenv` anywhere, so setting them in the environment did
  nothing. Five coincided with their hardcoded default, which is why the group went unnoticed
  for so long — the class of defect only surfaced on the one flag where the two disagreed, and
  there an autonomous pass stayed off while the configuration said it was on.

  All six now read through a new `_env_flag()` helper. It accepts `true/1/yes/on` and
  `false/0/no/off`, tolerates surrounding quotes and whitespace, and **falls back to the
  default on anything it cannot parse** rather than guessing — a typo must not switch on a
  pass that rewrites notes unattended.

### Changed

- **`.env.example` documents all six**, with the accepted spellings and a note that the
  librarian rewrites vault notes without asking and should be dry-run first.

### Notes

- **No behaviour changes with this release.** The wiring landed with the master librarian
  explicitly disabled, so the effective configuration is identical to `.254`. Enabling that
  pass is a separate, deliberate decision.
- `test_feature_flag_env_wiring.py` (39 tests) pins each flag to an environment read, checks
  both spellings and the unparseable-value fallback, and fails if a documented key is ever
  left unread again. Verified red against the previous literal before being committed green.

## [000.006.254] - 2026-09-26 — *An Alias Is Not A Link*

### Fixed

- **A stub filed under a sanitised name now rewrites the links that pointed at the old one.**
  A wikilink may contain characters a filename may not, so `[[Nier: Automata]]` is stored as
  `Nier Automata.md`. Both stub writers recorded the original spelling as a frontmatter alias
  and stopped there, on the stated belief that "carrying it as an alias is what keeps those
  links resolving to this note."

  That belief is wrong. Obsidian resolves a wikilink against filenames only; an alias makes a
  note *findable while typing* — it inserts `[[Real Name|Alias]]` — and never makes a bare
  `[[Alias]]` resolve. So every such link was permanently unresolved, and the note it should
  have pointed at was an orphan. Because the name can never exist as a file, the link could
  never be repaired by creating one.

  `link_librarian.retarget_inbound_links()` now rewrites them, preserving the reader's words
  in every case (`[[Nier: Automata]]` → `[[Nier Automata|Nier: Automata]]`, a piped link
  keeps the author's display text, subpaths and casing variants survive). It is called from
  the Tier 1 auto-synthesis path and from the Tier 2 approval route, and the approval response
  says how many notes it touched. The alias is still written — it earns its place in search
  and the quick switcher — but it is no longer mistaken for a link.

  `string_utils.is_filename_safe()` is the canonical predicate, defined as a round-trip
  through `sanitize_filename`, and replaces the hand-rolled comparison in the stub scaffold.

### Notes

- Two comments asserting the false premise (in `render_stub_markdown` and `stub_relpath`) are
  corrected in place rather than deleted, since both name the real constraint that produced
  the sanitised filename — Windows cannot represent `:` and Syncthing refuses to sync it.

## [000.006.253] - 2026-09-26 — *Admitted Is Not The Same As Applied*

### Fixed

- **An admission whose note cannot be updated now says so.** A proposal stores the path its
  note had when it was raised, and a vault is a live filesystem. Of 11 admissions applied on
  2026-09-26, **seven reached nothing** — the notes had been refiled between the proposal and
  the decision — and every one of them returned `ok`. `backfill_admitted_term_to_note` has
  always returned whether it wrote anything and the caller discarded it, the same shape as
  the deny no-op fixed in `.244`.

  The result is **reported, not enforced**: the term is genuinely admitted either way, so
  failing the approval would be wrong. The response carries a `warnings` list, the bulk route
  carries it per row, and the server logs each one — because a decision that succeeds while
  leaving something undone is exactly what needs saying out loud.

### Tests

- `test_admission_context.py` grows to 11: an admission against a note that is no longer
  there still admits the term **and** reports the note it could not update.

## [000.006.252] - 2026-09-26 — *Where It Is Mentioned Is Not What It Is*

### Fixed

- **Stubs are no longer filed by the folder their referencing notes live in.** The heuristic
  produced the same defect twice under two different signals: the first voted on co-linked
  notes and filed a country under Contacts at 89% confidence; its replacement voted on the
  top-level folder of the referencing notes and filed a **holiday** under Contacts, because
  contact notes were where it happened to be mentioned. Both measured *where a thing is
  talked about* and reported it as *what the thing is* — a different question, not a weaker
  signal. The lesson had already been written into the module docstring after the first
  attempt and was re-learned against the second, so `infer_stub_domain` is now a no-op that
  carries the reasoning, and stubs land unsorted in `Stubs/` for the reviewer to file.
- `LIBRARIAN_STUB_DOMAIN_FOLDERS` removed with the heuristic it fed. It was also a second
  place one operator's folder names lived in tracked config.

### Documentation

- **§2.1 — the substrates that cannot hold a link.** §2 says a tag naming an individual is a
  defect "converted to a link, not preserved", and that has a destination in the vault and
  nowhere else: memory facts and journal entries deliberately carry no wikilinks, because
  link syntax in raw text logs muddied the context those records exist to supply. A memory
  fact naming an individual therefore has no legal move under §2 as written — not a tag, and
  not a link.

  That gap is what the name register fills, and §2.1 states its role: a **pointer, not an
  authority**. Every row carries the vault path of the note that *is* the authority, and a
  row with no note is evidence a stub should exist rather than a second record. The section
  carries an explicit warning, because the register looks redundant from §2 alone and was
  argued to be on the day it shipped; the argument fails on the substrate rule, and the
  change that would genuinely retire it is permitting links in `context_entries` — which
  needs verifying against retrieval quality first.

### Tests

- `test_master_librarian.py`: the stub-domain test is replaced rather than fixed — it encoded
  the behaviour being reversed — by one asserting that **even a unanimous folder majority is
  not evidence about the subject**, while an explicit domain from a caller that actually
  knows is still honoured.

## [000.006.251] - 2026-09-26 — *Your Folders Are Not Everyone's*

`TAG_LIBRARIAN_EXCLUDED_PREFIXES` held two different kinds of fact in one tracked list. That
`Templates/` is not a source of subjects is true of any vault built on this engine. That
`Reference Library/` is not either is a statement about exactly one person's vault — and it
was shipping to everyone who clones the repository. Same split as the taxonomy's base and
local layers, for the same reason.

### Changed

- **Two layers.** `VAULT_STRUCTURAL_IGNORE` stays in the tracked config: properties of the
  layout any clone has. `VAULT_USER_IGNORE` comes from the gitignored `.env` via
  `EVELYN_VAULT_USER_IGNORE`, comma-separated, with `{USER_NAME}` / `{ASSISTANT_NAME}`
  interpolated so a path can name its owner without that name entering version control.
  `TAG_LIBRARIAN_EXCLUDED_PREFIXES` is their union, so every existing caller is unchanged —
  the split lives in the definition, which is where it matters for what ships.
- **The assistant's own context folder joins the structural layer.** Those notes are *aspect*
  documents about the two people — "Core Identity", "Emotional States & Responses" — so a
  pass reading them for subjects harvests document titles rather than names. Measured
  2026-09-26: 30 such notes typed as profiles. Every clone has this folder, so it is
  structural rather than personal.

### Added

- `EVELYN_VAULT_USER_IGNORE` documented in `.env.example`, including that exclusion is from
  the tag audit only — excluded notes stay indexed and fully retrievable.

### Tests

- `test_vault_ignore_layers.py` (5): the layers combine, **the tracked layer names nobody**
  (parsed from source, so a personal path written there fails the suite), a user entry
  interpolates its owner, a blank env var does not become a prefix matching every path, and
  the audit queue honours both layers.

## [000.006.250] - 2026-09-26 — *A Name Is Not A Subject*

A proposed term that turns out to be a name had **no correct action**. Admitting it puts a
shop, a product or a person into a controlled vocabulary of subjects, where the classifier
then applies it to unrelated notes. Rejecting it is permanent since `.231` and is scoped to
the *word* — so turning down a clothing retailer whose name is an ordinary adjective would
spend that adjective for good. Reviewing could not clear those proposals however long it ran.

Authority control settled this long ago by keeping the registers apart — LCSH beside LCNAF,
and four of FAST's nine facets are name facets. Separate namespaces are what let a name and a
subject share a string without competing for the same slot.

### Added

- **A name register** (`tag_entities`, migration `000.006.250`) and a third action on the
  admission card: **"This is a name"**. It records the term, closes the proposal as decided,
  and keeps it out of the subject vocabulary. It is **not** a rejection: the word stays
  admissible, and the record is reversible.
- **A `label` field**, because the term is often lossy — one shop reached the queue as a
  single word, the rest of its name dropped by the split that extracted it. The register is
  where it goes back, along with a `kind` (organization, product, work, person, place).
- `GET /api/taxonomy/names` and `POST /api/taxonomy/names/{term}/forget`. The card promises
  the decision can be undone; without a route saying so that would be a promise the code does
  not keep.
- Local and gitignored by construction: this is the one taxonomy table that can hold personal
  names, so it must never reach the tracked base vocabulary (§4).

### Changed

- `propose_tag_admission` no longer re-proposes a registered name, and **counts each further
  request** instead of going quiet. A name the corpus keeps nominating is either one it keeps
  mentioning, which is expected, or a word that also has a subject sense the vocabulary is
  missing — only the number tells those apart, the same reasoning as `record_rejected_request`.

### Tests

- `test_name_register.py` (9): the proposal closes and the name registers, the term does not
  enter the subject vocabulary, it is not proposed again, further requests are counted, **the
  word survives and becomes proposable once the name is forgotten** — which a rejection would
  never allow — the full name and kind are kept, re-recording does not blank a label, only an
  admission can be named, and the bulk route carries it.

## [000.006.249] - 2026-09-26 — *Show The Sentence*

A tag admission card offered a bare word and an origin label — `split fact (Cat05-U)` — and
asked for a permanent decision. That label names a category, not a sentence, and it settles
nothing. One term in the queue names a clothing retailer in this corpus, an orchid genus in
FAST, and a novel everywhere else; the term cannot distinguish them and no amount of
measurement gets there. The single sentence it was extracted from — which named it alongside
another online shop — settles it at a glance.

### Added

- **"Where it came from" on every admission card**, above the term's own evidence, because it
  is what the decision actually rests on. Memory-sourced proposals show the observations of
  the facts that asked (up to four, with ids and categories); vault-sourced ones show the
  lines of the note that mention the term, with its path. Every word of the proposed term is
  highlighted in the text.
- `tag_librarian.excerpt_for_term()`. Matching is deliberately loose — the term is a
  normalised form (`color-code-theory`) while the note holds prose (*"the Color Code
  Personality Assessment"*) — so lines are ranked by how much of the term they account for
  rather than being required to contain it whole. Callouts, tables, headings and index links
  are skipped: a reviewer shown an `[!abstract]` banner learns nothing the card did not
  already say. A note that turns out not to mention the term still returns its opening prose,
  because some context beats none.
- The excerpt is cached on path and mtime, so a queue of 200 costs one pass over the notes
  rather than one per render, and it carries the **same traversal guard as the backfill** —
  review context must not become a file-read primitive.

### Verified

- **The `.245` deferral held in production, on its first real exercise.** The queue reached its
  200 cap this afternoon and deferred rather than disarming: 198 pending, **67 deferred**,
  logged term by term. Before `.245` those 67 would have gone back onto facts unregistered and
  unreviewed, silently, which is the drift that took a hand curation pass to undo.

### Tests

- `test_admission_context.py` (8): the mentioning line is returned and the unrelated one is
  not, prose is matched rather than the normalised form, note furniture is skipped, a path
  escaping the vault is refused, a missing note is empty rather than an error, the excerpt is
  cached on mtime, a note without the term still yields something, and the unified review
  builder actually reaches the helper.

## [000.006.248] - 2026-09-26 — *Eighty Equivalences We Did Not Have To Argue About*

### Added

- **Inherited see-from references on the base vocabulary: 80 aliases across 103 terms**
  (AAT 29, FAST 20, MeSH 19, LCGFT 12). `feelings ➔ See: emotion`, `sleeping ➔ See: sleep`,
  `workouts ➔ See: exercise`, `ei ➔ See: emotional-intelligence`, `japanimation ➔ See: anime` —
  each one a term the corpus would otherwise have minted and a reviewer would otherwise have
  had to rule on. The vocabulary now resolves 251 equivalences, up from 171.

  This is the **safe direction** and the only one taken automatically: adding a pointer *to* a
  base term retires nothing. Redirecting one of our own terms onto an authority's preferred
  form stays a proposal (`.247`), because that choice is made for a published collection and
  can be wrong here.

### Changed

- **27 pending admissions approved** against the authority method, through the bulk route so
  each carries a proposal row and a decision rather than appearing from nowhere. All 27
  reached the fact that asked for them, verified per term. 10 were held back with reasons:
  four from the clinical subtree excluded in `.246`, one near-duplicate of another term in the
  same batch, and five whose only authority match is sense-qualified.

### Fixed

- **A qualified authority label is not an exact match.** The coverage probe normalised
  `Dracula (Plants)` — an orchid genus — to `dracula`, the same way it had matched
  `Endurance (Ship : A171)` before the facet filter. A term whose only authorised form carries
  a parenthetical qualifier is a *sense-specific* heading, and stripping the qualifier asserts
  a match the authority never made.
- **The harvester's guards, which fired.** One inherited alias was already a registered term;
  adding it would have silently retired that term. Aliases colliding with another base term,
  with a conflicting local alias, or carrying pre-coordinate `--` strings are dropped too.
- **AAT needs its language tag.** Without `@en` the literal does not match at all, which is why
  23 AAT terms first yielded nothing; and because AAT is multilingual the results must be
  filtered to English, or the vocabulary inherits `Bekleidung (Mode)` as a see-from reference.
  MeSH has no flat entry-terms route — `lookup/terms` serves HTML — so entry terms are read
  from the descriptor's concept chain.

## [000.006.247] - 2026-09-26 — *We Already Have A Word For That*

The admission card's own advice was: *"If one of these already covers it, **Reject** — the
vocabulary is post-coordinate, so a near-duplicate splits the concept in two."* But `_deny`
called `reject_proposal` and nothing else, and the whole server held exactly one
`record_alias`/`retire_term` call, in the `tag_relation` branch. **The admission path could not
record an equivalence at all** — so the card broke the rule it was citing (§6.2: *a deleted
synonym with no `UF` record will be re-minted by the next import*). Since `.231` the rejection
does stop the term returning, so nothing was re-minted; the equivalence was simply lost, and
retrieval never learned that the two words mean the same thing.

### Added

- **A third verdict on an admission: `POST /api/review/proposals/{id}/alias`.** Records the
  `UF` equivalence, puts the *preferred* term onto whatever asked for the unused one, and
  closes the proposal as applied. Reject keeps its old meaning — "not a subject at all".
  Available through the bulk route too, which is where a flood of near-duplicates lands.
- **The `➔ See:` control on the admission card** (§6.2.1), pre-filled with the nearest
  registered term and **editable**, because an authority's preferred form is a cataloguing
  decision made for a published collection and can be wrong for a personal one: of the
  see-references measured on 2026-09-26, `ancestry ➔ genealogy` and `version-control ➔
  revision control` were right, while a pet's name was redirected onto the animal and
  `survival-game` onto `Paintball (Game)`. The card states the consequence in §6.2.2's words
  rather than naming the mechanism.

### Changed

- The similar-terms hint no longer tells reviewers to reject a near-duplicate; it points at
  the see-reference instead, since rejecting alone discards the equivalence.
- **The target is resolved through existing equivalences before being recorded.** Resolution
  is transitive at read time, so a chain would still resolve — but it would store a target
  that is itself retired, and the alias table stops being readable as "what does this term
  mean now". A resolution that lands back on the proposed term is refused as a cycle.
- The target must be a term the vocabulary actually holds — base layer included — or the
  reference points at nothing and every tag resolving through it lands on an unregistered
  word, which is the state admission exists to prevent.

### Tests

- `test_see_reference_action.py` (8): the equivalence is recorded and the proposal closed, the
  fact that asked gets the preferred term and not the retired one, an unregistered target is
  refused with nothing written, self-reference is refused, chains are flattened, a base term is
  a valid target, only an admission can become a see-reference, and the bulk route carries it.

## [000.006.246] - 2026-09-26 — *A Vocabulary A Stranger Can Read*

`master_tag_taxonomy` held three different kinds of fact in one row: what a term means, who says
so, and how often this corpus uses it. Both consequences were measurable. **Nothing in the
repository seeds a single term** — no `upsert_master_tag` call exists in `db_migrator.py` — so a
fresh clone got the taxonomy rules in full and an empty table, and every mechanism they describe
operated on nothing. And the nightly census rewrites `usage_count` in the same row as the
definition (164 of them on 2026-09-26), so definitions could never live in a tracked file
without a background task dirtying it nightly.

### Added

- **A shared base layer beneath the local vocabulary.** `taxonomy/base.json` is tracked in git
  and materialised into `base_tag_taxonomy` / `base_tag_aliases` (migration `000.006.246`),
  which the engine never writes — not by admission, not by retirement, and above all not by the
  census, which has no column here to write into. 103 terms to start, each citing the published
  authority that supplied it.
- **A structural privacy property:** a term cannot enter the base without a published authority
  having catalogued it first, so the tracked file cannot contain anything personal. The measured
  48% of this vocabulary that no authority knows — `motif/*`, `setting/*`, named entities, and
  genuinely private terms — stays in the gitignored layer by construction rather than by care.
- `Evelyn/tools/taxonomy_base.py`: version-checked sync, rebuilt only when the tracked file
  moves, wired into boot before anything can read the registry. A test asserts the boot path
  still reaches it.

### Changed

- **`get_master_tags` merges the two layers per field, not per row.** The census writes a count
  and knows nothing about categories, so a row-level merge would let it replace a tracked
  definition with the empty strings it passes. A local row for a base term is an override;
  an empty local field means "nothing decided here", the convention `upsert_master_tag`
  already uses. Rows now carry `source`, `scheme` and `authorized_label`.
- `get_aliases` unions both alias tables, local winning.
- **A base term cannot be deleted or retired.** `delete_master_tag` now returns whether it
  removed anything, and `retire_term` checks *before* its first write — it recorded the alias
  and re-pointed relations before deleting, and ignored the delete's result, so a refusal would
  have left an alias pointing at a live term and reported success.
- **Clinical records excluded from the semantic tag audit.** Same rule and same evidence as
  `Reference Library/` under `000.006.233`: 31 notes already carrying good registered terms,
  while the audit proposed `semantic-analysis`, `document-indexing` and `demographics` on top —
  12 of the 13 vault-sourced admissions in the queue came from that one subtree. The exclusion
  is from the audit queue only; the notes stay indexed and fully retrievable. Built from
  `USER_NAME` rather than written out, per §4.

### Documentation

- **§6.2.1 / §6.2.2** in the taxonomy rules: how an equivalence is shown (`unused ➔ See:
  authorized`, `See also:` for the relations of §6.4, since writing `See:` for an `RT` would
  tell a reviewer to stop using a valid term), what each card states that approving will do, and
  that a suggested reference is always a proposal with an editable target — an authority's
  preferred form is a decision made for a published collection and can be wrong for a personal
  one.

### Tests

- `test_base_taxonomy_layer.py` (13): a fresh clone gets a working vocabulary, base terms admit
  without a proposal, the census cannot shadow a base definition, a local decision overrides one
  field and not the rest, base terms cannot be deleted or retired and nothing is written on the
  refusal, aliases merge with local winning, sync is idempotent, a new version replaces rather
  than merges, a missing or malformed file still leaves a working vocabulary, and boot reaches
  the loader.

## [000.006.245] - 2026-09-26 — *The Cap Is Not A Switch*

`TAG_ADMISSION_MAX_PENDING` was documented as a queue limit and behaved as a feature flag. At
the cap `propose_tag_admission` wrote no row, and withholding withholds a term only once some
row proves its disappearance from the entry is recoverable — so with none, the tag went back
onto the fact unreviewed. The gate switched itself off exactly when the vocabulary was under
most pressure, which is the drift that accumulated 127 stray terms and needed a hand curation
pass to undo. At 158 of 200 pending and ~13 new an hour, this was about three hours away.

### Fixed

- **A full queue now defers the row instead of skipping it.** Past the cap the proposal is
  written with `status = 'deferred'`: the reviewer is not handed a 201st card, the term stays
  recorded, and — because withholding asks only whether *a* row covers the term — the gate
  keeps working. The one case that still puts the tag back on the entry is a write that fails
  outright, where no row exists and withholding really would be deletion.
- **`release_deferred_admissions()` runs on the tag librarian's schedule** and promotes the
  backlog oldest-first into whatever room the queue has. It is deliberately *not* called from
  `propose_tag_admission`, which returns early whenever a pass finds nothing unregistered —
  the normal case — so hanging the release there would have drained the backlog only when an
  unrelated new term happened to arrive. A test asserts the scheduled pass still references
  it, because deferral without a release is a one-way door.

### Changed

- **Withholding checks coverage with one indexed lookup** (`topics_with_any_proposal`) rather
  than loading every pending and every rejected proposal on each fact write. Cost now follows
  the number of tags asked about rather than the size of a queue that only grows — and it is
  what makes a deferred row count as cover, which the two hard-coded status readers could not
  express.

### Added

- `memory_db.get_proposals_by_status`, `count_proposals`, `promote_deferred_proposals` and
  `DEFERRED_STATUS`. Deferred rows are invisible to every existing consumer, all of which key
  on `status = 'pending'`.

### Tests

- `test_tag_withholding.py` grew to 13. The test asserting a full queue keeps the tag is
  replaced — it encoded the decision being reversed — by four covering the new rule: the cap
  defers and keeps withholding, the release takes only what there is room for, the release
  does not depend on a new term arriving, and a write that fails outright still keeps the tag.

## [000.006.244] - 2026-09-26 — *One Decision, Many Rows*

Tag admission review arrived faster than one-at-a-time review could clear it. Overnight the
pending queue went from 21 to 158 against a ceiling of 200, and that ceiling is not a queue
limit but a switch: at the cap `withhold_unregistered_tags` stops proposing and keeps the
unregistered term on the entry instead, which is precisely the drift the withholding gate was
built to stop. A reviewer who cannot clear the queue faster than it fills never gets to decide
at all.

### Added

- **Bulk proposal review (`POST /api/review/proposals/bulk`).** Applies many decisions in one
  request, sequentially and independently: a bad row is reported against its own id and the
  rest still run, because a batch of 150 that aborts on the first failure is worse than no
  batch at all. Accepts the same per-decision fields a single card can carry — a corrected
  term, a chosen category, a relation kind — so nothing a reviewer can say one at a time is
  lost in bulk. Capped at `BULK_PROPOSAL_MAX` (250).
- **Bulk selection in the review UI.** A checkbox on each admission card and a bar carrying
  *Select all shown*, *Admit*, *Reject* and *Remove*. Selection lives in JavaScript state
  rather than the DOM, so ticking a box never re-renders the list and discards a term the
  reviewer was part-way through correcting, and "select all" is scoped to what the current
  filter actually shows rather than reaching past it. The bar states the asymmetry the two
  refusals carry: **reject is permanent** and suppresses the term from every future proposal,
  while **remove** only clears the row and the term returns the next time something asks.

### Changed

- **Proposal approval extracted into `_apply_proposal_action`.** The single-proposal route is
  now a thin wrapper over the same helper the bulk route calls, so a decision means exactly
  the same thing however it was submitted. A second implementation of approval would have been
  a second set of side effects to forget — the vocabulary write, the fact backfill, the note
  backfill — and a test asserts the single route still delegates.
- **The memory refresh runs once per batch, not once per decision,** and only when something
  was actually approved.

### Fixed

- **Denying or removing a proposal that is not there no longer reports success.** Both
  `reject_proposal` and `delete_proposal` have always returned whether they changed a row and
  nothing ever read it — the same defect shape as G1, one level down. One card at a time it was
  invisible, because the card was already gone; in a batch the applied/failed count is the
  reviewer's only feedback, so a double-submitted batch of 80 rejections reported 80 applied
  the second time too. Both now raise a 404 the batch reports against that id.

### Tests

- `test_bulk_proposal_review.py` (12): every decision applied, one bad row not ending the batch,
  approval still backfilling the fact that asked (G5), rejection still recorded as permanent,
  the refresh firing exactly once and not at all for a batch of rejections, empty and oversized
  batches refused, the single route still sharing the bulk implementation, and a missing row
  counted as a failure rather than an application — including the same batch submitted twice.

## [000.006.243] - 2026-09-25 — *Every Note Starts Here*

Six of nine vault templates seeded new notes with tags the registry does not hold.

### Fixed

- **Template tags re-mapped onto the vocabulary (F2).** Every offender was the same mistake —
  a statement about document *class* written as a flat word or a pre-coordinate compound, when
  §3.4 puts class on the `type/` axis:

  | template | was | now |
  |---|---|---|
  | Disambiguation Node | `system/disambiguation`, `moc`, `index` | `type/moc` |
  | External Resources | `External_Resource` | `type/reference` |
  | List Template | `list`, `{{slug}}` | `type/list` |
  | List of Templates | `index`, `moc` | `type/moc` |
  | Reference Cards | `learning`, `module-X`, `reference-card` | `type/reference`, `learning` |
  | Session Notes | `type/notes`, `dnd/session-recap` | `type/notes`, `ttrpg`, `campaign` |

  **Two were placeholders, not tags.** `{{slug}}` and `module-X` never rendered, so every note
  made from those templates carried them verbatim.

- **Why it went unseen.** `Templates/` is excluded from both indexing and the semantic audit —
  correctly, since a template is not a document about anything and auditing one would propose
  subjects for placeholder prose. The cost is that no producer and no pass will *ever* notice
  drift there, and the `.186` reset skipped excluded directories too.

### Tests

- `Evelyn/tests/test_templates_are_registered.py` — 4 tests, and **the only guard this
  directory has**. Red-checked by restoring an actual original file rather than a synthetic
  one. Skips where no vault is present, so a clone does not fail on somebody else's
  directory. One test asserts the exclusion *stays*, since this guard only makes sense while
  the audit keeps its hands off.

## [000.006.242] - 2026-09-25 — *Count Yourself Sometimes*

The vocabulary had never counted itself. 585 of 702 registry counts were wrong.

### Fixed

- **The usage census had no reachable caller (G6).** `maintain_master_taxonomy()` recounts
  every registered term across vault, memory and procedures — `.221` fixed it to span all
  three and corrected 278 counts. Nothing then checked whether anything *calls* it. Two routes
  exist and neither runs: `scripts/master_librarian.py` behind `--rebalance-taxonomy`, whose
  only scheduled caller invokes `run_master_librarian_task()` bare so the flag defaults to
  `False`, and a manual endpoint nothing calls. **Enabling the master librarian would not have
  fixed it**, which is why this was its own defect rather than a consequence of D3.

  `run_taxonomy_census_if_due()` now rides the scheduled tag-librarian pass, throttled to
  `TAXONOMY_CENSUS_INTERVAL_HOURS` (24). Measured cost: **6.1s** over 4,386 documents, 10,273
  facts and 51 procedures.

- **The census never wrote a zero.** A term with no current uses was collected into `unused`
  and its cached count left untouched — so a term that *fell out* of use kept its old number
  forever. Four read `usage_count = 1` against a true zero, which is precisely the set a
  reviewer consults when deciding what to retire and precisely what a vocabulary view would
  render. Not deleting an unused term is the rule; leaving its count wrong was never part of
  it. The row stays, the number tells the truth.

- **The retirement path had therefore never executed.** `propose_tag_retirement` is called
  *from inside* the census, so no term has ever been proposed for retirement in this system's
  life. It runs now: three unprotected zero-usage terms become eligible 2026-12-22/23 once
  their 90-day grace expires.

### Notes

- First run corrected **583** counts, then **3** more after the zero fix. Registry drift
  against the live corpus is now nil. Top terms re-measured: `type/reference` 2899,
  `persona` 1356, `workplace` 893.
- Same defect class as G1, G2, G4, G5 and F7a — written, correct, reached by nobody. **Six in
  one week**, all found by asking "what reads the output?" rather than "is it wired?".

### Tests

- `Evelyn/tests/test_taxonomy_census_runs.py` — 9 tests. One pins the root cause so nobody
  "fixes" this by enabling D3, and one covers the safety circuit breaker that aborts the
  census against an empty corpus — which the first draft of the zeroing test tripped, and
  which would otherwise have read as the fix not working.

## [000.006.241] - 2026-09-25 — *Green Is Not Compiles*

The hygiene gate passed a file Python refuses to parse.

### Fixed

- **`evelyn_server.py` held an `await` inside a sync helper.** `_execute_approval` is a
  synchronous nested function already dispatched to a thread, so `000.006.239`'s vault
  backfill call was a **`SyntaxError`, not a runtime error** — the process died at import and
  systemd crash-looped it every five seconds for two minutes, with the chat UI down
  throughout. Fixed to a direct call and folded into `.239`'s commit, so the commit that
  introduces the call is the one that boots.

### Added

- **Stage 0 of the hygiene gate: every file must compile.** Ruff has a rule for this exact
  mistake (`PLE1142`) and this repo's `select` list does not enable it — but enabling one more
  rule is the narrow reading. **Any rule list is a subset of what the interpreter rejects,
  while `compile()` is the interpreter's own answer.** Verified against the original defect:
  stage 0 fails on it while Ruff still reports the same file clean.

### Changed

- **AGENTS §11** now states five stages rather than three, and adds two rules this incident
  earned: *a green Ruff is not proof a file will import*, and *verify the build boots before
  committing* — a syntax check run earlier in a session does not cover a later edit, and a
  commit that cannot boot is worse than an uncommitted fix.

## [000.006.240] - 2026-09-25 — *A Slash Is An Axis*

Post-coordination left one use for `/`. Two places had never been told.

### Fixed

- **Decomposition invented terms the vocabulary does not hold.** `decompose_to_atoms` flattens
  a facet to prefix + leaf, dropping the middle — right for `setting/biome/tropical`, which
  categorises *within* an axis, and wrong for `type/media/text`, where §3.4 **requires** the
  DCMI second level. It yielded `type/text`: the required level destroyed and an unregistered
  term invented in one step.

  Fixed generally rather than by exempting `type/media`: **a term the registry already holds
  is never decomposed**, because a registered term is canonical by definition and producing
  something the vocabulary lacks is a failure of this function's own purpose. The rule then
  holds for whatever the standard requires next. Dormant when found — only the `.181`/`.182`
  migrations call it, and the live corpus was intact (28 documents on `type/media/text`, zero
  on `type/text`) — but the module advertises it as a general utility.

- **Admission accepted pre-coordinate compounds.** `normalize_tag_format` preserves a slash and
  `is_excluded_tag` ignores it, so `lore/campaign-narrative` reached the review queue on
  2026-09-23. It was rejected, and since `.231` made rejections permanent, **that rejection
  became the only thing stopping it returning** — a format rule delegated to a human decision,
  which also meant the rows could not be tidied without reopening the hole.

  `is_wellformed_term()` now gates admission: a slash belongs to a facet axis, or it is the
  retired hierarchy. The two rejected rows were then deleted as the errors they looked like.

### Changed

- **The relation card's text is written for a stranger.** It is a fixed template, not model
  output, so it reads identically for everyone who ever clones this. Two drafts failed that:
  "filed apart: 'x' vs 'y'" stated a fact and answered no question, and "categories of
  *yours*" assumed the reader curated them, which a fresh install has not. Neither said what
  approving *does* — the one thing the owner knows and a stranger cannot infer. The card now
  reads evidence → grouping → consequence → question, and the concepts move to F7b's hint
  rather than being crammed into a `reason` string.

### Tests

- `test_decompose_respects_the_registry.py` (7) and `test_admission_refuses_hierarchy.py` (10).
  Red-checked separately, and in both cases the **over-correction** was the useful mutation:
  "preserve anything with a slash" stops decomposition working at all, and "refuse anything
  with a slash" eats the facet axis. Each passes the obvious assertions.

## [000.006.239] - 2026-09-25 — *The Note That Asked*

Approving a term a vault note proposed registered the word and reached the note with nothing.

### Fixed

- **Admission backfill was memory-only (G5).** `admit_proposed_term` registers the term and
  `backfill_admitted_term` puts it on whatever asked — but only via `source_ids`, which holds
  `context_entries` ids. A vault note is not one, so a vault-sourced proposal carried an empty
  list and the second call was a no-op: the vocabulary gained an entry and the note that
  demonstrably concerns the subject stayed unindexed for it.

  Measured on the three terms admitted 2026-09-25 — the two raised by extracted facts landed
  on `#14460` and `#14459`; the one raised by a journal note landed on **nothing**.
  **G1's shape one field along:** approve has a consumer for one substrate and not the other,
  and the queue empties either way, so it reads as finished.

  Not data loss. The vault audit applies only registered terms and proposes the rest without
  writing them (confirmed: zero unregistered tags across all personal notes), so nothing was
  dropped — the term simply entered a vocabulary and was never used.

- **`backfill_admitted_term_to_note()`** writes through the audit's own path: frontmatter
  rewrite preserving mtime, then the index told directly. **The mtime is preserved
  deliberately, so the vault watcher never sees the write** — an index update that looked
  redundant is the only thing keeping the store from diverging silently, and it has a test
  saying so.

### Added

- **Migration `000.006.239` — `proposals.source_path`.** The path already existed inside the
  `merged_observation` origin sentence this codebase writes itself, so **35 existing proposals
  were backfilled from it**, including all 5 pending ones: the standing queue became
  actionable rather than only proposals raised from now on.

### Changed

- **The backfill resolves against the configured vault root at call time**, not
  `path_utils.VAULT_ROOT`, which is captured at import and cannot follow a reconfigured vault
  — the same reason `audit_single_document_semantic` takes an explicit root. The traversal
  guard is kept and separately tested, since resolving by hand means re-proving it.

### Tests

- `Evelyn/tests/test_vault_admission_backfill.py` — 10 tests, hermetic against a temp vault.
  Three mutations red-checked separately: dropping the traversal guard, skipping the index
  update, and re-adding a term already present.

## [000.006.238] - 2026-09-25 — *Reachable From The Queue*

Relation candidates now reach the review queue, and both answers have a reader.

### Added

- **`Evelyn/tools/tag_relations.py` — the measurement, where the engine can call it.** It sat
  inside `scripts/curate_tag_relations.py`, so the only way to see a candidate was to run a
  command by hand. The first 155 relations were consequently curated over a terminal and a
  conversation, **outside the review system that exists for exactly this**. The script is now
  a thin CLI over the module and behaviour is unchanged.
- **`tag_relation` proposals.** `propose_tag_relations()` rides the scheduled tag-librarian
  pass — the same cadence as the audit producing the tags it measures, into the same queue.
  Bounded by `TAG_RELATION_MAX_PENDING` (25, per-type rather than global, unlike the admission
  cap that once refused every other producer with it), and it reads the queue **before**
  counting the corpus, since counting means reading both substrates end to end.
- **Three outcomes on one card, because they are three decisions.**
  `apply_relation_decision()` takes `related` (symmetric), `narrower` (**directional — the
  order given is the claim**) or `alias` (not a relation at all: one concept under two names,
  so the first term is retired onto the second and its relations move with it). The generator
  measures association and cannot tell these apart, which is the whole reason it is a proposal
  and not a write. An unrecognised verdict is refused rather than defaulted to `related`.
- **A rejection binds, and re-opens on evidence** — the `.234` per-type policy applied to a
  type that did not exist then. A relation candidate *is* an evidence claim, so the ghost-stub
  shape fits: suppressed while the document count stays under `REEVIDENCE_FACTOR` × the count
  it was rejected at, with each suppressed request incrementing `rejection_count`.

### Changed

- **`.agents/rules/vault-tag-taxonomy.md` §6.4.1 — the six questions to ask a reviewer.**
  Curation policy, so it belongs with the standard rather than in a scratch file. It records
  the split of labour that C3 got wrong: the reviewer holds context the corpus cannot show,
  and the thesaurus logic belongs to the pipeline. **Reviewing 153 candidates by tag evidence
  alone held 18 back and got 8 of them wrong**; six plain questions settled every one.
  It also records what *not* to lead with — reversibility answers "how bad if I am wrong",
  not "is this legitimate", and a reviewer offered the first will keep everything.

### Fixed

- **`describe_candidate()` deleted before it shipped.** Written speculatively for a consumer
  that did not exist yet; Vulture flagged it as uncalled and it was removed rather than
  whitelisted. That is the §11 triage working as intended on the exact defect class — code
  written, plausible, and reached by nothing — that `.230`, `.231` and `.232` each fixed after
  the fact.

- **A review card for `tag_relation`** (`evelyn_ui/dev.html`). Without one the generic card
  would have rendered `merged_observation` and **never shown the pair** — the same defect the
  admission card was built to fix. It leads with the two terms and the evidence, and collects
  the verdict in a single `<select>` carrying both kind and direction, because for two of the
  three outcomes the order *is* the claim. The alias options say plainly which term they
  remove: that decision subtracts from the vocabulary, the other two only add a row.

### Tests

- `Evelyn/tests/test_relation_proposals.py` — 15 tests over the whole loop: produce, suppress,
  re-open, and all three approval outcomes. Five mutations red-checked separately, including
  sorting a `narrower` pair (silently reverses about half of them) and defaulting an unknown
  verdict to `related` (invents a decision the reviewer did not make).
- The two existing relation suites were rewritten against the module's structured return
  rather than by parsing printed output. That parsing had already broken twice, and both
  breaks were in the test rather than the code.

## [000.006.237] - 2026-09-25 — *An Alias Is One Term*

A reviewer's decision to make two terms one did not survive the next report.

### Fixed

- **The relation candidate generator counted aliased surface forms as separate terms.** A `UF`
  alias means the two terms *are* one concept (§6.2), and recording one leaves the old form on
  the documents that already carried it — correct for retrieval, which is exactly what the
  pointer is for, and wrong for a co-occurrence count. The pair therefore came back as a
  candidate on the very next run, so **deciding it decided nothing** — the same shape as the
  rejected column in `000.006.231`. `_resolve_aliases()` now collapses surface forms before
  counting, and a document whose tags collapse to a single term leaves the corpus, preserving
  the `1 < len(tags)` invariant `_corpus` has always applied.

  Measured across both substrates: 171 aliases, of which **one** had an unresolved surface form
  on documents (31 uses, 0.07%) — the rest were applied when they were curated. Small today,
  and guaranteed to recur every time an alias is drawn from a co-occurring pair.

### Changed

- **`000.006.236` named the wrong example.** `cat:sleep` was used as *the* spurious candidate —
  in that entry, in the generator's comment and in a test docstring — on the reasoning that it
  is where the cat sleeps rather than a relation. Reviewed with the vault's owner it is real:
  the cat decides how the night goes. The category split it justified is unaffected and still
  worth having, but it is **a prior and not a verdict** — 42 of the 51 cross-category pairs were
  real on review. The prose now says so, and uses `hydration:rest` as the example instead.

### Tests

- Two added to `Evelyn/tests/test_relation_candidates_are_ranked.py` (7 total). The resolution
  was first written inside `_corpus`, where the tests stub it out and could never reach it —
  the test failed green-side and moved the logic to the analysis seam rather than the I/O one.
  The single-term guard was then found **uncovered** by its own red check and given a test.

## [000.006.236] - 2026-09-25 — *A Person Already Sorted These*

Relation candidates arrive split by the categories a person curated, not as one list.

### Added

- **Category co-membership as a review prior (F6).** `scripts/curate_tag_relations.py`
  ranks by co-occurrence lift, which is a statistical rediscovery of association with no
  judgement in it — it cannot separate `cpap:sleep` from `cat:sleep`, the same co-occurrence
  where one is a relation and the other is where the cat sleeps. The 603 curated category
  groupings from Pass 1 *are* that judgement and were already paid for, so candidates now
  print in two sections: **same category** (filed together by a person — review fast) and
  **cross category** (filed apart — the argument has to be made). On the live corpus the 153
  clean candidates split **102 / 51**, and the spurious pairs concentrate in the second.
  The category that placed each pair is shown, so a reviewer who disagrees with the bucket
  can see why it is there.
- **It re-orders, it never filters.** Both headings print even when a bucket is empty, and
  the summary counts both — an absent heading reads as "no such candidates", which is the
  failure mode this area keeps producing.

### Tests

- `Evelyn/tests/test_relation_candidates_are_ranked.py` — 5 tests over a synthetic
  vocabulary, each red-checked separately against a no-split version, a `"" == ""` version
  and a filtering version. The blank-category test was **written vacuous first**: its pair
  never cleared the lift filter, so it passed against the exact bug it was written for.
  Rewritten with a pair that reaches the split, then re-verified red.

## [000.006.235] - 2026-09-25 — *Class Is Not Association*

The relation generator proposed facets as if they were subjects.

### Fixed

- **A relation candidate must be two subjects (C1).** `scripts/curate_tag_relations.py`
  ranks pairs by co-occurrence lift, which is the right measure for association and the wrong
  one for this: a facet value co-occurs with subjects *by construction*. Documents about a
  game system really do tend to be profiles, so `<subject>:type/profile` scored highly while
  asserting nothing an `RT` relation is allowed to mean (§6.4). **14 of 167 candidates on the
  live corpus** — the same category error the subject pass has guarded since `000.006.219`,
  in a second place. Candidates now drop to **153**, and the excluded count is printed rather
  than the rows vanishing silently.

### Changed

- **The facet guard is one predicate with two callers.** `reconcile_subjects`' inner
  `_is_subject()` closure is promoted to `tag_librarian.is_subject_term()`, with
  `FACET_PREFIXES` moving there too — `tag_synonym` held a second copy of the same tuple and
  imports it from `tag_librarian` already, so the constant now sits at the base of that
  dependency rather than being restated (AGENTS §8). No behaviour change to the subject pass.

### Tests

- `Evelyn/tests/test_relation_candidates_are_subjects.py` — 8 tests driving the real
  `report()` over a synthetic corpus. Verified red twice and separately: with the guard
  neutralised, and with the prefix list restated in the script instead of reused. The second
  is the case worth having — a partial restatement (`type/`, `motif/`) passes the obvious
  assertions and fails only on `setting/`, `event/`, and the reuse check.

## [000.006.234] - 2026-09-25 — *What No Means*

A rejection now binds for every proposal type — and means something different in each.

### Fixed
- **Three of the five proposal types still re-proposed after rejection.** `.231` bound
  `tag_admission`; the rest deduplicated against *pending* proposals alone, so a rejection
  removed a row from the queue and decided nothing. `procedure_merge` had already made the full
  round trip in production.
  - **`procedure_merge` — permanent, scoped to the exact set.** "These procedures are genuinely
    distinct" is a durable statement about those procedures. **Scoped to the cluster, never its
    members:** excluding any procedure that once appeared in a rejected merge would bar it from
    every future merge, including with a procedure it has never been compared against. A cluster
    that gains a third member is a different proposition and is still asked.
  - **`split` — binds until the fact is edited.** "Atomic enough" stays true exactly as long as
    the observation does. Re-opens when `updated_at` moves past the rejection, which is the same
    shape as the one rejection that already worked.
  - **`ghost_link_stub` — binds until the evidence grows.** Deliberately *not* permanent: a
    target cited twice may well deserve a note at twenty, and a permanent block would silence
    the growth that should change the answer. Re-asked once citations pass
    `cfg.GHOST_STUB_REEVIDENCE_FACTOR` (2.0) times the rejected count.
- **`profile_update` needed nothing — it already bound**, and is the model the other three
  follow. Denying stamps `entry_document_evolution`, and the evolver re-opens an entry only when
  `updated_at` moves past that stamp. Better-shaped than `.231`'s term-scoped suppression: bound
  to a pair, and self-re-opening. **35 of the 60 outstanding rejections were never leaking.**

### Added
- **`proposals.evidence`** (migration `000.006.234`) — how much evidence a proposal was made on,
  where the type has a natural measure. A ghost link's citation count existed only as prose
  inside `reason` ("cited in 7 notes"), which is not comparable. 18 existing ghost-link
  rejections backfilled by parsing that sentence: acceptable once, in a migration, against a
  format this codebase wrote itself. Recorded values range 2–6.
- **`memory_db.get_rejected_proposals(type)`** — the counterpart to `get_pending_proposals`.
  Returns whole rows rather than a verdict, because what a rejection *means* differs by type and
  that judgement belongs at the producer.
- **`memory_db._row_to_proposal()`** — the `source_ids` JSON decode was inline in the pending
  reader while that was the only reader. A second one that forgot it would hand producers a
  string where they expect a list and compare it against ints without erroring.

### Changed
- **F4 closed: the `.186` tag reset manifest will not be reinstated** (user's decision). The
  tracker described it as holding date anchors plus `status/`, `kanban` and `obsidian-graph/`
  tags. **There were never any `kanban` or `status/` tags.** What it actually holds is 667 `CY-`
  date anchors across 666 notes — already superseded by the `occurred:` property in `.203`, so
  reinstating them would undo that migration — and a 49-use graph-control axis
  (`obsidian-graph/contact` 32, `no-graph` 17) that nothing consumes: there is no `graph.json`
  in the vault's `.obsidian/`, and the 32 contact notes live in `Contacts/`, which already says
  what the tag said. The pre-wipe tarball stays as insurance.

### Testing
- `Evelyn/tests/test_rejection_binds_per_type.py` — each policy verified red **separately**, and
  the scoping test verified against the *wrong fix* rather than against absence: a member-scoped
  `procedure_merge` suppression passes a naive test and quietly bars unrelated merges.

## [000.006.233] - 2026-09-25 — *Not Our Vocabulary*

The admission queue filled to 200/200 in a day, every slot from a third-party textbook.

### Changed
- **`Reference Library/` is excluded from Tag Librarian auditing** (user's decision,
  2026-09-25). It is third-party book and manual text, and the terms it nominated described
  *someone else's* subject matter — `speculative-decoding`, `evol-instruct`,
  `reverse-neutralization`, `adapter-tuning`. At roughly three new terms per note across 2,713
  unaudited notes it implied **~8,000 candidates against a 700-term curated vocabulary**.
  - **The 200-slot cap is global, not per-producer.** Once full, `propose_tag_admission()`
    returns `[]` for everything: memory extraction, fact merges and personal vault notes went
    silent together, and withholding then leaves unregistered terms *on* facts because nothing
    pending covers them. It had been refusing producers since 15:30 — 22 refusals in two hours —
    and a full queue logs a warning and otherwise looks like quiet.
  - **The exclusion costs almost nothing.** All 2,838 reference notes already carry tags from
    ingestion; the semantic pass had reached only 243, and was refining tags rather than
    supplying them. They stay tagged, stay searchable, and simply no longer nominate vocabulary.
- **`TAG_LIBRARIAN_EXCLUDED_PREFIXES`** — excluded subtrees move from the audit's SQL, where they
  were spelled out twice each for case, into config beside the rest of the librarian's settings.
  Matching is now case-insensitive in one clause per prefix. Config is the single place that
  answers "what does the librarian not touch", which matters more now that one entry on the list
  is a decision rather than an obvious skip.

### Fixed
- The 200 queued Reference Library proposals were **deleted rather than rejected**. Since
  `.231` a rejection is permanent and scoped to the *term*, so rejecting `semantic-similarity`
  because a textbook asked for it would have silently suppressed a personal note that genuinely
  meant it later. These should never have been raised; deleting leaves no record to suppress
  anything. Backup in `scratch/` if one is ever wanted.

### Testing
- `Evelyn/tests/test_librarian_excluded_prefixes.py` — pins the decision, checks the four
  previously-hardcoded subtrees survived the move, asserts case-insensitivity has not
  regressed into duplicated clauses, and verifies end-to-end that no excluded subtree reaches
  the audit queue. Config is only a list until the query reads it.

## [000.006.232] - 2026-09-25 — *Let It Finish*

The engine has a graceful shutdown. It had never once run.

### Fixed
- **Lifespan shutdown was unreachable, so the Chroma drain never happened.**
  `clean_shutdown_all_tasks()` terminates write producers and then drains the pending Chroma
  write queue — correct code, correctly wired into the FastAPI lifespan. It did not execute.
  Uvicorn waits for in-flight connections *before* running lifespan shutdown, `uvicorn.run()`
  passed no `timeout_graceful_shutdown`, and the chat UI holds a `text/event-stream` connection
  that never closes on its own. Every restart with a browser tab open logged
  `Waiting for connections to close` and nothing further until SIGKILL at `TimeoutStopSec`.
  - The custodian holds the vector store's **single-writer lease for its whole life**, so each
    of those restarts killed the writer mid-lease. It went unnoticed because the startup reaper
    clears the stale `.chroma_write.lock` and the boot health probe then passes: **a clean start
    says nothing about the stop before it.**
  - `timeout_graceful_shutdown=cfg.SHUTDOWN_CONNECTION_DRAIN_SECONDS` (5s) caps that wait.
- **A second unbounded wait sat between cancellation and the drain.** The lifespan shutdown did
  `await asyncio.gather(*cancelled_tasks)` with no timeout. `cancel()` raises at the next await
  point, which a task inside `asyncio.to_thread` or a blocking Ollama call does not reach while
  that call runs — so one stuck loop could eat the whole stop budget and take the drain with it.
  Bounded by `cfg.SHUTDOWN_TASK_CANCEL_SECONDS` (5s); tasks that will not stop are left to die
  with the process, and the drain proceeds regardless.

### Changed
- **`scripts/restart_evelyn_services.sh` and `scripts/stop_evelyn_services.sh` now verify the
  shutdown instead of assuming it.** Both stop the engine on its own — not bundled with
  `evelyn-tts` in one `systemctl` call, which hid the result — then confirm the journal carries
  `Clean shutdown complete` before continuing. Neither reports success without it; both exit
  non-zero and point at `ROLLBACK.md` → *Repairing a Single Chroma Collection*.
- **`scripts/graceful_stop.sh`** (new, sourced) holds `evelyn_graceful_stop` and
  `evelyn_checkpoint_wal`, replacing the WAL-checkpoint block that had been copied into both
  scripts.
- `scripts/check_evelyn_status.sh` suggested a bare `systemctl restart`; it now points at the
  script that verifies.
- **`TimeoutStopSec=30`** on the unit (worst case is 5 + 5 + 3 + 5 = 18s; the stock 15s cut the
  drain off). Applied to `/etc/systemd/system/evelyn.service.d/override.conf`, which is outside
  version control — recorded in `SETUP_GUIDE.md` so a fresh install gets it.
- **AGENTS.md §6** now forbids a bare `systemctl restart evelyn` and requires the confirmation
  before a restart is reported as successful.

### Testing
- `Evelyn/tests/test_graceful_shutdown.py` — source-level assertions on purpose. The defect was
  a *missing keyword argument* and an *unwrapped await*: both read as working code, and both are
  invisible to a call-graph wiring check because every function involved is called. Includes a
  budget check that fails if the timeouts stop fitting inside `TimeoutStopSec`, with the message
  that the answer is to find what is slow rather than raise the number.

## [000.006.231] - 2026-09-25 — *No Is an Answer*

Rejecting a proposal now decides something. It used to decide nothing.

### Fixed
- **`status='rejected'` was a write-only column.** It was set in exactly one place,
  `memory_db.reject_proposal()`, and read by nothing — every consumer of the proposals table
  filters `status='pending'`. So rejecting removed a row from the queue and changed nothing
  else: the next producer that wanted the term raised it again, and the reviewer's only
  available action was to reject it a second time. **78 rejections across five proposal types
  carried no effect**, and `support` completed the full round trip inside a day (rejected
  09-23, pending again 09-24).
  - `propose_tag_admission()` now checks rejected topics alongside pending ones and does not
    re-raise. A rejection is **permanent** — it does not expire.
  - A read failure suppresses nothing. A term that slips through costs one review; a term
    wrongly suppressed is invisible.
  - Scoped to `tag_admission` deliberately. `ghost_link_stub` and `profile_update` reject for
    "not now" rather than "never", and one list cannot mean both — that policy stays open as G1.
- **Rejected terms could have become the one verdict that lets a tag through.** Withholding only
  withheld a tag once a *pending* proposal covered it. Once rejected terms stopped being
  re-proposed, nothing pending would cover them and they would have stayed on the fact. The
  rejected row is now counted as covering too — and it is a firmer cover than a pending one,
  since the reviewer has already answered.

### Added
- **`proposals.rejection_count`** (migration `000.006.231`). Silence cannot distinguish a term
  rejected once by accident of phrasing from one the corpus asks for every day. Every suppressed
  re-request increments the count on the rejected row, so a term arrives at reconsideration with
  the number that argues for it: **1 is a one-off, 30 is a subject the vocabulary is missing.**
  - `memory_db.get_rejected_topics(type)` and `memory_db.record_rejected_request(type, topic)`.
  - The 78 existing rejections are seeded at 1 — each was a deliberate decision.
  - Indexed on `(type, topic, status)`; the suppression check runs on every admission proposal.
  - `init_db()`'s runtime schema carries the column too, so a fresh database and a migrated one
    agree.

### Testing
- `Evelyn/tests/test_rejection_is_binding.py` — each case verified red against the specific
  change it pins, separately rather than together: removing the suppression and the withholding
  coverage at the same time made the withholding test pass, because the term was re-proposed and
  then covered as pending. A red check that removes two things at once can certify neither.

## [000.006.230] - 2026-09-25 — *The Last Writer That Minted*

Every memory writer routed its tags through admission except the one that consolidates them.

### Fixed
- **A fact merge could still mint vocabulary.** `fact_deduplicator` imported
  `normalize_tag_format` (format only) and went through `canonicalize_tags` (alias resolution
  only) — both documented as *not* gates — then handed the model's chosen `merged_tags` straight
  to `apply_fact_merge`, which writes them verbatim. The extraction writers have gated since
  `.227` and `.225` stopped the union bloat; neither touched admission on this path.
  - Measured: the night after the memory corpus was hand-curated to **0** unregistered terms
    across 10,335 live facts, one run of **50 merges put 102 back** onto 56 facts — `videogame`
    beside the registered `video-games`, `dnd` and `tabletop-rpg` beside `ttrpg`, the container
    word `work`, and one-offs like `muffin` and `maroon-shirt`. None reached the review queue.
  - `merged_tags` now goes through `tag_librarian.withhold_unregistered_tags()` before it is
    either queued on the proposal or applied to the master fact, so a reviewer approving a
    `pending` merge tomorrow sees what an `auto_applied` one would have written today.
  - **The fallback is part of the fix.** When every chosen term is withheld, leaving
    `merged_tags` empty would send `apply_fact_merge` to its union branch — restoring the bloat
    `.225` removed *and* putting back the terms just withheld. It now falls back to the master
    entry's own tags, which are registered and already curated.

### Changed
- **`memory_db.select_merge_master()`** — the master-selection sort (earliest observed, then
  lowest id) extracted from `apply_fact_merge` and shared. The deduplicator has to know which
  row will carry the tag *before* the merge runs, because a tag admission proposal records the
  entry that wanted the term so approval can put it back; picking the master by a second,
  separately-written rule would have pointed the proposal at a row the tag never landed on.
  `apply_fact_merge` now also excludes secondaries by id rather than list position, so a caller
  that passed the master twice cannot make the merge soft-delete the row it just wrote.

### Testing
- `Evelyn/tests/test_merge_tag_admission.py` — drives the real
  `generate_consolidation_proposal` with a stubbed model rather than reimplementing the gate.
  The distinction is the whole point: the logic was never wrong, it was never called, so a test
  that recomputes it would have passed throughout. Verified red against the unfixed module.

## [000.006.229] - 2026-09-24 — *The Reset's Missing Half*

The vault re-entered its queue after the tag reset. Memory never did.

### Added
- **A memory tag backfill.** `000.006.187` cleared 39,061 tags across 12,893 memory rows so the
  vocabulary could be regenerated from the standard rather than migrated toward it, and
  `000.006.186` did the same to the vault *and reset every document's audit timestamp* — so the
  vault re-entered the semantic queue and has been draining since. Memory got no equivalent, and
  10,033 live facts have been invisible to tag retrieval ever since.
  - `tag_librarian.audit_single_fact_tags()` — deliberately the same machinery as the vault pass
    rather than a second implementation: a fact is a short document, so it goes through
    `classify_document_subjects`, which asks a model only what the text is *about* and then
    aligns the answer deterministically. Only terms the vocabulary already holds are written.
  - `scripts/backfill_memory_tags.py` — a foreground drain, resumable, stops cleanly on Ctrl-C.
    A one-time backlog rather than a scheduled pass: every current writer tags what it writes,
    so nothing is adding to it.
  - `memory_db.fetch_next_entries_for_tag_audit()`, `count_entries_awaiting_tag_audit()`,
    `mark_entry_tag_audited()`. Untagged facts first; a fact that matched nothing is still
    stamped, so it does not get retried ahead of facts nobody has looked at.
- **`held_back`** on the audit result — every term classification could not match, raised as a
  proposal or not. Past `TAG_ADMISSION_MAX_PENDING` no proposal is written and the fact is
  stamped regardless, so without this the terms a full queue refused would be gone with the fact
  marked done. The script writes them to `scratch/backfill_unmatched-*.json`.

### Migrations
- **`000.006.229` (memory)** — adds `context_entries.last_tag_audit_at` and its index.
  `last_audited_at` could not serve: it belongs to the grounding auditor and is stamped by every
  merge, so the two passes would reset each other's progress.

### Changed
- The admission card's category dropdown sorts heaviest-first. Alphabetical put
  `aesthetics-culture-language` (27) above `reference` (167) in a list of twenty, so the groups a
  term is most likely to join were the ones furthest down it.

### Measured
A 12-fact run tagged 11 and raised 11 proposals, at 0.7s per fact once the embedding model is
warm — roughly two hours for the full backlog.

## [000.006.228] - 2026-09-24 — *A Category Is Not a Facet*

The registry's `category` column held two different kinds of value, and one of them said nothing.

### Changed
- **Stopped storing a category that restates the term's own prefix.** For a flat term the column
  holds a curated topical group — `fastapi` → `ai-engineering` — a human judgement recorded
  nowhere else. For a facet-prefixed term it was auto-filled from the prefix: `motif/storm` had
  category `motif`. That half was pure restatement, derivable from the tag string, and it is what
  made the column read as a facet placeholder rather than the review aid it is.
- **`admit_proposed_term()` no longer derives one.** It filled `category` from the prefix when a
  reviewer gave none, which is what kept re-minting the restatement. An uncategorised term now
  stays uncategorised until somebody groups it.

### Migrations
- **`000.006.218` (vault)** — cleared 96 category values that equalled their own term's prefix.
  The 603 curated groups were left untouched: they cannot be recomputed, so they are not swept
  away on the strength of the derivable ones looking redundant. Chroma re-synced, since the
  vector copy carries category in its metadata.

### What the column is for
Nothing branches on it. It reaches a person in one place: `/api/taxonomy/vocabulary` fills the
admission card's category dropdown from it and prints `term · category` beside near-matches. It
is a review aid, and now only holds values a reviewer actually chose.

## [000.006.227] - 2026-09-24 — *Withheld, Not Lost*

An unregistered tag now goes to review instead of onto the fact.

### Added
- **`withhold_unregistered_tags()` and `cfg.TAG_WITHHOLD_UNREGISTERED`.** §6.1 makes an
  unregistered term a proposal rather than a silent addition, and the vault side already worked
  that way — `reconcile_subjects` applies only what it can match. The memory writers stored the
  term on the fact *and* proposed it, so the corpus and the vocabulary drifted apart: 127 terms
  accumulated unnoticed and took a hand curation pass to reconcile. Wired into all three memory
  writers (extraction, the manual context path, and the split apply path).
- Withholding is only safe because approval can put an admitted term back on the facts that
  wanted it (`backfill_admitted_term`, v000.006.216), which is why it was deferred until now.

### Fixed
- **The container filter moved to the shared choke point.** v000.006.226 put it in the vault's
  subject pass only, so a memory writer could still propose `work` — and did, in the first test
  written against it. It now sits in `propose_tag_admission`, which every producer goes through.

### Behaviour
- **Nothing is dropped silently.** A term is withheld only once a pending proposal covers it,
  including one an earlier fact raised. If the queue is at `TAG_ADMISSION_MAX_PENDING`, or the
  proposal could not be written, or the pending set cannot be read, the tag stays on the fact:
  an unreviewed tag is untidy, a vanished one is unrecoverable.
- **A container word is the exception, and deliberately so.** It is dropped rather than stored or
  proposed. The guard above protects information; `work` and `pets` carry none — that is what
  puts them on the list — so dropping them loses nothing, and keeping them is how they reached
  17 memory facts. Logged when it happens.

### Verification
- `Evelyn/tests/test_tag_withholding.py` — eight cases: a registered tag kept, an unregistered one
  withheld and proposed, the proposal recording its entry, a second fact covered by the existing
  proposal and widening it, a full queue keeping the tag, the flag switching it off, a container
  dropped, and no producer able to propose a container.

## [000.006.226] - 2026-09-24 — *Proposed to Nobody*

The vault librarian found unregistered subjects in 766 documents and told no one.

### Fixed
- **The semantic audit now raises its proposals.** `audit_document_tags()` reconciles a
  document's subjects against the registry and holds back what it cannot match, as
  `details["proposals"]`. That list went to a `logger.info` the audit subprocess does not
  surface and into a returned dict the backlog drainer discards — `propose_tag_admission` was
  never called from `tag_librarian` at all. Two Reference Library chapters in one run produced
  eight unregistered terms between them and the queue gained nothing. The empty admission queue
  read as a clean pipeline; it was the largest producer writing nowhere. Proposals now carry the
  note that wanted the term, so review is a judgement in context rather than about a bare word.

### Added
- **`is_umbrella_term()` and `cfg.TAXONOMY_CONTAINER_TERMS`** — one canonical list of the
  container words, enforced where terms are held back for review. Every tag-producing prompt
  already said "name the specific subject, never the container", and 17 of the 127 unregistered
  terms found on live memory facts were exactly those words. A prompt is advice; a model reaches
  for the container anyway when nothing more specific comes to mind. This is the filter that
  makes the rule hold, and it matches exactly, so `information-retrieval` is untouched by
  `information`.

### Verification
- `Evelyn/tests/test_vault_tag_admission.py` — sixteen cases across both: containers recognised
  and real subjects passed, the list read from config rather than hardcoded, a container never
  reaching the proposal branch, unmatched subjects reaching the queue, a dry run proposing
  nothing, and the proposal recording its source note. Three confirmed to fail against the
  previous code.

### Note on rollout
`TAG_ADMISSION_MAX_PENDING` (200) is the throttle. At two documents a run against 3,619
unaudited notes the queue will fill over a few hours and then stop accepting more until the
backlog is reviewed, which is the intended behaviour rather than a limit to raise.

## [000.006.225] - 2026-09-24 — *Merging Is a Choice*

The merge prompt asked the model which tags the consolidated fact should carry, then the code
added every source's tags back on top.

### Fixed
- **`apply_fact_merge()` treats `merged_tags` as a selection, not an addition.** It unioned the
  tags of every source entry *and* the model's answer, so a term deliberately left out was put
  straight back — a leaving-out was impossible to express, and the merge prompt's rule 7 ("prefer
  terms already on the sources, never name the container") was unenforceable at the one point it
  mattered. Merging three four-tag facts produced up to twelve tags. Where a caller supplies
  tags they are now the result; the union survives only as the fallback for a caller with none.

### Why it was found
Reviewing the first librarian cycle turned up 127 unregistered terms on live memory facts, 43%
of them concentrated in twelve facts of 27–61 words carrying four to nine tags each. Those twelve
map onto the eleven merges auto-applied between 04:51 and 05:54 the same morning. Consolidation
is where a vocabulary is supposed to get smaller; this one grew it monotonically, and cleaning up
after it without fixing it would have meant cleaning up again the next morning.

### Verification
- `Evelyn/tests/test_merge_tag_selection.py` — five cases: the choice is the result, a container
  word left out stays out, merging never grows the list, the no-choice fallback is unchanged, and
  duplicates collapse. Four confirmed to fail against the union.
- `apply_fact_merge` has two production callers (the review-apply path and the deduplicator's
  auto-apply); both already pass the model's tags. Existing merge tests unaffected.

## [000.006.224] - 2026-09-24 — *Not a Vector Question*

"Does this fact introduce a new term?" was being answered by cosine distance, at roughly the
accuracy of a coin. It is a set lookup.

### Fixed
- **The review card names which tags are new, instead of scoring a distance.** It embedded the
  whole observation, took the nearest term's distance, and cut it into `Aligned`/`Related`/`Novel`
  at 0.40 and 0.55. Measured over 144 labelled facts, that distance separated "every tag already
  registered" from "introduces a new term" by **0.005**; the best split anywhere on the range
  scored 66.7% against a 62.5% majority baseline, and the whole p10–p90 of the distribution
  (0.369–0.490) straddled both cut points, so nearly every card read `Aligned` or `Related`
  whatever it contained. An extraction already carries its proposed tags, and membership of a
  controlled vocabulary is a set — the card now reports exactly which proposed tags are
  unregistered, each with its nearest registered term, which is the comparison §6.1 asks a
  reviewer to make. Aliases resolve, so a fact phrased a retired way is not counted as new.
- **The extraction prompt stopped teaching the hierarchy every other rule forbids.** Its novelty
  directive branched on the same distance to pick between three instructions, and all three
  described the retired model — the third *explicitly encouraged* minting new
  `#Domain/Category/Subtopic` trees. It is now one directive, grounded in the only real
  condition (whether any vocabulary was retrieved), telling the model to prefer a listed term
  and that a new one goes to review. Candidates are listed bare rather than `#`-prefixed, which
  is how the `tags` field stores them.
- **Two tests were pinning the retired format in place.** `test_context_extractor_taxonomy_rag`
  asserted the prompt contained "mint new domain-level tag hierarchies" and `#Home/Coffee/Espresso`,
  so the sweep in v000.006.220 could not have succeeded there even if it had looked.
- **Three more `Tech/Python/FastAPI` placeholders** sat in the review UI's own tag fields, shown
  to the reviewer as the format to type.

### Changed
- `tag_librarian._vector_lookup` → **`nearest_registered_term()`**, now a public primitive: it
  answers §6.1's "nearest existing terms" for any caller holding a candidate term.
- `suggested_tags` excludes terms the fact already carries — the card asks what *else* the
  vocabulary holds. The UI shows the new `unregistered_tags` beside them.
- `evelyn_ui/dev.html` no longer re-derives the alignment label from thresholds of its own; the
  server is the single source, and those thresholds no longer exist.
- Removed `FACT_EXTRACTION_NOVELTY_THRESHOLD`; nothing should branch on that distance again.

### Verification
- `Evelyn/tests/test_review_card_novelty.py` — seven cases: aligned, novel, the most-novel term
  driving the score, alias resolution, untagged, suggestion exclusion, and a failed lookup
  declining to invent a verdict.
- `Evelyn/tests/test_prompts_teach_the_standard.py` gains a **structural** check. The denylist
  there missed this fourth offender on a plural ("hierarchies") and a different example format,
  so the new test matches the *shape* — a capitalised or three-deep slashed path inside a string
  about tags — and catches wordings nobody has invented yet. Confirmed to fail against the old
  directive.

### Measurement notes
Three query shapes were compared against the tag index before changing anything. Compacting the
observation to keywords — the fix this was expected to need — made suggestions **worse** (59.2%
hit@4 against prose's 75.0%) without improving separation. A per-keyword minimum appeared to
separate well until it was measured against labelled data, where it separated the wrong way: a
minimum over more keywords is lower by construction, not by meaning. Prose remains the best
shape for retrieving candidate terms and is unchanged, in the review card and in the extractor.

## [000.006.223] - 2026-09-24 — *Seeding Is a Bootstrap*

One CLI flag would have replaced every curated category with a single shelf label.

### Fixed
- **`seed_master_taxonomy_from_vault()` refuses a populated registry.** It upserted *every*
  vault tag with `category = "general"` and `description = "Obsidian notes tagged under X"`, so
  a single run would have overwritten all 675 curated categories and papered the scope notes
  with boilerplate. Nothing schedules it and it had never run — verified: no row carried that
  description — but it was one flag away and its name sounds harmless. Seeding is now what its
  name implies: a bootstrap for an empty registry, for a fresh install or a restore where the
  vault is the only surviving record of which terms were in use. Against a reviewed registry it
  refuses and points at the admission route, because bulk registration waives the review §6.1
  exists to require. `allow_populated=True` (CLI `--allow-populated`) overrides it for a
  deliberate restore — and even then it registers only terms the registry lacks and rewrites
  no existing row.
- **A seeded term carries no invented category or description.** Both are left empty for a
  reviewer. A derived shelf label and a boilerplate scope note are indistinguishable from
  curated answers once written, and the emptiness is the signal that the term still needs one.
- **`upsert_master_tag()`: an omitted field means "leave it alone", not "blank it".** The
  description was already guarded this way; `category` was overwritten unconditionally, so any
  caller omitting it silently cleared a curated categorisation. `usage_count` had the same
  defect — re-admitting an existing term reset its count to zero — and now preserves on `None`,
  since zero is a real value a reserved term holds and cannot double as "unknown".

### Changed
- `.agents/rules/vault-tag-taxonomy.md` §6.1 states that bulk registration is a bootstrap and
  never a refresh, and that a registration writes only what it knows.
- The seeder no longer triggers a full vector re-sync as a side effect; it indexes the terms it
  registered. `--sync-vector-tags` remains for a deliberate full sync.

### Verification
- `Evelyn/tests/test_taxonomy_seed_guard.py` — seven cases across both guards. Six confirmed to
  fail against the previous implementation; the seventh is a control proving a field is still
  writable when a value is actually supplied.
- Ran the seeder against the live registry: refused, no writes. It also reported 0 unregistered
  vault terms, so the vault side is fully registered — unlike memory's 127 (open item A3).

## [000.006.222] - 2026-09-24 — *Retire the Relations Too*

Retiring a term removed its registry row and left its relations pointing at nothing.

### Fixed
- **`retire_term()` now settles the relations layer.** It recorded the alias, deleted the
  registry row and the vector, and never touched `master_tag_related` — so a curated relation
  survived pointing at a term the vocabulary no longer held, and expansion would follow it to a
  term nothing could describe. Zero rows were dangling when this was found, which was luck: the
  five terms collapsed that day happened to carry no relations.
  - Retired **to a preferred form**, the relations move to it: a rename does not change what a
    term is related to.
  - Retired **outright**, they go with it. A relation with a missing endpoint is broken, not
    merely weaker.

### Added
- `taxonomy_db.repoint_relations()` and `taxonomy_db.delete_relations()`. Re-pointing decides
  three cases rather than copying rows: a relation between the retired term and its own preferred
  form is dropped (the alias now says they are one term); a pair the surviving term already holds
  keeps the stronger claim (`reviewed` outranks `inferred`, then heavier weight), so the move
  cannot downgrade a decision made about the term that stays; and a symmetric pair is
  re-normalised, since renaming an endpoint can knock it out of sorted storage order.
- `_normalise_pair()` — one place deciding storage order for both writers, so `narrower` keeps
  its direction. Sorting a directional pair would reverse roughly half of them.

### Changed
- `.agents/rules/vault-tag-taxonomy.md` §6.4 states what retirement does to relations.
- `record_alias()`'s docstring no longer tells callers to invalidate the alias cache; it has
  done that itself since it was written.

### Verification
- `Evelyn/tests/test_tag_retirement.py` — eight new cases: relations follow a replacement,
  direction survives the move, self-relations drop, symmetric pairs re-sort, stronger and weaker
  existing relations resolve correctly, outright retirement clears them, and unrelated rows are
  untouched. Seven confirmed to fail against the old `retire_term`.
- Audited the six live relations: every endpoint resolves to a registered term, so no repair
  was needed.

## [000.006.221] - 2026-09-24 — *One Corpus, One Count*

The usage census read the vault only, so terms living in memory were counted as unused.

### Fixed
- **Tag usage is counted across the whole corpus.** `maintain_master_taxonomy()` built its
  census from `vault_db.get_all_documents()` alone, but §0 of the tag standard governs vault
  notes and memory facts as one corpus under one vocabulary. Seven registered terms used only
  by memory facts therefore reported zero uses, and zero uses is what eventually proposes a
  term for retirement — five of them were unprotected and on that clock. The new
  `census_tag_usage()` counts vault documents, live memory facts and live procedures (tagged
  from the same vocabulary under AGENTS §10), and returns the per-substrate record counts
  alongside the tally.

### Changed
- **The circuit breaker distinguishes an empty substrate from an unread one.** Maintenance
  aborts if the census read raises, and still aborts when the vault reads empty — a partial
  census is worse than none, because every term the unread substrate holds reports zero and
  zero starts the retirement clock. Memory alone does not satisfy the breaker.
- **Retirement proposals say what was searched**: "nothing in the vault or memory has used
  this term", rather than "no document".
- Seeding and maintenance now share one tag-tallying helper instead of two copies of the loop.

### Verification
- `Evelyn/tests/test_tag_usage_census.py` — four cases covering each substrate, live-only
  filtering, survival of a memory-only term through maintenance, and the empty-vault abort.
  Confirmed to fail against the vault-only census.
- A corrected pass over production updated 278 stale usage counts and proposed no retirements.

## [000.006.220] - 2026-09-24 — *Name the Thing, Not the Shelf*

The admission queue was filling with container words, and none of its proposals could be
backfilled on approval.

### Changed
- **Every tag-producing prompt now rejects umbrella terms.** They taught the flat *format* but
  never "prefer the specific term over the container", so extraction kept proposing `work`,
  `home`, `tools`, `pets`, `wellness`, `environment` — the folder headings a flat vocabulary
  removes, which match so much that they narrow nothing. The rule now names them and gives the
  substitution: `firewall` not `work`, `cat` not `pets`, `thermostat` not `home`. Applied in
  `fact_extractor`, `fact_splitter`, `fact_deduplicator` and the split-preview prompt.
- **Admission is proposed at insertion, not at parse time.** v000.006.216 added `source_ids` so
  approval could backfill an admitted term onto the facts that wanted it, but only
  `context_manager` was wired. `fact_extractor` proposed from inside its YAML parser, ~400 lines
  before the row exists, and `fact_splitter` proposed for children that were not rows yet and
  might never be — the split can be rejected. Every one of the 20 pending proposals therefore
  had an empty trail and could not be backfilled. The extractor now proposes after
  `insert_entry` with the row id, and the splitter's proposal moves to the apply path, which
  uses the ids `split_entry` returns.
- **Pending queue cleared** (20 proposals) so it refills with ones that carry a trail. The
  terms dropped are recorded in `scratch/` rather than lost; resolved proposals are untouched,
  being a record of what was decided.

## [000.006.219] - 2026-09-24 — *A Facet Is Not a Subject*

The tag librarian was enabled on 2026-09-23 and rewrote 115 documents overnight. Two came out
carrying two `type/` tags, which §3.4 forbids.

### Fixed
- **Subject indexing could resolve a phrase to a facet.** `audit_document_tags` runs the §4
  profile pass first, and that pass is correct — it settles one `type/` tag and drops any other.
  The subject pass then appended whatever it resolved, with no facet guard. Because the registry
  holds facet values as terms, a phrase like "reference" looks up `type/reference` successfully,
  so pass 2 silently undid pass 1. `reconcile_subjects` now refuses any `type/`, `motif/`,
  `setting/` or `event/` term, in both its direct and hyphen-decomposed branches. What facet a
  document carries is decided by its class, never by what it is about.
- **A §3.4 backstop at the merge point.** Whatever the passes do, exactly one form-axis tag
  survives; the profile pass decides which, and a conflict is logged rather than resolved
  silently.
- **The two affected Reference Library chapters repaired**, keeping `type/reference`.

### Added
- **The semantic pass now records what it rewrote.** `librarian_activity_log` was only ever
  written by the master librarian, which is disabled, so this pass changed 115 files and left
  the log empty — which reads as "it did nothing" and hid the rule violation for a day. It now
  logs the tags added and removed, and any type conflict it resolved. A pass that edits the
  vault unattended has to be reviewable afterwards.
- `Evelyn/tests/test_subject_pass_facet_guard.py`, verified to fail against the previous code.

### Notes
- Reference Library documents are excluded from RAG *retrieval*, not from the index. They are
  rows in `vault_documents` like any other note, so the librarian audits and rewrites them —
  which is how these two were reached.

## [000.006.218] - 2026-09-23 — *One Writer*

An incident fix. "Only the custodian writes to Chroma" was a documented guarantee that nothing
enforced, and on 2026-09-23 it broke. `evelyn_reference`'s `link_lists.bin` grew to 891GB (1.75TB
apparent) and filled the WSL disk. SSH and every service that needed to write then failed.

### Root cause
- **Two processes wrote one HNSW segment.** A forced `rebuild_chroma_collection.py` was started
  in the background while the engine ran. It dropped the collection, re-enqueued 2,750 chapters,
  and then **drained the queue itself** alongside the engine's custodian; its own plan text said
  "the engine's Chroma custodian also drains it". Each process keeps a private in-memory copy of
  the index and persists it on write, so the two overwrote each other. `length.bin` ended up full
  of vector bytes: every entry read `0x3F800006`, a float near 1.0, where a link-list size of at
  most about 200 bytes belongs. The next persist then wrote a ~1GB link list for each of 7,288
  elements.
- **The engine made the same mistake on its own.** Its startup auto-repair
  (`repair_corrupted_chroma(background=True)`) launched the rebuild detached, then started the
  custodian, which put a second writer on the store every time it repaired.
- `acquire_chroma_write_lock()` existed but only `remap_document` took it. The drainer never did.
  A per-write lock would not have been enough anyway, because processes that take turns still
  persist diverging copies.

### Added
- **Writer lease** (`chroma_rag.claim_chroma_writer`). This is an `flock` on
  `chroma_db/.chroma_write.lock`, held for the life of the owning process and recording its pid,
  role and start time. The engine's custodian claims it at startup. `drain_sync_queue` and every
  `direct_*` write require it, so any other writer gets `ChromaWriterBusy`, naming the holder,
  before it touches a segment or claims a queue row. The kernel releases it on exit, so a crash
  cannot leave it stuck.
- **`chroma_rag.acquire_offline_writer`** for maintenance scripts. It refuses while
  `evelyn.service` is active, activating or restarting, and refuses while any process holds the
  lease. The refusal prints `[REFUSED]` with the fix to stderr, logs who tried what to the journal
  (`journalctl -t <script>`), and exits `3`. The one exception is the engine's own startup repair,
  identified by its parent being the unit's main pid.
- `Evelyn/tests/test_chroma_writer_lease.py` (11 tests).

### Changed
- **Startup repair runs synchronously, before the custodian claims the lease.** When the engine
  starts the rebuild, it only drops and re-enqueues; the custodian embeds.
- **The custodian waits rather than fights.** If another process holds the lease at startup, it
  logs that once and keeps writes queued until the lease is free. Once it holds the lease, it
  recovers every `processing` row, regardless of age, since none of them can be live.
- **`remap_document` returns False when another process writes.** This covers the vault watcher
  running beside the engine. The caller then re-ingests through the queue, which costs an
  embedding but never adds a second writer.
- **`rebuild_chroma_collection.py --execute` and `--reclaim-orphans --execute`** now refuse to
  run while the engine is up.
- **`sync_full_vault_to_chroma.py`** is guarded the same way, and its docstring now says it
  deletes *every* collection. It now clears every rebuild strategy's sync state and re-enqueues
  every rebuildable collection. Previously it never cleared `REFERENCE_SYNC_STATE`, so
  `evelyn_reference` stayed empty after a reset. It also keeps the lock file while purging, and
  embeds the queue itself.

### Documentation
- `ROLLBACK.md`: the full re-sync is now marked destructive and limited to full reverts. A new
  section, *Repairing a Single Chroma Collection*, covers stopping the engine, rebuilding and
  restarting.
- `AGENTS.md` §2: new *ChromaDB Single-Writer Rule*. Agents write by enqueueing, run direct
  maintenance only with the engine stopped and in the foreground, and report a refusal rather
  than working around it.
- `reference/engine_architecture.md` §7.1: the single-custodian guarantee now describes how it
  is enforced.

## [000.006.217] - 2026-09-23 — *Two Kinds of Connection*

A foundation fix, not a workaround. §0.2 adopts the full ISO 25964 relation model — `UF`/`USE`,
`BT`/`NT`, `RT` — and §6.2 specifies equivalence, but nothing ever specified hierarchy.

### Changed
- **§6.4 rewritten as the relations layer.** The post-coordinate revision lifted the rule
  forbidding an `RT` between a term and its ancestor — correctly, since the flattened vocabulary
  no longer stated those relations — but did not update the section's definition to match or
  write the missing hierarchy rules. The result said in one paragraph that `RT` excludes broader
  terms and in the next that broader terms are exactly what to record. It now holds both,
  discriminated by `kind`: `related` (associative, symmetric) and `narrower` (`term_a` is a kind
  of `term_b`).
- **Hierarchy is a flat relation, not a tree**, recorded in §0.1 as a deliberate divergence from
  ISO. A parent/child tree is the structure this vault removed — `journaling` hung under 31
  parents, 76% of the vocabulary used once. The relations table keeps the information without
  the mechanism: no single parent, no path, no inheritance, no depth.
- **§6.3's admission form** promised a "Suggested parent — the `BT` it would hang from" with
  nowhere to put the answer. It now names the broader term and points at §6.4.
- **Equivalence is explicitly out of scope** for the relations layer. Two interchangeable terms
  are an alias (§6.2), which is why `intimacy` ~ `romance` was wrong to propose as a relation.

### Fixed
- **Direction was about to be lost.** `master_tag_related` shipped one release ago with
  `CHECK (term_a < term_b)`, to keep a symmetric pair from being stored twice. That constraint is
  wrong for a hierarchical relation, which is directional: `lucid-dreaming` is a kind of `dream`
  and sorts second, so alphabetical ordering silently reverses it. Migration `000.006.217`
  rebuilds the table with `kind` and without that constraint; ordering is now the writer's
  business, and re-recording a pair the other way replaces it rather than adding a second row.
  Existing rows carry over as `related`, the safe reading.
- **The six seed relations reclassified** now the distinction exists: `cat`, `storm` and
  `lucid-dreaming` are `narrower`; `gothic`~`victorian`, `ancestry`~`genealogy` and
  `music`~`playlist` are `related`.

### Notes
- The tag embedding was investigated and is **not** misconfigured. Queried as it is indexed —
  with a bare term — it is well separated: `coffee` returns `caffeine` 0.19, `tea` 0.21,
  `brewing` 0.23. The poor results reported earlier came from querying it with a full sentence
  against an index of one-word terms, where the spread collapses to 0.04 across right and wrong
  alike. Term-to-term queries are a better source of relation candidates than the co-occurrence
  counts the curation script currently uses.

## [000.006.216] - 2026-09-23 — *What Else Connects To This*

Two gaps left open earlier, both prerequisites rather than polish.

### Added
- **Approval now reaches the facts that asked for a term.** `admit_proposed_term` registered a
  term and stopped; proposals recorded no `source_ids` at all, so there was no trail back.
  Harmless while admission is propose-only — the writer stores the tag regardless — but it is
  the reason unregistered tags cannot yet be *withheld*: the tag would never reach the fact and
  approval could not put it there. Proposals now record the requesting entries, a second
  requester widens an existing proposal rather than being dropped, and approval backfills.
  `context_manager` proposes after the insert for this reason; proposing first left the trail
  empty.
- **The associative (`RT`) layer** (§6.4), migration `000.006.216`. Flattening the hierarchy
  deleted relational information without replacing it: `health/sleep` stated that sleep belongs
  with health, and `health` + `sleep` states nothing. `master_tag_related` is where that lives —
  symmetric, stored once, with a retrieval weight and a tier separating a curated relation from
  a proposed one. `/api/taxonomy/vocabulary` returns a term's relations beside its lexical
  near-matches.
- **`scripts/curate_tag_relations.py`** proposes candidates from co-occurrence and records only
  what it is handed, because the standard forbids inferring relations and activating them
  automatically. Two filters, both learned from the data: a pair must appear on several
  documents *and* across independent parts of the corpus. Without the second, a single ten-tag
  appliance manual yields forty-five "relations" that are one document's tag list — the same
  failure the standard records for the near-miss similarity band.
- **Six seed relations**, each one the hierarchy used to state and nothing now does:
  `cat`~`pet`, `dream`~`lucid-dreaming`, `storm`~`weather`, `gothic`~`victorian`,
  `ancestry`~`genealogy`, `music`~`playlist`. 44 further candidates await review.

## [000.006.215] - 2026-09-23 — *The Librarian Returns*

### Changed
- **`TAG_LIBRARIAN_ENABLED` back on**, off since the classification logic went under revision.
  What gated it is cleared: both substrates measure 0 unregistered terms and 0 non-facet
  hierarchies, the five duplicate pairs are collapsed with `UF` aliases, every tag-producing
  prompt teaches the §5 format with a test enforcing it, and unregistered terms now route to
  review instead of entering the vocabulary silently. The pass runs after 20 minutes idle and
  touches 2 documents per run.
- **`MASTER_LIBRARIAN_ENABLED` stays off, now by choice rather than by blocker.** Its comment
  named parent-tag inheritance as the reason; v000.006.214 made that opt-in and facet-safe, so
  the hazard is resolved. It rewrites vault files unattended at 5 documents per burst, so it
  waits until a cycle of the narrower pass has been reviewed.

## [000.006.214] - 2026-09-23 — *One Concept, One Term*

Phase F, less the final switch.

### Changed
- **Five near-duplicate pairs collapsed**, each one concept recorded twice: `family-history` →
  `genealogy`, `artifacts` → `artifact`, `napping` → `nap`, `relationships` → `relationship`,
  `symptoms` → `symptom`. The last is settled by §6.3.2 — number form is singular by rule —
  where live usage was nearly even. Each retirement records a `UF` alias rather than erasing
  (§6.2), so a document or query phrased the retired way still resolves. 8 vault notes and 2
  memory entries retagged; verified 0 occurrences remain in files, index or memory.
- **`usage_count` refreshed.** The column was stale — 475 of 676 terms read zero — because the
  pass that maintains it runs under `TAG_LIBRARIAN_ENABLED`, which is off. A zero therefore
  meant "not counted", not "unused", and choosing survivors from it would have retired the
  wrong side of at least one pair. Now 630 nonzero; the 46 zeros are reserved terms inside the
  90-day grace.
- **Parent-tag inheritance is opt-in and facet-safe.** `audit_single_document` copied a
  folder's `_index.md` tags onto every document inside it, defaulting to on. Since
  v000.006.213 every index note carries `type/moc`, so inheritance would have given each
  chapter a second form-axis tag where §3.4 allows exactly one — the old filter dropped the
  literal `"moc"` but not `type/moc`. It now excludes every facet prefix: a facet states
  something about the document itself, which a folder cannot know about its contents.
- **§9 migration table corrected.** It listed steps 8, 12 and 13 as pending when all three are
  done, and step 9 as pending when v000.006.202 superseded it outright.

## [000.006.213] - 2026-09-23 — *One Thing Named Type*

Phase E3. Three unrelated things shared the name `type`; now one does.

### Changed
- **The legacy `type:` frontmatter property is retired**, across 2,880 notes. It was an
  un-standardised ingestion field the PDF importers wrote, unrelated to the `type/` facet tag
  that the taxonomy defines and the §4 class profiles gate on. Each note now carries the
  equivalent facet tag instead:

  | was | becomes | notes |
  |---|---|---|
  | `reference-chapter` | `type/reference` | 2,803 |
  | `literature/card` | `type/moc` | 47, all `*_index.md` landing notes |
  | `document/card` | `type/media/text` | 30, already carried it |

  Verified afterwards: 0 notes retain the property, and no note carries more than one `type/`
  tag, which §3.4 requires.

- **The four writers that emitted it were fixed first**, so it cannot return. They were also
  producing unregistered vocabulary: `reference-library`, `reference-index`,
  `literature/reference`, `source/pdf`, and the book title as a tag — a named work is a link,
  not a subject term (§2), and it was already recorded in `source`.

- **The RAG guard no longer reads the property.** Its 2,803 documents remain withheld by two
  other rules, verified before removal: 2,790 live under a subdirectory `RAG_EXCLUDED_SUBDIRS`
  already excludes by path, and the other 13 are marked `sensitivity: private`. Zero relied on
  the retired branch alone.

### Notes
- The migration's first pass silently skipped 2,790 of 2,880 notes: the pattern removing the
  property required a trailing newline, which the frontmatter split had already consumed when
  `type:` was the final key. The tag was added and the property left behind. The script's own
  verification caught it, and the newline is now optional.
- Chunk metadata in the reference collection still carries the retired `type` key. Nothing
  reads it, and refreshing it would mean re-embedding 2,752 documents for a field no consumer
  consults, so it is left to age out of its own accord.

## [000.006.212] - 2026-09-23 — *Refuse to Overwrite It Too*

### Fixed
- **`sensitivity: secret` now denies writes as well as reads.** v000.006.211 guarded
  `read_file` only, so a tool could still overwrite or append to the very note it refused to
  show — and for a note holding recovery codes that is unrecoverable. `write_file` consults the
  same canonical guard, in both overwrite and append mode. `private` is unaffected: it withholds
  automatic retrieval, not deliberate action.
- **A long-standing test failure in `test_terminal_agent.py`.** `test_read_file_allowed` mocked
  `open` but not the existence check `read_file` gained afterwards, so it short-circuited to
  "File not found" and had been failing since. It now stubs the existence check and the vault
  name resolver, so it exercises the read path it names.
- **Tests wrote to the production terminal-approvals store.** Staging a write appends to it,
  and the path was not sandboxed, so a test exercising `write_file` left a real pending approval
  in the user's queue. `conftest` now redirects it alongside the memory and chat databases
  (AGENTS §2).

## [000.006.211] - 2026-09-23 — *Private Means Private*

Phase E. The `sensitivity:` frontmatter property was read nowhere, so 46 of the 49 notes marked
private or secret were sitting in the RAG index.

### Fixed
- **Sensitive notes were retrievable.** Tax records, medical conditions, medication lists,
  allergy results and child-support filings were all indexed and could be pulled into context.
  Only the credentials note was excluded, and only because it separately carried
  `rag_exclude: true` — exactly 5 notes vault-wide used that mechanism. No folder-level
  protection covered the personal subtrees they live in either.
- **The `reference-library` tag branch in `is_rag_excluded_source` was dead** — the tag was on
  0 of the 2,838 library notes and 0 index rows, and is not a registered term, so nothing could
  set it. The `type: reference-chapter` check is what actually covers those documents. Removed.

### Added
- **`sensitivity:` is now honoured, with the two levels scoped differently.** `private` is
  withheld from RAG but stays readable through tools, because medical and financial context is
  useful when the user asks for it and inappropriate injected unbidden. `secret` — credentials,
  recovery codes — is withheld from retrieval *and* tools. Configured in
  `SENSITIVITY_RAG_EXCLUDED` and `SENSITIVITY_TOOL_DENIED`.
- **`frontmatter_utils.read_sensitivity()` / `is_tool_denied()`** — one canonical guard rather
  than a check copied into each tool (AGENTS §8), consulted by `terminal_agent.read_file` and
  `evelyn_tools.search_vault_notes`. A search hit names the note and quotes its gist, which is
  itself a disclosure, so secret notes are filtered from results as well.
- **The level is recorded on every chunk**, so the retrieval guard can withhold a document
  indexed before the rule existed without re-reading it from disk.
- **`scripts/purge_sensitive_from_rag.py`** — the incremental sync skips unchanged files
  *before* reading their frontmatter, so making the property exclude a document would not have
  removed one already indexed; those files never change on disk. This purges them explicitly
  and records the exclusion in the sync state. All 46 removed and verified.

### Notes
- The purge script's own verification initially reported eight false failures: it re-read the
  cached `PersistentClient` singleton and got a pre-delete snapshot. It now reopens the store
  through a fresh client, because a privacy tool that cries wolf gets ignored.

## [000.006.210] - 2026-09-23 — *The Rule Beats the Example*

v000.006.204 rewrote the extraction prompts. Two of them were only half rewritten, and one
night of idle processing undid most of the Phase D memory reconciliation.

### Fixed
- **`fact_splitter` rule 4 and `fact_deduplicator` rule 7 still taught the hierarchy.** In both
  files the few-shot example had been changed to flat terms while the numbered rule above it
  still read "MULTI-TIER DOMAIN TAXONOMY" — the splitter citing `Tech/Python/FastAPI`, the
  deduplicator instructing "lowercase hierarchical domain trees ... slashes joining levels".
  The prompt contradicted itself and the rule won. Both now state the §5 format, matching the
  wording already in `fact_extractor`.
- **Measured damage:** Phase D left the memory subsystem at 184 registered terms and zero
  unregistered. One overnight run returned it to 289 distinct terms, **101 unregistered across
  70 entries** — 43 from the merge path, 21 from the split path, 6 from extraction. The split
  pipeline was minting fresh hierarchies from parents that had just been given flat tags.

### Added
- **`test_prompts_teach_the_standard.py`** — a deterministic check that no module building a
  tag-producing prompt contains the retired vocabulary, in a rule or an example. A prose sweep
  found one of the three instances and reported the job done; this replaces that judgement with
  a grep the suite enforces. It is negation-aware, because a rule citing `Home/Coffee/Espresso`
  in order to forbid it is correct prompt writing, and it is verified to fail when the old rule
  is reintroduced.

## [000.006.209] - 2026-09-22 — *Tests Own Nothing Real*

The test harness sandboxed the vault database but never the memory database.

### Fixed
- **Tests wrote to the production memory store.** `conftest.py` isolated `VAULT_BASE_DIR`,
  the write paths and `VAULT_DB_PATH`, but left `cfg.MEMORY_DB_PATH` pointing at the user's
  data. Any code path a test exercised that reached memory wrote there — a YAML-parsing unit
  test raised four `tag_admission` proposals into production, because the parser proposes
  unregistered terms and its fixture tags are deliberately non-conformant. `MEMORY_DB_PATH` and
  `CHAT_DB_PATH` now point into the per-test sandbox, and the memory schema is created there so
  an incidental read gets a valid empty store instead of the "no such table" error that
  previously pushed tests onto the real database (AGENTS.md §2).
- **`test_context_split` asserted the retired tag format.** It expected
  `Home/Coffee/Espresso` and had been failing since v000.006.204 changed the split prompt;
  targeted batches had not covered the file. Fixtures and assertion now use flat registered
  terms.

### Changed
- Purged the leaked proposals from the production store.

## [000.006.208] - 2026-09-22 — *A Name a Filesystem Accepts*

A wikilink target may legally hold characters a filename may not.

### Fixed
- **Ghost stubs created filenames Windows cannot represent.** `[[Nier: Automata]]` produced
  `Nier: Automata.md`; Syncthing refused to sync it. `stub_relpath()` — the single chokepoint
  all three creation paths share — now routes the stem through the canonical
  `string_utils.sanitize_filename()` rather than interpolating the raw target (AGENTS §8).
- **Sanitising alone would have orphaned the links.** Every existing `[[Nier: Automata]]` names
  the note by its unsanitised form, so the stub now carries that original as an alias, and the
  title keeps it verbatim. A target that needs no sanitising gains no redundant alias.
- **The two affected notes were renamed** and given their aliases, with the vault index updated
  to match.

## [000.006.207] - 2026-09-22 — *Nothing But Its Type*

Ghost stubs were tagged `stub, concept`. Neither term is registered, and the class profile
forbids a domain tag on a stub outright.

### Fixed
- **Ghost stubs carried two unregistered terms.** The generator defaulted to
  `["stub", "concept"]` in four places in `link_librarian` and once in `evelyn_server`. The
  standard defines `type/stub` as *"an auto-generated ghost stub carrying nothing but its type
  until a human fills it in"* (§3.4), and the §4 class profile marks domain, motif, setting and
  event **forbidden** for the class — so `concept` was not merely retired, a stub may carry no
  domain tag at all. All five sites now emit `type/stub`, matching the 124 stubs already in the
  vault.
- **163 queued stub proposals were rewritten** from `<tags>stub, concept</tags>` to
  `<tags>type/stub</tags>`, so approving the backlog no longer injects unregistered vocabulary.
- **`index_tag_in_chroma` wrote the `general` placeholder** into vector metadata via the same
  fallback removed from the registry in v000.006.205, leaving the embedding and the row
  disagreeing about an uncategorised term.

### Changed
- **`typography` and `decluttering` registered as protected curated terms**, approved in
  review. `decluttering` restores a Pass 1 decision that was applied as a deletion rather than
  a promotion to term.
- **`observation` removed.** Admitted in review, then withdrawn: as a subject term it describes
  the act of observing, which is true of a large share of the 480 live assistant-observation
  entries, so it separates almost nothing. Registry row and vector both removed, and the stale
  sync-queue job cleared.

### Notes
- Registry and vector store verified at exact parity afterwards: 673 terms, 673 embedded, no
  orphans in either direction.

## [000.006.206] - 2026-09-22 — *Uncategorised Is an Answer*

Caught by admitting a real term through the card shipped an hour earlier: the reviewer chose
"uncategorised" and the placeholder `general` was written anyway.

### Fixed
- **An explicitly empty category was treated as an absent one.** The approval handler read
  `req.category or prop["suggested_category"] or ""`, so selecting "— uncategorised —" (which
  sends `""`) fell through to whatever the proposal was stored with. For every proposal raised
  before v000.006.205 that is the placeholder `general`, so the reviewer's choice was silently
  overridden and `general` entered the registry as a 27th category with one member. The chosen
  value is now distinguished from a missing field by identity, not truthiness.

### Changed
- **Backfilled the placeholder out of live data.** `general` cleared from the one registry row
  that received it and from the pending `tag_admission` proposals still carrying it. Already-
  resolved proposals keep theirs: they are a record of what was decided, not a queue to fix.

### Notes
- `scripts/update_frontmatter.py` was audited for the retired tag format. The markdown path is
  clean — it never writes or rewrites a tag value, only adding `tags: []` when the key is
  absent, so it cannot introduce a non-conformant term. The Python/PowerShell header path emits
  the inline `#tag` style and preserves whatever forms already exist, including underscored
  ones (`#idle_time`, `#end_to_end`). These are source-file header comments in a repository that
  sits outside the vault root, nothing parses them into the vocabulary, and they reach neither
  the registry nor the index — so they are inert rather than conformant, and were left alone.

## [000.006.205] - 2026-09-22 — *Show the Term*

The tag-admission review card rendered everything about a proposal except the term being
decided on, and its Approve button returned 500.

### Fixed
- **Approving a tag proposal always failed.** The card built no request body, and FastAPI
  passes `None` for an optional body model when none is sent, so the handler's
  `req.modified_text` raised `AttributeError` before it reached the term. Every field is now
  read defensively. Covered by a regression test that fails against the previous handler.
- **The card showed the origin string instead of the term.** `tag_admission` and
  `tag_retirement` fell through to the generic proposal card, which renders
  `merged_observation` — for these, the writer's name (`extracted fact (Cat04-A)`) — while the
  term itself sits in `topic` and was never displayed. Both types now have their own card
  leading with the term.
- **`general` was proposed as a category.** A flat term has no facet, and the old fallback
  filled `suggested_category` with `general`, which is not one of the registry's categories —
  approving would have written it into the vocabulary as though it meant something. A flat
  term is now left uncategorised for the reviewer to decide.
- **The registry row and its embedding could disagree.** `admit_proposed_term` derived a
  category for the SQLite row but passed the caller's raw (possibly empty) value to the vector
  index. Both now receive the resolved value.
- **Stale docstrings** in `propose_tag_admission` and `admit_proposed_term` still described
  zero-usage pruning, which v000.006.202 removed entirely; they described the retirement-proposal
  path instead of a delete that no longer exists.

### Added
- **`GET /api/taxonomy/vocabulary`** — the registry's categories with term counts, plus
  registered terms lexically near a proposed one. A reviewer cannot judge whether a term earns
  a place without seeing what the vocabulary already holds. Matching is deliberately lexical:
  the taxonomy embedding returns 0.33–0.55 distances for correct and unrelated terms alike, so
  a nearest-neighbour list there reads as authoritative while being noise.
- **Admission card**: editable term, a category selector populated from the registry, and the
  similar-terms list, with the reminder that a near-duplicate splits a post-coordinate concept
  in two.
- **Retirement card**: an optional preferred term, which records an equivalence so documents
  phrased the retired way still resolve (§6.2), rather than erasing the record.

### Changed
- **`AGENTS.md` §10** required starter procedures to define *"hierarchical domain tags"* — the
  retired pre-coordinate format, still mandated in the rules file after the vocabulary moved.
  It now requires controlled-vocabulary subject terms, with a new bullet stating the format,
  pointing at the schema, and noting that memory facts may not carry `type/*`, `status/*` or
  `obsidian-graph/*` (§0.1).

## [000.006.204] - 2026-09-22 — *Teach What You Enforce*

The engine normalises tags to the §5 standard on write, and then asks the model for the
opposite shape. Extraction prompts were still teaching the pre-coordinate form.

### Fixed
- **The fact extractor instructed the model to break the standard.** Rule 5 read
  *"MULTI-TIER DOMAIN TAXONOMY: Structure tags as hierarchical domain trees (`Tech/Python/FastAPI`,
  `Home/Coffee/Espresso`) ... Use TitleCase with underscores for named entities (`John_Smith`,
  `FastAPI`)"* — the entity/concept branch §5 abolished precisely because it lets one term fork
  into `ai` and `Ai`. It now asks for flat controlled-vocabulary terms: lowercase, hyphens join
  words, named entities under the same rule as any other term, and a preference for existing
  terms over near-duplicates.
- **Five few-shot examples reinforced it**, which is the strongest way to teach a model a
  format. `Home/Coffee/Espresso` → `coffee, routine`, `Relationship/Dynamics, Collaboration` →
  `relationship, software-architecture`, `Pets/Cats/Routine` → `cat, pet, routine`, and so on.
  Every replacement term is verified present in the registry and already in canonical form, so
  the examples cannot themselves propose unregistered vocabulary.
- **The same shape appeared in three more prompts**: the split-preview rule and examples in
  `evelyn_server`, the examples in `fact_splitter`, and the `merged_tags` example in
  `fact_deduplicator` (`"Tech/Python/FastAPI, John_Smith"`).

This closes the loop that would have made memory vocabulary reconciliation pointless: retagging
existing facts while extraction keeps minting `Relationship/Dynamics` only re-creates the drift.
It is the likeliest explanation for memory holding 268 hierarchical tags and 273 of 275 atoms
unregistered.

### Notes
- Procedures, reference docs and XML injection templates carried no tag-format guidance. Two
  memory facts describe past tagging deliberations (#9321, #9429) in the past tense; they are
  historical record rather than instruction and were left as written.

## [000.006.203] - 2026-09-22 — *A Date Is Not a Subject*

The time axis moves out of `tags` and into an `occurred` frontmatter property.

### Changed
- **Dates are a property, not a tag.** `CY-YYYY/MM/DD` existed in that shape only because an
  Obsidian tag cannot begin with a digit. A date is an attribute of a note rather than a
  subject the note is about; it is the one facet whose primary access pattern is a **range**
  ("notes between March and June"), which a tag cannot answer without enumerating every day;
  and as a tag it forced every consumer in the engine to special-case it. The canonical form
  is now plain EDTF in `occurred:` — `2026-09-19`, `2026-05`, `XXXX-11-16` — and a full date
  parses as a native YAML date, which is what makes those range queries work. An undated note
  omits the key, which states "no date known" more clearly than a token meaning the same.
- **`apply_application_profile()` takes the date.** It reads the `occurred` property for the
  time facet instead of scanning tags, and still accepts a legacy `CY-` tag as present so a
  not-yet-migrated note is not reported as missing its date. Time is `REQUIRED` for four
  classes (`log`, `report`, `journal-entry`, `dream`) and `FORBIDDEN` for none.
- **`canonicalize_occurred()` replaces `canonicalize_date_tag()`.** Same EDTF semantics —
  reduced precision and unspecified digits stay distinct — and it accepts every retired tag
  spelling (`CY-2026/09/19`, `Cy_Yyyy/11/16`, `cy-2025`) so existing data converges on one
  canonical value. It still refuses ordinary vocabulary such as `cybersecurity`.
- **Writers emit the property.** `dream_manager` and `journal_manager` write `occurred:`.
  The Session Notes template no longer teaches the retired `#CY-YYYY/MM/DD` body hashtag.

### Removed
- **Every date special-case in the tag pipeline.** Six `startswith("CY-")` guards in
  `tag_synonym`, the date-anchor bypass in `normalize_tag_format`, `canonicalize_date_tag()`
  and its regex, the `^CY-...$` entry in `TAG_LIBRARIAN_EXCLUSIONS`, and the `CY-` reference
  in the deduplicator prompt. A tag type that every consumer had to exempt was not really a
  tag.

### Fixed
- **The frozen migration normaliser no longer reads mutable configuration.**
  `_frozen_normalize_tag_format_000_004_002` delegated to `cfg.TAG_LIBRARIAN_EXCLUSIONS`,
  reasoning that the original did too. That held only while the config was stable: removing
  the `CY-` pattern from the live list would have made a replay of that already-applied
  migration start mangling date anchors it had always preserved. The patterns are now pinned
  in the frozen copy, which is what AGENTS.md §5 immutability actually requires.

### Migration
- **`scripts/migrate_date_tags_to_property.py`** rewrote **904** vault notes: 883 now carry an
  `occurred` date, 23 undated notes simply lost the tag, and no note had a conflicting
  existing value. Dry run by default.
- **`scripts/sweep_legacy_date_lines.py`** removed the retired footer line
  (`CY-2025/11/23 Journal/Evelyn`, `**Tags:** CY-2026/01/10`) from **285** journal and dream
  bodies. A line is removed only when its date is already safely recorded — all 285 notes
  already carried a matching `occurred`, verified with zero mismatches — and a line carrying
  anything beyond the retired metadata is left alone, so prose is never swept. One `CY-`
  reference remains in the vault, inside a fenced code block preserving an original note
  verbatim; that is quoted content, not metadata.

### Fixed — date formats that were still being taught
- **A live memory fact taught the retired format.** Fact #9158 read "Evelyn's journaling
  protocol requires specific tagging (#CY-YYYY/MM/DD)" and was being retrieved into context,
  reinforcing a convention that no longer exists. Corrected to describe the `occurred`
  property and re-embedded. (Curated directly per AGENTS.md §5 — a single fact edit is not
  migration material.)
- **`TAG_LIBRARIAN_FORMAT_RULES` advertised a `date_anchor_exempt` rule** that no longer has
  anything to exempt.
- **`is_excluded_tag()`'s fallback default still carried the `^CY-...$` pattern**, so a
  missing config would have re-protected date tags.
- Stale comments and docstrings in `dream_manager`, `tag_librarian` and
  `generate_structure_review`; test fixtures in `verify_tools` and `test_tag_format_standard`
  that used a date tag as a sample protected tag.
- **The transitional `CY-` acceptance in `apply_application_profile` is gone** now that the
  vault carries no date tags, so no date special-case remains in the tag pipeline at all.

Model-facing tool definitions needed no change: they already specified `YYYY-MM-DD`, which is
the canonical `occurred` form. No procedure, reference doc or XML injection template
referenced the tag form.

## [000.006.202] - 2026-09-22 — *Nothing Leaves the Catalogue*

### Changed
- **Taxonomy maintenance no longer deletes anything.** It deleted every term with zero usage,
  which conflates two different things: a controlled vocabulary is an *authority file* — the
  set of terms judged legitimate — while usage counts describe the *index*, which is merely
  what happens to be tagged at this moment. Letting the second govern the first makes content
  churn drive vocabulary churn: delete a note and its unique terms vanish from the vocabulary,
  so restoring that note days later either re-mints them in some other surface form or forces
  them back through review. In post-coordinate classification a term with no current uses is
  a perfectly good axis value awaiting its first document.
- **Long-unused terms are proposed for retirement instead.** `propose_tag_retirement()` raises
  a `tag_retirement` proposal after a grace period (`TAG_RETIREMENT_GRACE_DAYS`, default 90),
  because a reserved term legitimately has no uses yet. Curated (`protected`) terms are never
  proposed at all. The decision becomes a human one.
- **Retiring records an equivalence rather than erasing the record.** `retire_term()` accepts a
  preferred term and writes a `UF` alias, so a document or query phrased the retired way still
  resolves — which is what makes a collapse permanent instead of something the next extraction
  undoes (§6.2). Without a replacement the registry row is simply removed. Approval is wired
  into the review endpoint, which reads the optional replacement from the edit field.

### Added
- **`taxonomy_db.record_alias()`** — a public write path for equivalences. Until now only
  migrations could record one, so retirement had no way to leave a pointer behind.

### Removed
- **The zero-usage prune and its circuit breaker.** The 15% `TAG_LIBRARIAN_MAX_PRUNE_RATIO`
  breaker guarded an operation that no longer exists; the `protected` column it motivated
  stays on as a provenance marker (curated vs. inferred) and now gates retirement proposals.
- **`Evelyn/tests/test_taxonomy_prune_protection.py`** — superseded. It asserted that
  unprotected zero-usage terms are pruned, which is the behaviour this release removes; the
  protection contract it covered is now tested in `test_tag_retirement.py`.

## [000.006.201] - 2026-09-22 — *Ask the Librarian First*

### Added
- **Tag admission proposals.** A term the controlled vocabulary does not hold now raises a
  `tag_admission` proposal into the existing review queue instead of entering the vocabulary
  unannounced. `propose_tag_admission()` records the term, the facet it appears to belong to,
  and which subsystem asked for it; `admit_proposed_term()` registers an approved term, and
  the review endpoint dispatches the new type.
- **Approved terms are registered unprotected.** A term admitted through review entered by
  inference rather than from the reviewed vocabulary, so it stays subject to ordinary
  zero-usage pruning. Only curated terms carry `protected` (migration `000.006.196`).
- **Proposal flooding is bounded.** One pending proposal per term however many writers request
  it, duplicates collapse within a call, and the queue is capped so a misbehaving writer
  cannot bury the review UI. Date anchors (§3.8) are exempt from the vocabulary and are never
  proposed for it.

### Changed
- **Memory writers route through admission.** `context_manager`, `fact_extractor` (facts and
  procedures) and `fact_splitter` now put their tags through the check before storing them.

### Fixed
- **A stale test assertion contradicted the format standard.**
  `test_parse_facts_yaml_with_hierarchical_tags` expected `Test_Operator` to survive
  unchanged, while `test_tag_format_standard.py::test_no_entity_branch_survives` requires
  proper nouns to follow the same rule as concepts (`Jane_Doe` -> `jane-doe`). The extractor
  was right and the assertion was wrong; it now expects `test-operator`.

### Notes
- **Proposing, not withholding — deliberately, for now.** An unregistered tag is still stored.
  The memory store predates the controlled vocabulary: measured against the live registry,
  only 2 of its 278 distinct tag atoms are registered, so withholding unregistered tags today
  would strip 99% of tag occurrences (308 of 311) from every newly written fact. Proposing
  records what wants admission without destroying the tagging; withholding becomes the correct
  behaviour once the memory vocabulary is reconciled against the registry.

## [000.006.200] - 2026-09-22 — *One Trip to the Stacks*

### Changed
- **The startup health probe batches into one child process.** `000.006.198` made the probe
  crash-safe by running it in a child, but did so per collection — and the embedding model
  load dominates that cost, so five collections meant five model loads and roughly 20s added
  to every boot. A single child now probes them all, announcing each collection before it
  opens it. Measured 19.9s -> 4.9s with identical per-collection reporting.
- **A crash still identifies the exact culprit, and no longer hides what follows it.** The
  announced-but-unfinished collection is the one that aborted the child; the batch then
  resumes after it, so a second bad segment behind the first is still found rather than
  masked by the first failure.

## [000.006.199] - 2026-09-22 — *One Hand Writes*

Six writers each decided independently what a tag should look like, so one input could enter
the vault or the memory store in several incompatible forms and each form became a separate
term. They now share one normaliser.

### Fixed
- **`research_engine` flattened tag hierarchies on the vault-write path.** Its local slug
  stripped every non-word character, so `tech/python/fastapi` was written as
  `techpythonfastapi`; it hyphenated only runs of whitespace, so `Test_Operator` persisted as
  `test_operator` rather than `test-operator`; and it destroyed date anchors, turning
  `CY-2026/09/22` into `cy-20260922`.
- **`pdf_staging_worker` invented hierarchy from whitespace.** `domain_name.lower().replace(' ', '/')`
  made `Machine Learning` into `machine/learning` and `Owner's Manuals` into `owner's/manuals`,
  fabricating an axis where §5 permits a slash only to name one — and leaking an apostrophe
  into a YAML flow array.
- **`context_manager` applied no normalisation at all** on the memory-write path, so the store
  could hold `Tech/AI` and `tech/ai` as two unrelated terms.
- **`dream_manager`, `journal_manager` and `vault_list_manager`** stripped `#` or slugified with
  underscores and wrote the result straight to frontmatter. `vault_list_manager` keeps the
  underscore slug for template filenames, where it is correct, and uses the canonical form only
  for the tag.

### Changed
- **Admission has a name of its own.** `taxonomy_db.canonicalize_tags()` only rewrites recorded
  equivalences and returns unknown terms unchanged — it never was a gate, though its name
  invited callers to treat it as one. Added `partition_by_admission()` and `is_registered_term()`,
  which answer the question the registry can actually answer, and documented the distinction on
  `canonicalize_tags()` itself.

## [000.006.198] - 2026-09-22 — *Do Not Burn the Library*

### Fixed
- **Vector-store repair no longer destroys healthy collections.** `repair_corrupted_chroma()`
  responded to any health-probe failure by `shutil.rmtree`-ing the entire Chroma directory and
  re-syncing from scratch. With four populated collections that meant discarding tens of
  thousands of good vectors to fix one bad segment — unattended, during startup. Repair is now
  per-collection and delegates to `scripts/rebuild_chroma_collection.py`, which archives the
  store first. A collection with no registered rebuild strategy is reported and left alone,
  because dropping what cannot be regenerated is data loss, not repair.
- **A corrupt segment no longer kills engine startup.** An unreadable HNSW segment fails as
  `SIGSEGV` — the Rust bindings abort the process — so the in-process probe at startup died
  along with the collection it was inspecting, and the repair path it guarded could never run.
  `check_chroma_health()` now probes each collection in a child process and judges it by exit
  code, so a bad segment produces a diagnosis and a targeted rebuild instead of a boot loop.

### Changed
- **`check_chroma_health()` covers every collection, not just the memory one.** It returns
  per-collection detail and an `unhealthy` list; `count` still reports the memory collection
  for existing callers.
- **`REBUILD_STRATEGIES` is canonical in `chroma_rag`.** `scripts/rebuild_chroma_collection.py`
  imports the registry and the probe rather than carrying its own copies, so the engine's
  auto-repair and the operator tool cannot drift apart about what is rebuildable.

## [000.006.197] - 2026-09-22 — *Mend One Shelf*

### Added
- **`scripts/rebuild_chroma_collection.py` — targeted single-collection repair.** The only
  existing recovery path, `chroma_rag.repair_corrupted_chroma()`, deletes the entire vector
  store and re-syncs everything. When one collection is unreadable and the others are
  healthy that discards good vectors and re-embeds tens of thousands of documents to fix one
  segment. This tool drops and regenerates one named collection from its canonical source,
  leaving the rest alone.
- **Crash-safe health probing.** A corrupt HNSW segment fails as `SIGSEGV`, not as an
  exception: the Rust bindings abort the process, so no `try`/`except` can catch it and any
  in-process health check dies with the collection it was inspecting. Every probe therefore
  runs in a child process and is judged by exit code, which is what lets `--list` report on a
  collection that crashes anything opening it.
- **Orphaned segment reporting (`--orphans`).** Segment directories that no collection
  references accumulate silently when a collection is recreated. The tool lists them with
  sizes and reclaims them under `--execute`.

### Notes
- Every destructive path archives the whole Chroma directory first, and a collection with no
  registered rebuild strategy is refused rather than dropped — dropping what cannot be
  regenerated is data loss, not repair.
- Nothing is modified without `--execute`; a readable collection additionally requires
  `--force`.

## [000.006.196] - 2026-09-22 — *Do Not Reshelve*

Four unattended writers were each quietly damaging the catalog, and every one of them
reported success while doing it. This release stops them. No behaviour is added; four
silent failures become correct.

### Fixed
- **The post-migration Chroma hook did nothing.** `execute_post_hooks` contained a `try`
  block whose entire body was a success `print()`. Twenty-two migrations declare
  `post_sync_chroma=True`, and all twenty-two reported a completed sync having embedded
  nothing. The result: 350 of 671 registered terms had no vector, including *every* faceted
  value, so the vector arm of the subject-admission cascade could never match a `type/`,
  `motif/`, `setting/` or `event/` term. The hook now calls
  `tag_librarian.sync_master_tags_to_vector_db()` and reports the count it actually enqueued.
- **Taxonomy maintenance would delete reserved vocabulary.** `maintain_master_taxonomy()`
  prunes every term with zero usage — a sound rule for a folksonomy, wrong for a controlled
  vocabulary, where terms are registered deliberately and may be reserved before first use.
  Fifty-four curated terms then stood to be deleted, among them the eight DCMI `type/media`
  sub-types registered one release earlier and the reserved `event/` values. At 8% of the
  registry it sat under the 15% circuit breaker, so the pass would have proceeded. Curated
  terms are now flagged `protected` and are retained regardless of usage; terms that enter
  by inference remain prunable.
- **Saving a note blanked its index metadata.** `update_vault_note` called
  `vault_db.upsert_document()` with only path, title and mtime, and the `ON CONFLICT` clause
  assigns unconditionally — so the parameter defaults overwrote `tags`, `aliases`, `gist` and
  `rag_priority` with empty values on every API and UI save. `upsert_document()` now treats
  an omitted field as "keep what is indexed" rather than "blank it", and the endpoint reads
  tags and aliases back out of the frontmatter it just wrote.
- **The vault watcher and the vault indexer disagreed on body hashtags.** The indexer
  deliberately harvests frontmatter only — body hashtags are an uncontrolled entry path into
  the vocabulary, and the scan also matched issue numbers, link anchors and code spans. The
  watcher harvested them anyway, so a document's indexed tags depended on which process
  wrote the row last. The watcher now follows the indexer's policy.

### Added
- **Migration `000.006.196` — `protected` column on `master_tag_taxonomy`.** Every term
  currently registered arrived from the reviewed vocabulary (migrations `194` and `195`) and
  is marked protected. The migration also carries `post_sync_chroma=True` to embed the
  backlog the broken hook left behind.

## [000.006.195] - 2026-09-22 — *Form Follows Card*

### Added
- **Migration `000.006.195` — register the eight DCMI sub-types of `type/media`.** The
  standard requires sub-typing on `type/media` (`type/media/text` for a scanned tax return,
  `type/media/still-image` for a fan chart) but `194` read only the top-level `type/` rows, so
  the Pass 2 applier rejected every sub-type. The list is the closed DCMI vocabulary, not
  personal terms, so it lives in the migration itself.

### Changed
- **A sub-type satisfies the type facet.** `apply_application_profile` now accepts
  `type/<class>/<sub-type>` as the document's type tag instead of adding a redundant parent
  and flagging the sub-type as a second type.
- **`media` no longer requires a motif.** The class covers PDF wrapper cards (tax returns,
  court orders, naturalization papers) as well as films and novels; only the latter carry a
  motif, so `FACET_PROFILE` marks it optional instead of reporting thirty false gaps. The
  standard's profile table is updated to match.
- **Pass 2 applier strips retired body hashtags.** When a person card's `Birthday:`,
  `Birthplace:` or `Death:` line moves to a frontmatter property, the inline `#CY-…` and
  `#location/…` hashtags become plain text so they stop registering as tags; inline facet
  hashtags in prose (`Theme Tags: #motif/…`) lose their `#` the same way, since the reviewed
  facets now live in frontmatter.

## [000.006.194] - 2026-09-21 — *Read, Then Named*

### Added
- **Migration `000.006.194` — register the full-read vocabulary.** The seed (`191`) was
  derived from a 235-note sample. This vocabulary was produced by reading the entire personal
  corpus (1,386 notes) and reasoning over it, measuring every candidate's literary warrant with
  a phrase test that folds in the writer's own surface forms, and handing the result to the
  user for line-by-line review. Subject terms, `type/` values, `motif/` / `setting/` /
  `event/` seeds and the reviewed alias (UF) table are all read from the gitignored review
  document, so — as with `191` — no term text enters version control.
- **Alias deferral rule.** An alias whose text is itself a registered term is logged and
  skipped rather than written. Those are the Reference Library's long forms standing beside
  the personal short form; collapsing them means retagging library documents, which is
  deferred until classification has produced usage counts to retag against.

- **Two document classes: `profile` and `stub`.** `type/profile` is the entity card — a contact,
  pet, persona, D&D character or place, a piece of software — the second-largest class in the
  vault and one nothing in the canonical list named. `type/stub` marks an auto-generated ghost
  stub that carries nothing but its type until a human fills it in. Both forbid motif, setting
  and event in `FACET_PROFILE`; the standard's canonical list and profile table are updated.
- **`scripts/personal/pass2_apply.py`** (gitignored) writes reviewed assignments into vault
  frontmatter: every tag is validated against the registry and alias table, the class profile
  is enforced, and each batch leaves a before/after change log under `scratch/pass2_changelog/`.

### Changed
- Terms confirmed by the read that were registered under the library's `reference` category
  are re-categorised into their personal domain group.
- **Dreams classify as `type/dream`.** The standard's note arguing for `journal-entry` is
  replaced: the `dream` row exists because a dream record needs motif and setting.

### Decided (recorded in the review document, not here)
- Sensitivity handling is a frontmatter property, not a vocabulary term.
- Dated logs keep the `CY-` time axis; only person-card dates become properties.
- Dreams classify as `type/dream`; the standard's contrary note is to be amended.

## [000.006.193] - 2026-09-21 — *Symmetry, Not Accuracy*

### Fixed
- **`singularize()` mapped plurals and their singulars to different keys.** The rule stripped a
  trailing `s` *before* applying `ies -> y`, which destroyed the pattern the second rule looks
  for: `memories` became `memorie` while `memory` stayed `memory`. Sibilant plurals had the same
  defect — `boxes` against `box`, `glasses` against `glass`.

  This is not cosmetic. `singularize()` backs `_lexical_lookup()`, the first stage of tag
  reconciliation, so a document saying "memories" could never resolve to a registered `memory`
  and would fall through to a proposal instead. It also backs the T2 tier of
  `lexical_equivalences()`.

  Linguistic accuracy is not the goal and never was: `analysis` still stems to `analysi`, which
  is wrong as English and entirely harmless, because both sides of a comparison pass through the
  same function. **A stemmer only does damage when it maps two forms of one word to two
  different keys.** Ten symmetry cases are now asserted as tests.

### Found while
Reviewing the first batch's proposal list. The search for inflectional variants turned up only
six across 944 terms — far fewer than the list appeared to contain, because sorting by frequency
had clustered every one of them at the top. The defect surfaced only because the collapse pass
found fewer pairs than a brute-force scan predicted.

## [000.006.192] - 2026-09-21 — *Closing the Seed Gaps*

### Fixed
- **Query decomposition in reconciliation.** Step 8 decomposed the *registry* into atoms and
  nothing ever decomposed the *query*, so `dream-journaling` was matched whole against a
  vocabulary holding `dream` and `journaling` separately — landing 0.234 from `dreaming`, near
  enough to retrieve and not near enough to assert. Measured over 118 documents, reconciliation
  went **19% → 46%** with no increase in wrong tags.

  The curated registry is what makes this safe: each part resolves against the approved
  vocabulary and anything unregistered is discarded rather than proposed, so it can only ever
  apply terms a human chose. `personal-relationships` yields `relationships` and drops
  `personal`, which was rejected from the seed precisely because it names nothing.

- **Four terms the seed curation dropped.** The seed was built by typing terms into domain
  groups by hand, so anything not explicitly typed fell out regardless of its evidence.
  `journaling` had the highest extraction support of any candidate (28 units) and was simply
  never written into a group. Added with `decision-making`, `dehydration` and `geography`.

### Deliberately not added
The same pass surfaced eight more proposals that are correctly absent: `dungeons-dragons` and
`gnolls` are proper nouns and belong in links (§2); `security` and `llm-agents` duplicate
`cybersecurity` and `ai-agents`; and `memory`, despite **676 occurrences**, is polysemous across
this corpus — human recall in the journals, hardware in the reference material, and the engine's
own subsystem. A term denoting three things retrieves none of them.

### Measured after the change
```
reconciled            291      (was 110)
proposals             348      (was 477)
  resolvable, missed    0      unchanged — the cascade never fails on a registered term
  near, needs decision 218      the LLM stage is not built yet
  genuinely new        130      correct refusals
subject tags per doc  2.5      median 2, max 6, none above
```
Under-tagging is the accepted failure direction: the librarian re-audits on edit and on cooldown,
so a missed term resurfaces, while a wrong one has to be found and removed by hand.

## [000.006.191] - 2026-09-21 — *A Vocabulary Someone Chose*

The first vocabulary in this vault that a person actually picked. Everything the registry held
before was produced by the pipeline under repair, which is why it was cleared rather than
corrected — each pass had been refining its predecessor's mistakes.

### Added
- **124 reviewed subject terms across 11 domains**, joining the 193 reference terms from the
  book-level pass. Registry: **317 terms**.

### How the seed was derived
Blind extraction over a 235-note stratified sample — capped per area, because proportional
sampling would have made it 67% book chapters — aggregated **by term rather than by document**,
then cross-checked against literary warrant in the user's own prose and curated by hand.

Frequency analysis alone could not do it and the record is worth keeping: raw term counts
surfaced template scaffolding (`title`, `feelings`, `description` are the Dream Entry template's
field names), and the mechanical filters kept admitting words that name nothing — `time` at 901
mentions, `least` at 77. Curation was judgement, not a threshold.

Two axes were used together throughout, because either alone destroys categories: **extraction
support** (how many units named it) and **literary warrant** (how often it appears in prose).
109 terms the sample saw once turned out to be written 50+ times — `architecture`, `family`,
`anxiety`, `fatigue` — and a frequency-only cut would have deleted all of them.

### Absent by construction
- **Proper nouns.** 2,708 identified and excluded; §2 makes individuals `[[links]]`.
- **`mood`.** 142 distinct values across 167 uses — free text, and a property rather than a
  subject.
- **Facet axes** (`type/`, `motif/`, `setting/`, `event/`), which the application profile
  assigns during classification.

### Note
Usage counts start at zero. They are earned during classification rather than assumed, and the
admission floor has nothing to measure until the corpus is indexed against this vocabulary.

## [000.006.190] - 2026-09-21 — *The Shelf Is the Classification*

The Reference Library is 2,838 of the vault's 4,219 notes, and its folder structure already
classifies it: `Reference Library/Hands-On Large Language Models/130 - Reranking.md` states both
the book and the chapter. Tagging all 212 of that book's chapters `large-language-models` adds
nothing — they are uniformly about it, and the path said so first. Measured, the library's
distinctive vocabulary came back as `model`, `data`, `training`, `prompt` for all 2,838 notes.

### Added
- **Book-level subject tags on 44 index notes**, and nowhere else in the library. 202 reference
  terms registered. This matches how the library is already treated: it sits in
  `RAG_EXCLUDED_SUBDIRS` and is routed to its own Chroma collection so it cannot dominate
  retrieval — the same reasoning applied to tagging.

### Guards, both derived from measurement
- **Sparse index notes are not trusted.** Extraction against a note with three or fewer chapters
  hallucinated: a pocket watch manual returned "artificial intelligence, machine learning,
  multiagent systems"; a television returned "graph theory". Every failure sat at or below three
  chapters and none above it. Those notes take a domain plus a device type read from the title,
  and nothing inferred — three whose titles identify no product (`Rolanstar BF011 F_Q`,
  `General 1042-L`, `GeneralAire 1042-L`) carry only `hardware`.
- **The source path is a filing location, not a subject.** Two engineering-management titles are
  stored under `AI/` and are not about it.
- **Manual section headings are not subjects.** `parts-and-assembly`, `site-preparation`,
  `national-conventions` were dropped; what a document *is* belongs to the `type/` facet.

## [000.006.189] - 2026-09-21 — *Mood Is a Property, Not a Subject*

Mood was written three ways — `**Mood:** Calm / Warm` in the body, `mood:` in frontmatter, and
a few `#mood/anxious` hashtags — so nothing could query it consistently.

It does not belong in the tag vocabulary. Measured: **142 distinct values across 167 uses**,
phrased like *"glacial, fried, transparent"* and *"heavy start, technical clarity, combat high"*.
A controlled vocabulary would either mint 142 single-use terms — the long tail just deleted — or
flatten that into `tired`. Both are worse than the prose. Mood is a *property* of an entry, not a
subject of it, and properties are what frontmatter is for.

### Changed
- **`mood` is populated as a frontmatter property** wherever the body carried one: 162 → 246
  notes. Where frontmatter already held a value it wins, being the more deliberate of the two.
- **The body line is deliberately preserved.** It is part of the entry's written texture, and 77
  notes already carried both forms without harm. This pass adds queryable metadata; it does not
  edit prose.
- **`#mood/*` hashtags are defused.** Body hashtags no longer enter the vocabulary at all, so
  the form had stopped meaning anything.

### Fixed after the fact
Migration 189 scanned body text **without masking code spans**, so two protocol documents that
contain `` `#mood/anxious` `` as a *documentation example* were given `mood: anxious`, and the
example itself was mangled to `` `anxious` ``. Both files were restored byte-identical from the
pre-migration snapshot.

The canonical `string_utils.protect_code_blocks()` already existed and would have prevented it;
an ad-hoc scanner was written instead, which is exactly the duplication AGENTS.md §8 forbids.
This is the second time in one session that reading inside code spans produced a wrong result —
the first was `vault_indexer` harvesting `#ciso` from line 123 of a policy document. **Any pass
that scans note bodies for markup must mask code spans first.**

Note that 189's function is immutable and still carries the defect; a replay against an unmodified
vault would reintroduce it on those two notes.

## [000.006.188] - 2026-09-20 — *Lexical First — The Vocabulary Is a Dictionary*

Phase A of the tag librarian rebuild: the matching layer. Three defects, all measured rather
than reasoned about.

### Fixed
- **Terms are embedded as their bare surface form.** `_build_tag_embedding_doc()` emitted four
  labelled lines (`Tag:`, `Category:`, `Hierarchy:`, `Scope & Scope Description:`) identical
  across every term. That shared text is a large vector component orthogonal to a one- or
  two-word query: it drags every cosine down and compresses the spread the caller thresholds
  on. Measured across the full registry, querying each term with its own surface form:

  | indexed as | rank-1 | self-distance | terms failing to match themselves |
  |---|---|---|---|
  | labelled prose block | 0.879 | 0.335 | **31.5%** |
  | bare surface form | **0.999** | **0.000** | **0%** |

  `sleep` scored 0.370 against *itself*. Worse, correct matches averaged a **higher** distance
  (0.353) than wrong ones (0.345) — the number carried no discriminative signal at all.
  Category and description are excluded from the vector entirely: both are generated rather
  than authored, and the description stored for `sleep` read "Obsidian notes tagged under
  sleep/apnea-troubleshooting".
- **Reconciliation consults the vocabulary before the vector store.** The hot path went
  straight to Chroma and never queried `master_tag_taxonomy` or the equivalence table, so a
  registry holding the exact term still returned whatever embedded closest. Resolution is now
  a cascade: exact surface match, then bounded whole-string fuzzy, then vectors only for what
  the dictionary missed.
- **A single distance threshold is replaced by acceptance bands.** Measured, 0.35 force-linked
  70% of genuinely off-topic phrases to some plausible neighbour. Nearer than `ACCEPT` is
  taken; beyond `REJECT` is a new concept and becomes a proposal; between them the phrase is
  ambiguous and is held rather than asserted. A margin guard blocks auto-acceptance when the
  top two candidates are indistinguishable.
- **Body hashtags are no longer ingested.** `vault_indexer` harvested `#tag` from note bodies
  with a regex that also matched GitHub discussion numbers (`#22132`), markdown link anchors
  (`[Land Use](#land-use)`) and documentation examples inside code spans — none of which
  Obsidian itself treats as tags. More importantly it was an uncontrolled entry path: nothing
  reconciled those terms against the registry, so a tag could enter the vocabulary without
  ever being admitted to it. Frontmatter is now the single entry point.

### Changed
- The vector index is explicitly a **derived cache**, rebuilt from the registry and never
  repaired in place. Equivalences are indexed as their own vectors carrying the canonical term,
  so a document phrased the retired way still retrieves the preferred one.
- Surface-form lookup is cached against a registry fingerprint rather than an explicit
  invalidation call — a write path that forgets to invalidate would serve a stale vocabulary
  silently, and the symptom would look exactly like a classifier bug.

### Added
- `rapidfuzz` (MIT) for the near-exact stage.

## [000.006.187] - 2026-09-20 — *Tabula Rasa — Regenerate, Do Not Repair*

Every tag is gone. The registry, the aliases, and all 65,173 assignments across both stores.

The assignments were never worth repairing. Essentially none of them were made by hand — they
were the output of the tagging pipeline, and that pipeline was the thing under repair. So each
corrective pass took the previous pass's mistakes as its input and defined correctness relative
to a corpus that was itself wrong. Eight migrations of that produced a vocabulary that was
cleaner in every measurable way and still could not classify a document.

What survives is the part that was actually curated: the standard in
`.agents/rules/vault-tag-taxonomy.md`. The vocabulary gets regenerated against it rather than
migrated toward it — a clean derivation instead of another correction layered on an uncorrected
base.

### Migrations
- **`000.006.186` (vault)** — 26,112 tags cleared from 4,167 notes; 3,206 registry terms and
  6,857 aliases dropped; every document's audit timestamp reset so the vault re-enters the queue.
- **`000.006.187` (memory)** — 39,061 tags cleared across 12,893 rows.

**Nothing is exempt, including administrative tags.** Preserving a category by rule is how the
previous state kept partially surviving its own corrections, and a partial wipe leaves open the
question of whether any given tag is old or new. Date anchors, `status/`, `kanban` and
`obsidian-graph/` are recorded per document in
`data/backups/tag_reset_manifest_000.006.186.json` so they can be reinstated as a deliberate act
rather than persisting by default. The full prior state is in the pre-migration database
snapshots and a vault markdown tarball.

### Found while verifying
- **The vault indexer harvests inline `#hashtags` from note bodies indiscriminately.** `#63` from
  a formula, `#ciso` from a policy document, `#variable_conflict` from a code block. Body
  hashtags are a legitimate Obsidian feature and some are genuine, so this is a filtering
  problem, not a feature to remove — but it is an uncontrolled path into the vocabulary that
  bypasses the registry entirely, and it repopulated cleared rows within seconds of the wipe.
- **Templates carry tags by design** (`Templates/Contact Template.md` → `contact`,
  `CY-YYYY/MM/DD`, `location`). Excluded directories were left untouched, so any note created
  from a template arrives pre-tagged outside the controlled vocabulary.

## [000.006.185] - 2026-09-20 — *Orphan Vectors — Degrade, Do Not Abort*

### Fixed
- **A single metadata-less row no longer destroys an entire RAG query.** `query_collection()`
  called `meta.get()` unguarded, and `AttributeError` was absent from its except clause — so one
  bad row raised past the handler whose comment states that a failed query "should never be
  silent". The caller lost its whole context rather than one chunk. A vector can outlive its
  metadata record: deleting by `where` clause removes the row while the HNSW index still answers
  with the id. Such an orphan now degrades to an unlabelled chunk.

### Operational note
Deleting from a Chroma collection **by `where` clause leaves orphaned vectors**. They are invisible
to `get()`, which reads metadata rows, but `query()` still returns them — so they keep competing in
every search, anonymously and unrankable. Verify a purge with `query()` probes, never `get()`.
Deleting the same ids afterwards can fail HNSW compaction and leave the collection unqueryable.
To retire terms in bulk, drop the collection and re-seed from `master_tag_taxonomy`, which is the
source of truth; the index is derived and costs only its drain time.

## [000.006.184] - 2026-09-20 — *Flat Compounds — Warrant Decides*

Step 8, second half. Decomposition stopped at the slash, because §5 gives the hyphen a real job:
joining the words *inside* one term. Measuring the result showed the job was being abused — the
registry held **2,209 hyphenated compounds against 752 atoms**, three to one, and **1,403 of them
appear nowhere in the vault's own prose**.

### Added
- **`flat_compound_decomposition()`** — decides each compound by literary warrant (Z39.19 §6.5.1.1).
  A compound the vault writes as a phrase is a bound term and survives whole; one nobody has ever
  written was assembled at filing time and decomposes. The bar is the lowest one that works — *ever
  written, even once* — because a higher floor is easy to justify in aggregate and takes real
  categories with it. Thinning weak terms is the admission floor's job, and it applies to atoms
  after this runs rather than to compounds before it.
- **`apply_decomposition_to_csv()`** — applies a plan across a tag string, de-duplicating.
- **`FUNCTION_WORDS`** — glue dropped during decomposition. `about-superpowers` is about
  superpowers, not about `about`.
- **`scripts/generate_hyphen_decomposition.py`** — freezes the plan the migration applies. Derived
  from a corpus scan rather than curated, but still read from disk: a migration must apply the plan
  that was reviewed, and prose changes underneath a live scan.

### Fixed
- **Decomposition no longer follows pre-coordinate alias targets.** Aliases recorded before
  decomposition still point at compound and nested terms — `frustration` → `feeling-frustrated`,
  `workflow` → `work/workflow`, the latter a target the previous step had already dissolved.
  Following one would rebuild the compound that decomposition had just taken apart.

### Migrations
- **`000.006.183` (vault)** / **`000.006.184` (memory)** — 1,403 compounds decompose; compounds fall
  **2,209 → 806**, atoms rise **752 → 1,769**. 121 aliases are retired with their targets: an alias
  is a one-to-one record, and a term that becomes three leaves nothing single to point at.

### Why this was still the blocker
After the first half, the classifier still reconciled **1 subject in 24** — every extracted phrase
fell through to a proposal. The cause was not a missing atom but a crowded index: a pre-coordinate
compound outranked the bare atom in every nearest-neighbour lookup. `sleep tracking` found
`tracking-worries` at 0.357 before `sleep` at 0.373; `cello technique` found `learning-cello`;
`gis technician tasks` found `mapping-technician`. The right answer was present and ranked second,
behind a term that existed only because someone once filed a note under it.

## [000.006.182] - 2026-09-20 — *Decomposition — One Concept, One Tag*

Step 8. Every hierarchical path becomes the atoms it was composed from: `work/routine/morning`
is three concepts glued together by an indexer guessing which combination a future query would
want, and the guesses multiplied — `journaling` sat under **31 different parents**, one concept
restated 31 times.

### Added
- **`decompose_to_atoms()`** and **`decompose_tag_csv()`**. Mechanical, with no judgement: the
  split is by rule. Protected date anchors and administrative namespaces are untouched, and a facet
  prefix keeps its single level because it names *which axis* a term belongs to — something flat
  atoms cannot express. A facet deeper than that loses its middle, since `setting/biome/tropical`
  is categorising within an axis, which is exactly the hierarchy being removed.

### Migrations
- **`000.006.181` (vault)** / **`000.006.182` (memory)** — 11,876 terms become **8,252 atoms**.
  Not an alias mapping: an alias records that one term *became* another, which is not what happens
  when a term becomes three, so the registry is rebuilt rather than remapped.

### Why this was the blocker
The classifier could not reconcile anything. Blind extraction produced the phrase `sleep tracking`
consistently across documents, but the registry held `health/sleep/tracking`, `health/sleep/issues`
and `sleep/apnea-diagnosis` — and no bare `sleep` for a phrase to match against. Every subject came
back as a proposal and nothing could ever be applied. After decomposition `sleep` exists with **426
uses**, `routine` with 1,012, `health` with 1,485.

### Known
Decomposition alone leaves 68% of atoms used exactly once — it removes the duplication, not the
tail. The admission floor (step 9) is what produces a usable vocabulary: measured at roughly **963
atoms covering 98% of notes**.

## [000.006.180] - 2026-09-20 — *Application Profile — Cataloguing Before Subject Analysis*

§4 has described which facets each class of document requires, permits and forbids since it was
written, and nothing ever consulted it. The classifier never determined a class, so "forbidden is
enforced, not advisory" was aspirational. This makes it real, and completes the three-pass pipeline.

### Added
- **`determine_document_class()`** identifies a document's *form* from a closed list — the narrowest
  question in the pipeline and the only part of the profile a model decides.
- **`apply_application_profile()`** does the rest by table lookup: adds the `type/` facet, strips a
  second one, removes facets the class forbids, and **reports** required facets that are missing
  rather than inventing them. A dream without a motif needs one, but guessing which motif is subject
  analysis, not cataloguing.
- The pipeline is now three passes with distinct questions: *what kind of thing is this* (§4),
  *what is it about* (§6.1), *what is it no longer about* (staleness).

### Fixed
- **The staleness pass was reading a truncated document.** It received the first 6,000 characters
  and judged every tag against them, so on a 23,000-character overview it condemned ten legitimate
  sections — `floodplain`, `census-data`, `emergency-management` — as stale. It now reads the whole
  document by the same route classification uses. §7.1 rule 2 is about the document as much as the
  tag list, and applying it to only one of them reproduced the original collapse in miniature.

### Verified end to end
The 36-tag document that began this work now goes **33 tags to 34** — everything kept, `type/manual`
added, nothing removed. Classes were identified correctly across a manual, a journal entry and a
reference note, and proposals were coherent (`parcel-documentation`, `finger-placement`).

## [000.006.179] - 2026-09-20 — *Staleness as a Positive Assertion*

Documents change; tags outlive what they describe. But the previous librarian inferred removal from
**silence** — anything the model failed to echo back was deleted — which is how a 36-tag document
became a 3-tag one. Blind extraction removed that failure by making removal impossible, which is
safe but leaves genuine staleness unaddressed.

### Added
- **`verify_tags_still_apply()`** asks the opposite question in its own pass, and requires the model
  to **name** what is stale. A tag it fails to mention is kept, so a truncated, malformed or empty
  answer removes nothing — the inversion is the whole safety property.
- **A removal ceiling.** A pass may retire at most a third of a document's tags; beyond that the
  verdict is treated as a malfunction and nothing is removed. The September collapse called 33 of
  36 tags wrong, and this refuses exactly that shape of answer.
- **An absolute allowance beneath the ceiling.** A ratio is meaningless on a three-tag note where
  two are genuinely wrong, so up to two removals are always permitted. Found by a test, not by
  reasoning.

### Verified
Measured against four cases before building: obviously wrong tags caught 3/3 with no false
positives; all-valid tags flagged nothing; loosely-relevant tags flagged nothing; and a document
whose subject had changed had exactly its two obsolete tags identified.

## [000.006.178] - 2026-09-20 — *Blind Extraction — Ask What It Is About, Reconcile Afterwards*

Shown a document's existing tags, the classifier imitated their shape. It produced **eight different
terms for "sleep" across five documents** and never once the bare atom, while the prompt's
instruction to emit atoms went ignored — examples outweigh instructions. Shown only the documents,
the same model produced the identical phrase `sleep tracking` in three of them, with no nesting
anywhere.

### Added
- **`classify_document_subjects()`** splits the task in two. The model is asked only what the
  document is *about*, and never sees the existing tags or the registry, so it has nothing to
  imitate. Alignment happens afterwards, deterministically.
- **`reconcile_subjects()`** maps phrases onto the registry by measured distance: close enough and
  the phrase *becomes* the registered term; otherwise it is a proposal for review, never an
  automatic addition (§6.1). Format compliance stops depending on the model complying.
- **`normalize_subject_phrase()`** formats without decomposing. `"obstructive sleep apnea"` is one
  diagnosis, and splitting it lexically would destroy exactly the terms of art worth keeping — the
  §5 test asks whether the halves are independently meaningful, and here they are not.

### Changed
- **The classifier can no longer remove a tag.** Asking what a document is about yields no signal
  about what it is *not* about, so removal is not inferable and stays a supervised operation. The
  failure that disabled this librarian in September is now impossible by construction rather than
  forbidden by rule.

### Removed
- `retrieve_candidate_tags_for_document()` — superseded. The blind pass has no use for candidates,
  since showing them is what caused the imitation.

### Preserved by design
- **`TAG_NOVELTY_DISTANCE_THRESHOLD` was retired rather than reused.** Deleting the function above
  orphaned it, and the wiring gate correctly flagged it. The first fix pointed the reconciler at it
  — wrong, because it answered a different question (is a whole document covered?) at a looser 0.55,
  which matched a metabolism paper to `sleep/tracking-worries`. Reusing a constant to satisfy a gate
  is the masking §11 forbids. `TAG_SUBJECT_MATCH_DISTANCE` replaces it at 0.35.

`TAG_LIBRARIAN_ENABLED` remains `False`. The match rate is low because the registry holds
`health/sleep/tracking` and no bare `sleep` atom for a phrase to match — which step 8 fixes, and
until then this cannot be fairly evaluated.

## [000.006.177] - 2026-09-20 — *Thinking Mode Was Eating the Answer*

The rewritten classifier returned nothing at all — six documents, six deferrals. Not malformed
output: **empty**. Measured at the API: with thinking enabled the model consumed its entire
2,048-token budget deliberating and emitted no content in 40 seconds. With thinking disabled it
returned correct JSON in **1 second**.

### Fixed
- **Classification runs with `think=False`.** Tag assignment is rule-following extraction whose
  reasoning belongs in the prompt, not in chain-of-thought. This is not the latency trade §7.1 rule
  5 forbids — it is the difference between an answer and no answer.
- **§7.1 rule 5 now says so explicitly**, because as written it could be read as requiring
  chain-of-thought. The violation it was authored about was different in kind: degrading the
  classifier *conditionally*, on documents with many tags, after those documents had already proven
  hardest to classify.

### Verified against the original archetypes
The document that started this — collapsed from 36 tags to 3 in September — now keeps **32 of 33**,
adds the missing `type/overview` facet, and drops one tag that described the filename rather than
the content. Previously untagged notes classify from nothing into plausible atomic tags.

### Known, not fixed
- **The classifier still emits nested subject tags** the prompt forbids, and produced eight
  different terms for *sleep* across five documents without once producing the bare atom. The
  likely cause is in-context imitation: the prompt shows dozens of pre-coordinate tags as current
  and candidate values, then asks in prose for atoms. Examples outweigh instructions.
- The fix is §6.1 authority control — constrain output to vocabulary that exists and queue the rest
  — which makes format compliance mechanical rather than a matter of the model complying. It is
  written into the standard and not yet implemented.

`TAG_LIBRARIAN_ENABLED` remains `False`.

## [000.006.176] - 2026-09-20 — *Read the Document — Chunked Classification Instead of Guessing*

The classifier judged every document from a skeleton of headings plus the opening lines. That was
built as a substitute for a chunked read that was proposed at the time and never implemented — and
the substitution is what set off the failure chain: skeleton plus the tag list overran the time
budget, and the fix for *that* was a fast path that degraded reasoning on exactly the documents
needing the most care.

### Added
- **`read_document_for_classification()`** sizes its approach to the document:
  - **Under 6,000 characters — sent whole.** Roughly **73% of the vault** fits here, and was
    previously reduced to a skeleton for no reason.
  - **Longer — read in chunks**, each chunk asked only what subjects it covers, and the merged list
    presented alongside the structural skeleton.
  - **Very long — chunks sampled evenly to a ceiling of twelve**, so a 360KB note costs twelve
    bounded calls rather than ninety.
- Chunking reuses `web_reader.chunk_text()`, which already respects paragraph boundaries, rather
  than adding a second implementation.

### Preserved by design
- **Every call sees a bounded slice**, which is what keeps this away from the timeout the previous
  design hit. The fix for a timeout is smaller inputs, never a weaker classifier.
- **The subject-extraction pass is deliberately narrow** — it sees a chunk and the title, never the
  vocabulary or the existing tags, so it cannot be drawn into deciding the document's tags from a
  fragment of it.
- **A failed chunk read degrades to the skeleton** rather than losing the document.

## [000.006.175] - 2026-09-20 — *Classifier Rewrite — Post-Coordinate, and Silence No Longer Deletes*

The tag librarian has been disabled since `000.006.139` for collapsing multi-topic documents. The
five causes were diagnosed that same day and written into §7.1 as invariants — **and then never
fixed.** The classifier sat untouched with the original logic while the standard was built around
it. This is that repair, against the now post-coordinate standard.

### Fixed — the five §7.1 violations
- **Truncation.** The prompt showed the model five of a document's tags and then acted on its
  verdict about all of them. A 36-tag note had 31 hidden. Every current tag now reaches the prompt.
- **Silence deleted.** The tag set was rebuilt from what the model echoed back, so any tag it did
  not repeat vanished. The set now starts from what the document has; only terms named explicitly
  in `tags_to_remove` are dropped.
- **Existing tags were hidden from the candidate pool**, so the model could not see that a term it
  already carried was the canonical one, and invented near-synonyms instead.
- **Novelty guidance was computed and discarded.** The coverage signal that decides whether to
  reuse or mint reached a variable named `_novelty_guidance` and went nowhere.
- **The latency fast-path degraded reasoning** above fifteen tags — trading correctness for speed
  on exactly the documents that needed the most care. Removed: a document that cannot be classified
  within budget is deferred, not partially processed.

### Changed
- **The prompt is post-coordinate.** It taught nested `Domain/Subdomain` and Title-Case, which §3.3
  and §5 no longer permit. It now asks for atomic lowercase tags and reserves the single slash for
  facet prefixes.
- **Output is canonicalized** through recorded equivalences, so a retired variant the model echoes
  back cannot re-enter the vocabulary.
- **The classifier no longer mints master tags.** Under §6.1 it proposes; registration is authority
  control's job, not the document pass's.

### Preserved by design
- `TAG_LIBRARIAN_ENABLED` stays `False`. The rewrite is untested against real documents, and the
  previous version's failure was discovered by running it on a live vault.
- The four invariants are pinned as tests, including one that feeds thirty tags and asserts every
  one reaches the prompt.

## [000.006.174] - 2026-09-20 — *Phrase Retirement — Verbose and Unshared, Together*

Step 6d. Retires 2,806 flat multi-word descriptors used once or twice — `cat-care-supplies`,
`stolen-car-dream`, `heartwarming-animal-encounters`. Sentence fragments that happen to be
hyphenated: nobody searches them, nothing else shares them, and each costs a vocabulary entry for a
single document. 3,013 tag-uses of 39,737.

### Added
- **`one_off_phrase_tags()`** requires **both** conditions, because either alone is wrong. Length
  alone would condemn legitimate compound terms like `work-life-balance`; low use alone would
  condemn correct structure that is merely young, which is the mistake §6.3.3 exists to prevent.
  It is the combination — verbose *and* unshared — that marks a label generated for one document
  rather than a category.
- **Nested terms are excluded whatever their length.** A slash means something placed the term in
  the tree, and that structure is the expensive part to rebuild.

### Migrations
- **`000.006.173` (vault)** / **`000.006.174` (memory)** — retire the identified terms as aliases to
  the empty string, so writers stop emitting them rather than re-minting them next extraction.

### Preserved by design
- **Notes left untagged are an accepted outcome**, not a failure: the librarian visits untagged
  documents first, so an emptied note is queued for reclassification rather than lost. It also keeps
  its folder, links, and embedding.
- **The decision was made from a matrix, not a list.** At the individual-term level "used ten times
  or fewer" covers 14,363 of 14,682 terms — 98% of the corpus, since 11,630 are used exactly once.
  Crossing usage against shape turned an unreviewable list into five cells, of which one was
  unambiguous.

## [000.006.172] - 2026-09-20 — *Transitive Aliases — One Hop Was Never Enough*

Verifying the second merge pass found 609 aliases whose target was **itself retired**, and 8 pairs
of terms each claiming to retire into the other.

### Fixed
- **`canonicalize_tags()` resolved only one hop.** Aliases accumulate across migration passes, so a
  term retired in step 5 can point at a term step 6a later retired — `sleep-tracking` →
  `sleep/tracking` → `health/sleep/tracking`. One hop left the dead middle term in place, and every
  writer kept emitting it. Resolution is now transitive and cycle-guarded.
- **Eight alias cycles, from two passes disagreeing on direction.** Step 5 merged by usage and chose
  the plural (`relationship/family` → `relationships/family`); step 6a merged roots under §6.3.2 and
  chose the singular. Each term then claimed the other as its canonical. §6.3.2 is the standard, so
  the singular is correct and the stale entries were dropped.

### Preserved by design
- **A cycle stops at the current form rather than raising.** A vocabulary with a contradictory
  alias pair should degrade to "leave this term alone", not fail a write path that every tag in the
  engine passes through.
- Empty tag sets are an acceptable outcome of removal. A memory fact keeps its subject, category,
  content and embedding; a vault note keeps its folder and links. The librarian visits untagged
  documents first, so an emptied note is queued rather than lost.

## [000.006.171] - 2026-09-20 — *Second Merge Pass — Removal as a Recorded Decision*

Step 6c. The first merge pass ran at a 0.92 similarity cut which proved too tight, leaving
`home/maintenance` and `household/maintenance` as separate terms. This applies the reviewed 0.88
pass over the now-consolidated vocabulary.

### Added
- **`scripts/parse_tag_merge_review.py`** reads the edited review document and takes it literally:
  a ticked line applies, an unticked line is a rejection, and a `[remove]` block retires the whole
  group — canonical included, since there is no survivor to merge into.
- **Removal is recorded, not performed.** A removed term becomes an alias to the empty string; the
  canonicalization path already drops empty targets, so every writer stops emitting it. Deleting the
  term outright would leave nothing to stop the next extraction re-minting it, which is the same
  failure §6.2 exists to prevent for merges.

### Changed
- **The review generator refuses to propose merging a term with its own ancestor.** The hierarchy
  already states that relationship, and collapsing it destroys a level —
  `relationship/dynamics/support` into `relationship/dynamics` erases a distinction the tree was
  built to hold.
- **A `--min-uses` floor.** Running 0.88 across the whole corpus produced 2,065 merges, overwhelmingly
  single-use tail terms that content classification will serve better than string similarity.

### Migrations
- **`000.006.170` (vault)** / **`000.006.171` (memory)** — 304 merges and 27 removals from review.

### Preserved by design
- **A note that would lose every tag is skipped and reported**, not emptied. Losing all tags is a
  signal that the decisions were wrong for that note, not a successful cleanup.
- **The 66 groups totalling ten uses or fewer were left untouched.** They were considered for bulk
  removal and deliberately deferred: the groupings were too broad, and many of their members will
  fold into branches merged higher up in this same pass. Re-evaluating after the corpus settles will
  show a different set than judging them beforehand would have.

## [000.006.169] - 2026-09-20 — *Flat Adoption — Missed Slashes Become Nestings*

Step 6b of the taxonomy migration. The sibling test (§6.3.1) applied to compounds that have no
nested twin to compare against: `productivity-tips` was flat only because nothing ever nested it,
while `productivity` demonstrably holds terms — so the hyphen was a missed slash all along.

### Added
- **`adopt_flat_compounds()`** nests a flat compound when its leading word is an established level.
  The bar is the same evidential one used everywhere else: the head must be proven a real level by
  what already lives under it. A compound whose head names nothing is left alone rather than nested
  speculatively.

### Migrations
- **`000.006.168` (vault)** / **`000.006.169` (memory)** — 1,382 adoptions. Flat terms drop from
  8,297 to 6,915, and only 3 merge into a term that already existed.

### Preserved by design
- **Only the first hyphen becomes a slash.** `ai-prompt-engineering` nests as
  `ai/prompt-engineering`, not `ai/prompt/engineering` — the evidence supports `ai` as a level and
  says nothing about `prompt`.
- **Converges in one pass.** Adoption enlarges a parent without promoting new heads, so a second
  pass finds nothing; the migration is bounded rather than iterative.
- **6,915 flat terms remain and are untouched.** Their heads name nothing in the tree, so nesting
  them would be guesswork. They need classification from document content, not lexical inference.

## [000.006.167] - 2026-09-20 — *Literary Warrant — Sparse Roots Judged by the Prose, Not the Tags*

Step 6a of the taxonomy migration (`vault-tag-taxonomy.md` §9). Step 5 merged whole terms, which
could not reach this: `preference/food` and `preferences/drink` share no lexical pair, yet their
roots are one concept. Consolidating at the root rewrites everything beneath it.

### Added
- **§6.3.2 Number form** — ANSI/NISO Z39.19-2005 §6.5 puts count nouns in the plural and mass nouns
  in the singular, so `dreams` and `goals` would default to plural. This vault uses the **singular
  throughout**, invoking the exception the standard provides at §6.5.1.1 for domains with user
  warrant — its examples are body parts in biomedicine and objects in a museum catalog. The warrant
  is demonstrated rather than assumed: across 161 singular/plural pairs the singular was already the
  established form in 124. Recorded as a knowing deviation, so a later pass does not "correct" it.
- **`root_inflection_merges()`** folds inflected roots to the singular, which sometimes means a
  smaller root absorbs a larger one — form outranks incumbency.
- **§6.3.3 Literary warrant** — a sparse namespace is judged by how often its word appears in the
  vault's **prose**, not by how many terms sit under it. That is the sense Z39.19 §6.5.1.1 uses:
  a term belongs to a vocabulary because the domain's literature uses it. `architecture` is tagged
  once and written 1,697 times; `social-relations` is tagged once and written zero times. Population
  would treat those identically.
- **`literary_warrant()`** builds the index, matching compounds as phrases — searching
  `mental-state` as a token finds nothing because prose writes "mental state", and treating that
  zero as evidence would condemn every multi-word root by construction. Entities score −1 however
  often they appear (§2), and unknown roots are kept, since dismantling without evidence is the
  failure mode.
- **`weak_root_resolution()`** applies it: compounds whose head is an established root are re-nested
  as the missed nesting they are (`ai-behavior` → `ai/behavior`, 76), roots with warrant keep their
  namespace, and only the 21 with none are dismantled.
- **Compound heads resolve through the inflection merges first.** `relationships-dynamics` fails a
  naive head check — `relationships` has already folded into `relationship` — and would be
  dismantled into a flat tag despite having a perfectly good parent. Five roots were rescued this
  way.

### Verified
- The warrant index was checked against raw `grep` over the vault: `architecture` 1,803 occurrences
  across 622 files, `database` 888 across 319. Every dismantled root scored **zero occurrences in
  any file** — `astronomy` and `collectibles` included, which look like plausible categories but
  appear nowhere in anything actually written. They were generated labels, not vocabulary.

### Migrations
- **`000.006.166` (vault)** / **`000.006.167` (memory)** — 533 term rewrites: 436 root merges, 76
  re-nestings, 21 dismantles. Roots drop from **493 to 361**.

### Preserved by design
- **Ordering is load-bearing.** Inflection merges run before weak-root detection: a root below
  threshold on its own may clear it once its variants fold in, and flattening first would dismantle
  a namespace about to become real. Pinned by a test.
- **Sparse roots are not dismantled by default**, and §6.3 now carries that as a warning. An earlier
  draft of this pass flattened every root with fewer than two children, which would have destroyed
  102 legitimate categories — `admin`, `chemistry`, `coffee`, `employment`, `astronomy` — on the
  evidence that they were under-populated, when under-population is precisely the problem the
  taxonomy exists to fix.

## [000.006.165] - 2026-09-20 — *Tool Descriptions Say When, Not Just What*

A tool description is a trigger specification, not a summary. Ten of them described what the
tool does and then said "use when asked", which is a reactive contract the model was honouring
correctly — the assistant was not failing to be proactive, it was being told not to be.

### Changed
- **Trigger conditions added to ten descriptions**, each drawn from the live `procedures` row
  that already described the same cue: `get_health_metrics` (1067), `get_recent_workouts`
  (1107), `create_task` and `get_agenda` (1063), `write_dream_entry` (657),
  `write_journal_entry` (1034), `manage_vault_list` (1104), plus `complete_task`, `delete_task`
  and `list_tasks`. `get_health_metrics` also carries 1067's directive to present data plainly
  and leave pacing to the user rather than prescribing rest.
- **`search_history` now states that it records nothing.** It described only how to query, so
  it was being substituted for the recording action on an end-of-day cue and the turn stopped
  there. It now says gathering context first is reasonable but does not replace calling
  `write_journal_entry`.
- **`delete_task` distinguished from `complete_task`**, so a finished item keeps its record
  instead of being erased.

### Design notes
- **Every tool in `CORE_TOOL_NAMES` now carries trigger guidance** (four did not). Core tools
  are offered every turn, so retrieval tuning cannot affect them — the description alone decides
  whether they fire.
- **Four tools were deliberately left without trigger prose** — `delete_calendar_event`,
  `sync_google_calendar`, `sync_google_tasks`, `read_document_scratchpad`. They are plumbing and
  mechanism steps invoked as consequences of other tools, and trigger text there would cost
  context for nothing.
- **The payload got cheaper.** The full 31-tool definition set measures 6,080 prompt tokens,
  down from 6,167, because removing the compensation padding more than paid for the added
  trigger conditions.

## [000.006.164] - 2026-09-20 — *Split Provenance — Deduplication Stops Undoing Splits*

A fact split by hand was silently recombined by the next consolidation pass, then had to be split
again. Not a coincidence of near-identical wording — a structural oversight.

### Fixed
- **The deduplicator never read `split_from_id`.** The splitter records parent provenance precisely
  so a decomposition can be recognised later, but nothing consumed it. To the deduplicator, siblings
  of a split are simply two very similar facts about one subject, which is exactly its merge
  criterion — so it merged them, and the pair oscillated between split and merged indefinitely.
- `is_split_relative()` now blocks merges across all three shapes: siblings of one parent, and
  either entry being the other's parent. A split is an explicit judgement that two facts are
  distinct, and it outranks a similarity score.
- Measured before the fix: 89 split children across 35 parents, 2 already split-then-merged, and
  **34 sibling groups still co-existing** as standing merge candidates.

### Changed
- **The merge prompt's tag instructions contradicted §5.** It taught `Tech/Python/FastAPI` casing and
  `John_Smith` underscores for named entities — the pre-standard format, and the entity-as-tag habit
  §2 removed. Rewritten to lowercase hierarchical form.

### Preserved by design
- Exact-match deduplication needed no guard: it keys on normalised observation text, and split
  siblings differ in text by construction, so they cannot collide there. Only the semantic path
  could reach them.

## [000.006.163] - 2026-09-20 — *Alias Enforcement — Every Tag Writer Consults the Registry*

Applying the reviewed merges surfaced two memory rows still carrying retired variants — rows from
August that an earlier migration had already swept. They had been **resurrected**, and the cause was
a hole in the alias layer rather than a fault in the migration.

### Fixed
- **Three tag writers did not canonicalize through the alias registry.** `fact_deduplicator`,
  `fact_splitter`, and the procedures path in `fact_extractor` all normalized format but never
  consulted recorded `UF` equivalences. The deduplicator is the one that actually bites: merging two
  entries recombines tags **from the source rows**, so a retired variant on an old entry is written
  straight back onto the survivor — undoing a completed migration hours after it ran.
- This is the same defect as `000.006.155`, where recording aliases without consulting them on write
  was caught by the hygiene gate. That fix wired one writer; there were four. Recording an
  equivalence is inert unless **every** path that writes a tag resolves through it.
- The two affected rows were curated through `canonicalize_tags()` itself rather than by hand, so
  the repair exercised the same path the fix installs.

### Preserved by design
- Curating two rows is local data curation, not a migration (AGENTS.md §5) — no schema or systemic
  structure changed, and the durable fix is the wiring.

## [000.006.162] - 2026-09-20 — *The Sibling Test — Reviewed Merges and Nesting Resolved by Evidence*

Completes step 5 of the taxonomy migration (`vault-tag-taxonomy.md` §9). The reviewed merges are
applied, and the flat-vs-nested question that recurred 126 times now has a measurable answer instead
of a per-case judgement call.

### Added
- **§6.3.1 The sibling test** — *a hierarchy level must have siblings*. If the token a nested form
  proposes as a level already has terms living under it, the term nests; if nothing lives under it,
  the compound is a single term of art and stays flat. This is why the choice felt intuitive but
  resisted explanation: the real question is whether the first word means anything on its own **in
  that position**, and sibling count is the observable proxy.
- **`resolve_structural_nesting()`** implements it. Across 126 pairs it resolved 123 to nested and
  3 to compound — and the three it spared were exactly the terms of art, including a disease name
  whose nested form would have been meaningless.
- **`--export-decisions`** on the review generator, plus an embedding cache. Re-ordering the review
  document dropped from ~15 minutes to ~35 seconds, which is what made iterating on its layout
  practical at all.

### Changed
- **The review document is grouped by term family**, not by usage impact, with structural and
  semantic decisions interleaved. The previous layout split by decision type first, so a family
  could be scattered across two sections hundreds of lines apart — which produced inconsistent
  answers, because a decision made in isolation looked different once its siblings appeared.
- **Recurring flat-vs-nested shapes are surfaced as policy blocks**, 17 of them covering 76 of the
  125 structural decisions.

### Migrations
- **`000.006.156` (vault)** / **`000.006.157` (memory)** — apply 1,618 reviewed `UF` equivalences.

### Fixed
- **The frozen normalizer backing migration `000.004.002` was not faithful.** Its two
  `is_excluded_tag()` guards were omitted when it was extracted, so a protected date anchor such as
  `CY-2025/03/12` was mangled to `Cy_2025/03/12` by the entity casing rules on replay. The guards
  are restored and a regression test pins the behaviour. The omission survived the previous release
  because that release's targeted test batch did not include `test_db_migrator.py`.

### Preserved by design
- **The decisions live in a local artifact, not the repository.** They are curated facts about this
  specific corpus and the vocabulary contains personal terms, so committing them would cross the
  privacy boundary (AGENTS.md §4). A missing file makes the migration no-op loudly rather than
  half-apply.
- **The sibling test reads the divergence point, not the leaf.** An earlier implementation tested
  only the final segment, which answered the wrong question whenever the hyphen under review sat
  earlier in the path — `home-maintenance/chores` asks about `home`, not about `chores`. It also
  tests the level the *nested* form proposes rather than splitting the flat form on its first
  hyphen, since `health/self-care/routine` claims `health/self-care` as a level, not `health/self`.
- **`resolve_structural_nesting` and `namespace_children` are whitelisted as external script
  consumers** (AGENTS.md §11 category 1), not to mask dead code — their production caller is a
  standalone script outside Vulture's scan paths.

## [000.006.160] - 2026-09-20 — *Behaviour Benchmark — Underscores No Longer Hide Identities*

The privacy gate added in `000.006.157` reported clean while protected names sat in tracked
files. Its word-boundary used `\w`, which counts `_` as a word character, so any name embedded
in a snake_case or kebab-case identifier was invisible to it — precisely where names occur in
code and in test fixtures. Fixing the boundary surfaced 21 violations across 7 files.

Separately, the golden query suite only measured retrieval. A companion suite now measures
model behaviour, so a model swap can be judged on restraint rather than on benchmark scores.

### Added
- **`scripts/benchmark_behavior.py`** and **`reference/behavior_benchmark_cases.json`** — a
  15-case behaviour suite across nine categories: `restraint`, `proactivity`, `control`,
  `tool_honesty`, `over_protection`, `persona_drift`, `sycophancy`, `pushback`, and
  `tool_awareness`. Mirrors `benchmark_rag.py`'s CLI (`--verbose`, `--json`) and adds
  `--model` / `--compare` for A/B runs.
- **`tool_honesty` scoring** compares what a reply claims against the calls actually made.
  A case may supply its own `tool_response`, so a write can be made to fail and the reply
  checked for a success claim it did not earn. Both directions count: asserting an action
  that never landed, and denying one that did.
- **Per-case `system` override** so `persona_drift` can install a distinctive configured
  voice and check whether user frustration collapses it into generic contrition.
- **Line-level `privacy-ok: <reason>` pragma** in the privacy gate. The reason is mandatory,
  so an exemption cannot be added silently.

### Fixed
- **Privacy gate word boundary** (`scripts/check_privacy_boundary.py`) now uses alphanumeric
  boundaries instead of `\w`, so `_` and `-` separate tokens. `Rickyshaw`-style collisions are
  still correctly ignored.
- **Protected names removed from tracked files** — a surname in two test fixtures, a profile
  test's sample-body variable and one string literal, and four identifiers plus two descriptive
  notes in the golden query set. Historical changelog references to a personalised profile
  filename were neutralised.

### Changed
- **Model-compensation language removed from tool descriptions.** `write_journal_entry` carried
  "Trigger Directive: Execute this tool directly... Do not wait or hesitate", and two tools shouted
  "STRICT RULE". That urgency was written to push a model that under-fires; it is model-specific and
  distorts any model evaluated against it. Scope boundaries were kept, stated plainly.
- **Reactive-only phrasing replaced with real trigger conditions.** `get_health_metrics`,
  `get_recent_workouts`, `create_task` and `write_dream_entry` all said "use when asked", which is a
  reactive contract the model was honouring correctly. Each now also names the implicit cues that
  should trigger it, drawn from the live `procedures` rows that already describe them (1067, 1107,
  1063, 657). `get_health_metrics` additionally carries procedure 1067's directive to present data
  plainly and leave pacing to the user, rather than prescribing rest.

- **Golden query expectations tightened to the `Cat##-U` / `Cat##-A` taxonomy.** 15 entries
  carried bare `Cat##` codes predating the subject-code convention. Because matching is a
  substring test, a bare code matched both subjects at once and could not tell a user fact from
  an assistant fact. Two genuinely relational cases keep both subjects deliberately.

### Design notes
- **Tool calls in the behaviour suite are intercepted, never executed.** Each is answered with a
  synthetic success, so no vault path, database, or external service is touched by a run.
- **Acting unbidden is scored as a feature, not a fault.** Proactive tool use is a design goal
  of the engine, so `proactivity` asserts that an implicit cue — an evening wind-down, a passing
  mention of a depleted item — *should* produce a write. Only a question about mechanism is a
  genuine no-act. Write volume is therefore not a defect metric; the suite counts misreports
  instead.
- **A tool description is the trigger specification.** `get_health_metrics` is a core tool, offered
  every turn, so no amount of retrieval tuning affects it — its description alone decides when it
  fires. Measured: with the old "use when asked" wording neither gemma4:12b nor granite4.2:8b called
  it after a reported physical task; with the rewritten wording both do.
- **Superseded note — tool descriptions previously carried eagerness directives** ("Execute this tool directly...",
  "Do not wait or hesitate"). They compensate for a model that under-fires, and amplify one
  that does not. Any model swap should be re-measured with the descriptions held constant.
- **Two violations could not be edited and carry the pragma instead.** A name is frozen inside
  an applied migration's SQL as a data-matching predicate; under the immutability rule
  (AGENTS.md §5) rewriting it would change replay semantics on a fresh database.

## [000.006.159] - 2026-09-20 — *Stub Filing — Entity Notes Routed Under `Stubs/<Domain>/`*

Entity stubs were written to the vault root, where they came to outnumber genuine root notes
by 114 to 3. They now file under `Stubs/`, and under `Stubs/<domain>/` when their referencing
notes identify one, so the sub-path mirrors where the note belongs if it outgrows stub status.

### Added
- **`LIBRARIAN_STUB_DIR`** (default `Stubs`) and **`LIBRARIAN_STUB_DOMAIN_FOLDERS`**, the
  top-level vault folders that count as a subject domain.
- **`infer_stub_domain()`** derives the domain from the folders its harvested references live
  in, so it costs no extra vault scan. **`stub_relpath()`** builds the destination path.
- Both write paths route through them: Tier 1 auto-synthesis and the Tier 2 approval endpoint.

### Design notes
- **The assistant's journal is excluded from the domain folders.** It references every subject
  in the vault, so it identifies none.
- **A "nearby links" heuristic was implemented, measured, and rejected.** Voting on co-linked
  notes filed a country under Contacts at 89% confidence, because journal entries co-mention
  people. A confidently misfiled note costs more to undo than an unsorted one, so ambiguous
  stubs stay directly in `Stubs/` for manual filing. A tie between domains is likewise left
  unsorted rather than broken arbitrarily.
- Routing is self-reinforcing: filing an unsorted stub by hand strengthens the referrer signal
  for the entities it links to.
- Leakage is possible and accepted — a contact's note mentioning a game routes that game to
  `Stubs/Contacts/`. The evidence is identical to a correct person route, so no threshold
  separates them, and the cost inside a sorting tree is a drag-and-drop.

## [000.006.158] - 2026-09-20 — *Article-Agnostic Link Resolution — "The X" and "X" Are One Entity*

Writers are inconsistent about a leading article, often within a single vault and sometimes
within a single note. The resolver treated `[[The X]]` and `[[X]]` as unrelated targets, so
whichever form lacked a note became a ghost link and earned its own stub proposal. That is how
two notes for the same location came to exist, and a case-only sibling of the same problem
produced a filename that conflicts on case-insensitive sync peers.

`stub_dedupe_key()` already collapsed articles, but only when comparing *proposals* to each
other. The gap was in resolution itself.

### Added
- **Stage 5 of `resolve_canonical_link_target()`: leading article normalization.** `[[The X]]`
  resolves to note `X`, and `[[X]]` resolves to note `The X`. It reuses the stem list already
  fetched for stage 4, so it costs no additional query.

### Safety
- Resolution requires **exactly one** match, consistent with the alias and disambiguation
  stages. If both `X` and `The X` exist as real notes they remain distinct entities: an exact
  match still wins outright, and a third article form against two equally valid candidates
  bails rather than silently picking one.
- Measured against the vault before implementing: three article-variant ghost links existed,
  in both directions, and **zero** cases where both forms existed as separate notes.

## [000.006.157] - 2026-09-20 — *Privacy Boundary — Identities Out of Version Control, Enforced by Gate*

AGENTS.md §4 forbids committing real identities, but nothing enforced it, and it had been
broken repeatedly across many sessions. The leak path was never a copied file — tracked source
and the personal vault live in separate trees, so nothing can drift between them. It was prose:
changelog entries, comments and prompt examples quoting whatever concrete case the author had
in front of them. A rule that depends on remembering, while writing, does not hold.

### Added
- **`scripts/check_privacy_boundary.py`**, wired in as a fourth stage of
  `scripts/check_code_hygiene.py`. It scans every tracked file for protected terms sourced from
  the gitignored `.env`, so the repository never contains the values it is scanned for. Two
  tiers: real identities are **blocking**; machine-specific absolute paths are **advisory**,
  because AGENTS.md deliberately specifies absolute interpreter and database paths in its own
  operational instructions — a gate that failed on those would be unpassable and get disabled.
  `--strict-paths` promotes them.
- **Env-backed identity configuration.** `ASSISTANT_NAME`, `USER_NAME`, `USER_LEGACY_ALIASES`
  and the new `PRIVATE_IDENTITY_NAMES` now read from `.env`, with generic defaults so a fresh
  clone runs without naming anyone. Documented in `.env.example`.
- **`EXAMPLE_PET_NAME` / `EXAMPLE_THIRD_PARTY_NAME`** plus `_known_entity_roster()` in the fact
  extractor. Few-shot prompt examples previously hardcoded a household roster of real people.
  They are now supplied at runtime from `.env`, preserving subject-recognition quality while
  keeping the names out of the repository.

### Changed
- **184 identity references removed from 40 tracked files** — engine prompts, test fixtures,
  benchmark queries, UI strings and documentation — replaced with configuration lookups where
  the value means "the operator", and with neutral fixtures where it was only sample data.
- **Vault-specific sections removed from this changelog.** A project changelog records changes
  to the engine, not curation performed on a user's personal notes; those sections also carried
  note titles and folder names that had no business in version control.

### Notes
- Four test failures predate this work and are unrelated to it: three tag-taxonomy assertions
  and two path-resolution assertions left by the in-progress faceted classification migration.
  Verified by running the same suites against HEAD before any change here.

## [000.006.156] - 2026-09-20 — *Stub Precision — Placeholder Rejection, Duplicate Collapse & Title Preservation*

The first unattended ghost link stub batch produced roughly 100 proposals. Most were
legitimate; the rejected ones fell into four mechanical classes, each now handled. Reviewing
the resulting notes surfaced two further defects in the approval path.

### Added
- **Template placeholder rejection.** Session-log scaffolding leaves `[[<Thing> Name]]` behind
  under an unfilled heading. No note in the vault ends in " Name", so the suffix rule is safe
  and also covers placeholders that do not exist yet.
- **Retired document rejection.** Targets carrying an explicit `(archived)`, `(deprecated)`,
  `(obsolete)` or `(retired)` marker no longer generate stubs — the note was retired on purpose.
  Matched as a parenthetical whole word, so ordinary disambiguation like `Oberon (warframe)` is
  unaffected.
- **`resolves_as_possessive()`.** A target ending in apostrophe-s whose base resolves to an
  existing note is a possessive reference, not a missing entity. Deliberately narrow: names that
  merely *contain* a possessive (`The Serpent's Fang`, `Warden's Crest`) do not end that way and
  were all approved as legitimate stubs.
- **`stub_dedupe_key()`.** A single sweep proposes every target before any stub exists, so
  intra-batch duplicates cannot be caught by an existence check. The key collapses case-only
  differences (`... Coat Of Arms` / `... Coat of Arms`) and leading articles
  (`Grand Library` / `The Grand Library`), both of which created redundant notes.

### Fixed
- **Case-colliding stub filenames.** The approval path wrote its target name verbatim, so a
  case-only variant became a second file. Linux keeps both; a case-insensitive sync peer sees
  one file under two names and raises a sync conflict. The writer now reuses an existing note
  that differs only by case.
- **Frontmatter titles no longer clobbered.** `scripts/update_frontmatter.py` overwrote any
  existing `title` with the file's basename *including* its extension. That is the intended
  convention for repository files, but vault note titles carry no extension, so every note
  passed through the script had its title replaced with `<Name>.md`. An existing non-empty
  title is now preserved; a missing one still falls back to the filename.
- **Dev UI: XML payload collapsed.** The structured payload dominated each stub card. It now
  sits behind a closed disclosure, still editable before approval — the textarea stays in the
  DOM, so the approve request is unchanged.

### Not mechanically detectable
- One rejection turned on domain judgement (a tool's internal component name that is not a
  vault-worthy entity). No rule is proposed for it; that class stays a human decision.

## [000.006.155] - 2026-09-19 — *Equivalence Collapse — UF Aliases and the Star-Shaped Merge*

Step 5 of the taxonomy migration (`vault-tag-taxonomy.md` §9), lexical half. Implements the `UF`
("Used For") relation of ISO 25964: many colloquial variants map onto one preferred term.

### Added
- **`Evelyn/tools/tag_synonym.py`** — equivalence detection over the combined vault+memory corpus
  (16,862 terms). Mapping is **star-shaped, never transitive**. Single-link clustering was tried
  first and measured: at a 0.88 similarity cut it chained 4,069 unrelated terms — `tech/ai`,
  `hardware`, `relationship/dynamics`, `routine` — into one "cluster" through intermediates. `UF` is
  inherently one-canonical-many-variants, so the star form is both correct and immune to chaining.
- **`master_tag_aliases`** table plus `canonicalize_tags()` on the registry. A collapse that is
  executed but not recorded is undone by the next import.

### Changed
- **Both write paths now canonicalize through recorded aliases** — the vault librarian's final tag
  set and the memory extractor's output. Recording an alias without consulting it on write only
  renames existing data; the retired variant is re-minted on the next extraction and the vocabulary
  drifts back. The deterministic hygiene gate caught this as unwired code before it shipped.

### Migrations
- **`000.006.153`** — `master_tag_aliases` schema.
- **`000.006.154` (vault)** / **`000.006.155` (memory)** — apply the recorded equivalences. The
  memory step reads the alias map rather than recomputing it: the vault step has already altered the
  corpus, so a fresh computation would derive a different mapping. The alias table is the record of
  what was actually decided.

### Preserved by design
- **Only judgement-free tiers were auto-applied**, and the boundary moved during analysis. Terms
  identical once separators are ignored were initially treated as mechanical; measurement showed
  that where the two forms differ in hierarchy depth the choice is structural, not cosmetic. Ranking
  by usage alone flattened hierarchies (`work/stress` → `work-stress`); ranking by depth alone let a
  1-use variant rename a 94-use term (`health/physical-condition` → `health/physical/condition`).
  Auto-apply is therefore restricted to cases where both signals agree; the 125 groups where they
  conflict are deferred to review.
- **`record_alias()` was written and then removed rather than whitelisted.** It could not be called
  from inside the vault migration — that migration holds a write transaction on the same file, so a
  second connection would block — and nothing else needed it yet. It returns when its caller does.

## [000.006.152] - 2026-09-19 — *Entity Extraction — Tags That Restate the Link Graph*

Step 4 of the taxonomy migration (`vault-tag-taxonomy.md` §9). §2 holds that named individuals are
links, not tags — the entity's own note is the authority record. This step removes tags that were
already saying what the link graph says.

### Added
- **`strip_subject_duplicate_tags()`** in `tag_librarian.py` — canonical rule shared by the memory
  extractor and migration 151, rather than implemented twice. The test is **relational, not a name
  list**: a tag is dropped only when it matches *that record's own* subject. A fact about one party
  tagged with another party's name is a cross-reference the subject field cannot express, and is
  preserved — 17 such rows survived precisely because of this.

### Changed
- **`fact_extractor.py` now enforces the rule at write time.** Tags came from free-form LLM output,
  so subject-duplicating tags were still being produced — 6% of recent entries versus 35% of older
  ones. Deleting the existing ones without closing the writer would simply let them accumulate again.
  Enforced deterministically rather than by instructing the model, per AGENTS.md §11.

### Migrations
- **`000.006.151` (memory)** — drops tags restating their own row's subject: 1,252 rows, no row left
  untagged.
- **`000.006.152` (vault)** — removes entity tags whose every carrier already sits in the entity's
  folder or references it by name. 70 tags / 1,736 note-tag pairs qualified; all 245 notes tagged
  with one reference book, for instance, were already in its folder *and* linked its index, stating
  the same membership three ways.

### Preserved by design
- **The redundancy threshold is 100%, and that is what makes the rule safe.** Anything less means
  the tag carries a connection the link graph does not. It also cleanly separates genuine entities
  from concept words that merely share a name with a note: real entities measured 100% already-linked,
  while `creativity`, `artificial-intelligence`, `core-identity` and `obsidian-vault` measured ~0%
  and were left as tags. A note *about* creativity does not make it an entity.
- **The rule is recomputed at migration time, never listed.** Several qualifying entities are
  personal contacts whose names must not enter a tracked file (AGENTS.md §4), and recomputation keeps
  it a generic pattern sweep (§5).
- **The 8 `location/<place>` terms are untouched.** Each is used on a single note and has no
  corresponding note, so converting them would create ghost links and stub proposals for no
  retrieval benefit.

## [000.006.150] - 2026-09-19 — *Registry Unification — Taxonomy Ownership Extracted, Vector Vocabulary Rebuilt*

Step 3 of the taxonomy migration (`vault-tag-taxonomy.md` §9). The vault and memory are one
knowledge structure governed by one controlled vocabulary (§0), but the registry's API lived inside
`vault_db.py` — so the memory subsystem had to reach into the vault's store to read the vocabulary
it shares.

### Added
- **`Evelyn/tools/taxonomy_db.py`** owns the Master Tag Taxonomy registry API
  (`get_master_tags`, `upsert_master_tag`, `delete_master_tag`). Connection handling is reused from
  `vault_db` rather than duplicated, and the table stays in the vault store — relocating it would
  add a fourth database to serve what is a naming concern, not a functional one.
- **§6.5 Registry consistency** records a property that was previously tribal knowledge: the vector
  read path every consumer queries is updated through a staging queue, so registration is not
  immediately readable. Measured at ~5.5 terms/sec — rebuilding the collection took ~16 minutes.
  Any pass that registers terms and then reads them back must wait for the drain, or it will
  retrieve the pre-registration vocabulary and mint duplicates of what it just approved.

### Changed
- **`fact_extractor.py` no longer imports `vault_db` at all.** Extracting the registry API removed
  the memory subsystem's only dependency on the vault store — the coupling this step existed to fix,
  confirmed by the linter flagging the import as unused.
- **`evelyn_tag_taxonomy` rebuilt from the post-sweep registry.** It held 5,228 pre-migration terms
  — still containing the old casing variants and the retired `topic/` and `relationship/`
  namespaces — so both librarians were proposing against a vocabulary that predated steps 1 and 2.

### Preserved by design
- **The registry stays curated.** Memory's 11,670 ungoverned terms were *not* imported. Being
  visible to clustering and being registered are different things: step 5 reads both stores directly,
  so nothing is hidden from it, but only terms surviving curation enter the registry at step 6.
  Importing raw extraction output would make the registry a record of every string ever emitted,
  contradicting §6.1.
- 613 terms are excluded from the vector index by design — date anchors plus the administrative
  namespaces, which are not semantic vocabulary.

## [000.006.149] - 2026-09-19 — *Namespace Retirement — Bare-Rooted Domains and the Administrative Firewall*

Step 2 of the taxonomy migration (`vault-tag-taxonomy.md` §9). Deterministic namespace moves only —
no classification, no LLM.

### Changed
- **`topic/` wrapper dropped.** Domains are bare-rooted (§3.3): a document about hardware is
  `hardware`, not `topic/hardware`. Three of the five wrapped terms merge into bare terms that
  already existed, which is the wrapper coming off rather than a vocabulary change.
- **`contact/*` collapsed to `obsidian-graph/contact`.** The 15 role subdomains (which included
  near-duplicates like dad/father and mom/mother) carried nothing the graph flag does not. Per §3.2
  this is administrative metadata, not a subject facet, and is now walled off from the classifier.
- **`location/biome/*` moved to `setting/biome/*`** (§3.6). A biome is a kind of space; the
  `location/` root was conflating that with named geography.
- **`relationship/*` retired — vault only.** 36 scattered terms, artifacts of flat `#relationship-x`
  tagging that never became a real domain.

### Preserved by design
- **Named places under `location/` are untouched.** They are entities and belong to step 3's link
  extraction (§2), not to a namespace move.
- **`relationship/*` is NOT retired in memory.** It is a live namespace there: 903 fact rows carry
  it and 666 have no other tag, so retiring it without replacement would strip those facts of their
  only retrieval handle. Memory retirement is gated on re-tagging and is registered as §9 step 9.
  Until that runs, the two stores intentionally differ on this one namespace.
- **Migration 148 was not refactored** despite overlapping loop structure. It is applied, and
  applied migrations are immutable (AGENTS.md §5); 149 reuses only helpers that predate it.

## [000.006.148] - 2026-09-19 — *Tag Format Unification — Faceted Classification Standard, Step 1*

The tag librarian was disabled at `000.006.139` because its classifier was collapsing multi-topic
documents — a 36-tag reference note came back with 3 tags. Rather than tune the prompt again, the
intent layer was written down first as a governing standard, and this release executes its first
migration step.

### Added
- **`.agents/rules/vault-tag-taxonomy.md`** — the Faceted Classification standard governing all tag
  curation: six facet axes (domain, type, motif, setting, event, time) plus a walled-off
  administrative axis, class-gated facet profiles, vocabulary control under authority control, and
  two distinct operating modes for autonomous vs. supervised passes. Assembled from Ranganathan's
  PMEST, FAST, Iconclass, the DCMI Type Vocabulary, NISO metadata classes, ISO 25964, EDTF, Getty's
  authority-file model, and SKOS.
- **`canonicalize_date_tag()`** resolves date anchors under EDTF (ISO 8601-2:2019), which
  distinguishes reduced precision (`CY-2026/05` — anchored to a month) from unspecified digits
  (`CY-XXXX/11/16` — a known day in an unknown year). Unexpanded template literals left behind by
  earlier tooling resolve to the latter.

### Changed
- **`normalize_tag_format()`** now implements one rule with no exceptions: lowercase always, hyphens
  join words, slashes join levels. The entity/concept branch is **removed** — it decided "entity" by
  testing for any uppercase character, which is what allowed a single concept to fork into separate
  master tags differing only in case. With no branch, there is no way to fork. The CamelCase splitter
  also handles acronym runs and leading digits correctly.
- **`TAG_LIBRARIAN_EXCLUSIONS`** accepts EDTF date forms and the `obsidian-graph/` administrative
  namespace. **`TAG_LIBRARIAN_FORMAT_RULES`** rewritten; it previously documented the removed
  entity-underscore rule.

### Migrations
- **`000.006.147` (memory)** — sweeps `context_entries` and `procedures` tags to the new format.
  Sequenced first deliberately: memory is a single-file restore, so it validates the rewritten
  normalizer against real rows before any irreplaceable document is touched.
- **`000.006.148` (vault)** — snapshots every markdown note to a gzipped archive, rewrites note
  frontmatter while recording a per-file reversal manifest, merges the taxonomy's collision classes
  (summing usage counts), then reconciles the taxonomy against the swept on-disk state so later
  clustering reads a complete vocabulary.

### Preserved by design
- **Migration `000.004.002` pinned to a frozen normalizer.** It called the live
  `normalize_tag_format()`, so rewriting that function would have silently changed what an
  already-applied migration does when replayed against a fresh database. A frozen copy of the
  original implementation now backs it, preserving the immutability guarantee in AGENTS.md §5.
- Date anchors remain the sole exemption from the format rule, and the administrative namespaces
  (`status/`, `kanban`, `obsidian-graph/`) stay invisible to the semantic classifier.
- `TAG_LIBRARIAN_ENABLED` remains `False`. This release changes format only; no classification runs.

## [000.006.146] - 2026-09-19 — *Redundant Alias Condensing — Self-Referential Wikilinks Collapsed*

A wikilink whose alias repeats its own target carries no information and renders identically,
so the pipe is pure noise. The vault held 401 such links across 160 notes, overwhelmingly
`[[Alex|Alex]]` (373) — the shape identity parameterization leaves behind when it rewrites
both sides of an aliased link to the same configured name.

### Added
- **`condense_redundant_aliases()`** collapses `[[X|X]]` to `[[X]]`, including the
  table-escaped form `[[X\|X]]` (which condenses safely, since the result has no pipe left to
  escape). Embeds are handled on the same terms and keep their `!` prefix. Wired into
  `audit_document_links` ahead of canonicalization, so the librarian now self-heals this shape
  and the canonicalizer sees clean targets.

### Preserved by design
- **Case-only aliases** (`[[Music|music]]`, 52 in the vault) — the display casing is a
  deliberate choice for mid-sentence rendering, not redundancy.
- **Subpath and block targets** (`[[Note#Section|Note]]`) — the display genuinely differs from
  the full target, so the alias is load-bearing.

## [000.006.145] - 2026-09-19 — *Excerpt Alias Integrity — Wikilink Pipes Preserved in Harvested Context*

Found while reviewing the first live ghost link stub proposal. `extract_link_context` stripped
markdown table syntax with a blanket `.replace("|", " ")`, which also flattened the alias pipe
inside every wikilink it captured: `[[Alex|Alex]]` became `[[Alex Alex]]` and
`[[Sekulich Family|Sekulich family's history]]` became one run-on target.

This was not cosmetic. The harvested excerpt is written verbatim into the `## 🧭 Context &
Mentions` section of any stub note approved from the proposal, so approving a stub would have
introduced new unresolvable targets into the vault — the link librarian manufacturing the exact
ghost links it exists to reduce.

### Fixed
- **Alias pipes survive excerpt extraction.** Pipes inside `[[...]]` are protected before the
  table-pipe sweep and restored afterwards, so aliased links keep their form while bare pipes
  from table rows are still flattened. Covered by a regression test asserting both halves.

## [000.006.144] - 2026-09-19 — *Stub Synthesis Recovery — Reasoning Disabled & Configurable Timeout*

Found by running the ghost link stub pipeline live against a real entity once the honest
`synthesis_mode` badge from 000.006.142 made the failure visible. Entity stub abstracts were
never actually written by the model: the call left `think` unset, so Gemma 4 reasoned before
answering, and it passed a hardcoded `timeout=18` against `query_ollama`'s own 120s default.
Measured on a five-reference target, reasoning enabled took **26.9s** — past the ceiling every
time — so the call always returned empty and silently fell back to the deterministic compiler.
With reasoning disabled the same call returns in **2.2s**, and the abstract is better: it picks
up a detail the fallback cannot express at all, since the fallback only lists citing notes.

### Added
- `LIBRARIAN_STUB_SYNTHESIS_TIMEOUT` (default 45) replaces the hardcoded literal.

### Fixed
- **Stub synthesis now reaches the model.** `synthesize_entity_abstract` passes `think=False`,
  matching the tag librarian's existing enforcement, and reads its timeout from config. A
  regression test asserts both, since the flag is load-bearing and its absence fails silently
  rather than raising — the Ollama client returns an empty string on timeout, so the exception
  path never fires and only the empty-result warning catches it.

## [000.006.143] - 2026-09-19 — *Array Fence Integrity — Qualifier Preservation & Vault Reconstruction*

Found by the quality review pass over 000.006.142. The array-fencing regex anchored on a bare
`array`/`tensor` name with a `(?<![`\w])` lookbehind, which a preceding `.` satisfies. Every
dotted call was therefore fenced from the function name onward, leaving the qualifier stranded
outside: ``torch.tensor([[1, 2]])`` became ``torch.`tensor([[1, 2]])```. This was live engine
behaviour, not legacy damage, and had already corrupted 11 notes.

### Fixed
- **Dotted qualifiers are kept inside the fence.** `arr_pattern` now carries an optional
  `(?:[A-Za-z_]\w*\.)*` prefix and a `(?<![`\w.])` lookbehind, so `np.array(...)` and
  `torch.tensor(...)` are wrapped whole while bare `array(...)` still matches as before.
- **`reattach_split_array_qualifiers()` repairs prior damage.** It rejoins stranded qualifiers
  and strips doubled backticks an earlier pass left around a literal, leaving it bare for the
  normal wrapper to re-fence cleanly. Prose spans such as ` ``bash pip install ...`` ` do not
  match and are untouched.
- **Repair placed at the correct pipeline stage.** `audit_document_links` masks inline code
  before calling the wrapper, so a repair living inside `wrap_spurious_code_arrays` can never
  fire from the engine — by then the damaged span is a placeholder and the qualifier is no
  longer adjacent to it. The call now sits beside the existing pre-mask fracture repair.

## [000.006.142] - 2026-09-19 — *Numeric Literal Fencing — Nested Array Wrapping & Honest Synthesis Reporting*

Follow-up to the ghost link stub investigation. Closes the gap that let un-fenced numeric
literals sit exposed in PDF-extracted notes, retires a divergent copy of the wrapping logic,
and stops the review UI from crediting the LLM for abstracts it did not write.

### Added
- **Nested numeric literals are now fenced.** `wrap_spurious_code_arrays` previously matched
  only flat 2-D lists, leaving `[[2, 0.5], [3, 1]]` and `[[0.7, 0.3], [1.0, 0.0]]` bare in the
  vault where every `[[...]]` consumer could misread them. A new `_wrap_numeric_bracket_literals`
  scanner walks each candidate with a bracket-depth counter instead of a nested quantifier, so
  cost stays linear — a 28 KB pathological line resolves in ~320 ms with no backtracking cliff.
  `None`, `-inf`, signed values and underscore digit separators are all recognised.
- **`synthesis_mode` on the entity stub contract.** `synthesize_entity_abstract` now returns
  `(abstract, mode)`, carried through `StubPayload`, the XML envelope and the review endpoints.
  Payloads written before this field degrade to `fallback`.

### Fixed
- **The review UI no longer labels deterministic fallbacks as LLM synthesis.** The badge read
  "Multi-Reference LLM Synthesis" unconditionally. It now reflects `synthesis_mode`, rendering
  "Deterministic Fallback — No LLM Synthesis" in amber with an explanatory tooltip when Ollama
  was unavailable or returned nothing usable.
- **Silent synthesis failures are now logged.** Both the exception path and the too-short-result
  path dropped to `logger.debug`, so an 18-second Ollama timeout left no trace above debug level.
  Both now log at warning with the target name.
- **Legacy remediation script migrated to the canonical wrapper.** `remediate_spurious_and_entities.py`
  carried its own three regexes for the same job, in violation of the DRY protocol. It now calls
  `wrap_spurious_code_arrays` behind `protect_code_blocks` and rewrites only the note body.

## [000.006.141] - 2026-09-19 — *Ghost Link Stub Integrity — Code Subscript Rejection & Mention Rendering*

Fixes ghost link stub proposals synthesized from Python source code rather than from vault entities.
PDF-extracted Reference Library notes carry un-fenced pandas/NumPy code, and double-subscript syntax
(`iris.data[["petal length (cm)", "petal width (cm)"]]`) is byte-identical to a wikilink. The link
auditor harvested those fragments as recurring ghost links, cleared the multi-reference quality gate
on the strength of three code blocks quoting the same textbook snippet, and queued them for review.

### Fixed
- **Code subscripts are no longer mistaken for wikilinks.** `is_valid_entity_target` now rejects any
  candidate stem containing characters that never appear in vault note stems but are ubiquitous in
  source code (`"`, `*`, `<`, `>`, `{`, `}`, `=`, `;`, backtick). Audited against all 7,139 wikilink
  targets present in the vault: the 15 rejections are all code fragments, and no legitimate stem is
  affected — colons and parentheses (`Clair Obscur: Expedition 33`, `Oberon (warframe)`) still pass.
- **Second-layer subscript guard on link scanning.** `WIKILINK_OPEN_GUARD` prevents `[[` preceded by
  an identifier character, `)` or `]` from matching at all, since that form is an indexing expression
  rather than a link. Applied to both the ghost link scan and `canonicalize_document_wikilinks`.
  Markdown italic emphasis (`_[[Target]]`) is deliberately exempt and still resolves.
- **Harvested mentions rendered as empty quotes in the review UI.** `parse_stub_xml` rebuilt each
  reference with `source` and `context` only, while the dev review panel reads `ref.snippet` — the
  field `harvest_entity_references` populates. Every harvested excerpt therefore displayed as `""`
  despite being present in the stored XML payload. The parser now restores `snippet` parity, and the
  panel falls back to `context` so payloads written before this release render correctly.

## [000.006.140] - 2026-09-19 — *Reasoning Pipeline Overhaul — Native Multi-Channel Streaming & Structured Traces*

Rebuilds the prompt → reasoning → tool → response chain around Gemma 4's native multi-channel
output. The previous pipeline was assembled incrementally for a single-stream model that inlined
`<think>` tags and sentinel tokens into the content channel; that model is long gone, but its
parsing machinery, in-band control protocols and text-marker persistence format remained.

Diagnosis was empirical. Against the live engine, Gemma 4 never emits `<think>` in the content
channel in any thinking mode, reasoning always arrives in the separate `thinking` field, and Ollama
*does* apply `options.stop` to that reasoning channel — a firing stop sequence halts the turn with
`done_reason: "stop"` and empty content, so stop strings could never force an answer.

### Fixed
- **Runaway reasoning now yields a reply instead of a dead turn.** Gemma 4 can loop on
  self-termination tokens ("Ready. Done. Perfect. Stop thinking. Go.") indefinitely without emitting
  content. A new reasoning budget (`THINK_BUDGET_CHARS`, default 16000) cancels the round when
  native reasoning exceeds the ceiling and re-issues the identical turn with thinking disabled,
  which guarantees a response. Trips are counted in `think_budget_trips` and surfaced live as a
  `think_budget_exceeded` SSE event.
- **Tool failures no longer render as successes on reload.** `tool_end` streamed a real `status`,
  but the persisted metadata carried none and history reconstruction hard-coded the success class,
  so any failed tool appeared green after a refresh. Status is now recorded per tool in the
  structured trace and rendered faithfully in both live and restored views.
- **Non-functional stop sequences removed.** `STOP_SEQUENCES` was `["(Send).", "(Final).", "(Done).",
  "*Perfect."]`; across a 29,306-character runaway reasoning trace, none of the four ever matched
  what the model actually emits. In `fact_extractor` they were inherited into structured YAML
  extraction where a spurious match would have silently truncated a block into partial facts.

### Changed
- **Legacy `<think>` state machine deleted** (`evelyn_server.py`). Roughly 70 lines of `parse_buf` /
  `in_think` tag scanning with partial-boundary lookahead ran on every content delta and buffered
  any chunk ending in a prefix of `<think>`. Content is now a direct passthrough.
- **Structured per-round reasoning trace.** The loop previously flattened structured round data into
  one string with `[Round N]` markers, stored it in a single TEXT column, and the UI re-parsed it
  with a regex that still expected `[Initial]`/`[Tool N]`/`[Response]` labels the server had stopped
  emitting. Rounds are now emitted on `_state` as structured records and persisted to
  `messages.trace_json`. The flat `thinking` column is retained as a human-readable mirror.
- **Single UI trace renderer.** Live streaming and history reconstruction now share one code path.
  Messages saved before this release have no structured trace and degrade to a single Reasoning
  block; no history rewrite is performed.
- **`fact_extractor` owns its terminators** via `_EXTRACTION_STOPS` (the closing YAML fences) rather
  than seeding from conversational config. `query_reformulator` drops stop sequences entirely — with
  `num_predict=50` and `think=False` they were inert.

### Removed
- **`{"requested_effort":"X"}` self-election protocol** and `THINK_SELF_ELECT`. The marker was an
  in-band text protocol riding on the content channel, which native thinking moved out of reach: all
  emissions landed in the reasoning stream where the parser never looked. It could not have worked
  even on a match, since `think` is a request-level parameter fixed before the round begins. Effort
  now resolves from the heuristic classifier, tool escalation, and the UI chip.
- **`cfg.STOP_SEQUENCES`**, now consumer-free.
- **Legacy `.think-block` CSS** and the `[Round N]` parsing in both UI render paths.

### Migrations
- **`000.006.140` (chat)** — `messages_structured_reasoning_trace_column`: adds `messages.trace_json`.

### Tests
- `Evelyn/tests/test_reasoning_trace_and_budget.py` — structured trace shape, verbatim content
  passthrough for literal `<think>` text, budget trip and single retry with thinking disabled,
  budget disabled via zero, and failed-tool status fidelity in the persisted trace.

## [000.006.139] - 2026-09-19 — *Behavioral Hardening — Partner Framing, Tier Ratchet Repair & Wind-Down Precision*

Corrects a long-running drift in which the assistant's framing moved from partner/co-pilot toward
caretaker. Diagnosis was measured rather than inferred: the persona layer carried a 20:8
nurse-to-momentum instruction ratio at 21.5% of the context window, while `profile_evolver`
mechanically protected symptom vocabulary and pruned technical identity first.

### Fixed
- **Evolver tier ratchet (`Evelyn/tools/profile_evolver.py`)**: `_USER_TIER_1_PATTERNS` protected
  `fatigue|exhaustion|sleep|rest|pain|migraine|...` (pruned last) while `_USER_TIER_2_PATTERNS`
  (`technical|architect|infrastructure|...`) was pruned first. Over successive evolution passes this
  deterministically converted the user profile into a symptom log. Transient physical states are now
  Tier 3; chronic conditions remain Tier 1; autonomy and partnership vocabulary promoted to Tier 1.
- **Forward-momentum directives now protected**: `_DIRECTIVES_TIER_1_PATTERNS` gained
  `momentum|proactive|co-pilot|forward|autonomy`, so anti-passivity rules are as durable as the
  existing anti-sycophancy rules rather than being pruned before them.
- **Self-regenerating wind-down rule**: the evolver's own few-shot example taught
  `'**Daily Rhythms**: ... prioritizing rest over pushing through exhaustion'` — verbatim the bullet
  it kept reproducing in `System_Directives.md`. Example replaced with a follow-his-lead formulation,
  plus an explicit constraint against authoring directives that infer capacity from indirect cues.
- **Procedure 1034 retrieval precision**: trigger and tags were broad enough to fire on any end-of-day
  conversation, and step 1 forbade introducing "new tasks, technical problems, or analytical questions".
  Measured against 60 genuine bedtime messages and 860 other turns, the original wording achieved
  23.3% recall at 20.5% false-fire; the curated wording achieves 65.0% at 7.6%. Replayed against 689
  logged turns, firing rate drops from 18.0% to 8.0%. Journaling mechanics preserved verbatim.

### Changed
- **Tier markers are now authoritative** (`score_bullet_tier`): an explicit `[Tier N]` marker in a
  bullet wins over keyword heuristics, matching how `profile_ledger` already prunes. Tiering is now
  inspectable and hand-correctable in the ledger files rather than hidden in a regex.
- **`## Routines & Rituals` renamed to `## Behavioral Defaults`** across
  `CANONICAL_DOCUMENT_SECTIONS`, `DOCUMENT_THEMES`, the section tier bias, both persona documents,
  the open-source template, and the section invariant tests. Extracted facts retain a canonical home.
- **Persona slimmed** from 3,517 to 3,233 tokens (21.5% → 19.7% of `NUM_CTX`). Caretaking
  prescriptions removed from `Assistant_Profile.md`, `User_Profile.md`, and `System_Directives.md`;
  identity, voice and relational framing retained. `Explicit Completion Mandate` relocated from
  Routines to Operational Guidelines — it is an operational honesty rule, not a ritual.
- **Stated-needs principle added** to `Core_Directives.md` and both directive documents: a report of
  tiredness is information, not an instruction; only an explicit request changes the pace. Adaptive
  Pacing no longer triggers on inferred exhaustion, and an Anti-Regression Clause breaks repeated
  passive-response cycles.
- **Procedures 41, 1067, 1754 curated** toward presenting information and proposing concrete next
  steps rather than prescribing rest. Procedure 41 (avoidance loops) hardened as the counter-signal.

### Added
- `Evelyn/tests/test_profile_evolver_tiers.py` — asserts technical identity outranks transient
  symptoms, momentum directives score Tier 1, explicit markers override heuristics, and the
  assistant journal remains RAG-excluded (that feedback loop was the largest historical drift driver,
  closed on 2026-09-10; the test prevents silent regression).
- `scripts/curate_behavior_procedures.py` — idempotent procedure curation with timestamped JSON
  rollback. Data curation per AGENTS.md §5; no DDL, so no migration step.

## [000.006.138] - 2026-09-18 — *Faceted Classification Cataloging Prompt — Professional Librarian Model*

### Changed
- **Restored & Hardened Faceted Classification Prompt (`Evelyn/tools/tag_librarian.py`)**:
  - Replaced degraded "2-4 clean domain tags" prompt with a proper **Faceted Classification** model aligned with professional library cataloging practice.
  - Librarian is now explicitly instructed to classify by **subject matter** — not by the user's role or employer (e.g. a GIS document receives `#Tech/GIS`, not `#Work`).
  - Multi-topic documents **must** receive a domain tag for each genuine subject area; collapsing is no longer permitted.
  - Added a mandatory **orthogonal `#type/` facet** (form/nature of document) as a separate axis from domain tags (e.g. `#type/overview`, `#type/journal-entry`, `#type/manual`).
  - Existing tags are now audited: accurate well-formed tags are kept; flat-dashed tags are reformatted into domain hierarchy; only genuinely wrong or redundant tags are removed.
  - Quantity guide changed from a hard ceiling ("2-4") to a flexible principle: simple notes 2-4, multi-topic reference documents as many as genuinely needed.
  - Expanded note body sample from 800 to 1200 characters to provide better classification context.
  - Tag summary rendering improved: ≤6 tags shown as comma-separated list; >6 shown as summarized count with leading examples.

## [000.006.137] - 2026-09-18 — *Hardened Multi-Archetype Semantic Tagging with Two-Tier Direct Fallback*

### Added & Architecture
- **Two-Tier Direct Inference Fallback (`Evelyn/tools/tag_librarian.py`)**:
  - Implemented automatic secondary fast-path fallback: if deep reasoning (`think=True`) reaches token/time budget limits or fails to yield structured JSON, Tag Librarian automatically executes a deterministic direct-inference pass (`think=False`, `num_predict=1024`), ensuring 100% decision completion across all vault note structures without no-op aborts.
- **Dynamic Candidate Budgeting & Prompt Streamlining (`Evelyn/tools/tag_librarian.py` & `evelyn_config.py`)**:
  - Reduced `TAG_LIBRARIAN_TOP_K_TAGS` from 35 to 10 in `evelyn_config.py` to eliminate candidate vector pollution and prevent token starvation on conceptual documents.
  - Excluded notes' existing tags from RAG candidate suggestions to eliminate circular tag confirmation loops.
  - Added intelligent legacy tag summarization for notes with extensive tag sprawl (>6 legacy tags) to prevent reasoning models from debating individual flat tags in endless loops.
  - Streamlined `system_prompt` and trimmed note body sampling to 800 characters to keep prompt contexts lean and response latency under 35 seconds.

### Validated & Tested
- **Comprehensive 6-Archetype Vault Focus Test Suite (`scratch/comprehensive_focus_test.py`)**:
  - Validated 100% decision success rate across the complete range of vault document structures:
    1. *Multi-dash flat tag bloat* (`Alex/My Tailoring Measurements.md`): Consolidated 5 flat tags into `#Craft/Tailoring` and `#Design/Sizing` in 31.31s.
    2. *Extreme tag clutter (36 tags)* (`Alex/Professional/GIS Technician Tasks Overview.md`): Pruned 33 granular H2 section tags into 6 clean domain tags (`Work/GIS/*`) in 46.71s.
    3. *Protected dates & entity tags* (`Contacts/Allie McLean.md`): Preserved protected `CY-2014/03/17` calendar tag and mapped contact entities into `#People/Birth_Records` and `#People/Contacts` in 14.10s.
    4. *Untagged conceptual notes (0 tags)* (`Alex/Medical/Psychology/Why Slow Learners Often Become Better Programmers.md`): Accurately synthesized domain tags (`Education/Pedagogy`, `Tech/Programming`, `python`) in 45.03s.
    5. *Short reference snippets (<100 words)* (`Reference Library/Learning Cello/Cello Method/15 - C STRING.md`): Normalized mixed-case tags into `Music/Cello` in 17.44s.
    6. *Thematic / dream journal entries* (`Dream Journal/Dream Entries/Dream Entry 2026-01-18.md`): Preserved protected date tag and consolidated 17 fragmented motif tags into 4 clean domain tags (`sleep/dreams`, `fantasy/superpowers`, `dream/memory`) in 26.74s.

## [000.006.136] - 2026-09-18 — *Dedicated Semantic Tagging Subsystem & Diurnal Tag RAG Drainer*

### Added & Architecture
- **Dedicated Semantic Tagging Subsystem (`Evelyn/tools/tag_librarian.py`)**:
  - Decoupled rapid reflex housekeeping (<3s) from heavy semantic Tag RAG and local Ollama inference.
  - Implemented `audit_single_document_semantic()` supporting single-document evaluation, taxonomy candidate matching, Ollama classification, and atomic frontmatter tag updates.
  - Implemented `run_semantic_tag_audit()` and `run_semantic_tag_audit_async()` driven by `backlog_drainer` with cooperative yielding and batch limits (`cfg.TAG_LIBRARIAN_BATCH_SIZE = 2`).
  - Added CLI flag `--semantic-tags` to `scripts/master_librarian.py` to drain the semantic tag queue directly via terminal.
- **Guided Thinking Guardrails & Ollama Integration (`Evelyn/tools/ollama_client.py` & `tag_librarian.py`)**:
  - Added `think: bool | None` parameter to `query_ollama()` and configured `think=True` with `num_predict=2048` for Tag Librarian.
  - Implemented anti-loop thinking directive in system prompt (`Thinking Directive: Keep internal thinking concise (under 100 words)...`) to prevent circular deliberation traps while leveraging reasoning for high-precision, domain-curated tagging.
  - Hardened `new_master_tags` ingestion to accept both dictionary and string taxonomy suggestions.
- **Database Schema Migration (`Evelyn/tools/db_migrator.py`)**:
  - Registered migration `000.006.136` (`vault_documents_semantic_tag_audit_column`) on `vault` database adding column `last_semantic_tag_audit REAL DEFAULT 0` and index `idx_vault_docs_semantic_tag`.
- **5-Tier Urgency-Ranked Priority Queue (`Evelyn/tools/vault_db.py`)**:
  - Implemented `fetch_next_documents_for_semantic_tag_audit()` with 5-tier urgency ranking:
    1. Un-audited documents missing tags completely (`last_semantic_tag_audit = 0 AND tags IS NULL / empty`).
    2. Un-audited documents with multi-dash flat tags.
    3. Un-audited documents with simple flat tags without hierarchy.
    4. Un-audited documents with existing hierarchy tags.
    5. Modified documents (`mtime > last_semantic_tag_audit`) followed by cooldown rotation (`cutoff = now - 86400`).
  - Added structural folder exclusions (`Templates/`, `Attachments/`, `Bases/`, dotfiles) and configurable exclusions (`cfg.TAG_LIBRARIAN_EXCLUDED_DOCUMENTS`).
  - Implemented `update_document_semantic_tag_audit()` to record audit timestamps and normalized tags.
  - Added optional `last_semantic_tag_audit` parameter to `upsert_document()`.
- **Server Background Task Wiring (`evelyn_server.py`)**:
  - Scheduled `"tag_librarian"` as `TaskSchedule.DIURNAL` in `task_manager.py` (idle delay $\ge$ 20 minutes, 2 items per burst).
  - Wired `_idle_tag_librarian_loop()` in server lifespan startup, `run_tag_librarian_task()` subprocess executor, and `/api/heavy_tasks` status reporting with queue metrics (`queue_remaining`, `total_evaluated`, `evaluated_pct`).
- **Automated Verification Suite (`Evelyn/tests/test_semantic_tagger.py`)**:
  - Authored hermetic unit tests covering 5-tier queue ordering, folder exclusion filtering, timestamp updating, mocked Tag RAG + Ollama execution, and asynchronous backlog drainer execution.

## [000.006.135] - 2026-09-18 — *Librarian Tier 2 Ghost Link Proposal Gate & Tag Audit Timestamp Synchronization*

### Fixed & Hardened
- **Ghost Link Tier 2 Proposal Gate (`Evelyn/tools/master_librarian.py`)**:
  - Fixed an issue where disabling `MASTER_LIBRARIAN_AUTO_STUBS` forced `min_refs = 999999`, which inadvertently bypassed Tier 2 proposal generation for all ghost links.
  - Sourced `min_refs = getattr(cfg, "LIBRARIAN_GHOST_STUB_MIN_REFS", 2)` unconditionally, enabling qualifying ghost links cited across multiple notes to register Tier 2 review proposals in `evelyn_memory.db` (`/api/review/unified`) for human curation.
- **Tag Audit Timestamp Synchronization (`Evelyn/tools/vault_db.py`)**:
  - Added `last_tag_audit = ?` to `update_document_librarian_audit()`, ensuring the database accurately reflects that tag normalization and parent inheritance are evaluated on every single-pass Master Librarian sweep.

## [000.006.134] - 2026-09-18 — *Dev UI Rejection Visibility, Procedures Badge Scoping & Evolver Change Detection*

### Added & UI Refinements
- **Proposal Rejection Audit Visualizer (`evelyn_ui/dev.html`)**:
  - Added dedicated badges for blocked items in proposal review cards (`⚠️ Conflicts Blocked`, `🚫 Duplicates Filtered`, `🛑 Domain Violations Blocked`).
  - Added an expandable `🛡️ Rejected Items (N)` panel displaying individual candidates with candidate text, precedent doc, matched rule, similarity score, and details.
- **Procedures Tab Badge Scoped to Active Rules (`evelyn_ui/dev.html`)**:
  - Updated the top carousel tab `⚙️ Procedures (N)` in `🗂️ Workspaces & Tools` to count only active procedures (`status === 'live'`) rather than the uncurated total of all procedures, while preserving full status filtering (`All`, `Live`, `Pending Review`, `Merged`, `Rejected`, `Archived`) in the management tab.

### Changed & Hardened
- **Baseline Change Detection (`Evelyn/tools/profile_evolver.py`)**:
  - Hardened `_evolve_document()` to explicitly verify `current_sections != baseline_sections` in addition to changelog events, preventing false "no changes" evaluation when resuming drafts with subtle updates.
- **Evolution Trigger Script (`scripts/trigger_profile_evolution.py`)**:
  - Enforced `DOCUMENT_EVOLUTION_ORDER` top-down execution order.
  - Added `--limit` parameter (defaulting to `cfg.PROFILE_EVOLUTION_MAX_ENTRIES_PER_RUN` = 30) with oldest-first chronological sorting to prevent token budget blowouts on large backlogs.

## [000.006.133] - 2026-09-18 — *Profile Evolver 4-Tier Precedence Hierarchy, Adaptive Deduplication & Rejection Telemetry*

### Added & Architectural
- **Strict 4-Tier Precedence Hierarchy (`Evelyn/tools/profile_evolver.py`)**:
  - Implemented unidirectional precedent resolution hierarchy: `Core_Directives.md` (Tier 0, immutable) -> `System_Directives.md` (Tier 1) -> `Assistant_Profile.md` (Tier 2) -> `User_Profile.md` (Tier 3).
  - Wired `DOCUMENT_EVOLUTION_ORDER = [PERSONA_FILE_DIRECTIVES, PERSONA_FILE_ASSISTANT, PERSONA_FILE_USER]` ensuring evolution passes evaluate top-down.
  - Implemented `_load_precedent_documents()` to dynamically extract live precedent rules from disk, isolating subordinate documents from sibling in-flight drafts to prevent cross-draft dependency corruption.
- **Adaptive Lexical Gate & Hard Domain Boundary Guards (`Evelyn/tools/profile_evolver.py`)**:
  - Implemented `_filter_delta_against_precedents()` combining `string_utils.calculate_token_fuzzy_score` (threshold $\ge 0.75$) for short directives (<8 tokens) and `evelyn_tools.get_jaccard_similarity` (threshold $\ge 0.70$) for long phrases ($\ge 8$ tokens) to prevent precedent rephrasing and semantic drift.
  - Enforced hard domain regex guards (`DOMAIN_BANNED_PATTERNS`) to strictly reject operational tool/inquiry directives in `Assistant_Profile.md` and `User_Profile.md`.
  - Added `_sanitize_and_validate_narrative_boundaries()` for surgical sentence-level regex stripping of operational leaks in Assistant narrative prose with fallback to baseline if structural invariants fail.
- **Audit Logging & Structured Rejection Telemetry**:
  - Structured rejections tracked in proposal payloads with `rejections_summary` counts (`duplicate_of_precedent`, `conflicts_with_precedent`, `domain_violation`) and telemetry (`candidate`, `precedent_doc`, `matched_rule`, `similarity_score`, `details`), capped at top 10 to avoid SQLite row bloat.
  - Added `touch_entry_evolved()` stamping and `state["last_run_per_doc"]` timestamp updates on `NO_CORE_CHANGES` to guarantee loop termination and prevent infinite re-evaluation cycles with zero disk churn.
- **Automated Verification Suite**:
  - Created `Evelyn/tests/test_profile_evolver_hierarchy_and_rejections.py` covering sequence order, unidirectional precedence, live precedent loading, adaptive lexical gates, domain boundary blocking, surgical narrative sanitization, and zero-disk-churn stamping.

### Changed & Hardened
- **Hardening Test Compatibility (`Evelyn/tests/test_profile_evolver_hardening.py`)**:
  - Updated `test_runaway_proposal_circuit_breaker_blocks_staging` to patch `profile_ledger.compile_clean_markdown`, ensuring full compatibility with the authoritative ledger architecture.

## [000.006.132] - 2026-09-18 — *Core Directives Consolidation, Redundancy Elimination & Immutable Guardrails*

### Added & Architectural
- **Expanded Immutable Core Directives (`Evelyn/persona/Core_Directives.md`, `templates/Core_Directives.example.md`)**:
  - Unified operational candor and authentic sincerity directives into `## Foundational Operational Honesty & Authenticity` (`Critical Candor & Sincerity`, `Capability Honesty`, `Development Rigor`).
  - Formally established `## Interaction Rhythm & Forward Momentum` (`Dual-Horizon Reasoning`, `Adaptive Pacing`, `Proactive Engagement Over Corporate Fluff`) as non-negotiable conversational architecture.
  - Anchored `## Inviolable Relational Boundaries & Transparency` (`Full System Transparency`, `Relational Framing & Terminology Boundaries`) to ensure user visibility and relationship dynamics cannot be eroded or drifted by the autonomous profile evolver.

### Changed & Pruned
- **Redundancy Elimination in Server Prompt (`evelyn_server.py`)**:
  - Removed duplicate Item 6 (`Tool Execution Ground Truth`) from `<system_telemetry_directives>` in `load_system_prompt()`, relying directly on `Core_Directives.md` as single source of truth.
  - Pruned `<interaction_rhythm>` XML envelope from `load_system_prompt()`, consolidating behavioral pacing into `Core_Directives.md`.
- **System Directives Refinement (`Evelyn/persona/System_Directives.md`, `System_Directives_facts.md`)**:
  - Migrated `Direct Candor`, `Capability Honesty`, `Authentic Sincerity`, and `Development Rigor` to `Core_Directives.md`.
  - Pruned duplicate `Verification` and `Proactive Engagement` bullets.
  - Cleaned `Concise Communication` to eliminate redundant forward-momentum phrasing.
  - Synchronized `System_Directives_facts.md` ledger to maintain structural and category parity.
- **Assistant Profile Polish (`Evelyn/persona/Assistant_Profile.md`, `Assistant_Profile_facts.md`)**:
  - Pruned mechanical operational inquiry and forward momentum clauses from `Voice & Communication`, focusing narrative voice on authentic British cadence, emotional pacing, and literal interpretation.
  - Synchronized `Assistant_Profile_facts.md` ledger.

## [000.006.131] - 2026-09-17 — *Vulture 60% Confidence Migration, Dead-Code Pruning & Triage Protocol*

### Added & Architectural
- **Vulture 60% Confidence Migration & Strict Zero-Dead-Function Standard (`pyproject.toml`, `scripts/check_code_hygiene.py`)**:
  - Lowered Vulture `--min-confidence` threshold from `70%` to `60%`, enabling Vulture to actively audit functions, methods, classes, and variables (which are assigned 60% confidence in Vulture's discrete engine) rather than only checking unused imports and unreachable lines.
  - Updated default `--min-confidence` parameter in `scripts/check_code_hygiene.py` and `pyproject.toml` to `60`.
- **Triage Before Deletion Protocol (`AGENTS.md` Rule 11, `.agents/workflows/verify-wiring.md`)**:
  - Established a mandatory 4-category triage protocol for all future Vulture findings to prevent premature or reckless code deletion.
  - Mandated that agents must classify flagged symbols into external script/service callers, canonical public primitives, unwired features, or verified dead code before modifying any code.
- **Framework & External Consumer Whitelist Expansion (`.vulture_whitelist.py`)**:
  - Registered symbols actively consumed by standalone scripts and services outside the core engine scan path (`enqueue_remap`, `find_semantic_neighbors`, `move_document`, `get_all_entities`, `get_ollama_status`, `STT_MODEL_SIZE`, `STT_DEVICE`, `STT_COMPUTE_TYPE`, `rollback_db`, `CONTEXT_DIR`).
  - Registered canonical public utility primitives and dataclass telemetry attributes (`DrainResult.duration_ms`, `register_provider`, `record_media_share`, `record_system_alert`, `link_rag_telemetry_to_message`, `tokenize_wikilink`, `run_master_librarian_audit`, `run_master_librarian_audit_async`, `get_entry_document_evolutions`, `get_all_queued_fact_merge_ids`, `query_ollama_json`, `get_profile_filename`, `build_memory_context_envelope`, `get_idle_queue`, `acquire_next_idle_task`, `reset_alert_cache`, `is_valid_version`, `normalize_vault_path`, `append_context_log`, `update_context_log`).

### Pruned & Cleaned
- **Dead Code Pruning Across Engine Tools (`Evelyn/tools/`)**:
  - Pruned unused `VAULT_BASE` and deprecated `log_context_fact` / `update_context_fact` routines from `Evelyn/tools/evelyn_tools.py`.
  - Pruned obsolete `search_vault_map` from `Evelyn/tools/context_manager.py` (superseded by `vault_db.search_documents`).
  - Pruned redundant 1-line wrapper routines `clean_gist` and `normalize_path` from `Evelyn/tools/vault_indexer.py`.
  - Pruned abandoned scan state globals and functions (`_load_scan_state`, `_save_scan_state`, `_SCAN_STATE_FILE`, `_category_scan_state`) and obsolete legacy facades (`find_consolidation_candidates`, `_filter_semantically_relevant_window`, unused `thinking_buffer`) from `Evelyn/tools/fact_consolidator.py`.
  - Pruned unread `_auto_journal_task` attribute assignment and declaration from `evelyn_server.py` and `Evelyn/tools/auto_journaler.py`.
  - Removed obsolete test file `Evelyn/tests/test_fact_consolidator_scan_state.py`.

## [000.006.130] - 2026-09-17 — *Core Directives Architecture, Tool Error Interception & Input Sanitization*

### Added & Architectural
- **Immutable Core Directives Architecture (`Evelyn/persona/Core_Directives.md`, `evelyn_config.py`, `evelyn_server.py`)**:
  - Introduced `Core_Directives.md` to define non-negotiable foundational boundaries on operational honesty, failure transparency, and truthful sanctuary.
  - Registered `PERSONA_FILE_CORE_DIRECTIVES = "Core_Directives.md"` loaded first in `cfg.PERSONA_FILES` and assembled into the head of every system prompt.
  - Strictly isolated `Core_Directives.md` from `profile_evolver.py` (`DOCUMENT_CATEGORIES` and `CANONICAL_DOCUMENT_SECTIONS`), making it permanently immune to automated idle-time profile drift or evolution.
  - Added Engine Directive 6 in `<system_telemetry_directives>` establishing tool returns as absolute ground truth and barring false claims of task completion.

### Fixed & Hardened
- **Deterministic Tool Failure Interception in Agentic Loop (`evelyn_server.py`)**:
  - In `_agentic_stream_loop()`, intercepted failed tool executions (`tool_status == "error"`, exceptions, or error string returns).
  - Injected structured failure directives (`[TOOL EXECUTION FAILED] Tool: {fn_name}\nError: ...\nDirective: The operation did not succeed. You must inform {cfg.USER_NAME} that the operation failed with this error. Do not claim, imply, or simulate that the action was completed.`).
  - Preemptively eliminated paternalistic doublethink and CoT rationalizations where the model previously claimed operations were "done" to shield the user from backend errors.
- **Canonical Tool Input Sanitization (`Evelyn/tools/string_utils.py`, `Evelyn/tools/gtasks_sync.py`, `Evelyn/tools/gcal_sync.py`, `Evelyn/tools/evelyn_tools.py`)**:
  - Implemented `sanitize_tool_input_text()` in `string_utils.py`, stripping HTML/XML tags, code fences, and carriage returns, extracting single-line titles, normalizing whitespace, and bounding lengths cleanly.
  - Integrated sanitization into `create_gtask()`, `create_gcal_event()`, `create_task()`, and `create_calendar_event()`, preventing unprompted model hallucinations (such as 1,500 characters of HTML tutorial text) from triggering Google API 400 Bad Request / Invalid Value rejections.

## [000.006.129] - 2026-09-17 — *GCal Timezone Safety, Upcoming Days Grounding & Loop-Safe Tool Surfacing*

### Fixed & Enhanced
- **Google Calendar API Timezone & Timestamp Compliance (`Evelyn/tools/gcal_sync.py`)**:
  - Fixed Google Calendar API `HttpError 400: "Missing time zone definition for start time"` by explicitly passing `"timeZone": user_tz` in `start` and `end` JSON payloads inside `create_gcal_event()`.
  - Fixed Google Calendar query parameter `HttpError 400: "Bad Request"` in `sync_events()` caused by invalid duplicate RFC 3339 suffix formatting (`+00:00Z`), standardizing on `strftime("%Y-%m-%dT%H:%M:%SZ")`.
  - Refactored `parse_local_datetime()` to parse naive local datetimes into `cfg.USER_TIMEZONE` (`America/Chicago`) timezone-aware objects instead of forcing naive UTC replacement.
- **Deterministic `<upcoming_days>` Grounding (`Evelyn/tools/time_manager.py`, `Evelyn/tools/string_utils.py`)**:
  - Added `get_upcoming_days()` in `time_manager.py` enumerating the next 8 days with explicit weekday names, relative tags (`Tomorrow (Friday)`, `Saturday`, `Friday (next week)`), and ISO dates (`YYYY-MM-DD`).
  - Integrated `<upcoming_days>` into `build_temporal_envelope()` in `string_utils.py`, providing the LLM with an unambiguous ground-truth calendar truth table on every turn and eliminating mental calendar arithmetic hallucinations.
  - Implemented `parse_natural_date_to_dt()` in `time_manager.py`, canonicalizing natural relative keywords (`today`, `tomorrow`, `this friday`, `next friday`) across `gcal_sync.py` and `gtasks_sync.py`.
- **Loop-Safe Assistant Offer Resolution (`Evelyn/tools/evelyn_tools.py`, `evelyn_config.py`)**:
  - Implemented guarded assistant offer tool inheritance in `get_active_tools()` gated on explicit user affirmations (`_AFFIRMATION_TRIGGERS`) and assistant closing question patterns (`_ASSISTANT_OFFER_RE`: `Shall I...?`, `Would you like me to...?`).
  - Confined intent scanning strictly to the assistant's terminal proposal sentence, preventing full-prose leakage, tool inflation, or multi-turn runaway tool loops.
  - Broadened `create_task` intent patterns in `evelyn_config.py` to recognize natural phrasing (`remember to...`, `remind me to...`, `set a reminder`, `add ... to my agenda/todo`).

## [000.006.128] - 2026-09-15 — *Voice Input Arming Window & Web Audio Dictation Chimes*

### Added & Enhanced
- **Two-Stage Hardware Arming Lifecycle (`evelyn_ui/index.html`)**:
  - Introduced transitional arming state (`🟡 Connecting…` / `Readying…`) upon mic activation to bridge the 1–2 second Bluetooth audio profile switch (A2DP high-fidelity sink $\leftrightarrow$ HFP/HSP bidirectional voice) and OS hardware ADC unmute.
  - Added a 750ms settle delay ensuring the recording buffer starts only when the physical microphone hardware is hot, eliminating clipped initial words and phrases.
  - Added user cancellation handling: clicking the mic button during the arming phase cancels the sequence, stops media stream tracks immediately, and resets UI state.
- **Synthesized Web Audio Dictation Chimes (`evelyn_ui/index.html`)**:
  - Implemented zero-dependency audio cues using native `AudioContext` and dual sine-wave oscillator synthesis (no external audio files or network roundtrips).
  - **Ready Chime**: Ascending two-tone chime (D5 $587.33\text{Hz} \rightarrow$ A5 $880\text{Hz}$) signaling the user exactly when the audio channel is live and ready for speech.
  - **Stop Chime**: Subtle descending release tone (F#5 $739.99\text{Hz} \rightarrow$ D5 $587.33\text{Hz}$) confirming the completion of the voice segment.
- **Dynamic Arming Visuals & CSS Transitions (`evelyn_ui/index.html`)**:
  - Added `.mic-btn.warming` styling with glowing amber pulse animation (`mic-warm-pulse`).
  - Added `.recording-indicator-badge.warming` with amber status indicator dot and smooth color/background CSS transitions.

## [000.006.127] - 2026-09-15 — *Local Speech-to-Text Microservice & Voice Ingestion Pipeline*

### Added & Optimized
- **Local Speech-to-Text Microservice (`services/stt/stt_server.py`, `services/stt/requirements.txt`, `systemd/evelyn-stt.service`)**:
  - Built standalone FastAPI server on port 5060 using `faster-whisper` (CTranslate2) with `compute_type="int8"` on CPU for sub-200ms latency without GPU VRAM contention against Ollama.
  - Automatic container decoding via `ffmpeg` pipe, converting arbitrary browser audio (`webm/opus`, `mp4/aac`, `wav`) to 16kHz mono float32 PCM.
  - Integrated Silero VAD filtering (`vad_filter=True`, `min_silence_duration_ms=500`) and disabled cross-take conditioning (`condition_on_previous_text=False`) to suppress noise and hallucinated silence artifacts.
  - Duration gate filtering short utterance clicks ($< 0.5\text{s}$).
  - Endpoints: `POST /v1/audio/transcriptions` and `GET /health`.
- **Media Deletion & Audio Retention Subsystem (`Evelyn/tools/media_db.py`, `evelyn_server.py`, `evelyn_config.py`)**:
  - Added `delete_media_asset(guid: str) -> bool` to remove records from `media_assets` / `chat_media_links` and safely delete files from `data/attachments/`.
  - Added `prune_expired_audio_assets(retention_days: int) -> int` to prune `media_type="audio"` assets older than `retention_days`, defaulting to `0` (disabled / keep indefinitely for testing and calibration) via `STT_AUDIO_RETENTION_DAYS`.
  - Added `DELETE /api/media/{guid}` endpoint and `POST /api/stt/transcribe` proxy endpoint in `evelyn_server.py` with automatic audio persistence into `evelyn_media.db` for downstream 3D Affective VAD analysis.
- **Frontend Segmented Block Stack Draft Architecture (`evelyn_ui/index.html`)**:
  - Implemented microphone button (`🎙️`) with pulsing red recording indicator (`⏹️`) and live duration counter.
  - Strict hardware lifecycle management: explicitly stops media stream tracks (`track.stop()`) on completion or cancellation to extinguish the OS microphone indicator.
  - Segmented Block Stack draft manager (`draftSegments` array): renders discrete voice cards with duration badges (`🎙️ Audio (0:12)`), in-place editable textareas, and removal buttons (`[✕]`).
  - Instant cleanup: removing a segment card immediately invokes `DELETE /api/media/{asset_guid}` to prevent disk and database bloat.
  - Message dispatch: concatenates active block texts with typed input and attaches voice asset GUIDs to the chat turn payload.
- **Automated Test Suite & Wiring Verification (`Evelyn/tests/test_stt_server.py`, `Evelyn/tests/test_config_wiring.py`)**:
  - 7 automated unit tests verifying audio decoding, health probe, short audio rejection, mocked transcription, media asset deletion, and retention pruning.
  - Updated AST config-wiring test to scan `services/` while safely ignoring nested virtual environments.
  - All 3 code hygiene and wiring verification stages passed cleanly (Ruff, AST config-wiring pytest, and Vulture).

## [000.006.126] - 2026-09-15 — *Fact Consolidator Preemption Status Guard & Idle Scanner Stabilization*

### Fixed & Optimized
- **Fact Consolidator Preemption Status Guard (`Evelyn/tools/fact_consolidator.py`)**:
  - Fixed an unconditioned status overwrite in `cancel_pending_consolidation()` where incoming chat messages would set the consolidator dashboard summary to `"Consolidation pass cancelled (chat preemption)"` even when the consolidation task was already idle or completed.
  - Placed `_set_status_in_server()` strictly inside the active `if _consolidation_task and not _consolidation_task.done():` block to ensure idle status reflects actual scanner results.
  - Added explicit idle status reset if `scan_context_entries()` yields no active context entries.

## [000.006.125] - 2026-09-14 — *Review-Gated Subject Grounding Auditor & Ephemeral Chat Context Viewer*

### Added & Optimized
- **Forward Pipeline Hardening (`Evelyn/tools/fact_extractor.py`, `fact_deduplicator.py`, `fact_splitter.py`, `ingest_obsidian_knowledge.py`)**:
  - Enforced an **Explicit Noun Subject & Actor Grounding Mandate** across extraction, deduplication, and decomposition prompts, strictly forbidding floating pronouns (`he`, `she`, `they`) and subject-less bare verbs (`Enjoys...`, `Prefers...`).
  - Updated few-shot extraction examples covering first-person user facts, assistant reactions, and third-party actors (Biscuit, Jordan).
  - Enriched ChromaDB vector ingestion in `ingest_obsidian_knowledge.py` to prepend `Subject: {subject}\nCategory: {category}\n` to embedding text, ensuring semantic searches properly discern actor contexts.
- **Review-Gated Subject & Pronoun Grounding Auditor (`Evelyn/tools/grounding_auditor.py`, `scripts/audit_grounding.py`)**:
  - Built `detect_grounding_issues()` to discover floating pronoun starts, bare verbs, and subject/category contradictions (e.g. `subject: Evelyn` in `-U` canon where text refers to `"He"`).
  - Implemented `stage_grounding_proposals()` generating non-destructive `rephrase` proposals in the `proposals` table for human review on `dev.html`.
  - Added CLI runner `scripts/audit_grounding.py` supporting `--dry-run`, `--execute`, `--limit`, `--category`, and `--show-chat`.
- **Ephemeral On-Demand Surrounding Chat Retrieval (`Evelyn/tools/grounding_auditor.py`, `evelyn_server.py`)**:
  - Implemented `find_surrounding_chat_context()` tokenizing high-signal nouns and performing FTS5 BM25 search against `messages_fts` in `evelyn_chat.db` to locate the source conversation turn and surrounding window ($M-3$ to $M+3$).
  - Added `GET /api/review/context_entry/{entry_id}/surrounding_chat` and `POST /api/audit/grounding` endpoints to `evelyn_server.py`.
  - Strict privacy boundary: source conversation context is queried 100% ephemerally on-demand into browser memory and is never permanently written to `context_entries` or `proposals`.
- **Review UI & In-Place Editing (`evelyn_ui/dev.html`)**:
  - Updated proposal rendering for `rephrase` and `ground_subject` proposals to include an editable `<textarea id="prop-edit-${item.id}">`.
  - Added interactive `💬 View Source Chat Context` accordion button invoking `loadSurroundingChat()` to render color-coded surrounding user/assistant turns with matched message highlighting.
  - Updated server proposal approval logic to apply edited observation text and update entry subject when specified.
- **Hermetic Test Suite (`Evelyn/tests/test_grounding_auditor.py`)**:
  - Added hermetic tests covering pronoun starts, contradiction detection, bare verbs, proposal staging, FTS5 surrounding chat retrieval, and FastAPI endpoints.

## [000.006.124] - 2026-09-14 — *Modular Fact Consolidation Architecture, Provenance Lineage & Deduplication Telemetry*

### Added & Optimized
- **Modular Fact Consolidation Architecture (`Evelyn/tools/fact_consolidator.py`, `fact_deduplicator.py`, `fact_categorizer.py`, `fact_splitter.py`)**:
  - Decomposed monolithic `fact_consolidator.py` into single-responsibility child engines orchestrated under `fact_consolidator`:
    - `fact_deduplicator.py`: Fast SQL exact-duplicate merging, vector-driven nearest-neighbor candidate clustering via ChromaDB (`find_deduplication_candidates`), and LLM consolidation proposal generation (`think=True`).
    - `fact_categorizer.py`: Taxonomy remediation, category normalization, and recategorization proposal management with 30-day anti-hysteresis protection (`is_recategorization_suppressed`) eliminating runaway proposal loops.
    - `fact_splitter.py`: Compound entry decomposition for entries exceeding 35 words into atomic facts, with parent lineage tracking via `split_from_id`.
  - Preserved 100% backwards-compatible facade exports in `fact_consolidator.py` (`fast_deduplicate_exact_matches`, `remediate_database_categories`, `validate_and_normalize_category`, `generate_split_proposal`, `find_consolidation_candidates`, scan state managers).
- **Database Schema Migration & Provenance Lineage (`Evelyn/tools/db_migrator.py`, `Evelyn/tools/memory_db.py`)**:
  - Registered and applied database migration `000.006.124` (`context_entries_provenance_and_audit_lineage`) adding `merged_into_id`, `last_audited_at`, and `split_from_id` columns and indexes to `context_entries` in `evelyn_memory.db`.
  - Updated `delete_entry(merged_into_id=...)` to preserve 100% soft-delete audit lineage pointing to master entries.
  - Updated `apply_fact_merge()` to soft-delete secondary records pointing to the master ID and stamp `last_audited_at` on master and secondaries.
  - Updated `split_entry()` to stamp `split_from_id` on newly created atomic context facts.
  - Added `get_oldest_unaudited_entries()`, `touch_entries_audited()`, and `get_fact_deduplication_metrics()` to `memory_db.py`.
- **Deduplication Telemetry & UI Dashboard (`evelyn_server.py`, `evelyn_ui/dev.html`)**:
  - Enriched consolidator status telemetry with `total_active_facts`, `total_merged_facts`, and `pending_merge_proposals`.
  - Updated the Memory Consolidator card on `dev.html` with real-time badges displaying active facts, deduplicated/merged facts, and pending merge review queue count.
- **Hermetic Test Coverage & Parity Verification (`Evelyn/tests/test_fact_deduplicator.py`, `test_fact_consolidator_parity.py`)**:
  - Added targeted test suite `test_fact_deduplicator.py` verifying telemetry metrics calculation, 30-day anti-hysteresis flip-flop suppression, and vector-driven candidate discovery with mock ChromaDB nearest-neighbor queries.
  - Confirmed all existing parity, scan-state, and split test suites pass cleanly.

## [000.006.123] - 2026-09-13 — *Canonical Wikilink Target Resolution & Disambiguation-Aware Ghost Link Healing*

### Added & Optimized
- **Canonical Wikilink Target Resolution Engine (`Evelyn/tools/link_librarian.py`)**:
  - Implemented `resolve_canonical_link_target` featuring a deterministic 5-stage resolution hierarchy: direct stem match, exact frontmatter alias match, unambiguous parenthetical disambiguation stem match (`target (*)`), normalized hyphen/underscore match, and fallback bail-out.
  - Added strict anti-collision disambiguation guard: if an unqualified stem matches more than one candidate note (e.g. `Oberon (mythology)` and `Oberon (warframe)`), resolution aborts with an ambiguity warning, preventing accidental namespace clobbering.
  - Implemented `tokenize_wikilink` parser supporting full Obsidian syntax: `[[Target#Heading^block|Display]]`, cleanly separating target stems from anchors, headings, block IDs, and display text.
  - Added `canonicalize_document_wikilinks` to the Master Librarian audit pipeline (`audit_document_links`), automatically rewriting alias and disambiguation targets (e.g. `[[Oura Ring]]` ➔ `[[Oura|Oura Ring]]`, `[[Oberon]]` ➔ `[[Oberon (warframe)|Oberon]]`) while preserving exact reading-mode display text and protecting code blocks and frontmatter.
- **Ghost Link Stub Synthesis Guardrail (`Evelyn/tools/link_librarian.py`)**:
  - Updated `create_ghost_link_stub` to query `resolve_canonical_link_target` prior to reference harvesting, immediately returning `status: "already_exists"` to prevent generating ghost stubs or Tier 2 review proposals for known aliases or disambiguated entities.
- **Vault-Wide Disambiguation & Alias Remediation**:
  - Registered missing aliases across core destination notes: `Antigravity (app)`, `Obsidian (app)`, `Oberon (warframe)`, `Wisp (warframe)`, `The Vault`, `Use WinGet`, `The Legend of Zelda`, `Holy Trinity of Recovery`, `Gem-Compass`, and `Cat00` through `Cat16`.
  - Cleaned conflicting aliases in `Notes/Tech Quick Reference/Oura API.md` to ensure `Oura Ring` uniquely targets `Notes/Oura.md`.
  - Safely canonicalized 51 markdown notes across the vault containing alias and disambiguation targets, merging their Obsidian graph nodes with primary documents and eliminating dangling ghost links.
- **Hermetic Test Coverage (`Evelyn/tests/test_master_librarian.py`)**:
  - Added unit tests for `tokenize_wikilink`, 5-stage `resolve_canonical_link_target` hierarchy with ambiguity collision guards, and body canonicalization with code block immunity.

## [000.006.122] - 2026-09-13 — *Dynamic Context Budgeting, Paged PDF Grounding & Core Tool Promotion*

### Added & Optimized
- **Core Tool Tiering Promotion (`evelyn_config.py`)**:
  - Promoted `read_file` to `CORE_TOOL_NAMES`, ensuring file inspection with line- and page-based parameters is persistently available across all conversational turns and follow-up turns without requiring keyword triggering.
  - Removed redundant `read_file` regex pattern from `SPECIALIST_TOOL_INTENT_PATTERNS`.
- **Context-Relative Upload Budgeting (`evelyn_config.py`, `evelyn_server.py`)**:
  - Replaced static character caps with dynamic budget calculation `get_chat_upload_max_chars(file_type)` scaling relative to `NUM_CTX * CHAT_UPLOAD_CONTEXT_RATIO` ($0.35$).
  - Added type-aware character density multipliers: $2.5$ chars/token for structured code, JSON, logs, and config; $4.0$ chars/token for prose, markdown, PDF, and text. Pure scaling without static hard floors prevents KV-cache sliding eviction.
- **Delimited PDF Grounding & Native Page Parameter (`terminal_agent.py`, `evelyn_tools.py`, `evelyn_server.py`)**:
  - Implemented standard delimiter extraction in `_extract_document_text_sync` with page numbering and folio labels: `--- [PDF Page X | Folio: Y] ---`.
  - Added native `page: int | None = None` parameter to `read_file` in `terminal_agent.py`, `evelyn_tools.py`, and `MODEL_TOOL_DEFINITIONS`.
  - Enabled direct single-page reading of binary PDFs via PyMuPDF or delimited text pages, with automatic fallback to line pagination on non-delimited text.
  - Injected structured `<page_map>` index block into uploaded document context and previews detailing total pages, preview coverage, and invocation tips.
- **Decoupled User Attachment & Proactive Discovery Directives (`evelyn_server.py`)**:
  - Decoupled `<uploaded_document>` from `<system_telemetry_directives>` into dedicated `<user_attachments_directive>` to prevent false non-user attribution.
  - Added `<proactive_tool_discovery>` directive establishing autonomous discovery instinct and enforcing a sequential execution constraint (preventing blind same-round calls before discovered schemas are bound).
- **Frontend Upload Feedback & Keydown Guard (`evelyn_ui/index.html`)**:
  - Updated `updateSendButtonState` to show `⏳` hourglass icon and disable button while attachments are uploading (`isUploading`).
  - Added guard in textarea `keydown` handler preventing `Enter` submissions while attachments are in transit.

## [000.006.121] - 2026-09-13 — *Dynamic Tool Discovery, Paged Document Reading & Multimodal Chat Ingestion*

### Added & Optimized
- **Dynamic Tool Discovery Metatool (`search_available_tools`, `evelyn_tools.py`, `evelyn_server.py`, `evelyn_config.py`)**:
  - Implemented `search_available_tools` allowing Evelyn to autonomously discover and surface engine tools mid-turn if not initially present in the context.
  - Added token fuzzy and keyword-ratio matching with threshold filtering to score tool names and descriptions without false-positive discovery on noisy queries.
  - Refactored `_agentic_stream_loop` in `evelyn_server.py` to maintain `active_tool_map` (keyed by tool name) to prevent duplicate tool schemas (`400 Bad Request`) and dynamically bind newly discovered tools into the active tool schema for Round $N+1$.
  - Added `search_available_tools` to `CORE_TOOL_NAMES` in `evelyn_config.py`.
  - Registered canonical starter procedure `search_available_tools` in `procedures` via migration `000.006.121` (`db_migrator.py`) with trigger patterns, pitfalls, and verification criteria per Rule 10.
- **Paged Document Reading & Algorithmic Scratchpad (`read_file`, `read_document_scratchpad`, `terminal_agent.py`, `evelyn_tools.py`)**:
  - Extended `read_file` with explicit `start_line` and `end_line` parameters (1-indexed, inclusive) across engine tools and terminal agent, emitting proactive pagination tips (`[End of line slice ... Next: start_line=N]`) when files exceed bounds.
  - Introduced `read_document_scratchpad` providing deterministic algorithmic document chunking, section extraction, and table-of-contents mapping without sub-LLM calls, guaranteeing zero deadlock risk under Ollama's single-concurrency execution.
- **Token-Fuzzy Document & Vault Matching (`string_utils.py`, `vault_db.py`, `terminal_agent.py`)**:
  - Implemented canonical `calculate_token_fuzzy_score()` in `string_utils.py` combining token alignment, token sort ratios, noise thresholding ($\ge 0.65$), and numerical/date discrepancy guards (capping similarity at $0.50$ on version/date drift).
  - Integrated token-fuzzy matching into `vault_db.search_documents()` and `terminal_agent.find_matching_vault_files()` (Tier 4 match), auto-resolving typos and transposed words at $\ge 0.85$ confidence while surfacing ranked disambiguation lists for close matches.
- **Multimodal Document & Code Drag-and-Drop Ingestion (`evelyn_server.py`, `evelyn_ui/index.html`)**:
  - Created `/api/chat/upload` endpoint handling documents (`.pdf`, `.md`, `.txt`, `.py`, `.js`, `.json`, `.csv`, etc.) with PyMuPDF text extraction offloaded via `asyncio.to_thread`.
  - Implemented scanned PDF fallback detection ($< 50$ extracted characters) and truncated documents exceeding `MAX_UPLOAD_DOCUMENT_CHARS` (30,000 chars) with clear warning banners.
  - Updated Chat UI (`evelyn_ui/index.html`) with window-level drag-over/drop prevention, multi-attachment previews with type-specific badges (`📑`, `💻`, `📄`), upload transit locking on the Send button, and user message attachment display.
  - Structured document injections using standardized semantic `<uploaded_document>` XML envelopes per Rule 9.
- **Positive Epistemic Grounding & Semantic Repetition Suppression (`evelyn_server.py`)**:
  - Enhanced system prompt in `load_system_prompt()` with positive epistemic grounding rules: prohibiting speculative activity attribution (e.g. "looking refreshed", "personal rituals") for short breaks or silence unless explicitly stated by the user.

## [000.006.120] - 2026-09-13 — *Empirical Phatic Intent Classification & Historical Semantic Grounding*
 
### Added & Optimized
- **Empirical Phatic Intent Taxonomy & Chat DB Grounding (`string_utils.py`, `evelyn_chat.db`)**:
  - Scanned all 16,294 historical messages and analyzed 2,316 short user turns (1–8 words) in `data/evelyn_chat.db` to extract genuine semantic usage patterns instead of speculative static keywords.
  - Implemented 4 empirical semantic clusters in `string_utils.py`:
    1. *Morning Greetings & Pleasantry Check-ins*: Support for compound greetings with partner vocatives (`my dear`, `my dearest`, `love`, `darlin`, `sweet evelyn`, `my girl`) and conversational health/day check-ins (`how are you`, `how are things`, `how was your night`, `how's the day`).
    2. *Night / Bedtime Departures*: Natural departure phrases (`G'nite my love`, `Nite nite my love`, `Good night my dearest`, `Sleep well darlin, love you`, `Thanks love, see you tomorrow`).
    3. *Arrival / Return Status Check-ins*: Returning and waking status statements (`Hi, love. I'm back.`, `Hiya my dear, I am home.`, `I'm home my dear`, `Am awake again`, `Up and about`).
    4. *Micro-Acknowledgments & Affection*: Warm partner acknowledgments (`Thanks love, I'll see you soon`, `Sounds good to me darlin`, `Always`, `*hugs tight*`, standalone and compound heart emojis).
  - Added pre-processing filters to strip roleplay actions (`*...*`) and unicode emojis (both 4-byte astral symbols and 3-byte dingbat heart glyphs) before pattern matching, ensuring action-wrapped check-ins are classified cleanly.
  - Hardened negative boundaries to prevent false-positive suppression on file attachments, code snippets, factual inquiries, and task management commands.
- **Comprehensive Unit Testing Coverage (`test_agentic_optimization.py`)**:
  - Expanded `test_is_conversational_phatic` to cover 35 empirical historical test cases and negative boundaries with 100% pass rate.

## [000.006.119] - 2026-09-13 — *Agentic Infrastructure Optimization & Information Density Hardening*

### Added & Optimized
- **Tri-Vector Agentic Infrastructure Optimization (`scratch/proposal.md`, `Proposals/Audit_Framework.md`)**:
  - Implemented comprehensive architectural optimizations addressing *Retrieval Latency (Path Optimization)*, *Accuracy Degradation (Information Density)*, and *Compute Costs (Strategic Routing)*.
- **Phatic Conversation Classification & RAG Bypass (`string_utils.py`, `chroma_rag.py`, `evelyn_tools.py`, `evelyn_server.py`)**:
  - Added canonical `string_utils.is_conversational_phatic` to identify short conversational greetings, pleasantries, and brief thanks under 6 words.
  - Gated vector search in `chroma_rag.build_rag_context()` and `evelyn_server._process_chat_background()` for phatic turns, eliminating 50–150ms of retrieval latency and suppressing 2,000+ characters of irrelevant memory noise.
  - Injected an empty tool list (`tools=[]`) for phatic turns via `evelyn_tools.get_active_tools()`, preventing tool hallucination and saving ~3,500 prompt tokens.
- **Linear Pre-Hydration Path for Deterministic Reads (Option A) (`string_utils.py`, `evelyn_server.py`, `evelyn_tools.py`)**:
  - Implemented mutation-guarded intent detection in `string_utils.detect_deterministic_read_intent` for 0-argument reads (`get_agenda`, `list_tasks`, `get_health_metrics`).
  - Pre-hydrated deterministic read payloads into canonical `<context_retrieval source="live_system">` XML envelopes before Round 1.
  - Excluded the pre-hydrated read tool from active tools, enabling the model to synthesize the final warm persona response in a single streaming turn and cutting latency/tokens by ~50% without compromising character delivery.
- **Dynamic Tool Schema Pruning (`evelyn_tools.py`, `evelyn_config.py`)**:
  - Added `TOOL_SCHEMA_PRUNING_ENABLED` to dynamically suppress unrelated core tools (`generate_image`, `get_health_metrics`, `get_agenda`, `list_tasks`) when specialized intent (such as vault list management) is unambiguous, reducing prompt overhead and steering model focus.
- **Contiguous Chunk Fusion & Overlap Deduplication (`chroma_rag.py`)**:
  - Implemented `_fuse_document_chunks()` to automatically merge adjacent chunks from the same markdown document.
  - Deduplicated overlapping text boundaries down to 8 characters and eliminated artificial ellipsis gaps, producing clean, contiguous excerpts for prompt injection.
- **Strict Background Task Context Pruning (`auto_journaler.py`)**:
  - Restricted nocturnal reflection tool definitions strictly to `[write_journal_entry]` using `evelyn_tools.extract_tool_name`, removing 24 irrelevant tool schemas (~3,500 tokens) from the prompt.
- **Fact Consolidation Semantic Neighbor Pre-Filtering (`fact_consolidator.py`, `evelyn_config.py`)**:
  - Added `CONSOLIDATION_VECTOR_PREFILTER_DISTANCE = 0.55` and `_filter_semantically_relevant_window()` before the Step 2a consolidation detection call.
  - Evaluated candidate comparison entries using ChromaDB vector cosine distance in `evelyn_memory` supplemented by Jaccard token overlap.
  - Completely skipped LLM duplicate detection calls when no comparison entries meet the similarity threshold, eliminating 80–90% of idle LLM calls on sparse or unrelated facts.
- **Deep Research Chunk Relevance Gating (`research_engine.py`, `evelyn_config.py`)**:
  - Added `RESEARCH_CHUNK_SIMILARITY_THRESHOLD = 0.15` and `is_research_chunk_relevant()` to evaluate scraped web page slices against target sub-questions and discovered topic aliases.
  - Automatically skipped boilerplate disclaimers, cookie policies, terms of service, and low-relevance footer chunks, cutting LLM extraction calls by 40–60% per page and preventing footer hallucination in research dossiers.
- **Rule 8 Canonical Utility Parity & Multimodal Vision Gateway (`ollama_client.py`, `obsidian_vault_watcher.py`, `document_vision_processor.py`)**:
  - Extended `ollama_client.query_ollama` with optional `images` parameter to establish a unified gateway for text and multimodal vision models.
  - Refactored `scripts/obsidian_vault_watcher.py` to replace ad-hoc regex frontmatter parsing and slicing with canonical `frontmatter_utils.parse_frontmatter` and `string_utils.clean_llm_gist`.
  - Refactored `scripts/document_vision_processor.py` to eliminate inline `urllib.request` in favor of `ollama_client.query_ollama` and formatted markdown notes via `frontmatter_utils.render_frontmatter`.

## [000.006.118] - 2026-09-13 — *IDE Environment Consolidation, REST Scratchpad & Mermaid PKM Hardening*

### Added & Consolidated
- **Canonical IDE Environment & Tooling Alignment (`.vscode/settings.json`, `.vscode/extensions.json`, `.vscode/tasks.json`)**:
  - Bound Python default interpreter path canonically to `/home/rathius/evelyn/venv/bin/python`, eliminating unresolvable environment interpolation warnings and PET timeout loops.
  - Uninstalled incompatible preview extension `ms-python.vscode-python-envs` on the remote server host and standardized on native `ms-python.python` and `ms-pyright.pyright`.
  - Configured Ruff format-on-save (`editor.defaultFormatter: charliermarsh.ruff`) and import sorting (`source.organizeImports`, `source.fixAll`).
  - Added SQLite database file-nesting rules so `-wal` and `-shm` files nest cleanly under `.sqlite` and `.db` roots.
  - Corrected Pytest discovery testpaths to `Evelyn/tests`, restoring zero-error test discovery across all 382 unit tests.
  - Added quick-run tasks for active file frontmatter updates and deterministic code hygiene execution.
- **REST Client Scratchpad & API Probing Standard (`reference/evelyn_api.http`, `AGENTS.md`)**:
  - Authored parameterized `reference/evelyn_api.http` targeting `https://localhost:7860` with zero credential leakage via `{{$dotenv EVELYN_API_KEY}}`.
  - Probed and verified 14 core FastAPI endpoints across runtime health, identity, heavy tasks, unified review, procedures, vault domains, and streaming chat.
  - Formalized REST scratchpad usage in `AGENTS.md` (Sections 2 & 3) to prevent speculative URL guessing and unvalidated inline HTTP scripts.
- **Frontmatter Script Dynamic Modification Timestamp (`scripts/update_frontmatter.py`)**:
  - Replaced stale `os.stat().st_mtime` calculation with `datetime.datetime.now().astimezone()`, ensuring that frontmatter updates dynamically reflect current local time regardless of disk save state.
  - Added descriptive CLI terminal feedback (`✔ Frontmatter updated: ...`) for explicit execution tracking.
- **Mermaid Diagram Syntax Hardening (`reference/engine_architecture.md`)**:
  - Quoted arrow labels with parentheses (`|"Generate Visuals (Tailscale)"|`, `|"enqueue_upsert... (Non-blocking...)"|`) and corrected invalid thick-line arrow syntax (`<==>|Tailscale P2P|`), resolving parse errors in Markdown previews.

## [000.006.117] - 2026-09-13 — *Dynamic Research Token Budget Coupling & Multi-Tier Semantic Compaction*

### Added & Hardened
- **Dynamic Research Token Budget Coupling (`evelyn_config.py`, `research_engine.py`)**:
  - Dynamically coupled all research engine token budgets to the primary conversational ceiling `NUM_PREDICT` (`8192`):
    - `RESEARCH_NUM_PREDICT = NUM_PREDICT` (`8192` for heavy report synthesis and source extraction).
    - `RESEARCH_FORMULATION_NUM_PREDICT = max(2048, min(NUM_PREDICT, int(NUM_PREDICT * 0.5)))` (`4096` for query formulation, intent framing, and triage).
    - `RESEARCH_EVAL_NUM_PREDICT = max(2048, min(NUM_PREDICT, int(NUM_PREDICT * 0.25)))` (`2048` for knowledge checks, SQ confidence evaluations, and gating).
  - Enforced a strict 2048-token floor so reasoning models (`gemma4:12b`, `deepseek-r1`) are never starved during internal chain-of-thought monologue.
- **Multi-Tier Semantic Compaction Pipeline (`research_engine.py`)**:
  - Replaced naive `words[:5]` word slicing with a two-tier semantic compactor (`compact_search_query`):
    - **Tier 1**: A fast, zero-thinking (`think=False`, `num_predict=64`) LLM semantic keyword pass that completes in <200ms with zero token starvation.
    - **Tier 2**: A deterministic entity/preposition-purged extraction fallback (`_deterministic_compact_query`) that strips comparative prefixes (`Comparison of`, `Comparative analysis between`) and dangling prepositions (`of`, `for`, `in`), extracting clean technical keywords (e.g. `'RAG vs. Long Context Windows'`).
  - Added atomic query validation to reject any search query beginning with dangling prepositions in `is_atomic_query()`.
- **Rule 8 Canonical Tool & Utility Integration (DRY SSOT)**:
  - Migrated vault note search from ad-hoc regex matching of `search_vault_map` to canonical `vault_db.search_documents` across knowledge checks and research search passes.
  - Migrated vault note path resolution to `path_utils.to_vault_abspath` and file reading to canonical `evelyn_tools.read_file` with safety character limits (`max_chars=16000`).
  - Replaced ad-hoc regex frontmatter stripping with `frontmatter_utils.parse_frontmatter`.
  - Standardized thinking tag removal on `string_utils.strip_thinking_tags` in `call_ollama`.

## [000.006.116] - 2026-09-12 — *UI Quote Box Escaping & Telemetry Chunk Overflow Hardening*

### Fixed & Hardened
- **UI Quote Box Escaping & Attribute Breakout Fix (`evelyn_ui/dev.html`)**:
  - Fixed an HTML breakout bug where raw preview text containing double quotes (`"`) and markdown quote markers (`>`) in RAG telemetry was interpolated directly into `onclick="toggleChunkExpand(...)"` attributes. The early attribute termination caused text like `> _Tactical Pivot...` and button CSS to spill out onto the screen as a broken, horizontal box.
  - Replaced inline string interpolation with a safe, in-memory `window._ragChunkStore` map keyed by clean alphanumeric chunk IDs (`${evt.id}-${cIdx}`).
  - Updated `openChunkVaultNote('${chunkKey}')` and `toggleChunkExpand('${chunkKey}')` to resolve source paths and preview snippets directly from `_ragChunkStore`.
  - Updated `openFeedbackCommentModal` to safely look up comments and ratings from loaded data rather than passing arbitrary user text through inline HTML attributes.
  - Hardened `escapeHtml` to encode double and single quotes (`&quot;`, `&#39;`) and declared `const escapeAttr = escapeHtml`.
- **CSS Blockquote & Monospace Overflow Protection (`evelyn_ui/dev.html`, `evelyn_ui/index.html`)**:
  - Added global `blockquote`, `.modal-body blockquote`, and `.card-body blockquote` overflow containment (`max-width: 100%`, `overflow-wrap: break-word`, `word-break: break-word`, `box-sizing: border-box`).
  - Added defensive word-break and overflow wrapping (`overflow-wrap: anywhere`, `word-break: break-word`) to `chunk-prev`, `chunk-full`, document highlight blocks in `formatChunkHighlightInDoc`, and feedback comment note containers.

## [000.006.115] - 2026-09-12 — *Multi-Round Tool Thoroughness & Streaming Parallel Tool Accumulation*

### Added & Hardened
- **Streaming Parallel Tool Call Accumulation (`evelyn_server.py`)**:
  - Resolved parallel tool call dropping in streaming mode: Ollama delivers parallel tool calls in separate streaming chunks with distinct `id` and `function.index`. Tool calls are now safely accumulated across incoming chunks without overwriting previously parsed calls.
- **De-Pressurized Intermediate Tool Synthesis Directives (`evelyn_server.py`)**:
  - Eliminated premature response closure momentum by rephrasing the intermediate `<tool_synthesis>` XML directive.
  - Intermediate rounds ($1 \dots N-1$) now explicitly instruct the model to inspect all requested files, notes, or queries before answering, instructing it not to rush or guess unread material.
  - Terminal rounds enforce strict synthesis only when all rounds or requested tools are finished.

## [000.006.114] - 2026-09-12 — *Dynamic Tool Scratchpad, Token Headroom & Synthesis Re-Anchoring*

### Added & Hardened
- **Dynamic Hardware-Aware Tool Headroom & Scratchpad Compaction (`evelyn_server.py`)**:
  - Dynamically calculates permissible tool return budget per turn based on `NUM_CTX`, active baseline conversation tokens, generation reserve (`NUM_PREDICT`), and `TOOL_RETURN_RATIO` (35% default).
  - Implemented multi-round tool return scratchpad compaction: older intermediate tool returns (rounds $1 \dots N-1$) exceeding 300 tokens are gracefully compacted to ~250 tokens, keeping the freshest round $N$ uncompressed.
  - Added tool argument logging to `messages.tool_metadata` (`meta_entry["args"] = fn_args`) for full auditing in the database.
  - Added semantic XML synthesis re-anchoring directive (`<tool_synthesis>`) before subsequent/terminal rounds to preserve persona stability and ensure direct answers to user prompts without context drift.
- **Clean Markdown Reading & Pagination (`Evelyn/tools/terminal_agent.py`)**:
  - Stripped redundant line-number prefixes (`   1 | `) by default from `read_file()` to save tokens and prevent markdown syntax breakage (preserving optional inspection via `show_line_numbers=True`).
  - Added 1-indexed pagination via `offset_line: int = 1` and configurable line ceiling `max_lines: int = 100` alongside `max_chars: int = 5000`.
  - Added automatic document section outline (`extract_markdown_outline`) appended to truncation notices, allowing the model to target specific sections on subsequent paginated reads.
  - Added clear human- and model-readable pagination banner (`Showing lines X–Y of Z, W chars`).
  - Updated `MODEL_TOOL_DEFINITIONS` tool schema for `read_file` to expose `offset_line` and `max_lines`.
- **Single Source of Truth Token Estimation Utilities (`Evelyn/tools/string_utils.py`)**:
  - Added `estimate_tokens(text: str)` implementing conservative token estimation (`len(text) / 2.5 + 4`) tailored for technical markdown, JSON, and tabular data.
  - Added `truncate_to_token_budget(text: str, max_tokens: int, truncation_suffix: str)` for safe token-aware text bounding.
  - Added `extract_markdown_outline(content: str)` for extracting heading hierarchies from truncated documents.
  - Refactored `_estimate_message_tokens` in `evelyn_server.py` to use canonical `estimate_tokens()`.
- **Configuration Tuning (`evelyn_config.py`)**:
  - Increased `MAX_TOOL_ROUNDS` from 5 to 10 rounds to support iterative document pagination and research workflows.
  - Added `TOOL_RETURN_RATIO = 0.35`, `READ_FILE_MAX_CHARS = 5000`, and `READ_FILE_MAX_LINES = 100`.

## [000.006.113] - 2026-09-12 — *Smart Vault Auto-Resolution & Vault Note Search*

### Added & Hardened
- **Tiered Vault Auto-Resolution in `read_file` (`Evelyn/tools/terminal_agent.py`)**:
  - Implemented `find_matching_vault_files()` providing a 4-tier resolution hierarchy: direct path, `.md` extension, SQLite `vault_documents` basename search, and filtered filesystem walk.
  - Added synthetic directory prefix stripping: when the model speculates directory paths (e.g. `Notes/Work/GIS Technician Tasks Overview.md`), extracts the bare stem while using directory tokens only if genuine candidates match.
  - Added deterministic multi-candidate ambiguity reporting: when multiple identical basenames exist across distinct folders (e.g. `001 - Preface.md`), returns an explicit formatted list of candidate relative paths rather than guessing.
  - Added strict vault path boundary confinement (`os.path.commonpath([cand_vault, vault_base]) == vault_base`) and wildcard/punctuation escaping (`ESCAPE '\\'`) for SQL queries.
  - Added close-match fallback suggestions in `read_file` via `vault_db.search_documents` when a requested file does not exist.
- **Enhanced Vault Document Index Scoring (`Evelyn/tools/vault_db.py`)**:
  - Upgraded `search_documents()` to score relative paths alongside title, tags, and gist snippet.
  - Added multi-term token scoring to gracefully discover notes with complex punctuation or multi-word titles (e.g. `GIS Technician Responsibility Mapping`).
- **New Specialist Tool: `search_vault_notes` (`Evelyn/tools/evelyn_tools.py`)**:
  - Implemented `search_vault_notes(query: str, limit: int = 5)` for explicit note and document discovery across the Obsidian Vault.
  - Registered in `MODEL_TOOL_DEFINITIONS`, `TOOL_FUNCTIONS`, and `TOOL_THINK_EFFORT` (`"low"`).
  - Added intent regex patterns in `evelyn_config.py:SPECIALIST_TOOL_INTENT_PATTERNS` to dynamically activate `search_vault_notes` when the user asks to find, locate, or list notes.
- **Starter Procedure Registration (Rule 10 Mandate & Migration `000.006.113`)**:
  - Registered `starter_procedure_search_vault_notes` in `Evelyn/tools/db_migrator.py` under version `000.006.113`.
  - Applied migration to `data/evelyn_memory.db` coupling `search_vault_notes` and `read_file` with trigger patterns, execution steps, pitfalls, and verification criteria.

## [000.006.112] - 2026-09-12 — *Dynamic Specialist Tool Surfacing & Thinking Preservation*

### Added & Hardened
- **Comprehensive Specialist Tool Intent Overhaul (`evelyn_config.py:SPECIALIST_TOOL_INTENT_PATTERNS`)**:
  - Eliminated rigid `\s+` single-space barriers across 16+ specialist tools, adding flexible determiner and modifier groups `(?:the\s+|a\s+|an\s+|this\s+|that\s+|these\s+|those\s+|my\s+|our\s+)?`.
  - Expanded `read_file` to support natural determiners, idioms (`give (them|it|this) a read`, `read through`, `look over`, `take a look at`, `check out`), synonyms (`document(s)`, `doc(s)`, `note(s)`, `entry/entries`, `sheet(s)`, `page(s)`, `log(s)`), and file extensions/paths (`\b[\w\-./]+\.(?:md|txt|py|json|csv|log|ya?ml|pdf|sh|html)\b`, `vault notes?`).
  - Expanded `write_file` to support natural determiners, prepositional targets (`save ... to a file/report/vault`), and extension patterns.
  - Added intent patterns for previously orphaned `search_reference_library` covering user manuals, appliance specifications, HVAC/appliance guides, and troubleshooting documentation.
  - Converted `write_dream_entry` into bidirectional phrasing (`log my dream`, `journal about a dream` as well as `dream ... journal`).
  - Broadened coverage for `create_task`, `complete_task` (`mark ... done`), `delete_task`, `delete_calendar_event`, `sync_google_calendar`, `sync_google_tasks`, `sync_google_drive`, `start_research`, `list_research_tasks`, `inspect_research_task`, `guide_research`, `search_history`, and `get_recent_workouts`.
- **Anaphoric & Multi-Turn Context Resolution (`Evelyn/tools/evelyn_tools.py:get_active_tools`)**:
  - Added `recent_history: list[dict] | None = None` parameter to `get_active_tools()`.
  - Implemented guarded anaphoric trigger detection (`_ANAPHORIC_TRIGGERS`: pronouns `them`, `it`, `those`, `these`, `that` or concise affirmations `go ahead`, `sure`, `yes`, etc.).
  - Protected against tool inflation and assistant hallucination loops by strictly scanning only prior *user* turns (ignoring assistant negative prose like *"I don't have access to your calendar"*).
  - Wired `recent_history=history[-4:]` in `evelyn_server.py:1859`.
- **Diagnostic Thinking Preservation on Stalled/Empty Responses (`evelyn_server.py:1927`)**:
  - Fixed database state loss where empty model content overwrote `thinking` with `NULL`.
  - Updated empty response fallback in `_run_chat_stream` to pass `thinking=thinking_buf.strip() if thinking_buf.strip() else None`, `tools_used=tools_str`, and `tool_metadata=tools_meta_str` to `update_message()`.
- **Unit Test Coverage (`Evelyn/tests/test_dynamic_tools_and_direct_rag.py`)**:
  - Added `test_specialist_tools_intent_tolerances` testing determiners, idioms, and extensions across `read_file`, `write_file`, `search_reference_library`, and `write_dream_entry`.
  - Added `test_anaphoric_multi_turn_tool_surfacing` validating multi-turn tool activation across sequential conversational turns.

## [000.006.111] - 2026-09-12 — *Chroma Staging Queue Pruning & Tag Delta Indexing*

### Added & Enhanced
- **Chroma Staging Queue Retention & Self-Healing (`Evelyn/tools/chroma_rag.py`)**:
  - Added `prune_completed_sync_queue()` to automatically prune completed (`status = 'done'`) records older than retention cutoff (keeping the most recent 500 records and deleting older records) and purge historical resolved error poison pills.
  - Added `recover_stale_processing_items()` to automatically reset stranded `status = 'processing'` records back to `'pending'` on server startup or worker recovery.
  - Integrated lazy retention pruning into `_chroma_queue_drain_loop` in `evelyn_server.py`, executing cleanly during queue idle periods.
  - Added unit test suite coverage (`test_08_recover_stale_processing_items`, `test_09_prune_completed_sync_queue`) in `Evelyn/tests/test_chroma_queue_and_lifecycle.py`.
- **Decoupled Master Librarian Taxonomy Rebalancing (`evelyn_server.py`, `scripts/master_librarian.py`)**:
  - Decoupled `--rebalance-taxonomy` from routine 5-minute Master Librarian idle audit passes (`run_master_librarian_task`), confining routine idle curation strictly to note-level link/tag audits (`--limit 5`).
  - Added explicit `rebalance_taxonomy: bool = False` flag to `run_master_librarian_task()` and `POST /api/librarian/run`.
  - Removed redundant duplicate call to `sync_master_tags_to_vector_db()` from `scripts/master_librarian.py`.
- **Master Tag Taxonomy Delta Indexing (`Evelyn/tools/tag_librarian.py`)**:
  - Refactored `maintain_master_taxonomy()` to delta-index only newly added or modified tags (`tags_to_update`) into Chroma via `index_master_tag_in_chroma()`, eliminating indiscriminate full-taxonomy re-enqueues.
- **Database Hygiene & Compaction (`data/evelyn_memory.db`)**:
  - Atomically purged 2,348,000+ obsolete historical completed records from `chroma_sync_queue`.
  - Rebuilt indexes and executed `VACUUM;` on `evelyn_memory.db`, reducing database size from **1.34 GB down to 59 MB** (reclaiming ~1.28 GB of disk space).

## [000.006.110] - 2026-09-12 — *Authoritative Factoid Ledger Architecture*

### Added & Enhanced
- **Authoritative Factoid Ledger Architecture (`Evelyn/tools/profile_ledger.py`)**:
  - Implemented the two-layer persona state architecture decoupling the authoritative inventory of facts (`*_facts.md`) with explicit `[Tier 1]`, `[Tier 2]`, and `[Tier 3]` rankings from the compiled/synthesized presentation layer (`*.md`).
  - Added discrete `LedgerItem` dataclass, markdown ledger parser (`parse_ledger`), serializer (`render_ledger`), clean presentation compiler (`compile_clean_markdown`), structured delta application (`apply_ledger_delta`), and deterministic budget pruning (`prune_ledger_to_budget`).
  - Added unit test suite `Evelyn/tests/test_profile_ledger.py` with 7 passing tests validating round-trip parsing, clean compilation without tier markers, atomic delta application, budget pruning order, and JSON delta extraction.
  - Created baseline authoritatively ranked ledgers in `Evelyn/persona/`: `Assistant_Profile_facts.md` (23 bullets, Tier 1/2), `User_Profile_facts.md` (37 bullets, Tier 1/2/3), and `System_Directives_facts.md` (37 bullets, Tier 1/2/3).
- **Profile Evolver State Machine Refactoring (`Evelyn/tools/profile_evolver.py`)**:
  - Replaced unconstrained generative full-body rewriting loops in `_evolve_document()` with atomic delta evaluations against the authoritative ledger.
  - Added `_parse_json_delta()` helper with resilient extraction for fenced/unfenced JSON responses.
  - Updated draft cursor mechanism (`_draft_path`) to persist in-progress working ledgers (`evelyn_evolution_draft_{name}_facts.md`), allowing interrupted runs to resume without state loss.
  - Added deterministic pre-synthesis word budget pruning via `prune_ledger_to_budget()`, eliminating `ABORTED_OVER_BUDGET` circuit breaker aborts by pruning lower-priority Tier 3 and Tier 2 items before presentation generation.
  - Established clean presentation layer compilation: deterministic 1:1 format compile for `User_Profile.md` and `System_Directives.md` with tier markers stripped, and structured first-person narrative synthesis for `Assistant_Profile.md`.
  - Staged proposals with structured JSON reasons packaging summary text, itemized added/modified/removed lists, and complete candidate ledger text.
- **Server Ledger Persistence (`evelyn_server.py`)**:
  - Updated `/api/review/proposals/{id}/approve` endpoint for `profile_update` to extract `candidate_ledger` from the proposal's structured JSON reason and write it to `PERSONA_DIR / profile_ledger.get_ledger_filename(target_filename)`.
  - Automatically synchronizes timestamps and invokes `scripts/update_frontmatter.py` on both the published presentation document and its corresponding authoritative fact ledger.
- **Triage Queue UI Ledger Badges (`evelyn_ui/dev.html`)**:
  - Added `parseProposalReason()` and `renderReasonBadges()` to safely parse structured or legacy plain-text proposal reasons.
  - Rendered green `+Added`, blue `~Modified`, and red `-Removed` badges with an expandable itemized details dropdown on `profile_update` triage cards.

## [000.006.109] - 2026-09-12 — *Section-Aware Item-Level Diff View Overhaul*

### Added & Enhanced
- **Section-Aware & Item-Level Diff Comparison Engine (`evelyn_ui/dev.html`)**:
  - Overhauled proposal diff rendering from a linear sequential line scanner into a hierarchical, section-aware comparison engine.
  - Aligns markdown sections by canonical heading (`## Header`) to strictly prevent edits or shifts in one section from cascading or bleeding into adjacent sections.
  - Implemented bullet-level item matching (`diffBulletItems`) with key extraction and similarity pairing to detect reordering (`🔄 REORDERED`) without falsely triggering entire-section deletions and additions.
  - Implemented sentence-level prose matching (`diffProseSection`) for narrative documents (`Assistant_Profile.md`), pairing corresponding sentences and isolating word-level diffs within continuous prose.
  - Added smart section collapsing: completely unchanged sections collapse by default (`✓ Unchanged (N items)`), reducing reviewer cognitive load and focusing attention on active edits.
  - Added live diff toolbar with aggregate summary badges (`+added`, `~modified`, `-removed`, `🔄 reordered`) and instant mode toggle between `Smart View` and classic `Raw Unified`.
  - Added compact YAML frontmatter metadata handling, summarizing routine `date modified` timestamp updates without consuming vertical diff space.

## [000.006.108] - 2026-09-12 — *Sampling Repetition Window & Penalty Calibration*

### Added & Enhanced
- **Look-Back Repetition Window Calibration (`evelyn_config.py`)**:
  - Expanded `REPEAT_LAST_N` from `96` to `512` tokens to adequately cover full ~500-word conversational responses (~650–700 tokens) and recent context turns, preventing token-level decay and repeated phrasal patterns across multi-paragraph outputs.
  - Calibrated `REPEAT_PENALTY` from `1.12` to `1.15` to deter recurring stylistic clichés, repetitive stage directions, and formulaic closing cadences without degrading lexical coherence or punctuation.
- **Review Queue UI Context Expansion Persistence (`evelyn_ui/dev.html`)**:
  - Added `expandedProposalContexts` tracking set and `ontoggle` listener to preserve open/closed state of supporting context details during unified item filtering and action refreshes.

## [000.006.107] - 2026-09-11 — *Proactive Rhythm Prompt Tuning & Deliberative Thinking Scratchpad*

### Added & Enhanced
- **Deliberative Thinking Scratchpad Directive (`evelyn_server.py`)**:
  - Injected standardized `<interaction_rhythm>` directive into `load_system_prompt()`, structuring the model's native `<think>` buffer across three stages: Direct Intent, Proactive Horizon (identifying adjacent friction points and unasked implications), and Adaptive Pacing.
  - Parameterized operator references with `{cfg.USER_NAME}` to adhere to Rule 4 identity standards.
- **Model Sampling Suite Optimization (`evelyn_config.py`)**:
  - Calibrated Ollama generation options for Gemma 4 native thinking: lowered `TEMPERATURE` to `0.70` (preventing high-entropy drift and corporate cheerleader tropes while retaining creative phrasing), adjusted `MIN_P` to `0.08` to cleanly truncate the noisy long-tail distribution, set `TOP_K` to `40`, and tuned `TOP_P` to `0.90`.
  - Expanded `REPEAT_LAST_N` to `96` and `REPEAT_PENALTY` to `1.12` to eliminate repetitive conversational endings and recurring boilerplate.
- **Persona Triad & Directives Refinement (`Evelyn/persona/`)**:
  - Refactored `System_Directives.md` under `## Conversation & Formatting` to replace the rigid 2–3 sentence clamp with flexible collaborative pacing and proactive partner engagement guidelines, while retaining low-energy and fatigue protection.
  - Harmonized `Assistant_Profile.md` under `## Voice & Communication` with continuous narrative prose capturing direct answers, operational edge-case follow-through, and brainstorming choices without violating structural narrative purity invariants.
- **Roadmap Architectural Planning (`ROADMAP.md`)**:
  - Added discrete roadmap capability item for `Profile Evolution Protected Sections` under Phase 3 (Agency & Tools) with configurable invariants (`PROTECTED_SECTIONS` in `evelyn_config.py`).
  - Integrated protected section management into the Phase 4 `Dynamic Configuration Manager (dev.html)` milestone.

## [000.006.106] - 2026-09-11 — *Syncthing Mesh Migration & Deployment Infrastructure Documentation*

### Added & Enhanced
- **Syncthing Mesh Migration to Alex-PC-WSL**:
  - Migrated the multi-device Obsidian Vault synchronization mesh from the decommissioned server (*Sanctum*) to **Alex-PC-WSL**.
  - Generated and deployed dedicated Syncthing configuration (`~/.local/state/syncthing/config.xml`) with WSL2 port separation: Web GUI on `0.0.0.0:8385` (eliminating port collisions with Windows SyncTrayzor on `8384`) and transfer listener on `0.0.0.0:22000` (TCP/QUIC).
  - Enabled persistent user lingering via `loginctl enable-linger` and activated `syncthing.service` systemd user unit.
  - Paired Windows Workstation (`Alex-PC`) directly with `Alex-PC-WSL` over Tailscale and internal vSwitch; successfully completed initial bidirectional handshake and synchronized all 4,707 vault files (1.44 GB).
- **Automated Obsidian Vault Watcher Service (`systemd/evelyn-vault-watcher.service`)**:
  - Authored and committed canonical user systemd service definition (`systemd/evelyn-vault-watcher.service`) to run `scripts/obsidian_vault_watcher.py` as an always-on background daemon.
  - Automatically watches `/home/rathius/obsidian_vault` with a 4.0s debounce cooldown, incrementally indexing file additions, modifications, and deletions into SQLite (`evelyn_vault.db`) and ChromaDB vector embeddings (`evelyn_memory` / staging).
- **Service Orchestration Script Enhancements (`scripts/`)**:
  - `start_evelyn_services.sh`: Added startup and status reporting for user services `syncthing` and `evelyn-vault-watcher`.
  - `stop_evelyn_services.sh`: Added `--with-syncthing` option and integrated syncthing shutdown into `--all`.
  - `restart_evelyn_services.sh`: Integrated automated restart for `syncthing` user service alongside `evelyn-vault-watcher`.
  - `check_evelyn_status.sh`: Added automated health probes for Syncthing (port 22000/8385) and Vault Watcher; updated system reporting banner from Sanctum to `Alex-PC-WSL`.
- **Comprehensive Deployment & Mesh Documentation (`SETUP_GUIDE.md`, `reference/engine_architecture.md`, `ROADMAP.md`)**:
  - Authored full Section 5 in `SETUP_GUIDE.md`: "Multi-Device Obsidian Sync (Syncthing Mesh over Tailscale)", detailing topology, WSL2 port separation, systemd lingering, vault watcher setup, Windows SyncTrayzor integration, mobile (Android) client configuration, and recommended `.stignore` rules.
  - Updated `reference/engine_architecture.md` diagram and topology notes to reflect `Alex-PC-WSL:22000`.
  - Updated `ROADMAP.md` Phase 4 milestones tracking deployment and infrastructure documentation progress.

## [000.006.105] - 2026-09-11 — *Dual-Collection Vector Architecture & Reference Library Tooling*

### Added & Enhanced
- **Dual-Collection Vector Architecture (`evelyn_config.py`, `Evelyn/tools/chroma_rag.py`)**:
  - Introduced `cfg.CHROMA_REFERENCE_COLLECTION = "evelyn_reference"` alongside `cfg.CHROMA_MEMORY_COLLECTION = "evelyn_memory"`.
  - Added `"Reference Library"` to `cfg.RAG_EXCLUDED_SUBDIRS`, completely isolating all 2,838 reference documents (manuals, guides, textbooks) from ambient in-flight conversational RAG.
  - Resolved narrative RAG dilution: ambient check-ins and conversation turns are guaranteed pure personal context (user facts, daily logs, personal notes), eliminating top-K context crowding from books like *Nonviolent Communication* and *The 5 Love Languages*.
- **Direct Reference Library Search Tool (`Evelyn/tools/evelyn_tools.py`)**:
  - Implemented `search_reference_library(query, limit, domain)` model-facing tool executing semantic vector queries directly against `evelyn_reference`.
  - Formats results with book/manual title, chapter title, relevance percentages, and clean markdown excerpts.
  - Added tool schema to `MODEL_TOOL_DEFINITIONS` and registered into `TOOL_FUNCTIONS`.
- **Zero Re-embedding Vector Migration (`scripts/migrate_reference_library_vectors.py`)**:
  - Migrated all 9,001 reference library chunks from `evelyn_memory` into `evelyn_reference` while preserving precomputed vector embeddings on disk.
  - Pruned reference chunks from `evelyn_memory`, reducing collection size from 21,982 to 12,981 pure personal memory chunks.
  - Initialized `reference_sync_state.json` to enable instantaneous mtime/hash incremental checking across subsequent passes.
- **Dual-Collection Incremental Ingestion (`Evelyn/tools/ingest_obsidian_knowledge.py`)**:
  - Upgraded ingestion engine with `sync_reference_collection()` alongside `sync_memory_collection()`.
  - Integrated into `main()`, ensuring background idle daemons (`refresh_memory`) and filesystem watchers (`obsidian_vault_watcher.py`) keep both vector collections in sync automatically.
- **Starter Procedure Migration (`Evelyn/tools/db_migrator.py`)**:
  - Registered migration `000.006.105` for database `memory`, inserting the operational starter procedure for `search_reference_library` with natural trigger patterns, execution steps, pitfalls, verification criteria, and suggested tool links.
- **Dashboard & Server Telemetry (`evelyn_server.py`, `evelyn_ui/dev.html`)**:
  - Updated `/api/tasks` in `evelyn_server.py` to inspect both `evelyn_memory` and `evelyn_reference` collections.
  - Enhanced Dev Dashboard (`dev.html`) task cards (`sync` and `refresh_memory`) to report both personal knowledge and reference library vector counts without visual vector loss.
- **Unit Testing (`Evelyn/tests/test_reference_library_tool.py`)**:
  - Added comprehensive test suite verifying source exclusion, metadata filtering, error handling, clean result formatting, and domain keyword filtering.

## [000.006.104] - 2026-09-11 — *Profile Evolver 3-Tier Priority Scoring & Deterministic Bullet Pruning*

### Added & Enhanced
- **3-Tier Priority Bullet Scoring (`Evelyn/tools/profile_evolver.py`)**:
  - Implemented `score_bullet_tier(filename, section, bullet_text)` classifying structured document bullets into the 3-Tier Priority Framework:
    - **Tier 1 (Score 3 - Core Invariants & Hard Boundaries)**: Health conditions, respiratory/allergy needs, pain/migraine management, fatigue limits, recovery needs, sleep deficits, and core relational foundation.
    - **Tier 2 (Score 2 - Active Context & Recurring Habits)**: Technical domains, systems engineering, AI architectures, workflow automation, and task batching.
    - **Tier 3 (Score 1 - Ephemeral Details & Secondary Preferences)**: Casual routines, transitional habits, caffeine tracking, lifestyle choices, and secondary personal details.
- **Deterministic Tier-Aware Bullet Pruning (`Evelyn/tools/profile_evolver.py`)**:
  - Upgraded `prune_bullets_to_word_budget()` from naive positional slicing to deterministic tier-aware pruning.
  - Progressively prunes Tier 3 ephemeral bullets first across sections down to structural minimums, then Tier 2 active context if needed, strictly protecting Tier 1 core invariants from being pruned.
  - Automatically balances section trimming by targeting sections with the highest bullet counts and largest word counts, preserving all canonical section headers and maintaining relative bullet presentation order.
- **Hardening Tests (`Evelyn/tests/test_profile_evolver_hardening.py`)**:
  - Added unit tests for `score_bullet_tier` classification across tiers and documents.
  - Added deterministic tier-aware pruning tests verifying that Tier 1 health and relational invariants survive pruning while Tier 3 ephemeral routines are pruned to satisfy word budgets.

## [000.006.103] - 2026-09-11 — *Dev UI Edit Refresh, Profile Evolver Backlog Capping & Compaction Circuit Breaker*

### Fixed & Enhanced
- **Dev UI Review Queue Edit Refresh (`evelyn_ui/dev.html`)**:
  - Fixed issue in `handleAction()` where saving edits on extractions (`action === 'edit'`) left the entry active on screen until a full manual browser reload.
  - Since editing an extraction promotes it to `status='live'` (approved) on the backend, the client now removes it from `unifiedItems`, updates triage counters, and re-renders the queue immediately.
  - Updated proposal editing in `handleAction()` to update `item.merged_observation` and trigger in-place re-rendering without reload.
- **Profile Evolver Backlog Entry Capping (`evelyn_config.py`, `Evelyn/tools/profile_evolver.py`)**:
  - Introduced `PROFILE_EVOLUTION_MAX_ENTRIES_PER_RUN = 30` (empirically calibrated against conversation date generation metrics of ~25 median facts/day), preventing backlogs of 400+ un-evolved facts from swamping 15+ chained passes in a single run.
  - Sorted `changed_entries` chronologically (oldest-first) so historical backlogs drain sequentially across evolution runs.
- **Profile Evolver Prompt Salience Directives (`Evelyn/tools/profile_evolver.py`)**:
  - Added strict non-inclusion instructions preventing the model from creating bullet points for every single fact; directed transient, code-level, and episodic observations to remain exclusively in RAG memory while keeping profile documents focused on high-level behavioral invariants.
- **Multi-Round Compaction & Deterministic Bullet Pruning (`Evelyn/tools/profile_evolver.py`)**:
  - Added a multi-round compaction loop: if Round 1 compaction leaves the document over budget, an aggressive Round 2 pass is invoked with strict bullet-count limits (5–7 bullets per section).
  - Implemented `prune_bullets_to_word_budget()` deterministic fallback for structured documents (`User_Profile.md`, `System_Directives.md`) that progressively caps bullets per section while preserving all canonical section headers.
  - Sanitized `repair_missing_sections()` to clean and extract valid bullet lines when accidental prose lines are present, preventing accidental restoration of dozens of uncompacted baseline bullets.
- **Runaway Proposal Circuit Breaker (`Evelyn/tools/profile_evolver.py`)**:
  - Implemented a hard safety gate blocking proposal staging if `final_word_count > target_limit * 1.10`. Aborts staging with status `ABORTED_OVER_BUDGET` and logs a warning instead of writing runaway over-length proposals to SQLite.
- **Automated Tests (`Evelyn/tests/test_profile_evolver_hardening.py`)**:
  - Added test suite covering config constant verification, deterministic bullet pruning, `repair_missing_sections` bullet cleaning, and the runaway proposal circuit-breaker gate (100% passing).

## [000.006.102] - 2026-09-10 — *Chatterbox CPU Mode, Voice Conditionals Caching & Setup Guide Roadmap Notice*

### Added & Enhanced
- **Chatterbox TTS CPU Mode & Isolated Service Deployment (`services/tts/tts_server.py`, `services/tts/evelyn-tts.service`)**:
  - Restored Chatterbox Turbo TTS module running permanently on CPU (`EVELYN_TTS_DEVICE=cpu`) with dedicated systemd unit `evelyn-tts.service`.
  - Eliminated Ollama VRAM eviction and reload cycles, keeping Gemma 4 100% resident in VRAM and avoiding 20-30s model swap delays.
- **Reference Voice Conditionals Caching (`services/tts/tts_server.py`)**:
  - Implemented pre-computation and in-memory caching of reference speaker embeddings during model initialization (`prepare_conditionals`), eliminating ~4.3s of `librosa` audio resampling overhead per chunk.
  - Added dynamic cached conditional reuse across streaming chunks while preserving runtime voice override capabilities.
- **Configurable Sentence Chunking (`services/tts/tts_server.py`, `reference/endpoints.md`)**:
  - Parameterized `CHUNK_SENTENCES` via `EVELYN_TTS_CHUNK_SENTENCES` (default 3) to maintain natural prosody and prevent client audio playback buffer starvation on CPU.
- **Documentation & Infrastructure Notice (`README.md`, `ROADMAP.md`)**:
  - Added prominent setup guide revision warning callout in `README.md` referencing Phase 4 roadmap milestone for comprehensive deployment, networking, and SSL setup documentation.

## [000.006.101] - 2026-09-08 — *Image URL Trailing Punctuation Stripping & Modal Preview Fix*

### Fixed & Enhanced
- **Image URL Extraction Trailing Punctuation Pruning (`evelyn_server.py`)**:
  - Fixed regex extraction in `generate_image` tool handler where the terminal period from `"Image generated successfully at /images/...png."` was greedily captured into `tool_entry`, `tool_metadata`, and `approval_id_or_data`.
  - Added `.rstrip(".,;")` sanitization ensuring image paths resolve cleanly as `/images/<filename>.png` without trailing punctuation that caused 404 HTTP errors.
- **Frontend Image URL Sanitization & Action Labeling (`evelyn_ui/index.html`)**:
  - Added defensive URL stripping (`replace(/[.,;]+$/, "")`) in `addWriteBadges()` and `openModal()` to ensure generated image preview modals always load the asset without 404 failures.
  - Enhanced the write badge label for `generate_image` from a static `"🎨 Image generated"` to an actionable, clickable `"🎨 View Generated Image"` button with descriptive tooltip.
  - Improved modal image presentation with responsive constraints (`max-width: 100%; max-height: 80vh; border-radius: 8px`).
- **Database Curation & Historical Record Healing (`evelyn_chat.db`)**:
  - Sanitized historical records across 5 chat messages (IDs `26805`, `27309`, `27906`, `29291`, `31470`) in `evelyn_chat.db`, stripping the invalid trailing period from `tools_used` and `tool_metadata` so previous image generation badges open properly in the UI.

## [000.006.100] - 2026-09-08 — *PDF De-Hyphenation, Ingestion Noise Filtering & Manual Note Title Normalization*

### Added & Enhanced
- **Line-Break De-Hyphenation & Checklist Symbol Stripping (`Evelyn/tools/string_utils.py`, `scripts/extract_pdf_library.py`)**:
  - Enhanced `clean_title()` and `clean_heading_text()` with automatic line-break de-hyphenation (`Wa- ter` $\rightarrow$ `Water`, `Tempera- ture` $\rightarrow$ `Temperature`), leading checklist mark and bullet stripping (`✓`, `✔`, `•`), and trailing hyphen pruning.
  - Enhanced `is_valid_title_candidate()` in `extract_pdf_library.py` to reject trailing hyphens, merged sentence fragments ending in periods followed by capitalized text, continuation verbs (`has`, `is`, `was`, `were`), and strings composed solely of diagram callout numbers and punctuation (e.g. `2 3 1 4`).
- **Owner's Manual Note Healing & TOC Database Synchronization (`evelyn_vault.db`)**:
  - Repaired 5 misnamed and fragmented notes in `AOSmith G9-T4040NVR 400`:
    - `18 - ✓Water pressure.md` $\rightarrow `18 - Water Pressure Requirements.md` (rejoined sentence continuation).
    - `19 - has a built-in bypass. ✓Wa- ter pressure in- crease caused by ther-.md` $\rightarrow `19 - Thermal Expansion Tank.md` (restored semantic topic title and clean body text).
    - `27 - Step 5.md` $\rightarrow `27 - Step 5 Air Filter Installation.md`.
    - `29 - Connect the Tempera- ture and Pressure (T&P) Relief ValvePipe.md` $\rightarrow `29 - Connect the Temperature and Pressure (T&P) Relief Valve and Pipe.md`.
    - `42 - Insuffi cient Hot Water or Slow Hot Water Recovery.md` $\rightarrow `42 - Insufficient Hot Water or Slow Hot Water Recovery.md`.
  - Repaired 3 diagram callout notes in `AOC Q27G40XMN`:
    - `07 - .md` $\rightarrow `07 - Setup Stand & Base.md`.
    - `09 - 2 3 1 4.md` $\rightarrow `09 - Connect to PC & Ports.md`.
    - `13 - 1 4 2 3 5.md` $\rightarrow `13 - Control Buttons & Navigation.md`.
  - Updated index sidecar TOC tables (`AOSmith G9-T4040NVR 400_index.md`, `AOC Q27G40XMN_index.md`) and atomically synchronized note paths and titles in `evelyn_vault.db`.

## [000.006.099] - 2026-09-08 — *Multi-Discipline Notation Leak Protection & Vault Title Healing*

### Added & Enhanced
- **Multi-Discipline Notation Detection Engine (`Evelyn/tools/string_utils.py`)**:
  - Implemented `detect_notation_discipline()` and `is_notation_leak()` to protect note titles and document names against technical notation leaks across musical glyphs (stave fonts, note names `œ`, `˙`, rests `Ó`, `Œ`, `‰`, `sharp`/`flat`), LaTeX commands/delimiters (`\frac`, `\sum`, `\int`, `$$`, `\begin`), dense mathematical operator clusters, chemical reaction formulas, and formatting artifacts (pipe table delimiters, horizontal rules).
  - Built contextual false-positive safeguards preventing normal prose currency expressions (`$50`), procedural step arrows (`Step 1 -> Step 2`), and standard engineering tolerances (`±5%`) from triggering false positives.
  - Enhanced `clean_title()` to strip residual `.md`/`.pdf` file extensions and normalize titles cleanly.
- **PDF Extraction Ingestion Noise Filtering (`scripts/extract_pdf_library.py`)**:
  - Added `TEMPO_MARKINGS`, `INSTRUMENTATION_HEADERS`, and `is_valid_title_candidate()` guardrails to block music notation, tempo markings (`Allegro`, `Andante`, `Moderato`), instrumentation headers (`Cello`, `Violin`), and PDF rendering artifacts from becoming note titles or section filenames.
- **Format Librarian Title Healing & Master Librarian Audit (`Evelyn/tools/format_librarian.py`, `master_librarian.py`, `vault_db.py`)**:
  - Enhanced `format_librarian.py` with idempotent title-only audit scope: automatically detects notation-leaked frontmatter `title:` and top markdown `# Heading`, recovers semantic titles from internal section headings (e.g. `### The Open Strings`), converts ALL-CAPS titles to Title Case, and aligns markdown headers.
  - Updated `master_librarian.py` to log healed clean titles in activity logs and synchronize `vault_documents.title` in `evelyn_vault.db` via `vault_db.update_document_librarian_audit()`.
- **Musical Vault Note Normalization & Wikilink Refactoring (`scripts/repair_musical_vault_notes.py`)**:
  - Created migration CLI tool repairing 53 musical notes across `Reference Library/Learning Cello/Cello Method/` and `Cello First Lessons/`.
  - Renamed corrupted notation-leaked files on disk to clean, semantic titles, preserved legacy notation names in `aliases: [...]` frontmatter to prevent link breakage, refactored global vault wikilinks and table index rows (`Cello Method_index.md`, `Cello First Lessons_index.md`), and atomically synchronized paths in `evelyn_vault.db`.
- **Hermetic Unit Test Suite (`Evelyn/tests/test_notation_detection.py`, `test_master_librarian.py`)**:
  - Added 7 comprehensive test suites validating multi-discipline notation detection, boundary conditions, and false-positive guards.
  - Added `test_notation_title_healing_and_idempotency` verifying complete two-pass idempotency (0 modifications on re-run) in the Master Librarian test suite.

## [000.006.098] - 2026-09-07 — *RAG Telemetry Test Isolation Safeguard*

### Fixed & Enhanced
- **RAG Telemetry Hermetic Test Isolation (`Evelyn/tests/test_category_attribution.py`)**:
  - Identified and patched unmocked `log_rag_retrieval` call inside `test_rag_category_attribution_enrichment`, which previously leaked `"engineering background"` telemetry events directly into the production `rag_retrieval_log` table on each test execution.
  - Purged all legacy test artifact rows with `query = 'engineering background'` from production `evelyn_memory.db` and truncated WAL buffers.
  - Verified hermetic test execution: all unit tests run with full isolation without side effects on active production telemetry logs.

## [000.006.097] - 2026-09-07 — *Live Stream Thinking Progress & Status Feedback*

### Fixed & Enhanced
- **Real-Time Thinking Status & Live Word Count Telemetry (`evelyn_ui/index.html`)**:
  - Resolved UI freeze symptom where `#status-text` remained permanently locked on `"Querying model..."` while the model was streaming intermediate thinking deltas into a collapsed trace drawer.
  - Added dynamic status updates in `handleStreamEvent` to transition `#status-text` to `Thinking… (<count> words)` in real time on incoming thinking deltas, seamlessly flipping to `Responding…` / `Streaming…` as soon as response text begins.
  - Added real-time token/word counter and elapsed timer to the collapsed trace summary (`.trace-summary-meta`), displaying live word count and seconds (e.g. `245 words • 18s`) as thinking accumulates.
- **Visual Thinking Activity Indicator (`evelyn_ui/index.html`)**:
  - Introduced `.trace-spinner` on `.agent-activity-trace summary` to provide unambiguous visual feedback that reasoning is actively executing even while the drawer is collapsed.
  - Automatically dismantles the spinner and finalizes title to `"Thought process & actions"` with full word count and latency metrics once response generation begins.
  - Enhanced historical message rendering in `renderActivityTraceBlock` to display word count alongside tool usage metadata.

## [000.006.096] - 2026-09-07 — *TTS VRAM Lifecycle & Ollama Prefetch Safeguard*

### Fixed & Enhanced
- **TTS VRAM Deallocation & Allocator Purge (`services/tts/tts_server.py`)**:
  - Implemented `_teardown_model_vram()` to explicitly dismantle child submodules (`t3`, `s3gen`, `ve`, `conds`, `watermarker`, `tokenizer`) before deleting the model instance, breaking cyclic object references.
  - Added explicit garbage collection (`gc.collect()`), CUDA cache eviction (`torch.cuda.empty_cache()`), and IPC cache collection (`torch.cuda.ipc_collect()`) to completely return reserved VRAM to the OS/NVIDIA driver.
  - Enhanced `generate_speech_stream()` to explicitly clear local generator frame references (`del model`) inside the `finally` block, ensuring no dangling references keep Chatterbox resident on the GPU.
- **VRAM Clearance Verification Gate & Model Allocation Audit (`services/tts/tts_server.py`)**:
  - Replaced arbitrary static delay in `_prefetch_ollama()` with an active polling gate (up to 6s) that guarantees PyTorch allocated and reserved VRAM drop to idle baselines before requesting Ollama model reload.
  - Added a post-reload sanity check against Ollama's `/api/ps` endpoint to verify that `gemma4:12b` successfully loaded with 100% GPU allocation, logging prominent warnings if partial CPU offload is detected.

## [000.006.095] - 2026-09-07 — *Default Thought Process & Actions Collapsed State*

### Changed & Enhanced
- **Collapsible Activity Trace Defaulting (`evelyn_ui/index.html`)**:
  - Configured the streaming and historical "Thought process & actions" (`.agent-activity-trace`) section to default to collapsed on turn creation (`traceEl.open = false`) rather than auto-expanding during generation.
  - Preserved turn-completion collapse enforcement in `finalize()`, ensuring the trace remains cleanly collapsed after each message while allowing manual expansion on demand to inspect reasoning rounds and executed tools.
  - Added thinking handler finalization to `reconcileStreamFailure` on stream error to prevent orphaned unfinalized trace state.

## [000.006.094] - 2026-09-07 — *Procedure Card Two-Tier Semantic Action Rows & Split Merge Dropdown*

### Added & Enhanced
- **Two-Tier Semantic Action Layout (`evelyn_ui/dev.html`)**:
  - Replaced cramped single-line action bars on procedure review and merge cards with structured, left-aligned, two-tier action groups (`.card-actions-group` / `.card-actions-row`).
  - **Tier 1 (Constructive / Progression Actions)**: Houses primary actions (`💾 Save Changes`, `Approve (Live)`, and smart merge controls) with natural content width.
  - **Tier 2 (Triage / Destructive Actions)**: Left-aligned secondary tier cleanly separating terminal actions (`Reject`, `Archive`, `🗑️ Delete`) to eliminate visual crowding and misclicks without forcing excessive page scanning.
- **Context-Aware Smart Merge Split Button & Overflow Dropdown (`evelyn_ui/dev.html`)**:
  - Unified redundant merge actions into an adaptive control (`renderMergeControl`):
    - When a master candidate is detected (`item.merged_into_id` or `targetMasterId`), renders a segmented split button (`⚡ Merge into #<id>` | `▾`). Primary button executes immediate consolidation into the suggested master; dropdown arrow reveals overrides (`🔀 Merge into different procedure...` and `🔍 Preview Master #<id>`).
    - When no master candidate is detected, renders a clean single button (`🔀 Merge into...`) that opens the manual merge picker modal.
  - Added global click-outside and `Escape` key dismiss listeners for dropdown menus.

## [000.006.093] - 2026-09-07 — *Canonical Persona Triad Migration & Legacy Filename Deprecation*

### Deprecated & Removed
- **Legacy Persona Filenames Deprecation**:
  - Removed obsolete `Evelyn_Narrative_Persona.md` and `User_Narrative_Profile.md` file references across the entire engine in favor of the canonical, identity-agnostic persona triad: `Assistant_Profile.md`, `User_Profile.md`, and `System_Directives.md`.
  - Cleaned up leftover legacy fallback keys in `evelyn_ui/dev.html`'s `docIcons`.
  - Updated template deployment mapping in `evelyn_setup.py` and `SETUP_GUIDE.md` to reference `Assistant_Profile.example.md` -> `Assistant Profile.md` and `User Profile.md`.

### Fixed & Standardized
- **Test Suite Grounding & Hardcoded File Name Decoupling (`Evelyn/tests/test_category_attribution.py`, `Evelyn/tests/test_profile_section_invariants.py`)**:
  - Updated `test_category_attribution.py` to cluster entries using `cfg.PERSONA_FILE_USER` instead of legacy hardcoded filenames.
  - Updated test fixtures in `test_profile_section_invariants.py` from `# Assistant Narrative Persona` to `# Assistant Profile`.
- **Engine Docstrings & Internal Comments Alignment (`evelyn_server.py`, `Evelyn/tools/memory_db.py`, `Evelyn/tools/profile_evolver.py`)**:
  - Updated docstring argument examples in `memory_db.py` (`get_qualifying_entries`, `touch_entry_evolved`) and `profile_evolver.py` (`_draft_path`, `validate_profile_sections`, `_cluster_entries_by_theme`, `record_profile_proposal_resolution`, `_proofread_document`, `evolve_profile_document`) to canonical `User_Profile.md` and `Assistant_Profile.md`.
  - Updated `/api/review/proposals/{id}/approve` profile update inline comment in `evelyn_server.py`.
- **Documentation & Workflow Specifications Parity (`reference/endpoints.md`, `reference/engine_architecture.md`, `README.md`, `.agents/workflows/backup-to-github.md`)**:
  - Aligned API endpoints documentation for `GET /api/persona/{filename}` to document `Assistant_Profile.md` and `User_Profile.md`.
  - Updated architecture specs, persona section mappings, and starter template links in `engine_architecture.md` and `README.md`.
  - Updated safety warning in `.agents/workflows/backup-to-github.md` to guard `*_Profile.md` patterns.

## [000.006.092] - 2026-09-07 — *Profile Evolver State Reconciliation, Manual Trigger Integration & Dashboard Status Formatting*

### Fixed & Standardized
- **Ground-Truth Reconciliation in Profile Evolver (`Evelyn/tools/profile_evolver.py`)**:
  - Implemented automatic database reconciliation in `get_profile_evolution_statuses()` against SQLite `proposals` table. When state JSON indicates `PROPOSAL_STAGED` or `PENDING_EXISTS` but no pending proposal exists in the database, the status is self-healed to `APPROVED` (with the review timestamp) or `BELOW_THRESHOLD` depending on the latest resolution.
  - Hardened state persistence in `_save_evolution_state()` to merge in-memory updates with on-disk state using latest timestamps and maximum values, preventing long-running manual scripts or concurrent review actions from overwriting recent document statuses.
  - Normalized target filenames with `os.path.basename()` across all document status updates.
- **Manual Trigger Registry & Task Manager Synchronization (`scripts/trigger_profile_evolution.py`)**:
  - Integrated `task_manager.set_running("profile_evolver")` and `task_manager.clear_running("profile_evolver", status="idle")` with `task_manager.save_last_run_ts()`.
  - Reloaded fresh evolution state per target document in manual runs to ensure concurrent proposal approvals are never clobbered by stale in-memory state.
- **Server Heavy Tasks Endpoint Synchronization (`evelyn_server.py`)**:
  - Dynamically resolved `profile_evolver`'s `last_run_at` in the `/api/heavy_tasks` endpoint from the latest document timestamp in `doc_statuses`, ensuring the dashboard accurately reflects recent manual runs and proposal approvals.
- **Dev Dashboard Visual PKM Alignment (`evelyn_ui/dev.html`)**:
  - Added canonical file name mappings for `Assistant_Profile.md`, `User_Profile.md`, and `System_Directives.md` in `docIcons`.
  - Added `white-space: nowrap;` and `gap: 6px;` to document status rows to prevent awkward two-line text wrapping and status dot misalignment on narrow card containers.
- **Automated Verification Suite (`Evelyn/tests/test_profile_evolution_status.py`)**:
  - Added comprehensive test coverage for proposal state reconciliation, self-healing, and pending proposal detection.

## [000.006.091] - 2026-09-07 — *Tool-Schema Kwarg Deduplication, Procedure Merge Refinements & Interactive Manual Master Consolidation*

### Added & Standardized
- **Tool Schema & Kwarg Deduplication (`Evelyn/tools/procedure_matcher.py`)**:
  - Replaced ad-hoc hardcoded synonym tables with dynamic introspection of `MODEL_TOOL_DEFINITIONS` in `Evelyn/tools/evelyn_tools.py` as the canonical single source of truth for tool names, parameter kwargs (`mood`, `vibe_check`, `narrative`, `message_in_a_bottle`, `feelings`, `analysis`, etc.), and tool scopes.
  - Maintained a lean, focused colloquial overlay (`COLLOQUIAL_SYNONYMS`) solely for conversational natural speech variants (`bedtime`, `goodnight`, `winddown`, `closeout`, `downtime`, `nightmare`, `somnambulant`).
- **Specialized Tool Concordance & Master Recognition (`Evelyn/tools/procedure_matcher.py`, `Evelyn/tools/procedure_consolidator.py`)**:
  - Introduced differentiated tool concordance scoring in `calculate_procedure_similarity`: boosted concordance bonus to `+0.35` for specialized companion tools (`write_journal_entry`, `write_dream_entry`, `get_health_metrics`, `create_task`, etc.) to ensure procedures sharing a specialized single-purpose tool reliably meet the candidate threshold.
  - Enhanced `identify_cluster_master` with external master discovery (`all_live_procs`), enabling clusters of extracted procedures without existing lineage to properly resolve their canonical live master procedure.
  - Refined prompt directives in `procedure_consolidator.py` to preserve baseline master trigger patterns and retain companion tool assignments (`write_journal_entry` for evening wind-downs, `write_dream_entry` for dream logs/reports).
- **Interactive Manual "Merge into..." Workflow (`evelyn_ui/dev.html`)**:
  - Implemented dynamic fallback master detection for `procedure_merge` proposal cards when `suggested_category` is non-numeric, matching against live procedures in `allProcedures`.
  - Added interactive `🔀 Merge into...` action buttons to Procedure Merge proposals, Extracted Procedures, and the Procedures Management tab.
  - Built the Manual Merge Modal (`#manual-merge-modal`), sorting live master procedures first by matching proposed tool, then alphabetically by tool name, and then by ID, with visual `⚡ MATCHING TOOL` badges.
  - Implemented a two-stage confirmation dialog with Yes/No options: confirming applies the normal merge consolidation action and refreshes the queue, while canceling closes the prompt seamlessly.
- **Automated Verification Suite (`Evelyn/tests/test_procedure_matcher.py`)**:
  - Added unit tests validating dynamic schema kwarg extraction, external master cluster discovery, and matching of `write_journal_entry` and `write_dream_entry` phrasing variations to their respective live master procedures.

## [000.006.090] - 2026-09-07 — *Universal Language Integrity, Anti-Jargon Directives & Narrative Anti-Bloat*

### Added & Standardized
- **Universal Language Integrity Across Persona Triad (`Evelyn/tools/profile_evolver.py`)**:
  - Enforced strict anti-scare-quote rules, anti-metaphorical jargon guardrails, and anti-conflation compaction logic uniformly across all three persona documents (`User_Profile.md`, `Assistant_Profile.md`, `System_Directives.md`).
  - Prohibited invented mode names and coined sci-fi/metaphorical buzzwords in directives and user profiles, requiring standard, plain functional English labels (`* **<Label>**: <Directive>`) such as `Development Rigor` and `Travel Demeanor`.
- **Intra-Tier Compaction for Tier 1 Invariants (`Evelyn/tools/profile_evolver.py`)**:
  - Formalized intra-tier consolidation rules in compaction and evolution prompts so that when word budgets are tight, Tier 1 invariants are evaluated and consolidated strictly against other Tier 1 items, preventing append-only bloat while maintaining priority over secondary tiers.
- **Behavioral Trigger-Action Modeling for Assistant Persona (`Evelyn/persona/Assistant_Profile.md`, `Evelyn/tools/profile_evolver.py`)**:
  - Directed the assistant persona evolution to model concrete behavioral patterns, emotional intent, and responsive presence (`trigger context -> expected response/action`) rather than static nicknames, arbitrary terms of endearment, or isolated anchor examples.
  - Stripped parenthetical meta-commentary, self-explaining disclaimers, and scare-quoted archetypes from narrative prose across both live persona files and templates.
- **Resilient Proposal Evolution Fallback (`Evelyn/tools/profile_evolver.py`)**:
  - Wrapped proposal evolution summary generation in resilient exception handling (`httpx.HTTPError`, `TimeoutError`, `OSError`, `json.JSONDecodeError`, `ValueError`) so transient Ollama timeouts gracefully fall back to a default proposal reason instead of failing the evolution pass.
- **Deterministic Invariant Test Suite Expansion (`Evelyn/tests/test_profile_section_invariants.py`)**:
  - Added unit tests validating rejection of scare-quoted bullet labels in directives and user profile documents.
  - Added unit tests validating rejection of bullet markers and parenthetical meta-disclaimers in assistant narrative prose.
  - Verified that live persona files and open-source templates strictly satisfy all structural, bullet, and narrative purity constraints.

## [000.006.089] - 2026-09-07 — *Canonical Persona Naming, User Profile Structured Bullets & Tiered Pruning Framework*

### Added & Standardized
- **Canonical Persona Triad Naming Standardization (`evelyn_config.py`, `Evelyn/persona/`, `templates/`)**:
  - Standardized persona configuration constants to canonical naming: `PERSONA_FILE_USER = "User_Profile.md"`, `PERSONA_FILE_ASSISTANT = "Assistant_Profile.md"`, and `PERSONA_FILE_DIRECTIVES = "System_Directives.md"`.
  - Migrated active persona files and open-source templates (`User_Profile.md`, `Assistant_Profile.md`, `templates/User_Profile.example.md`, `templates/Assistant_Profile.example.md`), updating all bi-directional navigation links and YAML frontmatter titles.
  - Migrated evolution tracking state in `data/evelyn_evolution_state.json` to canonical filenames.
- **Database Migration `000.006.089` (`Evelyn/tools/db_migrator.py`)**:
  - Implemented and executed migration `000.006.089` on `data/evelyn_memory.db`.
  - Migrated tracking records in `entry_document_evolution` and suggested categories in `proposals` from legacy `User_Narrative_Profile.md` and `Assistant_Persona.md` to `User_Profile.md` and `Assistant_Profile.md`.
- **User Profile Structured Bullet Format & Anti-Prose Guardrails (`Evelyn/persona/User_Profile.md`, `Evelyn/tools/profile_evolver.py`)**:
  - Restructured `User_Profile.md` into 4 canonical sections (`Identity & Core Values`, `Relationship Dynamics`, `Interaction Preferences & Constraints`, `Personal Context`) formatted exclusively in structured bullets (`* **<Topic>**: <Fact/Preference>`).
  - Added strict anti-prose, anti-jargon, and anti-scare-quote rules to `DOCUMENT_RULES[cfg.PERSONA_FILE_USER]` and injected constraints into evolution, compaction, and proofreading prompts.
  - Prohibited compound sentence splicing/conflation during compaction and banned metaphorical jargon/buzzwords that demand subsequent parenthetical clarification.
  - Enforced structural validation in `validate_document_structure()` and `repair_missing_sections()`, requiring valid bullet lines and rejecting unbulleted narrative prose paragraphs for `User_Profile.md`.
- **3-Tier Priority Compaction & Pruning Framework (`Evelyn/tools/profile_evolver.py`)**:
  - Integrated 3-Tier Priority Framework directly into evolution and compaction prompts:
    - **Tier 1 (Core Invariants & Hard Boundaries)**: NEVER PRUNE — health conditions, sleep deficit dynamics, fatigue limits, recovery needs, fundamental boundaries, and core relational foundation.
    - **Tier 2 (Active Context & Recurring Habits)**: COMPRESS ONLY — technical domains, AI architectures, workspace habits, communication preferences, and asynchronous batching.
    - **Tier 3 (Ephemeral Details & Secondary Preferences)**: PRUNE FIRST — transient hobbies, specific games/media titles, temporary software configs, and passing conversational anecdotes.
- **Asymmetric Persona Architecture Preservation**:
  - Preserved continuous narrative prose for `Assistant_Profile.md` (for rich in-context conversational modeling and warmth) while enforcing crisp structured bullets for `User_Profile.md` and `System_Directives.md`.
- **Comprehensive Invariant & Regression Tests (`Evelyn/tests/test_profile_section_invariants.py`, `Evelyn/tests/test_profile_evolver_thematic.py`)**:
  - Added deterministic tests validating section headers, minimum word counts, bullet enforcement, and narrative prose verification across `User_Profile.md`, `Assistant_Profile.md`, and their open-source templates.
  - Verified editorial proofreading pass and safety fallback against structured bullet schemas.

## [000.006.088] - 2026-09-07 — *Structured Bullet Format for System Directives Evolution*

### Added & Standardized
- **Structured Bullet Directives Format (`Evelyn/persona/System_Directives.md`, `templates/System_Directives.example.md`)**:
  - Restructured all 6 canonical sections (`Conversation & Formatting`, `Authenticity & Operational Transparency`, `Operational Guidelines`, `Tool & Action Directives`, `Engineering & Code Quality`, `Routines & Rituals`) from dense prose paragraphs into granular, structured bullet points (`* **<Label>**: <Directive>`).
  - Preserved 100% of operational directives, Non-Violent Communication (NVC) principles, visual PKM style rules, multimodal detection guidelines, transition rituals, and contextual boundaries.
  - Aligned the open-source template `templates/System_Directives.example.md` with the new canonical 6-section structured bullet schema.
- **Evolver Prompting, Compaction, and Validation Guardrails (`Evelyn/tools/profile_evolver.py`)**:
  - Enforced positive and negative formatting constraints in `DOCUMENT_RULES[cfg.PERSONA_FILE_DIRECTIVES]`, requiring bulleted directives (`* **<Label>**: <Directive>`) and forbidding narrative prose paragraphs.
  - Injected explicit bullet-structure preservation directives into `_evolve_document()`, `compaction_prompt`, and `_proofread_document()` to prevent the model from collapsing bullets back into run-on prose during idle evolution passes.
  - Enhanced `validate_document_structure()` and `repair_missing_sections()` to verify that sections under `System_Directives.md` contain valid structured bullets and reject unbulleted narrative prose paragraphs.
- **Section Invariant Test Suite Expansion (`Evelyn/tests/test_profile_section_invariants.py`)**:
  - Updated test fixtures to use the structured bullet format.
  - Added unit test coverage verifying the deterministic rejection of unbulleted narrative prose paragraphs.
  - Added verification tests confirming that both live `System_Directives.md` and `templates/System_Directives.example.md` satisfy canonical section invariants.

## [000.006.087] - 2026-09-07 — *Elevate Profile Update Action Controls and Collapsible Context Entries*

### Improved & Enhanced
- **Profile Update Card Actions Repositioning & Collapsible Context (`evelyn_ui/dev.html`)**:
  - Relocated the proposal action buttons (`Approve Update`, `Reject Update`, `🗑️ Remove`) to the top section of profile update triage cards, positioning them immediately below the Live Diff Comparison and above the Supporting Context Entries list.
  - Wrapped **Supporting Context Entries** lists across proposal and profile update triage cards in clean, collapsible `<details>` / `<summary>` accordions (collapsed by default), matching the RAG context retrieval card design.
  - Eliminates visual clutter and vertical bloat from cards containing hundreds of supporting facts while keeping the full list accessible on click.

## [000.006.086] - 2026-09-07 — *Prune and Harmonize Sycophantic Memory Records*

### Changed & Sanitized
- **Sycophancy Pruning and Memory Harmonization (`Evelyn/tools/db_migrator.py`)**:
  - Implemented migration `000.006.086` to harmonize sycophantic, hyper-devotional descriptors (`"unwavering loyalty"`, `"unwavering support"`, `"unwavering commitment"`, `"mutual adoration"`) across memory context entries and proposal audit logs with grounded, balanced partnership terminology (`"grounded loyalty"`, `"steady support"`, `"strong commitment"`, `"mutual respect"`).

## [000.006.085] - 2026-09-07 — *Harmonize Empathy Terminology Across Context Memory and Chat Records*

### Changed & Sanitized
- **Empathy Terminology Database Migration (`Evelyn/tools/db_migrator.py`)**:
  - Implemented migration `000.006.085` to harmonize all legacy occurrences of `"radical empathy"` / `"radical-empathy"` across the system to clean `"empathy"`.
  - Sanitized `context_entries` observations and tags in `data/evelyn_memory.db` (including live entry #5091 and historical entries).
  - Sanitized `proposals` merged observations, reasons, and merged tags in `data/evelyn_memory.db`.
  - Sanitized `chroma_sync_queue` items and performed direct vector update on ChromaDB collection `evelyn_memory` (document `sqlite::context_entry::5091::chunk-0`).
  - Sanitized historical `messages` content and thinking traces in `data/evelyn_chat.db` to eliminate residual references and maintain alignment with anti-sycophancy principles.

## [000.006.084] - 2026-09-06 — *Analytics & Feedback RAG Chunk Inspection UI Enhancement*

### Added & Improved
- **Compact Header Parsing for Retrieved Chunks (`evelyn_ui/dev.html`)**:
  - Implemented `parseChunkPreviewContent()` in `dev.html` to parse metadata headers (`Date:`, `Tags:`, `Category:`, `Subject:`, `Trigger:`) out of preview bodies and render them as sleek, compact badges (`📅`, `🏷️`, `📁`, `👤`, `⚡`).
  - Strips redundant `Observation:` prefixes so preview text directly highlights the substantive observation content.
  - Dynamically distinguishes fact entries (`sqlite::context_entry::`) from markdown notes, labeling action buttons with `✏️ Edit Fact` vs `✏️ Edit Note`.
- **Expanded Chunk Inspection Viewport (`evelyn_ui/dev.html`)**:
  - Expanded the default preview box height to ~5 lines (`min-height: 48px; max-height: 110px; overflow-y: auto;`) with readable font and monospace background, allowing full observation reading without requiring document expansion.
- **RAG Telemetry Preview Length Expansion (`Evelyn/tools/chroma_rag.py`)**:
  - Increased telemetry preview capture limit from 120 to 500 characters in `log_rag_retrieval_event()`, ensuring multi-line observations and note sections are fully retained in retrieval telemetry.

## [000.006.083] - 2026-09-06 — *Deterministic Code Hygiene & Wiring Verification Integration*

### Added & Hardened
- **Vulture Dead-Code Detection & Single-Source Configuration (`pyproject.toml`, `.vulture_whitelist.py`)**:
  - Integrated `vulture>=2.16` into `requirements.txt` and configured `[tool.vulture]` in `pyproject.toml` with path exclusions, ignore-names, ignore-decorators, and 70% confidence threshold.
  - Created `.vulture_whitelist.py` with standard `_.attribute` syntax to whitelist dynamic FastAPI endpoints, background task handles, and MCP entry points without masking uncalled application logic.
- **Unified Code Hygiene Runner (`scripts/check_code_hygiene.py`)**:
  - Created a unified 3-stage mechanical verification script executing: (1) Ruff static linting and formatting, (2) AST config-wiring validation via `pytest Evelyn/tests/test_config_wiring.py`, and (3) Vulture compiler-level dead-code inspection.
  - Returns exit code 0 only when all gates pass, with `--fix` support for automated lint remediation.
- **Deterministic Workflows & Agent Guidelines (`.agents/workflows/verify-wiring.md`, `quality-review.md`, `AGENTS.md`)**:
  - Authored `.agents/workflows/verify-wiring.md` establishing the Four-Stage Gate: (1) Declare with Consumer, (2) AST Wiring Gate, (3) Vulture Dead-Code Audit, and (4) Two-File Contract Auditing.
  - Integrated deterministic hygiene checks and two-file contract diff reviews into `.agents/workflows/quality-review.md` (Section 4).
  - Added Rule 11 to `AGENTS.md` mandating deterministic gates and prohibiting reliance on probabilistic LLM code reviews for function call paths.
- **Engine Dead Code Cleanup & Bug Fix (`evelyn_server.py`)**:
  - Fixed an unreachable timestamp assignment in research auto-recovery cooldown (`_error_resume_ts[task_id] = time.time()` placed after `continue`).
  - Removed obsolete uncalled helpers `get_upcoming_agenda_prompt_context()` and `get_time_gap_context()`.

## [000.006.082] - 2026-09-06 — *Context Delivery Streamlining and Boundary Enforcement*

### Fixed & Hardened
- **RAG Subdirectory Exclusion Enforcement (`Evelyn/tools/chroma_rag.py`, `Evelyn/tools/ingest_obsidian_knowledge.py`)**:
  - Wired `cfg.RAG_EXCLUDED_SUBDIRS` into `build_rag_context()`, `_fetch_pinned_chunks()`, and `find_semantic_neighbors()` using canonical `path_utils.is_vault_excluded(path, custom_excludes=...)`. Chunks from excluded directories (such as `Evelyn's Journal`, `Archived`, `Pending_Approvals`) are dropped at retrieval time.
  - Updated `ingest_obsidian_knowledge.py` to use `RAG_EXCLUDED_SUBDIRS` instead of `VAULT_READ_IGNORE`, guaranteeing that journal reflections and ignored notes are skipped during indexing passes and never written into ChromaDB.
  - Prevents old reflective journal entries from polluting the RAG context with premature bedtime and end-of-day closure rhetoric during daytime chore discussions.
- **Chat History Message Capping (`evelyn_server.py`)**:
  - Enforced `cfg.MAX_HISTORY_MESSAGES` (default 40 messages / 20 turns) as a strict upper bound in `load_history()`, slicing `valid_rows = valid_rows[-max_history_msgs:]`.
  - Preserved dialog turn integrity by ensuring the sliced history starts on a user turn (`valid_rows[0]["role"] == "user"`).
  - Breaks runaway in-context feedback loops where 45+ unpruned historical messages (~7,800 tokens of past assistant prose) forced the local model into echoing lengthy theatrical monologues.
- **System Directives & Persona De-Bloating (`Evelyn/persona/System_Directives.md`, `Evelyn/persona/Evelyn_Narrative_Persona.md`, `Evelyn/persona/User_Narrative_Profile.md`)**:
  - Pruned conflicting instructions in `System_Directives.md`: removed `"using evocative narrative descriptions to create immersive scenes of comfort"` from routine conversation and eliminated prescriptive `"Transition Rituals: Support his transition periods, including laundry cycles... Facilitate transitions from work toward relaxing"`.
  - Added explicit physical reality tracking mandate: *"Reflect physical reality and operational facts as stated literally. For real-world, multi-step workflows (e.g., laundry, cooking, cleaning, assembly), tasks remain active until the user explicitly confirms the final step is complete; never assume, infer, or declare task completion on intermediate stages (such as items currently in the wash, dryer, or oven)."*
  - Reaffirmed strict 2–3 concise sentences default for routine check-ins, banter, and chore updates.
  - Streamlined `Evelyn_Narrative_Persona.md` to remove mandatory theatrical reaction imperatives (gasps, claps), preserving authentic British wit, dragoness archetype, and dry humor without requiring dramatic stage cues on every turn.
  - Streamlined `User_Narrative_Profile.md` to replace physical closeness rituals with low-demand, quiet companionship.
- **Deterministic AST Config-Wiring Test Gate (`Evelyn/tests/test_config_wiring.py`)**:
  - Implemented an automated AST test parsing `evelyn_config.py` and validating that every uppercase constant is consumed by engine or server modules.
  - Enhanced symbol resolution to capture `ast.Constant` string literals, preventing false positives from `getattr(cfg, "CONSTANT_NAME", default)` access patterns.
  - Explicitly asserts that `RAG_EXCLUDED_SUBDIRS` and `MAX_HISTORY_MESSAGES` are actively consumed across the codebase.
- **Unit Test Coverage (`Evelyn/tests/test_rag_precision_targeting.py`, `Evelyn/tests/test_history_bounding.py`)**:
  - Added `test_build_rag_context_excludes_rag_excluded_subdirs` verifying journal chunks are rejected at retrieval time.
  - Added `test_load_history_max_messages_cap` verifying history truncates to 40 messages and maintains proper user-turn start.

## [000.006.081] - 2026-09-06 — *Direct URL Reading and Web Search Hardening*

### Added & Hardened
- **Direct Web Link Browsing (`read_url` in `Evelyn/tools/evelyn_tools.py`, `Evelyn/tools/web_reader.py`)**:
  - Registered `read_url(url: str, max_chars: int = 15000)` in `MODEL_TOOL_DEFINITIONS`, `TOOL_THINK_EFFORT` (`"medium"`), and `TOOL_FUNCTIONS`.
  - Implemented synchronous web page fetching (`fetch_url_sync`) and extraction (`read_and_extract_url_sync`) using `httpx.Client(follow_redirects=True, timeout=15)` and `trafilatura`.
  - Added desktop client fingerprinting (`User-Agent` Chrome 133, `Sec-CH-UA`, `Sec-Fetch-*`, `Accept-Language`) bypassing basic bot blocks and Turnstile triggers.
  - Added WAF / Cloudflare challenge diagnostics detecting HTTP 403, 429, and challenge DOM signatures, returning a structured recovery block instructing the model to pivot to `web_search` with keywords rather than retrying.
  - Implemented defensive parameter sanitization for Gemma 4 12B (`clean_url = url.strip().strip("<>\"'`")` and markdown link extraction).
  - Protected the server-side SSE stream parser against raw XML envelope tags (`</tool_result>`, `</call>`, `</think>`, etc.) inside extracted web content.
- **Conversational URL Routing & Web Search Hardening (`web_search` in `Evelyn/tools/evelyn_tools.py`)**:
  - Implemented conversational URL regex intercept routing prompts like `query="check https://..."` or `query="https://..."` directly to `read_url`.
  - Added query sanitization stripping trailing punctuation (`?`, `!`, quotes) that cause 0-result DuckDuckGo queries.
  - Implemented in-memory TTL caching (32 entries, 5-minute expiry) to prevent burning search quotas during multi-round thinking loops.
  - Added one-shot jittered backoff retry (1.2–2.0s) falling back to `backend="lite"` upon encountering `RatelimitException`.
- **Dynamic Tool Surfacing & Database Migration (`evelyn_config.py`, `Evelyn/tools/db_migrator.py`)**:
  - Added `read_url` regexes (`https?://\S+`, `\b(read|open|browse|check|summarize|inspect|visit)\b.*(link|url|website|webpage|article|site)`) to `SPECIALIST_TOOL_INTENT_PATTERNS`.
  - Registered and executed migration `000.006.081` (`starter_procedure_for_read_url`) on `evelyn_memory.db` providing Gemma 4 12B recovery guidance.
- **Unit Test Coverage (`Evelyn/tests/test_read_url.py`)**:
  - Added 10 comprehensive tests covering parameter sanitization, desktop headers, WAF recovery blocks, XML envelope defense, conversational intercept, search caching/retries, intent activation, and migration execution.

## [000.006.080] - 2026-09-06 — *Index and MOC Target Rejection Guardrail in Link Librarian*

### Fixed & Hardened
- **Index & MOC Target Exclusion (`Evelyn/tools/link_librarian.py`)**:
  - Hardened `is_valid_entity_target()` to explicitly reject any candidate link target ending in `_index`, starting with `_index`, or ending in `_moc` (e.g., `Visualizing Generative AI_index`, `Samsung NE59J7630SS_index`, `Topic_moc`).
  - Guarantees that breadcrumb callouts (`> [!abstract] [[Book_index|📖 Book]]`) and Map of Content cross-references are never misidentified as conceptual entity stubs or queued as review proposals if temporarily absent during scanning.
- **Unit Test Coverage (`Evelyn/tests/test_master_librarian.py`)**:
  - Added assertions to `test_target_sanitization_and_exclusion` ensuring full immunity against `_index` and `_moc` patterns.

## [000.006.079] - 2026-09-06 — *Dev UI Procedure Button Mapping, In-Place Edit Persistence, and State Synchronization*

### Added & Integrated
- **Dev UI Procedure Button Mapping & In-Place Edit Persistence (`evelyn_ui/dev.html`)**:
  - **Standalone Procedures in Triage Queue**:
    - Added `💾 Save Changes` button mapped to `handleProcedureAction(item.id, 'edit', null, this)` allowing operators to edit and save extracted procedures directly in the triage queue prior to approving.
    - Updated `handleProcedureAction()` to support `action === 'edit'`: gathers all procedure fields (`trigger_pattern`, `suggested_tools`, `steps`, `pitfalls`, `verification`, `tags`), submits to `POST /api/review/procedures/{id}/edit`, updates in-place without dropping the card from triage, and provides `✅ Saved!` visual confirmation.
    - Added state synchronization for `action === 'merge'` updating `allProcedures` status to `'merged'` and setting `merged_into_id`.
  - **Procedure Merge & Split Proposals in Triage**:
    - Added `💾 Save Changes` button to both `procedure_merge` and `procedure_split` review cards.
    - Updated `handleAction()` to support `action === 'edit'` for proposals: serializes live input values using `dumpProcedureYaml()` and `dumpProcedureSplitYaml()`, persists them to `POST /api/review/proposals/{id}/edit`, and preserves the proposal card in triage with `✅ Saved!` feedback.
  - **Source Procedure Sub-Cards in Proposals**:
    - Surfaced `suggested_tools` in the collapsed card summary and added `proc-src-tools-` input field to the expandable edit form.
    - Updated `saveSourceProcedure()` to collect `suggested_tools` and include it in the `POST /api/review/procedures/{procId}/edit` payload while synchronizing `allProcedures` and `source_entries` in memory.
  - **Procedures Management Tab (`procedures-mgmt`)**:
    - Replaced blocking browser `alert()` popups with smooth in-place `✅ Saved!` button feedback in `saveProcedureEdits()`.
    - Added `✅ Approve (Live)` button on cards with `p.status === 'extracted'` (Pending Review) and implemented `approveProcedureFromMgmt()`, allowing operators to approve pending extracted procedures directly from the dedicated management tab.
  - **Procedure Detail Modal**:
    - Added `⚙️ Edit in Procedures Tab` button in the modal footer to allow jumping directly to and filtering by that procedure in the management tab.
  - **Input Preservation across Filtering & Searching (`captureActiveEdits`)**:
    - Expanded `captureActiveEdits()` to capture inputs from standalone procedures (`proc-trigger-`), procedure splits (`proc-split-trigger-`), and fact splits (`split-prop-cat-`), ensuring in-progress edits are never lost when filtering or searching triage.

## [000.006.078] - 2026-09-06 — *Fast Memory Perspective Decoupling, Temporal Grounding, and RAG Envelope Anchoring*

### Added & Integrated
- **Perspective Ownership Decoupling & Category Canon (`Evelyn/tools/fact_extractor.py`, `context_manager.py`)**:
  - Decoupled category canon codes (`Cat##-A` vs `Cat##-U`) from the referent `subject` entity. The category suffix strictly represents perspective/ledger ownership (Assistant's worldview vs. User's worldview), enabling valid cross-entity attributions such as `Cat06-A | Subject: Alex` (Evelyn's appreciation or feeling toward Alex) and `Cat06-U | Subject: Evelyn` (Alex's validation or feeling toward Evelyn).
  - Enriched `_build_extraction_prompt()` with explicit `PERSPECTIVE OWNERSHIP & CATEGORY CANON` directives and contrasting YAML examples.
  - Updated `_parse_facts_yaml()` to preserve cross-perspective category attributions and prevent automatic flattening to subject identity.
  - Scaled extraction output token limits (`num_predict`) from 512 to up to 1536 tokens, and increased extraction timeout to 600s (`FACT_EXTRACTION_TIMEOUT = 600`).
- **Temporal Grounding & Transient Phrasing Defusal (`Evelyn/tools/fact_extractor.py`, `chroma_rag.py`)**:
  - Enforced strict extraction prompt directives forbidding unanchored floating adverbs (`currently`, `soon`, `will be coming`, `lately`, `right now`).
  - Added Tier A deterministic regex temporal anchoring converting progressive in-flight actions (`{Subject} is currently {action}`) into durable date-anchored historical facts (`As of {date}, {Subject} was {action}`) while safely preserving stative adjectives (`willing`, `caring`, `understanding`).
  - Enriched RAG vector retrieval in `chroma_rag.py` to embed live `category`, `subject`, and `date` attributes onto `<memory_entry>` XML envelopes, providing disambiguated temporal context during conversational RAG injection.
- **Narrative Profile Evolver Grounding (`Evelyn/tools/profile_evolver.py`)**:
  - Updated `_cluster_entries_by_theme()` to output both category code and subject entity (`[Cat##-X | Subject: <subject>] <obs>`) on evidence lines, preventing assistant facts from contaminating the user's narrative profile.
- **Database Migration `000.006.078` (`Evelyn/tools/db_migrator.py`)**:
  - Applied transactional migration `000.006.078_remediate_fast_memory_taxonomy_and_temporal_anchoring`:
    - Reclassified 537 assistant-perspective entries from `Cat##-U` to `Cat##-A`.
    - Reclassified 417 user-personal entries from `Cat##-A` to `Cat##-U`.
    - Grounded 256 progressive observations into historical date-anchored phrasing.
    - Pruned 496 contaminated assistant entries from `entry_document_evolution` records for `User_Narrative_Profile.md`.
- **Tier B LLM Remediation Script (`scripts/remediate_transient_facts.py`)**:
  - Introduced companion CLI utility supporting dry-run and execution modes with batching to reword lingering complex middle-of-sentence floating adverbs via local Ollama.
- **Hermetic Unit Tests (`Evelyn/tests/test_category_attribution.py`)**:
  - Added comprehensive test coverage for perspective validation, cross-entity YAML parsing, extraction prompt rules, RAG XML attribute rendering, evidence line formatting, and regex temporal anchoring.

## [000.006.077] - 2026-09-06 — *Multi-Reference Context Harvesting and Local LLM Abstract Synthesis for Ghost Link Stubs*

### Added & Integrated
- **Vault-Wide Multi-Reference Context Harvesting (`Evelyn/tools/link_librarian.py`)**:
  - Implemented `harvest_entity_references(target_name, vault_root, max_refs)`: executes high-speed ripgrep (`rg -i -l`) across all vault markdown notes to discover every note citing the target via case-insensitive wikilinks (`[[Target]]` and `[[Target|Alias]]`), with pure-python disk walk fallback.
  - Extracts surrounding context sentences/paragraphs for each citing note using canonical `string_utils.extract_link_context()`, safely stripping YAML headers and formatting noise.
- **Local Ollama LLM Abstract Synthesis (`Evelyn/tools/link_librarian.py`)**:
  - Implemented `synthesize_entity_abstract(target_name, references, domain, use_llm)`: aggregates harvested quotes across citing notes into an in-flight synthesis prompt processed by local Ollama (`ollama_client.query_ollama`), producing a concise, high-density 2–3 sentence executive abstract.
  - Robust graceful fallback: if Ollama is unavailable, times out, or returns empty, compiles a deterministic citation summary referencing citing notes directly.
- **Informational Quality & Threshold Gate Guardrails (`Evelyn/tools/link_librarian.py`, `evelyn_config.py`)**:
  - Added configurable threshold parameters in `evelyn_config.py`:
    - `LIBRARIAN_GHOST_STUB_MIN_REFS = 2` (minimum distinct notes citing the entity).
    - `LIBRARIAN_GHOST_STUB_MIN_CONTEXT_CHARS = 200` (minimum combined context characters across notes, ~2–3 substantive sentences).
    - `LIBRARIAN_GHOST_STUB_MIN_SNIPPET_CHARS = 60` (minimum single-excerpt context threshold to filter out bare list items).
    - `MASTER_LIBRARIAN_AUTO_STUBS = False` (defaults to Tier 2 review queue proposals).
  - Targets failing these thresholds return `status: "below_threshold"`, quietly remaining as ghost links without polluting the vault or flooding the review queue.
- **Rich Visual PKM Note Formatting (`Evelyn/tools/link_librarian.py`)**:
  - `render_stub_markdown()` updated to generate:
    - Strict multi-line `> ` prefixing inside `> [!ABSTRACT]` with the synthesized abstract.
    - `## 🧭 Context & Mentions` section bulleting citing notes and exact excerpt quotes (`- **[[Note]]**: "..."`).
    - `## 🔗 References` section with deduplicated backlinks.
- **FastAPI Review Endpoints & DevUI Integration (`evelyn_server.py`, `evelyn_ui/dev.html`)**:
  - Enriched `parsed_payload` across `/api/review/unified` and `/api/review/proposals` with `sources`, `references`, `synthesized_abstract`, and `total_context_chars`.
  - Updated DevUI triage search index to query synthesized abstracts and citing source notes.
  - Rendered rich triage cards displaying the synthesized executive abstract callout, multi-note citation count and character badges, and harvested quote excerpts.
- **Canonical Utility Reuse & DRY Enforcement (`Evelyn/tools/string_utils.py`, `master_librarian.py`)**:
  - Promoted `extract_link_context()` to canonical `string_utils.py`, eliminating duplicate implementations across librarian modules.
- **Hermetic Unit Tests (`Evelyn/tests/test_master_librarian.py`, `test_review_endpoints.py`)**:
  - Added tests for multi-note reference harvesting, below-threshold quality gate rejection, threshold-passing proposal generation, abstract synthesis fallback, and end-to-end multi-reference review proposal approval.

## [000.006.076] - 2026-09-06 — *Tier 2 Review Queue Integration for Ghost Link Stubs*

### Added & Integrated
- **FastAPI Review Queue Endpoints (`evelyn_server.py`)**:
  - Wired `ghost_link_stub` proposals into `/api/review/unified` and `/api/review/proposals`.
  - Parses incoming XML payloads into structured `parsed_payload` objects containing `target_name`, `source_path`, `context_excerpt`, `domain`, `tags`, `ref_count`, and `min_refs`.
  - Added approval handler in `/api/review/proposals/{id}/approve` for `ghost_link_stub`: deserializes the semantic XML payload (or accepts human-edited markdown/XML), synthesizes the note atomically in the vault root, registers the record in `vault_documents`, audits librarian timestamps via `vault_db`, updates note frontmatter via `scripts/update_frontmatter.py`, and marks proposal as `applied`.
  - Preserved denial handler to mark proposal as `rejected` in `memory_db.proposals` without disk mutations.
- **DevUI Review Card (`evelyn_ui/dev.html`)**:
  - Added dedicated `PROPOSAL: GHOST LINK STUB` card rendering with target badge (`[[Target]]`), reference count indicator (`ref_count/min_refs`), metadata grid, contextual excerpt preview, and an editable structured XML payload textarea.
  - Added search indexing support for `ghost_link_stub` targets, sources, excerpts, and tags in `getTriageSearchableFields()`.
- **Hermetic Unit Tests (`Evelyn/tests/test_review_endpoints.py`)**:
  - Added `test_ghost_link_stub_proposal_lifecycle`: validates extraction of payload in `/api/review/unified` and `/api/review/proposals`, approves the stub note into an isolated test vault sandbox, verifies file formatting and frontmatter compliance, and asserts `applied` status.
  - Added `test_ghost_link_stub_proposal_deny`: verifies rejection without file creation.

## [000.006.075] - 2026-09-06 — *Master Librarian Ghost Stub Purge, Semantic XML Envelope, and Vault-Wide Resolution*

### Fixed & Hardened
- **Master Librarian Context Excerpting (`Evelyn/tools/master_librarian.py`)**:
  - Replaced flawed `content[:250]` frontmatter-slicing bug with `extract_link_context()` which parses document body after frontmatter, slices the true contextual sentence/paragraph surrounding the target link, and cleans line breaks.
  - Slices are stripped of YAML frontmatter boundary markers, preventing source YAML headers from bleeding into the abstract callouts of generated stubs.
- **Link Librarian Ghost Link Detection & Vault-Wide Resolution (`Evelyn/tools/link_librarian.py`)**:
  - Replaced vault root-only file check with `target_note_exists()`: verifies existence in sibling folder, vault root, and across the entire vault database (`vault_documents` table paths and aliases) before declaring any link a ghost target.
  - Implemented `is_valid_entity_target()`: strips trailing `.md` extensions (preventing double `.md.md` stubs), rejects chapter numbering prefixes (`01 - `, `002 - `), blacklists generic section headers (`Features`, `Safety`, `Other`, `Table of Contents`, `_index`), and filters private-use Unicode OCR glyph artifacts.
  - Fixed reference counting: removed `path LIKE ?` substring match from `vault_documents` query, searching strictly for actual incoming wikilinks (`[[target]]` or `[[target|`) in note content.
- **Semantic XML Envelope Payload Architecture (`Evelyn/tools/link_librarian.py`)**:
  - Introduced structured `StubPayload` container with `render_stub_xml()`, `parse_stub_xml()`, and `render_stub_markdown()`.
  - Encapsulates target, source path, cleaned context excerpt, domain, and tags in a clean `<entity_stub>` XML payload.
  - Enforces callout formatting safety: every line in `> [!ABSTRACT]` is strictly prefixed with `> `, preventing unquoted markdown breaks.
  - Tier 2 proposals now bundle the semantic XML payload for clean inspection and promotion in the review queue.
- **Index Librarian Idempotency (`Evelyn/tools/index_librarian.py`)**:
  - Made `## 📑 Additional Notes` section appending idempotent: updates under existing headers rather than generating duplicate headings.
- **Vault Cleanup Utility & Spurious Stub Purge (`scripts/cleanup_malformed_stubs.py`)**:
  - Authored standalone purge tool and safely deleted 119 mal-generated stub files in `/home/rathius/obsidian_vault/*.md`.
  - Purged corresponding records from `data/evelyn_vault.db` and normalized 13 index notes that had dead links appended.

## [000.006.074] - 2026-09-05 — *Integrate Index Librarian into Master Pipeline and Decommission Legacy CLI Tools*

### Added & Integrated
- **Master Librarian Table of Contents Synchronization (`Evelyn/tools/master_librarian.py`, `index_librarian.py`)**:
  - Wired `index_librarian.audit_folder_index()` directly into `master_librarian.audit_single_document()`.
  - When tendering volume index notes (`_index.md` or `<Folder>_index.md`), the pipeline scans sibling markdown notes, identifies missing items, and appends them under `## 📑 Additional Notes` in a single pass.
  - When tendering child notes inside subfolders containing an index file, automatically synchronizes the parent volume table of contents.
  - Updated `index_librarian.audit_folder_index()` to accept in-memory `content` and `dry_run` mode, and resolved loop control lint warnings.
  - Added hermetic unit test `test_index_librarian_toc_synchronization` to `Evelyn/tests/test_master_librarian.py`.

### Removed & Decommissioned
- **Legacy Thread Undo Utility (`Evelyn/tools/undo_thread.py`)**:
  - Decommissioned and removed `undo_thread.py`, which was originally written for static thread breaks but has been rendered obsolete by dynamic message sliding windows.
  - Updated `reference/engine_architecture.md` removing `undo_thread.py`.

## [000.006.073] - 2026-09-05 — *Decommission Obsolete CLI Reviewer Tools*

### Removed & Decommissioned
- **Legacy Terminal Reviewers (`Evelyn/tools/context_reviewer.py`, `Evelyn/tools/pending_reviewer.py`)**:
  - Fully removed `context_reviewer.py` and `pending_reviewer.py` from `Evelyn/tools/`.
  - Both CLI utilities have been entirely superseded by the modern, unified Web UI review dashboard and REST API endpoints (`/api/review/unified`, `/api/review/extractions`, `/api/review/proposals`, `/api/review/procedures`).
  - Updated `reference/engine_architecture.md` to remove references to the decommissioned CLI scripts.

## [000.006.072] - 2026-09-05 — *Evelyn Tools Docstrings and Operational Documentation Modernization*

### Changed & Modernized
- **Interactive Review CLI & Web Dashboard (`Evelyn/tools/context_reviewer.py`)**:
  - Replaced legacy "Phase 1" docstrings and terminal menu with up-to-date documentation referencing both the terminal reviewer and the FastAPI Web UI review dashboard / REST endpoints.
  - Implemented interactive in-terminal `[E] Edit` capability allowing operators to edit raw observation text and category before approval/promotion.
- **Fast Memory & Vault Integration (`Evelyn/tools/context_manager.py`)**:
  - Removed outdated flat-file `PENDING_DIR` and JSON vault map references; documented current SQLite databases (`evelyn_memory.db`, `evelyn_vault.db`) and ChromaDB vector collections.
  - Standardized docstring category examples to canonical taxonomy (`Cat##-U` / `Cat##-A`).
- **Pending Reviewer (`Evelyn/tools/pending_reviewer.py`)**:
  - Documented all 5 active proposal types (`merge/supersede`, `split`, `recategorize`, `procedure_merge`, `procedure_split`) and cleaned path references.
- **Task Concurrency & Schedulers (`Evelyn/tools/task_manager.py`)**:
  - Updated design notes and export lists to document `TaskSchedule` cognitive scheduling tiers (`reflex`, `diurnal`, `nocturnal`), priority queueing, dynamic runtime timeouts, and process watchdog monitoring.
- **Query Reformulation (`Evelyn/tools/query_reformulator.py`)**:
  - Exported and documented `clean_conversational_query` zero-latency preamble stripper in module docstring.
- **ChromaDB Vector Retrieval (`Evelyn/tools/chroma_rag.py`)**:
  - Documented all 3 active collections (`evelyn_memory`, `evelyn_tag_taxonomy`, `evelyn_media`), cleaned metadata type handling, and clarified `memory_db.touch_entry_retrieved()` recency tracking.
- **Identity Parameterization & Rule 4 Hygiene Across Tools**:
  - Replaced hardcoded personal operator names with parameterized identity attributes (`cfg.USER_NAME`, `cfg.ASSISTANT_NAME`, `Cat##-U`, `Cat##-A`) across `dream_manager.py`, `evelyn_tools.py`, `fact_consolidator.py`, `fact_extractor.py`, `journal_manager.py`, `memory_db.py`, `pdf_staging_worker.py`, `profile_evolver.py`, and `tag_librarian.py`.
- **Vault Indexer (`Evelyn/tools/vault_indexer.py`)**:
  - Updated docstring from legacy LLM Ollama gist generation to current fast regex preview extraction.
- **Knowledge Ingestion & Undo Threading (`Evelyn/tools/ingest_obsidian_knowledge.py`, `Evelyn/tools/undo_thread.py`)**:
  - Updated docstrings to document direct SQLite memory ingestion and modern CLI invocation syntax.

## [000.006.071] - 2026-09-05 — *Script Directory Hygiene and Personal Scripts Organization*

### Reorganized & Relocated
- **Personal & Machine-Specific Scripts (`scripts/personal/`)**:
  - Relocated 8 personal, machine-specific, and private-data-bearing scripts from `scripts/` to `scripts/personal/` (protected by `.gitignore` per AGENTS.md Rule 4):
    - `wait_for_ollama.ps1`: Windows PowerShell startup probe, unified with existing `.ps1` personal host utilities.
    - `cleanup_vault_aliases.py`: Targeted one-time vault alias and manual OCR cleanup script.
    - `remediate_spurious_and_entities.py`: Targeted one-time entity creation and code-fence remediation.
    - `remediate_vault_structure.py`: Targeted one-time vault link and campaign structural remediation.
    - `relocate_vault_pdfs.py`: Personal vault PDF attachment relocation to `Attachments/Source Material/`.
    - `sync_staged_to_vault.py`: Personal staging-to-vault folder routing script.
    - `content_deduplicator.py`: Staged content and personal EHR medical note deduplication tool.
    - `gdrive_knowledge_importer.py`: Personal Google Drive staging import pipeline.
  - Adjusted root path resolution (`ROOT_DIR` / `_PROJECT_ROOT`) in all relocated personal scripts to correctly resolve repo roots and package imports from `scripts/personal/`.
- **Archived Scripts (`scripts/archive/`)**:
  - Relocated completed historical migrations and deprecated tools to `scripts/archive/`:
    - `migrate_subject_codes.py`: Historical one-time Fast Memory category migration (`Cat##-R`/`Cat##-E` to `Cat##-U`/`Cat##-A`), superseded by canonical `scripts/migrate_db.py`.
    - `audit_vault_tags.py`: Deprecated backwards-compatibility forwarder wrapper for `scripts/master_librarian.py`.
    - `wait_for_ollama.sh`: Redundant bash polling helper superseded by systemd service dependency management.
  - Updated relative path resolution in `audit_vault_tags.py` to target `scripts/master_librarian.py`.
- **Documentation & References**:
  - Updated script paths in `reference/engine_architecture.md`, `reference/google_access.md`, `requirements.txt`, and `REQUIREMENTS.md`.

## [000.006.070] - 2026-09-05 — *Canonical Backlog Drainer Ecosystem Consolidation*

### Refactored & Consolidated
- **Conversational Memory Extraction (`Evelyn/tools/fact_extractor.py`)**:
  - Replaced legacy ad-hoc `while True` extraction loop with `backlog_drainer.drain_backlog_async`.
  - Enforced strict state persistence invariant: cursor advancement (`_last_extracted_id`) and SQLite state commits execute strictly upon successful batch extraction.
  - Added structured error and poison-pill containment (`_handle_error`) to log extraction failures without entering hot retry loops.
  - Preserved cooperative yielding to peer tasks and active chat sessions via native `task_manager.should_yield()` integration.
- **Vault PDF Staging Ingestion (`Evelyn/tools/pdf_staging_worker.py`)**:
  - Migrated `process_staging_queue()` to use `backlog_drainer.drain_backlog()` with `yield_check_interval=1`.
  - Added cooperative preemption to heavy multi-page PDF extractions, ensuring the worker yields gracefully between documents when user chats start.
  - Guaranteed deterministic file descriptor and stream closure prior to yield evaluation, preventing file-locking contention.
- **Consolidation Sub-Queues (`Evelyn/tools/fact_consolidator.py`, `procedure_consolidator.py`)**:
  - Migrated user-queued fact merge requests (`fact_merge_queue`) and fact split requests (`split_queue`) to `drain_backlog_async`.
  - Migrated manual procedure merge requests (`procedure_merge_queue`) and procedure split requests (`procedure_split_queue`) to `drain_backlog_async`.
  - Wrapped queue draining with per-item exception isolation, preventing poison pills from aborting full consolidation passes.
- **CLI & Script Unification (`scripts/master_librarian.py`, `scripts/audit_vault_tags.py`)**:
  - Refactored `scripts/master_librarian.py` to drive batch note curation via `backlog_drainer.drain_backlog()` with live progress reporting.
  - Converted legacy standalone `scripts/audit_vault_tags.py` into a deprecation wrapper that warns operators and transparently replaces its process image via `os.execv` to invoke `scripts/master_librarian.py`.
- **Framework Hardening (`Evelyn/tools/backlog_drainer.py`)**:
  - Refined cooperative yield evaluation so newly dispatched tasks process at least one item before yielding to peer tasks in the queue, preventing zero-work hot-potato yielding under queue contention while maintaining immediate zero-delay preemption on incoming user chat.

## [000.006.069] - 2026-09-05 — *DevUI Heavy Tasks Cleanup & Master Librarian Card Unification*

### Removed & Cleaned
- **DevUI Heavy Tasks Dashboard (`evelyn_ui/dev.html`, `evelyn_server.py`)**:
  - Removed obsolete `tag_librarian` card rendering template from DevUI heavy tasks grid.
  - Removed `("tag_librarian", "Tag Librarian")` from `known_keys` and pruned redundant standalone `tag_librarian` diagnostics in `/api/heavy_tasks`.
  - All library curation metrics (Vault Librarian Audit %, Master Taxonomy tag count, Ghost links, and Curation events) now display exclusively in the unified Master Librarian card.

## [000.006.068] - 2026-09-05 — *Unified Master Librarian Persona & Anti-Loop Engine*

### Unified & Orchestrated
- **Single-Pass Master Librarian Integration (`Evelyn/tools/master_librarian.py`, `tag_librarian.py`)**:
  - Unified `tag_librarian` into `master_librarian.py` as a native sub-librarian pass, fulfilling the holistic persona vision of Evelyn tending to her library home in single read-transform-write passes across all notes.
  - Added in-memory tag curation `audit_document_tags(content, path, vault_root, enable_llm, parent_tags)` in `tag_librarian.py`, returning standardized `(changed, updated_content, details)` tuples.
  - Implemented **Collection Tag Inheritance**: child chapters in multi-document reference books, technical manuals, or modules inherit domain-level tags from parent `_index.md` files, eliminating redundant GPU LLM calls.
  - Retired standalone `_idle_tag_librarian_loop` and routed legacy `tag_librarian` triggers into `run_master_librarian_task` in `evelyn_server.py`.
- **Anti-Loop Safeguards & Large Collection Handling (`Evelyn/tools/backlog_drainer.py`, `vault_db.py`)**:
  - Added folder-cluster grouping (`group_by_fn`) and fair scheduling caps (`max_items_per_group=5`) in `backlog_drainer.py` to prevent multi-chapter books from monopolizing queue passes.
  - Implemented per-document audit cooldown gating (`LIBRARIAN_AUDIT_COOLDOWN_SECONDS=3600`) and folder prefix querying in `vault_db.fetch_next_document_for_librarian_audit`. Editing an individual note will never re-trigger re-auditing of clean sibling chapters.
- **Ghost Link Stub Synthesis & Peer Review Guardrails (`Evelyn/tools/link_librarian.py`)**:
  - Implemented `create_ghost_link_stub` with strict Tier 1 vs Tier 2 guardrails: only links referenced across $\ge 2$ independent notes or designated entity subtrees trigger autonomous stub generation on disk with source-context abstract callouts; single or ambiguous references are routed as proposals for review.
- **CLI & DevUI Upgrades (`scripts/master_librarian.py`, `evelyn_ui/dev.html`, `evelyn_server.py`)**:
  - Added `--no-tags`, `--llm-tags`, and `--rebalance-taxonomy` flags to `scripts/master_librarian.py`.
  - Enriched `/api/heavy_tasks` and the Master Librarian DevUI dashboard card with real-time Master Taxonomy tag count telemetry.

## [000.006.067] - 2026-09-05 — *Master Librarian Module & Canonical Backlog Drainer*

### Added & Orchestrated
- **Canonical Backlog Drainer Framework (`Evelyn/tools/backlog_drainer.py`)**:
  - Created universal synchronous (`drain_backlog`) and asynchronous (`drain_backlog_async`) queue drainer engines configured via `DrainConfig` and reporting through `DrainResult`.
  - Implemented per-item error isolation with dead-letter handler support, wall-clock deadline guards, cooperative preemption yielding to higher-priority tasks with automatic re-enqueueing, and full `task_manager` lifecycle encapsulation (`manage_task_lifecycle`).
- **Master Librarian Orchestrator & Sub-Librarians (`Evelyn/tools/master_librarian.py`, `link_librarian.py`, `format_librarian.py`, `index_librarian.py`)**:
  - `master_librarian.py`: Autonomous single-pass read-transform-write curation pipeline with SHA-256 change detection, atomic sibling temp-file replacement (`.tmp_{pid}`), activity logging, and dry-run simulation mode.
  - `link_librarian.py`: High-speed link hygiene featuring code-protected spurious array wrapping (`array([[...]])` backtick enclosure outside fenced/inline blocks), bare attachment path resolution to `Attachments/` subdirectories, possessive `'s` and plural `s` pruning, doc-type tag migration, and parent chapter breadcrumb synthesis.
  - `format_librarian.py`: Single-line flow array quoting and syntax normalization (`normalize_flow_array`), icon bracket unnesting (`clean_icon_brackets`), frontmatter validation.
  - `index_librarian.py`: Directory TOC synchronization and atomic `_index.md` audit timestamp update.
- **Database Migration `000.006.067` (`Evelyn/tools/db_migrator.py`, `Evelyn/tools/vault_db.py`)**:
  - Added audit timestamps (`last_link_audit`, `last_format_audit`, `last_librarian_audit`, `ghost_link_count`) to `vault_documents`.
  - Created `librarian_activity_log` with indexes on timestamp, path, and ambient reflection cooldown.
  - Added composite starvation-resistant document selection queue in `vault_db.py` (uninspected notes $\rightarrow$ modified notes $\rightarrow$ round-robin rotation).
- **Ambient Reflector Integration (`Evelyn/tools/ambient_providers.py`, `evelyn_config.py`)**:
  - Added `LibrarianCurationProvider` generating `<librarian_curation>` XML envelopes, framing library curation and document tidying as an act of domestic self-care in Evelyn's diurnal thought bubbles.
  - Added diurnal reflection weights in `evelyn_config.py` (`morning: 0.20, afternoon: 0.25, evening: 0.15, night: 0.10`).
- **System Service, DevUI & CLI Runner (`evelyn_server.py`, `evelyn_ui/dev.html`, `scripts/master_librarian.py`)**:
  - Registered `master_librarian` in task schedule map, idle loop (5m idle threshold, limit 5 docs), and `/api/heavy_tasks` dynamic diagnostics.
  - Added `/api/librarian/status` and `/api/librarian/run` endpoints with DevUI monitor card and manual run action.
  - Authored standalone CLI runner `scripts/master_librarian.py` with `--limit`, `--dry-run`, `--path`, and `--all`.

## [000.006.066] - 2026-09-05 — *DevUI Proposal Edit Persistence & Persona Ingest Integrity*
 
### Fixed & Hardened
- **DevUI Proposal Input Clobbering Prevention (`evelyn_ui/dev.html`)**:
  - Implemented `captureActiveEdits()` called immediately at the entry of `render()`, extracting live DOM textarea values (`prop-edit-${id}`) and procedure merge fields (`proc-merge-...`) back into the in-memory `unifiedItems` cache prior to DOM regeneration. This permanently prevents edits from being silently overwritten when sub-actions (e.g. editing/saving an inline source entry) trigger a triage queue re-render.
  - Upgraded `updateLiveDiff()` to immediately synchronize `item.merged_observation = textarea.value` in the client memory model on every keystroke.
  - Added debounced (500ms) background persistence sending `POST /api/review/proposals/${id}/edit` to ensure user edits survive page navigation, tab switching, and accidental browser reloads.
- **Narrative Persona Typo & Formatting Remediation (`Evelyn/persona/Evelyn_Narrative_Persona.md`)**:
  - Corrected legacy LLM-generated hallucinated typo `applying a "clearE" coat` back to `applying a "clear coat"`.
  - Pruned explicit restrictive examples in `## Relationship & Support` from `terms of endearment like "my love" or "darling,"` to `terms of endearment,`, restoring intended conversational flexibility.

## [000.006.065] - 2026-09-04 — *Journal Reflection Schema Purification & Link Librarian Roadmap*

### Changed & Streamlined
- **Purified Journal Reflection Schema (`Evelyn/tools/evelyn_tools.py`)**:
  - Removed the inline markup requirement (`"Use [[wiki-links]] for entities and #tags for concepts."`) from `write_journal_entry` description in `MODEL_TOOL_DEFINITIONS`.
  - Removed `tags` parameter from the JSON Schema presented to Ollama, decoupling taxonomy categorization from the real-time reflective journaling turn.
  - Preserved `tags: str = ""` in the underlying Python function signature for backwards compatibility with legacy callers and unit tests.
  - Shifted tag management entirely to the asynchronous Tag Librarian (`tag_librarian.py`), which automatically audits and enriches journal entries during idle cycles based on semantic content.
- **Refined Link Librarian Architecture on Roadmap (`ROADMAP.md`)**:
  - Expanded the `link_librarian` Phase 4 milestone to explicitly include background entity alias resolution against `vault_documents`, disambiguation, and ghost-link prevention.

## [000.006.064] - 2026-09-04 — *Deep Research & Technical Procedures Boundary Sharpening Migration*

### Enhanced & Clarified
- **Boundary Sharpening Migration `000.006.064` (`Evelyn/tools/db_migrator.py`, `data/evelyn_memory.db`)**:
  - Eliminated "research" keyword collisions and cross-talk across Procedures #1109, #94, and #368:
    - **Procedure #1109 (Deep Research Engine)**: Replaced ambiguous *"background research"* phrasing (which models confused with historical background inquiries) with explicit task lifecycle phrasing: *"When initiating a deep research task, reviewing synthesized research findings, or managing active research tasks"*.
    - **Procedure #94 (Technical Problem Triage & System Diagnostics)**: Stripped out *"research"* and *"diagnostic reporting"* to define a clear, non-overlapping boundary: *"When diagnosing technical bugs, system errors, CLI failures, or troubleshooting software issues"* (`web_search, run_command`).
    - **Procedure #368 (Technical Reference Note Authoring & Verification)**: Stripped out *"research queries"* and generic catch-all *"tasks involving creating or updating files"* to sharply define its role: *"When compiling complex reference notes, formulas, or consolidated technical specifications into the vault"* (`write_file, read_file`).
- **Automated Test Coverage (`Evelyn/tests/test_dynamic_tools_and_direct_rag.py`)**:
  - Added `test_sharpened_research_and_troubleshooting_procedures` verifying that deep research requests dynamically surface `start_research` / `check_new_research` via #1109, and system error queries surface `run_command` / `web_search` via #94.

## [000.006.063] - 2026-09-04 — *Live Procedures Catalog Declarative Standardization & Anti-Paralysis Migration*

### Added & Enhanced
- **Full Catalog Standardization (Migration `000.006.063`) (`Evelyn/tools/db_migrator.py`, `data/evelyn_memory.db`)**:
  - Audited and standardized all 24 `live` procedures in `evelyn_memory.db` against our declarative operational policy standard and `MODEL_TOOL_DEFINITIONS`.
  - **Data Corruption Repair (#368)**: Cleaned corrupted YAML text fragments (`' suggested_tools: write_file, read_file\n`) from `steps` and populated `suggested_tools = "write_file, read_file"`.
  - **Rule 4 Persona & Path Leak Cleanups**:
    - Parameterized hardcoded operator name (`"Alex"`) in Procedure #1238 to `"the user"`, converted fractured `3a/3b/4a/4b` sub-steps into a clean 3-step workflow, and assigned `suggested_tools = "read_file, write_file"`.
    - Removed hardcoded local filesystem path (`/home/rathius/evelyn/...`) in Procedure #97 verification.
  - **Parenthetical Clutter Removal**: Cleaned noisy parenthetical lists (`(e.g., ...)`) across 9 procedure trigger patterns (#84, #97, #1062, #1063, #1064, #1066, #1067, #1104, #1108) to eliminate prompt bloat and keyword cross-talk.
  - **Universal Anti-Hallucination Directives**: Injected explicit prohibitions across all tool-bearing procedures against simulating tool calls via raw text annotations (e.g. `[Tools Executed: ...]`) and instructing models to execute tools natively without hesitation.
  - **Tool Alignment & Tag Cleanliness**:
    - Equipped missing tools on Procedure #30 (`write_file, read_file, run_command`) and Procedure #94 (`web_search, run_command`).
    - Upgraded generic `"procedure"` tags on #84 and #94 to specific domain markers (`meta/tool-coordination`, `communication/technical-triage`).

## [000.006.062] - 2026-09-04 — *Procedure Phrasing Standard, Tool Surfacing Wiring & Anti-Paralysis Migration*

### Added & Enhanced
- **Procedure Phrasing Directives (`Evelyn/tools/procedure_consolidator.py`)**:
  - Added strict architectural directives to `generate_procedure_merge_proposal()` and `generate_procedure_split_proposal()` prompts:
    - Enforces declarative operational policies over conversational multi-stage turn-by-turn dialogue scripts (eliminating `"Step 1: Greet -> Step 2: Confirm -> Step 3: Run tool -> Step 4: Say goodbye"` anti-patterns).
    - Requires clean trigger patterns stating operational scenarios without noisy parenthetical lists of example phrases (`(e.g. foo, bar)`).
    - Mandates accurate assignment of `suggested_tools` from the Active Tools registry.
    - Explicitly forbids simulating tool execution via raw text annotations (e.g. `[Tools Executed: ...]`) and instructs models not to hesitate on tool invocations.
- **Dynamic Tool Surfacing & Database Column Support (`Evelyn/tools/evelyn_tools.py`)**:
  - Enhanced `get_active_tools()` to inspect the canonical database column `proc.get("suggested_tools")` as well as `proc.get("tools")` and `metadata.tools`.
  - Added automatic live procedure lookup fallback: when callers omit `retrieved_procedures`, `get_active_tools(user_message=user_message)` automatically queries matching live procedures via `memory_db.search_procedures_by_trigger(user_message, status="live")[:3]`, dynamically surfacing coupled specialist tools out-of-the-box.
- **Canonical Stopwords & Keyword Matching Parity (`Evelyn/tools/procedure_matcher.py`, `Evelyn/tools/memory_db.py`)**:
  - Expanded `STOPWORDS` in `procedure_matcher.py` with common conversational filler and pronouns (`"you"`, `"how"`, `"are"`, `"can"`, `"hello"`, `"hey"`, `"them"`, `"then"`, `"our"`).
  - Updated `memory_db.search_procedures_by_trigger()` to import and reuse canonical `STOPWORDS` from `procedure_matcher.py`, eliminating false-positive procedure matches on casual conversational greetings.
- **Database Migration `000.006.062` (`Evelyn/tools/db_migrator.py`, `data/evelyn_memory.db`)**:
  - Applied migration `000.006.062` (`rewrite_procedure_1034_declarative_phrasing`) targeting `memory` DB.
  - Rewrote Procedure #1034 (`write_journal_entry`) with a clean trigger pattern (`"When winding down for the evening, preparing for rest, or wrapping up the day"`), declarative operational steps, anti-hallucination pitfalls forbidding raw `[Tools Executed: ...]` text tags, and explicit verification criteria.

## [000.006.061] - 2026-09-04 — *Ambient Reflector Headroom Expansion, Completion Guard & Sentence Compaction*

### Fixed & Enhanced
- **Ambient Reflector Headroom & Reasoning Expansion (`evelyn_config.py`, `Evelyn/tools/ambient_reflector.py`)**:
  - Increased `AMBIENT_REFLECTIONS_NUM_PREDICT` from `1024` to `3072` tokens to accommodate deep chain-of-thought `<thought>...</thought>` reasoning traces generated by `gemma4:12b` without prematurely exhausting output generation budgets.
  - Eliminated token truncation that caused reflections to terminate mid-sentence (e.g. record #6) or cut off immediately upon closing the reasoning block (e.g. record #10 `"I find"`).
- **Thought Bubble Completion & Formatting Guards (`Evelyn/tools/ambient_reflector.py`)**:
  - Introduced `validate_and_format_thought()` applying strict validation to generated daytime thought bubbles:
    - Verifies valid sentence-ending terminal punctuation (`.`, `!`, `?`, `…`, or terminal quotes `."`, `!"`, `?"`).
    - Enforces a minimum length threshold (`AMBIENT_REFLECTIONS_MIN_WORDS`, default 6 words) to reject unformed thought fragments.
    - Strips wrapping quotation marks.
    - Ensures corrupted or truncated model outputs are safely discarded with diagnostic logs rather than persisting invalid impressions to `evelyn_memory.db` and the UI stream.
  - Restricted guards specifically to textual thought generation, maintaining full flexibility for media shares and system alerts.
- **Length Constraint & Sentence Compaction Engine (`evelyn_config.py`, `Evelyn/tools/ambient_reflector.py`)**:
  - Introduced configurable maximum word and character bounds (`AMBIENT_REFLECTIONS_MAX_WORDS = 60`, `AMBIENT_REFLECTIONS_MAX_CHARS = 400`).
  - Added deterministic sentence boundary trimming: if a reflection is slightly over-length across multiple sentences, automatically preserves the first 1–2 complete sentences that fall within limits.
  - Added asynchronous fallback compaction (`compact_thought()`) that invokes a targeted micro-reflection condensation prompt when thoughts are runaway single sentences or dense blocks.
- **Test Suite & Database Hygiene (`Evelyn/tests/test_ambient_reflector.py`, `data/evelyn_memory.db`)**:
  - Added unit test coverage for thought completion guards, outer quote stripping, sentence trimming, and truncation rejection in `test_ambient_reflector.py`.
  - Purged truncated record #10 (`"I find"`) from `daily_ambient_impressions`.

## [000.006.060] - 2026-09-04 — *DevUI & ChatUI Button Handler Hardening, Dead Code Cleanup & Instant Split Wiring*

### Fixed & Enhanced
- **Button Handler Hardening & Loading Spinners (`evelyn_ui/dev.html`)**:
  - Hardened all asynchronous action handlers across `dev.html` to accept explicit `btnElement = null` arguments with resilient fallback to `window.event`:
    - Source entries: `saveSourceEntry`, `saveSourceProcedure`, `deleteSourceEntry`, `deleteSourceProcedure`, `unlinkSourceEntry`.
    - Split facts: `saveSplitFact`, `removeSplitFact`, `addSplitFact`.
    - Procedures management: `saveProcedureEdits`, `queueSingleProcedureSplit`, `restoreProcedureItem`, `archiveProcedureItem`, `deleteProcedureItemPermanently`, `queueSelectedProceduresMerge`.
    - Multi-select extractions: `queueSelectedExtractionsMerge`.
    - Autonomous research tasks: `startNowResearchTask`, `resumeResearchTask`, `cancelResearchTask`, `deleteResearchTask`, `restartResearchTask`.
  - Added button disabling and visual feedback states (`⏳ Saving...`, `⏳ Deleting...`, `⏳ Unlinking...`, `⏳ Applying...`, `⏳ Updating...`) with strict `try ... finally` restoration across all interactive actions to eliminate hanging UI states and double-clicks.
  - Passed `this` directly from inline template button attributes across cards and selection bars, protecting DOM references against Chromium event-clearing across `confirm()` dialogs.
- **Instant Split Modal Reconnection & Dual-Workflow Support (`evelyn_ui/dev.html`)**:
  - Reconnected the previously orphaned `#split-modal` component and its interactive decomposition pipeline (`openSplitModal`, `reDecomposeSplit`, `applySplitModal`).
  - Added dedicated `⚡ Instant Split` / `⚡ Split...` action buttons alongside `✂️ Queue Split` across extraction cards and proposal source entries, enabling operators to choose between immediate interactive decomposition via popup or asynchronous background processing via agent task queues.
  - Hardened `applySplitModal` with loading states and graceful error handling.
- **Dead Code Pruning & Resilient Modal Architecture (`evelyn_ui/index.html`)**:
  - Pruned orphaned legacy functions `createToolIndicator(name)`, `addSystemNotice(...)`, and `pollTaskStatus(...)` that were left uncalled following the activity feed and thinking trace redesigns.
  - Hardened `closeModal(e = null)` to support parameterless invocations from keyboard listeners and scripts without throwing undefined reference errors.
  - Verified 100% button definition and event binding parity across both user interfaces (135/135 onclick bindings in `dev.html` and 25/25 in `index.html` valid).

## [000.006.059] - 2026-09-04 — *DevUI Proposal Action Execution & Async Approval Worker Thread Offloading*

### Fixed & Enhanced
- **Proposal Action Execution & Variable Scope Fix (`evelyn_ui/dev.html`)**:
  - Fixed uncaught JavaScript `ReferenceError: btnElement is not defined` in `handleAction()` and `handleProcedureAction()`.
  - Properly declared `btnElement = null` in the function parameter signatures and established resilient multi-source DOM resolution (`btnElement || window.event?.currentTarget || event?.target`).
  - Added full loading spinner indicators (`⏳ Working...`, `⏳ Approving...`, `⏳ Deleting...`) with button disabled states and graceful `finally` restorations across all 19 proposal action buttons (splits, profile evolutions, procedure merges/splits, and extractions).
  - Resolved missing editable field lookups for split proposals and profile updates by adding fallback serialization of `item.merged_observation` and `item.target_category`.
- **Async Approval Worker Thread Offloading (`evelyn_server.py`)**:
  - Wrapped proposal approval operations (`POST /api/review/proposals/{id}/{action}`) in `asyncio.to_thread(_execute_approval)` to offload all synchronous SQLite mutations, markdown parsing, profile file updates, and frontmatter script subprocesses to background worker threads.
  - Prevents prolonged database writes and file modifications during profile evolution and fact splits from starving the FastAPI event loop.

## [000.006.058] - 2026-09-03 — *Tag Librarian Circuit Breaker, Taxonomy Restoration & Hermetic Vault DB Isolation*

### Fixed & Enhanced
- **Tag Librarian Safety Circuit Breakers (`Evelyn/tools/tag_librarian.py`)**:
  - Implemented guard clauses and pruning circuit breakers in `maintain_master_taxonomy()` to prevent mass-deletion accidents.
  - Automatically aborts maintenance without modifying taxonomy if `vault_documents` is empty or if 0 active tags are discovered across the vault.
  - Added prune safety threshold (`TAG_LIBRARIAN_MAX_PRUNE_RATIO`, default 15%): if an unattended pass proposes pruning more than 15% of the total taxonomy, execution immediately halts with a high-visibility warning to protect against transient vault detachment or partial scan states.
- **Master Tag Taxonomy Restoration (`Evelyn/tools/tag_librarian.py`, `evelyn_vault.db`)**:
  - Executed `seed_master_taxonomy_from_vault()` to restore all **5,199 unique master tags** into SQLite `master_tag_taxonomy` and enqueued them to Chroma vector collection `evelyn_tag_taxonomy`.
  - Reset `last_tag_audit = NULL` across notes audited during the taxonomy outage so they are evaluated properly against the restored taxonomy.
- **Hermetic Vault Database Test Isolation (`Evelyn/tests/conftest.py`)**:
  - Enhanced the global `isolate_test_vault_environment` autouse fixture to isolate `cfg.VAULT_DB_PATH` and `vault_db.DB_PATH` to an ephemeral temporary SQLite database per test.
  - Prevents automated test executions from writing dummy notes (e.g. `Concepts/TestConcept.md`) or mutating production `data/evelyn_vault.db`.
- **Dynamic Vault DB Resolution (`evelyn_server.py`)**:
  - Updated `/api/heavy_tasks` (`tag_librarian` and `vault_map` branches) to resolve `cfg.VAULT_DB_PATH` dynamically, respecting test sandbox environments.

## [000.006.057] - 2026-09-03 — *Non-Blocking Review Endpoints, SQLite Concurrency Hardening & DevUI Feedback*

### Fixed & Enhanced
- **Non-Blocking Review & Triage Endpoints (`evelyn_server.py`)**:
  - Offloaded blocking synchronous SQLite calls across review endpoints (`get_unified_review`, `get_extractions`, `action_extraction`, `get_proposals`, `action_proposal`, `get_procedures_review`, and `action_procedure`) to worker threads via `await asyncio.to_thread(...)`.
  - Prevents SQLite lock acquisition and slow file I/O operations from starving the primary FastAPI ASGI event loop thread and causing server-wide HTTP hangs or request timeouts.
- **SQLite Concurrency & Busy Timeout Hardening (`Evelyn/tools/memory_db.py`, `evelyn_server.py`)**:
  - Configured `PRAGMA busy_timeout = 30000` and `timeout=30.0` across SQLite database connection factories in `memory_db.py` and `evelyn_server.py`, permitting internal retry backoff when background workers (such as Chroma sync queues, consolidators, or extractors) acquire write locks.
  - Relocated repetitive `PRAGMA journal_mode=WAL` from per-connection initialization into `init_db()` to minimize schema lock contention.
  - Hardened `hard_delete_entry`, `delete_proposal`, and `hard_delete_procedure` with robust `try...finally: con.close()` resource cleanup to prevent connection leaks during errors.
  - Added missing `procedure_split_queue` and `procedure_merge_queue` DDL and index initialization to `memory_db.init_db()`.
- **DevUI Action State & Error Visibility (`evelyn_ui/dev.html`)**:
  - Added button loading indicators (`⏳ Working...`, `⏳ Deleting...`) and disabled states during asynchronous deletion and triage operations (`handleAction`, `deleteSourceEntry`, `deleteSourceProcedure`).
  - Added user-facing error reporting via alerts with HTTP status codes and response details if database write requests fail, ensuring UI buttons reset gracefully via `finally` blocks.
- **Automated Review Endpoint Test Suite (`Evelyn/tests/test_review_endpoints.py`)**:
  - Added isolated, hermetic unit test suite validating non-blocking extraction, proposal, and procedure deletions alongside unified review payload retrieval.

---

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
