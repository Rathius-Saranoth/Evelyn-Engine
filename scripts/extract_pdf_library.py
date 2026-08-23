# extract_pdf_library.py
# date created: 2026-04-17 21:17:42
# date modified: 2026-08-22 19:16:00
# tags: #pdf, #extraction, #library, #parsing, #sidecar, #normalization, #tools

"""
extract_pdf_library.py — Extract PDFs into structured Obsidian markdown & Sidecar Index Cards.

Standalone, reusable tool that:
  1. Reads a PDF and detects chapter/section boundaries via font-size heuristics
  2. Splits into one .md file per chapter/major section
  3. Normalizes concatenated filenames into clean Title Case and subtitle metadata
  4. Generates rich Library Index Cards (.md Sidecars) with YAML frontmatter, attachment embeds, and semantic links
  5. Relocates source binaries into Attachments/Source Material/<Domain>/ for clean graph rendering
  6. Writes to the Obsidian Vault (or custom output dir) for RAG integration

Usage:
    python extract_pdf_library.py                               # All PDFs in default drop dir
    python extract_pdf_library.py "path/to/file.pdf"            # Single file
    python extract_pdf_library.py "path/to/folder"              # All PDFs in folder
    python extract_pdf_library.py --output "custom/path"        # Custom output dir
    python extract_pdf_library.py --domain "AI" --move-source   # Relocate source PDF to Attachments/Source Material/AI/
    python extract_pdf_library.py --skip-gists                  # Skip Ollama summarization
    python extract_pdf_library.py --dry-run                     # Preview structure only

Designed to plug into Evelyn's existing RAG pipeline:
  - Vault map generator discovers new .md files automatically
  - Gist ingestion embeds summaries into ChromaDB
  - search_vault + recall_specific_memory work out of the box
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field

# Anchor workspace roots for imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for _p in (ROOT_DIR, TOOLS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evelyn_config as cfg  # noqa: E402
import fitz  # pymupdf

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_INPUT_DIR = getattr(cfg, "PDF_DROP_DIR", r"/tmp")
DEFAULT_OUTPUT_DIR = os.path.join(getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault"), "Reference Library")
DEFAULT_ATTACHMENTS_DIR = os.path.join(getattr(cfg, "VAULT_BASE_DIR", r"/home/rathius/obsidian_vault"), "Attachments", "Source Material")

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "gemma4:26b"  # Overridden by --model CLI arg

# Font-size thresholds (relative to body text size)
HEADING_RATIO_H1 = 1.5    # Chapter titles / Part titles
HEADING_RATIO_H2 = 1.25   # Section headers
HEADING_RATIO_H3 = 1.1    # Sub-section headers

# Minimum text length for a block to count as body text (for font-size stats)
MIN_BODY_TEXT_LEN = 40

# Maximum heading text length — longer text is probably a paragraph, not a heading
MAX_HEADING_LEN = 120

# Minimum chapter length in characters to be worth its own file
MIN_CHAPTER_LEN = 200


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TextBlock:
    """A contiguous block of text with consistent formatting."""
    text: str
    font_size: float
    is_bold: bool
    page_num: int
    max_span_size: float = 0.0  # Largest individual span in this block
    has_drop_cap: bool = False  # Block starts with a single large letter


@dataclass
class Section:
    """A chapter or major section of the book."""
    title: str
    level: int  # 1 = chapter, 2 = section, 3 = subsection
    content: str = ""
    page_num: int = 0


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def extract_text_blocks(doc: fitz.Document) -> list[TextBlock]:
    """Extract all text blocks from a PDF with font metadata."""
    blocks = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

        for block in page_dict["blocks"]:
            if block["type"] != 0:  # text blocks only
                continue

            # Collect all spans for analysis
            all_spans = []
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    if text:
                        all_spans.append(span)

            if not all_spans:
                continue

            # Build the full block text from lines
            line_texts = []
            for line in block["lines"]:
                line_text = "".join(s["text"] for s in line["spans"]).strip()
                if line_text:
                    line_texts.append(line_text)

            full_text = " ".join(line_texts)
            if not full_text.strip():
                continue

            # Font-size analysis
            size_weights = Counter()
            bold_chars = 0
            total_chars = 0
            max_span_size = 0.0
            for span in all_spans:
                char_count = len(span["text"].strip())
                sz = round(span["size"], 1)
                size_weights[sz] += char_count
                total_chars += char_count
                max_span_size = max(max_span_size, sz)
                if "bold" in span.get("font", "").lower() or (span.get("flags", 0) & 16):
                    bold_chars += char_count

            dominant_size = size_weights.most_common(1)[0][0]
            is_bold = bold_chars > total_chars * 0.5

            # Detect drop-cap pattern: first span is a single large letter,
            # rest of the block is body-sized text
            has_drop_cap = False
            if len(all_spans) >= 2:
                first_text = all_spans[0]["text"].strip()
                first_size = round(all_spans[0]["size"], 1)
                # Drop-cap: single letter at 1.5x+ body size, followed by normal text
                if (len(first_text) <= 2
                        and first_text[0].isupper()
                        and first_size > dominant_size * 1.3):
                    has_drop_cap = True

            blocks.append(TextBlock(
                text=full_text,
                font_size=dominant_size,
                is_bold=is_bold,
                page_num=page_num,
                max_span_size=max_span_size,
                has_drop_cap=has_drop_cap,
            ))

    return blocks


def determine_body_font_size(blocks: list[TextBlock]) -> float:
    """Determine the most common (body text) font size."""
    size_char_counts = Counter()
    for block in blocks:
        if len(block.text) >= MIN_BODY_TEXT_LEN:
            size_char_counts[block.font_size] += len(block.text)

    if not size_char_counts:
        all_sizes = Counter()
        for block in blocks:
            all_sizes[block.font_size] += len(block.text)
        return all_sizes.most_common(1)[0][0] if all_sizes else 10.0

    return size_char_counts.most_common(1)[0][0]


def classify_heading_level(font_size: float, body_size: float) -> int:
    """Classify a heading level based on font size ratio to body text."""
    ratio = font_size / body_size if body_size > 0 else 1.0
    if ratio >= HEADING_RATIO_H1:
        return 1
    elif ratio >= HEADING_RATIO_H2:
        return 2
    elif ratio >= HEADING_RATIO_H3:
        return 3
    return 0


def is_junk_text(text: str, body_size: float = 0, font_size: float = 0) -> bool:
    """Detect page numbers, headers/footers, decorative elements, and other noise."""
    clean = text.strip()

    # Pure page numbers
    if re.match(r'^[\divxlcm]+$', clean, re.IGNORECASE):
        return True

    # Page number patterns like "| 1" or "1 |" or "• 1 •"
    if re.match(r'^[\|\s•]*\d+[\|\s•]*$', clean):
        return True

    # Very short roman numerals
    if len(clean) <= 4 and re.match(r'^[ivxlcm]+$', clean, re.IGNORECASE):
        return True

    # Copyright/publisher boilerplate
    if any(marker in clean.lower() for marker in [
        "all rights reserved", "isbn", "library of congress",
        "printed in the", "puddledancer press", "© "
    ]):
        return True

    # Single punctuation / decorative characters (including large curly quotes)
    if len(clean) <= 2 and not clean.isalpha():
        return True

    # Single letter that isn't part of a meaningful block (standalone drop-cap artifacts)
    if len(clean) == 1 and clean.isalpha():
        return True

    # Running header/footer patterns: small-caps book title with underscores
    if '_____' in clean:
        return True

    # Small-caps running headers like "NONVIOLENT COMMUNICATION" at smaller-than-body size
    if body_size and font_size and font_size < body_size * 0.9:
        if clean.isupper() or re.match(r'^[A-Z\s]+$', clean):
            return True

    return False


def is_toc_text(text: str) -> bool:
    """Detect table of contents entries."""
    clean = text.strip()
    if re.match(r'^(Chapter|Part|Section)\s+\d+.*\d+\s*$', clean, re.IGNORECASE):
        return True
    if re.search(r'\.{3,}\s*\d+\s*$', clean):
        return True
    return False


def is_chapter_marker(text: str) -> bool:
    """Detect 'chapter one', 'chapter 1', 'C H A P T E R  O N E' style markers."""
    clean = text.strip().lower()

    number_words = (
        r'one|two|three|four|five|six|seven|eight|nine|ten|'
        r'eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|'
        r'eighteen|nineteen|twenty|\d+'
    )
    pattern = rf'^chapter\s+({number_words})\s*$'

    # Try matching the original text first
    if re.match(pattern, clean):
        return True

    # Try collapsing spaced-out letters (e.g., "C H A P T E R  O N E")
    # Only collapse single-char sequences separated by spaces
    if re.match(r'^([a-z]\s+){3,}', clean):
        collapsed = re.sub(r'(?<=[a-z])\s+(?=[a-z])', '', clean)
        collapsed = re.sub(r'\s{2,}', ' ', collapsed)
        if re.match(pattern, collapsed):
            return True

    return False


def is_part_marker(text: str) -> bool:
    """Detect 'PART ONE', 'PART 1', 'PART I' style markers."""
    clean = text.strip()
    number_words = (
        r'ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|'
        r'I{1,3}|IV|V|VI{0,3}|[1-9]'
    )
    if re.match(rf'^PART\s+({number_words})\s*$', clean, re.IGNORECASE):
        return True
    return False


def detect_front_matter_end(blocks: list[TextBlock], body_size: float) -> int:
    """Find where the actual content starts (after title page, copyright, TOC, etc.)."""
    content_markers = [
        r'introduction',
        r'preface',
        r'foreword',
        r'chapter\s*(one|1)',
        r'part\s*(one|1|i)\b',
        r'acknowledgements?',
    ]

    body_text_count = 0
    first_body_page = None

    for i, block in enumerate(blocks):
        text_lower = block.text.lower().strip()

        if block.font_size == body_size and len(block.text) >= MIN_BODY_TEXT_LEN:
            body_text_count += 1
            if first_body_page is None:
                first_body_page = block.page_num

        ratio = block.font_size / body_size if body_size > 0 else 1.0
        if ratio >= HEADING_RATIO_H3 and len(text_lower) < MAX_HEADING_LEN:
            for marker in content_markers:
                if re.search(marker, text_lower):
                    return i

        # Also detect "chapter one" / "chapter 1" at any font size (LoveLang uses body-size bold)
        if is_chapter_marker(block.text):
            return i

    if first_body_page is not None and first_body_page >= 2:
        for i, block in enumerate(blocks):
            if block.page_num >= first_body_page:
                return max(0, i - 1)

    return 0


def build_sections(blocks: list[TextBlock], body_size: float,
                   start_idx: int = 0) -> list[Section]:
    """Build a hierarchical section list from text blocks."""
    sections = []
    current_section = None
    current_content_parts = []
    pending_chapter_marker = None  # Holds "chapter one" text, next heading becomes title
    pending_part_label = None      # Holds "PART ONE" text, prepended to next chapter

    def flush_section():
        nonlocal current_section
        if current_section is not None:
            current_section.content = "\n\n".join(current_content_parts).strip()
            if current_section.content or current_section.title:
                sections.append(current_section)

    for i, block in enumerate(blocks):
        if i < start_idx:
            continue

        # --- Priority 1: Chapter/Part marker detection (before junk filter) ---
        # These markers must be detected even on blocks that would otherwise be junk

        # Detect "chapter one" markers — set flag, next heading becomes the title
        if is_chapter_marker(block.text):
            pending_chapter_marker = block.text.strip()
            continue

        # Detect "PART ONE" markers — store label for next chapter
        if is_part_marker(block.text):
            pending_part_label = block.text.strip()
            continue

        # If we have a pending chapter marker, check if THIS block is the title
        # (before junk filter, because decorative titles may look like junk)
        if pending_chapter_marker:
            is_decorative_title = (
                block.is_bold
                and len(block.text) <= MAX_HEADING_LEN
                and block.max_span_size >= body_size * HEADING_RATIO_H1
            )
            heading_level_check = classify_heading_level(block.font_size, body_size)
            is_heading_candidate = (
                heading_level_check > 0 and len(block.text) <= MAX_HEADING_LEN
            )
            # Any short bold block right after a chapter marker is likely the title
            # (catches labels like "Love Language #2" that are at body-size bold)
            is_short_bold_title = (
                block.is_bold
                and len(block.text) <= 60
                and not block.has_drop_cap
            )
            if is_decorative_title or is_heading_candidate or is_short_bold_title:
                flush_section()
                title = clean_heading_text(block.text)
                if pending_part_label:
                    title = f"{pending_part_label} — {title}"
                    pending_part_label = None
                current_section = Section(
                    title=title,
                    level=1,
                    page_num=block.page_num,
                )
                current_content_parts = []
                pending_chapter_marker = None
                continue

        # --- Priority 2: Junk/TOC/drop-cap filtering ---

        # Skip junk (page numbers, running headers, decorative elements)
        if is_junk_text(block.text, body_size, block.font_size):
            continue

        # Skip TOC entries
        if is_toc_text(block.text):
            continue

        # Skip blocks that are just drop-caps with minimal text following
        if block.has_drop_cap and len(block.text.strip()) <= 3:
            continue

        # If we still have a pending chapter marker and this is body text,
        # use the chapter marker itself as the chapter title
        if pending_chapter_marker:
            flush_section()
            title = clean_heading_text(pending_chapter_marker)
            if pending_part_label:
                title = f"{pending_part_label} — {title}"
                pending_part_label = None
            current_section = Section(
                title=title,
                level=1,
                page_num=block.page_num,
            )
            current_content_parts = []
            pending_chapter_marker = None
            # Fall through to process this block as body text

        # Classify heading level
        heading_level = classify_heading_level(block.font_size, body_size)

        # Blocks with drop-caps are body text, not headings, even if max_span_size
        # is large (the drop-cap inflates the max but the dominant size is body)
        if block.has_drop_cap:
            heading_level = 0

        is_likely_heading = (
            heading_level > 0
            and len(block.text) <= MAX_HEADING_LEN
        )

        # Catch bold text at body size that looks like a section header
        # but NOT if it's longer than ~60 chars (probably a callout/emphasis paragraph)
        if (not is_likely_heading
                and block.is_bold
                and len(block.text) <= 60
                and block.font_size >= body_size
                and not block.has_drop_cap):
            is_likely_heading = True
            heading_level = max(heading_level, 3)



        if is_likely_heading and heading_level <= 2:
            # New chapter or major section
            flush_section()
            title = clean_heading_text(block.text)
            if pending_part_label:
                title = f"{pending_part_label} — {title}"
                pending_part_label = None
            current_section = Section(
                title=title,
                level=heading_level,
                page_num=block.page_num,
            )
            current_content_parts = []
        elif is_likely_heading and heading_level == 3:
            # Sub-section header
            if current_section is None:
                current_section = Section(
                    title="Introduction",
                    level=1,
                    page_num=block.page_num,
                )
                current_content_parts = []
            current_content_parts.append(f"### {clean_heading_text(block.text)}")
        else:
            # Body text
            if current_section is None:
                title = "Introduction"
                if pending_part_label:
                    title = pending_part_label
                    pending_part_label = None
                current_section = Section(
                    title=title,
                    level=1,
                    page_num=block.page_num,
                )
                current_content_parts = []

            cleaned = clean_body_text(block.text)
            if cleaned:
                current_content_parts.append(cleaned)

    flush_section()
    return sections


def clean_heading_text(text: str) -> str:
    """Clean up heading text — remove numbering artifacts, normalize spacing."""
    # Remove patterns like "| 1" or "1 |"
    text = re.sub(r'\s*\|\s*\d*\s*', '', text).strip()
    text = re.sub(r'\s+', ' ', text)  # Normalize whitespace
    text = text.rstrip('.')
    # Collapse spaced-out letters like "C H A P T E R  O N E"
    if re.match(r'^([A-Z]\s+){3,}', text):
        text = re.sub(r'(?<=[A-Z])\s+(?=[A-Z])', '', text)
        text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def clean_body_text(text: str) -> str:
    """Clean body text — fix hyphenation, normalize spacing."""
    # Fix word-break hyphenation at line ends
    text = re.sub(r'(\w)-\s+(\w)', r'\1\2', text)
    # Normalize multiple spaces to single
    text = re.sub(r'  +', ' ', text)
    return text.strip()


# ---------------------------------------------------------------------------
# Chapter grouping
# ---------------------------------------------------------------------------

def group_into_chapters(sections: list[Section]) -> list[Section]:
    """Group sections into chapter-level files."""
    if not sections:
        return []

    # Determine optimal split level: if only 0 or 1 section at level 1, split on level 2
    level_1_count = sum(1 for s in sections if s.level <= 1)
    split_level = 1
    if level_1_count <= 1:
        higher_levels = [s.level for s in sections if s.level > 1]
        if higher_levels:
            split_level = min(higher_levels)

    chapters = []
    current_chapter = None
    current_parts = []

    def flush_chapter():
        nonlocal current_chapter
        if current_chapter is not None:
            current_chapter.content = "\n\n".join(current_parts).strip()
            # Only append if there is actual title or content
            if current_chapter.title or current_chapter.content:
                chapters.append(current_chapter)

    for section in sections:
        if section.level <= split_level:
            flush_chapter()
            current_chapter = Section(
                title=section.title,
                level=1,
                page_num=section.page_num,
            )
            current_parts = []
            if section.content:
                current_parts.append(section.content)
        else:
            if current_chapter is None:
                current_chapter = Section(
                    title=section.title,
                    level=1,
                    page_num=section.page_num,
                )
                current_parts = []

            heading_prefix = "#" * max(2, section.level - split_level + 2)
            current_parts.append(f"{heading_prefix} {section.title}")
            if section.content:
                current_parts.append(section.content)

    flush_chapter()

    # Filter out empty cover pages
    valid_chapters = [ch for ch in chapters if len(ch.content) > 0 or len(chapters) == 1]
    if not valid_chapters and chapters:
        valid_chapters = chapters

    # Merge very short chapters into their predecessor
    merged = []
    for ch in valid_chapters:
        if len(ch.content) < MIN_CHAPTER_LEN and merged:
            merged[-1].content += f"\n\n## {ch.title}\n\n{ch.content}"
        else:
            merged.append(ch)

    return merged



# ---------------------------------------------------------------------------
# Normalization & Sidecar Generation
# ---------------------------------------------------------------------------

TECH_TERM_MAP = {
    "ai": "AI",
    "ml": "ML",
    "llm": "LLM",
    "llms": "LLMs",
    "llmops": "LLMOps",
    "pytorch": "PyTorch",
    "scikit-learn": "Scikit-Learn",
    "langchain": "LangChain",
    "rag": "RAG",
    "nlp": "NLP",
    "cuda": "CUDA",
    "gpu": "GPU",
    "gpus": "GPUs",
    "api": "API",
    "apis": "APIs",
    "devops": "DevOps",
    "multiagent": "Multi-Agent",
    "multiagents": "Multi-Agents",
}

STOP_WORDS = {"a", "an", "the", "and", "or", "but", "for", "nor", "on", "at", "to", "from", "by", "with", "in", "of"}

KNOWN_VOCABULARY = [
    "building", "applications", "application", "designing", "implementing", "systems", "system",
    "crafting", "engineering", "strategy", "thoughtful", "decisions", "solve", "solving", "complex",
    "problems", "problem", "developers", "developer", "playbook", "models", "model", "security",
    "executives", "executive", "primer", "impactful", "technical", "leadership", "generative",
    "design", "patterns", "pattern", "hands-on", "machine", "learning", "coders", "coder",
    "visualizing", "writes", "paints", "assists", "prompt", "prompts", "large", "language",
    "understanding", "deep", "neural", "networks", "network", "reinforcement", "practical",
    "foundations", "foundation", "advanced", "guide", "handbook", "cookbook", "reference",
    "introduction", "mastering", "essential", "essentials", "architecture", "architectures",
    "data", "science", "python", "javascript", "typescript", "rust", "cplusplus", "golang",
    "docker", "kubernetes", "cloud", "aws", "gcp", "azure", "agent", "agents", "multiagent",
    "multi", "ops", "llmops", "pytorch", "scikit-learn", "scikit", "learn", "langchain", "ai", "ml",
    "augmented", "human", "emotional", "intelligence", "nonviolent", "communication", "love", "languages",
    "cello", "first", "lessons", "method", "manual", "spec", "sheet",
    "for", "with", "and", "in", "how", "what", "why", "the", "to", "of", "from", "on"
]



def segment_concatenated_words(text: str) -> str:
    """Segment a lowercase concatenated string into separated words using dynamic programming."""
    clean = text.lower().strip()
    if not clean:
        return ""

    vocab = set(KNOWN_VOCABULARY) | set(TECH_TERM_MAP.keys()) | set(STOP_WORDS)
    n = len(clean)


    # dp[i] holds the best word list for clean[:i]
    dp: dict[int, list[str]] = {0: []}
    for i in range(1, n + 1):
        best_match = None
        for j in range(max(0, i - 25), i):
            if j in dp:
                word = clean[j:i]
                if word in vocab:
                    candidate = dp[j] + [word]
                    # Fewer total words is preferred (greedier on longer vocab matches)
                    if best_match is None or len(candidate) < len(best_match):
                        best_match = candidate

        if best_match is not None:
            dp[i] = best_match
        elif (i - 1) in dp:
            dp[i] = dp[i - 1] + [clean[i - 1]]

    words = dp.get(n, [clean])
    # If the segmentation resulted in singleton characters (failed segmentation), keep original token
    if any(len(w) == 1 and w.lower() not in {"a", "i"} for w in words):
        return clean
    return " ".join(words)




def segment_text_with_hyphens(text: str) -> str:
    """Segment a string that may contain hyphens or spaces while preserving hyphenated compounds."""
    # Replace known compound hyphens like hands-on or scikit-learn
    tokens = re.split(r'([-\s_]+)', text)
    out = []
    for tok in tokens:
        if re.match(r'^[-\s_]+$', tok):
            out.append(tok)
        else:
            out.append(segment_concatenated_words(tok))
    return "".join(out)


def title_case_phrase(phrase: str) -> str:
    """Convert a space-separated phrase into proper Title Case respecting acronyms and stop words."""
    tokens = phrase.replace("-", " - ").replace("_", " ").split()
    result = []
    for i, tok in enumerate(tokens):
        if tok == "-":
            result.append("-")
            continue
        tok_clean = tok.lower()
        if tok_clean in TECH_TERM_MAP:
            result.append(TECH_TERM_MAP[tok_clean])
        elif i > 0 and tok_clean in STOP_WORDS:
            result.append(tok_clean)
        else:
            result.append(tok.capitalize())

    # Re-stitch hyphenated terms like "Hands - On" -> "Hands-On"
    formatted = " ".join(result)
    formatted = re.sub(r'\s+-\s+', '-', formatted)
    return formatted


def normalize_book_title(filename_or_path: str, doc_metadata: dict | None = None) -> tuple[str, str]:
    """Normalize a messy PDF filename or metadata into a clean Title and Subtitle.

    Args:
        filename_or_path: The filename or absolute path of the PDF.
        doc_metadata: Optional PyMuPDF doc.metadata dictionary.

    Returns:
        tuple[str, str]: (clean_title, subtitle)
    """
    raw_base = os.path.splitext(os.path.basename(filename_or_path))[0]

    # Check for manual / specsheet prefixes
    if re.match(r'^(manual|specsheet|spec sheet|guide)\s*-\s*', raw_base, re.IGNORECASE):
        parts = re.split(r'\s*-\s*', raw_base, maxsplit=1)
        prefix = parts[0].strip()
        device_raw = parts[1].strip()
        clean_title = device_raw
        clean_sub = "Specification Sheet" if "spec" in prefix.lower() else "User Manual"
        return clean_title, clean_sub

    # Check if doc_metadata has a valid, readable title
    if doc_metadata:
        meta_title = (doc_metadata.get("title") or "").strip()
        tokens = meta_title.split()
        single_chars = sum(1 for t in tokens if len(t) == 1)
        is_garbled = (len(tokens) > 2 and (single_chars / len(tokens)) > 0.3)
        if meta_title and len(meta_title) >= 4 and not is_garbled and not meta_title.lower().startswith("untitled") and not meta_title.endswith(".pdf"):
            if ":" in meta_title:
                parts = meta_title.split(":", 1)
                return parts[0].strip(), parts[1].strip()
            elif " - " in meta_title:
                parts = meta_title.split(" - ", 1)
                return parts[0].strip(), parts[1].strip()
            return meta_title, ""

    # Split on underscore or colon if present
    if "_" in raw_base:
        parts = raw_base.split("_", 1)
        title_raw = parts[0]
        sub_raw = parts[1]
    elif " - " in raw_base:
        parts = raw_base.split(" - ", 1)
        title_raw = parts[0]
        sub_raw = parts[1]
    else:
        title_raw = raw_base
        sub_raw = ""

    # Segment and format title
    title_segmented = segment_text_with_hyphens(title_raw)
    clean_title = title_case_phrase(title_segmented)


    # Segment and format subtitle
    clean_sub = ""
    if sub_raw:
        sub_segmented = segment_text_with_hyphens(sub_raw)
        clean_sub = title_case_phrase(sub_segmented)

    return clean_title, clean_sub



def sanitize_filename(name: str) -> str:
    """Make a string safe for use as a filename."""
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    if len(name) > 80:
        name = name[:80].strip()
    return name


def format_chapter_filename(index: int, title: str, total_count: int = 100) -> str:
    """Generate a zero-padded section filename like '001 - What Are Emotions For.md'."""
    safe_title = sanitize_filename(title)
    pad_width = max(2, len(str(total_count)))
    return f"{index:0{pad_width}d} - {safe_title}.md"


def format_chapter_markdown(chapter: Section, book_title: str) -> str:
    """Format a chapter as Obsidian-compatible markdown."""
    frontmatter = f"""---
tags: [reference-library, {sanitize_tag(book_title)}]
source: "{book_title}"
type: reference-chapter
---

"""
    heading = f"# {chapter.title}\n\n"
    return frontmatter + heading + chapter.content


def sanitize_tag(text: str) -> str:
    """Convert book title to a kebab-case tag."""
    tag = text.lower().strip()
    tag = re.sub(r'[^a-z0-9\s-]', '', tag)
    tag = re.sub(r'\s+', '-', tag)
    return tag


def generate_sidecar_card(
    title: str,
    subtitle: str = "",
    author: str = "",
    attachment_rel_path: str = "",
    chapters: list[Section] | None = None,
    gists: dict[str, str] | None = None,
    overview_gist: str = "",
    tags: list[str] | None = None,
    aliases: list[str] | None = None,
    semantic_neighbors: list[dict] | None = None,
    referenced_entities: list[dict] | None = None,
) -> str:
    """Generate a rich Library Index Card (Sidecar Note) for a non-markdown asset.

    Args:
        title: Normalized book/document title.
        subtitle: Optional subtitle.
        author: Author string.
        attachment_rel_path: Relative vault path to the binary attachment (e.g. 'Attachments/Source Material/AI/book.pdf').
        chapters: Optional list of Section objects.
        gists: Optional map of chapter title to summary gist.
        overview_gist: 1-2 sentence overall summary of the document.
        tags: List of normalized taxonomy tags.
        aliases: List of alternative titles or keywords.
        semantic_neighbors: List of dicts from chroma_rag.find_semantic_neighbors.
        referenced_entities: List of dicts from vault_db.get_all_entities matched in the text.

    Returns:
        str: Fully formatted Obsidian markdown sidecar note.
    """
    all_tags = set(tags or [])
    all_tags.add("literature/reference")
    tag_lines = "\n".join(f"  - {t.lstrip('#')}" for t in sorted(all_tags))

    alias_list = list(aliases or [])
    if subtitle and subtitle not in alias_list:
        alias_list.append(subtitle)
    alias_lines = "\n".join(f"  - \"{a}\"" for a in alias_list) if alias_list else ""

    fm_aliases_block = f"aliases:\n{alias_lines}\n" if alias_lines else ""
    fm_source_block = f'source: "[[{attachment_rel_path}]]"\n' if attachment_rel_path else ""
    fm_sub_block = f'subtitle: "{subtitle}"\n' if subtitle else ""
    fm_author_block = f'authors: "{author}"\n' if author else ""

    frontmatter = f"""---
title: "{title}"
{fm_sub_block}type: literature/card
{fm_source_block}{fm_author_block}tags:
{tag_lines}
{fm_aliases_block}created: {time.strftime('%Y-%m-%d')}
status: unread
---

"""
    # Header & Overview Callout
    header = f"# {title}\n"
    if subtitle:
        header += f"### *{subtitle}*\n\n"
    else:
        header += "\n"

    if author:
        header += f"**Author**: {author}  \n"
    header += f"**Catalog Entry**: {time.strftime('%Y-%m-%d')}  \n\n"

    if overview_gist:
        overview_block = f"> [!summary] Evelyn Overview\n> {overview_gist}\n\n"
    else:
        overview_block = ""

    # Source Material Embed
    if attachment_rel_path:
        source_block = f"## Source Document\n![[{attachment_rel_path}]]\n\n"
    else:
        source_block = ""

    # Table of Contents
    toc_block = ""
    if chapters:
        toc_block = "## Chapters & Sections\n\n| # | Chapter | Page | Summary |\n|---|---|---|---|\n"
        gists_map = gists or {}
        pad_width = max(2, len(str(len(chapters))))
        for i, ch in enumerate(chapters, 1):
            ch_filename = format_chapter_filename(i, ch.title, len(chapters)).replace(".md", "")
            ch_gist = gists_map.get(ch.title, "_Summary pending_").replace("|", "—").replace("\n", " ")
            toc_block += f"| {i:0{pad_width}d} | [[{ch_filename}\\|{ch.title}]] | p. {ch.page_num} | {ch_gist} |\n"
        toc_block += "\n"


    # Semantic Connections
    sem_block = ""
    if semantic_neighbors:
        sem_block = "## Semantic Connections\n"
        for n in semantic_neighbors:
            n_title = n.get("title", "Related Note")
            sim = n.get("similarity", 0.0)
            snippet = n.get("snippet", "").replace("\n", " ")[:140]
            sem_block += f"- [[{n_title}]] (Match: `{int(sim * 100)}%`) — {snippet}...\n"
        sem_block += "\n"

    # Referenced Entities
    entity_block = ""
    if referenced_entities:
        entity_block = "## Referenced Vault Entities\n"
        for ent in referenced_entities[:8]:
            ent_title = ent.get("title", "")
            if ent_title and ent_title != title:
                entity_block += f"- [[{ent_title}]]\n"
        entity_block += "\n"

    return frontmatter + header + overview_block + source_block + toc_block + sem_block + entity_block


def generate_index_markdown(book_title: str, author: str,
                             chapters: list[Section],
                             gists: dict[str, str]) -> str:
    """Generate the _Index.md master TOC file (legacy compatibility)."""
    tag = sanitize_tag(book_title)
    frontmatter = f"""---
tags: [reference-library, reference-index, {tag}]
source: "{book_title}"
type: reference-index
---

"""
    header = f"# {book_title} — Reference Index\n\n"
    if author:
        header += f"**Author**: {author}  \n"
    header += f"**Source**: PDF extraction via extract_pdf_library.py  \n"
    header += f"**Extracted**: {time.strftime('%Y-%m-%d')}\n\n"

    toc = "## Table of Contents\n\n"
    toc += "| # | Section | Gist |\n"
    toc += "|---|---------|------|\n"

    for i, chapter in enumerate(chapters, 1):
        filename = format_chapter_filename(i, chapter.title)
        link_name = filename.replace(".md", "")
        gist = gists.get(chapter.title, "_Gist pending_")
        gist_safe = gist.replace("|", "—").replace("\n", " ")
        toc += f"| {i} | [[{link_name}]] | {gist_safe} |\n"

    return frontmatter + header + toc



# ---------------------------------------------------------------------------
# Ollama gist generation
# ---------------------------------------------------------------------------

def generate_gist(text: str, chapter_title: str, book_title: str,
                  skip_gist: bool = False) -> str:
    """Generate a 1-2 sentence gist of a chapter using Ollama."""
    if skip_gist:
        return "_Gist generation skipped_"

    import urllib.request

    max_chars = 12000
    truncated = text[:max_chars] if len(text) > max_chars else text

    user_message = (
        f"You are summarizing a chapter from the book \"{book_title}\". "
        f"The chapter is titled \"{chapter_title}\". "
        f"Write a concise 1-2 sentence gist that captures the core idea or argument of this chapter. "
        f"Be specific and substantive -- avoid vague descriptions like 'this chapter discusses...'. "
        f"Instead, state what the chapter actually teaches or argues.\n\n"
        f"Chapter text:\n{truncated}"
    )

    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "user", "content": user_message}
        ],
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 1024,  # Gemma 4 is a thinking model; needs room for CoT + answer
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read())
            message = result.get("message", {})
            gist = message.get("content", "").strip()
            gist = gist.strip('"').strip()
            return gist if gist else "_Could not generate gist_"
    except Exception as e:
        print(f"  [WARN] Gist generation failed: {e}")
        return "_Gist generation failed_"


# ---------------------------------------------------------------------------
# Book metadata extraction
# ---------------------------------------------------------------------------

def extract_book_metadata(doc: fitz.Document, filepath: str) -> tuple[str, str]:
    """Try to extract book title and author from PDF metadata or filename."""
    meta = doc.metadata or {}
    title = meta.get("title", "").strip()
    author = meta.get("author", "").strip()

    if not title or len(title) < 3:
        title = os.path.basename(filepath).replace(".pdf", "").replace("_", " ")

    if author and len(author) > 100:
        author = ""

    return title, author


# ---------------------------------------------------------------------------
# Main extraction pipeline
# ---------------------------------------------------------------------------

def extract_pdf(
    filepath: str,
    output_dir: str,
    skip_gists: bool = False,
    dry_run: bool = False,
    domain: str = "AI",
    attachments_dir: str = "/home/rathius/obsidian_vault/Attachments/Source Material",
    move_source: bool = False,
    create_sidecar: bool = True,
) -> dict:
    """Extract a single PDF into structured markdown files and a rich Sidecar Index Card."""
    print(f"\n{'='*60}")
    print(f"Processing: {os.path.basename(filepath)}")
    print(f"{'='*60}")

    doc = fitz.open(filepath)
    clean_title, subtitle = normalize_book_title(filepath, doc.metadata)
    _, author = extract_book_metadata(doc, filepath)

    full_display_title = f"{clean_title}: {subtitle}" if subtitle else clean_title
    print(f"  Title: {clean_title}")
    if subtitle:
        print(f"  Subtitle: {subtitle}")
    print(f"  Author: {author or '(unknown)'}")
    print(f"  Pages: {len(doc)}")

    # Step 1: Extract all text blocks
    print("  Extracting text blocks...")
    blocks = extract_text_blocks(doc)
    print(f"  Found {len(blocks)} text blocks")

    if not blocks:
        if create_sidecar:
            print("  Notice: Scanned/raster document with no selectable text. Generating Sidecar Index Card with embedded viewer.")
            chapters = []
        else:
            print("  ERROR: No text blocks found. Skipping.")
            doc.close()
            return {"title": clean_title, "chapters": 0, "error": "No text blocks"}
    else:
        # Step 2: Determine body text font size
        body_size = determine_body_font_size(blocks)
        print(f"  Body text font size: {body_size}")

        # Step 3: Detect where front matter ends
        content_start = detect_front_matter_end(blocks, body_size)
        if content_start > 0:
            print(f"  Content starts at block {content_start} (page {blocks[content_start].page_num})")

        # Step 4: Build section hierarchy
        print("  Building section hierarchy...")
        sections = build_sections(blocks, body_size, start_idx=content_start)
        print(f"  Found {len(sections)} raw sections")

        # Step 5: Group into chapters
        chapters = group_into_chapters(sections)
        print(f"  Grouped into {len(chapters)} chapters")


    if not chapters and not (create_sidecar and not blocks):
        print("  ERROR: No chapters detected. Skipping.")
        doc.close()
        return {"title": clean_title, "chapters": 0, "error": "No chapters detected"}

    # Preview chapter structure
    if chapters:
        print("\n  Chapter structure:")
        for i, ch in enumerate(chapters, 1):
            content_len = len(ch.content)
            print(f"    {i:2d}. {ch.title} ({content_len:,} chars, page {ch.page_num})")

    if dry_run:
        print("\n  [DRY RUN] Skipping file output.")
        doc.close()
        return {"title": clean_title, "chapters": len(chapters), "dry_run": True}


    # Step 6: Create output directory for chapters
    safe_folder_name = sanitize_filename(clean_title)
    book_dir = os.path.join(output_dir, safe_folder_name)
    os.makedirs(book_dir, exist_ok=True)
    print(f"\n  Output dir: {book_dir}")

    # Step 7: Handle Source Relocation to Attachments
    rel_attachment_path = ""
    if move_source:
        domain_att_dir = os.path.join(attachments_dir, domain)
        os.makedirs(domain_att_dir, exist_ok=True)
        dest_pdf_path = os.path.join(domain_att_dir, os.path.basename(filepath))
        if os.path.abspath(filepath) != os.path.abspath(dest_pdf_path):
            import shutil
            shutil.copy2(filepath, dest_pdf_path)
            print(f"  Copied source PDF -> {dest_pdf_path}")
        rel_attachment_path = os.path.relpath(dest_pdf_path, getattr(cfg, "VAULT_BASE_DIR", "/home/rathius/obsidian_vault")).replace("\\", "/")
    else:
        # Reference in-place
        rel_attachment_path = os.path.relpath(filepath, getattr(cfg, "VAULT_BASE_DIR", "/home/rathius/obsidian_vault")).replace("\\", "/")

    # Step 8: Generate gists and write chapter files
    gists = {}
    sample_text_for_overview = ""
    for i, chapter in enumerate(chapters, 1):
        filename = format_chapter_filename(i, chapter.title, len(chapters))
        filepath_out = os.path.join(book_dir, filename)

        if not skip_gists and chapter.content:
            if not sample_text_for_overview and len(chapter.content) > 300:
                sample_text_for_overview = chapter.content[:4000]
            print(f"  Generating gist for: {chapter.title}...")
            gist = generate_gist(chapter.content, chapter.title, clean_title)
            gists[chapter.title] = gist
            print(f"    -> {gist[:100]}{'...' if len(gist) > 100 else ''}")

        md_content = format_chapter_markdown(chapter, clean_title)
        with open(filepath_out, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"  Wrote: {filename}")

    # Step 9: Generate Overview Gist
    overview_gist = ""
    if not skip_gists and sample_text_for_overview:
        overview_gist = generate_gist(sample_text_for_overview, "Overview", full_display_title)

    # Step 10: Semantic Neighbors & Vault Entity Resolution
    semantic_neighbors = []
    referenced_entities = []
    try:
        import chroma_rag
        search_snippet = overview_gist or f"{clean_title} {subtitle}"
        semantic_neighbors = chroma_rag.find_semantic_neighbors(search_snippet, limit=3, min_similarity=0.60)
    except Exception as e:
        print(f"  [WARN] Semantic neighbor lookup skipped: {e}")

    try:
        import vault_db
        all_entities = vault_db.get_all_entities()
        full_text_sample = " ".join([ch.content[:500] for ch in chapters[:5]])
        for ent in all_entities:
            ent_title = ent.get("title", "")
            if ent_title and len(ent_title) > 3 and ent_title.lower() in full_text_sample.lower():
                referenced_entities.append(ent)
    except Exception as e:
        print(f"  [WARN] Entity resolution skipped: {e}")

    # Step 11: Write Rich Sidecar Index Card (<Title>_index.md)
    index_filename = f"{clean_title}_index.md"
    index_path = os.path.join(book_dir, index_filename)
    if create_sidecar:
        domain_tag = f"Tech/{domain}" if domain in {"AI", "Engineering", "Architecture", "Python"} else f"Topic/{domain}"
        aliases_list = [clean_title, f"{clean_title}_index", f"{clean_title}_Index"]
        if subtitle and subtitle not in aliases_list:
            aliases_list.append(subtitle)
        if full_display_title and full_display_title not in aliases_list:
            aliases_list.append(full_display_title)

        sidecar_content = generate_sidecar_card(
            title=clean_title,
            subtitle=subtitle,
            author=author,
            attachment_rel_path=rel_attachment_path,
            chapters=chapters,
            gists=gists,
            overview_gist=overview_gist,
            tags=[domain_tag],
            aliases=aliases_list,
            semantic_neighbors=semantic_neighbors,
            referenced_entities=referenced_entities,
        )
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(sidecar_content)
        print(f"  Wrote Sidecar Index Card: {index_filename}")
    else:
        index_content = generate_index_markdown(clean_title, author, chapters, gists)
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(index_content)
        print(f"  Wrote: {index_filename}")


    doc.close()


    # If move_source was requested, remove the original unorganized file now that doc is closed
    if move_source and os.path.abspath(filepath) != os.path.abspath(dest_pdf_path):
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                print(f"  Cleaned up source from: {filepath}")
        except Exception as e:
            print(f"  [WARN] Could not remove original source file {filepath}: {e}")

    stats = {
        "title": clean_title,
        "subtitle": subtitle,
        "author": author,
        "chapters": len(chapters),
        "total_chars": sum(len(ch.content) for ch in chapters),
        "output_dir": book_dir,
    }
    print(f"\n  [OK] Done: {len(chapters)} chapters, {stats['total_chars']:,} characters")
    return stats




# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_pdfs(path: str) -> list[str]:
    """Find all PDF files in the given path (file or directory)."""
    if os.path.isfile(path) and path.lower().endswith(".pdf"):
        return [path]
    elif os.path.isdir(path):
        pdfs = []
        for f in sorted(os.listdir(path)):
            if f.lower().endswith(".pdf"):
                pdfs.append(os.path.join(path, f))
        return pdfs
    else:
        print(f"Error: '{path}' is not a PDF file or directory.")
        return []


def main():
    parser = argparse.ArgumentParser(
        description="Extract PDFs into structured Obsidian-compatible markdown.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python extract_pdf_library.py                             # All PDFs in C:\\Temp
  python extract_pdf_library.py "C:\\path\\to\\file.pdf"      # Single file
  python extract_pdf_library.py "C:\\path\\to\\folder"        # All PDFs in folder
  python extract_pdf_library.py --output "G:\\custom"        # Custom output dir
  python extract_pdf_library.py --skip-gists                # Skip Ollama gists
  python extract_pdf_library.py --dry-run                   # Preview only
        """,
    )
    parser.add_argument(
        "input", nargs="?", default=DEFAULT_INPUT_DIR,
        help=f"PDF file or directory (default: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--output", "-o", default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--domain", "-d", default="AI",
        help="Domain tag / folder classification (e.g. AI, Engineering, Health, DND) (default: AI)",
    )
    parser.add_argument(
        "--attachments-dir", default=DEFAULT_ATTACHMENTS_DIR,
        help=f"Attachments base directory (default: {DEFAULT_ATTACHMENTS_DIR})",
    )
    parser.add_argument(
        "--move-source", action="store_true",
        help="Copy/relocate source PDF to Attachments/Source Material/<domain>/",
    )
    parser.add_argument(
        "--no-sidecar", action="store_true",
        help="Skip generating rich Sidecar Index Card note",
    )
    parser.add_argument(
        "--skip-gists", action="store_true",
        help="Skip Ollama summarization (faster, no gists in _Index.md)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview structure without writing files",
    )
    parser.add_argument(
        "--model", default=OLLAMA_MODEL,
        help=f"Ollama model for gist generation (default: {OLLAMA_MODEL})",
    )

    args = parser.parse_args()

    # Override model if specified via CLI
    import extract_pdf_library
    extract_pdf_library.OLLAMA_MODEL = args.model

    # Find PDFs
    pdfs = find_pdfs(args.input)
    if not pdfs:
        print("No PDF files found.")
        sys.exit(1)

    print(f"Found {len(pdfs)} PDF(s) to process")
    print(f"Output directory: {args.output}")
    print(f"Domain classification: {args.domain}")
    if args.move_source:
        print(f"Relocating sources to: {args.attachments_dir}/{args.domain}/")
    if args.skip_gists:
        print("Gist generation: SKIPPED")
    if args.dry_run:
        print("Mode: DRY RUN (no files written)")
    print()

    # Process each PDF
    all_stats = []
    for pdf_path in pdfs:
        try:
            stats = extract_pdf(
                pdf_path,
                args.output,
                skip_gists=args.skip_gists,
                dry_run=args.dry_run,
                domain=args.domain,
                attachments_dir=args.attachments_dir,
                move_source=args.move_source,
                create_sidecar=not args.no_sidecar,
            )
            all_stats.append(stats)

        except Exception as e:
            print(f"\n  ERROR processing {os.path.basename(pdf_path)}: {e}")
            import traceback
            traceback.print_exc()
            all_stats.append({"title": os.path.basename(pdf_path), "error": str(e)})

    # Summary
    print(f"\n{'='*60}")
    print("EXTRACTION SUMMARY")
    print(f"{'='*60}")
    for stats in all_stats:
        if "error" in stats:
            print(f"  [FAIL] {stats['title']}: {stats['error']}")
        elif stats.get("dry_run"):
            print(f"  ~ {stats['title']}: {stats['chapters']} chapters (dry run)")
        else:
            print(f"  [OK] {stats['title']}: {stats['chapters']} chapters, "
                  f"{stats.get('total_chars', 0):,} chars")
    print()


if __name__ == "__main__":
    main()
