---
description: A structured self-review checklist based on the "Notes to Live By" engineering standards
title: quality-review.md
date created: 2026-04-26 10:18:20
date modified: 2026-05-28 14:58:20
tags: quality, review, guidelines, standards, checklist
---

# Quality Review Workflow

Run this after completing any significant feature or refactor. This review is performed **after** Phase C (Verify) and **before** Phase D (Document) of the Operation Protocol (`.ai-instructions.md` §0).

> [!IMPORTANT]
> Every checkbox below requires **evidence from tool output** — a file read, a grep result, or a command execution. Do NOT check a box from memory or assumption.

## 1. Quality Gates

Walk through each gate for the code you just wrote or modified:

- [ ] **Correct** — Does it do what it claims, fully? Have you re-read the modified file(s) to confirm edits landed?
- [ ] **Elegant** — Does every abstraction earn its cost? Can you name the cost?
- [ ] **Efficient** — Any redundant parsing, unnecessary copies, or hot-loop allocations?
- [ ] **Maintainable** — Will a future reader understand intent without decoding execution?
- [ ] **Responsive** — Did you measure or estimate the latency impact? Is startup affected?

## 2. Operational Disciplines

- [ ] **Hot Paths** — Did you identify which paths are performance-critical? Did they get special attention?
- [ ] **Profiling** — Did you measure before and after? Can you state the delta?
- [ ] **Dependencies** — Did you add any new packages? For each: what does it buy the *user*, and what does it cost?
- [ ] **Resource Budget** — Memory, CPU, startup, network: did any budget move? Is the move justified?

## 3. AI Amplifier Check

- [ ] Review AI-generated code for median-pattern traps: over-abstraction, unnecessary serialization, dependency bloat
- [ ] Confirm no "beautifully structured catastrophe" snuck through
- [ ] Verify no hallucinated function calls or non-existent API endpoints were introduced

## 4. Verification Checkpoint

- [ ] **Re-read every modified file** — Confirm edits are syntactically correct and complete (no truncated functions, no duplicate blocks)
- [ ] **Run the code** if possible — Confirm it executes without errors. If you cannot run it, state this explicitly and explain why.
- [ ] **Frontmatter updated** — `python scripts/update_frontmatter.py` has been run on every file you touched.

## 5. Documentation Integrity

- [ ] **ROADMAP.md** — Did you complete a milestone? Is it marked done with a date?
- [ ] **API Endpoints (`reference/endpoints.md`)** — Have any endpoint contracts, parameter signatures, or return payloads been modified or added? Is the endpoint reference document updated to match exactly?
- [ ] **Engine Architecture Map (`reference/engine_architecture.md`)** — Have any core scripts, background workers, or storage components been introduced or refactored? Is the structural blueprint and Mermaid diagram updated to reflect the new state?
- [ ] **Dependency files** — `requirements.txt` and/or `REQUIREMENTS.md` updated if applicable?

## 6. Verdict

If any gate failed, fix before proceeding. Document trade-offs for intentional violations. Report your findings to the user with specific file names and line references — never with vague summaries.
