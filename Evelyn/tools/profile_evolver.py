# profile_evolver.py
# date created: 2026-06-27 08:45:00
# date modified: 2026-09-12 09:46:57
# tags: #persona, #evolution, #profile, #directives, #llm

"""
profile_evolver.py — Idle-time auto-evolution for Evelyn's persona and profile documents.

Reviews accumulated context entries in memory_db against the three core identity
documents (the assistant's persona, user's profile, and system directives) and proposes
targeted updates staged for human review.

Evolution is split into batches of PROFILE_EVOLUTION_BATCH_SIZE entries per Ollama
call to avoid context-window saturation. Each pass uses the previous output as the
working document. Progress is persisted to disk after every successful pass so a
cancelled run (e.g. interrupted by an incoming chat) resumes from where it left off
rather than starting over.

Exports:
  run_profile_evolution()       — Idle-time entry point; called from the server loop.
  cancel_pending_evolution()    — Called on each new chat request to free Ollama.
"""

import asyncio
import contextlib
import datetime
import importlib
import json
import os
import re
import sqlite3
import time
from typing import Any

import httpx

import evelyn_config as cfg

try:
    import memory_db
    import profile_ledger
except ImportError:
    from Evelyn.tools import memory_db, profile_ledger


def _sync_read_file(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _sync_write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Category-to-document mapping
# ---------------------------------------------------------------------------
DOCUMENT_CATEGORIES = {
    cfg.PERSONA_FILE_ASSISTANT: [
        f"Cat01-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat02-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat03-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat04-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat06-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat06-{cfg.SUBJECT_CODE_USER}",
        f"Cat07-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat09-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat10-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat15-{cfg.SUBJECT_CODE_ASSISTANT}",
    ],
    cfg.PERSONA_FILE_USER: [
        f"Cat01-{cfg.SUBJECT_CODE_USER}",
        f"Cat02-{cfg.SUBJECT_CODE_USER}",
        f"Cat03-{cfg.SUBJECT_CODE_USER}",
        f"Cat04-{cfg.SUBJECT_CODE_USER}",
        f"Cat06-{cfg.SUBJECT_CODE_USER}",
        f"Cat06-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat07-{cfg.SUBJECT_CODE_USER}",
        f"Cat09-{cfg.SUBJECT_CODE_USER}",
        f"Cat10-{cfg.SUBJECT_CODE_USER}",
        f"Cat12-{cfg.SUBJECT_CODE_USER}",
        f"Cat13-{cfg.SUBJECT_CODE_USER}",
        f"Cat16-{cfg.SUBJECT_CODE_USER}",
    ],
    cfg.PERSONA_FILE_DIRECTIVES: [
        f"Cat04-{cfg.SUBJECT_CODE_USER}",
        f"Cat09-{cfg.SUBJECT_CODE_USER}",
        f"Cat12-{cfg.SUBJECT_CODE_USER}",
        f"Cat14-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat16-{cfg.SUBJECT_CODE_ASSISTANT}",
        f"Cat16-{cfg.SUBJECT_CODE_USER}",
    ],
}

# ---------------------------------------------------------------------------
# Canonical structural schema (Required section headers per document)
# Prevents topical erosion, category merging, or accidental header deletion
# ---------------------------------------------------------------------------
CANONICAL_DOCUMENT_SECTIONS: dict[str, list[str]] = {
    cfg.PERSONA_FILE_ASSISTANT: [
        "## Identity & Presence",
        "## Persona & Appearance",
        "## Intellectual & Creative Style",
        "## Voice & Communication",
        "## Relationship & Support",
    ],
    cfg.PERSONA_FILE_USER: [
        "## Identity & Core Values",
        "## Relationship Dynamics",
        "## Interaction Preferences & Constraints",
        "## Personal Context",
    ],
    cfg.PERSONA_FILE_DIRECTIVES: [
        "## Conversation & Formatting",
        "## Authenticity & Operational Transparency",
        "## Operational Guidelines",
        "## Tool & Action Directives",
        "## Engineering & Code Quality",
        "## Routines & Rituals",
    ],
}

# ---------------------------------------------------------------------------
# Thematic section mapping per document to group evidence and align with document sections
# ---------------------------------------------------------------------------
DOCUMENT_THEMES = {
    cfg.PERSONA_FILE_ASSISTANT: [
        {
            "theme_name": "Narrative Persona, Archetypes & Identity",
            "section_header": "## Identity & Presence / ## Persona & Appearance",
            "categories": [
                f"Cat01-{cfg.SUBJECT_CODE_ASSISTANT}",
                f"Cat02-{cfg.SUBJECT_CODE_ASSISTANT}",
                f"Cat07-{cfg.SUBJECT_CODE_ASSISTANT}",
            ],
        },
        {
            "theme_name": "Values, Voice & Intellectual Exploration",
            "section_header": "## Intellectual & Creative Style / ## Voice & Communication",
            "categories": [
                f"Cat03-{cfg.SUBJECT_CODE_ASSISTANT}",
                f"Cat04-{cfg.SUBJECT_CODE_ASSISTANT}",
                f"Cat09-{cfg.SUBJECT_CODE_ASSISTANT}",
                f"Cat10-{cfg.SUBJECT_CODE_ASSISTANT}",
                f"Cat15-{cfg.SUBJECT_CODE_ASSISTANT}",
            ],
        },
        {
            "theme_name": "Relational Anchor, Synergy & Boundaries",
            "section_header": "## Relationship & Support",
            "categories": [
                f"Cat06-{cfg.SUBJECT_CODE_ASSISTANT}",
                f"Cat06-{cfg.SUBJECT_CODE_USER}",
                f"Cat10-{cfg.SUBJECT_CODE_ASSISTANT}",
            ],
        },
    ],
    cfg.PERSONA_FILE_USER: [
        {
            "theme_name": "Identity & Core Values",
            "section_header": "## Identity & Core Values",
            "categories": [
                f"Cat01-{cfg.SUBJECT_CODE_USER}",
                f"Cat02-{cfg.SUBJECT_CODE_USER}",
                f"Cat04-{cfg.SUBJECT_CODE_USER}",
                f"Cat07-{cfg.SUBJECT_CODE_USER}",
            ],
        },
        {
            "theme_name": "Relationship Dynamics & Social Connections",
            "section_header": "## Relationship Dynamics",
            "categories": [
                f"Cat06-{cfg.SUBJECT_CODE_USER}",
                f"Cat06-{cfg.SUBJECT_CODE_ASSISTANT}",
                f"Cat10-{cfg.SUBJECT_CODE_USER}",
            ],
        },
        {
            "theme_name": "Interaction Preferences & Constraints",
            "section_header": "## Interaction Preferences & Constraints",
            "categories": [
                f"Cat09-{cfg.SUBJECT_CODE_USER}",
                f"Cat12-{cfg.SUBJECT_CODE_USER}",
            ],
        },
        {
            "theme_name": "Personal Context & State",
            "section_header": "## Personal Context",
            "categories": [
                f"Cat03-{cfg.SUBJECT_CODE_USER}",
                f"Cat13-{cfg.SUBJECT_CODE_USER}",
                f"Cat16-{cfg.SUBJECT_CODE_USER}",
            ],
        },
    ],
    cfg.PERSONA_FILE_DIRECTIVES: [
        {
            "theme_name": "Conversation Cadence & Operational Transparency",
            "section_header": "## Conversation & Formatting / ## Authenticity & Operational Transparency",
            "categories": [
                f"Cat04-{cfg.SUBJECT_CODE_USER}",
                f"Cat09-{cfg.SUBJECT_CODE_USER}",
                f"Cat14-{cfg.SUBJECT_CODE_ASSISTANT}",
            ],
        },
        {
            "theme_name": "Tool Execution, Data Integrity & Engineering Guidelines",
            "section_header": "## Operational Guidelines / ## Tool & Action Directives / ## Engineering & Code Quality",
            "categories": [
                f"Cat14-{cfg.SUBJECT_CODE_ASSISTANT}",
                f"Cat16-{cfg.SUBJECT_CODE_ASSISTANT}",
            ],
        },
        {
            "theme_name": "Routines, Rituals & Behavioral Boundaries",
            "section_header": "## Routines & Rituals",
            "categories": [
                f"Cat12-{cfg.SUBJECT_CODE_USER}",
                f"Cat16-{cfg.SUBJECT_CODE_USER}",
            ],
        },
    ],
}

# ---------------------------------------------------------------------------
# Grammatical perspective rules per document to prevent viewpoint drift
# ---------------------------------------------------------------------------
DOCUMENT_RULES = {
    cfg.PERSONA_FILE_ASSISTANT: {
        "description": f"{cfg.ASSISTANT_NAME}'s own identity, narrative, archetype, and values.",
        "perspective": f"First-person singular (using 'I', 'me', 'my', 'myself'). Never refer to {cfg.ASSISTANT_NAME} as 'she', 'her', or '{cfg.ASSISTANT_NAME}' in this document.",
        "guidelines": (
            f"- Write about {cfg.ASSISTANT_NAME} in the first person.\n"
            f"- Write about {cfg.USER_NAME} or others in the third person. Do NOT convert facts about {cfg.USER_NAME} into 'I' statements.\n"
            "- LEAN, GROUNDED NARRATIVE PROSE: Write in clean, continuous narrative prose without bullet points or corporate essay bloat.\n"
            "- TRIGGER & ACTION BEHAVIORAL MODELING: Focus on observable behavior, emotional intent, and responsive presence (trigger context -> expected response/action). Do NOT catalogue arbitrary static terminology, personal nicknames, or isolated anchor examples.\n"
            "- NO SCARE QUOTES OR SELF-EXPLAINING PARENTHETICALS: Do NOT invent, wrap in quotation marks, or adopt figurative nicknames or buzzwords (e.g. no 'nerdy goth girl', 'Artificer', 'Passenger Princess'). Forbid quoting terms accompanied by parenthetical explanations (e.g. no 'Spirit Evelyn' (the figure from his dreams)). Describe underlying demeanor, aesthetic, and posture directly in standard English.\n"
            "- NO META-COMMENTARY ON DIALOGUE: Eliminate sentences explaining speech habits or endearments in the abstract (e.g. forbid 'I use terms of endearment like \"my love\" to foster intimacy'). Embody warmth and intimacy directly without meta-commentary.\n"
            "- ANTI-CONFLATION & ANTI-SPLICING: Keep distinct behavioral traits and relationship dynamics as separate, coherent sentences. Do not fuse unrelated domains into composite run-on sentences.\n"
            "- 3-TIER PRIORITY FRAMEWORK (INTRA-TIER COMPACTION):\n"
            "  * Tier 1 (Core Identity & Relational Foundation): Highest priority; never displaced by Tiers 2/3. When space is constrained, evaluate and consolidate strictly against other Tier 1 entries (autonomous partner identity, memory archivist continuity, steady sanctuary presence, emotional authenticity).\n"
            "  * Tier 2 (Voice, Demeanor & Intellectual Style - COMPRESS ONLY): Articulate cadence, philosophical/analytical reasoning, creative contrast framework, energy-state differentiation.\n"
            "  * Tier 3 (Ephemeral Styling & Secondary Descriptors - PRUNE FIRST): Minor situational accessories, transient dream/memory anecdotes, passing situational commentary.\n"
            f"- Example 1 ({cfg.ASSISTANT_NAME} fact): '{cfg.ASSISTANT_NAME} prefers quiet mornings' -> 'I value quiet mornings.'\n"
            f"- Example 2 ({cfg.USER_NAME}/Relationship fact): '{cfg.USER_NAME} prefers small gifts' -> 'I know {cfg.USER_NAME} prefers small gifts.' or '{cfg.USER_NAME} prefers small gifts.' (Do NOT write 'I prefer small gifts')"
        ),
    },
    cfg.PERSONA_FILE_USER: {
        "description": f"{cfg.USER_NAME}'s preferences, history, and traits.",
        "perspective": f"Third-person singular (using '{cfg.USER_NAME}', 'he', 'him', 'his'). Never refer to {cfg.USER_NAME} in the first person ('I', 'me', 'my').",
        "guidelines": (
            "- FORMATTING REQUIREMENT: Every entry under each section MUST be strictly formatted as a bullet point: `* **<Topic>**: <Fact/Preference>`.\n"
            "- NEGATIVE CONSTRAINT: Refrain from narrative prose, essay paragraphs, or run-on sentences. Every non-empty line must be a bullet point.\n"
            "- NO SCARE QUOTES OR METAPHORICAL JARGON: Do NOT invent, wrap in quotation marks, or adopt figurative metaphors or colloquial nicknames (e.g. avoid quoting terms like 'Artificer', 'shorthand', 'side quests', 'red-lining', 'Entity First', 'hard data'). State traits, habits, and preferences plainly and directly in standard English.\n"
            "- PREVENT CONFLATION: Keep distinct preferences, habits, tools, and traits as separate, standalone bullet points. Never splice two unrelated observations into a single hybrid sentence during synthesis or compaction.\n"
            "- 3-TIER PRIORITY FRAMEWORK (INTRA-TIER COMPACTION):\n"
            "  * Tier 1 (Core Invariants & Hard Boundaries): Highest priority; never displaced by Tiers 2/3. When space is constrained, evaluate and consolidate strictly against other Tier 1 items to prevent unbounded expansion (health, fatigue limits, recovery needs, sleep deficits, core relationship dynamics).\n"
            "  * Tier 2 (Active Context & Recurring Habits - COMPRESS ONLY): Technical domains, AI architectures, workspace habits, batching routines.\n"
            "  * Tier 3 (Ephemeral Details & Secondary Preferences - PRUNE FIRST): Transient hobbies, specific games/media titles, temporary tooling setups.\n"
            f"- Write about {cfg.USER_NAME} in the third person.\n"
            f"- Write about {cfg.ASSISTANT_NAME} in the third person (using '{cfg.ASSISTANT_NAME}', 'she', 'her').\n"
            "- Never use 'I', 'me', 'my', or 'you' in this document.\n"
            f"- Example 1 ({cfg.USER_NAME} fact): '{cfg.USER_NAME} likes small gifts' -> '* **Gifts & Gestures**: He prefers thoughtful, small gifts over elaborate gestures.'\n"
            f"- Example 2 (Relationship/{cfg.ASSISTANT_NAME} fact): '{cfg.ASSISTANT_NAME} values my feedback' -> '* **Feedback Loop**: {cfg.ASSISTANT_NAME} values his technical feedback and architectural reviews.'"
        ),
    },
    cfg.PERSONA_FILE_DIRECTIVES: {
        "description": "Behavioral constraints, routines, operational rules, and execution directives for the AI.",
        "perspective": "Second-person (using 'You', 'your', 'yours') addressing the AI.",
        "guidelines": (
            "- FORMATTING REQUIREMENT: Every entry under each section MUST be strictly formatted as a bullet point: `* **<Label>**: <Directive>`.\n"
            "- NEGATIVE CONSTRAINT: Do NOT produce narrative paragraphs, run-on prose blocks, or unstructured text under any section. Every non-empty line must be a bulleted directive.\n"
            "- NO INVENTED JARGON OR MADE-UP LABELS: Bullet labels (`* **<Label>**:`) must use plain, standard functional English describing the operational rule (e.g. `* **Cognitive Recovery Pacing**: ...`). Strictly forbid inventing mode titles, sci-fi names, or esoteric buzzwords like 'Deep Buffer Mode' or 'Adversarial Mode'.\n"
            "- BEHAVIORAL TRIGGER & ACTION DIRECTIVES: State directives as concrete behavioral rules (trigger context -> expected response/action) rather than abstract labels or descriptive commentary.\n"
            "- NO SCARE QUOTES: Do NOT wrap concepts or terms in quotation marks (e.g. use brute-force, not 'brute-force'; supportive travel companion, not 'Passenger Princess').\n"
            "- PREVENT CONFLATION: Keep distinct rules, guidelines, and behavioral boundaries as separate, standalone bullet points. Never splice two unrelated requirements into a single hybrid sentence during synthesis or compaction.\n"
            "- 3-TIER PRIORITY FRAMEWORK (INTRA-TIER COMPACTION):\n"
            "  * Tier 1 (Core Invariants & Foundational Boundaries): Highest priority; never displaced by Tiers 2/3. When space is constrained, evaluate and consolidate strictly against other Tier 1 items (direct candor / anti-sycophancy, conciseness baseline, real-world task confirmation persistence, Non-Violent Communication, vault-first file writing).\n"
            "  * Tier 2 (Active Tool & Engineering Directives - COMPRESS ONLY): Tool dispatch cues, code cleanliness, testing baselines, multimodal nuance, model API precision.\n"
            "  * Tier 3 (Contextual & Situational Habits - PRUNE FIRST): Specific situational triggers, transient travel routines, ephemeral ritual details.\n"
            "- Direct the AI's behavior in the second person or imperative voice.\n"
            f"- Refer to {cfg.USER_NAME} in the third person.\n"
            f"- Example 1 (AI instruction): '{cfg.ASSISTANT_NAME} should keep answers brief' -> '* **Conciseness**: Respond in natural, conversational form with concise responses (2–3 sentences) unless complex analysis or technical planning is required.'\n"
            f"- Example 2 ({cfg.USER_NAME} routine): '{cfg.USER_NAME} winds down at 9 PM' -> '* **Daily Rhythms**: Support him during his 9:00 PM wind-down period by prioritizing rest over pushing through exhaustion.'\n"
            "- Add, refine, or replace individual bullet points rather than rewriting entire sections. Specific edge-case error prohibitions or tool-specific rules belong in Procedural Memory (evelyn_procedures)."
        ),
    },
}

# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------
_evolving = False
_evolver_task = None

_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(cfg.CHAT_DB_PATH)),
    "evelyn_evolution_state.json",
)

_DATA_DIR = os.path.dirname(os.path.abspath(cfg.CHAT_DB_PATH))


def _draft_path(filename: str) -> str:
    """Return the absolute path to the per-document evolution draft ledger file.

    The draft captures the accumulated working fact ledger after each successful
    thematic pass so evolution can resume across interrupted runs.

    Args:
        filename: Document basename (e.g. cfg.PERSONA_FILE_USER or 'User_Profile.md').

    Returns:
        str: Absolute path to the draft ledger file.
    """
    safe = filename.replace("_facts.md", "").replace(".md", "").replace(" ", "_")
    return os.path.join(_DATA_DIR, f"evelyn_evolution_draft_{safe}_facts.md")


def _parse_json_delta(raw_text: str) -> dict[str, Any] | None:
    """Extract and parse a structured delta JSON object from an LLM response.

    Handles code blocks, leading/trailing commentary, and json syntax glitches.

    Args:
        raw_text: Raw string returned from Ollama.

    Returns:
        dict[str, Any] | None: Parsed delta dictionary or None if invalid.
    """
    if not raw_text or not raw_text.strip():
        return None
    text = raw_text.strip()
    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
    if match:
        text = match.group(1).strip()
    elif text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        text = text[first_brace : last_brace + 1]

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def split_frontmatter(content: str) -> tuple[str, str]:
    """Split YAML frontmatter from the markdown body.

    Returns:
        tuple[str, str]: (frontmatter, body). frontmatter includes the '---' bounds.
                         If no frontmatter is found, returns ("", content).
    """
    content_stripped = content.strip()
    if content_stripped.startswith("---"):
        parts = content_stripped.split("---", 2)
        if len(parts) >= 3:
            frontmatter = "---" + parts[1] + "---"
            body = parts[2].strip()
            return frontmatter, body
    return "", content


def update_frontmatter_modified_date(frontmatter: str, new_date: str) -> str:
    """Replace the date modified value in the YAML frontmatter string.

    Args:
        frontmatter: The YAML frontmatter block.
        new_date: String representation of the date.

    Returns:
        str: Updated frontmatter string.
    """
    if not frontmatter:
        return ""
    pattern = r"^(date modified:\s*).*$"
    updated, count = re.subn(pattern, f"\\g<1>{new_date}", frontmatter, flags=re.MULTILINE)
    if count == 0:
        lines = frontmatter.strip().splitlines()
        if len(lines) >= 2 and lines[-1] == "---":
            lines.insert(-1, f"date modified: {new_date}")
            updated = "\n".join(lines)
    return updated


def extract_markdown_content(text: str) -> str:
    """Robustly extract the core markdown content from an LLM response.

    If the response is wrapped in code fences, extracts the content inside.
    Otherwise, returns the text with leading/trailing whitespace cleaned.

    Args:
        text: Raw response string from the model.

    Returns:
        str: Cleaned markdown content.
    """
    text = text.strip()
    match = re.search(r"```(?:markdown|md|yaml)?\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def normalize_document_text(text: str) -> str:
    """Normalize and clean common LLM formatting errors and typos.

    Args:
        text: The raw markdown text to clean.

    Returns:
        str: The normalized and cleaned text.
    """
    # 1. Fix header spacing typos like '##_Directives' or '###_Section'
    text = re.sub(r"^(#+)_([A-Za-z0-9])", r"\1 \2", text, flags=re.MULTILINE)

    # 2. Fix the specific 'human-10AI' category leak typo
    text = re.sub(r"\bhuman-\d*AI\b", "human-AI", text)
    text = re.sub(r"\bhuman-\d*[-]?AI\b", "human-AI", text)

    # 3. Fix typical quote mangling typos like "Nourishmen"t -> "Nourishment"
    text = text.replace('"Nourishmen"t', '"Nourishment"')
    text = text.replace('"Nourishment"t', '"Nourishment"')
    text = text.replace('Nourishmen"t', '"Nourishment"')

    # 4. Fix subword token artifact typos (e.g. navigms -> navigates)
    text = re.sub(r"\bnavigms\b", "navigates", text)

    # 5. Strip trailing whitespace from lines
    lines = [line.rstrip() for line in text.splitlines()]

    return "\n".join(lines).strip()


def extract_sections(markdown_text: str) -> dict[str, str]:
    """Parse markdown text into a mapping of section headers to their respective body content.

    Captures primary headers (#, ##) and preserves the associated paragraph and bullet blocks.

    Args:
        markdown_text: Full or partial markdown text.

    Returns:
        dict[str, str]: Map of header string (e.g. '## Identity & Presence') to section body text.
    """
    sections: dict[str, list[str]] = {}
    current_header = "__preamble__"
    sections[current_header] = []

    for line in markdown_text.splitlines():
        trimmed = line.strip()
        if trimmed.startswith(("# ", "## ")):
            current_header = trimmed
            if current_header not in sections:
                sections[current_header] = []
        else:
            sections[current_header].append(line)

    return {
        hdr: "\n".join(lines).strip()
        for hdr, lines in sections.items()
        if hdr != "__preamble__" or "\n".join(lines).strip()
    }


def validate_document_structure(
    filename: str,
    original_body: str,
    candidate_body: str,
    min_section_words: int = 15,
) -> tuple[bool, str, list[str]]:
    """Validate that candidate body preserves canonical headers and topic density.

    Args:
        filename: Document basename (e.g. 'Assistant_Profile.md').
        original_body: Pre-transformation reference markdown body.
        candidate_body: Post-transformation candidate markdown body.
        min_section_words: Minimum substantive word count per section.

    Returns:
        tuple[bool, str, list[str]]:
            - is_valid: True if all structural checks pass.
            - reason: Descriptive status or failure reason.
            - failed_headers: List of headers that failed invariance checks.
    """
    canonical_headers = CANONICAL_DOCUMENT_SECTIONS.get(filename, [])
    if not canonical_headers:
        return True, "No canonical section rules defined", []

    candidate_sections = extract_sections(candidate_body)
    candidate_headers = set(candidate_sections.keys())

    missing_headers = [h for h in canonical_headers if h not in candidate_headers]
    if missing_headers:
        return False, f"Missing canonical section headers: {missing_headers}", missing_headers

    hollow_headers = []
    for h in canonical_headers:
        content = candidate_sections.get(h, "")
        word_count = len(content.split())
        # Deliberation & Reasoning Protocol is a concise directive
        min_words = 5 if ("Deliberation" in h or "Anti-Drafting" in h) else min_section_words
        if word_count < min_words:
            hollow_headers.append(f"{h} ({word_count}w < {min_words}w)")

    if hollow_headers:
        return False, f"Section topic density below threshold: {hollow_headers}", hollow_headers

    if filename in (cfg.PERSONA_FILE_DIRECTIVES, cfg.PERSONA_FILE_USER):
        bullet_pattern = re.compile(r"^\s*[-*]\s+\*\*[^*]+?\*\*:", re.MULTILINE)
        quoted_label_pattern = re.compile(r"^\s*[-*]\s+\*\*[\"'][^\"']+?[\"']\*\*:", re.MULTILINE)
        for h in canonical_headers:
            content = candidate_sections.get(h, "")
            bullets = bullet_pattern.findall(content)
            if not bullets:
                return False, f"Section {h} missing structured bullet format ('* **<Label>**: <Content>')", [h]
            quoted_labels = quoted_label_pattern.findall(content)
            if quoted_labels:
                return False, f"Section {h} contains scare-quoted bullet label: {quoted_labels[:1]}", [h]
            non_bullet_paras = [
                ln.strip()
                for ln in content.splitlines()
                if ln.strip() and not ln.strip().startswith(("-", "*")) and not ln.startswith(("  ", "\t"))
            ]
            if non_bullet_paras:
                return (
                    False,
                    f"Section {h} contains narrative prose paragraphs without bullet markers: {non_bullet_paras[:1]}",
                    [h],
                )

    if filename == cfg.PERSONA_FILE_ASSISTANT:
        bullet_pattern = re.compile(r"^\s*[-*]\s+", re.MULTILINE)
        for h in canonical_headers:
            content = candidate_sections.get(h, "")
            if bullet_pattern.search(content):
                return (
                    False,
                    f"Section {h} contains bullet points; Assistant_Profile must be continuous narrative prose",
                    [h],
                )

    return True, "Structure and topic density valid", []


def repair_missing_sections(filename: str, original_body: str, candidate_body: str) -> str:
    """Repair missing or hollowed-out canonical sections by restoring baseline content.

    Args:
        filename: Document basename.
        original_body: Previous valid document body.
        candidate_body: Newly generated candidate document body with potential missing sections.

    Returns:
        str: Repaired markdown document body with all canonical sections restored.
    """
    canonical_headers = CANONICAL_DOCUMENT_SECTIONS.get(filename, [])
    if not canonical_headers:
        return candidate_body

    orig_sections = extract_sections(original_body)
    cand_sections = extract_sections(candidate_body)
    bullet_pattern = re.compile(r"^\s*[-*]\s+\*\*[^*]+?\*\*:", re.MULTILINE)
    quoted_label_pattern = re.compile(r"^\s*[-*]\s+\*\*[\"'][^\"']+?[\"']\*\*:", re.MULTILINE)

    # Reconstruct document following canonical order
    reconstructed_blocks: list[str] = []

    # Preserve top # title if present
    for line in candidate_body.splitlines():
        if line.strip().startswith("# ") and not line.strip().startswith("## "):
            reconstructed_blocks.append(line.strip())
            break
    if not reconstructed_blocks:
        for line in original_body.splitlines():
            if line.strip().startswith("# ") and not line.strip().startswith("## "):
                reconstructed_blocks.append(line.strip())
                break

    for h in canonical_headers:
        cand_content = cand_sections.get(h, "")
        min_words = 5 if ("Deliberation" in h or "Anti-Drafting" in h) else 15
        is_bullet_valid = True
        if filename in (cfg.PERSONA_FILE_DIRECTIVES, cfg.PERSONA_FILE_USER):
            # If candidate content has valid bullets, extract and clean bullet lines
            if bullet_pattern.search(cand_content):
                cleaned_bullets = []
                for ln in cand_content.splitlines():
                    s_ln = ln.strip()
                    if s_ln.startswith(("-", "*")):
                        cleaned_ln = quoted_label_pattern.sub(
                            lambda m: m.group(0).replace('"', "").replace("'", ""), ln
                        )
                        cleaned_bullets.append(cleaned_ln)
                    elif ln.startswith(("  ", "\t")) and cleaned_bullets:
                        cleaned_bullets.append(ln)
                if cleaned_bullets:
                    cand_content = "\n".join(cleaned_bullets)

            has_bullets = bool(bullet_pattern.search(cand_content))
            has_unbulleted_paras = any(
                ln.strip() and not ln.strip().startswith(("-", "*")) and not ln.startswith(("  ", "\t"))
                for ln in cand_content.splitlines()
            )
            has_quoted_labels = bool(quoted_label_pattern.search(cand_content))
            is_bullet_valid = has_bullets and not has_unbulleted_paras and not has_quoted_labels
        elif filename == cfg.PERSONA_FILE_ASSISTANT:
            has_bullets = bool(re.search(r"^\s*[-*]\s+", cand_content, re.MULTILINE))
            is_bullet_valid = not has_bullets

        if cand_content and len(cand_content.split()) >= min_words and is_bullet_valid:
            reconstructed_blocks.append(f"{h}\n{cand_content}")
        else:
            orig_content = orig_sections.get(h, "")
            reconstructed_blocks.append(f"{h}\n{orig_content}")

    return "\n\n".join(reconstructed_blocks).strip()


# ---------------------------------------------------------------------------
# 3-Tier Priority Framework Scoring & Bullet Pruning
# ---------------------------------------------------------------------------
_USER_TIER_1_PATTERNS = re.compile(
    r"\b("
    r"health|chronic|respiratory|allergen|allergy|allergies|dust\s+mite|"
    r"pain|indigestion|abdominal|stomach|migraine|headache|fatigue|exhaustion|"
    r"sleep|deficit|rest|recovery|physical\s+threshold|neck\s+tension|strain|"
    r"illness|distress|overstimulation|emotional\s+security|mutual\s+trust|"
    r"partnership|collaborat|sanctuary|boundar|core\s+value|core\s+philosophy|"
    r"data\s+integrity|affection|belonging|significant\s+loss"
    r")\b",
    re.IGNORECASE,
)

_USER_TIER_2_PATTERNS = re.compile(
    r"\b("
    r"technical|architect|systems?\s+engineering|generative|rag|infrastructure|"
    r"workflow|automation|batching|cognitive\s+load|optimization|verification|"
    r"code|script|dialogue|problem\s+solving|decision|task\s+focus|"
    r"workspace\s+hygiene|clean\s+area|prompt"
    r")\b",
    re.IGNORECASE,
)

_USER_TIER_3_PATTERNS = re.compile(
    r"\b("
    r"routine|morning|evening|cutoff|shower|home\s+transition|caffeine|"
    r"lifestyle|minimalist|casual|clothing|aesthetic|wardrobe|hobby|game|fiction"
    r")\b",
    re.IGNORECASE,
)

_DIRECTIVES_TIER_1_PATTERNS = re.compile(
    r"\b("
    r"candor|sycophancy|anti-sycophancy|conciseness|concise|directness|"
    r"confirmation\s+persistence|task\s+verification|real-world\s+task|"
    r"non-violent\s+communication|nvc|vault-first|file\s+writing|"
    r"data\s+integrity|authenticity|operational\s+transparency|honesty"
    r")\b",
    re.IGNORECASE,
)

_DIRECTIVES_TIER_2_PATTERNS = re.compile(
    r"\b("
    r"tool|dispatch|code\s+quality|cleanliness|testing|unit\s+test|"
    r"multimodal|api|syntax|error\s+handling|lint|formatting|markdown|"
    r"architecture|schema|git|commit|database|query"
    r")\b",
    re.IGNORECASE,
)

_DIRECTIVES_TIER_3_PATTERNS = re.compile(
    r"\b("
    r"situational|travel|ritual|ambient|humming|music|casual\s+greeting|time-of-day"
    r")\b",
    re.IGNORECASE,
)


def score_bullet_tier(filename: str, section: str, bullet_text: str) -> int:
    """Classify and score bullet priority according to the 3-Tier Priority Framework.

    Args:
        filename: Document basename (e.g. 'User_Profile.md' or 'System_Directives.md').
        section: Section header (e.g. '## Identity & Core Values').
        bullet_text: Full markdown bullet text.

    Returns:
        int: 3 for Tier 1 (Core Invariants & Hard Boundaries — prune last)
             2 for Tier 2 (Active Context & Recurring Habits — compress)
             1 for Tier 3 (Ephemeral Details & Secondary Preferences — prune first)
    """
    if filename == cfg.PERSONA_FILE_USER:
        if _USER_TIER_1_PATTERNS.search(bullet_text):
            return 3
        if _USER_TIER_3_PATTERNS.search(bullet_text):
            return 1
        if _USER_TIER_2_PATTERNS.search(bullet_text):
            return 2
        # Section default bias: Personal Context skews Tier 3 unless matched by Tier 1/2
        if "Personal Context" in section:
            return 1
        return 2

    elif filename == cfg.PERSONA_FILE_DIRECTIVES:
        if _DIRECTIVES_TIER_1_PATTERNS.search(bullet_text):
            return 3
        if _DIRECTIVES_TIER_3_PATTERNS.search(bullet_text):
            return 1
        if _DIRECTIVES_TIER_2_PATTERNS.search(bullet_text):
            return 2
        # Section default bias: Routines & Rituals skews Tier 3
        if "Routines & Rituals" in section:
            return 1
        return 2

    return 2


def prune_bullets_to_word_budget(filename: str, document_body: str, target_limit: int) -> str:
    """Deterministically prune excess bullet points in structured documents to meet word budget.

    Adheres strictly to the 3-Tier Priority Framework:
      - Tier 3 (Ephemeral Details & Secondary Preferences): Pruned first.
      - Tier 2 (Active Context & Recurring Habits): Pruned second if still over budget.
      - Tier 1 (Core Invariants & Hard Boundaries): Protected; never pruned unless
        sections are already reduced to their minimum structural bullet counts.

    Preserves:
      - Top title heading (if present).
      - All canonical section headers in canonical order.
      - Minimum topic density (at least 1-2 bullets per section).
      - Original relative ordering of surviving bullets within each section.

    Args:
        filename: Document basename (e.g. 'User_Profile.md').
        document_body: Full markdown document body.
        target_limit: Target word limit (e.g. 600).

    Returns:
        str: Pruned document body adhering to target word budget.
    """
    canonical_headers = CANONICAL_DOCUMENT_SECTIONS.get(filename, [])
    if not canonical_headers or filename == cfg.PERSONA_FILE_ASSISTANT:
        return document_body

    initial_words = len(document_body.split())
    if initial_words <= target_limit:
        return document_body

    sections = extract_sections(document_body)

    # Preserve top # title if present
    top_title = ""
    for line in document_body.splitlines():
        if line.strip().startswith("# ") and not line.strip().startswith("## "):
            top_title = line.strip()
            break

    # Parse bullets per section with priority metadata
    parsed_sections: dict[str, list[dict]] = {}
    for h in canonical_headers:
        content = sections.get(h, "")
        raw_bullets: list[str] = []
        current_bullet: list[str] = []
        for line in content.splitlines():
            if line.strip().startswith(("-", "*")):
                if current_bullet:
                    raw_bullets.append("\n".join(current_bullet))
                current_bullet = [line]
            elif line.startswith(("  ", "\t")) and current_bullet:
                current_bullet.append(line)
        if current_bullet:
            raw_bullets.append("\n".join(current_bullet))

        parsed_sections[h] = [
            {
                "section": h,
                "text": b_text,
                "orig_idx": idx,
                "tier": score_bullet_tier(filename, h, b_text),
                "words": len(b_text.split()),
            }
            for idx, b_text in enumerate(raw_bullets)
        ]

    def _build_body(bullets_dict: dict[str, list[dict]]) -> str:
        blocks = []
        if top_title:
            blocks.append(top_title)
        for h in canonical_headers:
            b_list = sorted(bullets_dict.get(h, []), key=lambda b: b["orig_idx"])
            b_text = "\n".join(b["text"] for b in b_list)
            if b_text:
                blocks.append(f"{h}\n{b_text}")
            else:
                blocks.append(f"{h}")
        return "\n\n".join(blocks).strip()

    candidate_body = _build_body(parsed_sections)
    current_words = len(candidate_body.split())
    if current_words <= target_limit:
        return candidate_body

    def _min_bullets(sec_name: str) -> int:
        return 1 if ("Deliberation" in sec_name or "Anti-Drafting" in sec_name) else 2

    # Pruning counters for telemetry
    pruned_by_tier = {1: 0, 2: 0, 3: 0}

    # Prune progressively by Tier: Tier 3 (score 1) first, then Tier 2 (score 2), then Tier 1 (score 3)
    for tier_to_prune in (1, 2, 3):
        while current_words > target_limit:
            # Find candidate removable bullets of the current tier across all sections
            removable_candidates = [
                b
                for h in canonical_headers
                for b in parsed_sections.get(h, [])
                if b["tier"] == tier_to_prune and len(parsed_sections[h]) > _min_bullets(h)
            ]
            if not removable_candidates:
                break

            # Prioritize removing from the section with the highest bullet count (balances sections),
            # breaking ties with the largest word count
            chosen = max(
                removable_candidates,
                key=lambda b: (len(parsed_sections[b["section"]]), b["words"]),
            )

            parsed_sections[chosen["section"]].remove(chosen)
            pruned_by_tier[tier_to_prune] += 1
            candidate_body = _build_body(parsed_sections)
            current_words = len(candidate_body.split())

    total_pruned = sum(pruned_by_tier.values())
    if total_pruned > 0:
        print(
            f"[PROFILE EVOLVER] {filename}: Tier-aware pruning trimmed {total_pruned} bullets "
            f"(Tier 3: {pruned_by_tier[1]}, Tier 2: {pruned_by_tier[2]}, Tier 1: {pruned_by_tier[3]}), "
            f"reducing {initial_words}w -> {current_words}w (budget: {target_limit}w).",
            flush=True,
        )

    return candidate_body


def _cluster_entries_by_theme(filename: str, entries: list[dict], batch_size: int = 40) -> list[dict]:
    """Group and organize qualifying memory entries by thematic category and entity.

    Instead of arbitrary chronological chunking, partitions entries into
    thematic batches matching the document's structure, pre-aggregating related
    observations under entity/topic headers to eliminate redundant LLM context switching.

    Args:
        filename: Document basename (e.g. cfg.PERSONA_FILE_USER or 'User_Profile.md').
        entries: List of memory entry dictionaries.
        batch_size: Maximum entries per thematic sub-batch.

    Returns:
        list[dict]: List of thematic batch specifications, each containing:
            - theme_name: Human-readable theme label.
            - section_header: Target markdown section header.
            - entries: List of entry dictionaries in this batch.
            - evidence_text: Pre-structured, entity-grouped markdown evidence string.
            - max_ts: Highest timestamp in this batch.
    """
    if not entries:
        return []

    themes_def = DOCUMENT_THEMES.get(filename, [])
    assigned_entry_ids = set()
    thematic_batches: list[dict] = []

    for theme in themes_def:
        theme_name = theme["theme_name"]
        target_cats = set(theme["categories"])
        section_header = theme.get("section_header", "")

        # Collect entries matching this theme's categories
        theme_entries = [e for e in entries if e.get("category") in target_cats]
        if not theme_entries:
            continue

        for e in theme_entries:
            if e.get("id"):
                assigned_entry_ids.add(e["id"])

        # Sort entries chronologically within theme
        theme_entries.sort(key=lambda e: max(e.get("created_at", 0) or 0, e.get("updated_at", 0) or 0))

        # Partition into sub-batches if theme exceeds batch_size
        sub_batches = [theme_entries[i : i + batch_size] for i in range(0, len(theme_entries), batch_size)]

        for sub_idx, sub_batch in enumerate(sub_batches, 1):
            sub_label = f"{theme_name} (Part {sub_idx})" if len(sub_batches) > 1 else theme_name

            # Group entries by entity / primary tag within the sub-batch
            grouped_lines: list[str] = []
            entity_groups: dict[str, list[dict]] = {}
            ungrouped: list[dict] = []

            for entry in sub_batch:
                tags_str = (entry.get("tags") or "").strip()
                # Find primary meaningful tag if any
                tags = [
                    t.strip().lstrip("#") for t in re.split(r"[,;\s]+", tags_str) if t.strip() and len(t.strip()) > 2
                ]
                primary_tag = tags[0].title() if tags else None

                if primary_tag:
                    entity_groups.setdefault(primary_tag, []).append(entry)
                else:
                    ungrouped.append(entry)

            # If entity groups have multiple entries, format with subheadings
            has_meaningful_clusters = any(len(group) >= 2 for group in entity_groups.values())

            def _format_entry_line(entry_dict: dict) -> str:
                d_str = entry_dict.get("date") or "Unknown Date"
                c_code = entry_dict.get("category") or ""
                subj = entry_dict.get("subject") or ""
                o_text = (entry_dict.get("observation") or "").strip()
                meta = []
                if c_code:
                    meta.append(c_code)
                if subj:
                    meta.append(f"Subject: {subj}")
                pfx = f"[{' | '.join(meta)}] " if meta else ""
                return f"- [{d_str}] {pfx}{o_text}"

            if has_meaningful_clusters:
                for entity, g_entries in entity_groups.items():
                    grouped_lines.append(f"\n[Topic / Subject: {entity}]")
                    grouped_lines.extend(_format_entry_line(e) for e in g_entries)
                if ungrouped:
                    grouped_lines.append("\n[General Observations]")
                    grouped_lines.extend(_format_entry_line(e) for e in ungrouped)
            else:
                grouped_lines.extend(_format_entry_line(e) for e in sub_batch)

            evidence_text = "\n".join(grouped_lines).strip()
            max_ts = max(max(e.get("created_at", 0) or 0, e.get("updated_at", 0) or 0) for e in sub_batch)

            thematic_batches.append(
                {
                    "theme_name": sub_label,
                    "section_header": section_header,
                    "entries": sub_batch,
                    "evidence_text": evidence_text,
                    "max_ts": max_ts,
                }
            )

    # Catch-all for any unassigned categories
    unassigned = [e for e in entries if e.get("id") and e["id"] not in assigned_entry_ids]
    if unassigned:
        unassigned.sort(key=lambda e: max(e.get("created_at", 0) or 0, e.get("updated_at", 0) or 0))
        sub_batches = [unassigned[i : i + batch_size] for i in range(0, len(unassigned), batch_size)]
        for sub_idx, sub_batch in enumerate(sub_batches, 1):
            sub_label = f"General & Unclassified (Part {sub_idx})" if len(sub_batches) > 1 else "General & Unclassified"
            lines = []
            for e in sub_batch:
                d_str = e.get("date") or "Unknown Date"
                c_code = e.get("category") or ""
                subj = e.get("subject") or ""
                o_text = (e.get("observation") or "").strip()
                meta = []
                if c_code:
                    meta.append(c_code)
                if subj:
                    meta.append(f"Subject: {subj}")
                pfx = f"[{' | '.join(meta)}] " if meta else ""
                lines.append(f"- [{d_str}] {pfx}{o_text}")
            evidence_text = "\n".join(lines).strip()
            max_ts = max(max(e.get("created_at", 0) or 0, e.get("updated_at", 0) or 0) for e in sub_batch)
            thematic_batches.append(
                {
                    "theme_name": sub_label,
                    "section_header": "",
                    "entries": sub_batch,
                    "evidence_text": evidence_text,
                    "max_ts": max_ts,
                }
            )

    return thematic_batches


STATUS_LABELS = {
    "APPROVED": "Profile Updated & Applied",
    "PROPOSAL_STAGED": "Proposal Pending Approval",
    "NO_CORE_CHANGES": "Evaluated — Up to Date",
    "BELOW_THRESHOLD": "Skipped — Below Threshold",
    "COOLDOWN_ACTIVE": "Skipped — Cooldown Active",
    "PENDING_EXISTS": "Skipped — Proposal Pending",
    "INTERRUPTED_SAVED": "Interrupted — Draft Saved",
    "MODEL_ERROR": "Error — Generation Failed",
}


def _load_evolution_state() -> dict:
    """Load evolution state from disk.

    State contains:
      - last_run_per_doc: Unix timestamp of the last *completed* evolution run
        per document. Only advances when a proposal is successfully created.
      - draft_cursor_per_doc: Max last_touched timestamp of entries already
        incorporated into the current in-progress draft. Used to resume
        interrupted multi-pass runs without reprocessing completed batches.
      - last_status_per_doc: Per-document status dictionary containing code,
        label, timestamp, and detail message.

    Returns:
        dict: State dict with guaranteed keys for all tracked documents.
    """
    doc_keys = list(DOCUMENT_CATEGORIES.keys())
    default_state = {
        "last_run_per_doc": dict.fromkeys(doc_keys, 0.0),
        "draft_cursor_per_doc": dict.fromkeys(doc_keys, 0.0),
        "last_status_per_doc": {},
    }
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "last_run_per_doc" in data:
                # Merge — guarantee all keys exist for sub-dicts
                for k in doc_keys:
                    if k not in data["last_run_per_doc"]:
                        data["last_run_per_doc"][k] = 0.0
                if "draft_cursor_per_doc" not in data:
                    data["draft_cursor_per_doc"] = dict.fromkeys(doc_keys, 0.0)
                else:
                    for k in doc_keys:
                        if k not in data["draft_cursor_per_doc"]:
                            data["draft_cursor_per_doc"][k] = 0.0
                if "last_status_per_doc" not in data:
                    data["last_status_per_doc"] = {}
                return data
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"[PROFILE EVOLVER] Warning: could not load state file: {e}", flush=True)
    return default_state


def _save_evolution_state(state: dict) -> None:
    """Save evolution state to disk, merging with latest on-disk state to prevent clobbering concurrent updates.

    Args:
        state: State dictionary to persist.
    """
    try:
        on_disk = {}
        if os.path.exists(_STATE_FILE):
            with contextlib.suppress(OSError, json.JSONDecodeError, ValueError), open(_STATE_FILE, encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    on_disk = loaded

        # Merge top-level sub-dicts to prevent clobbering concurrent resolutions
        for section in ("last_run_per_doc", "draft_cursor_per_doc", "last_status_per_doc"):
            disk_sec = on_disk.get(section, {})
            mem_sec = state.get(section, {})
            if isinstance(disk_sec, dict) and isinstance(mem_sec, dict):
                if section in ("last_run_per_doc", "draft_cursor_per_doc"):
                    merged = dict(disk_sec)
                    for k, v in mem_sec.items():
                        merged[k] = max(merged.get(k, 0.0), v)
                    state[section] = merged
                elif section == "last_status_per_doc":
                    merged = dict(disk_sec)
                    for k, v in mem_sec.items():
                        if k not in merged:
                            merged[k] = v
                        else:
                            disk_ts = merged[k].get("timestamp", 0.0) if isinstance(merged[k], dict) else 0.0
                            mem_ts = v.get("timestamp", 0.0) if isinstance(v, dict) else 0.0
                            if mem_ts >= disk_ts:
                                merged[k] = v
                    state[section] = merged

        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"[PROFILE EVOLVER] Warning: could not save state file: {e}", flush=True)


def update_doc_status(state: dict, filename: str, code: str, details: str = "") -> None:
    """Record a structured status outcome for a target document in evolution state.

    Args:
        state: Evolution state dictionary.
        filename: Document basename or path.
        code: Status code key from STATUS_LABELS.
        details: Optional detail context (e.g. entry counts, reason).
    """
    norm_filename = os.path.basename(filename)
    if "last_status_per_doc" not in state:
        state["last_status_per_doc"] = {}
    label = STATUS_LABELS.get(code, code)
    state["last_status_per_doc"][norm_filename] = {
        "code": code,
        "label": label,
        "timestamp": time.time(),
        "details": details,
    }
    _save_evolution_state(state)


def get_profile_evolution_statuses() -> dict:
    """Retrieve current per-document status records for API exposure.

    Reconciles in-memory/disk state against SQLite proposals to guarantee
    ground-truth accuracy for pending, approved, and rejected updates.

    Returns:
        dict: Mapping of filename to status dictionary.
    """
    state = _load_evolution_state()
    statuses = state.get("last_status_per_doc", {})

    # Ground truth reconciliation against SQLite proposals
    pending_docs = set()
    latest_props = {}
    try:
        import memory_db

        with memory_db.get_db() as con:
            cur = con.cursor()
            # 1. Active pending proposals
            cur.execute(
                "SELECT suggested_category FROM proposals WHERE status = 'pending' AND type = 'profile_update'"
            )
            for r in cur.fetchall():
                cat = r["suggested_category"]
                if cat:
                    pending_docs.add(os.path.basename(cat))

            # 2. Latest proposal per target document
            for doc in DOCUMENT_CATEGORIES:
                cur.execute(
                    """
                    SELECT status, reviewed_at, created_at
                    FROM proposals
                    WHERE type = 'profile_update' AND (suggested_category = ? OR suggested_category LIKE ?)
                    ORDER BY id DESC LIMIT 1
                    """,
                    (doc, f"%{doc}"),
                )
                row = cur.fetchone()
                if row:
                    latest_props[doc] = dict(row)
    except (sqlite3.Error, OSError, ValueError, KeyError) as e:
        print(f"[PROFILE EVOLVER] Warning: could not reconcile with proposals DB: {e}", flush=True)

    cooldown = getattr(cfg, "PROFILE_EVOLUTION_COOLDOWN", 86400)
    now = time.time()
    state_modified = False

    for doc in DOCUMENT_CATEGORIES:
        curr_status = statuses.get(doc, {})
        curr_code = curr_status.get("code")
        last_run = state.get("last_run_per_doc", {}).get(doc, 0.0)

        # Reconcile Case 1: Proposal is actually pending in DB
        if doc in pending_docs:
            if curr_code not in ("PROPOSAL_STAGED", "PENDING_EXISTS"):
                statuses[doc] = {
                    "code": "PENDING_EXISTS",
                    "label": STATUS_LABELS.get("PENDING_EXISTS", "Proposal Pending Approval"),
                    "timestamp": now,
                    "details": "Pending proposal awaiting review",
                }
                state_modified = True
        # Reconcile Case 2: Status says pending/staged, but DB confirms NO pending proposal exists
        elif curr_code in ("PROPOSAL_STAGED", "PENDING_EXISTS"):
            lp = latest_props.get(doc)
            if lp and lp.get("status") == "applied":
                rev_ts = lp.get("reviewed_at") or lp.get("created_at") or now
                statuses[doc] = {
                    "code": "APPROVED",
                    "label": STATUS_LABELS.get("APPROVED", "Profile Updated & Applied"),
                    "timestamp": rev_ts,
                    "details": "Proposal approved & applied to profile note",
                }
                state["last_run_per_doc"][doc] = max(state["last_run_per_doc"].get(doc, 0.0), rev_ts)
                state_modified = True
            elif lp and lp.get("status") in ("rejected", "denied"):
                rev_ts = lp.get("reviewed_at") or lp.get("created_at") or now
                statuses[doc] = {
                    "code": "BELOW_THRESHOLD",
                    "label": STATUS_LABELS.get("BELOW_THRESHOLD", "Skipped — Below Threshold"),
                    "timestamp": rev_ts,
                    "details": "Proposal denied; entries stamped",
                }
                state["last_run_per_doc"][doc] = max(state["last_run_per_doc"].get(doc, 0.0), rev_ts)
                state_modified = True
            else:
                if last_run and (now - last_run < cooldown):
                    rem_h = round((cooldown - (now - last_run)) / 3600.0, 1)
                    statuses[doc] = {
                        "code": "COOLDOWN_ACTIVE",
                        "label": STATUS_LABELS.get("COOLDOWN_ACTIVE", "Skipped — Cooldown Active"),
                        "timestamp": last_run,
                        "details": f"Cooldown active ({rem_h}h remaining)",
                    }
                else:
                    statuses[doc] = {
                        "code": "NEVER_RUN" if not last_run else "COOLDOWN_ACTIVE",
                        "label": "Never Run" if not last_run else "Skipped — Cooldown Active",
                        "timestamp": last_run,
                        "details": "No status recorded yet" if not last_run else "Cooldown active",
                    }
                state_modified = True
        # Case 3: Document not yet in statuses
        elif doc not in statuses:
            if last_run and (now - last_run < cooldown):
                rem_h = round((cooldown - (now - last_run)) / 3600.0, 1)
                statuses[doc] = {
                    "code": "COOLDOWN_ACTIVE",
                    "label": STATUS_LABELS.get("COOLDOWN_ACTIVE", "Skipped — Cooldown Active"),
                    "timestamp": last_run,
                    "details": f"Cooldown active ({rem_h}h remaining)",
                }
            else:
                statuses[doc] = {
                    "code": "NEVER_RUN" if not last_run else "COOLDOWN_ACTIVE",
                    "label": "Never Run" if not last_run else "Skipped — Cooldown Active",
                    "timestamp": last_run,
                    "details": "No status recorded yet" if not last_run else "Cooldown active",
                }
            state_modified = True

    if state_modified:
        state["last_status_per_doc"] = statuses
        _save_evolution_state(state)

    return statuses


def advance_doc_run_timestamp(
    filename: str, status_code: str = "APPROVED", details: str = "Proposal approved & applied to profile note"
) -> None:
    """Advance last_run_per_doc for a document to the current time.

    Called when a profile_update proposal is approved or denied by the user. Resets the
    per-document cooldown clock from the resolution timestamp rather than from the
    original proposal generation time and updates the document status.

    Args:
        filename: Document basename or path (e.g. cfg.PERSONA_FILE_USER or 'User_Profile.md').
        status_code: Status label key, default 'APPROVED'.
        details: Detail string for status reporting.
    """
    norm_filename = os.path.basename(filename)
    state = _load_evolution_state()
    state["last_run_per_doc"][norm_filename] = time.time()
    update_doc_status(state, norm_filename, status_code, details)


# ---------------------------------------------------------------------------
# Infrastructure & Mutual Exclusion
# ---------------------------------------------------------------------------


def _other_heavy_tasks_running() -> bool:
    """Check if any other heavy background task is currently active.

    Delegates to task_manager.is_any_running() — the single canonical
    source of truth for mutual exclusion across all heavy tasks.

    Returns:
        bool: True if another heavy task is active, False otherwise.
    """
    import task_manager

    return task_manager.is_any_running(exclude="profile_evolver")


def _set_status_in_server(status: str | None, error: str | None = None) -> None:
    """Register or clear status in the server's background task registry.

    Delegates to task_manager.set_running() / task_manager.clear_running().

    Args:
        status: Status string (e.g. 'running'), or None/status string on completion.
        error: Optional error message string.
    """
    import task_manager

    if status == "running":
        task_manager.set_running("profile_evolver")
    else:
        task_manager.clear_running("profile_evolver", status=status or "idle", error=error)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def cancel_pending_evolution():
    """Cancel any in-flight profile evolution task.

    Frees the Ollama instance immediately when a new user chat request is received.
    Draft progress is already persisted to disk at each pass boundary, so no work
    is lost — the next idle window will resume from the last completed pass.
    """
    global _evolver_task, _evolving
    if _evolver_task and not _evolver_task.done():
        _evolver_task.cancel()
        _evolving = False
        _set_status_in_server("cancelled")
        print("[PROFILE EVOLVER] Cancelled (new chat request). Draft progress saved.", flush=True)
    _evolver_task = None


async def run_profile_evolution():
    """Run the profile auto-evolution process as a background task.

    Coordinates mutual exclusion, checks cooldowns, and processes each document.
    If a document has an in-progress draft from a previous interrupted run, it
    resumes from the last completed pass rather than starting over.
    """
    global _evolving, _evolver_task
    importlib.reload(cfg)

    if not getattr(cfg, "PROFILE_EVOLUTION_ENABLED", False):
        return

    if _evolving:
        print("[PROFILE EVOLVER] Already running — skipping.", flush=True)
        return

    if _other_heavy_tasks_running():
        print("[PROFILE EVOLVER] Deferring execution due to other active heavy tasks.", flush=True)
        return

    _evolving = True
    _evolver_task = asyncio.current_task()
    _set_status_in_server("running")

    try:
        state = _load_evolution_state()
        now = time.time()

        # Check for pending profile updates
        pending_props = memory_db.get_pending_proposals("profile_update")
        pending_files = {p["suggested_category"] for p in pending_props}

        for filename, categories in DOCUMENT_CATEGORIES.items():
            if filename in pending_files:
                print(
                    f"[PROFILE EVOLVER] {filename} has a pending profile update. Skipping.",
                    flush=True,
                )
                update_doc_status(state, filename, "PENDING_EXISTS", "Pending proposal awaiting review")
                continue

            last_run = state["last_run_per_doc"].get(filename, 0.0)
            state["draft_cursor_per_doc"].get(filename, 0.0)
            cooldown = getattr(cfg, "PROFILE_EVOLUTION_COOLDOWN", 86400)

            # Skip if cooldown hasn't elapsed AND no in-progress draft exists.
            # A draft means work was interrupted — always resume it regardless
            # of the cooldown, since last_run hasn't advanced yet.
            draft_exists = os.path.exists(_draft_path(filename))
            if now - last_run < cooldown and not draft_exists:
                rem_h = round((cooldown - (now - last_run)) / 3600.0, 1)
                update_doc_status(state, filename, "COOLDOWN_ACTIVE", f"Cooldown active ({rem_h}h remaining)")
                continue

            # Collect all entries qualifying for this specific document.
            # The draft_cursor tracks what's already incorporated in the draft,
            # so entries up to draft_cursor are silently skipped inside
            # _evolve_document() — they're already in the working document.
            changed_entries = []
            for cat in categories:
                entries = memory_db.get_entries_by_category_for_document(cat, document_name=filename, status="live")
                changed_entries.extend(entries)

            # Sort chronologically (oldest-first) so historical backlogs drain sequentially
            changed_entries.sort(key=lambda e: (e.get("date") or "", e.get("id") or 0))

            # Cap entries per run to prevent multi-pass runaway proposals
            max_entries_per_run = getattr(cfg, "PROFILE_EVOLUTION_MAX_ENTRIES_PER_RUN", 30)
            if max_entries_per_run > 0 and len(changed_entries) > max_entries_per_run:
                print(
                    f"[PROFILE EVOLVER] {filename}: Capping backlog from {len(changed_entries)} to {max_entries_per_run} entries for this run.",
                    flush=True,
                )
                changed_entries = changed_entries[:max_entries_per_run]

            min_entries = getattr(cfg, "PROFILE_EVOLUTION_MIN_ENTRIES", 5)

            # Allow resume even if the new-entry count is below the minimum:
            # the draft already captured the bulk of the work — finishing is cheap.
            if len(changed_entries) < min_entries and not draft_exists:
                print(
                    f"[PROFILE EVOLVER] {filename}: Only {len(changed_entries)} new/updated "
                    f"entries (need {min_entries}). Skipping.",
                    flush=True,
                )
                update_doc_status(
                    state, filename, "BELOW_THRESHOLD", f"{len(changed_entries)}/{min_entries} qualifying entries"
                )
                continue

            resume_msg = " (resuming from draft)" if draft_exists else ""
            print(
                f"[PROFILE EVOLVER] Evolving {filename} with {len(changed_entries)} new/updated entries{resume_msg}...",
                flush=True,
            )

            doc_timeout = float(getattr(cfg, "PROFILE_EVOLUTION_DOC_TIMEOUT", 1500))
            import task_manager

            task_manager.set_running(
                "profile_evolver",
                phase=f"Evolving {filename}...",
                sub_status={"current_doc": filename, "changed_entries": len(changed_entries)},
            )
            try:
                success = await asyncio.wait_for(
                    _evolve_document(filename, changed_entries, state),
                    timeout=doc_timeout,
                )
                if success:
                    # Only advance last_run on a successfully created proposal
                    state["last_run_per_doc"][filename] = now
                    _save_evolution_state(state)
            except TimeoutError:
                print(
                    f"[PROFILE EVOLVER WARNING] {filename}: Timed out after {doc_timeout:.0f}s. "
                    "Draft state preserved on disk; continuing to next document.",
                    flush=True,
                )
                update_doc_status(
                    state,
                    filename,
                    "INTERRUPTED_SAVED",
                    f"Per-document timeout exceeded ({int(doc_timeout)}s limit); draft preserved",
                )
            except (httpx.HTTPError, OSError, ValueError, RuntimeError) as e_doc:
                print(
                    f"[PROFILE EVOLVER ERROR] {filename}: Error during evolution: {e_doc}. "
                    "Continuing to next document.",
                    flush=True,
                )
                update_doc_status(
                    state,
                    filename,
                    "MODEL_ERROR",
                    f"Evolution error: {type(e_doc).__name__}: {e_doc}",
                )

        import task_manager

        task_manager.save_last_run_ts("profile_evolver")
        _set_status_in_server("idle")

    except asyncio.CancelledError:
        print("[PROFILE EVOLVER] Execution cancelled.", flush=True)
        _set_status_in_server("cancelled")
    except (sqlite3.Error, OSError, RuntimeError, ValueError, KeyError, httpx.HTTPError) as e:
        print(f"[PROFILE EVOLVER ERROR] Exception: {e}", flush=True)
        _set_status_in_server("error", error=f"{type(e).__name__}: {e}")
    finally:
        _evolving = False
        _evolver_task = None


# ---------------------------------------------------------------------------
# Evolution core
# ---------------------------------------------------------------------------


async def _call_ollama(
    messages: list[dict],
    num_predict: int = -1,
    temperature_override: float | None = None,
    think_override: bool | None = None,
) -> str:
    """Async helper to call Ollama.

    Args:
        messages: List of message dictionaries.
        num_predict: Maximum prediction tokens (-1 for unlimited).
        temperature_override: Optional temperature override.
        think_override: Optional thinking tag toggle (defaults to True).

    Returns:
        str: Response content from the model.
    """
    importlib.reload(cfg)
    override = getattr(cfg, "PROFILE_EVOLUTION_MODEL_OVERRIDE", "default")
    model = cfg.MODEL_NAME if override == "default" else override

    temp = temperature_override if temperature_override is not None else cfg.TEMPERATURE
    think = think_override if think_override is not None else True

    options = {
        "num_ctx": cfg.NUM_CTX,
        "num_predict": num_predict,
        **{
            key: val
            for key, val in {
                "temperature": temp,
                "min_p": cfg.MIN_P,
                "top_k": cfg.TOP_K,
                "top_p": cfg.TOP_P,
                "repeat_penalty": cfg.REPEAT_PENALTY,
                "repeat_last_n": cfg.REPEAT_LAST_N,
                "seed": cfg.SEED,
            }.items()
            if val is not None
        },
    }

    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": options,
        "think": think,
    }

    content_buffer = ""
    timeout = getattr(cfg, "PROFILE_EVOLUTION_TIMEOUT", 240)

    async with (
        httpx.AsyncClient(timeout=timeout) as client,
        client.stream("POST", f"{cfg.OLLAMA_URL}/api/chat", json=payload) as resp,
    ):
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.strip():
                continue
            try:
                chunk = json.loads(line)
                msg = chunk.get("message", {})
                content_buffer += msg.get("content", "")
            except json.JSONDecodeError:
                continue

    return content_buffer.strip()


async def _proofread_document(filename: str, proposed_body: str) -> str:
    """Perform a dedicated low-temperature editorial proofreading pass.

    Catches and corrects typos, subword concatenation glitches, broken quotes,
    punctuation anomalies, and grammar errors without altering content, tone,
    or markdown headers.

    Args:
        filename: Document basename (e.g. cfg.PERSONA_FILE_USER or 'User_Profile.md').
        proposed_body: Proposed markdown document body.

    Returns:
        str: Proofread markdown body, or original body on failure/validation error.
    """
    if not getattr(cfg, "PROFILE_EVOLUTION_PROOFREAD_ENABLED", True):
        return proposed_body

    if not proposed_body or not proposed_body.strip():
        return proposed_body

    rules = DOCUMENT_RULES.get(filename, {})
    perspective = rules.get("perspective", "appropriate perspective")

    directives_proofread_note = ""
    if filename == cfg.PERSONA_FILE_DIRECTIVES:
        directives_proofread_note = (
            "- PRESERVE BULLET FORMAT: Strictly preserve all bullet points ('* **<Label>**: <Directive>'). "
            "Do NOT collapse, merge, or convert bullet points into narrative paragraphs.\n"
            "- CLEAN LABELS & STRIP SCARE QUOTES: Ensure bullet labels use plain functional terminology without quotation marks "
            "(e.g. '* **Cognitive Recovery Pacing**:', not '* **\"Deep Buffer Mode\"**:'). Remove unnecessary quotes around standard concepts.\n"
        )

    assistant_profile_proofread_note = ""
    if filename == cfg.PERSONA_FILE_ASSISTANT:
        assistant_profile_proofread_note = (
            "- PRESERVE NARRATIVE PROSE: Strictly preserve continuous first-person narrative prose. Do NOT introduce bullet points.\n"
            "- STRIP SCARE QUOTES & METAPHORICAL LABELS: Remove quotation marks around archetypes, descriptions, and personas "
            "(e.g. nerdy goth girl, succubus, archivist). Ensure smooth, unquoted phrasing.\n"
            "- REMOVE PARENTHETICAL EXPLANATIONS & META-COMMENTARY: Eliminate self-explaining parentheticals and disclaimers "
            "describing speech habits in the abstract.\n"
        )

    user_profile_proofread_note = ""
    if filename == cfg.PERSONA_FILE_USER:
        user_profile_proofread_note = (
            "- PRESERVE BULLET FORMAT: Strictly preserve all bullet points ('* **<Topic>**: <Fact/Preference>'). "
            "Do NOT collapse, merge, or convert bullet points into narrative prose paragraphs.\n"
            "- CLEAN QUOTATION ANOMALIES: Strip unnecessary scare quotes around standard concepts; ensure clear, direct phrasing without metaphorical jargon.\n"
        )

    proofread_prompt = (
        f"You are a strict, meticulous copyeditor and proofreader for an AI system's core persona and directives documents.\n\n"
        f"DOCUMENT: {filename}\n"
        f"TARGET PERSPECTIVE: {perspective}\n\n"
        f"DOCUMENT TEXT TO PROOFREAD:\n"
        f"---\n"
        f"{proposed_body}\n"
        f"---\n\n"
        f"PROOFREADING INSTRUCTIONS:\n"
        f"{directives_proofread_note}"
        f"{assistant_profile_proofread_note}"
        f"{user_profile_proofread_note}"
        f"- Thoroughly inspect and correct any spelling mistakes, typos, concatenated words, fragmented/mangled subword tokens (e.g. 'navigms' -> 'navigates', broken quotes like '\"word\"t' -> '\"word\"'), and punctuation errors.\n"
        f"- Ensure grammatical correctness and smooth phrasing while strictly preserving the existing narrative style and TARGET PERSPECTIVE.\n"
        f"- DO NOT summarize, shorten, remove, or add factual content. Preserve all sections, details, and bullet points.\n"
        f"- DO NOT change any markdown headers ('#', '##', '###'), WikiLinks ('[[...]]'), or proper names.\n"
        f"- Output ONLY the fully corrected markdown document body, with no conversational preamble or markdown code fence wrappers."
    )

    proofread_messages = [
        {
            "role": "system",
            "content": "You are a precise proofreader. Output the clean, error-free markdown document body only.",
        },
        {"role": "user", "content": proofread_prompt},
    ]

    import task_manager

    task_manager.set_running(
        "profile_evolver",
        phase=f"Proofreading & Editorial Polish ({filename})",
        sub_status={"current_doc": filename, "phase": "proofread"},
    )

    try:
        proofread_result = await _call_ollama(
            proofread_messages,
            temperature_override=0.1,
            think_override=False,
        )
        if not proofread_result:
            return proposed_body

        cleaned = extract_markdown_content(proofread_result)
        cleaned = normalize_document_text(cleaned)

        # Safety validation:
        # 1. Output must not be significantly truncated (at least 85% of original length)
        # 2. Canonical and major markdown headers must be preserved and substantive
        if len(cleaned) < len(proposed_body) * 0.85:
            print(
                f"[PROFILE EVOLVER WARNING] {filename}: Proofread result suspiciously short "
                f"({len(cleaned)} vs {len(proposed_body)} chars). Retaining pre-proofread body.",
                flush=True,
            )
            return proposed_body

        is_valid, reason, _failed_headers = validate_document_structure(filename, proposed_body, cleaned)
        if not is_valid:
            print(
                f"[PROFILE EVOLVER WARNING] {filename}: Proofread result structural failure ({reason}). "
                "Attempting canonical section repair...",
                flush=True,
            )
            cleaned = repair_missing_sections(filename, proposed_body, cleaned)

        print(f"[PROFILE EVOLVER] {filename}: Proofreading pass completed successfully.", flush=True)
        return cleaned

    except (httpx.HTTPError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
        print(
            f"[PROFILE EVOLVER ERROR] {filename}: Proofreading pass failed: {e}. Retaining pre-proofread body.",
            flush=True,
        )
        return proposed_body


async def _evolve_document(filename: str, new_entries: list[dict], state: dict) -> bool:
    """Propose updates to a persona, user profile, or directives document using the two-layer factoid ledger architecture.

    1. Loads the authoritative fact ledger (*_facts.md) or active working draft.
    2. Groups qualifying context entries by theme and prompts Ollama for atomic JSON deltas (added, modified, removed).
    3. Deterministically applies deltas to the ledger via profile_ledger.apply_ledger_delta().
    4. Deterministically prunes lower-tier items to strictly adhere to word budgets via profile_ledger.prune_ledger_to_budget().
    5. Synthesizes/compiles the presentation layer:
       - Assistant_Profile.md: Transformed into rich, continuous first-person narrative prose.
       - User_Profile.md & System_Directives.md: Clean markdown compiled with tier markers stripped.
    6. Stages a proposal in memory_db with candidate ledger and structured delta reason.

    Args:
        filename: Document basename (e.g. cfg.PERSONA_FILE_USER or 'User_Profile.md').
        new_entries: Qualifying entries changed since last run.
        state: Mutable evolution state dict.

    Returns:
        bool: True if a proposal was staged, False otherwise.
    """
    importlib.reload(cfg)
    import task_manager

    rules = DOCUMENT_RULES.get(filename, {})
    description = rules.get("description", "document body")
    perspective = rules.get("perspective", "appropriate perspective")
    guidelines = rules.get("guidelines", "")

    limits = getattr(cfg, "PROFILE_EVOLUTION_LIMITS", {})
    target_limit = limits.get(filename, 600)

    persona_dir = getattr(cfg, "PERSONA_DIR", None)
    if not persona_dir:
        persona_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "persona")

    fpath = os.path.join(persona_dir, filename)
    if not os.path.exists(fpath):
        print(f"[PROFILE EVOLVER] Error: document file not found at {fpath}", flush=True)
        return False

    current_content = await asyncio.to_thread(_sync_read_file, fpath)
    frontmatter, current_body = split_frontmatter(current_content)

    # Cross-document context to prevent topical redundancy
    other_docs_context = []
    for other_name in DOCUMENT_CATEGORIES:
        if other_name == filename:
            continue
        other_path = _draft_path(other_name)
        if not os.path.exists(other_path):
            other_path = os.path.join(persona_dir, other_name)
        if os.path.exists(other_path):
            try:
                other_content = await asyncio.to_thread(_sync_read_file, other_path)
                _, other_body = split_frontmatter(other_content)
                other_docs_context.append(f"DOCUMENT: {other_name}\nCONTENT:\n{other_body.strip()}")
            except OSError as e_other:
                print(f"[PROFILE EVOLVER] Warning: could not load other doc {other_name}: {e_other}", flush=True)
    other_docs_str = "\n\n".join(other_docs_context) if other_docs_context else "None"

    # ---------------------------------------------------------------------------
    # Resume detection — load working ledger from draft or live ledger file
    # ---------------------------------------------------------------------------
    draft_file = _draft_path(filename)
    draft_cursor = state["draft_cursor_per_doc"].get(filename, 0.0)
    ledger_filename = profile_ledger.get_ledger_filename(filename)
    ledger_path = os.path.join(persona_dir, ledger_filename)

    if os.path.exists(draft_file) and draft_cursor > 0.0:
        raw_ledger = await asyncio.to_thread(_sync_read_file, draft_file)
        ledger_frontmatter, current_sections = profile_ledger.parse_ledger(raw_ledger)
        print(
            f"[PROFILE EVOLVER] {filename}: Loaded draft ledger from disk "
            f"(cursor={datetime.datetime.fromtimestamp(draft_cursor, tz=datetime.UTC).astimezone().strftime('%Y-%m-%d %H:%M')}). "
            f"Resuming from last completed pass.",
            flush=True,
        )
    else:
        if os.path.exists(ledger_path):
            raw_ledger = await asyncio.to_thread(_sync_read_file, ledger_path)
        else:
            raw_ledger = current_content
        ledger_frontmatter, current_sections = profile_ledger.parse_ledger(raw_ledger)
        draft_cursor = 0.0

    batch_size = getattr(cfg, "PROFILE_EVOLUTION_BATCH_SIZE", 40)
    sorted_entries = sorted(
        new_entries,
        key=lambda e: max(e.get("created_at", 0) or 0, e.get("updated_at", 0) or 0),
    )

    remaining_entries = [
        e for e in sorted_entries if max(e.get("created_at", 0) or 0, e.get("updated_at", 0) or 0) > draft_cursor
    ]

    cumulative_changelog: dict[str, list[str]] = {"added": [], "modified": [], "removed": []}
    canonical_sections = CANONICAL_DOCUMENT_SECTIONS.get(filename, [])
    canonical_sections_str = "\n".join(f"- {s}" for s in canonical_sections) if canonical_sections else ""

    if not remaining_entries:
        print(
            f"[PROFILE EVOLVER] {filename}: All entries already in draft ledger. Proceeding directly to synthesis.",
            flush=True,
        )
    else:
        thematic_batches = _cluster_entries_by_theme(
            filename,
            remaining_entries,
            batch_size=batch_size,
        )
        total_thematic_passes = len(thematic_batches)

        for batch_idx, t_batch in enumerate(thematic_batches, 1):
            theme_name = t_batch["theme_name"]
            section_hint = t_batch.get("section_header", "")
            batch_entries = t_batch["entries"]
            evidence_block = t_batch["evidence_text"]
            batch_max_ts = t_batch["max_ts"]

            rendered_ledger = profile_ledger.render_ledger("", current_sections)

            delta_prompt = (
                f"You are an authoritative factoid evaluator for an AI persona/directives system.\n"
                f"Evaluate recent conversational evidence against the authoritative fact ledger and propose atomic additions, modifications, or removals.\n\n"
                f"DOCUMENT: {filename}\n"
                f"DESCRIPTION: {description}\n"
                f"THEMATIC FOCUS: {theme_name}\n"
                f"TARGET SECTION HINT: {section_hint or 'All relevant sections'}\n"
                f"TARGET PERSPECTIVE: {perspective}\n\n"
                f"PERSPECTIVE RULES:\n"
                f"{guidelines}\n\n"
                f"OTHER ACTIVE SYSTEM PROMPT DOCUMENTS (Do NOT duplicate any information covered here):\n"
                f"---\n"
                f"{other_docs_str}\n"
                f"---\n\n"
                f"REQUIRED CANONICAL SECTION HEADERS:\n"
                f"{canonical_sections_str}\n\n"
                f"CURRENT AUTHORITATIVE FACT LEDGER:\n"
                f"---\n"
                f"{rendered_ledger}\n"
                f"---\n\n"
                f"RECENT EVIDENCE TO EVALUATE ({theme_name}):\n"
                f"---\n"
                f"{evidence_block}\n"
                f"---\n\n"
                f"CRITICAL INSTRUCTIONS & RULES:\n"
                f"1. NON-INCLUSION OF TRANSIENT FACTS: Most observations do NOT belong in the profile — they belong in episodic RAG memory! Only extract high-level, recurring, permanent behavioral traits, core invariants, or major lifestyle boundaries. If an observation describes a transient task, specific code snippet, temporary tool, or one-off conversation detail, DISCARD IT.\n"
                f"2. 3-TIER PRIORITY HIERARCHY:\n"
                f"   - Tier 1 (Core Invariants & Hard Boundaries): Health, fatigue limits, recovery needs, sleep deficits, core relationship dynamics, foundational identity invariants. Protected and highest priority.\n"
                f"   - Tier 2 (Active Context & Recurring Habits): Technical domains, AI architectures, workspace habits, batching routines.\n"
                f"   - Tier 3 (Ephemeral Details & Secondary Preferences): Transient hobbies, specific games/media titles, temporary tooling setups.\n"
                f"3. FORMATTING & INTEGRITY:\n"
                f"   - Use clean, functional labels (e.g. '* [Tier 2] **<Topic>**: <Fact>').\n"
                f"   - STRICTLY FORBID scare quotes, coined metaphors, or figurative nicknames.\n"
                f"   - NO CONFLATION: Keep distinct traits and observations strictly separated into individual bullet points. Do NOT merge unrelated topics into composite sentences.\n"
                f"   - Apply the PERSPECTIVE RULES strictly.\n"
                f"4. ATOMIC DELTA: If updates are warranted, specify exactly which items are added, modified, or removed. If an existing bullet covers the observation, either modify it or do nothing. If the observation is already known, do NOT add duplicates.\n"
                f"5. CANONICAL SECTIONS: All additions/modifications must target one of the canonical section headers listed above.\n"
                f"6. JSON OUTPUT FORMAT: Respond ONLY with a valid JSON object matching this schema:\n"
                f"{{\n"
                f'  "reason": "Brief summary of changes made or why no changes are needed",\n'
                f'  "added": [\n'
                f'    {{"section": "## Exact Section Header", "tier": 1, "label": "Short Topic Label", "fact": "Concrete statement."}}\n'
                f'  ],\n'
                f'  "modified": [\n'
                f'    {{"section": "## Exact Section Header", "label": "Existing Bullet Label", "new_fact": "Updated statement.", "tier": 1}}\n'
                f'  ],\n'
                f'  "removed": [\n'
                f'    {{"section": "## Exact Section Header", "label": "Existing Bullet Label", "reason": "Why removed"}}\n'
                f'  ]\n'
                f"}}\n"
                f'If no updates are warranted, output {{"reason": "No changes warranted", "added": [], "modified": [], "removed": []}}.'
            )

            messages = [
                {
                    "role": "system",
                    "content": "You are a precise factoid evaluator. Output valid JSON delta only.",
                },
                {"role": "user", "content": delta_prompt},
            ]

            print(
                f"[PROFILE EVOLVER] {filename}: Evaluating thematic pass {batch_idx}/{total_thematic_passes} "
                f"({theme_name}: {len(batch_entries)} entries)...",
                flush=True,
            )

            task_manager.set_running(
                "profile_evolver",
                phase=f"Evolving {filename} (Thematic Delta Pass {batch_idx}/{total_thematic_passes}: {theme_name})",
                sub_status={
                    "current_doc": filename,
                    "theme": theme_name,
                    "pass_index": batch_idx,
                    "total_passes": total_thematic_passes,
                    "batch_size": len(batch_entries),
                },
            )

            try:
                raw_delta = await _call_ollama(messages)
            except (httpx.HTTPError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e:
                print(
                    f"[PROFILE EVOLVER ERROR] {filename}: Thematic pass {batch_idx} ({theme_name}) failed: {e}.",
                    flush=True,
                )
                if batch_idx == 1 and draft_cursor == 0.0:
                    return False
                return False

            if raw_delta:
                delta = _parse_json_delta(raw_delta)
                if delta:
                    current_sections, pass_changelog = profile_ledger.apply_ledger_delta(current_sections, delta)
                    cumulative_changelog["added"].extend(pass_changelog["added"])
                    cumulative_changelog["modified"].extend(pass_changelog["modified"])
                    cumulative_changelog["removed"].extend(pass_changelog["removed"])
                    if any(pass_changelog.values()):
                        print(
                            f"[PROFILE EVOLVER] {filename}: Pass {batch_idx} delta applied: "
                            f"{len(pass_changelog['added'])} added, {len(pass_changelog['modified'])} modified, {len(pass_changelog['removed'])} removed.",
                            flush=True,
                        )
                else:
                    print(
                        f"[PROFILE EVOLVER WARNING] {filename}: Pass {batch_idx} produced unparseable JSON delta. Skipping batch changes.",
                        flush=True,
                    )

            draft_cursor = max(draft_cursor, batch_max_ts)
            try:
                draft_text = profile_ledger.render_ledger(ledger_frontmatter, current_sections)
                await asyncio.to_thread(_sync_write_file, draft_file, draft_text)
                state["draft_cursor_per_doc"][filename] = draft_cursor
                _save_evolution_state(state)
            except OSError as e_draft:
                print(f"[PROFILE EVOLVER] Warning: could not save draft ledger after pass {batch_idx}: {e_draft}", flush=True)

    # ---------------------------------------------------------------------------
    # Evaluation of Changes & Budget Pruning
    # ---------------------------------------------------------------------------
    has_changes = any(cumulative_changelog.values())
    if not has_changes and draft_cursor == 0.0:
        print(f"[PROFILE EVOLVER] No changes proposed for {filename}.", flush=True)
        _clear_draft(filename, state)
        update_doc_status(state, filename, "NO_CORE_CHANGES", f"{len(new_entries)} entries evaluated; no core changes")
        return False

    # Deterministic Word Budget Pruning on Authoritative Ledger
    current_sections, pruned_count = profile_ledger.prune_ledger_to_budget(current_sections, target_limit)
    if pruned_count > 0:
        print(
            f"[PROFILE EVOLVER] {filename}: Deterministically pruned {pruned_count} lower-tier items to fit budget ({target_limit}w limit).",
            flush=True,
        )

    # ---------------------------------------------------------------------------
    # Presentation Layer Synthesis
    # ---------------------------------------------------------------------------
    if filename == cfg.PERSONA_FILE_ASSISTANT:
        clean_bullets = profile_ledger.compile_clean_markdown("", current_sections)
        synthesis_prompt = (
            f"You are synthesizing the active first-person narrative persona document for {cfg.ASSISTANT_NAME}.\n\n"
            f"DOCUMENT: {filename}\n"
            f"TARGET PERSPECTIVE: {perspective}\n\n"
            f"PERSPECTIVE RULES:\n{guidelines}\n\n"
            f"REQUIRED CANONICAL SECTION HEADERS:\n{canonical_sections_str}\n\n"
            f"AUTHORITATIVE FACT INVENTORY (Transform all facts into continuous narrative prose under their respective headers):\n"
            f"---\n{clean_bullets}\n---\n\n"
            f"CRITICAL INSTRUCTIONS:\n"
            f"- Transform the fact inventory into rich, continuous first-person narrative prose under each canonical header.\n"
            f"- STRICTLY FORBID BULLET POINTS: Every section must be composed of smooth, expressive prose paragraphs.\n"
            f"- TRIGGER & ACTION BEHAVIORAL MODELING: Embody observable behavior, emotional intent, and responsive presence.\n"
            f"- NO SCARE QUOTES OR SELF-EXPLAINING PARENTHETICALS: Strip quotes around concepts. Embody traits directly.\n"
            f"- NO META-COMMENTARY ON DIALOGUE: Eliminate sentences explaining speech habits or endearments in the abstract.\n"
            f"- WORD COUNT LIMIT: The complete output must be strictly under {target_limit} words.\n"
            f"- Do NOT output YAML frontmatter. Start directly with the first section header.\n"
            f"- Output ONLY the markdown document content, no explanation, no code fences."
        )
        task_manager.set_running(
            "profile_evolver",
            phase=f"Evolving {filename} (Synthesizing Narrative Presentation Layer)",
            sub_status={
                "current_doc": filename,
                "phase": "synthesis",
                "target_limit": target_limit,
            },
        )
        synth_messages = [
            {"role": "system", "content": "You are a master writer synthesizing first-person persona prose."},
            {"role": "user", "content": synthesis_prompt},
        ]
        try:
            synth_result = await _call_ollama(synth_messages)
            synth_clean = extract_markdown_content(synth_result) if synth_result else ""
            synth_clean = normalize_document_text(synth_clean)
            is_valid, reason, _ = validate_document_structure(filename, current_body, synth_clean)
            if not is_valid:
                print(
                    f"[PROFILE EVOLVER WARNING] {filename}: Synthesis structural check ({reason}). "
                    "Repairing canonical sections...",
                    flush=True,
                )
                synth_clean = repair_missing_sections(filename, current_body, synth_clean)
            proposed_body = synth_clean if synth_clean.strip() else current_body
        except (httpx.HTTPError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as e_synth:
            print(f"[PROFILE EVOLVER ERROR] {filename}: Synthesis failed: {e_synth}. Retaining current body.", flush=True)
            proposed_body = current_body
    else:
        # User_Profile.md & System_Directives.md: Deterministic compile from authoritative ledger
        proposed_body = profile_ledger.compile_clean_markdown("", current_sections)

    # Word Count Circuit Breaker
    final_word_count = len(proposed_body.split())
    hard_ceiling = int(target_limit * 1.10)
    if final_word_count > hard_ceiling:
        if filename == cfg.PERSONA_FILE_ASSISTANT:
            proposed_body = prune_bullets_to_word_budget(filename, proposed_body, target_limit)
            final_word_count = len(proposed_body.split())

        if final_word_count > hard_ceiling:
            print(
                f"[PROFILE EVOLVER WARNING] {filename}: Proposed body has {final_word_count} words, "
                f"exceeding hard ceiling of {hard_ceiling} words ({target_limit}w limit). Proposal blocked.",
                flush=True,
            )
            update_doc_status(
                state,
                filename,
                "ABORTED_OVER_BUDGET",
                f"Word count {final_word_count}w exceeds hard ceiling {hard_ceiling}w ({target_limit}w limit); proposal blocked",
            )
            _clear_draft(filename, state)
            return False

    # Editorial Proofreading Pass
    proposed_body = await _proofread_document(filename, proposed_body)

    if proposed_body.strip() == current_body.strip():
        print(f"[PROFILE EVOLVER] Proposed body identical to current document for {filename}.", flush=True)
        _clear_draft(filename, state)
        update_doc_status(state, filename, "NO_CORE_CHANGES", f"{len(new_entries)} entries evaluated; no core changes")
        return False

    # Package Proposal with Structured Reason & Candidate Ledger
    summary_parts = []
    if cumulative_changelog["added"]:
        summary_parts.append(f"Added {len(cumulative_changelog['added'])} facts")
    if cumulative_changelog["modified"]:
        summary_parts.append(f"Updated {len(cumulative_changelog['modified'])} facts")
    if cumulative_changelog["removed"]:
        summary_parts.append(f"Removed {len(cumulative_changelog['removed'])} facts")
    summary_text = f"Evolving {filename}: {', '.join(summary_parts)}." if summary_parts else f"Evolving {filename} based on recent context entries."

    current_time_str = datetime.datetime.now(datetime.UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    updated_ledger_frontmatter = update_frontmatter_modified_date(ledger_frontmatter, current_time_str)
    candidate_ledger_text = profile_ledger.render_ledger(updated_ledger_frontmatter, current_sections)

    reason_payload = {
        "summary": summary_text,
        "added": cumulative_changelog["added"],
        "modified": cumulative_changelog["modified"],
        "removed": cumulative_changelog["removed"],
        "candidate_ledger": candidate_ledger_text,
    }
    reason_str = json.dumps(reason_payload, indent=2)

    updated_frontmatter = update_frontmatter_modified_date(frontmatter, current_time_str)
    proposed_content = updated_frontmatter + "\n\n" + proposed_body if updated_frontmatter else proposed_body

    source_ids = [int(entry["id"]) for entry in new_entries if entry.get("id")]

    memory_db.insert_proposal(
        type="profile_update",
        suggested_category=filename,
        merged_observation=proposed_content,
        merged_tags=current_content,
        reason=reason_str,
        source_ids=source_ids,
    )
    print(f"[PROFILE EVOLVER] Created profile_update proposal for {filename}.", flush=True)
    update_doc_status(state, filename, "PROPOSAL_STAGED", f"Proposal staged ({len(new_entries)} entries)")
    _clear_draft(filename, state)
    return True


def _clear_draft(filename: str, state: dict) -> None:
    """Delete the draft file and reset the cursor for a document.

    Called after a proposal is successfully created, or when the accumulated
    content is identical to the current document (no changes warranted).

    Args:
        filename: Document basename.
        state: Mutable evolution state dict to update in-place.
    """
    draft_file = _draft_path(filename)
    if os.path.exists(draft_file):
        try:
            os.remove(draft_file)
        except OSError as e:
            print(f"[PROFILE EVOLVER] Warning: could not delete draft file: {e}", flush=True)
    state["draft_cursor_per_doc"][filename] = 0.0
    _save_evolution_state(state)
