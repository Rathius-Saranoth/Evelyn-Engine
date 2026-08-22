# visual_indexer.py
# date created: 2026-08-21 19:44:00
# tags: #vision, #indexer, #chroma, #multimodal, #taxonomy, #rag

"""visual_indexer.py — Asynchronous Visual Memory Extraction and Vector Indexer.

Processes media assets in the background to generate:
  1. Semantic visual descriptions (captions).
  2. OCR text extraction for screenshots, code, and text-heavy visuals.
  3. Master Tag Taxonomy alignment via ChromaDB (evelyn_tag_taxonomy).
  4. ChromaDB vector indexing (evelyn_media) for future conversation RAG recall.

Includes an asyncio.Queue background worker that yields during interactive chat
streaming to prevent GPU VRAM collisions.
"""

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any, Callable

import httpx

import evelyn_config as cfg
from Evelyn.tools import chroma_rag, media_db

logger = logging.getLogger(__name__)

# Asynchronous queue for background indexing tasks
vision_indexing_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()


def _encode_file_to_base64(file_path: Path) -> str | None:
    """Read a binary file from disk and return its base64-encoded string."""
    try:
        with open(file_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.error("Failed encoding file %s to base64: %s", file_path, e)
        return None


async def extract_visual_metadata_from_ollama(
    base64_image: str,
    mime_type: str = "image/png",
    user_context: str | None = None,
) -> dict[str, Any]:
    """Call Ollama's vision model to extract OCR, descriptions, domain, and hashtags.

    Args:
        base64_image: Base64-encoded image data.
        mime_type: Image MIME type.
        user_context: Optional conversational note or message provided with the image.

    Returns:
        dict: Parsed metadata containing 'caption', 'ocr_text', 'suggested_tags', 'domain'.
    """
    prompt = (
        "Analyze this image thoroughly for visual memory indexing.\n"
    )
    if user_context and user_context.strip():
        prompt += (
            f"Context note from the user sharing this image:\n\"{user_context.strip()}\"\n"
            "Incorporate any relevant named entities (e.g. pet names, person names, projects, or places) "
            "into the caption and suggested tags.\n"
        )
    prompt += (
        "Return ONLY a valid JSON object matching this schema with no markdown code blocks:\n"
        "{\n"
        '  "caption": "Concise 1-3 sentence summary of the subject, visual layout, and key entities",\n'
        '  "ocr_text": "All prominent readable text or code snippets visible in the image (or empty string if none)",\n'
        '  "suggested_tags": ["#tag1", "#tag2", "#domain/topic"],\n'
        '  "domain": "Domain/Category (e.g. Tech/Software, Creative/Art, Personal/Photo, System/Diagram)"\n'
        "}"
    )

    payload = {
        "model": cfg.MODEL_NAME,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [base64_image],
            }
        ],
        "stream": False,
        "think": False,
        "options": {
            "num_predict": 1024,
            "temperature": 0.2,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{cfg.OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw_content = data.get("message", {}).get("content", "").strip()

            # Clean potential code fence artifacts
            if raw_content.startswith("```"):
                lines = raw_content.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw_content = "\n".join(lines).strip()

            parsed = json.loads(raw_content)
            return {
                "caption": parsed.get("caption", "").strip(),
                "ocr_text": parsed.get("ocr_text", "").strip(),
                "suggested_tags": parsed.get("suggested_tags", []),
                "domain": parsed.get("domain", "").strip() or "General/Media",
            }
    except Exception as exc:
        logger.warning("Visual metadata extraction fallback due to: %s", exc)
        return {
            "caption": "Image attachment",
            "ocr_text": "",
            "suggested_tags": ["#image", "#attachment"],
            "domain": "General/Media",
        }


async def process_media_asset_indexing(
    guid: str,
    base64_image: str | None = None,
    user_context: str | None = None,
) -> bool:
    """Run visual analysis, database updates, and ChromaDB vector indexing for an asset.

    Args:
        guid: The media asset GUID (e.g. med_img_...).
        base64_image: Optional in-memory base64 image data to avoid disk re-read.
        user_context: Optional user message text providing context about the image.

    Returns:
        bool: True on successful indexing.
    """
    asset = media_db.get_media_asset(guid)
    if not asset:
        logger.error("Media asset %s not found for visual indexing", guid)
        return False

    # If already indexed, skip
    if asset.get("description") and asset.get("description") != "Image attachment":
        logger.debug("Media asset %s already indexed", guid)
        return True

    # Get base64 representation
    if not base64_image:
        abs_path = Path(cfg.BASE_DIR) / "data" / asset["file_path"]
        base64_image = _encode_file_to_base64(abs_path)

    if not base64_image:
        logger.error("Could not obtain base64 data for asset %s", guid)
        return False

    meta = await extract_visual_metadata_from_ollama(
        base64_image=base64_image,
        mime_type=asset.get("mime_type", "image/png"),
        user_context=user_context,
    )

    caption = meta.get("caption") or "Image asset"
    ocr_text = meta.get("ocr_text") or ""
    tags = meta.get("suggested_tags") or []
    domain = meta.get("domain") or "General/Media"

    # Update SQLite database
    media_db.update_media_metadata(
        guid=guid,
        description=caption,
        extracted_text=ocr_text,
        tags=tags,
        taxonomy_domain=domain,
    )

    # Format vector document for ChromaDB (bounded OCR to prevent vector dilution)
    ocr_snippet = (ocr_text[:200] + "...") if len(ocr_text) > 200 else ocr_text
    tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)

    doc_text = (
        f"[Image Asset: {guid}]\n"
        f"Domain: {domain}\n"
        f"Tags: {tags_str}\n"
        f"Description: {caption}"
    )
    if ocr_snippet:
        doc_text += f"\nVisible Text: {ocr_snippet}"

    meta_json = asset.get("metadata_json") or {}
    if isinstance(meta_json, str):
        try:
            meta_json = json.loads(meta_json)
        except Exception:
            meta_json = {}

    exif_details = []
    if meta_json.get("datetimeoriginal") or meta_json.get("datetime"):
        dt = meta_json.get("datetimeoriginal") or meta_json.get("datetime")
        exif_details.append(f"Taken: {dt}")
    if meta_json.get("camera_make") or meta_json.get("camera_model"):
        cam = f"{meta_json.get('camera_make', '')} {meta_json.get('camera_model', '')}".strip()
        exif_details.append(f"Camera: {cam}")
    if meta_json.get("gps") and isinstance(meta_json["gps"], dict):
        gps = meta_json["gps"]
        lat = gps.get("latitude")
        lon = gps.get("longitude")
        if lat is not None and lon is not None and (lat != 0 or lon != 0):
            exif_details.append(f"GPS: ({lat}, {lon})")

    if exif_details:
        doc_text += f"\nEXIF: {', '.join(exif_details)}"

    extra_meta = {
        "guid": guid,
        "media_type": asset.get("media_type", "image"),
        "file_path": asset.get("file_path", ""),
        "domain": domain,
        "created_ts": asset.get("created_ts", 0),
    }
    if meta_json.get("gps") and isinstance(meta_json["gps"], dict):
        gps = meta_json["gps"]
        if "latitude" in gps and "longitude" in gps:
            lat_f = float(gps["latitude"])
            lon_f = float(gps["longitude"])
            if lat_f != 0 or lon_f != 0:
                extra_meta["latitude"] = lat_f
                extra_meta["longitude"] = lon_f

    chroma_rag.enqueue_upsert(
        source_path=f"media::{guid}",
        content=doc_text,
        extra_metadata=extra_meta,
        collection_name=cfg.CHROMA_MEDIA_COLLECTION,
    )

    logger.info("Successfully indexed visual asset %s into media_db and ChromaDB", guid)
    return True


async def visual_indexing_worker_loop(is_busy_predicate: Callable[[], bool] | None = None) -> None:
    """Continuous background worker loop that drains vision_indexing_queue during idle time."""
    logger.info("Visual indexing background worker started")
    while True:
        job = await vision_indexing_queue.get()
        guid = job.get("guid")
        base64_data = job.get("base64")
        user_context = job.get("user_context")

        try:
            # Yield if an interactive stream is currently occupying the GPU
            while is_busy_predicate and is_busy_predicate():
                await asyncio.sleep(1.0)

            if guid:
                await process_media_asset_indexing(
                    guid=guid,
                    base64_image=base64_data,
                    user_context=user_context,
                )
        except Exception as exc:
            logger.error("Error in visual indexing worker for %s: %s", guid, exc)
        finally:
            vision_indexing_queue.task_done()
