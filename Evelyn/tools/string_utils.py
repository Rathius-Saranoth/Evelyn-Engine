# string_utils.py
# date created: 2026-08-28 12:25:00
# date modified: 2026-08-28 12:25:00
# tags: #utils, #strings, #sanitization, #slugify, #gist

"""
string_utils.py — Canonical String Processing, Sanitization & Text Normalization.

Exports:
    sanitize_filename()     — Strips illegal filesystem characters and normalizes whitespace.
    slugify()               — Converts text into standard snake_case or kebab-case identifiers.
    clean_title()           — Cleans file stems or headings into standardized Title Case.
    strip_thinking_tags()   — Strips CoT <think> tags and LLM formatting artefacts.
    clean_llm_gist()        — Cleans summaries, stripping thinking tags, LaTeX, and prefixes.

Key config: Standard library only (zero internal project dependencies).
See also: reference/engine_architecture.md
"""

from __future__ import annotations

import re
import unicodedata


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


def sanitize_filename(name: str, max_length: int = 200, default: str = "untitled") -> str:
    """Strip illegal filesystem characters and collapse whitespace.

    Strips characters illegal on Linux/Windows/macOS (/ \\ : * ? " < > |)
    and removes non-printable / control characters.

    Args:
        name: Desired filename or note title.
        max_length: Maximum allowed character length for the output.
        default: Fallback string if sanitization leaves name empty.

    Returns:
        Safe filesystem filename string.
    """
    if not name:
        return default

    # Normalize unicode
    clean = unicodedata.normalize("NFKC", str(name))
    # Replace illegal filesystem characters with space
    clean = re.sub(r'[/\\:*?"<>|\x00-\x1f\x7f]', " ", clean)
    # Collapse multiple spaces into one
    clean = re.sub(r"\s+", " ", clean).strip()
    # Strip leading/trailing dots or spaces (problematic on Windows/SMB)
    clean = clean.strip(". ")

    if not clean:
        return default

    return clean[:max_length].rstrip(". ")


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

    # If underscores exist and it is not an all-caps code (like SEC_10K_2026), replace underscores with spaces
    if "_" in clean and not re.match(r"^[A-Z0-9_-]+$", clean):
        clean = clean.replace("_", " ")

    # Collapse whitespace
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean
