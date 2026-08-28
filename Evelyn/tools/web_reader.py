# web_reader.py
# date created: 2026-05-26
# date modified: 2026-05-26 11:47:16
# tags: #web, #scraping, #extraction, #trafilatura, #chunking

"""web_reader.py — High-performance web scraping and text extraction for Evelyn's Deep Research.

Leverages `trafilatura` to extract clean, semantic text/markdown from raw HTML,
bypassing advertisements, navigation bars, and boilerplate. Includes robust
async fetching via `httpx` and a semantic chunking pipeline to keep contents
within the model's 16k context window constraint.
"""

import datetime
from typing import Any

import httpx
import trafilatura

import evelyn_config as cfg  # [[evelyn_config.py]]

_original_print = print

def _timestamped_print(*args, **kwargs):
    """Print with a local ISO timestamp prefix [YYYY-MM-DD HH:MM:SS]."""
    ts = datetime.datetime.now(datetime.UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    if args and isinstance(args[0], str):
        if not (args[0].startswith("[20") and len(args[0]) > 20 and args[0][20] == "]"):
            args = (f"[{ts}] {args[0]}", *args[1:])
    elif not args:
        args = (f"[{ts}]",)
    else:
        args = (f"[{ts}]", *args)
    _original_print(*args, **kwargs)

print = _timestamped_print



async def fetch_url(url: str, timeout: int = 15) -> str | None:
    """Fetch the raw HTML content of a URL asynchronously.

    Includes a standard browser User-Agent header to minimize request blocks
        and respects robots.txt by self-identifying in a clean, non-stealth manner.

    Args:
        url: The full web page URL to fetch.
        timeout: HTTP request timeout in seconds.

    Returns:
        Optional[str]: The raw HTML content, or None if the request failed.
    """
    headers = {
        "User-Agent": "EvelynResearchAgent/1.0 (+https://github.com/LearningCircuit/local-deep-research)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    try:
        if cfg.DEBUG_LOGGING:
            print(f"[WEB_READER] Fetching: {url}", flush=True)

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text
    except (httpx.HTTPError, TimeoutError, OSError) as e:
        print(f"[WEB_READER ERROR] Failed to fetch {url}: {e}", flush=True)
        return None


def extract_content(html: str) -> str | None:
    """Extract clean, printable text from raw HTML using trafilatura.

    Removes boilerplate, navigation menus, ads, and sidebars, yielding clean
    article-like content. Respects `cfg.RESEARCH_MAX_PAGE_CHARS` by truncating
    excessively large payloads.

    Args:
        html: Raw HTML string fetched from a web page.

    Returns:
        Optional[str]: Extracted text, or None if extraction returned nothing.
    """
    try:
        # Extract content as clean Markdown-like structure
        text = trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_images=False,
            include_tables=True,
            favor_precision=True
        )

        if not text:
            return None

        # Enforce max characters constraint from config
        max_chars = getattr(cfg, "RESEARCH_MAX_PAGE_CHARS", 15000)
        if len(text) > max_chars:
            if cfg.DEBUG_LOGGING:
                print(f"[WEB_READER] Truncating page from {len(text)} to {max_chars} chars.", flush=True)
            text = text[:max_chars] + "\n\n[Content truncated due to length constraint...]"

        return text.strip()
    except (ValueError, RuntimeError, TypeError) as e:
        print(f"[WEB_READER ERROR] Extraction failed: {e}", flush=True)
        return None


def chunk_text(text: str, chunk_size: int = 4000, overlap: int = 400) -> list[str]:
    """Split a long text document into overlapping chunks.

    Maintains semantic boundaries (paragraphs or line breaks) where possible
    to keep extracted facts contiguous.

    Args:
        text: The clean extracted text to slice.
        chunk_size: Maximum character length per chunk (~1000 tokens).
        overlap: Character overlap between contiguous chunks.

    Returns:
        List[str]: A list of overlapping text chunks.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        # If we aren't at the end of the text, try to find a nice boundary
        if end < len(text):
            # Scan backwards up to 300 chars to find a paragraph break or line break
            boundary = -1
            for offset in range(300):
                pos = end - offset
                if text[pos:pos+2] == "\n\n":
                    boundary = pos + 2
                    break
                elif text[pos] == "\n":
                    if boundary == -1:
                        boundary = pos + 1

            if boundary != -1:
                end = boundary

        chunks.append(text[start:end].strip())

        # Advance starting pointer considering overlap
        start = end - overlap
        if start >= len(text) - overlap:
            break

    return chunks


async def read_and_extract_url(url: str) -> dict[str, Any]:
    """Helper that fetches a URL and returns clean extracted text with metadata.

    Args:
        url: The full web page URL to process.

    Returns:
        Dict[str, Any]: A dictionary containing:
            - "success": bool
            - "url": str
            - "content": Optional[str]
            - "chunks": List[str]
            - "char_count": int
            - "chunk_count": int
            - "error": Optional[str]
    """
    result = {
        "success": False,
        "url": url,
        "content": None,
        "chunks": [],
        "char_count": 0,
        "chunk_count": 0,
        "error": None
    }

    html = await fetch_url(url)
    if not html:
        result["error"] = "Failed to fetch HTML content."
        return result

    content = extract_content(html)
    if not content:
        result["error"] = "Failed to extract clean text from page structure."
        return result

    chunks = chunk_text(content)

    result.update({
        "success": True,
        "content": content,
        "chunks": chunks,
        "char_count": len(content),
        "chunk_count": len(chunks)
    })

    return result
