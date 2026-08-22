# media_db.py
# date created: 2026-08-21 19:43:00
# tags: #database, #sqlite, #media, #vision, #attachments, #guid

"""media_db.py — SQLite access layer for Evelyn's media and attachment assets database.

Provides storage, deduplication, metadata management, and message junction mapping
for media assets (images, audio, documents) in evelyn_media.db. Keeps binary
attachments and heavy OCR/caption metadata strictly isolated from conversational
and core memory databases.

Schema:
  media_assets     — Stores unique physical media records (deduplicated by SHA-256).
  chat_media_links — Many-to-many junction table mapping media_assets to chat messages.

Usage:
  import media_db
  media_db.init_media_db()
  asset = media_db.store_or_get_media_asset(image_bytes, mime_type="image/png", source_msg_id=123)
  links = media_db.get_media_for_message(123)
"""

import hashlib
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any
import uuid

import evelyn_config as cfg

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------


def get_db() -> sqlite3.Connection:
    """Open a connection to evelyn_media.db with row_factory enabled.

    Returns:
        sqlite3.Connection: A database connection configured for dict-like row access.
    """
    os.makedirs(os.path.dirname(cfg.MEDIA_DB_PATH), exist_ok=True)
    con = sqlite3.connect(cfg.MEDIA_DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


# ---------------------------------------------------------------------------
# Schema Initialization
# ---------------------------------------------------------------------------


def init_media_db() -> None:
    """Initialize the evelyn_media.db schema idempotently."""
    con = get_db()
    with con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS media_assets (
                id              TEXT PRIMARY KEY,
                media_type      TEXT NOT NULL,
                file_path       TEXT NOT NULL,
                file_hash       TEXT NOT NULL UNIQUE,
                mime_type       TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                width           INTEGER,
                height          INTEGER,
                description     TEXT,
                extracted_text  TEXT,
                tags            TEXT,
                taxonomy_domain TEXT,
                metadata_json   TEXT,
                created_ts      REAL NOT NULL
            );
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_media_links (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id    TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE,
                message_id  INTEGER NOT NULL,
                created_ts  REAL NOT NULL
            );
            """
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_hash ON media_assets(file_hash);"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_media_type ON media_assets(media_type);"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_links_msg ON chat_media_links(message_id);"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_links_media ON chat_media_links(media_id);"
        )
    con.close()


# ---------------------------------------------------------------------------
# GUID and Hash Utilities
# ---------------------------------------------------------------------------


def generate_guid(media_type: str = "image") -> str:
    """Generate a prefix-typed, collision-resistant GUID (e.g. med_img_...).

    Args:
        media_type: One of 'image', 'audio', 'document'.

    Returns:
        str: Typed GUID string.
    """
    prefix_map = {
        "image": "med_img",
        "audio": "med_aud",
        "document": "med_doc",
    }
    prefix = prefix_map.get(media_type, "med_asset")
    return f"{prefix}_{uuid.uuid4().hex}"


def compute_file_hash(data: bytes) -> str:
    """Compute the SHA-256 hash of raw file bytes."""
    return hashlib.sha256(data).hexdigest()


def _get_extension_for_mime(mime_type: str) -> str:
    """Map common mime types to file extensions."""
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "audio/wav": ".wav",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/ogg": ".ogg",
        "audio/m4a": ".m4a",
        "application/pdf": ".pdf",
    }
    return mapping.get(mime_type.lower(), ".bin")


# ---------------------------------------------------------------------------
# EXIF & GPS Extraction Helper
# ---------------------------------------------------------------------------


def extract_image_exif_and_gps(image_bytes: bytes) -> dict[str, Any]:
    """Extract EXIF metadata and GPS coordinates from image bytes using Pillow."""
    meta: dict[str, Any] = {}
    try:
        from PIL import Image, ExifTags

        with Image.open(io.BytesIO(image_bytes)) as img:
            exif_data = img.getexif()
            if not exif_data:
                return meta

            exif_dict: dict[str, Any] = {}
            for tag_id, value in exif_data.items():
                tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                if isinstance(value, bytes):
                    continue
                exif_dict[tag_name] = value

            if "Make" in exif_dict:
                meta["camera_make"] = str(exif_dict["Make"]).strip()
            if "Model" in exif_dict:
                meta["camera_model"] = str(exif_dict["Model"]).strip()
            if "DateTime" in exif_dict:
                meta["datetime"] = str(exif_dict["DateTime"]).strip()

            # Parse IFD / GPS info
            gps_ifd = (
                exif_data.get_ifd(ExifTags.IFD.GPSInfo)
                if hasattr(ExifTags, "IFD") and hasattr(ExifTags.IFD, "GPSInfo")
                else None
            )
            if gps_ifd:
                gps_dict: dict[str, Any] = {}
                for gps_tag_id, gps_val in gps_ifd.items():
                    gps_tag_name = ExifTags.GPSTAGS.get(gps_tag_id, str(gps_tag_id))
                    if isinstance(gps_val, bytes):
                        continue
                    gps_dict[gps_tag_name] = gps_val

                lat_raw = gps_dict.get("GPSLatitude")
                lat_ref = gps_dict.get("GPSLatitudeRef")
                lon_raw = gps_dict.get("GPSLongitude")
                lon_ref = gps_dict.get("GPSLongitudeRef")
                alt_raw = gps_dict.get("GPSAltitude")

                if lat_raw and lat_ref and lon_raw and lon_ref:
                    try:
                        lat = float(lat_raw[0]) + float(lat_raw[1]) / 60.0 + float(lat_raw[2]) / 3600.0
                        if str(lat_ref).upper() == "S":
                            lat = -lat
                        lon = float(lon_raw[0]) + float(lon_raw[1]) / 60.0 + float(lon_raw[2]) / 3600.0
                        if str(lon_ref).upper() == "W":
                            lon = -lon

                        gps_info: dict[str, Any] = {
                            "latitude": round(lat, 6),
                            "longitude": round(lon, 6),
                        }
                        if alt_raw:
                            gps_info["altitude_m"] = round(float(alt_raw), 2)
                        meta["gps"] = gps_info
                    except Exception as e:
                        logger.debug("Error converting GPS coordinates: %s", e)

            # Check for Exif SubIFD
            exif_ifd = (
                exif_data.get_ifd(ExifTags.IFD.Exif)
                if hasattr(ExifTags, "IFD") and hasattr(ExifTags.IFD, "Exif")
                else None
            )
            if exif_ifd:
                for sub_tag_id, sub_val in exif_ifd.items():
                    sub_tag_name = ExifTags.TAGS.get(sub_tag_id, str(sub_tag_id))
                    if sub_tag_name in (
                        "DateTimeOriginal",
                        "DateTimeDigitized",
                        "LensModel",
                        "ExposureTime",
                        "FNumber",
                        "ISOSpeedRatings",
                    ):
                        if not isinstance(sub_val, bytes):
                            meta[sub_tag_name.lower()] = str(sub_val).strip()

    except Exception as exc:
        logger.debug("Failed extracting EXIF/GPS via Pillow: %s", exc)
    return meta


# ---------------------------------------------------------------------------
# Asset Storage & CRUD
# ---------------------------------------------------------------------------


def store_or_get_media_asset(
    data: bytes,
    mime_type: str,
    source_msg_id: int | None = None,
    original_name: str | None = None,
    media_type: str = "image",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store a media asset with physical SHA-256 deduplication and message linking.

    If an identical file has already been ingested, reuses the existing physical record
    and creates a new junction link in chat_media_links if source_msg_id is provided.

    Args:
        data: Raw binary bytes of the media asset.
        mime_type: MIME type string (e.g. "image/png").
        source_msg_id: Optional database message ID that attached this media.
        original_name: Optional original filename from user upload.
        media_type: Type category ("image", "audio", "document").
        metadata: Optional dictionary of arbitrary metadata.

    Returns:
        dict: Asset record dictionary including "id", "file_path", "is_new" (bool).
    """
    init_media_db()
    file_hash = compute_file_hash(data)
    file_size = len(data)
    now = time.time()

    con = get_db()
    try:
        # Check for existing physical duplicate
        existing = con.execute(
            "SELECT * FROM media_assets WHERE file_hash = ?", (file_hash,)
        ).fetchone()

        if existing:
            asset = dict(existing)
            asset["is_new"] = False
            if source_msg_id is not None:
                link_exists = con.execute(
                    "SELECT 1 FROM chat_media_links WHERE media_id = ? AND message_id = ?",
                    (asset["id"], source_msg_id),
                ).fetchone()
                if not link_exists:
                    with con:
                        con.execute(
                            "INSERT INTO chat_media_links (media_id, message_id, created_ts) VALUES (?, ?, ?)",
                            (asset["id"], source_msg_id, now),
                        )
            return asset

        # New asset: Generate GUID and determine disk path
        guid = generate_guid(media_type)
        ext = _get_extension_for_mime(mime_type)
        date_folder = time.strftime("%Y/%m", time.localtime(now))
        rel_dir = Path("attachments") / f"{media_type}s" / date_folder
        abs_dir = Path(cfg.BASE_DIR) / "data" / rel_dir
        abs_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{guid}{ext}"
        abs_path = abs_dir / filename
        rel_path = str(rel_dir / filename)

        # Write binary to disk
        with open(abs_path, "wb") as f:
            f.write(data)

        # Inspect dimensions for images if PIL is available
        width, height = None, None
        if media_type == "image":
            try:
                from PIL import Image

                with Image.open(io.BytesIO(data)) as img:
                    width, height = img.size
            except Exception as e:
                logger.debug("Could not inspect image dimensions: %s", e)

        meta_dict = dict(metadata) if metadata else {}
        if original_name:
            meta_dict["original_name"] = original_name

        if media_type == "image":
            server_exif = extract_image_exif_and_gps(data)
            for k, v in server_exif.items():
                if k not in meta_dict:
                    meta_dict[k] = v

        with con:
            con.execute(
                """
                INSERT INTO media_assets (
                    id, media_type, file_path, file_hash, mime_type,
                    file_size_bytes, width, height, description,
                    extracted_text, tags, taxonomy_domain, metadata_json, created_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guid,
                    media_type,
                    rel_path,
                    file_hash,
                    mime_type,
                    file_size,
                    width,
                    height,
                    None,
                    None,
                    json.dumps([]),
                    None,
                    json.dumps(meta_dict) if meta_dict else None,
                    now,
                ),
            )
            if source_msg_id is not None:
                con.execute(
                    "INSERT INTO chat_media_links (media_id, message_id, created_ts) VALUES (?, ?, ?)",
                    (guid, source_msg_id, now),
                )

        return {
            "id": guid,
            "media_type": media_type,
            "file_path": rel_path,
            "abs_file_path": str(abs_path),
            "file_hash": file_hash,
            "mime_type": mime_type,
            "file_size_bytes": file_size,
            "width": width,
            "height": height,
            "description": None,
            "extracted_text": None,
            "tags": [],
            "taxonomy_domain": None,
            "metadata_json": meta_dict,
            "created_ts": now,
            "is_new": True,
        }
    finally:
        con.close()


def get_media_asset(guid: str) -> dict[str, Any] | None:
    """Retrieve a media asset record by GUID."""
    con = get_db()
    try:
        row = con.execute("SELECT * FROM media_assets WHERE id = ?", (guid,)).fetchone()
        if not row:
            return None
        res = dict(row)
        if res.get("tags"):
            try:
                res["tags"] = json.loads(res["tags"])
            except Exception as e:
                logger.debug("Failed parsing tags json: %s", e)
                res["tags"] = []
        if res.get("metadata_json"):
            try:
                res["metadata"] = json.loads(res["metadata_json"])
            except Exception as e:
                logger.debug("Failed parsing metadata json: %s", e)
                res["metadata"] = {}
        else:
            res["metadata"] = {}
        return res
    finally:
        con.close()


def get_media_for_message(message_id: int) -> list[dict[str, Any]]:
    """Retrieve all media assets linked to a specific chat message ID."""
    con = get_db()
    try:
        rows = con.execute(
            """
            SELECT a.* FROM media_assets a
            JOIN chat_media_links l ON a.id = l.media_id
            WHERE l.message_id = ?
            ORDER BY l.id ASC
            """,
            (message_id,),
        ).fetchall()
        assets = []
        for r in rows:
            item = dict(r)
            if item.get("tags"):
                try:
                    item["tags"] = json.loads(item["tags"])
                except Exception:
                    item["tags"] = []
            if item.get("metadata_json"):
                try:
                    item["metadata"] = json.loads(item["metadata_json"])
                except Exception:
                    item["metadata"] = {}
            else:
                item["metadata"] = {}
            assets.append(item)
        return assets
    finally:
        con.close()


def update_media_metadata(
    guid: str,
    description: str | None = None,
    extracted_text: str | None = None,
    tags: list[str] | None = None,
    taxonomy_domain: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> bool:
    """Update descriptive and taxonomic metadata for a media asset."""
    con = get_db()
    try:
        fields = []
        values = []
        if description is not None:
            fields.append("description = ?")
            values.append(description)
        if extracted_text is not None:
            fields.append("extracted_text = ?")
            values.append(extracted_text)
        if tags is not None:
            fields.append("tags = ?")
            values.append(json.dumps(tags))
        if taxonomy_domain is not None:
            fields.append("taxonomy_domain = ?")
            values.append(taxonomy_domain)
        if metadata_json is not None:
            fields.append("metadata_json = ?")
            values.append(json.dumps(metadata_json))

        if not fields:
            return False

        values.append(guid)
        sql = f"UPDATE media_assets SET {', '.join(fields)} WHERE id = ?"
        with con:
            cursor = con.execute(sql, tuple(values))
            return cursor.rowcount > 0
    finally:
        con.close()


def list_unindexed_media(media_type: str = "image", limit: int = 50) -> list[dict[str, Any]]:
    """List media assets that have not yet had their description/tags generated."""
    con = get_db()
    try:
        rows = con.execute(
            """
            SELECT * FROM media_assets
            WHERE media_type = ? AND (description IS NULL OR description = '')
            ORDER BY created_ts ASC
            LIMIT ?
            """,
            (media_type, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()
