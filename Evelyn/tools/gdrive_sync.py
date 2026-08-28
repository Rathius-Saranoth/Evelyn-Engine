# gdrive_sync.py
# date created: 2026-08-16
# tags: #gdrive, #google-drive, #google-workspace, #health-connect, #sync

"""gdrive_sync.py — Google Drive & Workspace Synchronizer for Evelyn Engine.

Provides authenticated access to Google Drive (with support for Docs, Sheets, and Tasks),
and handles automated background synchronization and extraction of Google Health Connect
daily database exports.
"""

import json
import os
import sys
import zipfile
from datetime import UTC, datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

import evelyn_config as cfg


def get_google_credentials(token_path: str | None = None) -> Credentials | None:
    """Retrieve and refresh valid Google OAuth credentials.

    Args:
        token_path: Path to token JSON. Defaults to cfg.GDRIVE_TOKEN_PATH.

    Returns:
        Credentials object if valid or refreshed, None otherwise.
    """
    path = token_path or cfg.GDRIVE_TOKEN_PATH
    if not os.path.exists(path):
        return None

    try:
        creds = Credentials.from_authorized_user_file(path)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed credentials
            with open(path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())
        return creds if creds and creds.valid else None
    except (GoogleAuthError, OSError, ValueError, KeyError) as e:
        print(f"[GDrive Sync] Error loading/refreshing credentials: {e}", flush=True)
        return None


def get_drive_service():
    """Build and return an authorized Google Drive API service resource."""
    creds = get_google_credentials()
    if not creds:
        return None
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def get_tasks_service():
    """Build and return an authorized Google Tasks API service resource."""
    creds = get_google_credentials()
    if not creds:
        return None
    return build("tasks", "v1", credentials=creds, cache_discovery=False)


def get_docs_service():
    """Build and return an authorized Google Docs API service resource."""
    creds = get_google_credentials()
    if not creds:
        return None
    return build("docs", "v1", credentials=creds, cache_discovery=False)


def get_sheets_service():
    """Build and return an authorized Google Sheets API service resource."""
    creds = get_google_credentials()
    if not creds:
        return None
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def search_drive_files(query: str, page_size: int = 10, fields: str | None = None) -> list:
    """Search Google Drive for files matching the given query string.

    Args:
        query: Drive search query (e.g. "name contains 'Health Connect' and trashed = false").
        page_size: Number of files to return.
        fields: File fields to retrieve.

    Returns:
        List of file metadata dicts.
    """
    service = get_drive_service()
    if not service:
        return []

    req_fields = fields or "files(id, name, mimeType, modifiedTime, size, md5Checksum)"
    try:
        response = service.files().list(
            q=query,
            pageSize=page_size,
            fields=req_fields,
            orderBy="modifiedTime desc"
        ).execute()
        return response.get("files", [])
    except (HttpError, GoogleAuthError, OSError, ValueError) as e:
        print(f"[GDrive Sync] Error searching Drive files: {e}", flush=True)
        return []


def download_drive_file(file_id: str, destination_path: str) -> bool:
    """Download a file from Google Drive to the local file system.

    Args:
        file_id: Google Drive file ID.
        destination_path: Local destination file path.

    Returns:
        True if successful, False otherwise.
    """
    service = get_drive_service()
    if not service:
        return False

    try:
        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
        request = service.files().get_media(fileId=file_id)
        with open(destination_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request, chunksize=1024 * 1024 * 4)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return True
    except (HttpError, GoogleAuthError, OSError, ValueError) as e:
        print(f"[GDrive Sync] Error downloading file {file_id}: {e}", flush=True)
        return False


def _load_sync_state() -> dict:
    """Load sync state JSON metadata."""
    if os.path.exists(cfg.HEALTH_SYNC_STATE_PATH):
        try:
            with open(cfg.HEALTH_SYNC_STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            print(f"[GDrive Sync] Warning: could not parse sync state file: {e}", flush=True)
    return {}


def _save_sync_state(state: dict):
    """Save sync state JSON metadata."""
    os.makedirs(os.path.dirname(cfg.HEALTH_SYNC_STATE_PATH), exist_ok=True)
    with open(cfg.HEALTH_SYNC_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def sync_health_connect_from_drive(force: bool = False) -> dict:
    """Find the latest 'Health Connect.zip' on Google Drive, download it, and extract the DB.

    Args:
        force: If True, forces download even if timestamps and MD5 match.

    Returns:
        Dict detailing the sync outcome: status, action, file metadata, error if any.
    """
    service = get_drive_service()
    if not service:
        return {
            "status": "error",
            "message": "Google Drive token not found or invalid. Run scripts/setup_gdrive.py."
        }

    # Find Health Connect zip on Drive
    files = search_drive_files("name = 'Health Connect.zip' and trashed = false", page_size=5)
    if not files:
        # Fallback to broader search if exact name differs
        files = search_drive_files("name contains 'Health Connect' and mimeType = 'application/zip' and trashed = false", page_size=5)

    if not files:
        return {
            "status": "error",
            "message": "No 'Health Connect.zip' found in Google Drive."
        }

    latest_file = files[0]
    file_id = latest_file["id"]
    file_name = latest_file["name"]
    modified_time = latest_file.get("modifiedTime")
    md5_checksum = latest_file.get("md5Checksum")

    state = _load_sync_state()
    last_file_id = state.get("drive_file_id")
    last_modified = state.get("drive_modified_time")
    last_md5 = state.get("drive_md5")

    # Check if local DB exists
    db_exists = os.path.exists(cfg.HEALTH_DB_PATH)

    if not force and db_exists and last_file_id == file_id and (last_modified == modified_time or last_md5 == md5_checksum):
        return {
            "status": "success",
            "action": "up_to_date",
            "message": f"Local Health Connect DB is already up-to-date (Drive modified: {modified_time}).",
            "file_id": file_id,
            "modified_time": modified_time,
            "db_path": cfg.HEALTH_DB_PATH
        }

    # Download to target directory
    os.makedirs(cfg.HEALTH_DATA_DIR, exist_ok=True)
    temp_zip_path = os.path.join(cfg.HEALTH_DATA_DIR, "Health Connect.zip")

    print(f"[GDrive Sync] Downloading '{file_name}' (ID: {file_id}, modified: {modified_time})...", flush=True)
    success = download_drive_file(file_id, temp_zip_path)
    if not success:
        return {
            "status": "error",
            "message": f"Failed to download '{file_name}' from Google Drive."
        }

    # Extract health_connect_export.db
    extracted_db = None
    try:
        with zipfile.ZipFile(temp_zip_path, "r") as z:
            for item in z.namelist():
                if item.endswith(".db"):
                    # Extract directly to cfg.HEALTH_DB_PATH
                    with z.open(item) as src, open(cfg.HEALTH_DB_PATH, "wb") as dst:
                        dst.write(src.read())
                    extracted_db = item
                    break

        if not extracted_db or not os.path.exists(cfg.HEALTH_DB_PATH):
            return {
                "status": "error",
                "message": "Zip file downloaded, but no .db file was found inside."
            }

        # Update sync state
        state["last_synced_at"] = datetime.now(UTC).isoformat()
        state["drive_file_id"] = file_id
        state["drive_file_name"] = file_name
        state["drive_modified_time"] = modified_time
        state["drive_md5"] = md5_checksum
        state["extracted_db_name"] = extracted_db
        state["db_size_bytes"] = os.path.getsize(cfg.HEALTH_DB_PATH)
        _save_sync_state(state)

        print(f"[GDrive Sync] Successfully updated Health Connect DB ({state['db_size_bytes']:,} bytes).", flush=True)
        return {
            "status": "success",
            "action": "downloaded",
            "message": "Successfully downloaded and updated Health Connect DB from Google Drive.",
            "file_id": file_id,
            "modified_time": modified_time,
            "db_path": cfg.HEALTH_DB_PATH,
            "db_size_bytes": state["db_size_bytes"]
        }

    except (zipfile.BadZipFile, OSError, ValueError, KeyError) as e:
        print(f"[GDrive Sync] Error extracting DB from zip: {e}", flush=True)
        return {
            "status": "error",
            "message": f"Error extracting database from zip: {e}"
        }
