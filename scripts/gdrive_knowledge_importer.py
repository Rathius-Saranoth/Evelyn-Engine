#!/usr/bin/env python3
# gdrive_knowledge_importer.py
# date created: 2026-08-16 20:21:04
# date modified: 2026-08-16 20:21:04
# tags:

# scripts/gdrive_knowledge_importer.py
"""
gdrive_knowledge_importer.py — Staging, Format Normalization, and Asset Extractor.

Downloads designated Google Drive knowledge folders into local staging:
  - Google Docs (.gdoc) -> Exported as HTML package, images extracted to Attachments, converted to Markdown.
  - Google Sheets (.gsheet) -> Exported as CSV and converted to Markdown tables.
  - Authoritative EHR ('Medical Record') -> Downloaded directly to data/medical_records/.
  - Text, Markdown, and PDF files -> Downloaded to data/staging/.
  - Generates data/gdrive_transfer_manifest.json and tracks all items.
"""

import hashlib
import io
import json
import os
import re
import sys
import zipfile

import markdownify
from bs4 import BeautifulSoup

# Setup path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT_DIR, "Evelyn", "tools")
for d in (ROOT_DIR, TOOLS_DIR):
    if d not in sys.path:
        sys.path.insert(0, d)

import gdrive_sync
from googleapiclient.http import MediaIoBaseDownload

import evelyn_config as cfg

# Paths
STAGING_DIR = getattr(cfg, "STAGING_DIR", os.path.join(ROOT_DIR, "data", "staging"))
ATTACHMENTS_DIR = os.path.join(STAGING_DIR, "Attachments")
MEDICAL_RECORDS_DIR = getattr(cfg, "MEDICAL_RECORDS_DIR", os.path.join(ROOT_DIR, "data", "medical_records"))
MANIFEST_FILE = os.path.join(ROOT_DIR, "data", "gdrive_transfer_manifest.json")

# Target folders and exclusions
TARGET_ROOT_NAMES = [
    "Reference Library", "Prompt Lab", "Pets", "Recipes",
    "Financial", "Professional", "Medical", "Genealogy",
    "Talonesti", "Schyler", "Evelyn", "Art Institute"
]

EXCLUDED_FOLDER_NAMES = {
    "audio books", "audiobooks", "emulators", "emulator",
    "video", "music", "pc backup", "gaming"
}

EXCLUDED_EXTENSIONS = {
    ".exe", ".zip", ".7z", ".rar", ".iso", ".tar", ".gz",
    ".m4b", ".mp3", ".flac", ".mp4", ".mkv", ".mov", ".msi"
}

def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

def html_to_markdown_with_images(html_content: str, doc_title: str, image_map: dict) -> str:
    """
    Convert Google Docs HTML to Markdown, mapping images to Obsidian Attachments wikilinks.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # Process image tags
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src in image_map:
            img_filename = image_map[src]
            # Replace img tag with placeholder text for markdownify
            img.replace_with(f" ![[{img_filename}]] ")
        elif src.startswith("images/") and src in image_map:
            img.replace_with(f" ![[{image_map[src]}]] ")

    # Remove inline style noise
    for tag in soup.find_all(["span", "style"]):
        if tag.name == "style":
            tag.decompose()

    md = markdownify.markdownify(str(soup), heading_style="ATX", bullets="-")

    # Clean up excessive blank lines
    md = re.sub(r'\n{3,}', '\n\n', md).strip()
    return md

def csv_to_markdown_table(csv_content: str, max_rows: int = 100) -> str:
    """Convert CSV string to clean Markdown table."""
    import csv
    reader = csv.reader(io.StringIO(csv_content))
    rows = list(reader)
    if not rows:
        return "(Empty Spreadsheet)"

    col_count = max(len(r) for r in rows)
    padded = [r + [""] * (col_count - len(r)) for r in rows]

    header = padded[0]
    md_lines = ["| " + " | ".join(c.replace("|", "\\|") for c in header) + " |"]
    md_lines.append("| " + " | ".join("---" for _ in range(col_count)) + " |")

    md_lines.extend("| " + " | ".join(c.replace("|", "\\|") for c in row) + " |" for row in padded[1:max_rows])

    if len(rows) > max_rows:
        md_lines.append(f"\n*(Showing first {max_rows} of {len(rows)} rows)*")

    return "\n".join(md_lines)

def load_manifest() -> dict:
    if os.path.exists(MANIFEST_FILE):
        try:
            with open(MANIFEST_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[GDRIVE IMPORTER] Could not read manifest: {e}", flush=True)
    return {"version": 1, "last_updated": "", "items": {}}

def save_manifest(manifest: dict):
    os.makedirs(os.path.dirname(MANIFEST_FILE), exist_ok=True)
    manifest["last_updated"] = gdrive_sync.datetime.now(gdrive_sync.timezone.utc).isoformat()
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

def download_file_bytes(service, file_id: str) -> bytes:
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()

def export_doc_as_html_zip(service, file_id: str) -> bytes:
    # Google Drive export to HTML zipped package
    request = service.files().export_media(fileId=file_id, mimeType="application/zip")
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()

def export_doc_as_html_text(service, file_id: str) -> str:
    request = service.files().export_media(fileId=file_id, mimeType="text/html")
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue().decode("utf-8", errors="replace")

def export_sheet_as_csv(service, file_id: str) -> str:
    request = service.files().export_media(fileId=file_id, mimeType="text/csv")
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue().decode("utf-8", errors="replace")

def process_google_doc(service, item: dict, rel_dir: str, manifest: dict) -> str:
    file_id = item["id"]
    name = item["name"]
    clean_name = sanitize_filename(name)
    out_dir = os.path.join(STAGING_DIR, rel_dir)
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(ATTACHMENTS_DIR, exist_ok=True)

    out_md_path = os.path.join(out_dir, f"{clean_name}.md")
    image_map = {}

    try:
        # Try downloading HTML zip with images
        zip_bytes = export_doc_as_html_zip(service, file_id)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            html_files = [f for f in z.namelist() if f.endswith(".html")]
            html_content = z.read(html_files[0]).decode("utf-8", errors="replace") if html_files else ""

            img_index = 1
            for member in z.namelist():
                if member.lower().startswith("images/") and not member.endswith("/"):
                    img_data = z.read(member)
                    ext = os.path.splitext(member)[1] or ".png"
                    img_filename = f"{clean_name}_img_{img_index:02d}{ext}"
                    img_path = os.path.join(ATTACHMENTS_DIR, img_filename)
                    with open(img_path, "wb") as f_img:
                        f_img.write(img_data)
                    image_map[member] = img_filename
                    img_index += 1

        md_content = html_to_markdown_with_images(html_content, clean_name, image_map)
    except (OSError, KeyError, ValueError) as err:
        print(f"[GDRIVE IMPORTER] Note formatting fallback for {clean_name}: {err}", flush=True)
        # Fallback to plain HTML export
        html_str = export_doc_as_html_text(service, file_id)
        md_content = html_to_markdown_with_images(html_str, clean_name, {})

    with open(out_md_path, "w", encoding="utf-8") as f_out:
        f_out.write(md_content)

    manifest["items"][file_id] = {
        "name": name,
        "rel_path": os.path.relpath(out_md_path, STAGING_DIR),
        "local_path": out_md_path,
        "type": "gdoc",
        "size_bytes": os.path.getsize(out_md_path),
        "sha256": compute_sha256(md_content.encode("utf-8")),
        "attachments": list(image_map.values()),
        "status": "STAGED"
    }
    return out_md_path

def process_google_sheet(service, item: dict, rel_dir: str, manifest: dict) -> str:
    file_id = item["id"]
    name = item["name"]
    clean_name = sanitize_filename(name)
    out_dir = os.path.join(STAGING_DIR, rel_dir)
    os.makedirs(out_dir, exist_ok=True)

    csv_str = export_sheet_as_csv(service, file_id)
    md_content = f"# {name}\n\n" + csv_to_markdown_table(csv_str)

    out_md_path = os.path.join(out_dir, f"{clean_name}.md")
    with open(out_md_path, "w", encoding="utf-8") as f_out:
        f_out.write(md_content)

    manifest["items"][file_id] = {
        "name": name,
        "rel_path": os.path.relpath(out_md_path, STAGING_DIR),
        "local_path": out_md_path,
        "type": "gsheet",
        "size_bytes": os.path.getsize(out_md_path),
        "sha256": compute_sha256(md_content.encode("utf-8")),
        "attachments": [],
        "status": "STAGED"
    }
    return out_md_path

def process_binary_or_text(service, item: dict, rel_dir: str, manifest: dict, is_ehr: bool = False) -> str:
    file_id = item["id"]
    name = item["name"]
    clean_name = sanitize_filename(name)

    base_dir = MEDICAL_RECORDS_DIR if is_ehr else STAGING_DIR
    out_dir = os.path.join(base_dir, rel_dir)
    os.makedirs(out_dir, exist_ok=True)

    out_path = os.path.join(out_dir, clean_name)
    data = download_file_bytes(service, file_id)

    with open(out_path, "wb") as f_out:
        f_out.write(data)

    status = "EHR_RAW" if is_ehr else "STAGED"
    manifest["items"][file_id] = {
        "name": name,
        "rel_path": os.path.relpath(out_path, base_dir),
        "local_path": out_path,
        "type": "ehr_raw" if is_ehr else ("pdf" if name.lower().endswith(".pdf") else "file"),
        "size_bytes": len(data),
        "sha256": compute_sha256(data),
        "attachments": [],
        "status": status
    }
    return out_path

def stage_all_knowledge_folders(dry_run: bool = False):
    service = gdrive_sync.get_drive_service()
    if not service:
        print("[gdrive_importer] Failed to authenticate with Google Drive API.", flush=True)
        return False

    os.makedirs(STAGING_DIR, exist_ok=True)
    os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
    os.makedirs(MEDICAL_RECORDS_DIR, exist_ok=True)

    manifest = load_manifest()
    print("=" * 70, flush=True)
    print("GOOGLE DRIVE KNOWLEDGE STAGING & CONVERSION PIPELINE", flush=True)
    print("=" * 70, flush=True)

    # 1. Map target root folders
    root_res = service.files().list(
        q="'root' in parents and trashed = false",
        pageSize=100,
        fields="files(id, name, mimeType)"
    ).execute()

    root_folders = {f["name"]: f["id"] for f in root_res.get("files", []) if f["mimeType"] == "application/vnd.google-apps.folder"}

    # Filter to targets
    active_targets = {k: v for k, v in root_folders.items() if k in TARGET_ROOT_NAMES}
    print(f"Target Root Folders Found: {list(active_targets.keys())}", flush=True)

    for folder_name, folder_id in active_targets.items():
        print(f"\nProcessing Folder: [{folder_name}]...", flush=True)

        # Traverse folder BFS
        queue = [(folder_id, folder_name)]
        while queue:
            curr_id, curr_rel_path = queue.pop(0)

            # Check if this subfolder is Health Connect (skip from vault staging)
            if "health connect" in curr_rel_path.lower() and folder_name == "Medical":
                print(f"  Skipping raw Health Connect telemetry subfolder: {curr_rel_path}", flush=True)
                continue

            is_ehr_branch = "medical record" in curr_rel_path.lower()

            page_token = None
            while True:
                res = service.files().list(
                    q=f"'{curr_id}' in parents and trashed = false",
                    pageSize=500,
                    fields="nextPageToken, files(id, name, mimeType, size)"
                ).execute()

                for item in res.get("files", []):
                    item_name = item.get("name", "")
                    mime = item.get("mimeType", "")
                    clean_item_name = sanitize_filename(item_name)
                    item_ext = ('.' + item_name.split('.')[-1].lower()) if '.' in item_name else ''

                    if mime == "application/vnd.google-apps.folder":
                        if item_name.lower() in EXCLUDED_FOLDER_NAMES:
                            continue
                        sub_rel = os.path.join(curr_rel_path, clean_item_name)
                        queue.append((item["id"], sub_rel))
                    else:
                        # Check extension exclusions
                        if item_ext in EXCLUDED_EXTENSIONS and not is_ehr_branch:
                            continue

                        file_id = item["id"]
                        if file_id in manifest["items"] and os.path.exists(manifest["items"][file_id].get("local_path", "")):
                            continue

                        if dry_run:
                            print(f"  [DRY RUN] Would stage: {curr_rel_path}/{item_name} ({mime})", flush=True)
                            continue

                        # Download & Convert
                        try:
                            if mime == "application/vnd.google-apps.document":
                                out_p = process_google_doc(service, item, curr_rel_path, manifest)
                                print(f"  Converted Doc -> MD: {os.path.basename(out_p)}", flush=True)
                            elif mime == "application/vnd.google-apps.spreadsheet":
                                out_p = process_google_sheet(service, item, curr_rel_path, manifest)
                                print(f"  Converted Sheet -> MD Table: {os.path.basename(out_p)}", flush=True)
                            else:
                                out_p = process_binary_or_text(service, item, curr_rel_path, manifest, is_ehr=is_ehr_branch)
                                print(f"  Downloaded: {curr_rel_path}/{item_name}", flush=True)
                        except (OSError, RuntimeError, ValueError, KeyError) as err:
                            print(f"  [ERROR] Failed to stage {item_name}: {err}", flush=True)

                page_token = res.get("nextPageToken")
                if not page_token:
                    break

        # Periodic manifest save
        save_manifest(manifest)

    save_manifest(manifest)
    print("\n" + "=" * 70, flush=True)
    print(f"Staging Complete! Total Staged Items in Manifest: {len(manifest['items'])}", flush=True)
    print("=" * 70, flush=True)
    return True

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview items without downloading")
    args = parser.parse_args()
    stage_all_knowledge_folders(dry_run=args.dry_run)
