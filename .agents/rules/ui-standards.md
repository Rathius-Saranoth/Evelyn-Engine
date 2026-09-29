---
title: ui-standards.md
description: Standards, information architecture, and accessibility guidelines for Evelyn web interfaces, with mandatory beginner-friendly hover tooltips.
tags: [ui, ux, style-guide, accessibility, tooltips, standards]
date created: 2026-09-28 19:15:00
date modified: 2026-09-28 19:15:00
---

# Evelyn Web UI Usability & Tooltip Standards

> Navigation: [[README.md]] · [[AGENTS.md]] · [[vault-note-style.md]] · [[vault-tag-taxonomy.md]]

Evelyn's web interfaces (`evelyn_ui/dev.html`, `evelyn_ui/index.html`, `evelyn_ui/taxonomy.html`) serve as the operator's primary workstation for memory curation, taxonomy governance, vault filing, system telemetry, and conversational pairing.

Unlike internal developer scripts, the UI is used by operators who may never inspect the underlying engine source code. Every control, input, metric badge, and filter must be intuitive, discoverable, and self-documenting.

---

## 1. The Human-First Tooltip Mandate ("UI Docstrings")

Just as backend Python functions require comprehensive docstrings explaining parameters and side effects, **every interactive element and metric in the UI requires a beginner-friendly hover tooltip (`title="..."`)**.

### Tooltip Content Guidelines by Element Type

1. **Form Inputs, Textareas, and Dropdowns (`<input>`, `<textarea>`, `<select>`)**:
   - State clearly **what the field controls** in plain English.
   - Explain **expected format** (e.g. comma-separated, kebab-case, path syntax).
   - Clarify **default behavior** if left empty (e.g. `defaults to 'general' subfolder`).
   - Mention the **downstream destination** (e.g. *"Creates Stubs/<Domain Folder>/<Target>.md in your Obsidian vault"* or *"Saved to evelyn_memory.db"*).

2. **Action Buttons (`<button>`)**:
   - State **what action is executed** upon click.
   - Clarify whether the action is **reversible or permanent** (e.g. *"Permanently removes this record from the database"* vs *"Archives proposal"*).
   - Detail any **downstream cascade** (e.g. *"Admits term into master taxonomy and backfills referencing vault notes and fast memory facts"*).

3. **Status Badges & Telemetry Metrics (`<span class="badge">`)**:
   - Translate abstract numbers or technical terms into **operational meaning**.
   - Explain thresholds (e.g. *"Semantic distance 0.28 (high confidence match < 0.35)"*).
   - Clarify fallback states (e.g. *"Multi-Reference LLM Synthesis"* vs *"Deterministic Fallback — compiled from references"*).

4. **Filter Bars & Search Inputs**:
   - Document supported **query syntax and filter tokens** (e.g. `type:stub`, `-type:term`, keywords).
   - Clarify which fields the search engine evaluates (e.g. *"Searches topic, reason, author, observation, and tags"*).

### Tone & Style
- **Plain English**: Avoid raw code variable names (e.g. use *"Executive Abstract"* instead of *"raw payload abstract_yaml string"*).
- **Concise & Instructive**: 1–2 sentences that provide immediate clarity on hover without cluttering the screen.

---

## 2. In-Place Usability Over Raw Serialization

1. **Direct Structured Form Controls**:
   - Never force operators to parse or edit raw serialized strings (JSON payloads, raw XML blocks, unformatted YAML chunks) in a generic text box.
   - Parse payloads into dedicated, labeled inputs (Title, Abstract, Domain, Tags, Category, Steps).
   - If raw XML or JSON is needed for advanced edge cases, place it inside a collapsed `<details>` escape hatch, never as the primary interface.

2. **Live Visual Context & Surrounds**:
   - When displaying harvested text snippets or citations from vault documents, wrap them in ellipsis surrounds (`"… <excerpt> …"`) so the operator knows the quote is embedded in surrounding context.
   - Highlight wiki-link targets (`[[Target]]`) and controlled vocabulary tags (`#tag`) with distinct font styles and accent colors.

---

## 3. Visual Polish & Information Hierarchy

1. **Aesthetic Consistency**:
   - Adhere to the established glassmorphic/carbon dark palette (`#07070a`, `var(--surface)`, `var(--surface2)`, `var(--border)`, `var(--accent)`).
   - Avoid harsh neon default colors; use curated semi-transparent badges (`rgba(r, g, b, 0.15)` with matching colored borders).

2. **Micro-Label Typography**:
   - Field headers and metadata tags must use uppercase micro-labels: `font-size: 11px` or `12px`, `font-weight: 600`, `color: var(--text-dim)`, `letter-spacing: 0.5px`.
   - Primary editable values should have high contrast (`color: #fff` or `color: var(--accent)`).

3. **Progressive Disclosure**:
   - Present primary decision metadata front-and-center (Title, Abstract, Core tags, Match confidence).
   - Place extensive lists (e.g. 10+ harvested mentions, secondary citation lists) in scrollable containers or expandable sections.

---

## 4. Verification Checklist for UI Changes

Before committing any modification to `evelyn_ui/`:
- [ ] Every new `<input>`, `<textarea>`, and `<select>` has an informative `title="..."` attribute and a helpful `placeholder`.
- [ ] Every new `<button>` has a `title="..."` describing what happens when clicked.
- [ ] Every new metric badge or score has a `title="..."` explaining what the metric signifies.
- [ ] Labels are clear and align with established vocabulary (e.g. "Domain Folder", "Executive Abstract", "Target Note").
- [ ] Inputs are responsive and do not overflow container cards.
- [ ] Code passes all 5 stages of `scripts/check_code_hygiene.py`.
