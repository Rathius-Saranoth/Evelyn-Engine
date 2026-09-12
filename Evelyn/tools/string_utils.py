# string_utils.py
# date created: 2026-08-28 12:25:00
# date modified: 2026-09-12 11:34:26
# tags: #utils, #strings, #sanitization, #slugify, #gist

"""
string_utils.py — Canonical String Processing, Sanitization & Text Normalization.

Exports:
    sanitize_filename()     — Strips illegal filesystem characters and normalizes whitespace.
    slugify()               — Converts text into standard snake_case or kebab-case identifiers.
    clean_title()           — Cleans file stems or headings into standardized Title Case.
    detect_notation_discipline() — Identifies domain of notation leak (music, math, chemistry, etc.).
    is_notation_leak()      — Determines whether text contains a leaked notation artifact.
    strip_thinking_tags()   — Strips CoT <think> tags and LLM formatting artefacts.
    clean_llm_gist()        — Cleans summaries, stripping thinking tags, LaTeX, and prefixes.
    escape_xml_content()    — Escapes &, <, > in XML element character data.
    escape_xml_attr()       — Escapes special characters in XML attribute values.
    wrap_xml_envelope()     — Constructs structured XML envelopes with token pruning.
    build_temporal_envelope() — Constructs standardized <temporal_context> envelopes.
    build_context_retrieval_envelope() — Constructs standardized <context_retrieval> envelopes.
    build_autonomous_trigger_envelope() — Constructs standardized <autonomous_trigger> envelopes.
    build_system_event_envelope() — Constructs standardized <system_event> telemetry envelopes.
    build_memory_context_envelope() — Constructs standardized <memory_context> envelopes.
    stack_envelopes()       — Deterministically stacks multiple XML envelopes.
    inject_envelope_to_turn() — Prepends envelope(s) to message turns with clean boundary isolation.
    protect_code_blocks()   — Masks fenced code, inline code, and math blocks with safe tokens.
    restore_code_blocks()   — Restores original code blocks from placeholder tokens.
    estimate_tokens()       — Fast conservative token estimator (~2.5 chars/token).
    truncate_to_token_budget() — Truncates text cleanly within token budgets.
    extract_markdown_outline() — Extracts markdown heading outline for truncated documents.

Key config: Standard library only (zero internal project dependencies).
See also: reference/xml_injection_conventions.md · reference/engine_architecture.md
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def strip_thinking_tags(text: str) -> str:
    """Strip chain-of-thought <think> tags, LaTeX markup, and markdown artefacts from LLM outputs.

    Args:
        text: Raw text or summary from an LLM.

    Returns:
        Cleaned text with thinking blocks and artifact markup removed.
    """
    if not text:
        return ""
    # Strip closed think tags: <think>...</think>
    cleaned = re.sub(r"^.*?</think>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL)
    # Strip unclosed think tags: <think>...
    cleaned = re.sub(r"<think>.*", "", cleaned, flags=re.DOTALL)
    # Strip LaTeX boxed markup
    cleaned = re.sub(r"\\boxed\{.*?\}", "", cleaned, flags=re.DOTALL)
    # Strip leading summary labels
    cleaned = re.sub(r"(?im)^\*?\*?summary:?\*?\*?\s*", "", cleaned)
    # Collapse 3+ newlines to 2
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def clean_llm_gist(text: str) -> str:
    """Clean and standardize an LLM-generated document gist or note summary.

    Args:
        text: Raw gist string.

    Returns:
        Sanitized, single/multi-paragraph clean gist string.
    """
    cleaned = strip_thinking_tags(text)
    # Strip leading/trailing quotation marks
    cleaned = cleaned.strip('"\'').strip()
    return cleaned


def sanitize_filename(
    name: str,
    max_length: int = 200,
    default: str = "untitled",
    vault_safe: bool = False,
) -> str:
    """Strip illegal filesystem characters and collapse whitespace.

    Strips characters illegal on Linux/Windows/macOS (/ \\ : * ? " < > |)
    and removes non-printable / control characters.

    Args:
        name: Desired filename or note title.
        max_length: Maximum allowed character length for the output.
        default: Fallback string if sanitization leaves name empty.
        vault_safe: If True, enforces strict vault naming rules (only alphanumeric,
            spaces, dashes '-', and underscores '_'). Parenthetical segments like
            '(app)' are converted to '- app'.

    Returns:
        Safe filesystem filename string.
    """
    if not name:
        return default

    # Normalize unicode
    clean = unicodedata.normalize("NFKC", str(name))

    # If filename has an extension, preserve it
    ext = ""
    if "." in clean and not clean.startswith("."):
        stem, potential_ext = clean.rsplit(".", 1)
        if len(potential_ext) <= 5 and re.match(r"^[A-Za-z0-9]+$", potential_ext):
            clean = stem
            ext = "." + potential_ext

    if vault_safe:
        # Convert parenthetical disambiguation to dash syntax: e.g. "Discord (app)" -> "Discord - app"
        clean = re.sub(r"\s*\((.*?)\)", r" - \1", clean)
        # Strip all characters except alphanumeric, whitespace, dash, underscore
        clean = re.sub(r"[^A-Za-z0-9\s_-]", " ", clean)
        # Collapse multiple dashes or spaces
        clean = re.sub(r"\s*-\s*", " - ", clean)
        clean = re.sub(r"\s+", " ", clean).strip(" -_")
    else:
        # Replace illegal filesystem characters with space
        clean = re.sub(r'[/\\:*?"<>|\x00-\x1f\x7f]', " ", clean)
        # Collapse multiple spaces into one
        clean = re.sub(r"\s+", " ", clean).strip()

    # Strip leading/trailing dots or spaces (problematic on Windows/SMB)
    clean = clean.strip(". ")

    if not clean:
        return default + ext

    final_name = clean[:max_length].rstrip(". ") + ext
    return final_name


def protect_code_blocks(text: str) -> tuple[str, dict[str, str]]:
    """Mask code blocks, inline code, and math blocks with safe placeholder tokens.

    Placeholders use distinct delimiters `@@EVELYN_CODE_{salt}_{i}@@` that do not
    collide with markdown link or tag regexes or nested protector calls.

    Args:
        text: Markdown text to protect.

    Returns:
        tuple[str, dict[str, str]]: (masked_text, placeholder_map)
    """
    if not text:
        return "", {}

    import uuid

    placeholders: dict[str, str] = {}
    salt = uuid.uuid4().hex[:8]
    counter = 0

    # Pattern matches:
    # 1. 4-backtick or 3-backtick fenced code blocks
    # 2. LaTeX math blocks ($$...$$)
    # 3. Inline code (`...`)
    # 4. Inline math ($...$)
    combined_pattern = re.compile(
        r"(````[\s\S]*?````|"
        r"```[\s\S]*?```|"
        r"\$\$[\s\S]*?\$\$|"
        r"`[^`\n]+`|"
        r"\$(?:\\\$|[^\$\n])+\$)",
        re.MULTILINE,
    )

    def _replace(match: re.Match) -> str:
        nonlocal counter
        token = f"@@EVELYN_CODE_{salt}_{counter}@@"
        placeholders[token] = match.group(0)
        counter += 1
        return token

    masked_text = combined_pattern.sub(_replace, text)
    return masked_text, placeholders


def restore_code_blocks(text: str, placeholders: dict[str, str]) -> str:
    """Restore original code and math blocks from safe placeholder tokens.

    Args:
        text: Masked markdown text containing placeholder tokens.
        placeholders: Dictionary mapping placeholder tokens to original code.

    Returns:
        str: Fully restored markdown text.
    """
    if not text or not placeholders:
        return text

    restored = text
    # Replace tokens in reverse insertion order
    for token, original in reversed(list(placeholders.items())):
        restored = restored.replace(token, original)

    return restored


def slugify(text: str, delimiter: str = "_") -> str:
    """Convert arbitrary text to a clean identifier (snake_case or kebab-case).

    Args:
        text: Input string (e.g. "Groceries & Supplies List").
        delimiter: Separator to use ("_" for snake_case, "-" for kebab-case).

    Returns:
        Lowercased ASCII slug identifier (e.g. "groceries_supplies_list").
    """
    if not text:
        return ""

    # Normalize unicode to ASCII
    text_norm = unicodedata.normalize("NFKD", str(text))
    text_ascii = text_norm.encode("ascii", "ignore").decode("ascii")
    # Replace non-alphanumeric characters with delimiter
    slug = re.sub(r"[^\w\s-]", "", text_ascii).strip()
    slug = re.sub(r"[-\s_]+", delimiter, slug).strip(delimiter).lower()
    return slug


def clean_title(filename_or_text: str) -> str:
    """Normalize file names or headers to clean Title Case titles.

    Strips common extensions (.pdf, .md, .txt), converts non-code underscores
    to spaces, and cleans up punctuation.

    Args:
        filename_or_text: Raw filename or title string.

    Returns:
        Standardized clean Title Case string.
    """
    if not filename_or_text:
        return ""

    clean = filename_or_text.strip()
    # Strip common file extensions
    clean = re.sub(r"\.(pdf|md|markdown|txt)$", "", clean, flags=re.IGNORECASE).strip()

    # Strip Unicode replacement / mojibake characters
    clean = clean.replace("\ufffd", "").replace("\ufffe", "").strip()

    # Strip leading bullets, checklist marks (✓, ✔, •)
    clean = re.sub(r"^[✓✔•\s\-_–—]+", "", clean).strip()

    # Rejoin line-break hyphenated words: e.g. "Wa- ter" -> "Water", "Tempera- ture" -> "Temperature"
    clean = re.sub(r"(\b[A-Za-z]{2,})-\s+([a-z]{2,}\b)", r"\1\2", clean)

    # Strip trailing dangling hyphens
    clean = clean.rstrip(" -–—")

    # If underscores exist and it is not an all-caps code (like SEC_10K_2026), replace underscores with spaces
    if "_" in clean and not re.match(r"^[A-Z0-9_-]+$", clean):
        clean = clean.replace("_", " ")

    # Collapse whitespace
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def detect_notation_discipline(text: str) -> str | None:
    """Detect whether a title or heading is a leaked notation artifact.

    Discipline-aware heuristics detect sheet music font glyphs, LaTeX equations,
    dense mathematical operators, chemical reaction formulas, and table artifacts,
    while explicitly guarding against false positives in currencies, procedural arrows,
    and standard comparative titles.

    Args:
        text: Candidate title or heading string.

    Returns:
        Discipline category ('music', 'latex_math', 'math_operators', 'chemistry',
        'formatting_artifact') if a leak is detected, or None if the text is clean.
    """
    if not text:
        return None
    s = text.strip()

    # 1. Music font glyphs & Unicode music symbols
    # Sheet music font mappings: œ (quarter), ˙ (half), Ó (half rest), Œ (quarter rest), ‰ (8th rest)
    # and Unicode music block U+2669-U+266F, U+1D100-U+1D1FF
    if re.search(r"[\u2669-\u266F\U0001D100-\U0001D1FFœ˙ÓŒ‰]", s):
        return "music"

    # Music note stems / accidentals / repeated notation keywords (e.g. sharp-sharp, w w w w)
    if re.search(r"\b(?:sharp|flat)\s*-\s*(?:sharp|flat)\b", s, re.IGNORECASE):
        return "music"
    if re.search(r"\b(?:w|sharp|flat)\b(?:\s+(?:w|sharp|flat)\b){2,}", s, re.IGNORECASE):
        return "music"
    if re.search(r"(?:^\s*|\s)\?\s*(?:\.\.|\#\#|[œ˙w])", s):
        return "music"

    # 2. LaTeX math vs Currency
    # LaTeX control tokens: \frac, \sqrt, \sum, \int, \partial, etc.
    if re.search(r"\\(?:frac|sqrt|sum|int|partial|alpha|beta|gamma|theta|lambda|mu|pi|sigma|omega|begin|cdot|times|infty|forall|exists|nabla|approx|equiv|leq|geq|neq)(?=[^a-zA-Z]|$)", s):
        return "latex_math"
    # Math $$...$$ or $...$ delimiters, strictly avoiding currency like $50 or $19.99
    if re.search(r"\$\$(?:[^\$]+)\$\$", s):
        return "latex_math"
    if re.search(r"(?<![\d\w])\$(?!\d|\s)(?:[^\$]{2,}?)(?<!\s|\d)\$(?![\d\w])", s):
        return "latex_math"

    # 3. Dense Math Operators
    # Matches symbols like ∑, ∫, ∂, √, ∏, ∆, ∇, ≤, ≥, ≠, ≈, ∈, ∉, ⊂, ⊃, ⊆, ⊇, ∪, ∩, ∀, ∃
    math_syms = re.findall(r"[∑∫∂√∏∆∇≤≥≠≈∈∉⊂⊃⊆⊇∪∩∀∃×÷]", s)
    if len(math_syms) >= 2:
        words = re.findall(r"[A-Za-z]{2,}", s)
        if len(words) < 4 or len(math_syms) / max(1, len(words)) >= 0.5:
            return "math_operators"

    # 4. Chemistry Stoichiometric Equations vs Procedural Arrows
    chem_match = re.search(r"(.*?)\s*(?:->|<=>|→|⇌|<->)\s*(.*)", s)
    if chem_match:
        left, right = chem_match.group(1).strip(), chem_match.group(2).strip()
        chem_formula_pattern = r"(?:\d*[A-Z][a-z]?\d*)+"
        chem_side_pattern = rf"^\s*{chem_formula_pattern}(?:\s*\+\s*{chem_formula_pattern})*\s*$"
        if re.match(chem_side_pattern, left) and re.match(chem_side_pattern, right):
            return "chemistry"

    # 5. Formatting & Punctuation Artifacts
    # Table dividers (two or more columns of hyphens/colons separated by pipes)
    if re.search(r"^\|?(?:\s*[-:]+\s*\|)+\s*[-:]*\s*\|?$", s):
        return "formatting_artifact"
    # Repeated single-character or punctuation tokens (e.g. Q Q Q, .. .., ===, ---)
    if re.search(r"\b([A-Za-z0-9])\s+\1\s+\1\b", s):
        return "formatting_artifact"
    # Single-letter token sequences of 3+ letters (e.g. "q k e")
    core = re.sub(r"^\d+\s*[-–—]\s*", "", s).strip()
    if re.match(r"^[a-z](\s+[a-z]){2,}$", core):
        return "formatting_artifact"
    if re.search(r"\.{2,}\s+\.{2,}", s):
        return "formatting_artifact"
    if re.search(r"^[-=_~*]{3,}$", s):
        return "formatting_artifact"
    # Lone time signatures / meter artifacts like "2 4" or "4 4" as whole title
    if re.match(r"^(?:\d+\s*[-–—]\s*)?[2346]\s+[48]$", s):
        return "formatting_artifact"
    # Unicode replacement / mojibake characters
    if "\ufffd" in s or "\ufffe" in s:
        return "formatting_artifact"
    # Accidental extension inside title
    if re.search(r"\.(?:md|pdf|markdown|txt)$", s, re.IGNORECASE):
        return "formatting_artifact"

    return None


def is_notation_leak(text: str) -> bool:
    """Determine whether text contains a leaked notation or formatting artifact."""
    return detect_notation_discipline(text) is not None


# ---------------------------------------------------------------------------
# Canonical XML Envelope & Prompt Telemetry Helpers
# ---------------------------------------------------------------------------


def escape_xml_content(text: Any) -> str:
    """Escape &, <, > in XML element character data.

    Args:
        text: Raw text to place inside an XML tag body.

    Returns:
        Sanitized text safe for XML body inclusion.
    """
    if text is None:
        return ""
    s = str(text)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_xml_attr(val: Any) -> str:
    """Escape &, <, >, \", ' in XML attribute values.

    Args:
        val: Attribute value.

    Returns:
        Escaped attribute string safe for key="value" inclusion.
    """
    if val is None:
        return ""
    s = str(val)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def wrap_xml_envelope(
    tag: str,
    body: str | list[str] | None = None,
    self_closing_if_empty: bool = False,
    **attrs: Any,
) -> str:
    """Wrap content in a semantic XML envelope with attribute escaping and token pruning.

    Rules:
      1. If body is provided and non-empty, wraps in <tag attrs>\\n  body\\n</tag>.
      2. If body is empty/None:
         - If self_closing_if_empty is True and attributes exist, returns <tag attrs />.
         - Otherwise returns "" (token pruning; never emit empty useless containers).

    Args:
        tag: XML tag name (e.g. 'temporal_context', 'context_retrieval').
        body: Inner element text, raw XML string, or list of inner child strings.
        self_closing_if_empty: If True and body is empty, emit self-closing <tag attrs />.
        **attrs: Key-value attributes for the tag (omitted if value is None).

    Returns:
        Structured XML string or empty string if pruned.
    """
    attr_parts = []
    for k, v in attrs.items():
        if v is not None:
            attr_parts.append(f'{k}="{escape_xml_attr(v)}"')
    attr_str = f" {' '.join(attr_parts)}" if attr_parts else ""

    # Process body content
    if isinstance(body, (list, tuple)):
        clean_items = [item.strip() for item in body if item and str(item).strip()]
        if clean_items:
            # Indent each line of child items by 2 spaces
            formatted_children = []
            for item in clean_items:
                indented = "\n".join(f"  {line}" if line else "" for line in item.split("\n"))
                formatted_children.append(indented)
            inner_content = "\n".join(formatted_children)
            return f"<{tag}{attr_str}>\n{inner_content}\n</{tag}>"
        body_text = ""
    elif body is not None:
        body_text = str(body).strip()
    else:
        body_text = ""

    if body_text:
        # Indent inner lines
        indented_lines = "\n".join(f"  {line}" if line else "" for line in body_text.split("\n"))
        return f"<{tag}{attr_str}>\n{indented_lines}\n</{tag}>"

    # Handle empty body
    if self_closing_if_empty and attr_str:
        return f"<{tag}{attr_str} />"

    return ""


def build_temporal_envelope(
    current_time: str,
    session_gap: dict[str, Any] | None = None,
    calendar_events: list[dict[str, Any]] | None = None,
    task_events: list[dict[str, Any]] | None = None,
) -> str:
    """Build a standardized <temporal_context> telemetry envelope.

    Args:
        current_time: Formatted local time string.
        session_gap: Optional dict with 'status', 'duration_str', etc.
        calendar_events: Optional list of dicts with 'title', 'start_str', 'status'.
        task_events: Optional list of dicts with 'title', 'due_str', 'status'.

    Returns:
        Structured <temporal_context> XML string.
    """
    children = [f"<current_time>{escape_xml_content(current_time)}</current_time>"]

    if session_gap and session_gap.get("status") != "active_flow":
        duration = session_gap.get("duration_str", "")
        last_ts = session_gap.get("last_interaction_ts", "")
        attrs = {"status": "resumed"}
        if duration:
            attrs["break_duration"] = duration
        if last_ts:
            attrs["last_interaction"] = last_ts
        children.append(wrap_xml_envelope("session_gap", self_closing_if_empty=True, **attrs))
    else:
        children.append('<session_gap status="active_flow" />')

    if calendar_events:
        ev_tags = []
        for ev in calendar_events:
            ev_tag = wrap_xml_envelope(
                "event",
                self_closing_if_empty=True,
                title=ev.get("title", ""),
                time=ev.get("start_str", ev.get("time", "")),
                status=ev.get("status", ""),
            )
            if ev_tag:
                ev_tags.append(ev_tag)
        if ev_tags:
            children.append(wrap_xml_envelope("calendar_agenda", body=ev_tags))

    if task_events:
        tk_tags = []
        for tk in task_events:
            tk_tag = wrap_xml_envelope(
                "task",
                self_closing_if_empty=True,
                title=tk.get("title", ""),
                time=tk.get("due_str", tk.get("time", "")),
                status=tk.get("status", ""),
            )
            if tk_tag:
                tk_tags.append(tk_tag)
        if tk_tags:
            children.append(wrap_xml_envelope("task_agenda", body=tk_tags))

    return wrap_xml_envelope("temporal_context", body=children)


def build_context_retrieval_envelope(
    source: str,
    query: str | None = None,
    items: list[str | dict[str, Any]] | None = None,
    match_count: int | None = None,
    include_query: bool = False,
) -> str:
    """Build a standardized <context_retrieval> envelope for RAG and vault excerpts.

    Prunes to empty string "" if items is empty (no matching content).
    By default, omits the raw query attribute from the XML opening tag to prevent
    the LLM from misinterpreting the active prompt as historical vault context.

    Args:
        source: Retrieval source (e.g. 'vault', 'memory_db', 'chroma').
        query: Query string triggering retrieval (logged in SQLite, omitted from XML unless include_query=True).
        items: List of pre-formatted child XML strings or chunk dicts.
        match_count: Optional count of retrieved items (defaults to len(items)).
        include_query: If True, includes query="..." on the tag (defaults to False).

    Returns:
        Structured <context_retrieval> XML block or empty string.
    """
    if not items:
        return ""

    child_strings = []
    for item in items:
        if isinstance(item, str):
            if item.strip():
                child_strings.append(item.strip())
        elif isinstance(item, dict):
            # Format dict as <document> or <item>
            doc_id = item.get("id") or item.get("path") or item.get("source", "")
            title = item.get("title", "")
            score = item.get("score") or item.get("similarity")
            content = item.get("content") or item.get("snippet", "")
            doc_attrs = {"id": doc_id}
            if title:
                doc_attrs["title"] = title
            if score is not None:
                doc_attrs["score"] = f"{score:.2f}" if isinstance(score, float) else str(score)
            child_strings.append(wrap_xml_envelope("document", body=content, **doc_attrs))

    if not child_strings:
        return ""

    count = match_count if match_count is not None else len(child_strings)
    attrs: dict[str, Any] = {
        "source": source,
        "match_count": count,
    }
    if include_query and query:
        attrs["query"] = query

    return wrap_xml_envelope(
        "context_retrieval",
        body=child_strings,
        **attrs,
    )


def build_autonomous_trigger_envelope(
    trigger_type: str,
    entity_id: str | None = None,
    severity: str | None = None,
    summary: str | None = None,
    directive: str | None = None,
) -> str:
    """Build a standardized <autonomous_trigger> envelope for background events.

    Args:
        trigger_type: Event identifier (e.g. 'task_overdue', 'research_stalled').
        entity_id: Optional ID of the task/event/alarm.
        severity: Optional urgency level ('low', 'medium', 'high', 'critical').
        summary: Human-readable summary of the trigger.
        directive: Operational instruction for how the agent should handle the event.

    Returns:
        Structured <autonomous_trigger> XML block.
    """
    children = []
    if summary:
        children.append(f"<summary>{escape_xml_content(summary)}</summary>")
    if directive:
        children.append(f"<directive>{escape_xml_content(directive)}</directive>")

    attrs = {"type": trigger_type}
    if entity_id:
        attrs["entity_id"] = entity_id
    if severity:
        attrs["severity"] = severity

    return wrap_xml_envelope("autonomous_trigger", body=children, **attrs)


def build_system_event_envelope(
    event: str,
    timestamp: str | None = None,
    status: str | None = None,
    description: str | None = None,
) -> str:
    """Build a standardized <system_event> envelope for server runtime telemetry.

    Args:
        event: Event identifier (e.g. 'research_ready', 'tool_completed', 'daemon_status').
        timestamp: Optional timestamp string.
        status: Optional status indicator ('completed', 'failed', 'active').
        description: Natural language summary or payload.

    Returns:
        Structured <system_event> XML block.
    """
    attrs = {"event": event}
    if timestamp:
        attrs["timestamp"] = timestamp
    if status:
        attrs["status"] = status

    body_content = escape_xml_content(description) if description else None
    return wrap_xml_envelope("system_event", body=body_content, self_closing_if_empty=True, **attrs)


def build_memory_context_envelope(
    category: str,
    subject: str,
    observation: str,
) -> str:
    """Build a standardized <memory_context> envelope for fast memory / profile facts.

    Args:
        category: Fast memory category code (e.g. 'Cat01-U', 'Cat08-A').
        subject: Entity name (e.g. cfg.USER_NAME, cfg.ASSISTANT_NAME).
        observation: Extracted fact statement.

    Returns:
        Structured <memory_context> XML block or empty string if observation is empty.
    """
    if not observation or not observation.strip():
        return ""

    return wrap_xml_envelope(
        "memory_context",
        body=escape_xml_content(observation.strip()),
        category=category,
        subject=subject,
    )


def stack_envelopes(*envelopes: str | None) -> str:
    """Stack multiple XML envelopes in canonical deterministic order.

    Canonical Order:
      1. <temporal_context>
      2. <system_event> / <autonomous_trigger>
      3. <context_retrieval> / <memory_context>
      4. Other custom XML envelopes

    Args:
        *envelopes: Sequence of XML envelope strings.

    Returns:
        Double-newline joined string of non-empty envelopes.
    """
    valid = [e.strip() for e in envelopes if e and str(e).strip()]
    if not valid:
        return ""

    def _tag_priority(env_str: str) -> int:
        if env_str.startswith("<temporal_context"):
            return 1
        if env_str.startswith(("<system_event", "<autonomous_trigger")):
            return 2
        if env_str.startswith(("<context_retrieval", "<memory_context")):
            return 3
        return 4

    sorted_envelopes = sorted(valid, key=_tag_priority)
    return "\n\n".join(sorted_envelopes)


def inject_envelope_to_turn(user_content: str, envelope: str | list[str] | None) -> str:
    """Prepend structured XML envelope(s) to a message turn with clean double-newline isolation.

    Args:
        user_content: Raw message text from user or agent.
        envelope: Single XML string or list/tuple of envelopes to stack.

    Returns:
        Turn string with envelope placed cleanly at the top.
    """
    if not envelope:
        return user_content or ""

    stacked = (
        stack_envelopes(*envelope)
        if isinstance(envelope, (list, tuple))
        else str(envelope).strip()
    )

    if not stacked:
        return user_content or ""

    clean_content = (user_content or "").strip()
    if not clean_content:
        return stacked

    return f"{stacked}\n\n{clean_content}"


def extract_link_context(body: str, target: str, window_chars: int = 180) -> str:
    """Extract a clean, non-YAML excerpt surrounding a target wikilink in document body.

    Supports case-insensitive matching and aliased wikilinks ([[Target]] or [[Target|Alias]]).
    Strips internal YAML boundary markers, linebreaks, and bounding non-alphanumeric noise.

    Args:
        body: Markdown body text (after frontmatter).
        target: Target entity name of the wikilink.
        window_chars: Characters of surrounding context before and after the match.

    Returns:
        str: Clean single-paragraph context string.
    """
    if not body or not target:
        return ""
    pattern = re.compile(rf"\[\[\s*{re.escape(target)}(?:\|[^\]\n]*)?\s*\]\]", re.IGNORECASE)
    match = pattern.search(body)
    if not match:
        return ""
    start = max(0, match.start() - window_chars)
    end = min(len(body), match.end() + window_chars)
    raw_slice = body[start:end]
    # Strip markdown table syntax or frontmatter boundaries if slice caught them
    raw_slice = raw_slice.replace("---", " ").replace("|", " ")
    clean = " ".join(raw_slice.split())
    clean = re.sub(r"^[\W_]+|[\W_]+$", "", clean)
    return clean


def estimate_tokens(text: str) -> int:
    """Fast conservative token estimator (~2.5 chars/token for dense text/code/json).

    Args:
        text: String content to estimate.

    Returns:
        int: Conservative estimate of token count (at least 1 for non-empty text, 0 if empty).
    """
    if not text:
        return 0
    return max(1, int(len(text) / 2.5) + 4)


def truncate_to_token_budget(
    text: str,
    max_tokens: int,
    truncation_suffix: str = "\n\n[... Truncated to stay within token budget ...]",
) -> str:
    """Truncate text to fit within a specified token budget.

    Truncates at newline or word boundary where possible.

    Args:
        text: Input string to truncate.
        max_tokens: Maximum allowed tokens.
        truncation_suffix: Suffix to append if truncation occurs.

    Returns:
        str: Original text or cleanly truncated string within budget.
    """
    if not text or max_tokens <= 0:
        return ""

    if estimate_tokens(text) <= max_tokens:
        return text

    # Target character limit based on conservative estimate
    target_chars = max(20, int(max_tokens * 2.5) - len(truncation_suffix))
    if len(text) <= target_chars:
        return text

    candidate = text[:target_chars]
    # Try breaking at last newline
    last_newline = candidate.rfind("\n")
    if last_newline > target_chars * 0.6:
        candidate = candidate[:last_newline]
    else:
        # Otherwise break at last space
        last_space = candidate.rfind(" ")
        if last_space > target_chars * 0.6:
            candidate = candidate[:last_space]

    return f"{candidate.rstrip()}{truncation_suffix}"


def extract_markdown_outline(content: str, max_headers: int = 15) -> list[str]:
    """Extract markdown header outline (# through ####) from markdown content.

    Excludes headers inside fenced code blocks.

    Args:
        content: Markdown file text.
        max_headers: Maximum number of headers to extract.

    Returns:
        list[str]: Formatted header outline list.
    """
    if not content:
        return []

    headers: list[str] = []
    in_code_block = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue

        if stripped.startswith(("# ", "## ", "### ", "#### ")):
            headers.append(stripped)
            if len(headers) >= max_headers:
                break

    return headers


