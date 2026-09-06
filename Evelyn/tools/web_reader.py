# web_reader.py
# date created: 2026-05-26
# date modified: 2026-09-06 15:51:26
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



DESKTOP_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-CH-UA": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

WAF_CHALLENGE_SIGNATURES = [
    "<title>just a moment...</title>",
    "<title>attention required! | cloudflare</title>",
    "<title>cloudflare</title>",
    "cf-browser-verification",
    "challenge-running",
    "ray id:",
]


def is_waf_challenge(status_code: int | None, html: str | None) -> bool:
    """Detect if HTTP status or HTML response indicates a Cloudflare or edge WAF block."""
    if status_code in (403, 429):
        return True
    if html:
        lower_html = html[:2500].lower()
        return any(sig in lower_html for sig in WAF_CHALLENGE_SIGNATURES)
    return False


def format_waf_recovery_message(url: str, status_code: int | None = None) -> str:
    """Format a structured recovery block for the model when an edge challenge is encountered."""
    status_str = f"HTTP {status_code} / Cloudflare Turnstile" if status_code else "HTTP 403 / Cloudflare Turnstile"
    return (
        f"[Web Access Blocked]\n"
        f"Unable to read {url}: Access was restricted by edge bot protection ({status_str}).\n"
        f"Instruction: Do NOT retry read_url on this link. Use web_search with relevant title or "
        f"topic keywords to find public discussions, documentation, or mirrors instead."
    )


async def fetch_url(url: str, timeout: int = 15) -> str | None:
    """Fetch the raw HTML content of a URL asynchronously.

    Args:
        url: The full web page URL to fetch.
        timeout: HTTP request timeout in seconds.

    Returns:
        Optional[str]: The raw HTML content, or None if the request failed.
    """
    try:
        if cfg.DEBUG_LOGGING:
            print(f"[WEB_READER] Fetching: {url}", flush=True)

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=DESKTOP_BROWSER_HEADERS)
            resp.raise_for_status()
            return resp.text
    except (httpx.HTTPError, TimeoutError, OSError) as e:
        print(f"[WEB_READER ERROR] Failed to fetch {url}: {e}", flush=True)
        return None


def fetch_url_sync(url: str, timeout: int = 15) -> tuple[str | None, int | None, str | None]:
    """Fetch the raw HTML content of a URL synchronously with desktop client headers.

    Args:
        url: The full web page URL to fetch.
        timeout: HTTP request timeout in seconds.

    Returns:
        tuple[Optional[str], Optional[int], Optional[str]]: (html_text, status_code, error_message)
    """
    try:
        if cfg.DEBUG_LOGGING:
            print(f"[WEB_READER] Synchronously fetching: {url}", flush=True)

        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers=DESKTOP_BROWSER_HEADERS)
            status_code = resp.status_code
            if status_code in (403, 429) or is_waf_challenge(status_code, resp.text):
                return resp.text, status_code, "Blocked by edge bot protection"
            resp.raise_for_status()
            return resp.text, status_code, None
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code if e.response else None
        body = e.response.text if e.response else None
        return body, status_code, str(e)
    except (httpx.HTTPError, TimeoutError, OSError) as e:
        print(f"[WEB_READER ERROR] Failed to fetch {url}: {e}", flush=True)
        return None, None, str(e)


def extract_content(html: str, max_chars: int | None = None) -> str | None:
    """Extract clean, printable text from raw HTML using trafilatura.

    Removes boilerplate, navigation menus, ads, and sidebars, yielding clean
    article-like content. Respects `max_chars` by truncating excessively large payloads.

    Args:
        html: Raw HTML string fetched from a web page.
        max_chars: Optional maximum character count. Defaults to cfg.RESEARCH_MAX_PAGE_CHARS or 15000.

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
            favor_precision=True,
        )

        if not text:
            return None

        # Enforce max characters constraint
        ceiling = max_chars if max_chars is not None else getattr(cfg, "RESEARCH_MAX_PAGE_CHARS", 15000)
        if len(text) > ceiling:
            if cfg.DEBUG_LOGGING:
                print(f"[WEB_READER] Truncating page from {len(text)} to {ceiling} chars.", flush=True)
            text = text[:ceiling] + "\n\n[Content truncated due to length constraint...]"

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
    result: dict[str, Any] = {
        "success": False,
        "url": url,
        "content": None,
        "chunks": [],
        "char_count": 0,
        "chunk_count": 0,
        "error": None,
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
        "chunk_count": len(chunks),
    })

    return result


def read_and_extract_url_sync(url: str, max_chars: int = 15000) -> dict[str, Any]:
    """Synchronous URL reader for Evelyn chat tools.

    Args:
        url: Full HTTP or HTTPS web page URL.
        max_chars: Maximum characters of markdown content to return. Defaults to 15,000.

    Returns:
        dict[str, Any]: Dictionary with success, content, error, status_code, is_blocked flags.
    """
    result: dict[str, Any] = {
        "success": False,
        "url": url,
        "content": None,
        "status_code": None,
        "is_blocked": False,
        "char_count": 0,
        "error": None,
    }

    html, status_code, err = fetch_url_sync(url)
    result["status_code"] = status_code

    if is_waf_challenge(status_code, html):
        result["is_blocked"] = True
        result["error"] = format_waf_recovery_message(url, status_code)
        return result

    if not html:
        result["error"] = f"Failed to fetch content from {url}: {err or 'Connection failed'}"
        return result

    content = extract_content(html, max_chars=max_chars)
    if not content:
        result["error"] = f"Failed to extract readable article or text content from {url}."
        return result

    result.update({
        "success": True,
        "content": content,
        "char_count": len(content),
    })
    return result

